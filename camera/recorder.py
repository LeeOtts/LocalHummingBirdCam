"""Clip recorder using CircularOutput for pre-detection buffering."""

import logging
import subprocess
import time
from datetime import datetime
from pathlib import Path

import config

logger = logging.getLogger(__name__)


class ClipRecorder:
    """Records video clips by flushing the circular buffer + recording post-detection."""

    def __init__(self, circular_output):
        self.circular_output = circular_output
        config.CLIPS_DIR.mkdir(parents=True, exist_ok=True)

    def record_clip(self) -> Path | None:
        """
        Flush the pre-detection buffer and record for CLIP_POST_SECONDS more.
        Returns the path to the final MP4 file, or None on failure.
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        h264_path = config.CLIPS_DIR / f"hummer_{timestamp}.h264"
        mp4_path = config.CLIPS_DIR / f"hummer_{timestamp}.mp4"

        try:
            # Start writing — flushes the circular buffer (pre-detection footage)
            self.circular_output.fileoutput = str(h264_path)
            self.circular_output.start()
            logger.info("Recording started: %s", h264_path.name)

            # Continue recording for the post-detection period
            time.sleep(config.CLIP_POST_SECONDS)

            # Stop recording
            self.circular_output.stop()
            logger.info("Recording stopped: %s", h264_path.name)

            # Remux H264 to MP4
            mp4_path = self._remux_to_mp4(h264_path, mp4_path)
            if mp4_path:
                # Clean up raw H264
                h264_path.unlink(missing_ok=True)
                return mp4_path
            return None

        except Exception:
            logger.exception("Failed to record clip")
            self.circular_output.stop()
            return None

    def _remux_to_mp4(self, h264_path: Path, mp4_path: Path) -> Path | None:
        """Remux raw H264 to MP4 container using ffmpeg (no re-encoding)."""
        try:
            result = subprocess.run(
                [
                    "ffmpeg", "-y",
                    "-i", str(h264_path),
                    "-c", "copy",
                    str(mp4_path),
                ],
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
