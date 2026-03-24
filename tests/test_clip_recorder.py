"""Tests for ClipRecorder in camera/recorder.py."""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import numpy as np
import pytest


def _make_recorder(camera=None):
    from camera.recorder import ClipRecorder
    r = object.__new__(ClipRecorder)
    r.camera = camera or MagicMock()
    return r


class TestRemuxH264ToMp4:
    """Test _remux_h264_to_mp4() ffmpeg subprocess call."""

    def test_returns_mp4_path_on_success(self, tmp_path):
        h264_path = tmp_path / "hummer.h264"
        mp4_path = tmp_path / "hummer.mp4"
        h264_path.write_bytes(b"fake h264 data")
        # Create the mp4 file so .stat() works
        mp4_path.write_bytes(b"fake mp4 data" * 100)

        mock_result = MagicMock()
        mock_result.returncode = 0

        r = _make_recorder()
        with patch("camera.recorder.subprocess.run", return_value=mock_result):
            result = r._remux_h264_to_mp4(h264_path, mp4_path)

        assert result == mp4_path

    def test_returns_none_on_nonzero_exit(self, tmp_path):
        h264_path = tmp_path / "hummer.h264"
        mp4_path = tmp_path / "hummer.mp4"
        h264_path.write_bytes(b"fake h264 data")

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "ffmpeg error output"

        r = _make_recorder()
        with patch("camera.recorder.subprocess.run", return_value=mock_result):
            result = r._remux_h264_to_mp4(h264_path, mp4_path)

        assert result is None

    def test_returns_none_on_exception(self, tmp_path):
        h264_path = tmp_path / "hummer.h264"
        mp4_path = tmp_path / "hummer.mp4"

        r = _make_recorder()
        with patch("camera.recorder.subprocess.run", side_effect=OSError("no ffmpeg")):
            result = r._remux_h264_to_mp4(h264_path, mp4_path)

        assert result is None

    def test_calls_ffmpeg_with_correct_args(self, tmp_path):
        h264_path = tmp_path / "hummer.h264"
        mp4_path = tmp_path / "hummer.mp4"
        h264_path.write_bytes(b"data")
        mp4_path.write_bytes(b"data" * 100)

        mock_result = MagicMock()
        mock_result.returncode = 0

        r = _make_recorder()
        with patch("camera.recorder.subprocess.run", return_value=mock_result) as mock_run:
            r._remux_h264_to_mp4(h264_path, mp4_path)

        args = mock_run.call_args[0][0]
        assert "-c" in args
        assert "copy" in args
        assert str(h264_path) in args
        assert str(mp4_path) in args


class TestDetectAudioDevice:
    """Test _detect_audio_device() USB audio auto-detection."""

    def test_returns_configured_device_when_not_default(self, monkeypatch):
        import config
        monkeypatch.setattr(config, "AUDIO_DEVICE", "hw:2,0")
        from camera.recorder import ClipRecorder
        result = ClipRecorder._detect_audio_device()
        assert result == "hw:2,0"

    def test_detects_usb_device_from_arecord_output(self, monkeypatch):
        import config
        monkeypatch.setattr(config, "AUDIO_DEVICE", "default")

        mock_result = MagicMock()
        mock_result.stdout = "card 1: Camera [USB Live camera], device 0: USB Audio\n"

        from camera.recorder import ClipRecorder
        with patch("camera.recorder.subprocess.run", return_value=mock_result):
            result = ClipRecorder._detect_audio_device()

        assert result == "hw:1,0"

    def test_returns_default_when_no_usb_found(self, monkeypatch):
        import config
        monkeypatch.setattr(config, "AUDIO_DEVICE", "default")

        mock_result = MagicMock()
        mock_result.stdout = "card 0: PCH [HDA Intel PCH], device 0: ALC269VC\n"

        from camera.recorder import ClipRecorder
        with patch("camera.recorder.subprocess.run", return_value=mock_result):
            result = ClipRecorder._detect_audio_device()

        assert result == "default"

    def test_returns_default_on_arecord_exception(self, monkeypatch):
        import config
        monkeypatch.setattr(config, "AUDIO_DEVICE", "default")

        from camera.recorder import ClipRecorder
        with patch("camera.recorder.subprocess.run", side_effect=OSError("arecord not found")):
            result = ClipRecorder._detect_audio_device()

        assert result == "default"


class TestWriteCompressedFramesToMp4:
    """Test _write_compressed_frames_to_mp4() frame writing with codec fallback."""

    def _make_jpeg_frame(self):
        """Create a real JPEG-encoded numpy array."""
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        import cv2
        _, jpeg = cv2.imencode(".jpg", frame)
        return jpeg

    def test_returns_none_for_empty_frame_list(self, tmp_path):
        mp4_path = tmp_path / "out.mp4"
        r = _make_recorder()
        result = r._write_compressed_frames_to_mp4([], mp4_path)
        assert result is None

    def test_writes_frames_successfully(self, tmp_path):
        mp4_path = tmp_path / "out.mp4"
        # Create a placeholder so .stat() works
        mp4_path.write_bytes(b"\x00" * 100)

        jpegs = [self._make_jpeg_frame(), self._make_jpeg_frame()]

        mock_writer = MagicMock()
        mock_writer.isOpened.return_value = True

        r = _make_recorder()
        with patch("camera.recorder.cv2.VideoWriter", return_value=mock_writer), \
             patch("camera.recorder.cv2.VideoWriter_fourcc", return_value=0), \
             patch("camera.recorder.cv2.imdecode",
                   return_value=np.zeros((480, 640, 3), dtype=np.uint8)):
            result = r._write_compressed_frames_to_mp4(jpegs, mp4_path)

        assert result == mp4_path
        mock_writer.write.assert_called()
        mock_writer.release.assert_called_once()

    def test_codec_fallback_to_mp4v(self, tmp_path):
        mp4_path = tmp_path / "out.mp4"
        mp4_path.write_bytes(b"\x00" * 100)

        jpegs = [self._make_jpeg_frame()]

        # First writer (avc1) fails to open; second (mp4v) succeeds
        mock_writer_fail = MagicMock()
        mock_writer_fail.isOpened.return_value = False

        mock_writer_ok = MagicMock()
        mock_writer_ok.isOpened.return_value = True

        writer_instances = [mock_writer_fail, mock_writer_ok]

        r = _make_recorder()
        with patch("camera.recorder.cv2.VideoWriter", side_effect=writer_instances), \
             patch("camera.recorder.cv2.VideoWriter_fourcc", return_value=0), \
             patch("camera.recorder.cv2.imdecode",
                   return_value=np.zeros((480, 640, 3), dtype=np.uint8)):
            result = r._write_compressed_frames_to_mp4(jpegs, mp4_path)

        # VideoWriter was called twice (once for avc1 attempt, once for mp4v fallback)
        assert result == mp4_path

    def test_returns_none_on_exception(self, tmp_path):
        mp4_path = tmp_path / "out.mp4"
        jpegs = [self._make_jpeg_frame()]

        r = _make_recorder()
        with patch("camera.recorder.cv2.imdecode", side_effect=RuntimeError("decode error")):
            result = r._write_compressed_frames_to_mp4(jpegs, mp4_path)

        assert result is None


class TestCleanupTempFiles:
    """Test _cleanup_temp_files() removes temporary files."""

    def test_removes_existing_files(self, tmp_path):
        from camera.recorder import ClipRecorder
        f1 = tmp_path / "temp1.wav"
        f2 = tmp_path / "temp2.mp4"
        f1.write_bytes(b"audio")
        f2.write_bytes(b"video")

        ClipRecorder._cleanup_temp_files(f1, f2)

        assert not f1.exists()
        assert not f2.exists()

    def test_ignores_nonexistent_file(self, tmp_path):
        from camera.recorder import ClipRecorder
        missing = tmp_path / "does_not_exist.wav"

        # Should not raise
        ClipRecorder._cleanup_temp_files(missing)

    def test_handles_non_path_argument(self, tmp_path):
        from camera.recorder import ClipRecorder
        # Passing a string (not a Path) — should not raise
        ClipRecorder._cleanup_temp_files("not_a_path_object")

    def test_handles_multiple_paths(self, tmp_path):
        from camera.recorder import ClipRecorder
        files = [tmp_path / f"temp{i}.dat" for i in range(3)]
        for f in files:
            f.write_bytes(b"data")

        ClipRecorder._cleanup_temp_files(*files)

        for f in files:
            assert not f.exists()


class TestRecordClip:
    """Test record_clip() routes to the correct backend."""

    def test_routes_to_picamera_when_type_picamera(self):
        camera = MagicMock()
        camera.camera_type = "picamera"
        r = _make_recorder(camera)

        with patch.object(r, "_record_picamera", return_value=None) as mock_pic, \
             patch.object(r, "_record_usb", return_value=None) as mock_usb:
            r.record_clip()

        mock_pic.assert_called_once()
        mock_usb.assert_not_called()

    def test_routes_to_usb_when_type_usb(self):
        camera = MagicMock()
        camera.camera_type = "usb"
        r = _make_recorder(camera)

        with patch.object(r, "_record_picamera", return_value=None) as mock_pic, \
             patch.object(r, "_record_usb", return_value=None) as mock_usb:
            r.record_clip()

        mock_usb.assert_called_once()
        mock_pic.assert_not_called()
