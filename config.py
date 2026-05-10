"""
config.py - Central configuration for the IoT Inventory Detection Device.

All hardware pin mappings, API keys, file paths, timing constants,
and tunable parameters are defined here.
"""

import os

# ──────────────────────────────────────────────────────────────────────
# GPIO Pin Mapping
# ──────────────────────────────────────────────────────────────────────
TRIG_PIN = 23          # Ultrasonic sensor TRIG
ECHO_PIN = 24          # Ultrasonic sensor ECHO
STATUS_LED = 17        # Heartbeat / alive indicator
CAPTURE_LED = 27       # Lights during image capture

# ──────────────────────────────────────────────────────────────────────
# Ultrasonic Sensor Calibration
# ──────────────────────────────────────────────────────────────────────
CALIBRATION_SAMPLES = 20          # Number of readings during calibration
THRESHOLD_OFFSET_CM = 2.0         # baseline - offset = trigger threshold
SENSOR_POLL_INTERVAL_S = 0.1      # Delay between distance polls (seconds)
SENSOR_TIMEOUT_S = 0.04           # Max wait for echo pulse (~6.8 m range)
CAPTURE_COOLDOWN_S = 3.0          # Minimum seconds between consecutive captures

# ──────────────────────────────────────────────────────────────────────
# Camera
# ──────────────────────────────────────────────────────────────────────
IMAGE_WIDTH = 1280
IMAGE_HEIGHT = 720
CAMERA_WARMUP_S = 2               # Seconds to let camera auto-expose

# ──────────────────────────────────────────────────────────────────────
# File Paths
# ──────────────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
IMAGE_BUFFER_DIR = os.path.join(BASE_DIR, "image_buffer")
LOG_DIR = os.path.join(BASE_DIR, "logs")
SERVICE_ACCOUNT_PATH = os.path.join(BASE_DIR, "service-account.json")
QUEUE_PERSIST_PATH = os.path.join(BASE_DIR, "image_buffer", ".queue_state.json")

# ──────────────────────────────────────────────────────────────────────
# Gemini API
# ──────────────────────────────────────────────────────────────────────
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "YOUR_GEMINI_API_KEY")
GEMINI_MODEL = "gemini-1.5-flash"

GEMINI_PROMPT = """Analyze this image and detect all products present.

For each product detected, provide:
- name: Full product name
- brand: Brand name
- product_type: Product category
- estimated_quantity: Accurate count
- sku_or_barcode: Visible barcode/SKU if visible

Return response as valid JSON array only:

[
  {
    "name": "Sunfeast Glucose Biscuits",
    "brand": "Sunfeast",
    "product_type": "Biscuit",
    "estimated_quantity": 2,
    "sku_or_barcode": null
  }
]

If no products are detected return:
[]

Important:
- Return valid JSON only
- No markdown
- No explanation text"""

# ──────────────────────────────────────────────────────────────────────
# Firebase
# ──────────────────────────────────────────────────────────────────────
FIRESTORE_COLLECTION = "inventory_logs"
RTDB_URL = "https://raspi-iot-bd60b-default-rtdb.asia-southeast1.firebasedatabase.app/"
DEVICE_ID = "device_001"

# ──────────────────────────────────────────────────────────────────────
# Heartbeat
# ──────────────────────────────────────────────────────────────────────
HEARTBEAT_INTERVAL_S = 30

# ──────────────────────────────────────────────────────────────────────
# Retry / Resilience
# ──────────────────────────────────────────────────────────────────────
API_MAX_RETRIES = 3
API_RETRY_BACKOFF_S = 2           # Exponential back-off base
FIREBASE_MAX_RETRIES = 3
FIREBASE_RETRY_BACKOFF_S = 2

# ──────────────────────────────────────────────────────────────────────
# Buffer Queue Worker
# ──────────────────────────────────────────────────────────────────────
QUEUE_POLL_INTERVAL_S = 1.0       # Worker checks queue this often
MAX_QUEUE_SIZE = 200              # Limit in-memory queue depth

# ──────────────────────────────────────────────────────────────────────
# Status LED
# ──────────────────────────────────────────────────────────────────────
STATUS_BLINK_ON_S = 0.5
STATUS_BLINK_OFF_S = 0.5
