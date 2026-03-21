"""Clip recorder supporting both Pi Camera (CircularOutput) and USB camera (ffmpeg with audio)."""

import logging
import subprocess
import time
from datetime import datetime
from pathlib import Path

import cv2

import config

logger = logging.getLogger(__name__)


class ClipRecorder:
    """Records video clips from either Pi Camera circular buffer or USB camera + mic via ffmpeg."""

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
        """
        Record using ffmpeg directly from the USB camera + microphone.

        This captures video from /dev/video{N} and audio from ALSA in a single
        ffmpeg process, producing a proper MP4 with sound.

        Pre-detection frames from the rolling buffer are written as a separate
        silent clip and then concatenated with the ffmpeg-recorded portion.
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        mp4_path = config.CLIPS_DIR / f"hummer_{timestamp}.mp4"
        pre_path = config.CLIPS_DIR / f"_pre_{timestamp}.mp4"
        post_path = config.CLIPS_DIR / f"_post_{timestamp}.mp4"
        concat_list = config.CLIPS_DIR / f"_concat_{timestamp}.txt"

        try:
            # --- Step 1: Write pre-detection frames (silent) ---
            pre_frames = self.camera.frame_buffer.get_all()
            has_pre = len(pre_frames) > 0
            if has_pre:
                self._write_frames_to_mp4(pre_frames, pre_path)
                logger.info("Saved %d pre-detection frames", len(pre_frames))

            # --- Step 2: Record post-detection with ffmpeg (video + audio) ---
            video_dev = f"/dev/video{config.USB_CAMERA_INDEX}"
            duration = config.CLIP_POST_SECONDS

            ffmpeg_cmd = [
                "ffmpeg", "-y",
                # Video input
                "-f", "v4l2",
                "-framerate", str(config.VIDEO_FPS),
                "-video_size", f"{config.VIDEO_WIDTH}x{config.VIDEO_HEIGHT}",
                "-i", video_dev,
            ]

            if config.AUDIO_ENABLED:
                ffmpeg_cmd += [
                    # Audio input
                    "-f", "alsa",
                    "-ac", "1",
                    "-ar", "44100",
                    "-i", config.AUDIO_DEVICE,
                ]

            ffmpeg_cmd += [
                # Duration
                "-t", str(duration),
                # Encoding
                "-c:v", "libx264",
                "-preset", "ultrafast",
                "-crf", "23",
                "-pix_fmt", "yuv420p",
            ]

            if config.AUDIO_ENABLED:
                ffmpeg_cmd += ["-c:a", "aac", "-b:a", "128k"]

            ffmpeg_cmd += [str(post_path)]

            logger.info("Recording started (ffmpeg): %s for %ds", mp4_path.name, duration)

            result = subprocess.run(
                ffmpeg_cmd,
                capture_output=True, text=True,
                timeout=duration + 30,
            )

            if result.returncode != 0:
                logger.error("ffmpeg recording failed: %s", result.stderr[-500:])
                # Fall back to frame-buffer-only recording
                if has_pre:
                    pre_path.rename(mp4_path)
                    return mp4_path
                return None

            logger.info("Recording stopped: %s", mp4_path.name)

            # --- Step 3: Concatenate pre + post if we have pre-detection frames ---
            if has_pre and pre_path.exists():
                # Use ffmpeg concat demuxer
                concat_list.write_text(
                    f"file '{pre_path.name}'\nfile '{post_path.name}'\n"
                )

                concat_cmd = [
                    "ffmpeg", "-y",
                    "-f", "concat",
                    "-safe", "0",
                    "-i", str(concat_list),
                    "-c", "copy",
                    str(mp4_path),
                ]

                concat_result = subprocess.run(
                    concat_cmd,
                    capture_output=True, text=True,
                    timeout=30,
                )

                # Clean up temp files
                pre_path.unlink(missing_ok=True)
                post_path.unlink(missing_ok=True)
                concat_list.unlink(missing_ok=True)

                if concat_result.returncode != 0:
                    logger.warning("Concat failed, using post-recording only: %s",
                                   concat_result.stderr[-300:])
                    # If concat failed, just use the post recording
                    if post_path.exists():
                        post_path.rename(mp4_path)
                    return mp4_path if mp4_path.exists() else None
            else:
                # No pre-detection frames, just rename the post recording
                post_path.rename(mp4_path)

            if mp4_path.exists():
                size_mb = mp4_path.stat().st_size / 1_048_576
                logger.info("Final clip: %s (%.1f MB)", mp4_path.name, size_mb)
                return mp4_path

            return None

        except subprocess.TimeoutExpired:
            logger.error("ffmpeg recording timed out")
            self._cleanup_temp_files(pre_path, post_path, concat_list)
            return None
        except Exception:
            logger.exception("Failed to record clip (USB)")
            self._cleanup_temp_files(pre_path, post_path, concat_list)
            return None

    def _write_frames_to_mp4(self, frames: list, mp4_path: Path) -> Path | None:
        """Write a list of BGR frames to an MP4 file using OpenCV (silent — no audio)."""
        try:
            h, w = frames[0].shape[:2]
            fourcc = cv2.VideoWriter_fourcc(*"mp4v")
            writer = cv2.VideoWriter(str(mp4_path), fourcc, config.VIDEO_FPS, (w, h))

            for frame in frames:
                writer.write(frame)

            writer.release()

            size_mb = mp4_path.stat().st_size / 1_048_576
            logger.info("Wrote pre-clip: %s (%.1f MB, %d frames)", mp4_path.name, size_mb, len(frames))
            return mp4_path

        except Exception:
            logger.exception("Failed to write pre-clip MP4")
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

    @staticmethod
    def _cleanup_temp_files(*paths):
        """Remove temporary files."""
        for p in paths:
            try:
                p.unlink(missing_ok=True)
            except Exception:
                pass
