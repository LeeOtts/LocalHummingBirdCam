"""Post to Twitter/X using the v2 API via tweepy."""

import logging
import time
from pathlib import Path

import config
from social.base_poster import SocialPoster

logger = logging.getLogger(__name__)


class TwitterPoster(SocialPoster):
    """Posts videos, photos, and text to Twitter/X."""

    platform_name = "Twitter"

    def __init__(self):
        self._client = None
        self._api_v1 = None  # Needed for media uploads (still v1.1 API)

    def is_configured(self) -> bool:
        return bool(
            config.TWITTER_API_KEY
            and config.TWITTER_API_SECRET
            and config.TWITTER_ACCESS_TOKEN
            and config.TWITTER_ACCESS_SECRET
        )

    def _get_clients(self):
        if self._client is not None:
            return self._client, self._api_v1
        try:
            import tweepy

            # v2 client for posting tweets
            self._client = tweepy.Client(
                consumer_key=config.TWITTER_API_KEY,
                consumer_secret=config.TWITTER_API_SECRET,
                access_token=config.TWITTER_ACCESS_TOKEN,
                access_token_secret=config.TWITTER_ACCESS_SECRET,
            )

            # v1.1 auth for media uploads (chunked upload is only on v1.1)
            auth = tweepy.OAuth1UserHandler(
                config.TWITTER_API_KEY,
                config.TWITTER_API_SECRET,
                config.TWITTER_ACCESS_TOKEN,
                config.TWITTER_ACCESS_SECRET,
            )
            self._api_v1 = tweepy.API(auth)

            logger.info("Authenticated with Twitter/X API")
            return self._client, self._api_v1

        except Exception:
            logger.exception("Failed to authenticate with Twitter")
            self._client = None
            self._api_v1 = None
            return None, None

    def _truncate(self, text: str, max_len: int = 280) -> str:
        """Truncate text to Twitter's 280-character limit."""
        if len(text) <= max_len:
            return text
        return text[:max_len - 3].rsplit(" ", 1)[0] + "..."

    def post_video(self, mp4_path: Path, caption: str) -> bool:
        """Upload a video via chunked upload and post a tweet."""
        client, api_v1 = self._get_clients()
        if not client or not api_v1:
            return False

        try:
            # Check file size (512MB limit for video, but free tier is much less)
            file_size = mp4_path.stat().st_size
            if file_size > 512 * 1024 * 1024:
                logger.warning("Twitter: video too large (%d MB)", file_size // (1024 * 1024))
                return self.post_text(caption)

            # Chunked media upload via v1.1 API
            media = api_v1.chunked_upload(
                str(mp4_path),
                media_category="tweet_video",
                wait_for_async_finalize=True,
            )

            # Wait for processing
            if hasattr(media, "processing_info"):
                self._wait_for_processing(api_v1, media.media_id)

            # Post tweet with media
            client.create_tweet(
                text=self._truncate(caption),
                media_ids=[media.media_id],
            )
            logger.info("Posted video to Twitter")
            return True

        except Exception:
            logger.exception("Twitter video post failed")
            self._client = None
            self._api_v1 = None
            return False

    def _wait_for_processing(self, api_v1, media_id: int, max_wait: int = 60):
        """Wait for async video processing on Twitter."""
        elapsed = 0
        while elapsed < max_wait:
            status = api_v1.get_media_upload_status(media_id)
            info = getattr(status, "processing_info", None)
            if not info:
                return
            state = info.get("state", "")
            if state == "succeeded":
                return
            if state == "failed":
                raise RuntimeError(f"Twitter video processing failed: {info}")
            wait = info.get("check_after_secs", 5)
            time.sleep(min(wait, 10))
            elapsed += wait

    def post_photo(self, image_path: Path, caption: str) -> bool:
        """Upload a photo and post a tweet."""
        client, api_v1 = self._get_clients()
        if not client or not api_v1:
            return False

        try:
            media = api_v1.media_upload(str(image_path))
            client.create_tweet(
                text=self._truncate(caption),
                media_ids=[media.media_id],
            )
            logger.info("Posted photo to Twitter")
            return True
        except Exception:
            logger.exception("Twitter photo post failed")
            self._client = None
            self._api_v1 = None
            return False

    def post_text(self, message: str) -> bool:
        """Post a text-only tweet."""
        client, _ = self._get_clients()
        if not client:
            return False

        try:
            client.create_tweet(text=self._truncate(message))
            logger.info("Posted text to Twitter")
            return True
        except Exception:
            logger.exception("Twitter text post failed")
            self._client = None
            return False
