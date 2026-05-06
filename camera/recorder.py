"""Clip recorder supporting both Pi Camera (CircularOutput) and USB camera."""

import ctypes
import gc
import logging
import subprocess
import sys
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


def force_gc():
    """Run garbage collection and release freed memory back to the OS.

    On Linux (glibc), malloc_trim() returns freed heap pages to the OS,
    countering Python's arena allocator which otherwise holds onto memory.
    """
    gc.collect()
    if sys.platform == "linux":
        try:
            ctypes.CDLL("libc.so.6").malloc_trim(0)
        except Exception:
            pass


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

        Streams frames directly to a VideoWriter to minimize memory usage —
        only one decoded frame is in memory at a time (~6MB) instead of
        accumulating all frames in a list (~30-50MB).

        Audio is captured separately via ffmpeg from the ALSA device and
        muxed into the final MP4.
        """
        timestamp = datetime.now(tz=_local_tz).strftime("%Y%m%d_%H%M%S")
        mp4_path = config.CLIPS_DIR / f"hummer_{timestamp}.mp4"
        video_path = config.CLIPS_DIR / f"_video_{timestamp}.mp4"
        audio_path = config.CLIPS_DIR / f"_audio_{timestamp}.wav"

        audio_proc = None
        writer = None
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

            # --- Step 3: Open VideoWriter and stream pre-detection frames ---
            total_frames = 0

            if pre_jpegs:
                first = cv2.imdecode(pre_jpegs[0], cv2.IMREAD_COLOR)
                if first is not None:
                    h, w = first.shape[:2]
                    writer = self._open_writer(video_path, w, h)
                    if writer:
                        writer.write(first)
                        total_frames += 1
                    del first

                # Write remaining pre-detection frames one at a time
                for jpeg in pre_jpegs[1:]:
                    frame = cv2.imdecode(jpeg, cv2.IMREAD_COLOR)
                    if frame is not None and writer:
                        writer.write(frame)
                        total_frames += 1
                del pre_jpegs  # Free buffer copies immediately

            # --- Step 4: Capture post-detection frames as they arrive ---
            # Frame-driven (not timer-driven): wait_next_frame blocks until the
            # capture thread delivers a unique new frame, so a capture stall
            # shortens the clip slightly instead of producing duplicate
            # frozen frames that look like the bird teleporting.
            post_deadline = time.monotonic() + config.CLIP_POST_SECONDS
            last_seq = 0
            consecutive_timeouts = 0

            logger.info("Recording up to ~%ds of post-detection frames...",
                         config.CLIP_POST_SECONDS)

            while time.monotonic() < post_deadline:
                full_frame, last_seq = self.camera.wait_next_frame(last_seq, timeout=0.5)
                if full_frame is None:
                    consecutive_timeouts += 1
                    if consecutive_timeouts >= 4:  # ~2s with no new frame -> camera dead
                        logger.warning("Capture stalled >2s; ending clip early")
                        break
                    continue
                consecutive_timeouts = 0
                if writer is None:
                    h, w = full_frame.shape[:2]
                    writer = self._open_writer(video_path, w, h)
                if writer:
                    writer.write(full_frame)
                    total_frames += 1

            # Release writer before muxing
            if writer:
                writer.release()
                writer = None

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

            if total_frames == 0:
                logger.warning("No frames captured — skipping clip")
                self._cleanup_temp_files(video_path, audio_path)
                return None

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

                self._cleanup_temp_files(video_path, audio_path)
            else:
                # No audio — just rename video
                video_path.rename(mp4_path)
                self._cleanup_temp_files(audio_path)

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
            if writer is not None:
                try:
                    writer.release()
                except Exception:
                    pass
            # Always kill audio subprocess to prevent zombie processes
            if audio_proc is not None and audio_proc.poll() is None:
                try:
                    audio_proc.kill()
                    audio_proc.wait(timeout=5)
                except Exception:
                    pass
            # Force GC to return frame memory to OS
            force_gc()

    @staticmethod
    def _open_writer(mp4_path: Path, w: int, h: int) -> cv2.VideoWriter | None:
        """Open a VideoWriter, trying avc1 first then falling back to mp4v."""
        fourcc = cv2.VideoWriter_fourcc(*"avc1")
        writer = cv2.VideoWriter(str(mp4_path), fourcc, config.VIDEO_FPS, (w, h))
        if not writer.isOpened():
            writer.release()
            fourcc = cv2.VideoWriter_fourcc(*"mp4v")
            writer = cv2.VideoWriter(str(mp4_path), fourcc, config.VIDEO_FPS, (w, h))
        if not writer.isOpened():
            writer.release()
            logger.error("Failed to open VideoWriter for %s", mp4_path.name)
            return None
        return writer


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
    def generate_thumbnail(mp4_path: Path) -> Path | None:
        """Extract a single frame from a clip as a JPEG thumbnail for the website."""
        thumb_path = mp4_path.with_name(mp4_path.stem + "_thumb.jpg")
        try:
            result = subprocess.run(
                [
                    "ffmpeg", "-y",
                    "-i", str(mp4_path),
                    "-ss", "3",  # 3 seconds in (past pre-roll, into the action)
                    "-frames:v", "1",
                    "-q:v", "5",
                    str(thumb_path),
                ],
                capture_output=True, text=True, timeout=15,
            )
            if result.returncode == 0 and thumb_path.exists():
                logger.info("Generated thumbnail: %s (%.1f KB)",
                            thumb_path.name, thumb_path.stat().st_size / 1024)
                return thumb_path
            else:
                logger.warning("Thumbnail generation failed: %s", result.stderr[-200:])
                return None
        except Exception:
            logger.exception("Thumbnail generation error")
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
