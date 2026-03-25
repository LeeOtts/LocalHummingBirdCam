import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent

# OpenAI / Azure OpenAI
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

# Azure OpenAI — set these if using Azure instead of OpenAI direct
# AZURE_OPENAI_ENDPOINT = "https://your-resource.openai.azure.com/"
# AZURE_OPENAI_DEPLOYMENT = your deployment name (e.g. "gpt-4o")
AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT", "")
AZURE_OPENAI_DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o")
AZURE_OPENAI_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2024-12-01-preview")

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
VIDEO_WIDTH = int(os.getenv("VIDEO_WIDTH", "1920"))
VIDEO_HEIGHT = int(os.getenv("VIDEO_HEIGHT", "1080"))
VIDEO_FPS = int(os.getenv("VIDEO_FPS", "15"))
CLIP_PRE_SECONDS = int(os.getenv("CLIP_PRE_SECONDS", "5"))
CLIP_POST_SECONDS = int(os.getenv("CLIP_POST_SECONDS", "20"))
VIDEO_BITRATE = int(os.getenv("VIDEO_BITRATE", "5000000"))

# Derived
CLIP_TOTAL_SECONDS = CLIP_PRE_SECONDS + CLIP_POST_SECONDS
CIRCULAR_BUFFER_SIZE = int(VIDEO_FPS * CLIP_PRE_SECONDS * 1.1)  # 10% margin

# Camera rotation: 0, 90, 180, 270 degrees (for upside-down or sideways mounting)
CAMERA_ROTATION = int(os.getenv("CAMERA_ROTATION", "0"))

# Camera type: "auto", "picamera", or "usb"
CAMERA_TYPE = os.getenv("CAMERA_TYPE", "usb")
USB_CAMERA_INDEX = int(os.getenv("USB_CAMERA_INDEX", "0"))

# Vision verification — use local MobileNetV2 bird classifier to confirm hummingbird
VISION_VERIFY_ENABLED = os.getenv("VISION_VERIFY_ENABLED", "true").lower() in ("true", "1", "yes")

# Vision captions — send a frame to GPT-4o so it can describe what the bird is doing
VISION_CAPTION_ENABLED = os.getenv("VISION_CAPTION_ENABLED", "true").lower() in ("true", "1", "yes")
VISION_CAPTION_DETAIL = os.getenv("VISION_CAPTION_DETAIL", "low")  # "low" or "high"

# Bluesky (AT Protocol) — leave blank to disable
BLUESKY_HANDLE = os.getenv("BLUESKY_HANDLE", "")
BLUESKY_APP_PASSWORD = os.getenv("BLUESKY_APP_PASSWORD", "")

# Twitter/X — leave blank to disable
TWITTER_API_KEY = os.getenv("TWITTER_API_KEY", "")
TWITTER_API_SECRET = os.getenv("TWITTER_API_SECRET", "")
TWITTER_ACCESS_TOKEN = os.getenv("TWITTER_ACCESS_TOKEN", "")
TWITTER_ACCESS_SECRET = os.getenv("TWITTER_ACCESS_SECRET", "")

# Weather (OpenWeatherMap free tier) — leave blank to disable
OPENWEATHERMAP_API_KEY = os.getenv("OPENWEATHERMAP_API_KEY", "")

# AI comment replies on Facebook
AUTO_REPLY_ENABLED = os.getenv("AUTO_REPLY_ENABLED", "false").lower() in ("true", "1", "yes")
AUTO_REPLY_MAX_PER_HOUR = int(os.getenv("AUTO_REPLY_MAX_PER_HOUR", "5"))

# Weekly digest — auto-post a recap every week
WEEKLY_DIGEST_ENABLED = os.getenv("WEEKLY_DIGEST_ENABLED", "true").lower() in ("true", "1", "yes")
WEEKLY_DIGEST_DAY = os.getenv("WEEKLY_DIGEST_DAY", "sunday").lower().strip()

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
MAX_RETRY_QUEUE_SIZE = int(os.getenv("MAX_RETRY_QUEUE_SIZE", "50"))
TRAINING_DIR = BASE_DIR / "training"

# Test mode — records clips and generates captions but skips Facebook posting
TEST_MODE = os.getenv("TEST_MODE", "true").lower() in ("true", "1", "yes")

# Night mode — auto sleep based on sunrise/sunset at your location
# Set your latitude/longitude and timezone
# Bartlett, TN 38134 defaults
LOCATION_LAT = float(os.getenv("LOCATION_LAT", "35.1495"))
LOCATION_LNG = float(os.getenv("LOCATION_LNG", "-89.8733"))
LOCATION_TIMEZONE = os.getenv("LOCATION_TIMEZONE", "America/Chicago")
LOCATION_NAME = os.getenv("LOCATION_NAME", "Bartlett, TN")

# How many minutes before sunrise to wake up / after sunset to sleep
WAKE_BEFORE_SUNRISE_MIN = int(os.getenv("WAKE_BEFORE_SUNRISE_MIN", "30"))
SLEEP_AFTER_SUNSET_MIN = int(os.getenv("SLEEP_AFTER_SUNSET_MIN", "30"))

# Set to false to disable night mode (run 24/7)
NIGHT_MODE_ENABLED = os.getenv("NIGHT_MODE_ENABLED", "true").lower() in ("true", "1", "yes")

# Web dashboard
WEB_HOST = os.getenv("WEB_HOST", "0.0.0.0")
WEB_PORT = int(os.getenv("WEB_PORT", "8080"))
# Optional password for the web dashboard (HTTP Basic Auth).
# Leave blank to disable authentication (default — local network only).
WEB_PASSWORD = os.getenv("WEB_PASSWORD", "")

# --- Validation (clamp to sane defaults) ---
import logging as _log

if VIDEO_FPS < 1:
    _log.warning("VIDEO_FPS=%d invalid, clamping to 1", VIDEO_FPS)
    VIDEO_FPS = 1
if CLIP_POST_SECONDS < 1:
    _log.warning("CLIP_POST_SECONDS=%d invalid, clamping to 1", CLIP_POST_SECONDS)
    CLIP_POST_SECONDS = 1
if COLOR_MAX_AREA < COLOR_MIN_AREA:
    _log.warning("COLOR_MAX_AREA (%d) < COLOR_MIN_AREA (%d), swapping", COLOR_MAX_AREA, COLOR_MIN_AREA)
    COLOR_MIN_AREA, COLOR_MAX_AREA = COLOR_MAX_AREA, COLOR_MIN_AREA
if CAMERA_ROTATION not in (0, 90, 180, 270):
    _log.warning("CAMERA_ROTATION=%d invalid, defaulting to 0", CAMERA_ROTATION)
    CAMERA_ROTATION = 0
if VIDEO_WIDTH < 1:
    _log.warning("VIDEO_WIDTH=%d invalid, clamping to 1", VIDEO_WIDTH)
    VIDEO_WIDTH = 1
if VIDEO_HEIGHT < 1:
    _log.warning("VIDEO_HEIGHT=%d invalid, clamping to 1", VIDEO_HEIGHT)
    VIDEO_HEIGHT = 1
if MOTION_THRESHOLD < 0:
    _log.warning("MOTION_THRESHOLD=%.1f invalid (must be >= 0), clamping to 0", MOTION_THRESHOLD)
    MOTION_THRESHOLD = 0.0
if MAX_POSTS_PER_DAY < 1:
    _log.warning("MAX_POSTS_PER_DAY=%d invalid, clamping to 1", MAX_POSTS_PER_DAY)
    MAX_POSTS_PER_DAY = 1
if not (-90.0 <= LOCATION_LAT <= 90.0):
    _log.warning("LOCATION_LAT=%.4f out of range [-90, 90], defaulting to 35.1495", LOCATION_LAT)
    LOCATION_LAT = 35.1495
if not (-180.0 <= LOCATION_LNG <= 180.0):
    _log.warning("LOCATION_LNG=%.4f out of range [-180, 180], defaulting to -89.8733", LOCATION_LNG)
    LOCATION_LNG = -89.8733
try:
    import zoneinfo as _zi
    _zi.ZoneInfo(LOCATION_TIMEZONE)
except Exception:
    _log.warning("LOCATION_TIMEZONE=%r invalid, defaulting to America/Chicago", LOCATION_TIMEZONE)
    LOCATION_TIMEZONE = "America/Chicago"
