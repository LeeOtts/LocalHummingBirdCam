import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent

# OpenAI
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

# Facebook
FACEBOOK_PAGE_ID = os.getenv("FACEBOOK_PAGE_ID", "")
FACEBOOK_PAGE_ACCESS_TOKEN = os.getenv("FACEBOOK_PAGE_ACCESS_TOKEN", "")

# Detection tuning
MOTION_THRESHOLD = float(os.getenv("MOTION_THRESHOLD", "15.0"))
COLOR_MIN_AREA = int(os.getenv("COLOR_MIN_AREA", "300"))
COLOR_MAX_AREA = int(os.getenv("COLOR_MAX_AREA", "5000"))
DETECTION_COOLDOWN_SECONDS = int(os.getenv("DETECTION_COOLDOWN_SECONDS", "60"))
MAX_POSTS_PER_DAY = int(os.getenv("MAX_POSTS_PER_DAY", "10"))

# Video settings
VIDEO_WIDTH = int(os.getenv("VIDEO_WIDTH", "640"))
VIDEO_HEIGHT = int(os.getenv("VIDEO_HEIGHT", "480"))
VIDEO_FPS = int(os.getenv("VIDEO_FPS", "15"))
CLIP_PRE_SECONDS = int(os.getenv("CLIP_PRE_SECONDS", "5"))
CLIP_POST_SECONDS = int(os.getenv("CLIP_POST_SECONDS", "20"))
VIDEO_BITRATE = int(os.getenv("VIDEO_BITRATE", "1500000"))

# Derived
CLIP_TOTAL_SECONDS = CLIP_PRE_SECONDS + CLIP_POST_SECONDS
CIRCULAR_BUFFER_SIZE = int(VIDEO_FPS * CLIP_PRE_SECONDS * 1.1)  # 10% margin

# Camera type: "auto", "picamera", or "usb"
CAMERA_TYPE = os.getenv("CAMERA_TYPE", "usb")
USB_CAMERA_INDEX = int(os.getenv("USB_CAMERA_INDEX", "0"))

# Vision verification — use GPT-4o to confirm hummingbird before recording
VISION_VERIFY_ENABLED = os.getenv("VISION_VERIFY_ENABLED", "true").lower() in ("true", "1", "yes")

# Audio — set AUDIO_DEVICE to the ALSA device (e.g. "hw:1,0" or "default")
# Use "arecord -l" on the Pi to list available audio devices
AUDIO_ENABLED = os.getenv("AUDIO_ENABLED", "true").lower() in ("true", "1", "yes")
AUDIO_DEVICE = os.getenv("AUDIO_DEVICE", "default")

# Lores stream for detection
LORES_WIDTH = 320
LORES_HEIGHT = 240

# Disk management — auto-delete oldest clips when total exceeds this limit
MAX_CLIPS_DISK_MB = int(os.getenv("MAX_CLIPS_DISK_MB", "2000"))  # 2GB default

# Paths
CLIPS_DIR = BASE_DIR / os.getenv("CLIPS_DIR", "clips")
LOGS_DIR = BASE_DIR / os.getenv("LOGS_DIR", "logs")
RETRY_QUEUE_FILE = BASE_DIR / "retry_queue.json"
TRAINING_DIR = BASE_DIR / "training"

# Test mode — records clips and generates captions but skips Facebook posting
TEST_MODE = os.getenv("TEST_MODE", "true").lower() in ("true", "1", "yes")

# Night mode — auto sleep based on sunrise/sunset at your location
# Set your latitude/longitude and timezone
# Bartlett, TN 38134 defaults
LOCATION_LAT = float(os.getenv("LOCATION_LAT", "35.2045"))
LOCATION_LNG = float(os.getenv("LOCATION_LNG", "-89.8740"))
LOCATION_TIMEZONE = os.getenv("LOCATION_TIMEZONE", "US/Eastern")
LOCATION_NAME = os.getenv("LOCATION_NAME", "Bartlett, TN")

# How many minutes before sunrise to wake up / after sunset to sleep
WAKE_BEFORE_SUNRISE_MIN = int(os.getenv("WAKE_BEFORE_SUNRISE_MIN", "30"))
SLEEP_AFTER_SUNSET_MIN = int(os.getenv("SLEEP_AFTER_SUNSET_MIN", "30"))

# Set to false to disable night mode (run 24/7)
NIGHT_MODE_ENABLED = os.getenv("NIGHT_MODE_ENABLED", "true").lower() in ("true", "1", "yes")

# Web dashboard
WEB_HOST = os.getenv("WEB_HOST", "0.0.0.0")
WEB_PORT = int(os.getenv("WEB_PORT", "8080"))
