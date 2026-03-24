"""Tests for CameraStream in camera/stream.py."""

import threading
from unittest.mock import MagicMock, patch, call

import cv2
import numpy as np
import pytest


def _make_stream():
    from camera.stream import CameraStream
    s = object.__new__(CameraStream)
    s.camera_type = None
    s._backend = None
    s.error = None
    s.rotation = 0
    s.circular_output = None
    s.frame_buffer = None
    return s


class TestIsAvailable:
    """Test CameraStream.is_available property."""

    def test_false_when_camera_type_none(self):
        s = _make_stream()
        s.camera_type = None
        s.error = None
        assert s.is_available is False

    def test_false_when_error_set(self):
        s = _make_stream()
        s.camera_type = "usb"
        s.error = "Camera failed"
        assert s.is_available is False

    def test_true_when_usb_started(self):
        s = _make_stream()
        s.camera_type = "usb"
        s.error = None
        assert s.is_available is True

    def test_true_when_picamera_started(self):
        s = _make_stream()
        s.camera_type = "picamera"
        s.error = None
        assert s.is_available is True


class TestApplyRotation:
    """Test _apply_rotation() with various rotation values."""

    @pytest.mark.parametrize("rotation,expected_code", [
        (0, None),
        (90, cv2.ROTATE_90_CLOCKWISE),
        (180, cv2.ROTATE_180),
        (270, cv2.ROTATE_90_COUNTERCLOCKWISE),
    ])
    def test_rotation(self, rotation, expected_code):
        s = _make_stream()
        s.rotation = rotation
        frame = np.zeros((10, 10, 3), dtype=np.uint8)

        if expected_code is None:
            # No rotation — should return same object without calling cv2.rotate
            with patch("camera.stream.cv2.rotate") as mock_rotate:
                result = s._apply_rotation(frame)
            mock_rotate.assert_not_called()
            assert result is frame
        else:
            mock_rotated = np.ones((10, 10, 3), dtype=np.uint8)
            with patch("camera.stream.cv2.rotate", return_value=mock_rotated) as mock_rotate:
                result = s._apply_rotation(frame)
            mock_rotate.assert_called_once_with(frame, expected_code)
            assert result is mock_rotated


class TestCaptureLoresArray:
    """Test capture_lores_array() for USB camera paths."""

    def test_returns_zeros_when_no_usb_frame(self, monkeypatch):
        import config
        monkeypatch.setattr(config, "LORES_HEIGHT", 240)
        monkeypatch.setattr(config, "LORES_WIDTH", 320)

        s = _make_stream()
        s.camera_type = "usb"
        s._usb_latest_frame = None
        s._usb_lock = threading.Lock()

        result = s.capture_lores_array()
        assert result.shape == (240, 320, 3)
        assert result.sum() == 0

    def test_returns_resized_usb_frame(self, monkeypatch):
        import config
        monkeypatch.setattr(config, "LORES_HEIGHT", 240)
        monkeypatch.setattr(config, "LORES_WIDTH", 320)

        s = _make_stream()
        s.camera_type = "usb"
        s._usb_latest_frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        s._usb_lock = threading.Lock()

        result = s.capture_lores_array()
        assert result.shape == (240, 320, 3)


class TestCaptureSnapshot:
    """Test capture_snapshot() saves JPEG for various camera states."""

    def test_returns_false_when_camera_unavailable(self, tmp_path):
        s = _make_stream()
        s.camera_type = None  # is_available == False
        s.error = None

        result = s.capture_snapshot(tmp_path / "snap.jpg")
        assert result is False

    def test_returns_false_when_no_usb_frame(self, tmp_path):
        s = _make_stream()
        s.camera_type = "usb"
        s.error = None
        s._usb_latest_frame = None
        s._usb_lock = threading.Lock()

        result = s.capture_snapshot(tmp_path / "snap.jpg")
        assert result is False

    def test_saves_jpeg_for_usb_camera(self, tmp_path):
        s = _make_stream()
        s.camera_type = "usb"
        s.error = None
        s._usb_latest_frame = np.zeros((480, 640, 3), dtype=np.uint8)
        s._usb_lock = threading.Lock()

        snap_path = tmp_path / "snap.jpg"
        with patch("camera.stream.cv2.imwrite", return_value=True) as mock_imwrite:
            result = s.capture_snapshot(snap_path)

        mock_imwrite.assert_called_once()
        assert result is True

    def test_returns_false_on_exception(self, tmp_path):
        s = _make_stream()
        s.camera_type = "usb"
        s.error = None
        s._usb_latest_frame = np.zeros((480, 640, 3), dtype=np.uint8)
        s._usb_lock = threading.Lock()

        snap_path = tmp_path / "snap.jpg"
        with patch("camera.stream.cv2.imwrite", side_effect=OSError("disk full")):
            result = s.capture_snapshot(snap_path)

        assert result is False


class TestCameraStreamStart:
    """Test CameraStream.start() with USB and auto-detect paths."""

    def test_usb_start_succeeds(self, monkeypatch):
        import config
        monkeypatch.setattr(config, "CAMERA_TYPE", "usb")
        monkeypatch.setattr(config, "USB_CAMERA_INDEX", 0)
        monkeypatch.setattr(config, "VIDEO_WIDTH", 640)
        monkeypatch.setattr(config, "VIDEO_HEIGHT", 480)
        monkeypatch.setattr(config, "VIDEO_FPS", 15)
        monkeypatch.setattr(config, "CLIP_PRE_SECONDS", 5)

        mock_cap = MagicMock()
        mock_cap.isOpened.return_value = True
        mock_cap.get.return_value = 640.0
        mock_cap.read.return_value = (True, np.zeros((480, 640, 3), dtype=np.uint8))

        from camera.stream import CameraStream
        s = CameraStream()

        with patch("camera.stream.cv2.VideoCapture", return_value=mock_cap):
            result = s.start()

        assert result is True
        assert s.camera_type == "usb"
        assert s.error is None

        # Clean up background thread
        s._usb_running = False
        if hasattr(s, "_usb_thread"):
            s._usb_thread.join(timeout=1)

    def test_usb_start_fails_when_capture_fails(self, monkeypatch):
        import config
        monkeypatch.setattr(config, "CAMERA_TYPE", "usb")
        monkeypatch.setattr(config, "USB_CAMERA_INDEX", 0)

        mock_cap = MagicMock()
        mock_cap.isOpened.return_value = False

        from camera.stream import CameraStream
        s = CameraStream()

        with patch("camera.stream.cv2.VideoCapture", return_value=mock_cap):
            result = s.start()

        assert result is False
        assert s.error is not None

    def test_auto_mode_falls_back_to_usb(self, monkeypatch):
        import config
        monkeypatch.setattr(config, "CAMERA_TYPE", "auto")
        monkeypatch.setattr(config, "USB_CAMERA_INDEX", 0)
        monkeypatch.setattr(config, "VIDEO_WIDTH", 640)
        monkeypatch.setattr(config, "VIDEO_HEIGHT", 480)
        monkeypatch.setattr(config, "VIDEO_FPS", 15)
        monkeypatch.setattr(config, "CLIP_PRE_SECONDS", 5)

        mock_cap = MagicMock()
        mock_cap.isOpened.return_value = True
        mock_cap.get.return_value = 640.0
        mock_cap.read.return_value = (True, np.zeros((480, 640, 3), dtype=np.uint8))

        from camera.stream import CameraStream
        s = CameraStream()

        # Make picamera fail; USB should succeed
        with patch.object(s, "_start_picamera", side_effect=RuntimeError("No pi camera")), \
             patch("camera.stream.cv2.VideoCapture", return_value=mock_cap):
            result = s.start()

        assert result is True
        assert s.camera_type == "usb"

        # Clean up
        s._usb_running = False
        if hasattr(s, "_usb_thread"):
            s._usb_thread.join(timeout=1)


class TestGetFullResFrame:
    """Test CameraStream.get_full_res_frame() thread-safe frame access."""

    def test_returns_frame_copy_for_usb(self):
        s = _make_stream()
        s.camera_type = "usb"
        s._usb_lock = threading.Lock()
        frame = np.ones((480, 640, 3), dtype=np.uint8) * 128
        s._usb_latest_frame = frame

        result = s.get_full_res_frame()
        assert result is not None
        assert result is not frame  # Should be a copy
        np.testing.assert_array_equal(result, frame)

    def test_returns_none_when_no_usb_frame(self):
        s = _make_stream()
        s.camera_type = "usb"
        s._usb_lock = threading.Lock()
        s._usb_latest_frame = None

        assert s.get_full_res_frame() is None

    def test_returns_none_for_unknown_camera_type(self):
        s = _make_stream()
        s.camera_type = None
        assert s.get_full_res_frame() is None

    def test_returns_none_for_picamera_when_backend_fails(self):
        s = _make_stream()
        s.camera_type = "picamera"
        s._backend = MagicMock()
        s._backend.capture_array.side_effect = RuntimeError("no camera")

        assert s.get_full_res_frame() is None
