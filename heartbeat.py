"""
heartbeat.py - Firebase RTDB heartbeat and Status LED blinker.
"""

import time
import logging
import threading

try:
    import RPi.GPIO as GPIO
except ImportError:
    GPIO = None

from firebase_admin import db as rtdb
import config

logger = logging.getLogger("heartbeat")
_shutdown_event = threading.Event()


def _setup_status_led():
    if GPIO is None:
        return
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(config.STATUS_LED, GPIO.OUT, initial=GPIO.LOW)
    logger.info("Status LED on GPIO %d", config.STATUS_LED)


def _blink_loop():
    _setup_status_led()
    logger.info("Status LED blink started")
    while not _shutdown_event.is_set():
        if GPIO:
            try:
                GPIO.output(config.STATUS_LED, GPIO.HIGH)
            except Exception:
                pass
        _shutdown_event.wait(config.STATUS_BLINK_ON_S)
        if GPIO:
            try:
                GPIO.output(config.STATUS_LED, GPIO.LOW)
            except Exception:
                pass
        _shutdown_event.wait(config.STATUS_BLINK_OFF_S)
    if GPIO:
        try:
            GPIO.output(config.STATUS_LED, GPIO.LOW)
            GPIO.cleanup(config.STATUS_LED)
        except Exception:
            pass
    logger.info("Status LED blink stopped")


def _heartbeat_loop():
    ref_path = f"devices/{config.DEVICE_ID}"
    logger.info("Heartbeat started → %s", ref_path)
    while not _shutdown_event.is_set():
        try:
            ref = rtdb.reference(ref_path)
            ref.update({"status": "online", "lastHeartbeat": int(time.time())})
            logger.debug("Heartbeat sent")
        except Exception:
            logger.exception("Heartbeat push failed")
        _shutdown_event.wait(config.HEARTBEAT_INTERVAL_S)
    try:
        rtdb.reference(ref_path).update({"status": "offline", "lastHeartbeat": int(time.time())})
        logger.info("Device marked offline")
    except Exception:
        logger.exception("Failed to mark offline")
    logger.info("Heartbeat stopped")


def start():
    t1 = threading.Thread(target=_blink_loop, name="StatusLED", daemon=True)
    t2 = threading.Thread(target=_heartbeat_loop, name="Heartbeat", daemon=True)
    t1.start()
    t2.start()
    return t1, t2


def stop():
    _shutdown_event.set()
    logger.info("Heartbeat/LED shutdown signalled")
