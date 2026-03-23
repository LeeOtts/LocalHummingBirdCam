"""Tests for FrameBuffer from camera/stream.py.

FrameBuffer stores JPEG-compressed frames in a thread-safe deque.
Tests use synthetic numpy arrays to avoid needing a real camera.
"""

import threading

import cv2
import numpy as np
import pytest

from camera.stream import FrameBuffer


def _make_frame(width=64, height=48, color=(0, 128, 255)):
    """Create a solid-color BGR frame for testing."""
    frame = np.zeros((height, width, 3), dtype=np.uint8)
    frame[:] = color
    return frame


class TestFrameBufferCapacity:
    """Test buffer capacity and auto-eviction."""

    def test_add_frames_up_to_capacity(self):
        """Adding frames up to maxlen keeps all of them."""
        buf = FrameBuffer(maxlen=5)
        for i in range(5):
            buf.add(_make_frame(color=(i * 50, 0, 0)))
        assert len(buf.get_all()) == 5

    def test_auto_eviction_when_over_capacity(self):
        """Adding more than maxlen evicts oldest frames (deque maxlen behavior)."""
        buf = FrameBuffer(maxlen=3)
        for i in range(10):
            buf.add(_make_frame(color=(i * 25, 0, 0)))
        frames = buf.get_all()
        assert len(frames) == 3

    def test_evicted_frames_are_oldest(self):
        """After overflow, the remaining frames are the most recent ones."""
        buf = FrameBuffer(maxlen=2)
        # Add 3 frames with distinct blue channel values (BGR order: index 0 = B)
        buf.add(_make_frame(color=(10, 0, 0)))    # frame 0, B=10 (evicted)
        buf.add(_make_frame(color=(100, 0, 0)))   # frame 1, B=100 (kept)
        buf.add(_make_frame(color=(200, 0, 0)))   # frame 2, B=200 (kept)

        frames = buf.get_all()
        assert len(frames) == 2
        # Due to JPEG compression there may be slight color shifts,
        # but the blue channel should be closer to 100 and 200 than to 10
        assert frames[0][0, 0, 0] > 50   # blue channel of frame 1
        assert frames[1][0, 0, 0] > 150  # blue channel of frame 2


class TestGetAll:
    """Test get_all() returns decompressed frames."""

    def test_get_all_returns_bgr_arrays(self):
        """get_all() returns list of BGR numpy arrays."""
        buf = FrameBuffer(maxlen=3)
        buf.add(_make_frame(width=64, height=48))
        frames = buf.get_all()
        assert len(frames) == 1
        assert isinstance(frames[0], np.ndarray)
        assert frames[0].ndim == 3  # height x width x channels

    def test_get_all_decompresses_jpeg(self):
        """Returned frames are decompressed (not raw JPEG bytes)."""
        buf = FrameBuffer(maxlen=3)
        original = _make_frame(width=100, height=80)
        buf.add(original)
        result = buf.get_all()[0]
        # Decompressed frame should have similar dimensions
        assert result.shape[0] == 80  # height
        assert result.shape[1] == 100  # width
        assert result.shape[2] == 3   # BGR channels


class TestGetAllCompressed:
    """Test get_all_compressed() returns raw JPEGs."""

    def test_returns_jpeg_buffers(self):
        """get_all_compressed() returns raw JPEG numpy arrays without decompressing."""
        buf = FrameBuffer(maxlen=5)
        buf.add(_make_frame())
        buf.add(_make_frame())
        compressed = buf.get_all_compressed()
        assert len(compressed) == 2
        # Each item is a 1D numpy array (JPEG bytes)
        for item in compressed:
            assert isinstance(item, np.ndarray)
            assert item.ndim == 1  # flat byte array

    def test_compressed_is_valid_jpeg(self):
        """Each compressed buffer can be decoded back to a frame."""
        buf = FrameBuffer(maxlen=3)
        buf.add(_make_frame(width=64, height=48))
        compressed = buf.get_all_compressed()
        decoded = cv2.imdecode(compressed[0], cv2.IMREAD_COLOR)
        assert decoded is not None
        assert decoded.shape == (48, 64, 3)


class TestClear:
    """Test clear() empties the buffer."""

    def test_clear_empties_buffer(self):
        """clear() removes all frames."""
        buf = FrameBuffer(maxlen=10)
        for _ in range(5):
            buf.add(_make_frame())
        assert len(buf.get_all()) == 5
        buf.clear()
        assert len(buf.get_all()) == 0

    def test_clear_allows_adding_again(self):
        """Buffer can be reused after clear()."""
        buf = FrameBuffer(maxlen=3)
        buf.add(_make_frame())
        buf.clear()
        buf.add(_make_frame())
        assert len(buf.get_all()) == 1


class TestEmptyBuffer:
    """Test behavior when buffer is empty."""

    def test_get_all_empty(self):
        """get_all() on empty buffer returns empty list."""
        buf = FrameBuffer(maxlen=5)
        assert buf.get_all() == []

    def test_get_all_compressed_empty(self):
        """get_all_compressed() on empty buffer returns empty list."""
        buf = FrameBuffer(maxlen=5)
        assert buf.get_all_compressed() == []


class TestThreadSafety:
    """Test concurrent add/get operations."""

    def test_concurrent_add_and_get(self):
        """Multiple threads adding and reading should not raise exceptions."""
        buf = FrameBuffer(maxlen=50)
        errors = []

        def writer():
            try:
                for _ in range(100):
                    buf.add(_make_frame())
            except Exception as e:
                errors.append(e)

        def reader():
            try:
                for _ in range(100):
                    buf.get_all()
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=writer),
            threading.Thread(target=writer),
            threading.Thread(target=reader),
            threading.Thread(target=reader),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert errors == [], f"Thread safety errors: {errors}"

    def test_concurrent_add_and_clear(self):
        """Concurrent add and clear should not raise exceptions."""
        buf = FrameBuffer(maxlen=20)
        errors = []

        def writer():
            try:
                for _ in range(50):
                    buf.add(_make_frame())
            except Exception as e:
                errors.append(e)

        def clearer():
            try:
                for _ in range(10):
                    buf.clear()
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=writer),
            threading.Thread(target=clearer),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert errors == [], f"Thread safety errors: {errors}"
