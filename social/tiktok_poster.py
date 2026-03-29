"""Post video clips and photos to TikTok via the Content Posting API.

TikTok Content Posting API flow (Direct Post):
  1. Initialize upload via /v2/post/publish/video/init/ (or /content/init/ for photos)
  2. Upload binary chunks via PUT to the returned upload_url
  3. Check publish status via /v2/post/publish/status/fetch/

OAuth tokens are stored in a JSON file and refreshed automatically.
"""

import json
import logging
import math
import os
import time
from datetime import date
from pathlib import Path

import requests

import config
from social.base_poster import SocialPoster

logger = logging.getLogger(__name__)

TIKTOK_API_BASE = "https://open.tiktokapis.com/v2"

# Chunk constraints from TikTok docs
_MIN_CHUNK_SIZE = 5 * 1024 * 1024  # 5 MB
_MAX_CHUNK_SIZE = 64 * 1024 * 1024  # 64 MB
_DEFAULT_CHUNK_SIZE = 10 * 1024 * 1024  # 10 MB
_MAX_VIDEO_SIZE = 4 * 1024 * 1024 * 1024  # 4 GB


class TikTokPoster(SocialPoster):
    """Handles video and photo uploads to TikTok via Content Posting API."""

    platform_name = "TikTok"

    def __init__(self):
        self.client_key = config.TIKTOK_CLIENT_KEY
        self.client_secret = config.TIKTOK_CLIENT_SECRET
        self._access_token = config.TIKTOK_ACCESS_TOKEN
        self._refresh_token = config.TIKTOK_REFRESH_TOKEN
        self._token_file = config.BASE_DIR / ".tiktok_tokens.json"
        self._posts_today = 0
        self._today = self._date_in_local_tz()

        # Load saved tokens (may override env vars if fresher)
        self._load_tokens()

    def is_configured(self) -> bool:
        return bool(
            self.client_key
            and self.client_secret
            and self._access_token
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
        """Enforce daily post limit."""
        today = self._date_in_local_tz()
        if today != self._today:
            self._today = today
            self._posts_today = 0
        if self._posts_today >= config.TIKTOK_MAX_POSTS_PER_DAY:
            logger.warning("TikTok daily post limit reached (%d)", config.TIKTOK_MAX_POSTS_PER_DAY)
            return False
        return True

    # ------------------------------------------------------------------
    # Token management
    # ------------------------------------------------------------------

    def _load_tokens(self):
        """Load tokens from disk if available (fresher than env vars)."""
        if self._token_file.exists():
            try:
                data = json.loads(self._token_file.read_text())
                if data.get("access_token"):
                    self._access_token = data["access_token"]
                if data.get("refresh_token"):
                    self._refresh_token = data["refresh_token"]
            except (json.JSONDecodeError, KeyError):
                pass

    def _save_tokens(self):
        """Persist tokens to disk so they survive restarts."""
        self._token_file.write_text(json.dumps({
            "access_token": self._access_token,
            "refresh_token": self._refresh_token,
        }))

    def _refresh_access_token(self) -> bool:
        """Refresh the OAuth access token using the refresh token."""
        if not self._refresh_token:
            logger.error("TikTok: no refresh token available")
            return False

        try:
            resp = requests.post(
                f"{TIKTOK_API_BASE}/oauth/token/",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                data={
                    "client_key": self.client_key,
                    "client_secret": self.client_secret,
                    "grant_type": "refresh_token",
                    "refresh_token": self._refresh_token,
                },
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()

            if "access_token" not in data:
                logger.error("TikTok: token refresh failed: %s", data)
                return False

            self._access_token = data["access_token"]
            if data.get("refresh_token"):
                self._refresh_token = data["refresh_token"]
            self._save_tokens()
            logger.info("TikTok: access token refreshed")
            return True

        except requests.RequestException:
            logger.exception("TikTok: token refresh request failed")
            return False

    def _auth_headers(self) -> dict:
        """Return authorization headers for TikTok API calls."""
        return {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json; charset=UTF-8",
        }

    def _api_request(self, method: str, url: str, retry_on_401: bool = True, **kwargs):
        """Make an API request, refreshing token on 401."""
        resp = requests.request(method, url, **kwargs)

        if resp.status_code == 401 and retry_on_401:
            logger.info("TikTok: got 401, attempting token refresh")
            if self._refresh_access_token():
                # Update auth header with new token
                if "headers" in kwargs:
                    kwargs["headers"]["Authorization"] = f"Bearer {self._access_token}"
                resp = requests.request(method, url, retry_on_401=False, **kwargs)

        return resp

    # ------------------------------------------------------------------
    # Video posting
    # ------------------------------------------------------------------

    def post_video(self, mp4_path: Path, caption: str) -> bool:
        """Upload a video to TikTok via Direct Post with FILE_UPLOAD.

        Flow: init -> chunk upload -> status check.
        """
        if not self.is_configured():
            logger.error("TikTok credentials not configured")
            return False

        if not self._check_rate_limit():
            return False

        try:
            file_size = os.path.getsize(mp4_path)
            if file_size > _MAX_VIDEO_SIZE:
                logger.error("TikTok: video too large (%d MB)", file_size // (1024 * 1024))
                return False

            chunk_size, total_chunks = self._compute_chunks(file_size)

            # Step 1: Initialize upload
            init_resp = requests.post(
                f"{TIKTOK_API_BASE}/post/publish/video/init/",
                headers=self._auth_headers(),
                json={
                    "post_info": {
                        "title": caption[:2200],
                        "privacy_level": config.TIKTOK_PRIVACY_LEVEL,
                    },
                    "source_info": {
                        "source": "FILE_UPLOAD",
                        "video_size": file_size,
                        "chunk_size": chunk_size,
                        "total_chunk_count": total_chunks,
                    },
                },
                timeout=30,
            )

            # Handle 401 with token refresh
            if init_resp.status_code == 401:
                if not self._refresh_access_token():
                    return False
                init_resp = requests.post(
                    f"{TIKTOK_API_BASE}/post/publish/video/init/",
                    headers=self._auth_headers(),
                    json={
                        "post_info": {
                            "title": caption[:2200],
                            "privacy_level": config.TIKTOK_PRIVACY_LEVEL,
                        },
                        "source_info": {
                            "source": "FILE_UPLOAD",
                            "video_size": file_size,
                            "chunk_size": chunk_size,
                            "total_chunk_count": total_chunks,
                        },
                    },
                    timeout=30,
                )

            init_resp.raise_for_status()
            init_data = init_resp.json()

            publish_id = init_data.get("data", {}).get("publish_id")
            upload_url = init_data.get("data", {}).get("upload_url")

            if not upload_url:
                logger.error("TikTok: init returned no upload_url: %s", init_data)
                return False

            # Step 2: Upload video in chunks
            self._upload_chunks(mp4_path, upload_url, file_size, chunk_size)

            # Step 3: Check publish status
            if publish_id:
                self._wait_for_publish(publish_id)

            logger.info("Posted video to TikTok! Publish ID: %s", publish_id)
            self._posts_today += 1
            return True

        except requests.RequestException as e:
            if hasattr(e, "response") and e.response is not None:
                logger.error("TikTok response: %s", e.response.text)
            logger.exception("TikTok video upload failed for %s", mp4_path.name)
            return False

    @staticmethod
    def _compute_chunks(file_size: int) -> tuple[int, int]:
        """Compute chunk_size and total_chunk_count for TikTok upload."""
        if file_size <= _MIN_CHUNK_SIZE:
            return file_size, 1

        chunk_size = _DEFAULT_CHUNK_SIZE
        if file_size < chunk_size:
            chunk_size = file_size

        total_chunks = math.ceil(file_size / chunk_size)
        return chunk_size, total_chunks

    def _upload_chunks(self, file_path: Path, upload_url: str,
                       file_size: int, chunk_size: int):
        """Upload file in chunks via PUT requests."""
        with open(file_path, "rb") as f:
            offset = 0
            while offset < file_size:
                chunk = f.read(chunk_size)
                end = offset + len(chunk) - 1

                resp = requests.put(
                    upload_url,
                    headers={
                        "Content-Type": "video/mp4",
                        "Content-Length": str(len(chunk)),
                        "Content-Range": f"bytes {offset}-{end}/{file_size}",
                    },
                    data=chunk,
                    timeout=120,
                )

                # 201 = complete, 206 = partial (more chunks needed)
                if resp.status_code not in (201, 206):
                    resp.raise_for_status()

                offset += len(chunk)

    def _wait_for_publish(self, publish_id: str, max_wait: int = 120):
        """Poll TikTok publish status until done or timeout."""
        elapsed = 0
        while elapsed < max_wait:
            time.sleep(5)
            elapsed += 5

            try:
                resp = requests.post(
                    f"{TIKTOK_API_BASE}/post/publish/status/fetch/",
                    headers=self._auth_headers(),
                    json={"publish_id": publish_id},
                    timeout=30,
                )
                resp.raise_for_status()
                status = resp.json().get("data", {}).get("status", "")

                if status == "PUBLISH_COMPLETE":
                    return
                if status in ("FAILED", "PUBLISH_FAILED"):
                    fail_reason = resp.json().get("data", {}).get("fail_reason", "unknown")
                    logger.warning("TikTok: publish failed: %s", fail_reason)
                    return

            except requests.RequestException:
                logger.debug("TikTok: status check failed, retrying...")

    # ------------------------------------------------------------------
    # Photo posting
    # ------------------------------------------------------------------

    def post_photo(self, image_path: Path, caption: str) -> bool:
        """Upload a photo to TikTok via Content Posting API.

        TikTok supports photo posts (single image or carousel).
        """
        if not self.is_configured():
            logger.error("TikTok credentials not configured")
            return False

        if not self._check_rate_limit():
            return False

        try:
            file_size = os.path.getsize(image_path)

            # Step 1: Initialize photo post
            init_resp = requests.post(
                f"{TIKTOK_API_BASE}/post/publish/content/init/",
                headers=self._auth_headers(),
                json={
                    "post_info": {
                        "title": caption[:2200],
                        "privacy_level": config.TIKTOK_PRIVACY_LEVEL,
                    },
                    "source_info": {
                        "source": "FILE_UPLOAD",
                        "photo_images": [
                            {"image_size": file_size},
                        ],
                    },
                    "post_mode": "DIRECT_POST",
                    "media_type": "PHOTO",
                },
                timeout=30,
            )

            if init_resp.status_code == 401:
                if not self._refresh_access_token():
                    return False
                init_resp = requests.post(
                    f"{TIKTOK_API_BASE}/post/publish/content/init/",
                    headers=self._auth_headers(),
                    json={
                        "post_info": {
                            "title": caption[:2200],
                            "privacy_level": config.TIKTOK_PRIVACY_LEVEL,
                        },
                        "source_info": {
                            "source": "FILE_UPLOAD",
                            "photo_images": [
                                {"image_size": file_size},
                            ],
                        },
                        "post_mode": "DIRECT_POST",
                        "media_type": "PHOTO",
                    },
                    timeout=30,
                )

            init_resp.raise_for_status()
            init_data = init_resp.json()

            publish_id = init_data.get("data", {}).get("publish_id")
            # Photo upload URLs are returned as a list
            upload_urls = init_data.get("data", {}).get("upload_url")

            if not upload_urls:
                logger.error("TikTok: photo init returned no upload_url: %s", init_data)
                return False

            # Handle both string and list responses
            upload_url = upload_urls[0] if isinstance(upload_urls, list) else upload_urls

            # Step 2: Upload image
            with open(image_path, "rb") as f:
                resp = requests.put(
                    upload_url,
                    headers={
                        "Content-Type": "image/jpeg",
                        "Content-Length": str(file_size),
                    },
                    data=f,
                    timeout=60,
                )
            if resp.status_code not in (200, 201):
                resp.raise_for_status()

            # Step 3: Check status
            if publish_id:
                self._wait_for_publish(publish_id)

            logger.info("Posted photo to TikTok! Publish ID: %s", publish_id)
            self._posts_today += 1
            return True

        except requests.RequestException as e:
            if hasattr(e, "response") and e.response is not None:
                logger.error("TikTok response: %s", e.response.text)
            logger.exception("TikTok photo upload failed for %s", image_path.name)
            return False

    # ------------------------------------------------------------------
    # Text posting (not supported)
    # ------------------------------------------------------------------

    def post_text(self, message: str) -> bool:
        """TikTok doesn't support text-only posts."""
        if not self.is_configured():
            logger.error("TikTok credentials not configured")
            return False

        logger.warning("TikTok: text-only posts not supported (skipped)")
        return False
