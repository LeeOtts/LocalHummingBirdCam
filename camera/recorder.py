"""Clip recorder supporting both Pi Camera (CircularOutput) and USB camera."""

import logging
import subprocess
import time
from datetime import datetime
from pathlib import Path

import cv2

import config

logger = logging.getLogger(__name__)


class ClipRecorder:
    """Records video clips from either Pi Camera circular buffer or USB camera frames."""

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
        Record using frames from the existing USB capture thread.

        Grabs pre-detection frames from the rolling buffer, then captures
        post-detection frames from the live thread. No second camera handle
        needed — uses the frames already being captured.

        Audio is captured separately via ffmpeg from the ALSA device and
        muxed into the final MP4.
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        mp4_path = config.CLIPS_DIR / f"hummer_{timestamp}.mp4"
        video_path = config.CLIPS_DIR / f"_video_{timestamp}.mp4"
        audio_path = config.CLIPS_DIR / f"_audio_{timestamp}.wav"

        try:
            # --- Step 1: Get pre-detection frames from buffer ---
            pre_frames = self.camera.frame_buffer.get_all()
            logger.info("Got %d pre-detection frames from buffer", len(pre_frames))

            # --- Step 2: Start audio recording in background (if enabled) ---
            audio_proc = None
            if config.AUDIO_ENABLED:
                try:
                    audio_proc = subprocess.Popen(
                        [
                            "arecord",
                            "-D", config.AUDIO_DEVICE,
                            "-f", "S16_LE",
                            "-r", "44100",
                            "-c", "1",
                            "-d", str(config.CLIP_POST_SECONDS),
                            str(audio_path),
                        ],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                except Exception:
                    logger.warning("Failed to start audio recording — continuing without audio")
                    audio_proc = None

            # --- Step 3: Capture post-detection frames from live thread ---
            post_frames = []
            fps = config.VIDEO_FPS
            total_post_frames = int(config.CLIP_POST_SECONDS * fps)
            interval = 1.0 / fps

            logger.info("Recording %d post-detection frames (~%ds)...",
                         total_post_frames, config.CLIP_POST_SECONDS)

            for _ in range(total_post_frames):
                if hasattr(self.camera, '_usb_lock'):
                    with self.camera._usb_lock:
                        if self.camera._usb_latest_frame is not None:
                            post_frames.append(self.camera._usb_latest_frame.copy())
                time.sleep(interval)

            # Wait for audio to finish
            if audio_proc is not None:
                try:
                    audio_proc.wait(timeout=config.CLIP_POST_SECONDS + 10)
                except subprocess.TimeoutExpired:
                    audio_proc.kill()

            # --- Step 4: Write all frames to video file ---
            all_frames = pre_frames + post_frames
            if not all_frames:
                logger.warning("No frames captured — skipping clip")
                self._cleanup_temp_files(video_path, audio_path)
                return None

            self._write_frames_to_mp4(all_frames, video_path)

            # --- Step 5: Mux video + audio if we have audio ---
            if audio_proc is not None and audio_path.exists() and audio_path.stat().st_size > 0:
                mux_result = subprocess.run(
                    [
                        "ffmpeg", "-y",
                        "-i", str(video_path),
                        "-i", str(audio_path),
                        "-c:v", "copy",
                        "-c:a", "aac", "-b:a", "96k",
                        "-shortest",
                        str(mp4_path),
                    ],
                    capture_output=True, text=True, timeout=60,
                )

                self._cleanup_temp_files(video_path, audio_path)

                if mux_result.returncode != 0:
                    logger.warning("Audio mux failed, using video-only: %s",
                                   mux_result.stderr[-300:])
                    # Fall back to video only
                    if not mp4_path.exists():
                        self._write_frames_to_mp4(all_frames, mp4_path)
            else:
                # No audio — just rename video
                video_path.rename(mp4_path)
                self._cleanup_temp_files(audio_path)

            if mp4_path.exists():
                size_mb = mp4_path.stat().st_size / 1_048_576
                logger.info("Final clip: %s (%.1f MB, %d frames)",
                            mp4_path.name, size_mb, len(all_frames))
                return mp4_path

            return None

        except Exception:
            logger.exception("Failed to record clip (USB)")
            self._cleanup_temp_files(video_path, audio_path)
            return None

    def _write_frames_to_mp4(self, frames: list, mp4_path: Path) -> Path | None:
        """Write a list of BGR frames to an MP4 file using OpenCV."""
        try:
            h, w = frames[0].shape[:2]
            # Use H264 if available, fall back to mp4v
            fourcc = cv2.VideoWriter_fourcc(*"avc1")
            writer = cv2.VideoWriter(str(mp4_path), fourcc, config.VIDEO_FPS, (w, h))

            if not writer.isOpened():
                # Fall back to mp4v codec
                fourcc = cv2.VideoWriter_fourcc(*"mp4v")
                writer = cv2.VideoWriter(str(mp4_path), fourcc, config.VIDEO_FPS, (w, h))

            for frame in frames:
                writer.write(frame)

            writer.release()

            size_mb = mp4_path.stat().st_size / 1_048_576
            logger.info("Wrote clip: %s (%.1f MB, %d frames)",
                         mp4_path.name, size_mb, len(frames))
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

    @staticmethod
    def _cleanup_temp_files(*paths):
        """Remove temporary files."""
        for p in paths:
            try:
                if isinstance(p, Path):
                    p.unlink(missing_ok=True)
            except Exception:
                pass
