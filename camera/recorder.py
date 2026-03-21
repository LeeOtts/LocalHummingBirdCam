"""Clip recorder supporting both Pi Camera (CircularOutput) and USB camera (FrameBuffer)."""

import logging
import subprocess
import time
from datetime import datetime
from pathlib import Path

import cv2

import config

logger = logging.getLogger(__name__)


class ClipRecorder:
    """Records video clips from either Pi Camera circular buffer or USB frame buffer."""

    def __init__(self, camera_stream):
        self.camera = camera_stream
        config.CLIPS_DIR.mkdir(parents=True, exist_ok=True)

    def record_clip(self) -> Path | None:
        """
        Record a 30-second clip (pre + post detection).
        Returns the path to the final MP4 file, or None on failure.
        """
        if self.camera.camera_type == "picamera":
            return self._record_picamera()
        else:
            return self._record_usb()

    def _record_picamera(self) -> Path | None:
        """Record using picamera2 CircularOutput."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        h264_path = config.CLIPS_DIR / f"hummer_{timestamp}.h264"
        mp4_path = config.CLIPS_DIR / f"hummer_{timestamp}.mp4"

        try:
            circ = self.camera.circular_output
            circ.fileoutput = str(h264_path)
            circ.start()
            logger.info("Recording started (picamera): %s", h264_path.name)

            time.sleep(config.CLIP_POST_SECONDS)

            circ.stop()
            logger.info("Recording stopped: %s", h264_path.name)

            mp4_path = self._remux_h264_to_mp4(h264_path, mp4_path)
            if mp4_path:
                h264_path.unlink(missing_ok=True)
                return mp4_path
            return None

        except Exception:
            logger.exception("Failed to record clip (picamera)")
            try:
                self.camera.circular_output.stop()
            except Exception:
                pass
            return None

    def _record_usb(self) -> Path | None:
        """Record using USB camera frame buffer + continued capture."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        mp4_path = config.CLIPS_DIR / f"hummer_{timestamp}.mp4"

        try:
            # Grab the pre-detection frames from the rolling buffer
            pre_frames = self.camera.frame_buffer.get_all()
            logger.info("Recording started (USB): %s (%d pre-detection frames)",
                        mp4_path.name, len(pre_frames))

            # Record post-detection frames
            post_frames = []
            total_post_frames = config.VIDEO_FPS * config.CLIP_POST_SECONDS
            interval = 1.0 / config.VIDEO_FPS

            for _ in range(total_post_frames):
                frame = self.camera.capture_lores_array()
                # Capture full-res for recording
                full_frame = None
                if hasattr(self.camera, '_usb_lock'):
                    with self.camera._usb_lock:
                        full_frame = self.camera._usb_latest_frame
                if full_frame is not None:
                    post_frames.append(full_frame.copy())
                time.sleep(interval)

            logger.info("Recording stopped: %s (%d post-detection frames)",
                        mp4_path.name, len(post_frames))

            # Write all frames to MP4
            all_frames = pre_frames + post_frames
            if not all_frames:
                logger.warning("No frames to write")
                return None

            return self._write_frames_to_mp4(all_frames, mp4_path)

        except Exception:
            logger.exception("Failed to record clip (USB)")
            return None

    def _write_frames_to_mp4(self, frames: list, mp4_path: Path) -> Path | None:
        """Write a list of BGR frames to an MP4 file using OpenCV."""
        try:
            h, w = frames[0].shape[:2]
            fourcc = cv2.VideoWriter_fourcc(*"mp4v")
            writer = cv2.VideoWriter(str(mp4_path), fourcc, config.VIDEO_FPS, (w, h))

            for frame in frames:
                writer.write(frame)

            writer.release()

            size_mb = mp4_path.stat().st_size / 1_048_576
            logger.info("Wrote MP4: %s (%.1f MB, %d frames)", mp4_path.name, size_mb, len(frames))
            return mp4_path

        except Exception:
            logger.exception("Failed to write MP4")
            return None

    def _remux_h264_to_mp4(self, h264_path: Path, mp4_path: Path) -> Path | None:
        """Remux raw H264 to MP4 container using ffmpeg (no re-encoding)."""
        try:
            result = subprocess.run(
                ["ffmpeg", "-y", "-i", str(h264_path), "-c", "copy", str(mp4_path)],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode == 0:
                logger.info("Remuxed to MP4: %s (%.1f MB)",
                            mp4_path.name, mp4_path.stat().st_size / 1_048_576)
                return mp4_path
            else:
                logger.error("ffmpeg failed: %s", result.stderr[-500:])
                return None
        except Exception:
            logger.exception("ffmpeg remux error")
            return None
