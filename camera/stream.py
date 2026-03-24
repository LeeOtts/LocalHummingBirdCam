"""Camera stream with automatic Pi Camera / USB camera detection."""

import logging
import threading
import time
from collections import deque

import cv2
import numpy as np

import config

logger = logging.getLogger(__name__)


class CameraStream:
    """
    Unified camera interface that auto-detects Pi Camera vs USB camera.

    Set CAMERA_TYPE in .env:
        - "auto"   : try Pi Camera first, fall back to USB (default)
        - "picamera": force Pi Camera (picamera2)
        - "usb"    : force USB camera (OpenCV)
    """

    def __init__(self):
        self.camera_type = None  # set during start()
        self._backend = None
        self.error = None  # None = OK, string = error message
        self.rotation = config.CAMERA_ROTATION
        # Shared interface for the recorder
        self.circular_output = None  # only set for picamera backend
        self.frame_buffer = None    # only set for USB backend

    @property
    def is_available(self) -> bool:
        """True if the camera is started and working."""
        return self.camera_type is not None and self.error is None

    def start(self) -> bool:
        """Detect camera type and start the appropriate backend.

        Returns True if camera started successfully, False otherwise.
        On failure, sets self.error with the reason.
        """
        self.error = None
        requested = config.CAMERA_TYPE.lower()

        try:
            if requested == "picamera":
                self._start_picamera()
            elif requested == "usb":
                self._start_usb()
            else:
                # Auto-detect: try picamera first
                try:
                    self._start_picamera()
                except Exception as e:
                    logger.info("Pi Camera not available (%s), trying USB camera...", e)
                    self._start_usb()
            return True
        except Exception as e:
            self.error = str(e)
            logger.error("Camera failed to start: %s", self.error)
            return False

    def retry(self) -> bool:
        """Attempt to restart the camera after a failure."""
        logger.info("Retrying camera connection...")
        return self.start()

    def _start_picamera(self):
        """Start using picamera2 (Pi Camera Module)."""
        from picamera2 import Picamera2
        from picamera2.encoders import H264Encoder
        from picamera2.outputs import CircularOutput

        picam2 = Picamera2()
        video_config = picam2.create_video_configuration(
            main={"size": (config.VIDEO_WIDTH, config.VIDEO_HEIGHT), "format": "RGB888"},
            lores={"size": (config.LORES_WIDTH, config.LORES_HEIGHT), "format": "YUV420"},
            controls={"FrameDurationLimits": (
                1_000_000 // config.VIDEO_FPS,
                1_000_000 // config.VIDEO_FPS,
            )},
        )
        picam2.configure(video_config)

        encoder = H264Encoder(bitrate=config.VIDEO_BITRATE)
        circ = CircularOutput(buffersize=config.CIRCULAR_BUFFER_SIZE)

        picam2.start()
        picam2.start_encoder(encoder, circ)

        self._backend = picam2
        self._encoder = encoder
        self.circular_output = circ
        self.camera_type = "picamera"

        logger.info(
            "Pi Camera started: main=%dx%d@%dfps, lores=%dx%d, buffer=%d frames",
            config.VIDEO_WIDTH, config.VIDEO_HEIGHT, config.VIDEO_FPS,
            config.LORES_WIDTH, config.LORES_HEIGHT, config.CIRCULAR_BUFFER_SIZE,
        )

    def _start_usb(self):
        """Start using OpenCV VideoCapture (USB camera)."""
        cap = cv2.VideoCapture(config.USB_CAMERA_INDEX)
        if not cap.isOpened():
            raise RuntimeError(f"Cannot open USB camera at index {config.USB_CAMERA_INDEX}")

        cap.set(cv2.CAP_PROP_FRAME_WIDTH, config.VIDEO_WIDTH)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, config.VIDEO_HEIGHT)
        cap.set(cv2.CAP_PROP_FPS, config.VIDEO_FPS)

        actual_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        actual_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        actual_fps = cap.get(cv2.CAP_PROP_FPS)

        self._backend = cap
        self.camera_type = "usb"

        # Rolling frame buffer for pre-detection capture
        # Uses JPEG compression to save memory (~10MB vs ~221MB raw)
        buffer_seconds = config.CLIP_PRE_SECONDS
        buffer_size = int(config.VIDEO_FPS * buffer_seconds)
        self.frame_buffer = FrameBuffer(maxlen=buffer_size)

        # Start background capture thread
        self._usb_running = True
        self._usb_latest_frame = None
        self._usb_lock = threading.Lock()
        self._usb_thread = threading.Thread(target=self._usb_capture_loop, daemon=True)
        self._usb_thread.start()

        logger.info(
            "USB Camera started: %dx%d@%.0ffps (requested %dx%d@%d), buffer=%d frames (JPEG compressed)",
            actual_w, actual_h, actual_fps,
            config.VIDEO_WIDTH, config.VIDEO_HEIGHT, config.VIDEO_FPS,
            buffer_size,
        )

    _ROTATION_MAP = {
        90: cv2.ROTATE_90_CLOCKWISE,
        180: cv2.ROTATE_180,
        270: cv2.ROTATE_90_COUNTERCLOCKWISE,
    }

    def _apply_rotation(self, frame):
        """Rotate frame based on current rotation setting."""
        cv2_code = self._ROTATION_MAP.get(self.rotation)
        if cv2_code is not None:
            return cv2.rotate(frame, cv2_code)
        return frame

    def _usb_capture_loop(self):
        """Background thread: continuously grab frames from USB camera."""
        interval = 1.0 / config.VIDEO_FPS
        _fail_count = 0
        _backoff_delays = [0.1, 1.0, 5.0, 10.0, 30.0]  # seconds between retries

        while self._usb_running:
            ret, frame = self._backend.read()
            if not ret:
                _fail_count += 1
                if _fail_count == 5:
                    logger.warning("USB camera: repeated read failures — camera may be disconnected")
                    self.error = "USB camera disconnected"
                delay = _backoff_delays[min(_fail_count - 1, len(_backoff_delays) - 1)]
                time.sleep(delay)
                continue

            if _fail_count > 0:
                logger.info("USB camera: read recovered after %d failures", _fail_count)
                self.error = None
                _fail_count = 0

            frame = self._apply_rotation(frame)

            with self._usb_lock:
                self._usb_latest_frame = frame

            # Add to rolling buffer for recording
            self.frame_buffer.add(frame)
            time.sleep(interval)

    def get_full_res_frame(self) -> np.ndarray | None:
        """Return a copy of the latest full-resolution frame, or None.

        Thread-safe — callers should not access _usb_lock/_usb_latest_frame directly.
        For picamera, captures from the 'main' stream.
        """
        if self.camera_type == "picamera":
            try:
                frame = self._backend.capture_array("main")
                return cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
            except Exception:
                return None
        elif self.camera_type == "usb":
            with self._usb_lock:
                if self._usb_latest_frame is not None:
                    return self._usb_latest_frame.copy()
            return None
        return None

    def capture_lores_array(self) -> np.ndarray:
        """Capture a low-resolution frame for detection."""
        if self.camera_type == "picamera":
            return self._backend.capture_array("lores")
        else:
            with self._usb_lock:
                frame = self._usb_latest_frame

            if frame is None:
                return np.zeros((config.LORES_HEIGHT, config.LORES_WIDTH, 3), dtype=np.uint8)

            # Resize to lores dimensions for detection
            return cv2.resize(frame, (config.LORES_WIDTH, config.LORES_HEIGHT))

    def capture_snapshot(self, output_path) -> bool:
        """Save the current camera frame as a JPEG file.

        Returns True on success, False if camera unavailable or frame is None.
        """
        try:
            if not self.is_available:
                logger.warning("Cannot capture snapshot — camera not available")
                return False

            frame = self.get_full_res_frame()

            if self.camera_type == "picamera" and frame is not None:
                frame = self._apply_rotation(frame)

            if frame is None:
                logger.warning("Cannot capture snapshot — no frame available")
                return False

            cv2.imwrite(str(output_path), frame, [cv2.IMWRITE_JPEG_QUALITY, 90])
            logger.info("Snapshot saved: %s", output_path)
            return True
        except Exception:
            logger.exception("Snapshot capture failed")
            return False

    def stop(self):
        """Stop the camera."""
        if self.camera_type is None or self._backend is None:
            return
        if self.camera_type == "picamera":
            try:
                self._backend.stop_encoder()
            except Exception:
                pass
            try:
                self._backend.stop()
            except Exception:
                pass
        elif self.camera_type == "usb":
            self._usb_running = False
            if self._usb_thread.is_alive():
                self._usb_thread.join(timeout=10)
            self._backend.release()

        logger.info("Camera stopped (%s)", self.camera_type)


class FrameBuffer:
    """Thread-safe rolling buffer storing JPEG-compressed frames to save RAM.

    Raw frames at 1920x1080 BGR = ~6.2MB each — uncompressed buffers would be huge.
    JPEG compressed at quality 80 = ~150-250KB each, keeping memory usage manageable.
    """

    def __init__(self, maxlen: int):
        self._buffer = deque(maxlen=maxlen)
        self._lock = threading.Lock()

    def add(self, frame: np.ndarray):
        """Compress frame to JPEG and add to buffer."""
        _, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
        with self._lock:
            self._buffer.append(jpeg)

    def get_all(self) -> list[np.ndarray]:
        """Decompress and return all buffered frames as BGR numpy arrays."""
        with self._lock:
            jpegs = list(self._buffer)

        return [cv2.imdecode(j, cv2.IMREAD_COLOR) for j in jpegs]

    def get_all_compressed(self) -> list[np.ndarray]:
        """Return all buffered frames as raw JPEG numpy arrays (no decompression).

        Use this instead of get_all() when writing to video — decompress
        one frame at a time to avoid ~110MB memory spike on Pi 3B+ (1GB RAM).
        """
        with self._lock:
            return list(self._buffer)

    def clear(self):
        with self._lock:
            self._buffer.clear()
