"""Clip recorder supporting both Pi Camera (CircularOutput) and USB camera."""

import gc
import logging
import subprocess
import time
from datetime import datetime
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from pytz import timezone as _pytz_tz
    ZoneInfo = lambda key: _pytz_tz(key)

import cv2

import config

_local_tz = ZoneInfo(config.LOCATION_TIMEZONE)

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
        timestamp = datetime.now(tz=_local_tz).strftime("%Y%m%d_%H%M%S")
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
        timestamp = datetime.now(tz=_local_tz).strftime("%Y%m%d_%H%M%S")
        mp4_path = config.CLIPS_DIR / f"hummer_{timestamp}.mp4"
        video_path = config.CLIPS_DIR / f"_video_{timestamp}.mp4"
        audio_path = config.CLIPS_DIR / f"_audio_{timestamp}.wav"

        audio_proc = None
        try:
            # --- Step 1: Get pre-detection frames from buffer (compressed) ---
            pre_jpegs = self.camera.frame_buffer.get_all_compressed()
            logger.info("Got %d pre-detection frames from buffer", len(pre_jpegs))

            # --- Step 2: Start audio recording in background (if enabled) ---
            if config.AUDIO_ENABLED:
                audio_device = self._detect_audio_device()
                try:
                    # Use plughw for auto format conversion, stereo (2ch),
                    # 16kHz sample rate — matches most USB camera mics
                    device = audio_device.replace("hw:", "plughw:")
                    arecord_cmd = [
                        "arecord",
                        "-D", device,
                        "-f", "S16_LE",
                        "-r", "16000",
                        "-c", "2",
                        "-d", str(config.CLIP_POST_SECONDS),
                        str(audio_path),
                    ]
                    logger.info("Starting audio: %s", " ".join(arecord_cmd))
                    audio_proc = subprocess.Popen(
                        arecord_cmd,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.PIPE,
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
                full_frame = self.camera.get_full_res_frame()
                if full_frame is not None:
                    _, jpeg = cv2.imencode(
                        '.jpg', full_frame,
                        [cv2.IMWRITE_JPEG_QUALITY, 80])
                    post_frames.append(jpeg)
                time.sleep(interval)

            # Wait for audio to finish
            if audio_proc is not None:
                try:
                    _, stderr_data = audio_proc.communicate(timeout=config.CLIP_POST_SECONDS + 10)
                    if audio_proc.returncode != 0:
                        logger.warning("arecord failed (exit %d): %s",
                                       audio_proc.returncode,
                                       stderr_data.decode(errors="replace")[-300:] if stderr_data else "no output")
                    elif audio_path.exists():
                        logger.info("Audio recorded: %s (%.1f KB)",
                                    audio_path.name, audio_path.stat().st_size / 1024)
                except subprocess.TimeoutExpired:
                    audio_proc.kill()
                    audio_proc.wait()

            # --- Step 4: Write frames to video file (decompress one at a time to save RAM) ---
            all_jpegs = pre_jpegs + post_frames
            del pre_jpegs, post_frames  # Free originals, all_jpegs has the data now
            total_frames = len(all_jpegs)
            if total_frames == 0:
                logger.warning("No frames captured — skipping clip")
                self._cleanup_temp_files(video_path, audio_path)
                return None

            self._write_compressed_frames_to_mp4(all_jpegs, video_path)

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

                if mux_result.returncode != 0:
                    logger.warning("Audio mux failed, using video-only: %s",
                                   mux_result.stderr[-300:])
                    # Fall back to video only — rename temp video to final path
                    if video_path.exists():
                        video_path.rename(mp4_path)
                    else:
                        self._write_compressed_frames_to_mp4(all_jpegs, mp4_path)

                self._cleanup_temp_files(video_path, audio_path)
            else:
                # No audio — just rename video
                video_path.rename(mp4_path)
                self._cleanup_temp_files(audio_path)

            del all_jpegs  # Free frame memory before returning

            if mp4_path.exists():
                size_mb = mp4_path.stat().st_size / 1_048_576
                logger.info("Final clip: %s (%.1f MB, %d frames)",
                            mp4_path.name, size_mb, total_frames)
                return mp4_path

            return None

        except Exception:
            logger.exception("Failed to record clip (USB)")
            self._cleanup_temp_files(video_path, audio_path)
            return None
        finally:
            # Always kill audio subprocess to prevent zombie processes
            if audio_proc is not None and audio_proc.poll() is None:
                try:
                    audio_proc.kill()
                    audio_proc.wait(timeout=5)
                except Exception:
                    pass
            # Force GC to return frame memory to OS
            gc.collect()

    def _write_compressed_frames_to_mp4(self, jpeg_frames: list, mp4_path: Path) -> Path | None:
        """Write JPEG-compressed frames to MP4, decompressing one at a time to save RAM."""
        try:
            if not jpeg_frames:
                return None

            first = cv2.imdecode(jpeg_frames[0], cv2.IMREAD_COLOR)
            h, w = first.shape[:2]

            fourcc = cv2.VideoWriter_fourcc(*"avc1")
            writer = cv2.VideoWriter(str(mp4_path), fourcc, config.VIDEO_FPS, (w, h))

            if not writer.isOpened():
                writer.release()
                fourcc = cv2.VideoWriter_fourcc(*"mp4v")
                writer = cv2.VideoWriter(str(mp4_path), fourcc, config.VIDEO_FPS, (w, h))

            # Write first frame (already decoded above)
            writer.write(first)

            # Decompress and write remaining frames one at a time
            for jpeg in jpeg_frames[1:]:
                frame = cv2.imdecode(jpeg, cv2.IMREAD_COLOR)
                if frame is not None:
                    writer.write(frame)

            writer.release()

            size_mb = mp4_path.stat().st_size / 1_048_576
            logger.info("Wrote clip: %s (%.1f MB, %d frames)",
                         mp4_path.name, size_mb, len(jpeg_frames))
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
    def _detect_audio_device() -> str:
        """Auto-detect USB audio device if AUDIO_DEVICE is 'default'."""
        device = config.AUDIO_DEVICE
        if device != "default":
            return device

        try:
            result = subprocess.run(
                ["arecord", "-l"],
                capture_output=True, text=True, timeout=5,
            )
            for line in result.stdout.splitlines():
                # Look for lines like: "card 1: camera [USB  Live camera], device 0:"
                if "card" in line.lower() and "usb" in line.lower():
                    import re
                    match = re.search(r"card\s+(\d+).*device\s+(\d+)", line, re.IGNORECASE)
                    if match:
                        detected = f"hw:{match.group(1)},{match.group(2)}"
                        logger.info("Auto-detected USB audio device: %s (%s)",
                                    detected, line.strip())
                        return detected
        except Exception:
            logger.warning("Could not auto-detect audio device")

        logger.warning("No USB audio device found, using 'default'")
        return "default"

    @staticmethod
    def _cleanup_temp_files(*paths):
        """Remove temporary files."""
        for p in paths:
            try:
                if isinstance(p, Path):
                    p.unlink(missing_ok=True)
            except Exception:
                pass
