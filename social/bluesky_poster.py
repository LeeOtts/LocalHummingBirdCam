"""Post to Bluesky via the AT Protocol."""

import logging
from pathlib import Path

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
            from atproto import Client
            self._client = Client()
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
            img_data = image_path.read_bytes()
            upload = client.upload_blob(img_data)

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
