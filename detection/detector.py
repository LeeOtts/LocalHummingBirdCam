"""Abstract base class for hummingbird detectors."""

from abc import ABC, abstractmethod
import numpy as np


class Detector(ABC):
    """Base interface for detection implementations."""

    @abstractmethod
    def detect(self, frame: np.ndarray) -> bool:
        """Analyze a frame and return True if a hummingbird is detected."""
        ...

    @abstractmethod
    def reset(self):
        """Reset detector state (e.g., after a recording cooldown)."""
        ...
