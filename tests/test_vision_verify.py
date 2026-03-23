"""Tests for detection/vision_verify.py — label matching and inference logic.

Mocks TFLite interpreter to avoid needing a real model or GPU.
"""

from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from detection.vision_verify import _is_hummingbird_label


class TestIsHummingbirdLabel:
    """Test _is_hummingbird_label() with various label strings."""

    @pytest.mark.parametrize("label", [
        "Ruby-throated Hummingbird",
        "ruby throated hummingbird",
        "RUBY-THROATED HUMMINGBIRD",
        "Hummingbird",
        "hummingbird",
        "Anna's Hummingbird",
        "Some hummingbird species",
    ])
    def test_hummingbird_labels_return_true(self, label):
        """Labels containing 'hummingbird' (case-insensitive) return True."""
        assert _is_hummingbird_label(label) is True

    @pytest.mark.parametrize("label", [
        "American Robin",
        "Blue Jay",
        "Red-tailed Hawk",
        "Cardinal",
        "Sparrow",
        "background",
        "",
        "humming",  # partial match doesn't count — needs "hummingbird"
    ])
    def test_non_hummingbird_labels_return_false(self, label):
        """Labels not containing 'hummingbird' return False."""
        assert _is_hummingbird_label(label) is False


class TestVerifyHummingbirdFallback:
    """Test verify_hummingbird() graceful fallback when model is unavailable."""

    def test_returns_true_when_model_not_loaded(self):
        """When interpreter is None, verify returns True as fallback."""
        import detection.vision_verify as vv

        # Save original state
        orig_interpreter = vv._interpreter
        orig_labels = vv._labels
        orig_attempted = vv._load_attempted

        try:
            # Simulate model not loaded
            vv._interpreter = None
            vv._labels = None
            vv._load_attempted = True  # prevent download attempt

            frame = np.zeros((480, 640, 3), dtype=np.uint8)
            result = vv.verify_hummingbird(frame)
            assert result is True
        finally:
            # Restore original state
            vv._interpreter = orig_interpreter
            vv._labels = orig_labels
            vv._load_attempted = orig_attempted

    def test_returns_true_when_vision_verify_disabled(self, monkeypatch):
        """When VISION_VERIFY_ENABLED is False, always returns True."""
        import config
        monkeypatch.setattr(config, "VISION_VERIFY_ENABLED", False)

        import detection.vision_verify as vv
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        result = vv.verify_hummingbird(frame)
        assert result is True


class TestVerifyHummingbirdInference:
    """Test inference pipeline with a mocked TFLite interpreter."""

    def _setup_mock_interpreter(self, vv, top_label_index=0, top_score=200):
        """Configure a mock interpreter on the vision_verify module."""
        mock_interp = MagicMock()

        # Input details: expect 224x224x3 uint8
        mock_interp.get_input_details.return_value = [{
            "shape": np.array([1, 224, 224, 3]),
            "dtype": np.uint8,
            "index": 0,
        }]

        # Output details: quantized uint8 output
        mock_interp.get_output_details.return_value = [{
            "index": 0,
            "quantization": (1.0 / 255.0, 0),
        }]

        # Build a fake output tensor — 10 labels, top_label_index gets highest score
        output = np.zeros(10, dtype=np.uint8)
        output[top_label_index] = top_score
        mock_interp.get_tensor.return_value = np.array([output])

        vv._interpreter = mock_interp
        vv._input_details = mock_interp.get_input_details()
        vv._output_details = mock_interp.get_output_details()
        vv._load_attempted = True

        return mock_interp

    def test_confirms_hummingbird_detection(self, monkeypatch):
        """When model predicts a hummingbird label with high confidence, returns True."""
        import config
        import detection.vision_verify as vv

        monkeypatch.setattr(config, "VISION_VERIFY_ENABLED", True)

        orig_interpreter = vv._interpreter
        orig_labels = vv._labels
        orig_attempted = vv._load_attempted
        orig_input = vv._input_details
        orig_output = vv._output_details

        try:
            vv._labels = [
                "background",
                "American Robin",
                "Ruby-throated Hummingbird",
                "Blue Jay",
                "Cardinal",
                "Sparrow",
                "Hawk",
                "Eagle",
                "Finch",
                "Wren",
            ]

            self._setup_mock_interpreter(vv, top_label_index=2, top_score=200)

            frame = np.zeros((480, 640, 3), dtype=np.uint8)
            result = vv.verify_hummingbird(frame)
            assert result is True

        finally:
            vv._interpreter = orig_interpreter
            vv._labels = orig_labels
            vv._load_attempted = orig_attempted
            vv._input_details = orig_input
            vv._output_details = orig_output

    def test_rejects_non_hummingbird(self, monkeypatch):
        """When model predicts a non-hummingbird label, returns False."""
        import config
        import detection.vision_verify as vv

        monkeypatch.setattr(config, "VISION_VERIFY_ENABLED", True)

        orig_interpreter = vv._interpreter
        orig_labels = vv._labels
        orig_attempted = vv._load_attempted
        orig_input = vv._input_details
        orig_output = vv._output_details

        try:
            vv._labels = [
                "background",
                "American Robin",
                "Ruby-throated Hummingbird",
                "Blue Jay",
                "Cardinal",
                "Sparrow",
                "Hawk",
                "Eagle",
                "Finch",
                "Wren",
            ]

            # Top prediction is "American Robin" (index 1)
            self._setup_mock_interpreter(vv, top_label_index=1, top_score=200)

            frame = np.zeros((480, 640, 3), dtype=np.uint8)
            result = vv.verify_hummingbird(frame)
            assert result is False

        finally:
            vv._interpreter = orig_interpreter
            vv._labels = orig_labels
            vv._load_attempted = orig_attempted
            vv._input_details = orig_input
            vv._output_details = orig_output

    def test_inference_exception_returns_true(self, monkeypatch):
        """If inference raises an exception, returns True as fallback."""
        import config
        import detection.vision_verify as vv

        monkeypatch.setattr(config, "VISION_VERIFY_ENABLED", True)

        orig_interpreter = vv._interpreter
        orig_labels = vv._labels
        orig_attempted = vv._load_attempted
        orig_input = vv._input_details
        orig_output = vv._output_details

        try:
            vv._labels = ["background", "hummingbird"]
            mock_interp = MagicMock()
            mock_interp.get_input_details.return_value = [{
                "shape": np.array([1, 224, 224, 3]),
                "dtype": np.uint8,
                "index": 0,
            }]
            mock_interp.get_output_details.return_value = [{"index": 0}]
            mock_interp.invoke.side_effect = RuntimeError("Inference failed")

            vv._interpreter = mock_interp
            vv._input_details = mock_interp.get_input_details()
            vv._output_details = mock_interp.get_output_details()
            vv._load_attempted = True

            frame = np.zeros((480, 640, 3), dtype=np.uint8)
            result = vv.verify_hummingbird(frame)
            assert result is True  # fallback

        finally:
            vv._interpreter = orig_interpreter
            vv._labels = orig_labels
            vv._load_attempted = orig_attempted
            vv._input_details = orig_input
            vv._output_details = orig_output
