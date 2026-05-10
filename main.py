"""
main.py - Entry point for the IoT Inventory Detection Device.

Orchestrates all subsystems:
  1. Logging setup
  2. Firebase init + heartbeat
  3. Status LED blink
  4. Camera init
  5. Sensor calibration
  6. Queue restore + background worker
  7. Main detection loop
"""

import os
import sys
import time
import signal
import logging
import threading
from logging.handlers import RotatingFileHandler

import config
import sensor
import capture
import heartbeat
import firebase_upload

logger = logging.getLogger("main")
_running = True


def setup_logging():
    os.makedirs(config.LOG_DIR, exist_ok=True)
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler = RotatingFileHandler(
        os.path.join(config.LOG_DIR, "device.log"),
        maxBytes=2 * 1024 * 1024,
        backupCount=3,
    )
    file_handler.setFormatter(fmt)
    file_handler.setLevel(logging.DEBUG)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(fmt)
    console_handler.setLevel(logging.INFO)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.addHandler(file_handler)
    root.addHandler(console_handler)


def signal_handler(signum, frame):
    global _running
    logger.info("Received signal %d — shutting down …", signum)
    _running = False


def cleanup():
    logger.info("Running cleanup …")
    heartbeat.stop()
    firebase_upload.stop_worker()
    capture.close_camera()
    sensor.cleanup()
    logger.info("Cleanup complete. Goodbye.")


def detection_loop(threshold: float):
    """
    Main detection loop.
    Monitors ultrasonic distance; when an object crosses the threshold,
    captures an image and enqueues it for processing.
    """
    last_capture_time: float = 0.0
    object_present = False

    logger.info("Detection loop started (threshold=%.1f cm)", threshold)

    while _running:
        dist = sensor.measure_distance()

        if dist is None:
            time.sleep(config.SENSOR_POLL_INTERVAL_S)
            continue

        if dist < threshold and not object_present:
            now = time.time()
            if now - last_capture_time < config.CAPTURE_COOLDOWN_S:
                logger.debug(
                    "Object detected (%.1f cm) but cooldown active", dist
                )
                time.sleep(config.SENSOR_POLL_INTERVAL_S)
                continue

            object_present = True
            logger.info("Object detected at %.1f cm — capturing …", dist)

            image_path = capture.capture_image()
            if image_path:
                try:
                    firebase_upload.image_queue.put_nowait(image_path)
                    logger.info("Image enqueued: %s", image_path)
                except Exception:
                    logger.error("Queue full — image not enqueued!")
            last_capture_time = time.time()

        elif dist >= threshold and object_present:
            object_present = False
            logger.debug("Object left zone (%.1f cm)", dist)

        time.sleep(config.SENSOR_POLL_INTERVAL_S)


def main():
    setup_logging()
    logger.info("=" * 60)
    logger.info("IoT Inventory Detection Device — starting up")
    logger.info("=" * 60)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    os.makedirs(config.IMAGE_BUFFER_DIR, exist_ok=True)

    # 1. Firebase
    logger.info("[1/6] Initialising Firebase …")
    firebase_upload.init_firebase()

    # 2. Heartbeat + Status LED
    logger.info("[2/6] Starting heartbeat & status LED …")
    heartbeat.start()

    # 3. Camera
    logger.info("[3/6] Initialising camera …")
    capture.init_camera()

    # 4. Sensor calibration
    logger.info("[4/6] Calibrating ultrasonic sensor …")
    try:
        baseline, threshold = sensor.calibrate()
    except RuntimeError as e:
        logger.critical("Sensor calibration failed: %s", e)
        cleanup()
        sys.exit(1)

    # 5. Restore queue + start worker
    logger.info("[5/6] Restoring queue & starting worker …")
    firebase_upload.restore_queue()
    firebase_upload.start_worker()

    # 6. Detection loop
    logger.info("[6/6] Entering detection loop …")
    try:
        detection_loop(threshold)
    except Exception:
        logger.exception("Unhandled error in detection loop")
    finally:
        cleanup()


if __name__ == "__main__":
    main()
