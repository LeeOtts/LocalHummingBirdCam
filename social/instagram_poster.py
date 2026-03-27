"""Post video clips and photos to Instagram via Meta's Graph API."""

import logging
import os
import time
from datetime import date
from pathlib import Path

import requests

import config
from social.base_poster import SocialPoster
from utils import safe_read_json, safe_write_json

logger = logging.getLogger(__name__)

GRAPH_API_BASE = f"https://graph.instagram.com/{config.INSTAGRAM_API_VERSION}"


class InstagramPoster(SocialPoster):
    """Handles media uploads to Instagram via Meta's Graph API."""

    platform_name = "Instagram"

    def __init__(self):
        self.business_account_id = config.INSTAGRAM_BUSINESS_ACCOUNT_ID
        self.user_id = config.INSTAGRAM_USER_ID
        self.access_token = config.FACEBOOK_PAGE_ACCESS_TOKEN  # Reuse Facebook token
        self._posts_today = 0
        self._today = self._date_in_local_tz()
        self._retry_in_progress = False

    def is_configured(self) -> bool:
        return bool(
            self.business_account_id
            and self.user_id
            and self.access_token
        )

    @staticmethod
    def _date_in_local_tz():
        """Return today's date in the configured location timezone."""
        from datetime import datetime
        try:
            try:
                from zoneinfo import ZoneInfo
                tz = ZoneInfo(config.LOCATION_TIMEZONE)
            except ImportError:
                import pytz
                tz = pytz.timezone(config.LOCATION_TIMEZONE)
            return datetime.now(tz).date()
        except (KeyError, ValueError):
            return date.today()

    def _check_rate_limit(self) -> bool:
        """Enforce daily post limit using Instagram-specific limit."""
        today = self._date_in_local_tz()

        if today != self._today:
            self._today = today
            self._posts_today = 0
        if self._posts_today >= config.INSTAGRAM_MAX_POSTS_PER_DAY:
            logger.warning("Instagram daily post limit reached (%d)", config.INSTAGRAM_MAX_POSTS_PER_DAY)
            return False
        return True

    def post_video(self, mp4_path: Path, caption: str) -> bool:
        """
        Upload a video to Instagram using the graph API.
        Videos are posted as single media items (not reels, not carousel).
        Returns True on success.
        """
        if not self.is_configured():
            logger.error("Instagram credentials not configured")
            if not self._retry_in_progress:
                self._save_to_retry_queue(mp4_path, caption, "video")
            return False

        if not self._check_rate_limit():
            if not self._retry_in_progress:
                self._save_to_retry_queue(mp4_path, caption, "video")
            return False

        try:
            file_size = os.path.getsize(mp4_path)

            # Step 1: Initialize upload session
            init_resp = requests.post(
                f"{GRAPH_API_BASE}/{self.business_account_id}/media",
                data={
                    "media_type": "VIDEO",
                    "video_data": "placeholder_for_chunked_upload",
                    "access_token": self.access_token,
                },
                timeout=30,
            )

            # If simple upload fails, try chunked upload approach
            if init_resp.status_code != 200:
                return self._post_video_chunked(mp4_path, caption)

            init_data = init_resp.json()
            if "error" in init_data:
                logger.warning("Instagram video init failed: %s", init_data.get("error"))
                return self._post_video_chunked(mp4_path, caption)

            media_id = init_data.get("id")
            if not media_id:
                return self._post_video_chunked(mp4_path, caption)

            # Step 2: Transfer video using chunked approach
            with open(mp4_path, "rb") as f:
                video_data = f.read()

            # Check file size (Instagram max: 1GB, but practically limit to 500MB)
            if len(video_data) > 500 * 1024 * 1024:
                logger.error("Instagram: video too large (%d MB)", len(video_data) // (1024 * 1024))
                return False

            # Upload video in chunks
            chunk_size = 5 * 1024 * 1024  # 5MB chunks
            offset = 0
            while offset < len(video_data):
                chunk = video_data[offset : offset + chunk_size]
                chunk_resp = requests.post(
                    f"{GRAPH_API_BASE}/{media_id}",
                    data={
                        "upload_phase": "transfer",
                        "start_offset": str(offset),
                        "access_token": self.access_token,
                    },
                    files={"video_data": chunk},
                    timeout=120,
                )
                chunk_resp.raise_for_status()
                offset += len(chunk)

            del video_data  # Free memory

            # Step 3: Finish upload and publish
            finish_resp = requests.post(
                f"{GRAPH_API_BASE}/{media_id}",
                data={
                    "upload_phase": "finish",
                    "caption": caption,
                    "access_token": self.access_token,
                },
                timeout=30,
            )
            finish_resp.raise_for_status()
            logger.info("Posted video to Instagram! Media ID: %s", media_id)

            self._posts_today += 1
            return True

        except requests.RequestException as e:
            if hasattr(e, 'response') and e.response is not None:
                logger.error("Instagram response: %s", e.response.text)
            logger.exception("Instagram video upload failed for %s", mp4_path.name)
            if not self._retry_in_progress:
                self._save_to_retry_queue(mp4_path, caption, "video")
            return False

    def _post_video_chunked(self, mp4_path: Path, caption: str) -> bool:
        """Alternative chunked video upload using media container approach."""
        try:
            # First, upload the video file itself
            with open(mp4_path, "rb") as f:
                video_data = f.read()

            if len(video_data) > 500 * 1024 * 1024:
                logger.error("Instagram: video too large (%d MB)", len(video_data) // (1024 * 1024))
                return False

            # Create media object with video
            create_resp = requests.post(
                f"{GRAPH_API_BASE}/{self.business_account_id}/media",
                data={
                    "media_type": "VIDEO",
                    "caption": caption,
                    "access_token": self.access_token,
                },
                files={"video_data": video_data},
                timeout=120,
            )
            create_resp.raise_for_status()
            media_data = create_resp.json()
            media_id = media_data.get("id")

            if not media_id:
                logger.error("Instagram: no media ID in response: %s", media_data)
                return False

            logger.info("Posted video to Instagram (chunked)! Media ID: %s", media_id)
            self._posts_today += 1
            return True

        except requests.RequestException as e:
            if hasattr(e, 'response') and e.response is not None:
                logger.error("Instagram response: %s", e.response.text)
            logger.exception("Instagram chunked video upload failed for %s", mp4_path.name)
            return False

    def post_photo(self, image_path: Path, caption: str) -> bool:
        """Upload a photo to Instagram and post to feed."""
        if not self.is_configured():
            logger.error("Instagram credentials not configured")
            return False

        if not self._check_rate_limit():
            return False

        try:
            with open(image_path, "rb") as f:
                image_data = f.read()

            # Create media object with photo
            resp = requests.post(
                f"{GRAPH_API_BASE}/{self.business_account_id}/media",
                data={
                    "media_type": "IMAGE",
                    "caption": caption,
                    "access_token": self.access_token,
                },
                files={"image_data": image_data},
                timeout=60,
            )
            resp.raise_for_status()
            resp_data = resp.json()
            media_id = resp_data.get("id")

            if not media_id:
                logger.error("Instagram: no media ID in response: %s", resp_data)
                return False

            logger.info("Posted photo to Instagram! Media ID: %s", media_id)
            self._posts_today += 1
            return True

        except requests.RequestException as e:
            if hasattr(e, "response") and e.response is not None:
                logger.error("Instagram response: %s", e.response.text)
            logger.exception("Instagram photo upload failed for %s", image_path.name)
            return False

    def post_text(self, message: str) -> bool:
        """Post text-only content to Instagram (as a caption-only media, if supported)."""
        if not self.is_configured():
            logger.error("Instagram credentials not configured")
            return False

        # Instagram doesn't support text-only posts via Graph API, so we log a warning
        logger.warning("Instagram: text-only posts not supported via Graph API (skipped)")
        return False

    def _save_to_retry_queue(self, mp4_path: Path, caption: str, media_type: str = "video"):
        """Save failed posts to a JSON retry queue for later."""
        queue = safe_read_json(config.RETRY_QUEUE_FILE, default=[])

        # Prune entries that point to missing clips
        queue = [e for e in queue if Path(e.get("path", "")).exists()]

        queue.append({
            "platform": "Instagram",
            "path": str(mp4_path),
            "caption": caption,
            "media_type": media_type,
        })

        # Cap to avoid unbounded growth
        if len(queue) > config.MAX_RETRY_QUEUE_SIZE:
            dropped = len(queue) - config.MAX_RETRY_QUEUE_SIZE
            logger.warning("Retry queue full — dropping %d oldest entries", dropped)
            queue = queue[-config.MAX_RETRY_QUEUE_SIZE:]

        safe_write_json(config.RETRY_QUEUE_FILE, queue)
        logger.info("Saved to Instagram retry queue (%d/%d): %s", len(queue), config.MAX_RETRY_QUEUE_SIZE, mp4_path.name)

    def retry_failed_posts(self):
        """Attempt to post any Instagram items in the retry queue."""
        if not config.RETRY_QUEUE_FILE.exists():
            return

        queue = safe_read_json(config.RETRY_QUEUE_FILE, default=[])
        if not isinstance(queue, list):
            return

        if not queue:
            return

        remaining = []
        self._retry_in_progress = True
        try:
            for item in queue:
                # Skip non-Instagram items
                if item.get("platform") != "Instagram":
                    remaining.append(item)
                    continue

                media_path = Path(item.get("path", ""))
                if not media_path.exists():
                    logger.warning("Retry clip missing, removing from queue: %s", media_path)
                    continue

                if not self._check_rate_limit():
                    remaining.append(item)
                    continue

                media_type = item.get("media_type", "video")
                if media_type == "photo":
                    success = self.post_photo(media_path, item["caption"])
                else:
                    success = self.post_video(media_path, item["caption"])

                if not success:
                    remaining.append(item)
        finally:
            self._retry_in_progress = False

        safe_write_json(config.RETRY_QUEUE_FILE, remaining)
