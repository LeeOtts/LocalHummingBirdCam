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
        for name in ("a.mp4", "b.mp4", "c.txt"):
            (tmp_path / name).write_bytes(b"data")

        resp = c.delete("/api/clips")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["deleted"] == 3

    def test_returns_ok_when_no_clips_dir(self, client, monkeypatch):
        import config
        c, m, tmp_path = client
        monkeypatch.setattr(config, "CLIPS_DIR", tmp_path / "nonexistent")
        resp = c.delete("/api/clips")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["deleted"] == 0


class TestApiLogs:
    """Test GET /api/logs endpoint."""

    def test_returns_log_list(self, client):
        c, m, tmp_path = client
        log_file = tmp_path / "hummingbird.log"
        log_file.write_text("[INFO] Test log line\n[ERROR] An error\n")

        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = "[INFO] Test log line\n[ERROR] An error"
            resp = c.get("/api/logs")

        assert resp.status_code == 200
        data = resp.get_json()
        assert "logs" in data

    def test_returns_empty_when_no_log_file(self, client, monkeypatch):
        import config
        c, m, tmp_path = client
        monkeypatch.setattr(config, "LOGS_DIR", tmp_path / "nologs")
        resp = c.get("/api/logs")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["logs"] == []


class TestClearLogs:
    """Test POST /api/logs/clear endpoint."""

    def test_clears_log_file(self, client):
        c, m, tmp_path = client
        log_file = tmp_path / "hummingbird.log"
        log_file.write_text("old log content\n")

        resp = c.post("/api/logs/clear")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert log_file.read_text() == ""


class TestFacebookDebug:
    """Test GET /api/facebook/debug endpoint."""

    def test_returns_token_info(self, client):
        c, m, _ = client
        m.poster.verify_token.return_value = {"valid": True, "scopes": []}

        resp = c.get("/api/facebook/debug")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert "facebook" in data

    def test_returns_503_when_no_monitor(self, client, monkeypatch):
        import web.dashboard as dashboard
        c, m, _ = client
        old = dashboard._monitor
        dashboard._monitor = None
        try:
            resp = c.get("/api/facebook/debug")
            assert resp.status_code == 503
        finally:
            dashboard._monitor = old

    def test_returns_500_on_exception(self, client):
        c, m, _ = client
        m.poster.verify_token.side_effect = RuntimeError("token error")

        resp = c.get("/api/facebook/debug")
        assert resp.status_code == 500


class TestTestFacebookPost:
    """Test POST /api/facebook/test endpoint."""

    def test_returns_ok_on_success(self, client):
        c, m, _ = client
        m.poster.post_text.return_value = True

        resp = c.post("/api/facebook/test")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True

    def test_returns_500_on_failure(self, client):
        c, m, _ = client
        m.poster.post_text.return_value = False

        resp = c.post("/api/facebook/test")
        assert resp.status_code == 500

    def test_returns_503_when_no_monitor(self, client):
        import web.dashboard as dashboard
        c, m, _ = client
        old = dashboard._monitor
        dashboard._monitor = None
        try:
            resp = c.post("/api/facebook/test")
            assert resp.status_code == 503
        finally:
            dashboard._monitor = old


class TestRotateCamera:
    """Test POST /api/camera/rotate endpoint."""

    def test_valid_rotation_returns_ok(self, client):
        c, m, _ = client
        with patch("web.dashboard._update_env_value"):
            resp = c.post(
                "/api/camera/rotate",
                json={"rotation": 90},
                content_type="application/json",
            )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["rotation"] == 90

    def test_invalid_rotation_returns_400(self, client):
        c, m, _ = client
        resp = c.post(
            "/api/camera/rotate",
            json={"rotation": 45},
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_returns_503_when_no_monitor(self, client):
        import web.dashboard as dashboard
        c, m, _ = client
        old = dashboard._monitor
        dashboard._monitor = None
        try:
            resp = c.post("/api/camera/rotate", json={"rotation": 0})
            assert resp.status_code == 503
        finally:
            dashboard._monitor = old


class TestApiClipsList:
    """Test GET /api/clips/list endpoint."""

    def test_returns_clip_list(self, client):
        c, m, tmp_path = client
        (tmp_path / "hummer_01.mp4").write_bytes(b"data" * 100)

        resp = c.get("/api/clips/list")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "clips" in data

    def test_returns_empty_when_no_clips(self, client):
        c, m, tmp_path = client
        resp = c.get("/api/clips/list")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["clips"] == []


class TestDetectionStateNoMonitor:
    """Test /api/detection-state with no monitor set."""

    def test_returns_idle_when_no_monitor(self, client):
        import web.dashboard as dashboard
        c, m, _ = client
        old = dashboard._monitor
        dashboard._monitor = None
        try:
            resp = c.get("/api/detection-state")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["state"] == "idle"
        finally:
            dashboard._monitor = old


class TestServeClip:
    """Test GET /clips/<filename>."""

    def test_rejects_path_traversal(self, client):
        c, m, _ = client
        resp = c.get("/clips/../secret")
        assert resp.status_code in (400, 404)

    def test_returns_400_for_backslash(self, client):
        c, m, _ = client
        resp = c.get("/clips/evil\\file.mp4")
        assert resp.status_code == 400

    def test_serves_existing_clip(self, client):
        c, m, tmp_path = client
        clip = tmp_path / "hummer_20260324.mp4"
        clip.write_bytes(b"fake video" * 100)

        resp = c.get("/clips/hummer_20260324.mp4")
        assert resp.status_code == 200


class TestEnforceAuth:
    """Test _enforce_auth basic auth when WEB_PASSWORD is set."""

    def test_blocks_unauthenticated_request(self, tmp_path, monkeypatch):
        import config
        from web import dashboard

        monkeypatch.setattr(config, "WEB_PASSWORD", "secret")
        monkeypatch.setattr(config, "CLIPS_DIR", tmp_path)
        monkeypatch.setattr(config, "LOGS_DIR", tmp_path)
        monkeypatch.setattr(config, "TRAINING_DIR", tmp_path / "training")

        dashboard.app.config["TESTING"] = True
        with dashboard.app.test_client() as c:
            resp = c.get("/api/status")
        assert resp.status_code == 401

    def test_allows_correct_password(self, tmp_path, monkeypatch, mock_monitor):
        import config
        from web import dashboard
        import base64

        monkeypatch.setattr(config, "WEB_PASSWORD", "secret")
        monkeypatch.setattr(config, "CLIPS_DIR", tmp_path)
        monkeypatch.setattr(config, "LOGS_DIR", tmp_path)
        monkeypatch.setattr(config, "TRAINING_DIR", tmp_path / "training")

        dashboard.set_monitor(mock_monitor)
        dashboard.app.config["TESTING"] = True
        credentials = base64.b64encode(b"user:secret").decode("utf-8")
        with dashboard.app.test_client() as c:
            resp = c.get("/api/status", headers={"Authorization": f"Basic {credentials}"})
        assert resp.status_code == 200

    def test_public_feed_bypasses_auth(self, tmp_path, monkeypatch, mock_monitor):
        import config
        from web import dashboard

        monkeypatch.setattr(config, "WEB_PASSWORD", "secret")
        monkeypatch.setattr(config, "CLIPS_DIR", tmp_path)
        monkeypatch.setattr(config, "LOGS_DIR", tmp_path)
        monkeypatch.setattr(config, "TRAINING_DIR", tmp_path / "training")

        dashboard.set_monitor(mock_monitor)
        mock_monitor.camera.is_available = False
        dashboard.app.config["TESTING"] = True
        with dashboard.app.test_client() as c:
            resp = c.get("/feed")
        # 503 because camera unavailable, but NOT 401 — auth was bypassed
        assert resp.status_code == 503


class TestTestRecord:
    """Test POST /api/test-record endpoint."""

    def test_returns_ok_and_starts_thread(self, client):
        c, m, _ = client
        import web.dashboard as dashboard
        dashboard._recording_in_progress = False

        with patch("threading.Thread") as mock_thread:
            mock_thread.return_value.start = MagicMock()
            resp = c.post("/api/test-record")

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True

    def test_returns_429_when_already_recording(self, client):
        import web.dashboard as dashboard
        c, m, _ = client
        dashboard._recording_in_progress = True
        try:
            resp = c.post("/api/test-record")
            assert resp.status_code == 429
        finally:
            dashboard._recording_in_progress = False

    def test_returns_503_when_not_running(self, client):
        c, m, _ = client
        m.running = False
        resp = c.post("/api/test-record")
        assert resp.status_code == 503


class TestRateLimiting:
    """Test login attempt rate limiting."""

    def test_returns_429_after_max_failed_attempts(self, tmp_path, monkeypatch):
        import config
        from web import dashboard

        monkeypatch.setattr(config, "WEB_PASSWORD", "secret")
        monkeypatch.setattr(config, "CLIPS_DIR", tmp_path)
        monkeypatch.setattr(config, "LOGS_DIR", tmp_path)
        monkeypatch.setattr(config, "TRAINING_DIR", tmp_path / "training")

        # Clear any prior rate limit state
        dashboard._auth_failures.clear()
        dashboard.app.config["TESTING"] = True

        with dashboard.app.test_client() as c:
            # Send 5 failed attempts
            for _ in range(5):
                resp = c.get("/api/status")
                assert resp.status_code == 401

            # 6th attempt should be rate limited
            resp = c.get("/api/status")
            assert resp.status_code == 429

        dashboard._auth_failures.clear()

    def test_allows_after_window_expires(self, tmp_path, monkeypatch):
        import config
        from web import dashboard
        import time as _time

        monkeypatch.setattr(config, "WEB_PASSWORD", "secret")
        monkeypatch.setattr(config, "CLIPS_DIR", tmp_path)
        monkeypatch.setattr(config, "LOGS_DIR", tmp_path)
        monkeypatch.setattr(config, "TRAINING_DIR", tmp_path / "training")

        dashboard._auth_failures.clear()
        dashboard.app.config["TESTING"] = True

        # Inject old timestamps (> 5 min ago)
        old_time = _time.time() - 400
        dashboard._auth_failures["127.0.0.1"] = [old_time] * 10

        with dashboard.app.test_client() as c:
            resp = c.get("/api/status")
            # Should get 401 (auth required), not 429 (rate limited)
            assert resp.status_code == 401

        dashboard._auth_failures.clear()
