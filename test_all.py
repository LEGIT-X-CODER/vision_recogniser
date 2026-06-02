import time
import logging
import os
import sys

# Add current directory to path to import project modules
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import config
import sensor
import capture
import heartbeat
import firebase_upload
import gemini_api

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("test_all")

def test_leds():
    logger.info("--- Testing LEDs ---")
    if heartbeat.GPIO is None:
        logger.error("GPIO not available")
        return
    
    logger.info("Blinking Status LED (GPIO %d) for 5 seconds...", config.STATUS_LED)
    heartbeat.GPIO.setmode(heartbeat.GPIO.BCM)
    heartbeat.GPIO.setup(config.STATUS_LED, heartbeat.GPIO.OUT)
    for _ in range(5):
        heartbeat.GPIO.output(config.STATUS_LED, heartbeat.GPIO.HIGH)
        time.sleep(0.5)
        heartbeat.GPIO.output(config.STATUS_LED, heartbeat.GPIO.LOW)
        time.sleep(0.5)
    
    logger.info("Blinking Capture LED (GPIO %d) for 5 seconds...", config.CAPTURE_LED)
    heartbeat.GPIO.setup(config.CAPTURE_LED, heartbeat.GPIO.OUT)
    for _ in range(5):
        heartbeat.GPIO.output(config.CAPTURE_LED, heartbeat.GPIO.HIGH)
        time.sleep(0.5)
        heartbeat.GPIO.output(config.CAPTURE_LED, heartbeat.GPIO.LOW)
        time.sleep(0.5)
    logger.info("LED test complete.")

def test_sensor():
    logger.info("--- Testing Ultrasonic Sensor ---")
    try:
        baseline, threshold = sensor.calibrate()
        logger.info("Calibration successful: Baseline=%.2f, Threshold=%.2f", baseline, threshold)
        
        logger.info("Taking 5 distance readings...")
        for i in range(5):
            dist = sensor.measure_distance()
            logger.info("Reading %d: %s cm", i+1, dist)
            time.sleep(1)
    except Exception as e:
        logger.error("Sensor test failed: %s", e)

def test_camera():
    logger.info("--- Testing Camera ---")
    try:
        capture.init_camera()
        path = capture.capture_image()
        if path and os.path.exists(path):
            logger.info("Camera test successful! Image saved to: %s", path)
            return path
        else:
            logger.error("Camera test failed: No image captured.")
    except Exception as e:
        logger.error("Camera test failed: %s", e)
    finally:
        capture.close_camera()
    return None

def test_firebase():
    logger.info("--- Testing Firebase ---")
    try:
        firebase_upload.init_firebase()
        # Test heartbeat push
        ref_path = f"devices/{config.DEVICE_ID}/test"
        from firebase_admin import db as rtdb
        ref = rtdb.reference(ref_path)
        ref.set({"status": "testing", "timestamp": int(time.time())})
        logger.info("Firebase test successful! Data pushed to %s", ref_path)
    except Exception as e:
        logger.error("Firebase test failed: %s", e)

def test_gemini(image_path):
    logger.info("--- Testing Gemini API ---")
    if not image_path:
        logger.error("No image path provided for Gemini test.")
        return
    
    try:
        if config.GEMINI_API_KEY == "YOUR_GEMINI_API_KEY" or not config.GEMINI_API_KEY:
            logger.error("Gemini API Key not set in config.py or environment.")
            return
            
        logger.info("Sending image to Gemini...")
        results = gemini_api.analyse_image(image_path)
        logger.info("Gemini response: %s", results)
        logger.info("Gemini API test complete.")
    except Exception as e:
        logger.error("Gemini API test failed: %s", e)

if __name__ == "__main__":
    test_leds()
    # test_sensor() - Skipping as requested (disconnected)
    img_path = test_camera()
    test_firebase()
    if img_path:
        test_gemini(img_path)
    
    # Cleanup
    # sensor.cleanup()
    logger.info("All tests completed.")
