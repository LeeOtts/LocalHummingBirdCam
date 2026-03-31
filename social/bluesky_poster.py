"""Post to Bluesky via the AT Protocol."""

import io
import logging
from pathlib import Path

from PIL import Image

import config
from social.base_poster import SocialPoster

logger = logging.getLogger(__name__)


class BlueskyPoster(SocialPoster):
    """Posts videos, photos, and text to Bluesky using the atproto library."""

    platform_name = "Bluesky"

    def __init__(self):
        self._client = None

    def is_configured(self) -> bool:
        return bool(config.BLUESKY_HANDLE and config.BLUESKY_APP_PASSWORD)

    def _get_client(self):
        if self._client is not None:
            return self._client
        try:
            from atproto import Client, Request
            from httpx import Timeout
            # 300s timeout — default 5s is too short for video uploads on Pi
            # A 30s 1080p clip can be ~25 MB; at 2 Mbps upload that's ~100s
            request = Request(timeout=Timeout(timeout=300.0))
            self._client = Client(request=request)
            self._client.login(config.BLUESKY_HANDLE, config.BLUESKY_APP_PASSWORD)
            logger.info("Logged into Bluesky as %s", config.BLUESKY_HANDLE)
            return self._client
        except Exception:
            logger.exception("Failed to login to Bluesky")
            self._client = None
            return None

    def _truncate(self, text: str, max_len: int = 300) -> str:
        """Truncate text to Bluesky's 300 grapheme limit."""
        if len(text) <= max_len:
            return text
        return text[:max_len - 3].rsplit(" ", 1)[0] + "..."

    @staticmethod
    def _compress_image(img_data: bytes, max_bytes: int = 975_000) -> bytes:
        """Compress image to fit under Bluesky's 1MB blob limit."""
        if len(img_data) <= max_bytes:
            return img_data

        img = Image.open(io.BytesIO(img_data))
        if img.mode == "RGBA":
            img = img.convert("RGB")

        # Try reducing JPEG quality first
        for quality in (85, 70, 55, 40):
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=quality)
            if buf.tell() <= max_bytes:
                logger.info("Compressed image to %d bytes (quality=%d)", buf.tell(), quality)
                return buf.getvalue()

        # Quality alone wasn't enough — resize and retry
        img.thumbnail((2048, 2048), Image.LANCZOS)
        for quality in (75, 55, 40):
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=quality)
            if buf.tell() <= max_bytes:
                logger.info("Compressed+resized image to %d bytes (quality=%d)", buf.tell(), quality)
                return buf.getvalue()

        # Last resort: aggressive resize
        img.thumbnail((1024, 1024), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=40)
        logger.info("Aggressively compressed image to %d bytes", buf.tell())
        return buf.getvalue()

    def post_video(self, mp4_path: Path, caption: str) -> bool:
        """Post a video to Bluesky."""
        client = self._get_client()
        if not client:
            return False

        try:
            # Bluesky supports video uploads via blob upload
            video_data = mp4_path.read_bytes()

            # Check size limit (50MB)
            if len(video_data) > 50 * 1024 * 1024:
                logger.warning("Bluesky: video too large (%d MB), posting as text only",
                               len(video_data) // (1024 * 1024))
                return self.post_text(caption)

            # Upload video blob
            upload = client.upload_blob(video_data)
            del video_data  # Free video bytes (~5-50MB)
            blob = upload.blob

            # Create post with video embed
            from atproto import models
            embed = models.AppBskyEmbedVideo.Main(
                video=blob,
                alt=self._truncate(caption, 300),
            )
            client.send_post(
                text=self._truncate(caption),
                embed=embed,
            )
            logger.info("Posted video to Bluesky")
            return True

        except Exception:
            logger.exception("Bluesky video post failed")
            # Reset client in case session expired
            self._client = None
            return False

    def post_photo(self, image_path: Path, caption: str) -> bool:
        """Post a photo to Bluesky."""
        client = self._get_client()
        if not client:
            return False

        try:
            img_data = self._compress_image(image_path.read_bytes())
            upload = client.upload_blob(img_data)
            del img_data  # Free image bytes

            from atproto import models
            embed = models.AppBskyEmbedImages.Main(
                images=[models.AppBskyEmbedImages.Image(
                    alt=self._truncate(caption, 300),
                    image=upload.blob,
                )],
            )
            client.send_post(
                text=self._truncate(caption),
                embed=embed,
            )
            logger.info("Posted photo to Bluesky")
            return True

        except Exception:
            logger.exception("Bluesky photo post failed")
            self._client = None
            return False

    def post_text(self, message: str) -> bool:
        """Post a text-only message to Bluesky."""
        client = self._get_client()
        if not client:
            return False

        try:
            client.send_post(text=self._truncate(message))
            logger.info("Posted text to Bluesky")
            return True
        except Exception:
            logger.exception("Bluesky text post failed")
            self._client = None
            return False
