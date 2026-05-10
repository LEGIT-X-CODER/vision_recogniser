"""
sensor.py - Ultrasonic HC-SR04 sensor driver and calibration.

Provides:
  • Single-shot distance measurement
  • Baseline calibration (mode of N samples)
  • Continuous monitoring with threshold-based object detection
"""

import time
import logging
from collections import Counter

try:
    import RPi.GPIO as GPIO
except ImportError:
    # Allow importing on non-Pi machines for development/testing
    GPIO = None

import config

logger = logging.getLogger("sensor")


def _setup_gpio() -> None:
    """Configure GPIO pins for the ultrasonic sensor (BCM mode)."""
    if GPIO is None:
        logger.warning("RPi.GPIO not available — running in stub mode")
        return
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(config.TRIG_PIN, GPIO.OUT, initial=GPIO.LOW)
    GPIO.setup(config.ECHO_PIN, GPIO.IN)
    logger.info(
        "GPIO configured — TRIG=%d  ECHO=%d", config.TRIG_PIN, config.ECHO_PIN
    )


def measure_distance() -> float | None:
    """
    Take a single HC-SR04 distance reading.

    Returns:
        Distance in centimetres, or None on timeout / error.
    """
    if GPIO is None:
        return None

    # Send 10 µs trigger pulse
    GPIO.output(config.TRIG_PIN, GPIO.HIGH)
    time.sleep(0.00001)
    GPIO.output(config.TRIG_PIN, GPIO.LOW)

    # Wait for echo to go HIGH (start of pulse)
    pulse_start: float = time.time()
    deadline = pulse_start + config.SENSOR_TIMEOUT_S
    while GPIO.input(config.ECHO_PIN) == 0:
        pulse_start = time.time()
        if pulse_start > deadline:
            logger.debug("Echo start timeout")
            return None

    # Wait for echo to go LOW (end of pulse)
    pulse_end: float = time.time()
    deadline = pulse_end + config.SENSOR_TIMEOUT_S
    while GPIO.input(config.ECHO_PIN) == 1:
        pulse_end = time.time()
        if pulse_end > deadline:
            logger.debug("Echo end timeout")
            return None

    # Distance = (time × speed_of_sound) / 2
    elapsed = pulse_end - pulse_start
    distance_cm = (elapsed * 34300) / 2
    return round(distance_cm, 2)


def calibrate() -> tuple[float, float]:
    """
    Calibrate the sensor by taking CALIBRATION_SAMPLES readings,
    computing the mode (most frequent rounded value), and deriving
    a detection threshold.

    Returns:
        (baseline_cm, threshold_cm)

    Raises:
        RuntimeError: If calibration fails to obtain enough valid readings.
    """
    _setup_gpio()

    logger.info(
        "Starting calibration with %d samples …", config.CALIBRATION_SAMPLES
    )
    readings: list[float] = []

    attempts = 0
    max_attempts = config.CALIBRATION_SAMPLES * 3  # allow retries for timeouts
    while len(readings) < config.CALIBRATION_SAMPLES and attempts < max_attempts:
        dist = measure_distance()
        if dist is not None and 2 < dist < 400:
            readings.append(round(dist))  # round to whole cm for mode
            logger.debug("Calibration sample %d: %.2f cm", len(readings), dist)
        else:
            logger.debug("Calibration sample discarded (dist=%s)", dist)
        attempts += 1
        time.sleep(0.05)

    if len(readings) < config.CALIBRATION_SAMPLES // 2:
        raise RuntimeError(
            f"Calibration failed — only {len(readings)} valid readings "
            f"out of {max_attempts} attempts"
        )

    # Mode = most common rounded distance
    counter = Counter(readings)
    baseline = float(counter.most_common(1)[0][0])
    threshold = baseline - config.THRESHOLD_OFFSET_CM

    logger.info(
        "Calibration complete — baseline=%.1f cm  threshold=%.1f cm",
        baseline,
        threshold,
    )
    return baseline, threshold


def cleanup() -> None:
    """Release GPIO resources used by the sensor."""
    if GPIO is not None:
        try:
            GPIO.cleanup([config.TRIG_PIN, config.ECHO_PIN])
            logger.info("Sensor GPIO cleaned up")
        except Exception:
            logger.exception("Error during sensor GPIO cleanup")
