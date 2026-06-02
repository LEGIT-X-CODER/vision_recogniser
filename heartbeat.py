"""
heartbeat.py - Firebase RTDB heartbeat and Status LED blinker.

LED Patterns on GPIO 17 (STATUS_LED):
  Fast blink (0.2s) = Booting / Searching for network
  Slow blink (1.0s) = Network connected, app running normally
  3 quick blinks    = Error / crash (callable from anywhere via signal_error())
"""

import time
import socket
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
_error_flag     = threading.Event()   # Set this to trigger 3-blink error pattern


# ──────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────

def signal_error():
    """
    Call from anywhere (main.py, firebase_upload, etc.) to trigger
    the 3-quick-blink error pattern on the status LED.
    """
    _error_flag.set()
    logger.warning("Error signal sent to LED")


def start():
    t1 = threading.Thread(target=_blink_loop,     name="StatusLED", daemon=True)
    t2 = threading.Thread(target=_heartbeat_loop, name="Heartbeat",  daemon=True)
    t1.start()
    t2.start()
    return t1, t2


def stop():
    _shutdown_event.set()
    logger.info("Heartbeat/LED shutdown signalled")


# ──────────────────────────────────────────────────────────────────────
# Network check
# ──────────────────────────────────────────────────────────────────────

def _has_internet(host="8.8.8.8", port=53, timeout=3) -> bool:
    """
    Tries to open a TCP socket to Google DNS.
    Fast (~3 s timeout) — no actual DNS query needed.
    """
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((host, port))
        sock.close()
        return True
    except OSError:
        return False


# ──────────────────────────────────────────────────────────────────────
# LED helpers
# ──────────────────────────────────────────────────────────────────────

def _setup_status_led():
    if GPIO is None:
        return
    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)
    GPIO.setup(config.STATUS_LED, GPIO.OUT, initial=GPIO.LOW)
    logger.info("Status LED ready on GPIO %d", config.STATUS_LED)


def _led_on():
    if GPIO:
        try:
            GPIO.output(config.STATUS_LED, GPIO.HIGH)
        except Exception:
            pass


def _led_off():
    if GPIO:
        try:
            GPIO.output(config.STATUS_LED, GPIO.LOW)
        except Exception:
            pass


def _blink(on_s: float, off_s: float, count: int = 1):
    """Blink LED `count` times. Respects shutdown event."""
    for _ in range(count):
        if _shutdown_event.is_set():
            break
        _led_on()
        _shutdown_event.wait(on_s)
        _led_off()
        _shutdown_event.wait(off_s)


# ──────────────────────────────────────────────────────────────────────
# Blink loop — network-aware pattern
# ──────────────────────────────────────────────────────────────────────

def _blink_loop():
    _setup_status_led()
    logger.info("Status LED blink loop started")

    # Track last internet check to avoid checking every single blink
    last_net_check  = 0.0
    net_check_every = 10.0   # re-check internet every 10 s
    network_ok      = False

    while not _shutdown_event.is_set():

        # ── Error signal: 3 rapid blinks then 1 s pause ──────────────
        if _error_flag.is_set():
            _error_flag.clear()
            logger.debug("LED: error pattern")
            _blink(0.1, 0.1, 3)
            _shutdown_event.wait(1.0)
            continue

        # ── Refresh network status periodically ──────────────────────
        now = time.time()
        if now - last_net_check >= net_check_every:
            network_ok     = _has_internet()
            last_net_check = now
            logger.debug("LED: network_ok=%s", network_ok)

        # ── Choose blink pattern ──────────────────────────────────────
        if network_ok:
            # Slow blink = all good, app running
            _blink(1.0, 1.0)
        else:
            # Fast blink = no network yet / searching
            _blink(0.2, 0.2)

    # Cleanup
    _led_off()
    if GPIO:
        try:
            GPIO.cleanup(config.STATUS_LED)
        except Exception:
            pass
    logger.info("Status LED blink loop stopped")


# ──────────────────────────────────────────────────────────────────────
# Heartbeat loop — pushes to Firebase RTDB
# ──────────────────────────────────────────────────────────────────────

def _heartbeat_loop():
    ref_path = f"devices/{config.DEVICE_ID}"
    logger.info("Heartbeat started → RTDB:%s", ref_path)

    while not _shutdown_event.is_set():
        try:
            ref = rtdb.reference(ref_path)
            ref.update({
                "status":        "online",
                "lastHeartbeat": int(time.time()),
            })
            logger.debug("Heartbeat sent")
        except Exception:
            logger.exception("Heartbeat push failed — will retry in %ds",
                             config.HEARTBEAT_INTERVAL_S)

        _shutdown_event.wait(config.HEARTBEAT_INTERVAL_S)

    # Mark offline on clean shutdown
    try:
        rtdb.reference(ref_path).update({
            "status":        "offline",
            "lastHeartbeat": int(time.time()),
        })
        logger.info("Device marked offline in RTDB")
    except Exception:
        logger.exception("Failed to mark device offline")

    logger.info("Heartbeat loop stopped")
