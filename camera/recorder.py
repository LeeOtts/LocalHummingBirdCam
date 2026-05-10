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

    def record_clip(self, detect_fn=None) -> Path | None:
        """Record a clip (pre-roll + post-detection) and return the MP4 path.

        detect_fn: optional callable that receives a full-res BGR frame and
        returns True while the bird is still present.  Each True result resets
        the recording deadline to now + CLIP_POST_SECONDS, so recording
        continues as long as the bird remains.  CLIP_MAX_SECONDS caps the
        total post-detection duration.
        """
        if self.camera.camera_type == "picamera":
            return self._record_picamera()
        else:
            return self._record_usb(detect_fn=detect_fn)

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

    def _record_usb(self, detect_fn=None) -> Path | None:
        """Record using frames from the existing USB capture thread.

        Streams frames directly to a VideoWriter to minimize memory usage.
        Audio is captured separately via arecord and muxed into the final MP4.

        detect_fn: optional callable receiving a full-res BGR frame; returns
        True while the bird is visible.  Each True result resets the recording
        deadline to now + CLIP_POST_SECONDS, extending the clip while the
        bird stays.  CLIP_MAX_SECONDS caps total post-detection duration.
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
            # No -d flag: record indefinitely until stopped after the clip ends.
            # This avoids the -shortest truncation bug where arecord exits early
            # (ALSA buffer overrun) and ffmpeg cuts the video to match.
            if config.AUDIO_ENABLED:
                audio_device = self._detect_audio_device()
                try:
                    device = audio_device.replace("hw:", "plughw:")
                    arecord_cmd = [
                        "arecord",
                        "-D", device,
                        "-f", "S16_LE",
                        "-r", "16000",
                        "-c", "2",
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

                for jpeg in pre_jpegs[1:]:
                    frame = cv2.imdecode(jpeg, cv2.IMREAD_COLOR)
                    if frame is not None and writer:
                        writer.write(frame)
                        total_frames += 1
                del pre_jpegs

            # --- Step 4: Capture post-detection frames, extending while bird present ---
            # last_bird_time initialises to post_start so the minimum clip length
            # is always CLIP_POST_SECONDS regardless of detect_fn results.
            # Each True from detect_fn resets last_bird_time → deadline extends.
            # CLIP_MAX_SECONDS is a hard cap to prevent runaway recordings.
            post_start = time.monotonic()
            last_bird_time = post_start
            max_end = post_start + config.CLIP_MAX_SECONDS
            last_seq = 0

            logger.info(
                "Recording: %ds buffer after bird leaves (max %ds)...",
                config.CLIP_POST_SECONDS, config.CLIP_MAX_SECONDS,
            )

            while True:
                now = time.monotonic()
                deadline = last_bird_time + config.CLIP_POST_SECONDS
                if now >= deadline or now >= max_end:
                    break

                remaining = min(deadline, max_end) - now
                full_frame, last_seq = self.camera.wait_next_frame(
                    last_seq, timeout=min(0.5, max(0.05, remaining)))
                if full_frame is None:
                    continue

                # Extend deadline if bird is still visible
                if detect_fn is not None:
                    try:
                        if detect_fn(full_frame):
                            last_bird_time = time.monotonic()
                    except Exception:
                        pass

                if writer is None:
                    h, w = full_frame.shape[:2]
                    writer = self._open_writer(video_path, w, h)
                if writer:
                    writer.write(full_frame)
                    total_frames += 1

            post_duration = time.monotonic() - post_start

            # Release writer before muxing
            if writer:
                writer.release()
                writer = None

            # --- Step 4b: Compute declared FPS so playback duration == wall-clock ---
            target_duration = float(config.CLIP_PRE_SECONDS) + post_duration
            if total_frames > 0 and target_duration > 0:
                measured_fps = total_frames / target_duration
            else:
                measured_fps = float(config.VIDEO_FPS)
            measured_fps = max(1.0, min(measured_fps, float(config.VIDEO_FPS) * 2))

            # --- Step 4c: Stop audio recording ---
            if audio_proc is not None:
                try:
                    audio_proc.terminate()
                    _, stderr_data = audio_proc.communicate(timeout=10)
                    rc = audio_proc.returncode
                    # SIGTERM (-15) is the expected exit on terminate()
                    if rc not in (0, -15):
                        logger.warning(
                            "arecord exited %d: %s", rc,
                            stderr_data.decode(errors="replace")[-300:] if stderr_data else "",
                        )
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

            # --- Step 5: Mux video + audio ---
            # `-r <fps>` BEFORE `-i video.mp4` re-stamps PTS without re-encoding.
            # `-itsoffset CLIP_PRE_SECONDS` delays audio to align it with the
            # post-detection phase of the video (audio starts when pre-roll ends).
            # No -shortest: audio may be slightly shorter due to SIGTERM timing;
            # the video timeline is authoritative.
            fps_arg = f"{measured_fps:.3f}"
            use_audio = (
                audio_proc is not None
                and self._audio_is_valid(audio_path, post_duration)
            )
            if use_audio:
                mux_result = subprocess.run(
                    [
                        "ffmpeg", "-y",
                        "-r", fps_arg,
                        "-i", str(video_path),
                        "-itsoffset", str(config.CLIP_PRE_SECONDS),
                        "-i", str(audio_path),
                        "-c:v", "copy",
                        "-c:a", "aac", "-b:a", "96k",
                        str(mp4_path),
                    ],
                    capture_output=True, text=True,
                    timeout=max(60, int(target_duration) + 30),
                )

                if mux_result.returncode != 0:
                    logger.warning("Audio mux failed, using video-only: %s",
                                   mux_result.stderr[-300:])
                    self._retime_video_only(video_path, mp4_path, fps_arg)

                self._cleanup_temp_files(video_path, audio_path)
            else:
                self._retime_video_only(video_path, mp4_path, fps_arg)
                self._cleanup_temp_files(audio_path)

            logger.info("Clip retimed to %.2f fps (wallclock %.1fs, %d frames)",
                        measured_fps, target_duration, total_frames)

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


    @staticmethod
    def _audio_is_valid(audio_path: Path, expected_seconds: float) -> bool:
        """Return True if the audio file covers at least 70% of expected_seconds.

        16 kHz × 2 ch × 2 bytes/sample = 64 000 bytes/sec.  A WAV header is
        44 bytes, so actual audio duration ≈ (size - 44) / 64000.  An overrun-
        truncated file from arecord may be much shorter than expected_seconds,
        which would previously cause -shortest to truncate the whole clip.
        """
        if not audio_path.exists():
            return False
        size = audio_path.stat().st_size
        actual_seconds = max(0.0, size - 44) / 64_000
        return actual_seconds >= expected_seconds * 0.7

    @staticmethod
    def _retime_video_only(video_path: Path, mp4_path: Path, fps_arg: str) -> None:
        """Re-stamp the video's frame rate to ``fps_arg`` without re-encoding.

        Used when there is no audio to mux. ``-r <fps>`` BEFORE ``-i`` tells
        ffmpeg to interpret the input at that rate; ``-c copy`` keeps the
        codec data unchanged so this is a fast container rewrite, not a
        re-encode (important on Pi 3B+ where libx264 is slow).

        Falls back to a plain rename if ffmpeg fails so we never lose the
        clip entirely.
        """
        try:
            result = subprocess.run(
                ["ffmpeg", "-y", "-r", fps_arg, "-i", str(video_path),
                 "-c", "copy", str(mp4_path)],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode == 0 and mp4_path.exists():
                video_path.unlink(missing_ok=True)
                return
            logger.warning("Video retime failed (fps=%s): %s",
                           fps_arg, result.stderr[-300:])
        except Exception:
            logger.exception("Video retime error")
        # Fallback: keep the un-retimed video so we don't lose the clip.
        if video_path.exists() and not mp4_path.exists():
            video_path.rename(mp4_path)

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
