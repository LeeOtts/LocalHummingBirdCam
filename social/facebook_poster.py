"""Post video clips to the 'Backyard Hummers' Facebook page via Graph API."""

import json
import logging
import os
from datetime import date
from pathlib import Path

import requests

import config

logger = logging.getLogger(__name__)

GRAPH_API_VERSION = os.getenv("FACEBOOK_API_VERSION", "v22.0")
GRAPH_API_BASE = f"https://graph.facebook.com/{GRAPH_API_VERSION}"


class FacebookPoster:
    """Handles video uploads to a Facebook Page using the Resumable Upload API."""

    def __init__(self):
        self.page_id = config.FACEBOOK_PAGE_ID
        self.access_token = config.FACEBOOK_PAGE_ACCESS_TOKEN
        self._posts_today = 0
        self._today = date.today()
        self._retry_in_progress = False

    def _check_rate_limit(self) -> bool:
        """Enforce daily post limit (uses location timezone for midnight)."""
        try:
            from datetime import datetime, timezone
            try:
                from zoneinfo import ZoneInfo
                tz = ZoneInfo(config.LOCATION_TIMEZONE)
            except Exception:
                import pytz
                tz = pytz.timezone(config.LOCATION_TIMEZONE)
            today = datetime.now(tz).date()
        except Exception:
            today = date.today()

        if today != self._today:
            self._today = today
            self._posts_today = 0
        if self._posts_today >= config.MAX_POSTS_PER_DAY:
            logger.warning("Daily post limit reached (%d)", config.MAX_POSTS_PER_DAY)
            return False
        return True

    def post_video(self, mp4_path: Path, caption: str) -> bool:
        """
        Upload a video to the Facebook Page with the given caption.
        Uses the Resumable Upload API for reliability.
        Returns True on success.
        """
        if not self.page_id or not self.access_token:
            logger.error("Facebook credentials not configured")
            if not self._retry_in_progress:
                self._save_to_retry_queue(mp4_path, caption)
            return False

        if not self._check_rate_limit():
            if not self._retry_in_progress:
                self._save_to_retry_queue(mp4_path, caption)
            return False

        try:
            file_size = os.path.getsize(mp4_path)

            # Step 1: Initialize upload session
            init_resp = requests.post(
                f"{GRAPH_API_BASE}/{self.page_id}/videos",
                data={
                    "upload_phase": "start",
                    "file_size": file_size,
                    "access_token": self.access_token,
                },
                timeout=30,
            )
            init_resp.raise_for_status()
            upload_session_id = init_resp.json()["upload_session_id"]
            logger.info("Upload session started: %s", upload_session_id)

            # Step 2: Transfer the video file
            with open(mp4_path, "rb") as f:
                transfer_resp = requests.post(
                    f"{GRAPH_API_BASE}/{self.page_id}/videos",
                    data={
                        "upload_phase": "transfer",
                        "upload_session_id": upload_session_id,
                        "start_offset": "0",
                        "access_token": self.access_token,
                    },
                    files={"video_file_chunk": f},
                    timeout=120,
                )
                transfer_resp.raise_for_status()
            logger.info("Video transferred successfully")

            # Step 3: Finish upload and publish
            finish_resp = requests.post(
                f"{GRAPH_API_BASE}/{self.page_id}/videos",
                data={
                    "upload_phase": "finish",
                    "upload_session_id": upload_session_id,
                    "title": "Hummingbird Sighting!",
                    "description": caption,
                    "access_token": self.access_token,
                },
                timeout=30,
            )
            finish_resp.raise_for_status()
            post_id = finish_resp.json().get("id", "unknown")
            logger.info("Posted to Facebook! Post ID: %s", post_id)

            self._posts_today += 1
            return True

        except requests.RequestException as e:
            if hasattr(e, 'response') and e.response is not None:
                logger.error("Facebook response: %s", e.response.text)
            logger.exception("Facebook upload failed for %s", mp4_path.name)
            if not self._retry_in_progress:
                self._save_to_retry_queue(mp4_path, caption)
            return False

    def post_text(self, message: str) -> bool:
        """Post a text-only status update to the Facebook Page."""
        if not self.page_id or not self.access_token:
            logger.error("Facebook credentials not configured")
            return False

        try:
            resp = requests.post(
                f"{GRAPH_API_BASE}/{self.page_id}/feed",
                data={
                    "message": message,
                    "access_token": self.access_token,
                },
                timeout=30,
            )
            resp.raise_for_status()
            post_id = resp.json().get("id", "unknown")
            logger.info("Text post published! Post ID: %s", post_id)
            return True

        except requests.RequestException as e:
            if hasattr(e, 'response') and e.response is not None:
                logger.error("Facebook response: %s", e.response.text)
            logger.exception("Facebook text post failed")
            return False

    def _save_to_retry_queue(self, mp4_path: Path, caption: str):
        """Save failed posts to a JSON retry queue for later."""
        queue = []
        if config.RETRY_QUEUE_FILE.exists():
            try:
                queue = json.loads(config.RETRY_QUEUE_FILE.read_text())
            except (json.JSONDecodeError, OSError):
                queue = []

        queue.append({
            "mp4_path": str(mp4_path),
            "caption": caption,
        })

        config.RETRY_QUEUE_FILE.write_text(json.dumps(queue, indent=2))
        logger.info("Saved to retry queue: %s", mp4_path.name)

    def retry_failed_posts(self):
        """Attempt to post any items in the retry queue."""
        if not config.RETRY_QUEUE_FILE.exists():
            return

        try:
            queue = json.loads(config.RETRY_QUEUE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            return

        if not queue:
            return

        remaining = []
        self._retry_in_progress = True
        try:
            for item in queue:
                mp4_path = Path(item["mp4_path"])
                if not mp4_path.exists():
                    logger.warning("Retry clip missing, skipping: %s", mp4_path)
                    continue
                if not self._check_rate_limit():
                    remaining.append(item)
                    continue
                success = self.post_video(mp4_path, item["caption"])
                if not success:
                    remaining.append(item)
        finally:
            self._retry_in_progress = False

        config.RETRY_QUEUE_FILE.write_text(json.dumps(remaining, indent=2))
