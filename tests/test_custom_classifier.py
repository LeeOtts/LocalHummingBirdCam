"""Tests for detection/custom_classifier.py — lightweight retraining pipeline."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_fake_images(tmp_path, n_hb=5, n_not=5):
    """Create tiny JPEG files in training subdirectories."""
    import cv2

    hb_dir = tmp_path / "hummingbird"
    hb_dir.mkdir()
    not_dir = tmp_path / "not_hummingbird"
    not_dir.mkdir()

    for i in range(n_hb):
        img = np.random.randint(0, 255, (224, 224, 3), dtype=np.uint8)
        cv2.imwrite(str(hb_dir / f"hb_{i:03d}.jpg"), img)

    for i in range(n_not):
        img = np.random.randint(0, 255, (224, 224, 3), dtype=np.uint8)
        cv2.imwrite(str(not_dir / f"not_{i:03d}.jpg"), img)

    return hb_dir, not_dir


def _random_scores(label: int, rng=None):
    """Return a 965-dim vector that's distinguishable by label."""
    if rng is None:
        rng = np.random.default_rng(42)
    vec = rng.random(965).astype(np.float32) * 0.1  # Low baseline
    # Make the classes clearly separable across multiple features
    if label == 1:
        vec[0:10] = 0.9   # Hummingbird: high scores at indices 0-9
        vec[10:20] = 0.01
    else:
        vec[0:10] = 0.01  # Not hummingbird: low scores at indices 0-9
        vec[10:20] = 0.9
    return vec


# ---------------------------------------------------------------------------
# retrain
# ---------------------------------------------------------------------------

class TestRetrain:
    """Test the retrain() function."""

    def test_rejects_insufficient_total_samples(self, tmp_path, monkeypatch):
        import config
        monkeypatch.setattr(config, "TRAINING_DIR", tmp_path)
        monkeypatch.setattr(config, "MODEL_DIR", tmp_path)
        _make_fake_images(tmp_path, n_hb=3, n_not=3)  # 6 total < 10

        from detection.custom_classifier import retrain
        result = retrain()
        assert result["ok"] is False
        assert "10" in result["error"]

    def test_rejects_insufficient_per_class(self, tmp_path, monkeypatch):
        import config
        monkeypatch.setattr(config, "TRAINING_DIR", tmp_path)
        monkeypatch.setattr(config, "MODEL_DIR", tmp_path)
        _make_fake_images(tmp_path, n_hb=9, n_not=1)  # not enough not_hb

        from detection.custom_classifier import retrain
        result = retrain()
        assert result["ok"] is False
        assert "not-hummingbird" in result["error"]

    def test_retrain_succeeds(self, tmp_path, monkeypatch):
        import config
        import detection.custom_classifier as cc
        monkeypatch.setattr(config, "TRAINING_DIR", tmp_path)
        monkeypatch.setattr(config, "MODEL_DIR", tmp_path)
        monkeypatch.setattr(cc, "_CLF_PATH", tmp_path / "custom_classifier.pkl")
        monkeypatch.setattr(cc, "_CLF_PREV_PATH", tmp_path / "custom_classifier.prev.pkl")
        monkeypatch.setattr(cc, "_CLF_META_PATH", tmp_path / "custom_classifier_meta.json")
        monkeypatch.setattr(cc, "_custom_clf", None)
        _make_fake_images(tmp_path, n_hb=6, n_not=6)

        rng = np.random.default_rng(99)
        call_count = {"n": 0}

        def mock_extract(frame):
            label = 1 if call_count["n"] < 6 else 0
            call_count["n"] += 1
            return _random_scores(label, rng)

        with patch("detection.custom_classifier.extract_features", side_effect=mock_extract):
            result = cc.retrain()

        assert result["ok"] is True
        assert "train_accuracy" in result
        assert result["n_hummingbird"] == 6
        assert result["n_not_hummingbird"] == 6
        assert (tmp_path / "custom_classifier.pkl").exists()
        assert (tmp_path / "custom_classifier_meta.json").exists()

    def test_backup_preserved(self, tmp_path, monkeypatch):
        import config
        import detection.custom_classifier as cc
        monkeypatch.setattr(config, "TRAINING_DIR", tmp_path)
        monkeypatch.setattr(config, "MODEL_DIR", tmp_path)
        monkeypatch.setattr(cc, "_CLF_PATH", tmp_path / "custom_classifier.pkl")
        monkeypatch.setattr(cc, "_CLF_PREV_PATH", tmp_path / "custom_classifier.prev.pkl")
        monkeypatch.setattr(cc, "_CLF_META_PATH", tmp_path / "custom_classifier_meta.json")
        monkeypatch.setattr(cc, "_custom_clf", None)
        _make_fake_images(tmp_path, n_hb=6, n_not=6)

        call_count = {"n": 0}

        def mock_extract(frame):
            label = 1 if call_count["n"] % 12 < 6 else 0
            call_count["n"] += 1
            return _random_scores(label)

        with patch("detection.custom_classifier.extract_features", side_effect=mock_extract):
            cc.retrain()
            first_model = (tmp_path / "custom_classifier.pkl").read_bytes()

            # Retrain again
            cc.retrain()

        assert (tmp_path / "custom_classifier.prev.pkl").exists()
        prev_bytes = (tmp_path / "custom_classifier.prev.pkl").read_bytes()
        assert prev_bytes == first_model


# ---------------------------------------------------------------------------
# predict
# ---------------------------------------------------------------------------

class TestPredict:
    """Test the predict() function."""

    def test_returns_true_when_no_classifier(self, monkeypatch):
        import detection.custom_classifier as cc
        monkeypatch.setattr(cc, "_custom_clf", None)
        monkeypatch.setattr(cc, "_CLF_PATH", Path("/nonexistent/nope.pkl"))

        is_hb, conf = cc.predict(np.zeros(965, dtype=np.float32))
        assert is_hb is True
        assert conf == 0.0

    def test_suppresses_false_positive(self, monkeypatch):
        """A trained classifier should reject a not-hummingbird vector."""
        from sklearn.linear_model import LogisticRegression
        import detection.custom_classifier as cc

        rng = np.random.default_rng(42)
        X = np.array([_random_scores(1, rng) for _ in range(20)] +
                     [_random_scores(0, rng) for _ in range(20)])
        y = np.array([1] * 20 + [0] * 20)

        clf = LogisticRegression(C=1.0, max_iter=200, solver="lbfgs")
        clf.fit(X, y)

        monkeypatch.setattr(cc, "_custom_clf", clf)

        # Test with a clearly not-hummingbird vector
        not_hb_vec = _random_scores(0, rng)
        is_hb, conf = cc.predict(not_hb_vec)
        assert is_hb is False
        assert conf >= 0.70

    def test_allows_hummingbird(self, monkeypatch):
        """A trained classifier should allow a hummingbird vector."""
        from sklearn.linear_model import LogisticRegression
        import detection.custom_classifier as cc

        rng = np.random.default_rng(42)
        X = np.array([_random_scores(1, rng) for _ in range(20)] +
                     [_random_scores(0, rng) for _ in range(20)])
        y = np.array([1] * 20 + [0] * 20)

        clf = LogisticRegression(C=1.0, max_iter=200, solver="lbfgs")
        clf.fit(X, y)

        monkeypatch.setattr(cc, "_custom_clf", clf)

        hb_vec = _random_scores(1, rng)
        is_hb, conf = cc.predict(hb_vec)
        assert is_hb is True


# ---------------------------------------------------------------------------
# revert
# ---------------------------------------------------------------------------

class TestRevert:
    """Test the revert_classifier() function."""

    def test_revert_returns_false_when_no_backup(self, tmp_path, monkeypatch):
        import detection.custom_classifier as cc
        monkeypatch.setattr(cc, "_CLF_PATH", tmp_path / "clf.pkl")
        monkeypatch.setattr(cc, "_CLF_PREV_PATH", tmp_path / "clf.prev.pkl")

        assert cc.revert_classifier() is False

    def test_revert_swaps_models(self, tmp_path, monkeypatch):
        import detection.custom_classifier as cc
        clf_path = tmp_path / "clf.pkl"
        prev_path = tmp_path / "clf.prev.pkl"
        clf_path.write_text("model_v2")
        prev_path.write_text("model_v1")

        monkeypatch.setattr(cc, "_CLF_PATH", clf_path)
        monkeypatch.setattr(cc, "_CLF_PREV_PATH", prev_path)
        monkeypatch.setattr(cc, "_custom_clf", None)
        # Mock reload so it doesn't try to joblib.load our text files
        monkeypatch.setattr(cc, "reload_custom_classifier", lambda: None)

        result = cc.revert_classifier()
        assert result is True
        assert clf_path.read_text() == "model_v1"
        assert prev_path.read_text() == "model_v2"


# ---------------------------------------------------------------------------
# get_training_counts
# ---------------------------------------------------------------------------

class TestGetTrainingCounts:
    """Test the get_training_counts() helper."""

    def test_counts_empty(self, tmp_path, monkeypatch):
        import config
        monkeypatch.setattr(config, "TRAINING_DIR", tmp_path)

        from detection.custom_classifier import get_training_counts
        counts = get_training_counts()
        assert counts["hummingbird"] == 0
        assert counts["not_hummingbird"] == 0

    def test_counts_with_images(self, tmp_path, monkeypatch):
        import config
        monkeypatch.setattr(config, "TRAINING_DIR", tmp_path)
        _make_fake_images(tmp_path, n_hb=3, n_not=7)

        from detection.custom_classifier import get_training_counts
        counts = get_training_counts()
        assert counts["hummingbird"] == 3
        assert counts["not_hummingbird"] == 7
