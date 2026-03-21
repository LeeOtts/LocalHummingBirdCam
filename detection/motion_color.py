"""Hummingbird detection using motion differencing + HSV color filtering.

Works on the lores YUV420 stream from picamera2 (320x240).
Detection triggers when BOTH motion and hummingbird-colored pixels are present.
"""

import logging

import cv2
import numpy as np

import config
from detection.detector import Detector

logger = logging.getLogger(__name__)

# HSV ranges for hummingbird colors
# Iridescent green (most common body color)
GREEN_LOWER = np.array([35, 40, 40])
GREEN_UPPER = np.array([85, 255, 255])

# Ruby-red throat (male Anna's, Ruby-throated, etc.)
RED_LOWER_1 = np.array([0, 100, 50])
RED_UPPER_1 = np.array([10, 255, 255])
RED_LOWER_2 = np.array([170, 100, 50])
RED_UPPER_2 = np.array([180, 255, 255])

# Rufous / orange (Rufous hummingbird)
ORANGE_LOWER = np.array([10, 100, 50])
ORANGE_UPPER = np.array([25, 255, 255])


class MotionColorDetector(Detector):
    """Detects hummingbirds via motion + distinctive color presence."""

    def __init__(self, motion_threshold: float = None, color_min_area: int = None):
        self.motion_threshold = motion_threshold or config.MOTION_THRESHOLD
        self.color_min_area = color_min_area or config.COLOR_MIN_AREA
        self.prev_gray = None
        self._consecutive_detections = 0
        self._required_consecutive = 3  # require 3 consecutive frames to reduce false positives

    def detect(self, frame: np.ndarray) -> bool:
        """
        Analyze a YUV420 or BGR frame.
        Returns True if motion + hummingbird color detected for consecutive frames.
        """
        # Convert YUV420 to BGR if needed (picamera2 lores YUV420 comes as a tall frame)
        if len(frame.shape) == 2 or frame.shape[2] == 1:
            bgr = cv2.cvtColor(frame, cv2.COLOR_YUV2BGR_I420)
        elif frame.shape[0] > frame.shape[1] * 1.2:
            # YUV420 planar comes as height*1.5 x width
            bgr = cv2.cvtColor(frame, cv2.COLOR_YUV2BGR_I420)
        else:
            bgr = frame

        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)

        # Motion detection via frame differencing
        has_motion = self._check_motion(gray)
        if not has_motion:
            self._consecutive_detections = 0
            self.prev_gray = gray
            return False

        # Color detection on the motion frame
        has_color = self._check_hummingbird_colors(bgr)
        if has_color:
            self._consecutive_detections += 1
        else:
            self._consecutive_detections = 0

        self.prev_gray = gray

        if self._consecutive_detections >= self._required_consecutive:
            logger.info("Hummingbird detected! (%d consecutive frames)", self._consecutive_detections)
            return True

        return False

    def _check_motion(self, gray: np.ndarray) -> bool:
        """Check if there's significant motion compared to previous frame."""
        if self.prev_gray is None:
            return False

        # Mean squared error between frames
        diff = cv2.absdiff(gray, self.prev_gray)
        mse = np.mean(diff.astype(np.float32) ** 2)
        return mse > self.motion_threshold

    def _check_hummingbird_colors(self, bgr: np.ndarray) -> bool:
        """Check for hummingbird-indicative colors in HSV space."""
        hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)

        # Create masks for each hummingbird color range
        mask_green = cv2.inRange(hsv, GREEN_LOWER, GREEN_UPPER)
        mask_red1 = cv2.inRange(hsv, RED_LOWER_1, RED_UPPER_1)
        mask_red2 = cv2.inRange(hsv, RED_LOWER_2, RED_UPPER_2)
        mask_orange = cv2.inRange(hsv, ORANGE_LOWER, ORANGE_UPPER)

        # Combine all color masks
        combined = mask_green | mask_red1 | mask_red2 | mask_orange

        # Count colored pixels
        colored_pixels = cv2.countNonZero(combined)

        return colored_pixels >= self.color_min_area

    def reset(self):
        """Reset state after a recording."""
        self.prev_gray = None
        self._consecutive_detections = 0
