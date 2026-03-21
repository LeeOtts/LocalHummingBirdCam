#!/usr/bin/env python3
"""
Backyard Hummers — Hummingbird Feeder Camera

Monitors a feeder with a Pi camera, detects hummingbirds via motion + color,
records 30-second clips, generates funny comments via ChatGPT, and posts
to the "Backyard Hummers" Facebook page.
"""

import logging
import signal
import sys
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from queue import Empty, Queue

import config
from camera.recorder import ClipRecorder
from camera.stream import CameraStream
from detection.motion_color import MotionColorDetector
from detection.vision_verify import verify_hummingbird
from social.comment_generator import generate_comment
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
        self.test_mode = config.TEST_MODE

    def start(self):
        """Start all components."""
        self.running = True
        self._start_time = time.time()
        if self.test_mode:
            logger.info("=== Backyard Hummers starting up (TEST MODE — no Facebook posting) ===")
        else:
            logger.info("=== Backyard Hummers starting up ===")

        # Start camera
        self.camera.start()

        # Start web dashboard
        start_web_server(self, host=config.WEB_HOST, port=config.WEB_PORT)
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
            try:
                frame = self.camera.capture_lores_array()
            except Exception:
                logger.exception("Failed to capture frame")
                time.sleep(1)
                continue

            detected = self.detector.detect(frame)

            if detected and self._cooldown_elapsed():
                # Motion+color passed — now verify with bird classifier
                logger.info("Motion+color triggered — verifying with bird classifier...")

                # Get a full-res frame for the classifier
                verify_frame = frame
                if hasattr(self.camera, '_usb_lock'):
                    with self.camera._usb_lock:
                        if self.camera._usb_latest_frame is not None:
                            verify_frame = self.camera._usb_latest_frame.copy()

                if not verify_hummingbird(verify_frame):
                    logger.info("Bird classifier rejected — skipping recording")
                    self.detector.reset()
                    continue

                self._last_detection_time = time.time()
                self._detections_today += 1
                logger.info("Hummingbird confirmed! Starting recording...")

                # Reset detector state during recording
                self.detector.reset()

                # Record clip (blocks for CLIP_POST_SECONDS)
                clip_path = recorder.record_clip()
                if clip_path:
                    self.clip_queue.put(clip_path)
                    logger.info("Clip queued for posting: %s", clip_path.name)

            # Small sleep to avoid busy-looping — detection at ~15-20 fps on Pi 3B+
            time.sleep(0.05)

    def _cooldown_elapsed(self) -> bool:
        """Check if enough time has passed since the last detection."""
        elapsed = time.time() - self._last_detection_time
        return elapsed >= config.DETECTION_COOLDOWN_SECONDS

    def _post_worker(self):
        """Background thread: generate comment + post each clip to Facebook."""
        while self.running:
            try:
                clip_path = self.clip_queue.get(timeout=5)
            except Empty:
                continue

            try:
                logger.info("Generating caption for %s...", clip_path.name)
                caption = generate_comment()

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
