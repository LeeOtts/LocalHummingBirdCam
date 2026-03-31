"""Hummingbird detection using motion differencing + HSV color filtering.

Works on the lores stream (320x240).
Detection triggers when BOTH motion and hummingbird-colored pixels are present
in the motion region, for several consecutive frames.
"""

import logging

import cv2
import numpy as np

import config
from detection.detector import Detector

logger = logging.getLogger(__name__)

# HSV ranges for hummingbird colors — tightened to reduce false positives
# Iridescent green (most common body color)
GREEN_LOWER = np.array([40, 60, 50])
GREEN_UPPER = np.array([80, 255, 255])

# Ruby-red throat (male Anna's, Ruby-throated, etc.)
RED_LOWER_1 = np.array([0, 120, 60])
RED_UPPER_1 = np.array([8, 255, 255])
RED_LOWER_2 = np.array([172, 120, 60])
RED_UPPER_2 = np.array([180, 255, 255])

# Rufous / orange (Rufous hummingbird)
ORANGE_LOWER = np.array([10, 120, 60])
ORANGE_UPPER = np.array([22, 255, 255])



class MotionColorDetector(Detector):
    """Detects hummingbirds via motion + distinctive color in the motion region."""

    def __init__(self, motion_threshold: float = None, color_min_area: int = None):
        self.motion_threshold = motion_threshold or config.MOTION_THRESHOLD
        self.color_min_area = color_min_area or config.COLOR_MIN_AREA
        self.color_max_area = int(getattr(config, 'COLOR_MAX_AREA', 5000))
        self.prev_gray = None
        self._consecutive_detections = 0
        self._required_consecutive = 5  # bumped from 3 — need 5 consecutive frames

        # Detection metadata (read by main.py after confirmation)
        self.last_bird_count = 1
        self.last_position_x: float | None = None
        self.last_position_y: float | None = None
        self.last_visit_duration_frames: int | None = None

    def detect(self, frame: np.ndarray) -> bool:
        """
        Analyze a YUV420 or BGR frame.
        Returns True if motion + hummingbird color detected for consecutive frames.
        """
        # Convert YUV420 to BGR if needed (picamera2 lores YUV420 comes as a tall frame)
        if len(frame.shape) == 2 or frame.shape[2] == 1:
            bgr = cv2.cvtColor(frame, cv2.COLOR_YUV2BGR_I420)
        elif frame.shape[0] > frame.shape[1] * 1.2:
            bgr = cv2.cvtColor(frame, cv2.COLOR_YUV2BGR_I420)
        else:
            bgr = frame

        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (21, 21), 0)  # blur to reduce noise-triggered motion

        # Motion detection via frame differencing
        motion_mask = self._get_motion_mask(gray)
        if motion_mask is None:
            self._consecutive_detections = 0
            self.prev_gray = gray
            return False

        # Only look for hummingbird colors INSIDE the motion region
        has_hummingbird = self._check_hummingbird_in_motion(bgr, motion_mask)
        if has_hummingbird:
            self._consecutive_detections += 1
        else:
            self._consecutive_detections = 0

        self.prev_gray = gray

        if self._consecutive_detections >= self._required_consecutive:
            self.last_visit_duration_frames = self._consecutive_detections
            logger.info("Hummingbird detected! (%d consecutive frames)", self._consecutive_detections)
            return True

        return False

    def _get_motion_mask(self, gray: np.ndarray) -> np.ndarray | None:
        """
        Return a binary mask of moving pixels, or None if no significant motion.
        """
        if self.prev_gray is None:
            return None

        diff = cv2.absdiff(gray, self.prev_gray)

        # Threshold the difference to get a binary motion mask
        _, thresh = cv2.threshold(diff, self.motion_threshold, 255, cv2.THRESH_BINARY)

        # Dilate to fill gaps in the motion region
        thresh = cv2.dilate(thresh, None, iterations=2)

        # Check if total motion exceeds threshold
        motion_pixels = cv2.countNonZero(thresh)
        total_pixels = gray.shape[0] * gray.shape[1]
        motion_percent = (motion_pixels / total_pixels) * 100

        # Too little motion = nothing there
        # Too much motion = camera shake, lighting change, wind blowing everything
        if motion_percent < 0.5 or motion_percent > 30:
            return None

        return thresh

    def _check_hummingbird_in_motion(self, bgr: np.ndarray, motion_mask: np.ndarray) -> bool:
        """Check for hummingbird colors only within the motion region."""
        hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)

        # Create masks for each hummingbird color range
        mask_green = cv2.inRange(hsv, GREEN_LOWER, GREEN_UPPER)
        mask_red1 = cv2.inRange(hsv, RED_LOWER_1, RED_UPPER_1)
        mask_red2 = cv2.inRange(hsv, RED_LOWER_2, RED_UPPER_2)
        mask_orange = cv2.inRange(hsv, ORANGE_LOWER, ORANGE_UPPER)

        # Combine all color masks
        color_mask = mask_green | mask_red1 | mask_red2 | mask_orange

        # Only keep colors that overlap with the motion region
        color_in_motion = cv2.bitwise_and(color_mask, motion_mask)

        colored_pixels = cv2.countNonZero(color_in_motion)

        # Must be within size range — too small is noise, too big is not a hummingbird
        if colored_pixels < self.color_min_area:
            return False
        if colored_pixels > self.color_max_area:
            return False

        # Find contours to check shape — hummingbird should be a compact blob, not scattered
        contours, _ = cv2.findContours(color_in_motion, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return False

        # Get the largest contour — should be a reasonably compact shape
        largest = max(contours, key=cv2.contourArea)
        area = cv2.contourArea(largest)

        # The largest blob should be at least half the total colored pixels
        # (scattered pixels across the frame = not a bird)
        if area < self.color_min_area * 0.5:
            return False

        # Count qualifying contours (potential simultaneous birds)
        min_bird_area = self.color_min_area * 0.5
        qualifying = [c for c in contours if cv2.contourArea(c) >= min_bird_area]
        self.last_bird_count = max(len(qualifying), 1)

        # Compute center of largest contour, normalized to 0-1
        M = cv2.moments(largest)
        if M["m00"] > 0:
            h, w = bgr.shape[:2]
            self.last_position_x = round(M["m10"] / M["m00"] / w, 4)
            self.last_position_y = round(M["m01"] / M["m00"] / h, 4)
        else:
            self.last_position_x = None
            self.last_position_y = None

        return True

    def reset(self):
        """Reset state after a recording."""
        self.prev_gray = None
        self._consecutive_detections = 0
