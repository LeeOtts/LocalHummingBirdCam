#!/usr/bin/env python3
"""
Backyard Hummers — Hummingbird Feeder Camera

Monitors a feeder with a Pi camera, detects hummingbirds via motion + color,
records 30-second clips, generates funny comments via ChatGPT, and posts
to the "Backyard Hummers" Facebook page.
"""

import gc
import json
import logging
import signal
import sys
import threading
import time
from datetime import date, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from queue import Empty, Queue

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from pytz import timezone as _pytz_tz
    ZoneInfo = lambda key: _pytz_tz(key)

import cv2

import config
from utils import safe_read_json, safe_write_json

_local_tz = ZoneInfo(config.LOCATION_TIMEZONE)

from camera.recorder import ClipRecorder
from camera.stream import CameraStream
from data.sightings import SightingsDB
from detection.motion_color import MotionColorDetector
from detection.vision_verify import verify_hummingbird
from schedule import is_daytime, get_schedule_info
from social.comment_generator import (
    generate_comment, generate_good_morning, generate_good_night, generate_milestone_post,
)
from social.facebook_poster import FacebookPoster
from social.poster_manager import PosterManager
from web.dashboard import start_web_server


class _TZFormatter(logging.Formatter):
    """Logging formatter that converts timestamps to the configured timezone."""

    def __init__(self, fmt=None, datefmt=None, tz=None):
        super().__init__(fmt, datefmt)
        self._tz = tz

    def formatTime(self, record, datefmt=None):
        dt = datetime.fromtimestamp(record.created, tz=self._tz)
        if datefmt:
            return dt.strftime(datefmt)
        return dt.strftime("%Y-%m-%d %H:%M:%S")


def setup_logging():
    """Configure logging with rotation — timestamps in local timezone."""
    config.LOGS_DIR.mkdir(parents=True, exist_ok=True)

    local_tz = ZoneInfo(config.LOCATION_TIMEZONE)
    formatter = _TZFormatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        tz=local_tz,
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


def _get_day_part(hour: int) -> str:
    """Return a human-friendly time-of-day label."""
    if hour < 8:
        return "early morning"
    if hour < 11:
        return "mid-morning"
    if hour < 14:
        return "midday"
    if hour < 17:
        return "afternoon"
    return "evening"


# Milestone thresholds for lifetime detection counts
_MILESTONES = {100, 250, 500, 1000, 1500, 2000, 2500, 3000, 5000, 10000}


class HummingbirdMonitor:
    """Main application controller."""

    def __init__(self):
        self.running = False
        self.camera = CameraStream()
        self.detector = MotionColorDetector()
        self.poster = FacebookPoster()  # Keep for backward compat (token verify, retry)
        self.poster_manager = PosterManager()  # Multi-platform posting
        self.sightings_db = SightingsDB()
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

        # Engagement tracking
        self._detection_hours: list = []  # hour ints for peak-hour calc
        self._yesterday_detections = 0
        self._all_time_record = 0
        self._total_lifetime_detections = 0

        # SSE event queue for live dashboard notifications
        self._sse_subscribers: list[Queue] = []

        self._morning_posted, self._night_posted, self._digest_posted = self._load_post_state()

    def _load_post_state(self) -> tuple:
        """Load morning/night/digest post flags from disk to prevent duplicates on restart.
        
        Uses timestamp-based tracking (Unix time) instead of date strings to survive
        system clock jumps (e.g., NTP resync, manual adjustment). Posts are blocked
        if they've already been made in the last 24 hours.
        """
        try:
            data = safe_read_json(self._state_file, default={})
            if not data:
                return False, False, False

            # Always restore lifetime stats
            self._all_time_record = data.get("all_time_record", 0)
            self._total_lifetime_detections = data.get("total_lifetime_detections", 0)
            self._yesterday_detections = data.get("yesterday_detections", 0)

            # Timestamp-based tracking: Check if > 24 hours since last post
            # This survives clock jumps (unlike date-based tracking)
            now = time.time()
            SECONDS_PER_DAY = 24 * 60 * 60
            
            morning_ts = data.get("last_morning_post_ts", 0)
            night_ts = data.get("last_night_post_ts", 0)
            digest_ts = data.get("last_digest_post_ts", 0)
            
            # Posts are active if they were posted < 24 hours ago
            morning = (now - morning_ts) < SECONDS_PER_DAY
            night = (now - night_ts) < SECONDS_PER_DAY
            digest = (now - digest_ts) < SECONDS_PER_DAY
            
            logger.info(
                "Restored post state (timestamp-based): morning=%s (%.1fh ago), "
                "night=%s (%.1fh ago), digest=%s (%.1fh ago)",
                morning, (now - morning_ts) / 3600 if morning_ts else float('inf'),
                night, (now - night_ts) / 3600 if night_ts else float('inf'),
                digest, (now - digest_ts) / 3600 if digest_ts else float('inf'),
            )
            return morning, night, digest
            
        except (KeyError, TypeError, ValueError):
            logger.exception("Failed to load post state")
        return False, False, False

    def _save_post_state(self):
        """Save morning/night/digest post flags and engagement stats to disk (atomic write).
        
        Uses Unix timestamps to track last post time (survives clock jumps).
        """
        now = time.time()
        safe_write_json(self._state_file, {
            # Timestamp-based tracking (replaces old date-based tracking)
            "last_morning_post_ts": now if self._morning_posted else 0,
            "last_night_post_ts": now if self._night_posted else 0,
            "last_digest_post_ts": now if self._digest_posted else 0,
            # Legacy date field for debugging/inspection (no longer used for logic)
            "date": str(date.today()),
            # Engagement stats
            "yesterday_detections": self._yesterday_detections,
            "all_time_record": self._all_time_record,
            "total_lifetime_detections": self._total_lifetime_detections,
        })

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

        # Verify Facebook token & permissions at startup
        fb_status = self.poster.verify_token()
        if fb_status["warnings"]:
            for w in fb_status["warnings"]:
                logger.warning("⚠️  Facebook: %s", w)
            logger.warning(
                "⚠️  If your app is in Development mode, posts will only be "
                "visible to app admins. Toggle to Live at developers.facebook.com"
            )
        else:
            logger.info("✅ Facebook token verified — scopes: %s", fb_status["scopes"])

        # Retry any previously failed posts on startup
        self.poster.retry_failed_posts()

        # Start AI comment responder if enabled
        if config.AUTO_REPLY_ENABLED:
            from social.comment_responder import CommentResponder
            self._comment_responder = CommentResponder(self.poster, self.sightings_db)
            responder_thread = threading.Thread(
                target=self._comment_responder.run, daemon=True)
            responder_thread.start()
            logger.info("AI comment responder enabled (max %d/hour)", config.AUTO_REPLY_MAX_PER_HOUR)

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

                    # End-of-day post with tally (background thread — don't block detection)
                    if not self._night_posted:
                        self._night_posted = True
                        self._save_post_state()
                        threading.Thread(
                            target=self._post_goodnight,
                            args=(schedule_info,),
                            daemon=True,
                        ).start()

                time.sleep(30)  # Check every 30 seconds if it's daytime yet
                continue
            elif self._sleeping:
                self._sleeping = False
                self._night_posted = False  # Reset for next night
                self._save_post_state()
                self.detection_state = "idle"
                logger.info("Good morning! Waking up and starting detection.")

                # Morning post (background thread — don't block detection)
                if not self._morning_posted:
                    self._morning_posted = True
                    self._save_post_state()
                    threading.Thread(target=self._post_goodmorning, daemon=True).start()

            # First-ever wake up (not coming from sleep)
            elif not self._morning_posted and config.NIGHT_MODE_ENABLED:
                self._morning_posted = True
                self._save_post_state()
                threading.Thread(target=self._post_goodmorning, daemon=True).start()

            try:
                frame = self.camera.capture_lores_array()
            except (RuntimeError, cv2.error):
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
                verify_frame = self.camera.get_full_res_frame() or frame

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
                    self._total_lifetime_detections += 1
                    self._detection_hours.append(datetime.now(tz=_local_tz).hour)
                logger.info("Hummingbird confirmed! Starting recording...")

                # Notify SSE subscribers
                self._broadcast_sse({
                    "event": "detection",
                    "detections_today": self._detections_today,
                    "time": datetime.now(tz=_local_tz).strftime("%I:%M %p").lstrip("0"),
                })

                # Reset detector state during recording
                self.detector.reset()

                # Record clip (blocks for CLIP_POST_SECONDS)
                clip_path = recorder.record_clip()
                if clip_path:
                    self.clip_queue.put(clip_path)
                    logger.info("Clip queued for posting: %s", clip_path.name)

                # Clean up old clips if disk is getting full
                self._cleanup_old_clips()

                # Force GC to return recording memory to OS
                gc.collect()

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

    def _broadcast_sse(self, data: dict):
        """Send an event to all SSE subscribers."""
        import json as _json
        dead = []
        for q in self._sse_subscribers:
            try:
                q.put_nowait(_json.dumps(data))
            except Exception:
                dead.append(q)
        for q in dead:
            try:
                self._sse_subscribers.remove(q)
            except ValueError:
                pass

    def _get_todays_clip(self):
        """Return the most recent clip from today, or None."""
        try:
            today_str = datetime.now(tz=_local_tz).strftime("%Y%m%d")
            clips = sorted(
                config.CLIPS_DIR.glob(f"hummer_{today_str}_*.mp4"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            return clips[0] if clips else None
        except Exception:
            return None

    def _post_goodmorning(self):
        """Post a good morning greeting to all platforms with a live camera snapshot."""
        try:
            now = datetime.now(tz=_local_tz)
            schedule_info = get_schedule_info()

            # Fetch weather for morning post context
            weather = None
            if config.OPENWEATHERMAP_API_KEY:
                from analytics.patterns import get_weather
                weather = get_weather(config.LOCATION_LAT, config.LOCATION_LNG)

            captions = generate_good_morning(
                location=config.LOCATION_NAME,
                sunrise=schedule_info["sunrise"],
                platforms=self.poster_manager.platform_names,
                day_of_week=now.strftime("%A"),
                month=now.strftime("%B"),
                yesterday_detections=self._yesterday_detections or None,
                lifetime_total=self._total_lifetime_detections,
                weather=weather,
            )
            logger.info("Morning post: %s", captions)

            if self.test_mode:
                logger.info("TEST MODE — skipping morning post")
                return

            # Try to attach a live camera snapshot
            snapshot_path = config.CLIPS_DIR / f"morning_{now.strftime('%Y%m%d')}.jpg"
            if self.camera.capture_snapshot(snapshot_path):
                logger.info("Morning post: attaching camera snapshot")
                results = self.poster_manager.post_photo(snapshot_path, captions)
                try:
                    snapshot_path.unlink(missing_ok=True)
                except Exception:
                    pass
                if any(results.values()):
                    return

            # Fallback to text-only
            logger.info("Morning post: no snapshot available, posting text-only")
            self.poster_manager.post_text(captions)
        except Exception:
            logger.exception("Failed to post morning greeting")

    def _post_goodnight(self, schedule_info):
        """Post a goodnight recap with daily tally to Facebook."""
        try:
            now = datetime.now(tz=_local_tz)
            with self._counter_lock:
                det, rej = self._detections_today, self._rejected_today
                hours = list(self._detection_hours)

            # Calculate peak hour
            peak_hour = None
            if hours:
                most_common = max(set(hours), key=hours.count)
                h = most_common % 12 or 12
                ampm = "AM" if most_common < 12 else "PM"
                peak_hour = f"{h} {ampm}"

            # Check for new record
            is_record = det > self._all_time_record and det > 0

            captions = generate_good_night(
                location=config.LOCATION_NAME,
                sunset=schedule_info["sunset"],
                detections=det,
                rejected=rej,
                platforms=self.poster_manager.platform_names,
                day_of_week=now.strftime("%A"),
                month=now.strftime("%B"),
                peak_hour=peak_hour,
                is_record=is_record,
                lifetime_total=self._total_lifetime_detections,
            )
            logger.info("Goodnight post: %s", captions)

            if self.test_mode:
                logger.info("TEST MODE — skipping goodnight post")
            else:
                posted = False

                # Try to attach today's last video clip
                clip = self._get_todays_clip()
                if clip:
                    logger.info("Goodnight post: attaching today's clip %s", clip.name)
                    results = self.poster_manager.post_video(clip, captions)
                    posted = any(results.values())

                # Fallback: try a live camera snapshot
                if not posted:
                    now_ts = datetime.now(tz=_local_tz)
                    snapshot_path = config.CLIPS_DIR / f"goodnight_{now_ts.strftime('%Y%m%d')}.jpg"
                    if self.camera.capture_snapshot(snapshot_path):
                        logger.info("Goodnight post: no clips today, attaching snapshot")
                        results = self.poster_manager.post_photo(snapshot_path, captions)
                        posted = any(results.values())
                        try:
                            snapshot_path.unlink(missing_ok=True)
                        except Exception:
                            pass

                # Final fallback: text-only
                if not posted:
                    logger.info("Goodnight post: no media available, posting text-only")
                    self.poster_manager.post_text(captions)

            # Save daily stats to database
            if self.sightings_db:
                self.sightings_db.save_daily_stats(
                    dt=date.today(),
                    detections=det,
                    rejected=rej,
                    peak_hour=peak_hour,
                    is_record=is_record,
                )

            # Rotate daily stats before resetting
            self._yesterday_detections = det
            if det > self._all_time_record:
                self._all_time_record = det

            # Weekly digest — post after goodnight on the configured day
            if (config.WEEKLY_DIGEST_ENABLED
                    and not self._digest_posted
                    and now.strftime("%A").lower() == config.WEEKLY_DIGEST_DAY):
                threading.Thread(
                    target=self._post_weekly_digest, daemon=True
                ).start()

            # Reset daily counters
            with self._counter_lock:
                self._detections_today = 0
                self._rejected_today = 0
                self._detection_hours.clear()
            self._morning_posted = False
            self._save_post_state()
        except Exception:
            logger.exception("Failed to post goodnight recap")

    def _post_weekly_digest(self):
        """Post a weekly recap to all platforms with an optional thumbnail collage."""
        try:
            from social.digest import generate_weekly_digest, create_thumbnail_collage

            captions = generate_weekly_digest(
                self.sightings_db,
                platforms=self.poster_manager.platform_names,
            )
            if not captions:
                logger.info("Weekly digest: no data to report, skipping")
                return

            logger.info("Weekly digest: %s", captions)

            if self.test_mode:
                logger.info("TEST MODE — skipping weekly digest post")
                self._digest_posted = True
                self._save_post_state()
                return

            # Try to create a thumbnail collage for a photo post
            collage_path = config.CLIPS_DIR / f"digest_{date.today()}.jpg"
            if create_thumbnail_collage(self.sightings_db, collage_path):
                results = self.poster_manager.post_photo(collage_path, captions)
                try:
                    collage_path.unlink(missing_ok=True)
                except Exception:
                    pass
            else:
                results = self.poster_manager.post_text(captions)

            if any(results.values()):
                logger.info("Weekly digest posted to: %s",
                            [p for p, ok in results.items() if ok])
            else:
                logger.warning("Weekly digest: all platforms failed")

            self._digest_posted = True
            self._save_post_state()
        except Exception:
            logger.exception("Failed to post weekly digest")

    def _extract_frame(self, clip_path: Path) -> Path | None:
        """Extract a representative frame from a clip for vision captions."""
        cap = None
        try:
            frame_path = clip_path.with_suffix(".thumb.jpg")
            cap = cv2.VideoCapture(str(clip_path))
            fps = cap.get(cv2.CAP_PROP_FPS) or 15
            # Jump to ~3 seconds into the post-detection segment
            target_frame = int((config.CLIP_PRE_SECONDS + 3) * fps)
            cap.set(cv2.CAP_PROP_POS_FRAMES, target_frame)
            ret, frame = cap.read()
            if ret and frame is not None:
                cv2.imwrite(str(frame_path), frame)
                return frame_path
        except Exception:
            logger.warning("Failed to extract frame from %s", clip_path.name)
        finally:
            if cap is not None:
                cap.release()
        return None

    def _post_worker(self):
        """Background thread: generate comment + post each clip."""
        while self.running:
            try:
                clip_path = self.clip_queue.get(timeout=5)
            except Empty:
                continue

            frame_path = None
            try:
                logger.info("Generating caption for %s...", clip_path.name)
                now = datetime.now(tz=_local_tz)
                schedule_info = get_schedule_info()
                with self._counter_lock:
                    det, rej = self._detections_today, self._rejected_today
                    lifetime = self._total_lifetime_detections

                elapsed = time.time() - self._last_detection_time if self._last_detection_time else None
                # Only mention gap if under 2 hours and not the current detection
                seconds_since = int(elapsed) if elapsed and elapsed < 7200 and elapsed > 5 else None

                milestone = None
                if lifetime in _MILESTONES:
                    milestone = f"Lifetime visitor #{lifetime}!"

                # Fetch current weather for caption context
                weather = None
                if config.OPENWEATHERMAP_API_KEY:
                    from analytics.patterns import get_weather
                    weather = get_weather(config.LOCATION_LAT, config.LOCATION_LNG)

                # Extract a frame for vision captions
                frame_path = self._extract_frame(clip_path)

                captions = generate_comment(
                    detections=det,
                    rejected=rej,
                    platforms=self.poster_manager.platform_names,
                    visit_number=det,
                    time_of_day=now.strftime("%I:%M %p").lstrip("0"),
                    day_part=_get_day_part(now.hour),
                    day_of_week=now.strftime("%A"),
                    seconds_since_last=seconds_since,
                    month=now.strftime("%B"),
                    sunrise=schedule_info.get("sunrise", ""),
                    sunset=schedule_info.get("sunset", ""),
                    milestone=milestone,
                    frame_path=frame_path,
                    weather=weather,
                )
                caption = captions.get("Facebook") or next(iter(captions.values()))

                # Save caption alongside the clip
                caption_path = clip_path.with_suffix(".txt")
                caption_path.write_text(caption)
                logger.info("Caption saved: %s", caption_path.name)

                # Record sighting in database
                if self.sightings_db:
                    self.sightings_db.record_sighting(
                        clip_filename=clip_path.name,
                        caption=caption,
                        frame_path=str(frame_path) if frame_path else None,
                        confidence=None,
                    )

                if self.test_mode:
                    logger.info("TEST MODE — skipping posting for %s", clip_path.name)
                else:
                    logger.info("Posting to platforms: %s", clip_path.name)
                    results = self.poster_manager.post_video(clip_path, captions)
                    any_success = any(results.values())

                    if any_success:
                        logger.info("Posted %s to: %s", clip_path.name,
                                    [p for p, ok in results.items() if ok])
                    else:
                        logger.warning("All posts failed for %s", clip_path.name)

                    # Post a separate milestone celebration if we hit one
                    if milestone:
                        days_running = (date.today() - date(2025, 3, 1)).days  # approx start
                        milestone_captions = generate_milestone_post(
                            lifetime_count=lifetime,
                            today_count=det,
                            days_running=days_running,
                            platforms=self.poster_manager.platform_names,
                        )
                        logger.info("Milestone post: %s", milestone_captions)
                        self.poster_manager.post_text(milestone_captions)

            except Exception:
                logger.exception("Post worker error for %s", clip_path.name)
            finally:
                # Clean up thumbnail to avoid disk accumulation
                if frame_path:
                    try:
                        Path(frame_path).unlink(missing_ok=True)
                    except Exception:
                        pass

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
