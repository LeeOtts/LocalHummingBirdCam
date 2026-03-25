"""Custom binary classifier trained on user-labeled frames.

Stacks a lightweight scikit-learn LogisticRegression on top of the frozen
MobileNetV2 bird classifier.  The base model's 965-class output scores
become features; the custom model learns a binary hummingbird / not-hummingbird
decision tuned to the user's specific camera and environment.

The custom classifier can only *suppress* false positives — it is never
consulted when the base model already rejected a frame.  If no custom model
exists, detection behaviour is identical to the default pipeline.
"""

import json
import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np

import config

logger = logging.getLogger(__name__)

# ── Module-level cache ────────────────────────────────────────────────
_custom_clf = None
_clf_lock = threading.Lock()

_CLF_PATH = config.MODEL_DIR / "custom_classifier.pkl"
_CLF_PREV_PATH = config.MODEL_DIR / "custom_classifier.prev.pkl"
_CLF_META_PATH = config.MODEL_DIR / "custom_classifier_meta.json"


# ── Feature extraction ────────────────────────────────────────────────

def extract_features(frame: np.ndarray) -> np.ndarray | None:
    """Run *frame* through the base MobileNetV2 and return the 965-dim score vector.

    Returns ``None`` on any failure (model not loaded, bad frame, etc.).
    """
    from detection.vision_verify import (
        _load_model,
        _interpreter,
        _input_details,
        _output_details,
        _model_lock,
    )

    _load_model()

    if _interpreter is None:
        return None

    try:
        with _model_lock:
            input_details = _input_details
            output_details = _output_details

            input_shape = input_details[0]["shape"]
            height, width = int(input_shape[1]), int(input_shape[2])
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

        # Dequantize
        if output.dtype == np.uint8:
            quant = output_details[0].get("quantization", (1.0, 0))
            scale, zero_point = quant[0], quant[1]
            if isinstance(scale, (list, tuple, np.ndarray)):
                scale = float(scale[0])
            if isinstance(zero_point, (list, tuple, np.ndarray)):
                zero_point = int(zero_point[0])
            scores = (output.astype(np.float32) - zero_point) * scale
        else:
            scores = output.astype(np.float32)

        return scores

    except Exception:
        logger.exception("Feature extraction failed")
        return None


# ── Training ──────────────────────────────────────────────────────────

def retrain() -> dict:
    """Train a binary classifier on the labelled frames in *TRAINING_DIR*.

    Returns a dict with ``ok`` (bool) and either error info or training stats.
    """
    from sklearn.linear_model import LogisticRegression
    import joblib

    hb_dir = config.TRAINING_DIR / "hummingbird"
    not_hb_dir = config.TRAINING_DIR / "not_hummingbird"

    hb_images = sorted(hb_dir.glob("*.jpg")) if hb_dir.exists() else []
    not_hb_images = sorted(not_hb_dir.glob("*.jpg")) if not_hb_dir.exists() else []

    n_hb = len(hb_images)
    n_not = len(not_hb_images)
    total = n_hb + n_not

    if total < config.CUSTOM_CLF_MIN_SAMPLES:
        return {
            "ok": False,
            "error": f"Need at least {config.CUSTOM_CLF_MIN_SAMPLES} samples "
                     f"(have {total})",
        }
    if n_hb < config.CUSTOM_CLF_MIN_PER_CLASS:
        return {
            "ok": False,
            "error": f"Need at least {config.CUSTOM_CLF_MIN_PER_CLASS} hummingbird "
                     f"samples (have {n_hb})",
        }
    if n_not < config.CUSTOM_CLF_MIN_PER_CLASS:
        return {
            "ok": False,
            "error": f"Need at least {config.CUSTOM_CLF_MIN_PER_CLASS} not-hummingbird "
                     f"samples (have {n_not})",
        }

    logger.info(
        "Retraining custom classifier: %d hummingbird, %d not-hummingbird",
        n_hb, n_not,
    )
    start = time.time()

    # Extract features for all labelled images
    X, y = [], []
    skipped = 0

    for img_path, label in [
        *[(p, 1) for p in hb_images],
        *[(p, 0) for p in not_hb_images],
    ]:
        frame = cv2.imread(str(img_path))
        if frame is None:
            logger.warning("Could not read %s — skipping", img_path.name)
            skipped += 1
            continue

        features = extract_features(frame)
        if features is None:
            logger.warning("Feature extraction failed for %s — skipping", img_path.name)
            skipped += 1
            continue

        X.append(features)
        y.append(label)

    if len(X) < config.CUSTOM_CLF_MIN_SAMPLES:
        return {
            "ok": False,
            "error": f"Only {len(X)} images produced valid features "
                     f"(need {config.CUSTOM_CLF_MIN_SAMPLES})",
        }

    X_arr = np.array(X)
    y_arr = np.array(y)

    # Train
    clf = LogisticRegression(C=1.0, max_iter=200, solver="lbfgs")
    clf.fit(X_arr, y_arr)
    train_accuracy = float(clf.score(X_arr, y_arr))

    elapsed = time.time() - start
    logger.info(
        "Custom classifier trained: accuracy=%.1f%%, %d samples, %.1fs",
        train_accuracy * 100, len(X), elapsed,
    )

    # Backup previous model
    config.MODEL_DIR.mkdir(parents=True, exist_ok=True)
    if _CLF_PATH.exists():
        _CLF_PREV_PATH.unlink(missing_ok=True)
        _CLF_PATH.rename(_CLF_PREV_PATH)

    # Save new model
    joblib.dump(clf, _CLF_PATH)

    # Write metadata
    meta = {
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "n_hummingbird": int(sum(y_arr == 1)),
        "n_not_hummingbird": int(sum(y_arr == 0)),
        "skipped": skipped,
        "train_accuracy": round(train_accuracy, 4),
        "train_seconds": round(elapsed, 1),
    }
    _CLF_META_PATH.write_text(json.dumps(meta, indent=2))

    # Reload into memory
    reload_custom_classifier()

    return {"ok": True, **meta}


# ── Loading / caching ─────────────────────────────────────────────────

def load_custom_classifier():
    """Load the custom classifier from disk (thread-safe, cached)."""
    global _custom_clf

    if _custom_clf is not None:
        return _custom_clf

    with _clf_lock:
        if _custom_clf is not None:
            return _custom_clf

        if not _CLF_PATH.exists():
            return None

        try:
            import joblib
            _custom_clf = joblib.load(_CLF_PATH)
            logger.info("Custom classifier loaded from %s", _CLF_PATH.name)
        except Exception:
            logger.exception("Failed to load custom classifier")
            _custom_clf = None

    return _custom_clf


def reload_custom_classifier():
    """Force a fresh reload of the custom classifier (call after retrain)."""
    global _custom_clf
    with _clf_lock:
        _custom_clf = None
    load_custom_classifier()


# ── Prediction ────────────────────────────────────────────────────────

def predict(scores: np.ndarray) -> tuple[bool, float]:
    """Ask the custom classifier whether *scores* represent a hummingbird.

    Returns ``(is_hummingbird, confidence)``.  If no custom classifier is
    loaded, returns ``(True, 0.0)`` — i.e. "no objection".
    """
    clf = load_custom_classifier()
    if clf is None:
        return True, 0.0

    try:
        proba = clf.predict_proba(scores.reshape(1, -1))[0]
        # Class order: [not_hummingbird, hummingbird] (sklearn sorts labels 0, 1)
        p_not_hb = float(proba[0])
        p_hb = float(proba[1])

        if p_not_hb >= config.CUSTOM_CLF_OVERRIDE_THRESHOLD:
            return False, p_not_hb

        return True, p_hb

    except Exception:
        logger.exception("Custom classifier prediction failed — allowing detection")
        return True, 0.0


# ── Revert ────────────────────────────────────────────────────────────

def revert_classifier() -> bool:
    """Swap the active classifier with the previous backup.

    Returns True on success, False if no backup exists.
    """
    if not _CLF_PREV_PATH.exists():
        return False

    try:
        tmp = _CLF_PATH.with_suffix(".tmp.pkl")

        if _CLF_PATH.exists():
            _CLF_PATH.rename(tmp)

        _CLF_PREV_PATH.rename(_CLF_PATH)

        if tmp.exists():
            tmp.rename(_CLF_PREV_PATH)

        reload_custom_classifier()
        logger.info("Custom classifier reverted to previous version")
        return True

    except Exception:
        logger.exception("Failed to revert custom classifier")
        return False


# ── Metadata helpers ──────────────────────────────────────────────────

def get_classifier_meta() -> dict | None:
    """Read the classifier metadata JSON, or None if it doesn't exist."""
    if not _CLF_META_PATH.exists():
        return None
    try:
        return json.loads(_CLF_META_PATH.read_text())
    except Exception:
        return None


def get_training_counts() -> dict:
    """Count labelled images per class."""
    hb_dir = config.TRAINING_DIR / "hummingbird"
    not_hb_dir = config.TRAINING_DIR / "not_hummingbird"
    return {
        "hummingbird": len(list(hb_dir.glob("*.jpg"))) if hb_dir.exists() else 0,
        "not_hummingbird": len(list(not_hb_dir.glob("*.jpg"))) if not_hb_dir.exists() else 0,
    }
