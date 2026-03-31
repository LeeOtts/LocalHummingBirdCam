"""Verify hummingbird detection using a lightweight TFLite bird classifier.

Uses a MobileNetV2 bird species classifier converted to TFLite for minimal
memory footprint (~20MB vs ~800MB for PyTorch). Runs entirely on the Pi CPU.

The model is downloaded on first run from Hugging Face and cached locally.
After that it runs fully offline.
"""

import hashlib
import logging
import os
import threading
import time
from pathlib import Path

import cv2
import numpy as np

import config

logger = logging.getLogger(__name__)

# Lazy-loaded model (protected by _model_lock for thread safety)
_interpreter = None
_labels = None
_load_attempted = False  # Prevents retrying every frame
_input_details = None    # Cached after first load
_output_details = None   # Cached after first load
_model_lock = threading.Lock()

MODEL_DIR = config.BASE_DIR / "models"
MODEL_PATH = MODEL_DIR / "bird_classifier.tflite"
LABELS_PATH = MODEL_DIR / "bird_labels.txt"

# Only Ruby-throated Hummingbirds in Bartlett, TN
HUMMINGBIRD_KEYWORDS = [
    "ruby throated hummingbird",
    "ruby-throated hummingbird",
    "hummingbird",
]

# Minimum confidence to accept a hummingbird classification
MIN_CONFIDENCE = 0.25

# SHA-256 checksum for integrity verification (empty string = skip check)
_MODEL_SHA256 = ""  # Populated on first successful download if desired

_MODEL_URL = "https://raw.githubusercontent.com/google-coral/test_data/master/mobilenet_v2_1.0_224_inat_bird_quant.tflite"
_LABELS_URL = "https://raw.githubusercontent.com/google-coral/test_data/master/inat_bird_labels.txt"
_DOWNLOAD_TIMEOUT = 30
_DOWNLOAD_RETRIES = 2


def _download_file(url: str, dest: Path, checksum: str = "") -> bool:
    """Download a file with retry logic and optional SHA-256 verification."""
    import urllib.request
    import urllib.error

    tmp = dest.with_suffix(".tmp")
    for attempt in range(1, _DOWNLOAD_RETRIES + 1):
        try:
            logger.info("Downloading %s (attempt %d/%d)...", dest.name, attempt, _DOWNLOAD_RETRIES)
            urllib.request.urlretrieve(url, str(tmp))

            # Verify checksum if provided
            if checksum:
                sha = hashlib.sha256(tmp.read_bytes()).hexdigest()
                if sha != checksum:
                    logger.error("Checksum mismatch for %s: expected %s, got %s", dest.name, checksum, sha)
                    tmp.unlink(missing_ok=True)
                    continue

            tmp.replace(dest)
            return True
        except (urllib.error.URLError, OSError) as e:
            logger.warning("Download failed for %s: %s", dest.name, e)
            tmp.unlink(missing_ok=True)
            if attempt < _DOWNLOAD_RETRIES:
                time.sleep(2 * attempt)  # Simple backoff

    return False


def _download_model():
    """Download a pre-trained bird classifier TFLite model if not present."""
    MODEL_DIR.mkdir(parents=True, exist_ok=True)

    if MODEL_PATH.exists() and LABELS_PATH.exists():
        return True

    if not _download_file(_MODEL_URL, MODEL_PATH, _MODEL_SHA256):
        logger.error("Failed to download bird classifier model after %d attempts", _DOWNLOAD_RETRIES)
        return False

    if not _download_file(_LABELS_URL, LABELS_PATH):
        logger.error("Failed to download bird labels after %d attempts", _DOWNLOAD_RETRIES)
        return False

    logger.info("Bird classifier model downloaded successfully")
    return True


def _load_model():
    """Load the TFLite bird classifier (thread-safe)."""
    global _interpreter, _labels, _load_attempted, _input_details, _output_details

    if _load_attempted:
        return

    with _model_lock:
        if _load_attempted:
            return
        _load_attempted = True  # Don't retry every frame

        if not _download_model():
            logger.error("Bird classifier model not available")
            return

        try:
            # Try ai_edge_litert first (Python 3.13+), then tflite_runtime, then full tf
            try:
                from ai_edge_litert.interpreter import Interpreter
            except ImportError:
                try:
                    from tflite_runtime.interpreter import Interpreter
                except ImportError:
                    from tensorflow.lite.python.interpreter import Interpreter

            interp = Interpreter(model_path=str(MODEL_PATH))
            interp.allocate_tensors()

            # Cache input/output details (don't change between inferences)
            _input_details = interp.get_input_details()
            _output_details = interp.get_output_details()

            # Load labels
            labels = []
            if LABELS_PATH.exists():
                labels = LABELS_PATH.read_text().strip().split("\n")
                labels = [l.strip().split(" ", 1)[-1] if " " in l.strip() else l.strip()
                          for l in labels]

            # Only set globals after everything succeeds
            _interpreter = interp
            _labels = labels

            logger.info(
                "Bird classifier loaded (TFLite, %d labels, %.1f MB)",
                len(_labels),
                os.path.getsize(MODEL_PATH) / 1_048_576,
            )

        except Exception:
            logger.exception("Failed to load bird classifier")
            _interpreter = None


def _is_hummingbird_label(label: str) -> bool:
    """Check if a classification label is a hummingbird species."""
    lower = label.lower()
    for keyword in HUMMINGBIRD_KEYWORDS:
        if keyword in lower:
            return True
    return False


def verify_hummingbird(frame: np.ndarray) -> bool:
    """
    Run the bird species classifier on a frame to confirm it's a hummingbird.

    Args:
        frame: BGR numpy array from the camera

    Returns:
        True if the classifier identifies a hummingbird species
    """
    if not config.VISION_VERIFY_ENABLED:
        return True  # Skip verification if disabled

    _load_model()

    if _interpreter is None or not _labels:
        logger.warning("Bird classifier not available — allowing detection as fallback")
        return True

    try:
        start = time.time()

        # Use cached input/output details
        input_details = _input_details
        output_details = _output_details

        # Get expected input shape
        input_shape = input_details[0]["shape"]  # e.g. [1, 224, 224, 3]
        height, width = input_shape[1], input_shape[2]
        input_dtype = input_details[0]["dtype"]

        # Preprocess: resize, convert BGR to RGB
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        resized = cv2.resize(rgb, (width, height))

        # Normalize based on model's expected input type
        if input_dtype == np.uint8:
            input_data = np.expand_dims(resized.astype(np.uint8), axis=0)
        else:
            input_data = np.expand_dims(resized.astype(np.float32) / 255.0, axis=0)

        # Run inference
        _interpreter.set_tensor(input_details[0]["index"], input_data)
        _interpreter.invoke()
        output = _interpreter.get_tensor(output_details[0]["index"])[0]

        # Get top 5 predictions
        if output.dtype == np.uint8:
            # Quantized model — convert to float
            quant = output_details[0].get("quantization", (1.0, 0))
            scale, zero_point = quant[0], quant[1]
            if isinstance(scale, (list, tuple, np.ndarray)):
                scale = float(scale[0])
            if isinstance(zero_point, (list, tuple, np.ndarray)):
                zero_point = int(zero_point[0])
            scores = (output.astype(np.float32) - zero_point) * scale
        else:
            scores = output

        top_indices = scores.argsort()[-5:][::-1]
        elapsed = time.time() - start

        # Check if any top prediction is a hummingbird
        for idx in top_indices:
            if idx < len(_labels):
                label = _labels[idx]
                score = float(scores[idx])

                if _is_hummingbird_label(label) and score >= MIN_CONFIDENCE:
                    # Check custom classifier (false-positive suppressor)
                    try:
                        from detection.custom_classifier import predict as custom_predict
                        is_hb, custom_conf = custom_predict(scores)
                        if not is_hb:
                            logger.info(
                                "Custom classifier overrode: %s (%.1f%%) -> "
                                "NOT hummingbird (custom conf %.1f%%, %.2fs)",
                                label, score * 100, custom_conf * 100, elapsed,
                            )
                            return False
                    except Exception:
                        pass  # No custom classifier or error — allow detection

                    logger.info(
                        "Bird classifier confirmed: %s (%.1f%% confidence, %.2fs)",
                        label, score * 100, elapsed,
                    )
                    return True

        # Log what it thought it saw
        if top_indices[0] < len(_labels):
            top_label = _labels[top_indices[0]]
            top_score = float(scores[top_indices[0]])
            logger.info(
                "Bird classifier rejected — top match: %s (%.1f%%), not a hummingbird (%.2fs)",
                top_label, top_score * 100, elapsed,
            )
        else:
            logger.info("Bird classifier rejected — unknown label (%.2fs)", elapsed)

        return False

    except Exception:
        logger.exception("Bird classifier failed — allowing detection as fallback")
        return True


def identify_species(frame: np.ndarray) -> tuple[str | None, float]:
    """Identify the most likely hummingbird species from a frame.

    Returns (species_name, confidence) or (None, 0.0) if identification fails.
    Called from the post worker thread (not the detection loop).
    """
    _load_model()
    if _interpreter is None or not _labels:
        return None, 0.0

    try:
        input_details = _input_details
        output_details = _output_details
        input_shape = input_details[0]["shape"]
        height, width = input_shape[1], input_shape[2]
        input_dtype = input_details[0]["dtype"]

        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        resized = cv2.resize(rgb, (width, height))

        if input_dtype == np.uint8:
            input_data = np.expand_dims(resized.astype(np.uint8), axis=0)
        else:
            input_data = np.expand_dims(resized.astype(np.float32) / 255.0, axis=0)

        _interpreter.set_tensor(input_details[0]["index"], input_data)
        _interpreter.invoke()
        output = _interpreter.get_tensor(output_details[0]["index"])[0]

        if output.dtype == np.uint8:
            quant = output_details[0].get("quantization", (1.0, 0))
            scale, zero_point = quant[0], quant[1]
            if isinstance(scale, (list, tuple, np.ndarray)):
                scale = float(scale[0])
            if isinstance(zero_point, (list, tuple, np.ndarray)):
                zero_point = int(zero_point[0])
            scores = (output.astype(np.float32) - zero_point) * scale
        else:
            scores = output

        top_indices = scores.argsort()[-5:][::-1]

        # Find best hummingbird match
        for idx in top_indices:
            if idx < len(_labels):
                label = _labels[idx]
                score = float(scores[idx])
                if _is_hummingbird_label(label) and score >= MIN_CONFIDENCE:
                    # Clean up the label for display
                    species_name = label.strip()
                    return species_name, round(score, 3)

        # Return top match even if not hummingbird (for logging)
        if top_indices[0] < len(_labels):
            return _labels[top_indices[0]].strip(), round(float(scores[top_indices[0]]), 3)

        return None, 0.0
    except Exception:
        logger.debug("Species identification failed")
        return None, 0.0
