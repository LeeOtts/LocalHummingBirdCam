"""Camera stream setup using picamera2 with dual-stream configuration."""

import logging
from picamera2 import Picamera2
from picamera2.encoders import H264Encoder
from picamera2.outputs import CircularOutput

import config

logger = logging.getLogger(__name__)


class CameraStream:
    """Manages picamera2 with a main stream for recording and lores stream for detection."""

    def __init__(self):
        self.picam2 = Picamera2()
        self.encoder = None
        self.circular_output = None

    def start(self):
        """Configure and start the camera with dual streams."""
        video_config = self.picam2.create_video_configuration(
            main={"size": (config.VIDEO_WIDTH, config.VIDEO_HEIGHT), "format": "RGB888"},
            lores={"size": (config.LORES_WIDTH, config.LORES_HEIGHT), "format": "YUV420"},
            controls={"FrameDurationLimits": (
                1_000_000 // config.VIDEO_FPS,
                1_000_000 // config.VIDEO_FPS,
            )},
        )
        self.picam2.configure(video_config)

        self.encoder = H264Encoder(bitrate=config.VIDEO_BITRATE)
        self.circular_output = CircularOutput(buffersize=config.CIRCULAR_BUFFER_SIZE)

        self.picam2.start()
        self.picam2.start_encoder(self.encoder, self.circular_output)
        logger.info(
            "Camera started: main=%dx%d@%dfps, lores=%dx%d, buffer=%d frames",
            config.VIDEO_WIDTH, config.VIDEO_HEIGHT, config.VIDEO_FPS,
            config.LORES_WIDTH, config.LORES_HEIGHT, config.CIRCULAR_BUFFER_SIZE,
        )

    def capture_lores_array(self):
        """Capture a frame from the low-resolution stream for detection."""
        return self.picam2.capture_array("lores")

    def stop(self):
        """Stop the camera and encoder."""
        try:
            self.picam2.stop_encoder()
        except Exception:
            pass
        try:
            self.picam2.stop()
        except Exception:
            pass
        logger.info("Camera stopped")
