"""Tests for config.py validation logic.

Since config.py executes validation at import time (module-level code),
we test by manipulating environment variables and reimporting the module.
After each test, the original config module is restored so other test
modules that hold a reference to it (e.g., schedule.py) are not broken.
"""

import importlib
import sys

import pytest


@pytest.fixture(autouse=True)
def _restore_config_module():
    """Ensure the original config module is restored after each test.

    test_config reimports config with different env vars, which replaces
    sys.modules["config"]. Other modules (schedule, detection, etc.) hold
    a reference to the original config module, so we must restore it.
    """
    original_config = sys.modules.get("config")
    yield
    if original_config is not None:
        sys.modules["config"] = original_config


def _reimport_config(monkeypatch, env_overrides: dict):
    """Helper: set env vars, remove cached config module, and reimport it."""
    for key, value in env_overrides.items():
        monkeypatch.setenv(key, str(value))

    # Remove config and dotenv cache so reimport picks up new env vars
    sys.modules.pop("config", None)

    # Prevent load_dotenv from overriding our monkeypatched env vars
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **kw: None)

    import config
    return config


class TestVideoFPSClamping:
    """VIDEO_FPS < 1 should be clamped to 1."""

    def test_fps_zero_clamped_to_one(self, monkeypatch):
        """VIDEO_FPS=0 gets clamped to 1."""
        cfg = _reimport_config(monkeypatch, {"VIDEO_FPS": "0"})
        assert cfg.VIDEO_FPS == 1

    def test_fps_negative_clamped_to_one(self, monkeypatch):
        """VIDEO_FPS=-5 gets clamped to 1."""
        cfg = _reimport_config(monkeypatch, {"VIDEO_FPS": "-5"})
        assert cfg.VIDEO_FPS == 1

    def test_fps_valid_unchanged(self, monkeypatch):
        """VIDEO_FPS=30 stays as 30."""
        cfg = _reimport_config(monkeypatch, {"VIDEO_FPS": "30"})
        assert cfg.VIDEO_FPS == 30


class TestClipPostSecondsClamping:
    """CLIP_POST_SECONDS < 1 should be clamped to 1."""

    def test_clip_post_zero_clamped(self, monkeypatch):
        """CLIP_POST_SECONDS=0 gets clamped to 1."""
        cfg = _reimport_config(monkeypatch, {"CLIP_POST_SECONDS": "0"})
        assert cfg.CLIP_POST_SECONDS == 1

    def test_clip_post_negative_clamped(self, monkeypatch):
        """CLIP_POST_SECONDS=-10 gets clamped to 1."""
        cfg = _reimport_config(monkeypatch, {"CLIP_POST_SECONDS": "-10"})
        assert cfg.CLIP_POST_SECONDS == 1

    def test_clip_post_valid_unchanged(self, monkeypatch):
        """CLIP_POST_SECONDS=20 stays as 20."""
        cfg = _reimport_config(monkeypatch, {"CLIP_POST_SECONDS": "20"})
        assert cfg.CLIP_POST_SECONDS == 20


class TestColorAreaSwap:
    """COLOR_MAX_AREA < COLOR_MIN_AREA should swap values."""

    def test_max_less_than_min_swapped(self, monkeypatch):
        """When COLOR_MAX_AREA < COLOR_MIN_AREA, they get swapped."""
        cfg = _reimport_config(monkeypatch, {
            "COLOR_MIN_AREA": "5000",
            "COLOR_MAX_AREA": "300",
        })
        assert cfg.COLOR_MIN_AREA == 300
        assert cfg.COLOR_MAX_AREA == 5000

    def test_max_greater_than_min_unchanged(self, monkeypatch):
        """When COLOR_MAX_AREA > COLOR_MIN_AREA, no swap."""
        cfg = _reimport_config(monkeypatch, {
            "COLOR_MIN_AREA": "100",
            "COLOR_MAX_AREA": "9000",
        })
        assert cfg.COLOR_MIN_AREA == 100
        assert cfg.COLOR_MAX_AREA == 9000


class TestCameraRotation:
    """CAMERA_ROTATION invalid values should default to 0."""

    @pytest.mark.parametrize("valid_rotation", [0, 90, 180, 270])
    def test_valid_rotations_accepted(self, monkeypatch, valid_rotation):
        """Valid rotation values (0, 90, 180, 270) are kept as-is."""
        cfg = _reimport_config(monkeypatch, {"CAMERA_ROTATION": str(valid_rotation)})
        assert cfg.CAMERA_ROTATION == valid_rotation

    @pytest.mark.parametrize("invalid_rotation", [45, 100, -90, 360, 1])
    def test_invalid_rotations_default_to_zero(self, monkeypatch, invalid_rotation):
        """Invalid rotation values default to 0."""
        cfg = _reimport_config(monkeypatch, {"CAMERA_ROTATION": str(invalid_rotation)})
        assert cfg.CAMERA_ROTATION == 0


class TestCircularBufferSize:
    """CIRCULAR_BUFFER_SIZE should be VIDEO_FPS * CLIP_PRE_SECONDS * 1.1."""

    def test_buffer_size_calculation(self, monkeypatch):
        """Buffer size = int(FPS * PRE_SECONDS * 1.1)."""
        cfg = _reimport_config(monkeypatch, {
            "VIDEO_FPS": "15",
            "CLIP_PRE_SECONDS": "5",
        })
        expected = int(15 * 5 * 1.1)  # 82
        assert cfg.CIRCULAR_BUFFER_SIZE == expected

    def test_buffer_size_with_different_values(self, monkeypatch):
        """Buffer size with non-default FPS and pre-seconds."""
        cfg = _reimport_config(monkeypatch, {
            "VIDEO_FPS": "30",
            "CLIP_PRE_SECONDS": "10",
        })
        expected = int(30 * 10 * 1.1)  # 330
        assert cfg.CIRCULAR_BUFFER_SIZE == expected
