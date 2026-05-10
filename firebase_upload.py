"""
firebase_upload.py - Firebase Firestore upload and buffer queue worker.

Responsibilities:
  • Initialise Firebase Admin SDK (Firestore)
  • Save inventory detection results to Firestore
  • Run an asynchronous background worker that drains the image queue,
    calls Gemini, and uploads results
  • Persist / restore queue state across reboots
"""

import os
import json
import time
import queue
import logging
import threading
from typing import Any

import firebase_admin
from firebase_admin import credentials, firestore

import config
import gemini_api

logger = logging.getLogger("firebase_upload")

# Module-level Firestore client
_db: Any = None

# Thread-safe image queue
image_queue: queue.Queue = queue.Queue(maxsize=config.MAX_QUEUE_SIZE)

# Shutdown flag
_shutdown_event = threading.Event()


# ──────────────────────────────────────────────────────────────────────
# Initialisation
# ──────────────────────────────────────────────────────────────────────

def init_firebase() -> None:
    """
    Initialise the Firebase Admin SDK using the service-account.json
    credential file.  Sets up the Firestore client.
    """
    global _db

    if not os.path.exists(config.SERVICE_ACCOUNT_PATH):
        logger.error(
            "service-account.json not found at %s", config.SERVICE_ACCOUNT_PATH
        )
        return

    try:
        cred = credentials.Certificate(config.SERVICE_ACCOUNT_PATH)
        # Initialise with both Firestore and RTDB support
        if not firebase_admin._apps:
            firebase_admin.initialize_app(cred, {
                "databaseURL": config.RTDB_URL,
            })
        _db = firestore.client()
        logger.info("Firebase Firestore initialised")
    except Exception:
        logger.exception("Failed to initialise Firebase")


# ──────────────────────────────────────────────────────────────────────
# Firestore Upload
# ──────────────────────────────────────────────────────────────────────

def upload_to_firestore(
    products: list[dict[str, Any]], image_name: str
) -> bool:
    """
    Save an inventory log document to Firestore.

    Args:
        products: List of product dicts from Gemini.
        image_name: Original image filename.

    Returns:
        True on success, False on failure.
    """
    if _db is None:
        logger.error("Firestore not initialised — cannot upload")
        return False

    doc_data = {
        "products": products,
        "image_name": image_name,
        "timestamp": firestore.SERVER_TIMESTAMP,
    }

    for attempt in range(1, config.FIREBASE_MAX_RETRIES + 1):
        try:
            _db.collection(config.FIRESTORE_COLLECTION).add(doc_data)
            logger.info(
                "Uploaded %d product(s) for %s to Firestore",
                len(products),
                image_name,
            )
            return True
        except Exception as exc:
            wait = config.FIREBASE_RETRY_BACKOFF_S ** attempt
            logger.warning(
                "Firestore upload error (attempt %d/%d): %s — retrying in %.1fs",
                attempt,
                config.FIREBASE_MAX_RETRIES,
                exc,
                wait,
            )
            time.sleep(wait)

    logger.error(
        "Firestore upload failed for %s after %d attempts",
        image_name,
        config.FIREBASE_MAX_RETRIES,
    )
    return False


# ──────────────────────────────────────────────────────────────────────
# Queue Persistence
# ──────────────────────────────────────────────────────────────────────

def persist_queue() -> None:
    """
    Save all unprocessed image paths in the queue to disk so they
    survive a reboot.
    """
    items: list[str] = []
    # Drain into a list and re-enqueue
    while not image_queue.empty():
        try:
            items.append(image_queue.get_nowait())
        except queue.Empty:
            break

    # Re-enqueue so the worker can still process them
    for item in items:
        try:
            image_queue.put_nowait(item)
        except queue.Full:
            break

    try:
        os.makedirs(os.path.dirname(config.QUEUE_PERSIST_PATH), exist_ok=True)
        with open(config.QUEUE_PERSIST_PATH, "w") as f:
            json.dump(items, f)
        logger.debug("Persisted %d queue items to disk", len(items))
    except Exception:
        logger.exception("Failed to persist queue state")


def restore_queue() -> int:
    """
    Restore previously persisted image paths back into the queue.

    Also scans the image_buffer directory for any orphaned images
    that are not already in the persisted list.

    Returns:
        Number of items restored.
    """
    restored: set[str] = set()

    # 1. Restore from persisted JSON
    if os.path.exists(config.QUEUE_PERSIST_PATH):
        try:
            with open(config.QUEUE_PERSIST_PATH, "r") as f:
                paths = json.load(f)
            for p in paths:
                if os.path.isfile(p):
                    restored.add(p)
        except Exception:
            logger.exception("Failed to read persisted queue state")

    # 2. Scan buffer directory for orphaned images
    if os.path.isdir(config.IMAGE_BUFFER_DIR):
        for fname in sorted(os.listdir(config.IMAGE_BUFFER_DIR)):
            if fname.lower().endswith((".jpg", ".jpeg", ".png")):
                fpath = os.path.join(config.IMAGE_BUFFER_DIR, fname)
                restored.add(fpath)

    # Enqueue all
    count = 0
    for path in sorted(restored):
        try:
            image_queue.put_nowait(path)
            count += 1
        except queue.Full:
            logger.warning("Queue full during restore — some images skipped")
            break

    if count:
        logger.info("Restored %d image(s) into processing queue", count)

    # Clear persist file after successful restore
    try:
        if os.path.exists(config.QUEUE_PERSIST_PATH):
            os.remove(config.QUEUE_PERSIST_PATH)
    except OSError:
        pass

    return count


# ──────────────────────────────────────────────────────────────────────
# Background Queue Worker
# ──────────────────────────────────────────────────────────────────────

def _worker_loop() -> None:
    """
    Background worker that continuously processes the image queue.

    For each image:
      1. Call Gemini API
      2. Upload results to Firestore
      3. Delete the processed image
    """
    logger.info("Queue worker started")

    while not _shutdown_event.is_set():
        try:
            image_path = image_queue.get(timeout=config.QUEUE_POLL_INTERVAL_S)
        except queue.Empty:
            continue

        if not os.path.isfile(image_path):
            logger.warning("Queued image missing, skipping: %s", image_path)
            image_queue.task_done()
            continue

        image_name = os.path.basename(image_path)
        logger.info("Processing queued image: %s", image_name)

        # 1. Analyse with Gemini
        products = gemini_api.analyse_image(image_path)

        # 2. Upload to Firestore
        success = upload_to_firestore(products, image_name)

        # 3. Delete image on success
        if success:
            try:
                os.remove(image_path)
                logger.info("Deleted processed image: %s", image_name)
            except OSError:
                logger.exception("Failed to delete image: %s", image_path)
        else:
            # Re-queue for retry (put at back of queue)
            logger.warning("Re-queuing image for retry: %s", image_name)
            try:
                image_queue.put_nowait(image_path)
            except queue.Full:
                logger.error("Queue full — cannot re-queue %s", image_name)

        image_queue.task_done()

    logger.info("Queue worker stopped")


def start_worker() -> threading.Thread:
    """
    Launch the background queue processing worker.

    Returns:
        The worker Thread instance.
    """
    thread = threading.Thread(
        target=_worker_loop, name="QueueWorker", daemon=True
    )
    thread.start()
    return thread


def stop_worker() -> None:
    """Signal the worker to shut down gracefully."""
    _shutdown_event.set()
    persist_queue()
    logger.info("Queue worker shutdown signalled; queue state persisted")
