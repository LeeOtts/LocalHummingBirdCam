#!/usr/bin/env python3
"""
Backyard Hummers — Hummingbird Feeder Camera

Monitors a feeder with a Pi camera, detects hummingbirds via motion + color,
records 30-second clips, generates funny comments via ChatGPT, and posts
to the "Backyard Hummers" Facebook page.
"""

import json
import logging
import signal
import sys
import threading
import time
from datetime import date
from logging.handlers import RotatingFileHandler
from pathlib import Path
from queue import Empty, Queue

import config
from camera.recorder import ClipRecorder
from camera.stream import CameraStream
from detection.motion_color import MotionColorDetector
from detection.vision_verify import verify_hummingbird
from schedule import is_daytime, get_schedule_info
from social.comment_generator import generate_comment, generate_good_morning, generate_good_night
from social.facebook_poster import FacebookPoster
from web.dashboard import start_web_server


def setup_logging():
    """Configure logging with rotation."""
    config.LOGS_DIR.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # File handler with rotation (5MB per file, keep 5 backups)
    file_handler = RotatingFileHandler(
        config.LOGS_DIR / "hummingbird.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
    )
    file_handler.setFormatter(formatter)

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)


logger = logging.getLogger(__name__)


class HummingbirdMonitor:
    """Main application controller."""

    def __init__(self):
        self.running = False
        self.camera = CameraStream()
        self.detector = MotionColorDetector()
        self.poster = FacebookPoster()
        self.clip_queue: Queue[Path] = Queue()
        self._last_detection_time = 0.0
        self._detections_today = 0
        self._start_time = 0.0
        self._counter_lock = threading.Lock()
        self.test_mode = config.TEST_MODE

        # Detection state for live feed overlay
        # "idle" | "motion" | "verifying" | "confirmed" | "rejected" | "sleeping"
        self.detection_state = "idle"
        self._detection_state_time = 0.0
        self._rejected_today = 0
        self._sleeping = False
        self._state_file = Path(config.BASE_DIR) / ".post_state.json"
        self._morning_posted, self._night_posted = self._load_post_state()

    def _load_post_state(self) -> tuple:
        """Load today's morning/night post flags from disk to prevent duplicates on restart."""
        try:
            if self._state_file.exists():
                data = json.loads(self._state_file.read_text())
                if data.get("date") == str(date.today()):
                    morning = data.get("morning_posted", False)
                    night = data.get("night_posted", False)
                    logger.info("Restored post state: morning=%s, night=%s", morning, night)
                    return morning, night
                # Stale date — reset
                logger.info("Post state is from %s, resetting for today", data.get("date"))
        except Exception:
            logger.exception("Failed to load post state")
        return False, False

    def _save_post_state(self):
        """Save morning/night post flags to disk (atomic write)."""
        try:
            tmp = self._state_file.with_suffix(".tmp")
            tmp.write_text(json.dumps({
                "date": str(date.today()),
                "morning_posted": self._morning_posted,
                "night_posted": self._night_posted,
            }))
            tmp.replace(self._state_file)
        except Exception:
            logger.exception("Failed to save post state")

    def start(self):
        """Start all components."""
        self.running = True
        self._start_time = time.time()
        if self.test_mode:
            logger.info("=== Backyard Hummers starting up (TEST MODE — no Facebook posting) ===")
        else:
            logger.info("=== Backyard Hummers starting up ===")

        # Start web dashboard FIRST — so it's always accessible for diagnostics
        start_web_server(self, host=config.WEB_HOST, port=config.WEB_PORT)

        # Start camera (graceful — dashboard still works if this fails)
        if not self.camera.start():
            logger.error("Camera not available — dashboard is up, will retry every 10 seconds")
            self.detection_state = "camera_error"

        recorder = ClipRecorder(self.camera)

        # Start the posting worker in a background thread
        post_thread = threading.Thread(target=self._post_worker, daemon=True)
        post_thread.start()

        # Retry any previously failed posts on startup
        self.poster.retry_failed_posts()

        logger.info("Monitoring feeder for hummingbirds...")

        try:
            self._detection_loop(recorder)
        except KeyboardInterrupt:
            logger.info("Interrupted by user")
        finally:
            self.stop()

    def _detection_loop(self, recorder: ClipRecorder):
        """Main loop: capture frames, detect hummingbirds, trigger recording."""
        while self.running:
            # Camera error — retry every 10 seconds
            if not self.camera.is_available:
                self.detection_state = "camera_error"
                time.sleep(10)
                if self.camera.retry():
                    logger.info("Camera reconnected!")
                    self.detection_state = "idle"
                    self.detector.reset()
                    recorder = ClipRecorder(self.camera)
                continue

            # Night mode — sleep when it's dark out
            if not is_daytime():
                if not self._sleeping:
                    self._sleeping = True
                    self.detection_state = "sleeping"
                    schedule_info = get_schedule_info()
                    logger.info(
                        "Night mode: sleeping until %s (sunset was %s)",
                        schedule_info["wake_time"],
                        schedule_info["sunset"],
                    )

                    # End-of-day post with tally
                    if not self._night_posted:
                        self._night_posted = True
                        self._save_post_state()
                        self._post_goodnight(schedule_info)

                time.sleep(30)  # Check every 30 seconds if it's daytime yet
                continue
            elif self._sleeping:
                self._sleeping = False
                self._night_posted = False  # Reset for next night
                self._save_post_state()
                self.detection_state = "idle"
                logger.info("Good morning! Waking up and starting detection.")

                # Morning post
                if not self._morning_posted:
                    self._morning_posted = True
                    self._save_post_state()
                    self._post_goodmorning()

            # First-ever wake up (not coming from sleep)
            elif not self._morning_posted and config.NIGHT_MODE_ENABLED:
                self._morning_posted = True
                self._save_post_state()
                self._post_goodmorning()

            try:
                frame = self.camera.capture_lores_array()
            except Exception:
                logger.exception("Failed to capture frame")
                time.sleep(1)
                continue

            detected = self.detector.detect(frame)

            # Clear stale detection state after 3 seconds
            if self.detection_state in ("confirmed", "rejected") and \
               time.time() - self._detection_state_time > 3:
                self.detection_state = "idle"

            if detected and self._cooldown_elapsed():
                # Motion+color passed — now verify with bird classifier
                self.detection_state = "verifying"
                self._detection_state_time = time.time()
                logger.info("Motion+color triggered — verifying with bird classifier...")

                # Get a full-res frame for the classifier
                verify_frame = frame
                if hasattr(self.camera, '_usb_lock'):
                    with self.camera._usb_lock:
                        if self.camera._usb_latest_frame is not None:
                            verify_frame = self.camera._usb_latest_frame.copy()

                if not verify_hummingbird(verify_frame):
                    self.detection_state = "rejected"
                    self._detection_state_time = time.time()
                    with self._counter_lock:
                        self._rejected_today += 1
                    logger.info("Bird classifier rejected — skipping recording")
                    self.detector.reset()
                    continue

                self.detection_state = "confirmed"
                self._detection_state_time = time.time()
                self._last_detection_time = time.time()
                with self._counter_lock:
                    self._detections_today += 1
                logger.info("Hummingbird confirmed! Starting recording...")

                # Reset detector state during recording
                self.detector.reset()

                # Record clip (blocks for CLIP_POST_SECONDS)
                clip_path = recorder.record_clip()
                if clip_path:
                    self.clip_queue.put(clip_path)
                    logger.info("Clip queued for posting: %s", clip_path.name)

                # Clean up old clips if disk is getting full
                self._cleanup_old_clips()

            elif self.detector._consecutive_detections > 0 and self.detection_state == "idle":
                self.detection_state = "motion"

            # Small sleep to avoid busy-looping — detection at ~15-20 fps on Pi 3B+
            time.sleep(0.05)

    def _cooldown_elapsed(self) -> bool:
        """Check if enough time has passed since the last detection."""
        elapsed = time.time() - self._last_detection_time
        return elapsed >= config.DETECTION_COOLDOWN_SECONDS

    def _cleanup_old_clips(self):
        """Remove oldest clips if total disk usage exceeds MAX_CLIPS_DISK_MB."""
        try:
            if not config.CLIPS_DIR.exists():
                return
            clips = sorted(config.CLIPS_DIR.glob("*.mp4"), key=lambda p: p.stat().st_mtime)
            total = sum(c.stat().st_size for c in clips)
            max_bytes = config.MAX_CLIPS_DISK_MB * 1_048_576

            while total > max_bytes and clips:
                old = clips.pop(0)
                total -= old.stat().st_size
                old.unlink()
                old.with_suffix(".txt").unlink(missing_ok=True)
                logger.info("Auto-deleted old clip: %s (disk cleanup)", old.name)
        except Exception:
            logger.exception("Clip cleanup failed")

    def _post_goodmorning(self):
        """Post a good morning greeting to Facebook."""
        try:
            schedule_info = get_schedule_info()
            caption = generate_good_morning(
                location=config.LOCATION_NAME,
                sunrise=schedule_info["sunrise"],
            )
            logger.info("Morning post: %s", caption)

            if self.test_mode:
                logger.info("TEST MODE — skipping morning Facebook post")
            else:
                self.poster.post_text(caption)
        except Exception:
            logger.exception("Failed to post morning greeting")

    def _post_goodnight(self, schedule_info):
        """Post a goodnight recap with daily tally to Facebook."""
        try:
            with self._counter_lock:
                det, rej = self._detections_today, self._rejected_today
            caption = generate_good_night(
                location=config.LOCATION_NAME,
                sunset=schedule_info["sunset"],
                detections=det,
                rejected=rej,
            )
            logger.info("Goodnight post: %s", caption)

            if self.test_mode:
                logger.info("TEST MODE — skipping goodnight Facebook post")
            else:
                self.poster.post_text(caption)

            # Reset daily counters
            with self._counter_lock:
                self._detections_today = 0
                self._rejected_today = 0
            self._morning_posted = False
            self._save_post_state()
        except Exception:
            logger.exception("Failed to post goodnight recap")

    def _post_worker(self):
        """Background thread: generate comment + post each clip to Facebook."""
        while self.running:
            try:
                clip_path = self.clip_queue.get(timeout=5)
            except Empty:
                continue

            try:
                logger.info("Generating caption for %s...", clip_path.name)
                with self._counter_lock:
                    det, rej = self._detections_today, self._rejected_today
                caption = generate_comment(
                    detections=det,
                    rejected=rej,
                )

                # Save caption alongside the clip
                caption_path = clip_path.with_suffix(".txt")
                caption_path.write_text(caption)
                logger.info("Caption saved: %s", caption_path.name)

                if self.test_mode:
                    logger.info("TEST MODE — skipping Facebook post for %s", clip_path.name)
                else:
                    logger.info("Posting to Facebook: %s", clip_path.name)
                    success = self.poster.post_video(clip_path, caption)

                    if success:
                        logger.info("Successfully posted %s", clip_path.name)
                    else:
                        logger.warning("Post failed (saved to retry queue): %s", clip_path.name)

            except Exception:
                logger.exception("Post worker error for %s", clip_path.name)

    def stop(self):
        """Shut down gracefully."""
        self.running = False
        self.camera.stop()
        logger.info("=== Backyard Hummers shut down ===")


def main():
    setup_logging()

    monitor = HummingbirdMonitor()

    # Handle SIGTERM for systemd graceful shutdown
    def handle_signal(signum, frame):
        logger.info("Received signal %d, shutting down...", signum)
        monitor.stop()
        sys.exit(0)

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    monitor.start()


if __name__ == "__main__":
    main()
