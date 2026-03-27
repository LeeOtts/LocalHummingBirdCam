"""Post video clips to the 'Backyard Hummers' Facebook page via Graph API."""

import logging
import os
from datetime import date
from pathlib import Path

import requests

import config
from social.base_poster import SocialPoster
from utils import safe_read_json, safe_write_json

logger = logging.getLogger(__name__)

GRAPH_API_BASE = f"https://graph.facebook.com/{config.FACEBOOK_API_VERSION}"


class FacebookPoster(SocialPoster):
    """Handles video uploads to a Facebook Page using the Resumable Upload API."""

    platform_name = "Facebook"

    def __init__(self):
        self.page_id = config.FACEBOOK_PAGE_ID
        self.access_token = config.FACEBOOK_PAGE_ACCESS_TOKEN
        self._posts_today = 0
        self._today = self._date_in_local_tz()
        self._retry_in_progress = False
        
        # Prune retry queue on startup (remove entries with missing clip files)
        self._prune_retry_queue_on_startup()

    def _prune_retry_queue_on_startup(self):
        """Clean up retry queue on startup: remove entries whose clips no longer exist."""
        if not config.RETRY_QUEUE_FILE.exists():
            return
        try:
            queue = safe_read_json(config.RETRY_QUEUE_FILE, default=[])
            original_len = len(queue)
            queue = [e for e in queue if Path(e.get("mp4_path", "")).exists()]
            if len(queue) < original_len:
                safe_write_json(config.RETRY_QUEUE_FILE, queue)
                logger.info("Retry queue pruned on startup: %d → %d entries", original_len, len(queue))
        except Exception:
            logger.exception("Failed to prune retry queue on startup")

    def is_configured(self) -> bool:
        return bool(self.page_id and self.access_token)

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

    def verify_token(self) -> dict:
        """Check token validity, scopes, and app mode via the Graph API.

        Returns a dict with token diagnostics (also logs warnings).
        """
        result = {
            "configured": bool(self.page_id and self.access_token),
            "valid": False,
            "scopes": [],
            "app_id": None,
            "app_name": None,
            "expires_at": None,
            "warnings": [],
        }

        if not result["configured"]:
            result["warnings"].append("Facebook credentials not configured in .env")
            logger.warning("Facebook credentials not configured")
            return result

        try:
            # Use debug_token endpoint — requires an app token or the token itself
            resp = requests.get(
                f"{GRAPH_API_BASE}/debug_token",
                params={
                    "input_token": self.access_token,
                    "access_token": self.access_token,
                },
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json().get("data", {})

            result["valid"] = data.get("is_valid", False)
            result["scopes"] = data.get("scopes", [])
            result["app_id"] = data.get("app_id")
            result["expires_at"] = data.get("expires_at", 0)

            # Check for required scopes
            # pages_manage_engagement is needed for AI comment replies (AUTO_REPLY_ENABLED)
            required = {"pages_manage_posts", "pages_read_engagement", "pages_manage_engagement"}
            granted = set(result["scopes"])
            missing = required - granted
            if missing:
                msg = f"Missing Facebook scopes: {', '.join(missing)}"
                result["warnings"].append(msg)
                logger.warning(msg)

            if not result["valid"]:
                result["warnings"].append("Facebook token is INVALID or expired")
                logger.error("Facebook token is INVALID or expired!")

            # Try to get app name
            if result["app_id"]:
                try:
                    app_resp = requests.get(
                        f"{GRAPH_API_BASE}/{result['app_id']}",
                        params={"access_token": self.access_token,
                                "fields": "name,mode"},
                        timeout=10,
                    )
                    if app_resp.status_code == 200:
                        app_data = app_resp.json()
                        result["app_name"] = app_data.get("name")
                        # 'mode' field is only available in some API versions
                except Exception:
                    pass

            logger.info(
                "Facebook token check: valid=%s, scopes=%s, app=%s",
                result["valid"],
                result["scopes"],
                result.get("app_name", result["app_id"]),
            )

        except requests.RequestException as e:
            msg = f"Failed to verify Facebook token: {e}"
            result["warnings"].append(msg)
            logger.error(msg)

        return result

    def verify_post_exists(self, post_id: str) -> dict:
        """Read back a post to check if Facebook considers it published."""
        try:
            resp = requests.get(
                f"{GRAPH_API_BASE}/{post_id}",
                params={
                    "fields": "id,message,created_time,is_published",
                    "access_token": self.access_token,
                },
                timeout=15,
            )
            if resp.status_code == 200:
                data = resp.json()
                is_published = data.get("is_published", "unknown")
                logger.info(
                    "Post %s readback: is_published=%s, created=%s",
                    post_id, is_published, data.get("created_time"),
                )
                return {"found": True, "is_published": is_published, "data": data}
            else:
                logger.warning(
                    "Post %s readback failed (HTTP %d): %s",
                    post_id, resp.status_code, resp.text,
                )
                return {"found": False, "error": resp.text}
        except requests.RequestException as e:
            logger.warning("Post readback failed for %s: %s", post_id, e)
            return {"found": False, "error": str(e)}

    def _check_rate_limit(self) -> bool:
        """Enforce daily post limit (uses location timezone for midnight)."""
        today = self._date_in_local_tz()

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
            file_size_mb = file_size / (1024 ** 2)
            
            # Validate file size before upload attempt (prevents wasted API calls)
            if file_size_mb > config.MAX_VIDEO_FILE_SIZE_MB:
                logger.error(
                    "Video file %s is %.1f MB, exceeds limit of %d MB. "
                    "Not posting or retrying (would always fail).",
                    mp4_path.name, file_size_mb, config.MAX_VIDEO_FILE_SIZE_MB
                )
                return False  # Don't retry; file is too large and will never upload

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
                # Check if the post was actually published despite the error
                try:
                    resp_data = e.response.json()
                    if resp_data.get("id"):
                        post_check = self.verify_post_exists(resp_data["id"])
                        if post_check.get("found"):
                            logger.info("Post %s was published despite error — skipping retry", resp_data["id"])
                            self._posts_today += 1
                            return True
                except (ValueError, KeyError):
                    pass
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
            resp_data = resp.json()
            post_id = resp_data.get("id", "unknown")
            # Log only response keys to avoid exposing any sensitive fields
            logger.info("Text post published! Post ID: %s | Response keys: %s", post_id, list(resp_data.keys()))

            # Verify the post is actually visible
            if post_id != "unknown":
                self.verify_post_exists(post_id)

            return True

        except requests.RequestException as e:
            if hasattr(e, 'response') and e.response is not None:
                logger.error("Facebook response: %s", e.response.text)
            logger.exception("Facebook text post failed")
            return False

    def post_photo(self, image_path: Path, caption: str) -> bool:
        """Upload a photo as a feed post on the Facebook Page.

        Posts to /feed with attached photo so it appears as a proper
        feed/timeline post that people see when scrolling (not just a photo album entry).
        """
        if not self.page_id or not self.access_token:
            logger.error("Facebook credentials not configured")
            return False

        if not self._check_rate_limit():
            return False

        try:
            with open(image_path, "rb") as f:
                resp = requests.post(
                    f"{GRAPH_API_BASE}/{self.page_id}/feed",
                    data={
                        "message": caption,
                        "access_token": self.access_token,
                    },
                    files={"picture": f},
                    timeout=60,
                )
            resp.raise_for_status()
            resp_data = resp.json()
            post_id = resp_data.get("id", "unknown")
            logger.info("Photo feed post published! Post ID: %s", post_id)

            self._posts_today += 1
            return True

        except requests.RequestException as e:
            if hasattr(e, "response") and e.response is not None:
                logger.error("Facebook response: %s", e.response.text)
            logger.exception("Facebook photo feed post failed for %s", image_path.name)
            return False

    def _save_to_retry_queue(self, mp4_path: Path, caption: str):
        """Save failed posts to a JSON retry queue for later.

        Entries whose clip file no longer exists are pruned only when queue reaches 80% capacity
        (optimization: avoid filesystem stats on every post).
        The queue is capped at MAX_RETRY_QUEUE_SIZE; oldest entries are dropped first.
        """
        queue = safe_read_json(config.RETRY_QUEUE_FILE, default=[])

        # Prune only if queue is getting large (80% of max) to avoid per-post overhead
        PRUNE_THRESHOLD = config.MAX_RETRY_QUEUE_SIZE * 0.80
        if len(queue) > PRUNE_THRESHOLD:
            original_len = len(queue)
            queue = [e for e in queue if Path(e.get("mp4_path", "")).exists()]
            if len(queue) < original_len:
                logger.info("Retry queue pruned: %d → %d (removed missing clips)", original_len, len(queue))

        queue.append({
            "mp4_path": str(mp4_path),
            "caption": caption,
        })

        # Cap to avoid unbounded growth — drop oldest entries first
        if len(queue) > config.MAX_RETRY_QUEUE_SIZE:
            dropped = len(queue) - config.MAX_RETRY_QUEUE_SIZE
            logger.warning("Retry queue full — dropping %d oldest entries", dropped)
            queue = queue[-config.MAX_RETRY_QUEUE_SIZE:]

        safe_write_json(config.RETRY_QUEUE_FILE, queue)
        logger.info("Saved to retry queue (%d/%d): %s", len(queue), config.MAX_RETRY_QUEUE_SIZE, mp4_path.name)

    def retry_failed_posts(self):
        """Attempt to post any items in the retry queue."""
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
                mp4_path = Path(item["mp4_path"])
                if not mp4_path.exists():
                    logger.warning("Retry clip missing, removing from queue: %s", mp4_path)
                    continue  # don't add to remaining — prune the dead entry
                if not self._check_rate_limit():
                    remaining.append(item)
                    continue
                success = self.post_video(mp4_path, item["caption"])
                if not success:
                    remaining.append(item)
        finally:
            self._retry_in_progress = False

        safe_write_json(config.RETRY_QUEUE_FILE, remaining)
