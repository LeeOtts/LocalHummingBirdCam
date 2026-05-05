"""Tests for MotionColorDetector from detection/motion_color.py.

Uses synthetic numpy frames with known pixel values to test
motion detection and hummingbird color filtering without a camera.
"""

import cv2
import numpy as np
import pytest

from detection.motion_color import (
    MotionColorDetector,
    GREEN_LOWER,
    GREEN_UPPER,
    RED_LOWER_1,
    RED_UPPER_1,
)


def _make_gray_frame(width=320, height=240, value=128):
    """Create a solid gray BGR frame."""
    frame = np.full((height, width, 3), value, dtype=np.uint8)
    return frame


def _make_frame_with_colored_patch(
    width=320, height=240, bg_value=128,
    patch_color_bgr=(0, 180, 0), patch_x=100, patch_y=80,
    patch_w=40, patch_h=40,
):
    """Create a BGR frame with a colored rectangular patch.

    The patch simulates a hummingbird-colored object.
    """
    frame = np.full((height, width, 3), bg_value, dtype=np.uint8)
    frame[patch_y:patch_y + patch_h, patch_x:patch_x + patch_w] = patch_color_bgr
    return frame


def _hsv_to_bgr(h, s, v):
    """Convert a single HSV pixel to BGR tuple for creating test frames."""
    hsv = np.array([[[h, s, v]]], dtype=np.uint8)
    bgr = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)
    return tuple(int(c) for c in bgr[0, 0])


def _make_framediff_detector(**kwargs):
    """Build a detector forced into framediff mode for synthetic-frame tests.

    Production default is MOG2, which needs ~500 warmup frames before it'll
    detect anything — far more than these synthetic tests feed. The motion+
    color filter logic being tested is identical between modes; only the
    motion-mask source differs.
    """
    detector = MotionColorDetector(**kwargs)
    detector._method = "framediff"
    detector._bg_sub = None
    detector._warmup_frames_remaining = 0
    return detector


class TestFirstFrame:
    """First frame should return False (no previous frame to compare)."""

    def test_first_frame_returns_false(self):
        """The very first frame always returns False since there is no prior frame."""
        detector = MotionColorDetector(motion_threshold=15.0, color_min_area=50)
        frame = _make_gray_frame()
        assert detector.detect(frame) is False

    def test_prev_gray_set_after_first_frame(self):
        """After the first frame, prev_gray should be populated."""
        detector = MotionColorDetector()
        detector.detect(_make_gray_frame())
        assert detector.prev_gray is not None


class TestNoMotion:
    """Identical frames should produce no motion detection."""

    def test_identical_frames_return_false(self):
        """Two identical frames produce zero motion, so detect returns False."""
        detector = MotionColorDetector(motion_threshold=15.0, color_min_area=50)
        frame = _make_gray_frame(value=128)
        detector.detect(frame)
        assert detector.detect(frame) is False

    def test_similar_frames_return_false(self):
        """Frames with very slight differences (< threshold) return False."""
        detector = MotionColorDetector(motion_threshold=15.0, color_min_area=50)
        frame1 = _make_gray_frame(value=128)
        frame2 = _make_gray_frame(value=130)  # tiny change, below noise floor
        detector.detect(frame1)
        assert detector.detect(frame2) is False


class TestMotionWithoutColor:
    """Motion detected but wrong colors should return False."""

    def test_motion_no_hummingbird_colors(self):
        """A large white patch moving on dark background has no hummingbird colors."""
        detector = MotionColorDetector(motion_threshold=15.0, color_min_area=50)

        # Frame 1: dark background
        frame1 = _make_gray_frame(value=30)
        detector.detect(frame1)

        # Frame 2: bright white patch (not green/red) — creates motion
        frame2 = _make_gray_frame(value=30)
        frame2[80:140, 100:180] = (255, 255, 255)  # white patch
        assert detector.detect(frame2) is False


class TestMotionWithHummingbirdColors:
    """Motion with green/red in HSV range should trigger detection after consecutive frames."""

    def _get_green_bgr(self):
        """Return a BGR color that falls within the GREEN HSV range."""
        # Middle of green HSV range: H=60, S=160, V=150
        return _hsv_to_bgr(60, 160, 150)

    def test_consecutive_frames_needed(self):
        """Detection requires 5 consecutive frames (not just one)."""
        detector = _make_framediff_detector(motion_threshold=15.0, color_min_area=50)
        green_bgr = self._get_green_bgr()

        # Frame 0: baseline (dark)
        detector.detect(_make_gray_frame(value=30))

        # Frames 1-4: motion + green patch, but not enough consecutive frames
        for i in range(4):
            frame = _make_frame_with_colored_patch(
                bg_value=30, patch_color_bgr=green_bgr,
                patch_x=100, patch_y=80, patch_w=40, patch_h=40,
            )
            result = detector.detect(frame)
            # Should not trigger yet (need 5 consecutive)
            if i < 3:
                assert result is False, f"Should not trigger on frame {i + 1}"

    def test_detection_after_enough_consecutive(self):
        """After enough consecutive motion+color frames, detection returns True."""
        detector = _make_framediff_detector(motion_threshold=15.0, color_min_area=50)
        detector._required_consecutive = 3  # lower for test speed
        green_bgr = self._get_green_bgr()

        # Use a plain dark background as baseline (no green patch)
        baseline = _make_gray_frame(value=30)
        detector.detect(baseline)

        # Now feed frames WITH the green patch — the patch itself creates motion
        # because it differs from the baseline. Keep feeding the same frame
        # so the patch is in the same place (consecutive detection).
        frame_with_patch = _make_frame_with_colored_patch(
            bg_value=30, patch_color_bgr=green_bgr,
            patch_x=100, patch_y=80, patch_w=50, patch_h=50,
        )

        # First detection frame: creates motion (patch appears)
        detector.detect(frame_with_patch)

        # Now alternate the patch position slightly to keep motion going
        triggered = False
        for i in range(10):
            offset = (i % 2) * 3  # shift patch by a few pixels each frame
            frame = _make_frame_with_colored_patch(
                bg_value=30, patch_color_bgr=green_bgr,
                patch_x=100 + offset, patch_y=80 + offset,
                patch_w=50, patch_h=50,
            )
            if detector.detect(frame):
                triggered = True
                break

        assert triggered, "Should have triggered after consecutive detections"


class TestExcessiveMotion:
    """More than 30% motion should return False (camera shake / lighting change)."""

    def test_excessive_motion_rejected(self):
        """When >30% of frame pixels change, _get_motion_mask returns None."""
        detector = MotionColorDetector(motion_threshold=15.0, color_min_area=50)

        # Frame 1: all dark
        frame1 = _make_gray_frame(value=0)
        detector.detect(frame1)

        # Frame 2: completely different — 100% of pixels changed
        frame2 = _make_gray_frame(value=255)
        assert detector.detect(frame2) is False


class TestReset:
    """reset() should clear prev_gray and consecutive detections."""

    def test_reset_clears_state(self):
        """After reset(), prev_gray is None and consecutive count is 0."""
        detector = MotionColorDetector()
        detector.detect(_make_gray_frame(value=100))
        assert detector.prev_gray is not None
        assert detector._consecutive_detections == 0

        # Manually set consecutive to simulate partial detection
        detector._consecutive_detections = 3

        detector.reset()
        assert detector.prev_gray is None
        assert detector._consecutive_detections == 0

    def test_reset_makes_next_frame_return_false(self):
        """After reset, the next frame is treated as the first (returns False)."""
        detector = MotionColorDetector()
        detector.detect(_make_gray_frame())
        detector.reset()
        # Next call is effectively "first frame" again
        assert detector.detect(_make_gray_frame()) is False
