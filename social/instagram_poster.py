"""Post video clips and photos to Instagram via Meta's Graph API.

Instagram Content Publishing API requires a two-step flow:
  1. Create a media container (with image_url or resumable video upload)
  2. Publish the container via /media_publish

Photos require a publicly accessible URL.  Since this project runs on a
Raspberry Pi with local files, we upload the image to Facebook as an
unpublished photo first, grab the CDN URL, then pass it to Instagram.

Videos use the resumable upload flow to rupload.facebook.com which accepts
binary data directly.
"""

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

GRAPH_API_BASE = f"https://graph.facebook.com/{config.INSTAGRAM_API_VERSION}"
RUPLOAD_BASE = f"https://rupload.facebook.com/ig-api-upload/{config.INSTAGRAM_API_VERSION}"

# Instagram videos are now all Reels
_MAX_VIDEO_SIZE = 500 * 1024 * 1024  # 500 MB practical limit
_STATUS_POLL_INTERVAL = 5  # seconds between status checks
_STATUS_POLL_MAX_WAIT = 300  # 5 minutes max wait for processing


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

    # ------------------------------------------------------------------
    # Photo posting (upload to Facebook CDN first, then create container)
    # ------------------------------------------------------------------

    def _upload_image_to_facebook_cdn(self, image_path: Path) -> str | None:
        """Upload image to Facebook as unpublished photo, return CDN URL.

        Instagram's Content Publishing API requires a publicly accessible
        image_url.  We leverage the Facebook Page /photos endpoint (which
        accepts binary uploads) to get a CDN URL.
        """
        page_id = config.FACEBOOK_PAGE_ID
        if not page_id:
            logger.error("Instagram photo post requires FACEBOOK_PAGE_ID for CDN upload")
            return None

        with open(image_path, "rb") as f:
            resp = requests.post(
                f"{GRAPH_API_BASE}/{page_id}/photos",
                data={
                    "published": "false",
                    "access_token": self.access_token,
                },
                files={"source": f},
                timeout=60,
            )
        resp.raise_for_status()
        photo_id = resp.json().get("id")
        if not photo_id:
            logger.error("Instagram: Facebook CDN upload returned no photo ID")
            return None

        # Fetch the CDN URL from the uploaded photo
        url_resp = requests.get(
            f"{GRAPH_API_BASE}/{photo_id}",
            params={"fields": "images", "access_token": self.access_token},
            timeout=30,
        )
        url_resp.raise_for_status()
        images = url_resp.json().get("images", [])
        if not images:
            logger.error("Instagram: no CDN URL returned for photo %s", photo_id)
            return None

        # First image is the largest resolution
        cdn_url = images[0].get("source")
        logger.debug("Instagram: got CDN URL for photo (id=%s)", photo_id)
        return cdn_url

    def post_photo(self, image_path: Path, caption: str) -> bool:
        """Upload a photo to Instagram via the Content Publishing API.

        Flow: upload to Facebook CDN -> create IG container with image_url
        -> publish via media_publish.
        """
        if not self.is_configured():
            logger.error("Instagram credentials not configured")
            return False

        if not self._check_rate_limit():
            return False

        try:
            # Step 1: Get a public URL for the local image
            image_url = self._upload_image_to_facebook_cdn(image_path)
            if not image_url:
                return False

            # Step 2: Create Instagram media container
            container_resp = requests.post(
                f"{GRAPH_API_BASE}/{self.business_account_id}/media",
                data={
                    "image_url": image_url,
                    "caption": caption,
                    "access_token": self.access_token,
                },
                timeout=60,
            )
            container_resp.raise_for_status()
            container_id = container_resp.json().get("id")
            if not container_id:
                logger.error("Instagram: no container ID for photo: %s", container_resp.json())
                return False

            # Step 3: Publish
            return self._publish_container(container_id)

        except requests.RequestException as e:
            if hasattr(e, "response") and e.response is not None:
                logger.error("Instagram response: %s", e.response.text)
            logger.exception("Instagram photo upload failed for %s", image_path.name)
            return False

    # ------------------------------------------------------------------
    # Video posting (resumable upload to rupload.facebook.com)
    # ------------------------------------------------------------------

    def post_video(self, mp4_path: Path, caption: str) -> bool:
        """Upload a video to Instagram as a Reel via resumable upload.

        Flow: create container with upload_type=resumable -> upload binary
        to rupload.facebook.com -> wait for processing -> publish.
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
            if file_size > _MAX_VIDEO_SIZE:
                logger.error("Instagram: video too large (%d MB)", file_size // (1024 * 1024))
                return False

            # Step 1: Create resumable upload container
            init_resp = requests.post(
                f"{GRAPH_API_BASE}/{self.business_account_id}/media",
                data={
                    "media_type": "REELS",
                    "upload_type": "resumable",
                    "caption": caption,
                    "access_token": self.access_token,
                },
                timeout=30,
            )
            init_resp.raise_for_status()
            init_data = init_resp.json()
            container_id = init_data.get("id")
            upload_uri = init_data.get("uri")

            if not container_id or not upload_uri:
                logger.error("Instagram: resumable init missing id/uri: %s", init_data)
                return False

            # Step 2: Upload binary to rupload.facebook.com
            with open(mp4_path, "rb") as f:
                upload_resp = requests.post(
                    upload_uri,
                    headers={
                        "Authorization": f"OAuth {self.access_token}",
                        "offset": "0",
                        "file_size": str(file_size),
                    },
                    data=f,
                    timeout=300,
                )
            upload_resp.raise_for_status()

            # Step 3: Wait for processing
            if not self._wait_for_container(container_id):
                logger.error("Instagram: video processing timed out for %s", mp4_path.name)
                return False

            # Step 4: Publish
            return self._publish_container(container_id)

        except requests.RequestException as e:
            if hasattr(e, "response") and e.response is not None:
                logger.error("Instagram response: %s", e.response.text)
            logger.exception("Instagram video upload failed for %s", mp4_path.name)
            if not self._retry_in_progress:
                self._save_to_retry_queue(mp4_path, caption, "video")
            return False

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    def _publish_container(self, container_id: str) -> bool:
        """Publish a media container via /{ig-user-id}/media_publish."""
        publish_resp = requests.post(
            f"{GRAPH_API_BASE}/{self.business_account_id}/media_publish",
            data={
                "creation_id": container_id,
                "access_token": self.access_token,
            },
            timeout=60,
        )
        publish_resp.raise_for_status()
        media_id = publish_resp.json().get("id", "unknown")
        logger.info("Posted to Instagram! Media ID: %s", media_id)
        self._posts_today += 1
        return True

    def _wait_for_container(self, container_id: str) -> bool:
        """Poll container status until ready or timeout."""
        elapsed = 0
        while elapsed < _STATUS_POLL_MAX_WAIT:
            time.sleep(_STATUS_POLL_INTERVAL)
            elapsed += _STATUS_POLL_INTERVAL

            resp = requests.get(
                f"{GRAPH_API_BASE}/{container_id}",
                params={
                    "fields": "status_code",
                    "access_token": self.access_token,
                },
                timeout=30,
            )
            resp.raise_for_status()
            status = resp.json().get("status_code", "")

            if status == "FINISHED":
                return True
            if status == "ERROR":
                logger.error("Instagram: container %s processing failed", container_id)
                return False

            logger.debug("Instagram: container %s status=%s (%ds)", container_id, status, elapsed)

        return False

    def post_text(self, message: str) -> bool:
        """Instagram doesn't support text-only posts via Graph API."""
        if not self.is_configured():
            logger.error("Instagram credentials not configured")
            return False

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
