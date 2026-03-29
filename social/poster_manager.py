"""Manages multiple social media posters — fans out posts to all enabled platforms."""

import logging
from pathlib import Path

from social.base_poster import SocialPoster

logger = logging.getLogger(__name__)


class PosterManager:
    """Discovers and manages all configured social media posters.

    Platforms are opt-in: if credentials aren't set, the platform is silently
    skipped. Add credentials and restart to enable a new platform.
    """

    def __init__(self):
        self._posters: list[SocialPoster] = []
        self._discover_platforms()

    def _discover_platforms(self):
        """Auto-discover which platforms are configured and register them."""
        # Facebook (always try — it was the original)
        try:
            from social.facebook_poster import FacebookPoster
            fb = FacebookPoster()
            if fb.is_configured():
                self._posters.append(fb)
                logger.info("Platform enabled: Facebook")
            else:
                logger.info("Platform skipped: Facebook (no credentials)")
        except Exception:
            logger.warning("Failed to initialize Facebook poster")

        # Instagram
        try:
            from social.instagram_poster import InstagramPoster
            ig = InstagramPoster()
            if ig.is_configured():
                self._posters.append(ig)
                logger.info("Platform enabled: Instagram")
            else:
                logger.info("Platform skipped: Instagram (no credentials)")
        except Exception:
            logger.warning("Failed to initialize Instagram poster")

        # Bluesky
        try:
            from social.bluesky_poster import BlueskyPoster
            bs = BlueskyPoster()
            if bs.is_configured():
                self._posters.append(bs)
                logger.info("Platform enabled: Bluesky")
            else:
                logger.info("Platform skipped: Bluesky (no credentials)")
        except ImportError:
            logger.info("Platform skipped: Bluesky (atproto not installed)")
        except Exception:
            logger.warning("Failed to initialize Bluesky poster")

        # Twitter/X
        try:
            from social.twitter_poster import TwitterPoster
            tw = TwitterPoster()
            if tw.is_configured():
                self._posters.append(tw)
                logger.info("Platform enabled: Twitter/X")
            else:
                logger.info("Platform skipped: Twitter/X (no credentials)")
        except ImportError:
            logger.info("Platform skipped: Twitter/X (tweepy not installed)")
        except Exception:
            logger.warning("Failed to initialize Twitter poster")

        # TikTok
        try:
            from social.tiktok_poster import TikTokPoster
            tt = TikTokPoster()
            if tt.is_configured():
                self._posters.append(tt)
                logger.info("Platform enabled: TikTok")
            else:
                logger.info("Platform skipped: TikTok (no credentials)")
        except Exception:
            logger.warning("Failed to initialize TikTok poster")

        if not self._posters:
            logger.warning("No social media platforms configured!")
        else:
            names = [p.platform_name for p in self._posters]
            logger.info("Active platforms: %s", ", ".join(names))

    @property
    def posters(self) -> list[SocialPoster]:
        return list(self._posters)

    @property
    def platform_names(self) -> list[str]:
        return [p.platform_name for p in self._posters]

    def get_poster(self, platform: str) -> SocialPoster | None:
        """Get a specific poster by platform name."""
        for p in self._posters:
            if p.platform_name.lower() == platform.lower():
                return p
        return None

    @staticmethod
    def _resolve_caption(caption: "str | dict[str, str]", platform_name: str) -> str:
        """Pick the right caption for a platform from a str or per-platform dict."""
        if isinstance(caption, dict):
            return caption.get(platform_name, next(iter(caption.values())))
        return caption

    def post_video(self, mp4_path: Path, caption: "str | dict[str, str]") -> dict[str, bool]:
        """Post a video to all enabled platforms. Returns {platform: success}."""
        results = {}
        for poster in self._posters:
            try:
                text = self._resolve_caption(caption, poster.platform_name)
                success = poster.post_video(mp4_path, text)
                results[poster.platform_name] = success
                if success:
                    logger.info("Posted video to %s", poster.platform_name)
                else:
                    logger.warning("Failed to post video to %s", poster.platform_name)
            except Exception:
                logger.exception("Error posting video to %s", poster.platform_name)
                results[poster.platform_name] = False
        return results

    def post_photo(self, image_path: Path, caption: "str | dict[str, str]") -> dict[str, bool]:
        """Post a photo to all enabled platforms. Returns {platform: success}."""
        results = {}
        for poster in self._posters:
            try:
                text = self._resolve_caption(caption, poster.platform_name)
                success = poster.post_photo(image_path, text)
                results[poster.platform_name] = success
            except Exception:
                logger.exception("Error posting photo to %s", poster.platform_name)
                results[poster.platform_name] = False
        return results

    def post_text(self, message: "str | dict[str, str]") -> dict[str, bool]:
        """Post text to all enabled platforms. Returns {platform: success}."""
        results = {}
        for poster in self._posters:
            try:
                text = self._resolve_caption(message, poster.platform_name)
                success = poster.post_text(text)
                results[poster.platform_name] = success
            except Exception:
                logger.exception("Error posting text to %s", poster.platform_name)
                results[poster.platform_name] = False
        return results
