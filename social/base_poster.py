"""Abstract base class for social media posters."""

import logging
from abc import ABC, abstractmethod
from pathlib import Path

logger = logging.getLogger(__name__)


class SocialPoster(ABC):
    """Interface that all social media posters must implement."""

    @property
    @abstractmethod
    def platform_name(self) -> str:
        """Human-readable platform name (e.g. 'Facebook', 'Bluesky')."""
        ...

    @abstractmethod
    def is_configured(self) -> bool:
        """Return True if this platform has the required credentials."""
        ...

    @abstractmethod
    def post_video(self, mp4_path: Path, caption: str) -> bool:
        """Post a video with caption. Returns True on success."""
        ...

    @abstractmethod
    def post_photo(self, image_path: Path, caption: str) -> bool:
        """Post a photo with caption. Returns True on success."""
        ...

    @abstractmethod
    def post_text(self, message: str) -> bool:
        """Post a text-only message. Returns True on success."""
        ...
