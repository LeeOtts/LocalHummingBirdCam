"""Memory profiling tests — measure per-component RAM usage and detect leaks.

Run selectively with:  pytest -m memory -v
Uses tracemalloc (stdlib) and synthetic numpy frames — no real camera needed.
"""

import gc
from pathlib import Path
from unittest.mock import MagicMock, patch

import cv2
import numpy as np
import pytest

from tests.memory_helpers import TracmallocSession

# All tests in this file get the 'memory' marker
pytestmark = pytest.mark.memory


def _make_frame(width=1920, height=1080, color=(0, 128, 255)):
    """Create a solid-color BGR frame for testing."""
    frame = np.zeros((height, width, 3), dtype=np.uint8)
    frame[:] = color
    return frame


def _make_lores_frame(width=320, height=240):
    """Create a random lores BGR frame (varies each call for motion detection)."""
    return np.random.randint(0, 255, (height, width, 3), dtype=np.uint8)


# ---------------------------------------------------------------------------
# FrameBuffer memory tests
# ---------------------------------------------------------------------------

class TestFrameBufferMemory:
    """Profile the JPEG-compressed FrameBuffer."""

    def test_framebuffer_memory_per_frame(self):
        """75 full-res frames compressed to JPEG should stay under 25MB."""
        from camera.stream import FrameBuffer

        with TracmallocSession() as tm:
            tm.snapshot("before")
            buf = FrameBuffer(maxlen=75)
            for i in range(75):
                buf.add(_make_frame(color=(i * 3 % 256, 100, 200)))
            tm.snapshot("after_fill")

            delta_kb = tm.delta_kb("before", "after_fill")
            # 75 JPEG frames at ~150-250KB each = ~11-19MB
            # Must be well under raw cost (~460MB)
            assert delta_kb < 25_000, f"FrameBuffer used {delta_kb:.0f}KB — expected < 25MB"

    def test_framebuffer_stable_after_eviction(self):
        """Memory should not grow after buffer is full and evicting old frames."""
        from camera.stream import FrameBuffer

        with TracmallocSession() as tm:
            buf = FrameBuffer(maxlen=30)
            for i in range(30):
                buf.add(_make_frame(color=(i * 8 % 256, 50, 150)))
            tm.snapshot("at_capacity")

            for i in range(70):
                buf.add(_make_frame(color=(i * 5 % 256, 100, 200)))
            tm.snapshot("after_eviction")

            delta_kb = tm.delta_kb("at_capacity", "after_eviction")
            assert abs(delta_kb) < 1_000, (
                f"Memory grew {delta_kb:.0f}KB after eviction — possible leak"
            )

    def test_framebuffer_clear_releases_memory(self):
        """Clearing the buffer + gc.collect should release most memory."""
        from camera.stream import FrameBuffer

        with TracmallocSession() as tm:
            tm.snapshot("baseline")
            buf = FrameBuffer(maxlen=50)
            for i in range(50):
                buf.add(_make_frame(color=(i * 5 % 256, 80, 160)))
            tm.snapshot("filled")

            buf.clear()
            del buf
            gc.collect()
            tm.snapshot("cleared")

            used_kb = tm.delta_kb("baseline", "filled")
            remaining_kb = tm.delta_kb("baseline", "cleared")
            released_pct = (1 - remaining_kb / used_kb) * 100 if used_kb > 0 else 100
            assert released_pct > 50, (
                f"Only {released_pct:.0f}% released after clear — expected > 50%"
            )


# ---------------------------------------------------------------------------
# Motion/color detector leak test
# ---------------------------------------------------------------------------

class TestDetectorMemory:
    """Ensure the motion detector doesn't leak over many cycles."""

    def test_motion_detector_no_leak(self):
        """500 detect() calls should not accumulate memory."""
        from detection.motion_color import MotionColorDetector

        detector = MotionColorDetector()

        with TracmallocSession() as tm:
            # Warm up (first few frames set prev_gray, etc.)
            for _ in range(10):
                detector.detect(_make_lores_frame())
            tm.snapshot("warmed_up")

            for _ in range(240):
                detector.detect(_make_lores_frame())
            tm.snapshot("mid_run")

            for _ in range(250):
                detector.detect(_make_lores_frame())
            tm.snapshot("end_run")

            growth_kb = tm.delta_kb("mid_run", "end_run")
            assert growth_kb < 500, (
                f"Detector leaked {growth_kb:.0f}KB over 250 cycles — expected < 500KB"
            )


# ---------------------------------------------------------------------------
# TFLite classifier memory tests (mocked interpreter)
# ---------------------------------------------------------------------------

class TestClassifierMemory:
    """Profile vision_verify with mocked TFLite interpreter."""

    @staticmethod
    def _make_mock_interpreter():
        """Create a mock TFLite interpreter matching the real API."""
        interp = MagicMock()
        interp.get_input_details.return_value = [
            {"shape": [1, 224, 224, 3], "dtype": np.uint8, "index": 0}
        ]
        interp.get_output_details.return_value = [
            {"shape": [1, 965], "dtype": np.uint8, "index": 0,
             "quantization": (0.00390625, 0)}
        ]
        # Return a dummy output tensor
        output = np.zeros(965, dtype=np.uint8)
        output[0] = 200  # high score for label 0
        interp.get_tensor.return_value = [output]
        return interp

    def test_classifier_inference_no_leak(self):
        """200 verify_hummingbird() calls should not accumulate memory."""
        import detection.vision_verify as vv

        mock_interp = self._make_mock_interpreter()
        mock_labels = [f"Bird species {i}" for i in range(965)]

        with TracmallocSession() as tm:
            with patch.object(vv, "_interpreter", mock_interp), \
                 patch.object(vv, "_labels", mock_labels), \
                 patch.object(vv, "_load_attempted", True), \
                 patch.object(vv, "_input_details", mock_interp.get_input_details()), \
                 patch.object(vv, "_output_details", mock_interp.get_output_details()):

                frame = _make_frame(width=640, height=480)

                # Warm up
                for _ in range(10):
                    vv.verify_hummingbird(frame)
                tm.snapshot("warmed_up")

                for _ in range(190):
                    vv.verify_hummingbird(frame)
                gc.collect()
                tm.snapshot("after_200")

            growth_kb = tm.delta_kb("warmed_up", "after_200")
            # cv2 resize/cvtColor + numpy expand_dims create intermediates
            # that tracemalloc tracks; allow generous headroom for allocator pools
            assert growth_kb < 35_000, (
                f"Classifier leaked {growth_kb:.0f}KB over 190 iterations — expected < 35MB"
            )


# ---------------------------------------------------------------------------
# System-level memory checks (Linux/Pi only)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not Path("/proc/meminfo").exists(),
    reason="requires /proc/meminfo (Linux only)",
)
class TestSystemMemoryThresholds:
    """Safety checks for available system RAM."""

    def test_available_memory_above_minimum(self):
        """System should have at least 100MB available RAM."""
        from tests.memory_helpers import read_proc_meminfo

        meminfo = read_proc_meminfo()
        assert meminfo is not None
        available_mb = meminfo.get("MemAvailable", 0) / 1024
        assert available_mb > 100, (
            f"Only {available_mb:.0f}MB available — system is memory-starved"
        )
