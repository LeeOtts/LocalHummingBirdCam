"""Verify hummingbird detection using a lightweight TFLite bird classifier.

Uses a MobileNetV2 bird species classifier converted to TFLite for minimal
memory footprint (~20MB vs ~800MB for PyTorch). Runs entirely on the Pi CPU.

The model is downloaded on first run from Hugging Face and cached locally.
After that it runs fully offline.
"""

import logging
import os
import time
from pathlib import Path

import cv2
import numpy as np

import config

logger = logging.getLogger(__name__)

# Lazy-loaded model
_interpreter = None
_labels = None
_model_loaded = False

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
MIN_CONFIDENCE = 0.15


def _download_model():
    """Download a pre-trained bird classifier TFLite model if not present."""
    MODEL_DIR.mkdir(parents=True, exist_ok=True)

    if MODEL_PATH.exists() and LABELS_PATH.exists():
        return True

    try:
        import urllib.request
        import zipfile
        import tempfile

        # Use the iNaturalist / Google bird classifier (lightweight MobileNet)
        # This is a commonly available bird species classifier
        model_url = "https://tfhub.dev/google/lite-model/aiy/vision/classifier/birds_V1/3?lite-format=tflite"

        logger.info("Downloading bird classifier model (~5MB)...")

        # Download the TFLite model directly
        urllib.request.urlretrieve(
            "https://storage.googleapis.com/tfhub-lite-models/google/lite-model/aiy/vision/classifier/birds_V1/3.tflite",
            str(MODEL_PATH),
        )

        # Download labels
        urllib.request.urlretrieve(
            "https://raw.githubusercontent.com/google-coral/test_data/master/inat_bird_labels.txt",
            str(LABELS_PATH),
        )

        logger.info("Bird classifier model downloaded successfully")
        return True

    except Exception:
        logger.exception("Failed to download bird classifier model")
        return False


def _load_model():
    """Load the TFLite bird classifier."""
    global _interpreter, _labels, _model_loaded

    if _model_loaded:
        return

    _model_loaded = True  # Don't retry every frame on failure

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

        _interpreter = Interpreter(model_path=str(MODEL_PATH))
        _interpreter.allocate_tensors()

        # Load labels
        if LABELS_PATH.exists():
            _labels = LABELS_PATH.read_text().strip().split("\n")
            # Clean up label format (some have index prefixes)
            _labels = [l.strip().split(" ", 1)[-1] if " " in l.strip() else l.strip()
                        for l in _labels]
        else:
            _labels = []

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

        # Get model input details
        input_details = _interpreter.get_input_details()
        output_details = _interpreter.get_output_details()

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
            scale, zero_point = output_details[0].get("quantization", (1.0, 0))
            if isinstance(scale, (list, tuple)):
                scale = scale[0]
                zero_point = zero_point[0]
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
