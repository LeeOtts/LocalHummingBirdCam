"""Tests for Flask routes in web/dashboard.py."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def mock_monitor():
    m = MagicMock()
    m.running = True
    m.detection_state = "idle"
    m.test_mode = False
    m._detections_today = 0
    m._rejected_today = 0
    m._start_time = 0.0
    m._last_detection_time = 0.0
    m._total_lifetime_detections = 0
    m._all_time_record = 0
    m._yesterday_detections = 0
    m.camera.is_available = True
    m.camera.camera_type = "usb"
    m.camera.error = None
    m.poster._posts_today = 0
    # _cooldown_elapsed is called by _get_status
    m._cooldown_elapsed.return_value = True
    return m


@pytest.fixture
def client(tmp_path, monkeypatch, mock_monitor):
    import config
    from web import dashboard

    monkeypatch.setattr(config, "CLIPS_DIR", tmp_path)
    monkeypatch.setattr(config, "LOGS_DIR", tmp_path)
    monkeypatch.setattr(config, "TRAINING_DIR", tmp_path / "training")

    dashboard.set_monitor(mock_monitor)
    dashboard.app.config["TESTING"] = True

    with dashboard.app.test_client() as c:
        yield c, mock_monitor, tmp_path


class TestApiDetectionState:
    """Test /api/detection-state endpoint."""

    def test_returns_idle_state(self, client):
        c, m, _ = client
        m.detection_state = "idle"
        resp = c.get("/api/detection-state")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["state"] == "idle"

    def test_returns_sleeping_state(self, client):
        c, m, _ = client
        m.detection_state = "sleeping"
        resp = c.get("/api/detection-state")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["state"] == "sleeping"


class TestApiStatus:
    """Test /api/status endpoint."""

    def test_returns_200(self, client):
        c, m, _ = client
        resp = c.get("/api/status")
        assert resp.status_code == 200

    def test_response_has_required_fields(self, client):
        c, m, _ = client
        resp = c.get("/api/status")
        data = resp.get_json()
        assert "running" in data
        assert "detections_today" in data
        assert "rejected_today" in data
        assert "camera_type" in data


class TestDeleteClip:
    """Test DELETE /api/clips/<filename> endpoint."""

    def test_deletes_existing_clip(self, client):
        c, m, tmp_path = client
        clip = tmp_path / "hummer_20260324_090000.mp4"
        clip.write_bytes(b"fake video content")

        resp = c.delete("/api/clips/hummer_20260324_090000.mp4")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert not clip.exists()

    def test_returns_404_for_missing_clip(self, client):
        c, m, tmp_path = client
        resp = c.delete("/api/clips/nonexistent.mp4")
        assert resp.status_code == 404
        data = resp.get_json()
        assert data["ok"] is False

    def test_rejects_path_traversal(self, client):
        c, m, tmp_path = client
        # URL-encoded ".." traversal
        resp = c.delete("/api/clips/../something")
        # Flask may return 404 for the route or 400 for the sanitization check
        assert resp.status_code in (400, 404)


class TestToggleTestMode:
    """Test POST /api/test-mode endpoint."""

    def test_toggles_from_false_to_true(self, client):
        c, m, _ = client
        m.test_mode = False

        with patch("web.dashboard._update_env_value"):
            resp = c.post("/api/test-mode")

        assert resp.status_code == 200
        assert m.test_mode is True

    def test_toggles_from_true_to_false(self, client):
        c, m, _ = client
        m.test_mode = True

        with patch("web.dashboard._update_env_value"):
            resp = c.post("/api/test-mode")

        assert resp.status_code == 200
        assert m.test_mode is False


class TestLiveFeed:
    """Test GET /feed endpoint."""

    def test_returns_503_when_camera_unavailable(self, client):
        c, m, _ = client
        m.camera.is_available = False
        resp = c.get("/feed")
        assert resp.status_code == 503

    def test_returns_503_when_monitor_not_running(self, client):
        c, m, _ = client
        m.running = False
        resp = c.get("/feed")
        assert resp.status_code == 503
