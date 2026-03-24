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


class TestDeleteAllClips:
    """Test DELETE /api/clips endpoint."""

    def test_deletes_all_clips(self, client):
        c, m, tmp_path = client
        (tmp_path / "clip1.mp4").write_bytes(b"data")
        (tmp_path / "clip2.mp4").write_bytes(b"data")
        (tmp_path / "clip1.txt").write_text("caption")

        resp = c.delete("/api/clips")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["deleted"] >= 2

    def test_returns_ok_when_no_clips_dir(self, client, monkeypatch):
        import config
        c, m, tmp_path = client
        nonexistent = tmp_path / "nonexistent_clips"
        monkeypatch.setattr(config, "CLIPS_DIR", nonexistent)
        resp = c.delete("/api/clips")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True


class TestApiLogs:
    """Test GET /api/logs endpoint."""

    def test_returns_logs_list(self, client, tmp_path):
        c, m, _ = client
        log_file = tmp_path / "hummingbird.log"
        log_file.write_text("[INFO] Test log entry\n[ERROR] Something went wrong\n")

        resp = c.get("/api/logs")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "logs" in data

    def test_returns_empty_when_no_log_file(self, client):
        c, m, _ = client
        resp = c.get("/api/logs")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["logs"] == []


class TestClearLogs:
    """Test POST /api/logs/clear endpoint."""

    def test_clears_log_file(self, client, tmp_path):
        c, m, _ = client
        log_file = tmp_path / "hummingbird.log"
        log_file.write_text("some log content\n")

        resp = c.post("/api/logs/clear")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert log_file.read_text() == ""


class TestApiClipsList:
    """Test GET /api/clips/list endpoint."""

    def test_returns_clips_list(self, client, tmp_path):
        c, m, _ = client
        (tmp_path / "hummer_20260101_120000.mp4").write_bytes(b"video data" * 100)

        resp = c.get("/api/clips/list")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "clips" in data

    def test_returns_empty_list_when_no_clips(self, client):
        c, m, _ = client
        resp = c.get("/api/clips/list")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["clips"] == []


class TestFacebookDebug:
    """Test GET /api/facebook/debug endpoint."""

    def test_returns_token_info(self, client):
        c, m, _ = client
        m.poster.verify_token.return_value = {
            "configured": True,
            "valid": True,
            "scopes": [],
            "warnings": [],
        }

        resp = c.get("/api/facebook/debug")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert "facebook" in data

    def test_returns_503_when_no_monitor(self, client, monkeypatch):
        import web.dashboard as dashboard
        c, m, _ = client
        dashboard.set_monitor(None)
        resp = c.get("/api/facebook/debug")
        assert resp.status_code == 503
        dashboard.set_monitor(m)


class TestRotateCamera:
    """Test POST /api/camera/rotate endpoint."""

    def test_valid_rotation(self, client):
        c, m, _ = client
        with patch("web.dashboard._update_env_value"):
            resp = c.post("/api/camera/rotate", json={"rotation": 180})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["rotation"] == 180

    def test_invalid_rotation_returns_400(self, client):
        c, m, _ = client
        resp = c.post("/api/camera/rotate", json={"rotation": 45})
        assert resp.status_code == 400
        data = resp.get_json()
        assert data["ok"] is False

    def test_returns_503_when_no_monitor(self, client):
        import web.dashboard as dashboard
        c, m, _ = client
        dashboard.set_monitor(None)
        resp = c.post("/api/camera/rotate", json={"rotation": 90})
        assert resp.status_code == 503
        dashboard.set_monitor(m)


class TestTestFacebookPost:
    """Test POST /api/facebook/test endpoint."""

    def test_success(self, client):
        c, m, _ = client
        m.poster.post_text.return_value = True

        resp = c.post("/api/facebook/test")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True

    def test_failure(self, client):
        c, m, _ = client
        m.poster.post_text.return_value = False

        resp = c.post("/api/facebook/test")
        assert resp.status_code == 500
        data = resp.get_json()
        assert data["ok"] is False

    def test_returns_503_when_no_monitor(self, client):
        import web.dashboard as dashboard
        c, m, _ = client
        dashboard.set_monitor(None)
        resp = c.post("/api/facebook/test")
        assert resp.status_code == 503
        dashboard.set_monitor(m)
