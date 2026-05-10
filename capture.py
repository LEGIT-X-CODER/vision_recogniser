"""
capture.py - Raspberry Pi Camera Module capture driver.

Uses libcamera (picamera2) which is the standard camera stack on
Raspberry Pi OS Bookworm / Lite for Pi 5.

Provides:
  • Camera initialisation and warm-up
  • Image capture with LED feedback
  • Graceful error handling for camera failures
"""

import os
import time
import logging

try:
    from picamera2 import Picamera2
except ImportError:
    Picamera2 = None

try:
    import RPi.GPIO as GPIO
except ImportError:
    GPIO = None

import config

logger = logging.getLogger("capture")

# Module-level camera instance (singleton)
_camera: "Picamera2 | None" = None


def _setup_capture_led() -> None:
    """Configure the capture LED GPIO pin."""
    if GPIO is None:
        return
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(config.CAPTURE_LED, GPIO.OUT, initial=GPIO.LOW)
    logger.info("Capture LED configured on GPIO %d", config.CAPTURE_LED)


def init_camera() -> None:
    """
    Initialise the Pi Camera and set it to a still-capture configuration.

    This should be called once at startup. The camera stays open for the
    lifetime of the process to avoid repeated open/close overhead.
    """
    global _camera

    _setup_capture_led()

    if Picamera2 is None:
        logger.error(
            "picamera2 not installed — camera functions will be unavailable"
        )
        return

    try:
        _camera = Picamera2()
        cam_config = _camera.create_still_configuration(
            main={"size": (config.IMAGE_WIDTH, config.IMAGE_HEIGHT)},
        )
        _camera.configure(cam_config)
        _camera.start()
        # Allow auto-exposure / white-balance to settle
        time.sleep(config.CAMERA_WARMUP_S)
        logger.info(
            "Camera initialised at %dx%d",
            config.IMAGE_WIDTH,
            config.IMAGE_HEIGHT,
        )
    except Exception:
        _camera = None
        logger.exception("Failed to initialise camera")


def capture_image() -> str | None:
    """
    Capture a JPEG image, save it to the image buffer directory,
    and toggle the capture LED during the process.

    Returns:
        Absolute path to the saved image, or None on failure.
    """
    if _camera is None:
        logger.error("Camera not initialised — cannot capture")
        return None

    # Ensure buffer directory exists
    os.makedirs(config.IMAGE_BUFFER_DIR, exist_ok=True)

    # Generate a unique filename using epoch milliseconds
    filename = f"{int(time.time() * 1000)}.jpg"
    filepath = os.path.join(config.IMAGE_BUFFER_DIR, filename)

    try:
        # LED ON → capture → LED OFF
        _set_capture_led(True)
        _camera.capture_file(filepath)
        _set_capture_led(False)

        logger.info("Image captured → %s", filepath)
        return filepath

    except Exception:
        _set_capture_led(False)
        logger.exception("Image capture failed")
        return None


def _set_capture_led(state: bool) -> None:
    """Set the capture indicator LED."""
    if GPIO is None:
        return
    try:
        GPIO.output(config.CAPTURE_LED, GPIO.HIGH if state else GPIO.LOW)
    except Exception:
        logger.debug("Could not set capture LED", exc_info=True)


def close_camera() -> None:
    """Gracefully shut down the camera and release resources."""
    global _camera
    if _camera is not None:
        try:
            _camera.stop()
            _camera.close()
            logger.info("Camera closed")
        except Exception:
            logger.exception("Error closing camera")
        finally:
            _camera = None

    # Cleanup capture LED GPIO
    if GPIO is not None:
        try:
            GPIO.cleanup(config.CAPTURE_LED)
        except Exception:
            logger.debug("Capture LED GPIO cleanup error", exc_info=True)
