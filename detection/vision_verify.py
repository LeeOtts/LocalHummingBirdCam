"""Verify hummingbird detection using a local bird species classifier.

Uses the EfficientNetB2 bird species classifier (525 species) from Hugging Face.
Runs entirely on the Pi — no API calls needed for detection.

Known hummingbird classes in the model:
  - ANNAS HUMMINGBIRD
  - RUBY THROATED HUMMINGBIRD
  - LUCIFER HUMMINGBIRD
  - WHITE EARED HUMMINGBIRD
  - HORNED SUNGEM (a hummingbird species)

The model downloads weights on first run (~50MB) and then runs offline.
"""

import logging
import time

import cv2
import numpy as np

import config

logger = logging.getLogger(__name__)

# Lazy-loaded model and processor
_pipe = None
_model_loaded = False

# All hummingbird-related labels in the 525-species dataset (case-insensitive check)
HUMMINGBIRD_KEYWORDS = [
    "HUMMINGBIRD",
    "SUNGEM",       # Horned Sungem is a hummingbird
    "SUNANGEL",
    "WOODSTAR",
    "HERMIT",       # some hermit species are hummingbirds — check context
    "MANGO",        # Green-throated Mango is a hummingbird
    "SYLPH",
    "SAPPHIRE",
    "EMERALD",
    "VIOLETEAR",
    "TOPAZ",
    "JACOBIN",
    "COQUETTE",
]


def _load_model():
    """Load the bird classifier pipeline (first call downloads the model)."""
    global _pipe, _model_loaded

    if _model_loaded:
        return

    try:
        from transformers import pipeline
        logger.info("Loading bird species classifier (first run downloads ~50MB)...")
        _pipe = pipeline(
            "image-classification",
            model="dennisjooo/Birds-Classifier-EfficientNetB2",
            device=-1,  # CPU
        )
        _model_loaded = True
        logger.info("Bird species classifier loaded successfully")
    except Exception:
        logger.exception("Failed to load bird species classifier")
        _model_loaded = True  # Don't retry every frame
        _pipe = None


def _is_hummingbird_label(label: str) -> bool:
    """Check if a classification label is a hummingbird species."""
    upper = label.upper()
    for keyword in HUMMINGBIRD_KEYWORDS:
        if keyword in upper:
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

    if _pipe is None:
        logger.warning("Bird classifier not available — allowing detection as fallback")
        return True

    try:
        from PIL import Image

        start = time.time()

        # Convert BGR to RGB PIL Image
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        image = Image.fromarray(rgb)

        # Classify — get top 5 predictions
        results = _pipe(image, top_k=5)
        elapsed = time.time() - start

        # Check if any top prediction is a hummingbird
        for r in results:
            label = r["label"]
            score = r["score"]

            if _is_hummingbird_label(label):
                logger.info(
                    "Bird classifier confirmed: %s (%.1f%% confidence, %.1fs)",
                    label, score * 100, elapsed,
                )
                return True

        # Log what it thought it saw
        top = results[0]
        logger.info(
            "Bird classifier rejected — top match: %s (%.1f%%), not a hummingbird (%.1fs)",
            top["label"], top["score"] * 100, elapsed,
        )
        return False

    except Exception:
        logger.exception("Bird classifier failed — allowing detection as fallback")
        return True
