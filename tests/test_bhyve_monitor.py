"""Tests for bhyve/monitor.py — BHyveMonitor class."""

import threading
import time
from unittest.mock import MagicMock, patch

import pytest


def _make_monitor(email="test@example.com", password="secret", watch_station=1):
    """Create a BHyveMonitor without touching network or threads."""
    from bhyve.monitor import BHyveMonitor
    m = object.__new__(BHyveMonitor)
    m._email = email
    m._password = password
    m._watch_station = watch_station
    m._token = None
    m._user_id = None
    m._device_id = None
    m._lock = threading.Lock()
    m._active = {}
    m._ws = None
    m._running = False
    m._thread = None
    m.connected = False
    m.last_event = None
    m.last_event_time = 0.0
    m._on_spray_start = None
    m._on_spray_stop = None
    return m


# ---------------------------------------------------------------------------
# is_spraying
# ---------------------------------------------------------------------------

class TestIsSpraying:
    def test_false_when_no_active(self):
        m = _make_monitor(watch_station=1)
        assert m.is_spraying is False

    def test_true_when_watched_station_active(self):
        m = _make_monitor(watch_station=1)
        m._active["dev1"] = {"mode": "auto", "station": 1, "started_at": time.time()}
        assert m.is_spraying is True

    def test_false_when_different_station_active(self):
        m = _make_monitor(watch_station=1)
        m._active["dev1"] = {"mode": "auto", "station": 2, "started_at": time.time()}
        assert m.is_spraying is False

    def test_true_any_station_when_watch_is_none(self):
        m = _make_monitor(watch_station=None)
        m._active["dev1"] = {"mode": "auto", "station": 3, "started_at": time.time()}
        assert m.is_spraying is True

    def test_true_station_none_assumes_match(self):
        """If the event had no current_station, station=None; assume it matches."""
        m = _make_monitor(watch_station=1)
        m._active["dev1"] = {"mode": "auto", "station": None, "started_at": time.time()}
        assert m.is_spraying is True

    def test_multiple_devices_one_matches(self):
        m = _make_monitor(watch_station=2)
        m._active["dev1"] = {"mode": "auto", "station": 1, "started_at": time.time()}
        m._active["dev2"] = {"mode": "auto", "station": 2, "started_at": time.time()}
        assert m.is_spraying is True


# ---------------------------------------------------------------------------
# active_zones
# ---------------------------------------------------------------------------

class TestActiveZones:
    def test_empty_when_no_active(self):
        m = _make_monitor()
        assert m.active_zones == []

    def test_returns_all_active_entries(self):
        m = _make_monitor()
        m._active["devA"] = {"mode": "manual", "station": 1, "started_at": 100.0}
        m._active["devB"] = {"mode": "auto",   "station": 2, "started_at": 200.0}
        zones = m.active_zones
        assert len(zones) == 2
        device_ids = {z["device_id"] for z in zones}
        assert device_ids == {"devA", "devB"}

    def test_zone_dict_includes_device_id(self):
        m = _make_monitor()
        m._active["xyz"] = {"mode": "auto", "station": 3, "started_at": 50.0}
        zone = m.active_zones[0]
        assert zone["device_id"] == "xyz"
        assert zone["station"] == 3
        assert zone["mode"] == "auto"


# ---------------------------------------------------------------------------
# _handle_event
# ---------------------------------------------------------------------------

class TestHandleEvent:
    def test_watering_in_progress_adds_to_active(self):
        m = _make_monitor(watch_station=1)
        m._handle_event({
            "event": "watering_in_progress_notification",
            "device_id": "dev1",
            "mode": "auto",
            "program": {"current_station": 1},
        })
        assert "dev1" in m._active
        assert m._active["dev1"]["station"] == 1
        assert m._active["dev1"]["mode"] == "auto"

    def test_watering_in_progress_no_program(self):
        """Missing program field: station should be None."""
        m = _make_monitor()
        m._handle_event({
            "event": "watering_in_progress_notification",
            "device_id": "dev1",
        })
        assert m._active["dev1"]["station"] is None

    def test_watering_complete_removes_from_active(self):
        m = _make_monitor()
        m._active["dev1"] = {"mode": "auto", "station": 1, "started_at": 0.0}
        m._handle_event({"event": "watering_complete", "device_id": "dev1"})
        assert "dev1" not in m._active

    def test_device_idle_removes_from_active(self):
        m = _make_monitor()
        m._active["dev1"] = {"mode": "auto", "station": 1, "started_at": 0.0}
        m._handle_event({"event": "device_idle", "device_id": "dev1"})
        assert "dev1" not in m._active

    def test_change_mode_off_removes_from_active(self):
        m = _make_monitor()
        m._active["dev1"] = {"mode": "auto", "station": 1, "started_at": 0.0}
        m._handle_event({"event": "change_mode", "device_id": "dev1", "mode": "off"})
        assert "dev1" not in m._active

    def test_change_mode_non_off_does_not_remove(self):
        m = _make_monitor()
        m._active["dev1"] = {"mode": "auto", "station": 1, "started_at": 0.0}
        m._handle_event({"event": "change_mode", "device_id": "dev1", "mode": "auto"})
        assert "dev1" in m._active

    def test_unknown_event_does_not_crash(self):
        m = _make_monitor()
        m._handle_event({"event": "some_new_event_type", "device_id": "dev1"})
        assert m._active == {}

    def test_watering_in_progress_top_level_current_station(self):
        """Station number at top level (not nested in program)."""
        m = _make_monitor(watch_station=1)
        m._handle_event({
            "event": "watering_in_progress_notification",
            "device_id": "dev1",
            "current_station": 1,
        })
        assert m._active["dev1"]["station"] == 1

    def test_watering_in_progress_station_as_string(self):
        """Station number as string should be coerced to int."""
        m = _make_monitor(watch_station=1)
        m._handle_event({
            "event": "watering_in_progress_notification",
            "device_id": "dev1",
            "current_station": "1",
        })
        assert m._active["dev1"]["station"] == 1

    def test_duplicate_watering_event_updates_silently(self):
        """Second watering_in_progress for same device should not re-add."""
        m = _make_monitor(watch_station=1)
        m._handle_event({
            "event": "watering_in_progress_notification",
            "device_id": "dev1",
            "current_station": 1,
        })
        started = m._active["dev1"]["started_at"]
        # Second event — should update station but not started_at
        m._handle_event({
            "event": "watering_in_progress_notification",
            "device_id": "dev1",
            "current_station": 2,
        })
        assert m._active["dev1"]["station"] == 2
        assert m._active["dev1"]["started_at"] == started

    def test_missing_device_id_uses_unknown(self):
        m = _make_monitor()
        m._handle_event({
            "event": "watering_in_progress_notification",
            "mode": "auto",
            "program": {"current_station": 1},
        })
        assert "unknown" in m._active

    def test_last_event_updated(self):
        m = _make_monitor()
        m._handle_event({"event": "device_idle", "device_id": "dev1"})
        assert m.last_event == "device_idle"
        assert m.last_event_time > 0

    def test_watering_complete_on_unknown_device_no_error(self):
        """Stopping a device that was never in _active should not raise."""
        m = _make_monitor()
        m._handle_event({"event": "watering_complete", "device_id": "not_there"})
        assert m._active == {}


# ---------------------------------------------------------------------------
# _login
# ---------------------------------------------------------------------------

class TestLogin:
    def test_login_success_top_level_token(self):
        m = _make_monitor()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"orbit_session_token": "tok123"}
        mock_resp.raise_for_status = MagicMock()
        with patch("requests.post", return_value=mock_resp):
            result = m._login()
        assert result is True
        assert m._token == "tok123"

    def test_login_success_nested_token(self):
        m = _make_monitor()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"user": {"orbit_session_token": "nestedtok"}}
        mock_resp.raise_for_status = MagicMock()
        with patch("requests.post", return_value=mock_resp):
            result = m._login()
        assert result is True
        assert m._token == "nestedtok"

    def test_login_missing_token_returns_false(self):
        m = _make_monitor()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"some_other_field": "value"}
        mock_resp.raise_for_status = MagicMock()
        with patch("requests.post", return_value=mock_resp):
            result = m._login()
        assert result is False
        assert m._token is None

    def test_login_http_error_returns_false(self):
        import requests as req
        m = _make_monitor()
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        http_err = req.HTTPError(response=mock_resp)
        mock_resp.raise_for_status.side_effect = http_err
        with patch("requests.post", return_value=mock_resp):
            result = m._login()
        assert result is False

    def test_login_connection_error_returns_false(self):
        import requests as req
        m = _make_monitor()
        with patch("requests.post", side_effect=req.ConnectionError("unreachable")):
            result = m._login()
        assert result is False


# ---------------------------------------------------------------------------
# start / stop
# ---------------------------------------------------------------------------

class TestStartStop:
    def test_start_sets_running_and_spawns_thread(self):
        m = _make_monitor()
        barrier = threading.Barrier(2)

        def slow_run():
            barrier.wait()  # signal we started
            barrier.wait()  # wait for test to finish checking

        with patch.object(m, "_run", side_effect=slow_run):
            m.start()
            barrier.wait()  # wait until thread is running
            assert m._running is True
            assert m._thread is not None
            assert m._thread.is_alive()
            barrier.wait()  # let thread finish
            m._running = False

    def test_stop_clears_running(self):
        m = _make_monitor()
        m._running = True
        m._ws = None
        m.stop()
        assert m._running is False

    def test_stop_closes_websocket(self):
        m = _make_monitor()
        m._running = True
        mock_ws = MagicMock()
        m._ws = mock_ws
        m.stop()
        mock_ws.close.assert_called_once()

    def test_stop_handles_ws_close_exception(self):
        m = _make_monitor()
        m._running = True
        mock_ws = MagicMock()
        mock_ws.close.side_effect = Exception("already closed")
        m._ws = mock_ws
        # Should not raise
        m.stop()
        assert m._running is False


# ---------------------------------------------------------------------------
# device_id property
# ---------------------------------------------------------------------------

class TestDeviceId:
    def test_device_id_none_by_default(self):
        m = _make_monitor()
        assert m.device_id is None

    def test_device_id_returns_value(self):
        m = _make_monitor()
        m._device_id = "abc123"
        assert m.device_id == "abc123"


# ---------------------------------------------------------------------------
# _discover_devices
# ---------------------------------------------------------------------------

class TestDiscoverDevices:
    def test_discover_success(self):
        m = _make_monitor()
        m._token = "tok"
        m._user_id = "uid"
        mock_resp = MagicMock()
        mock_resp.json.return_value = [
            {"id": "dev1", "type": "sprinkler_timer", "name": "Front Yard"},
            {"id": "dev2", "type": "bridge", "name": "Hub"},
        ]
        mock_resp.raise_for_status = MagicMock()
        with patch("requests.get", return_value=mock_resp):
            m._discover_devices()
        assert m._device_id == "dev1"

    def test_discover_no_sprinkler(self):
        m = _make_monitor()
        m._token = "tok"
        m._user_id = "uid"
        mock_resp = MagicMock()
        mock_resp.json.return_value = [
            {"id": "dev2", "type": "bridge", "name": "Hub"},
        ]
        mock_resp.raise_for_status = MagicMock()
        with patch("requests.get", return_value=mock_resp):
            m._discover_devices()
        assert m._device_id is None

    def test_discover_network_error(self):
        import requests as req
        m = _make_monitor()
        m._token = "tok"
        m._user_id = "uid"
        with patch("requests.get", side_effect=req.ConnectionError("fail")):
            m._discover_devices()  # should not raise
        assert m._device_id is None

    def test_discover_no_token(self):
        m = _make_monitor()
        m._token = None
        m._discover_devices()
        assert m._device_id is None

    def test_discover_seeds_active_when_already_watering(self):
        m = _make_monitor(watch_station=1)
        m._token = "tok"
        m._user_id = "uid"
        mock_resp = MagicMock()
        mock_resp.json.return_value = [
            {
                "id": "dev1", "type": "sprinkler_timer", "name": "Mister",
                "status": {"watering_status": {"stations": [{"station": 1}]}},
            },
        ]
        mock_resp.raise_for_status = MagicMock()
        with patch("requests.get", return_value=mock_resp):
            m._discover_devices()
        assert m._device_id == "dev1"
        assert m.is_spraying is True
        assert m._active["dev1"]["station"] == 1

    def test_discover_no_active_when_not_watering(self):
        m = _make_monitor(watch_station=1)
        m._token = "tok"
        m._user_id = "uid"
        mock_resp = MagicMock()
        mock_resp.json.return_value = [
            {"id": "dev1", "type": "sprinkler_timer", "name": "Mister", "status": {}},
        ]
        mock_resp.raise_for_status = MagicMock()
        with patch("requests.get", return_value=mock_resp):
            m._discover_devices()
        assert m._device_id == "dev1"
        assert m.is_spraying is False

    def test_discover_seeds_active_via_is_watering_flag(self):
        m = _make_monitor(watch_station=1)
        m._token = "tok"
        m._user_id = "uid"
        mock_resp = MagicMock()
        mock_resp.json.return_value = [
            {
                "id": "dev1", "type": "sprinkler_timer", "name": "Mister",
                "is_watering": True, "status": {},
            },
        ]
        mock_resp.raise_for_status = MagicMock()
        with patch("requests.get", return_value=mock_resp):
            m._discover_devices()
        assert m.is_spraying is True


# ---------------------------------------------------------------------------
# start_watering / stop_watering
# ---------------------------------------------------------------------------

class TestStartWatering:
    @patch("time.sleep")  # skip the 3s verification delay
    def test_start_success(self, _sleep):
        m = _make_monitor(watch_station=1)
        m._device_id = "dev1"
        m._token = "tok"
        m.connected = True
        m._ws = MagicMock()
        with patch.object(m, "_query_device_watering", return_value=True):
            result = m.start_watering(run_time_minutes=5)
        assert result["ok"] is True
        assert result["station"] == 1
        assert result["run_time"] == 5
        m._ws.send.assert_called_once()
        import json
        payload = json.loads(m._ws.send.call_args[0][0])
        assert payload["event"] == "change_mode"
        assert payload["mode"] == "manual"
        assert payload["device_id"] == "dev1"
        assert payload["stations"] == [{"station": 1, "run_time": 5}]
        assert payload["orbit_session_token"] == "tok"

    def test_start_not_connected(self):
        m = _make_monitor()
        m._device_id = "dev1"
        m.connected = False
        result = m.start_watering()
        assert result["ok"] is False
        assert "Not connected" in result["error"]

    def test_start_no_device(self):
        m = _make_monitor()
        m.connected = True
        m._ws = MagicMock()
        result = m.start_watering()
        assert result["ok"] is False
        assert "No device" in result["error"]

    @patch("time.sleep")
    def test_start_clamps_run_time(self, _sleep):
        m = _make_monitor(watch_station=1)
        m._device_id = "dev1"
        m.connected = True
        m._ws = MagicMock()
        with patch.object(m, "_query_device_watering", return_value=True):
            result = m.start_watering(run_time_minutes=999)
        assert result["ok"] is True
        assert result["run_time"] <= 30  # BHYVE_MAX_RUN_MINUTES default

    def test_start_ws_send_error(self):
        m = _make_monitor(watch_station=1)
        m._device_id = "dev1"
        m.connected = True
        m._ws = MagicMock()
        m._ws.send.side_effect = Exception("connection lost")
        result = m.start_watering()
        assert result["ok"] is False
        assert "connection lost" in result["error"]

    @patch("time.sleep")
    def test_start_default_station(self, _sleep):
        m = _make_monitor(watch_station=2)
        m._device_id = "dev1"
        m.connected = True
        m._ws = MagicMock()
        with patch.object(m, "_query_device_watering", return_value=True):
            result = m.start_watering()
        assert result["station"] == 2

    @patch("time.sleep")
    def test_start_verification_fails(self, _sleep):
        """Device didn't actually start — report failure honestly."""
        m = _make_monitor(watch_station=1)
        m._device_id = "dev1"
        m.connected = True
        m._ws = MagicMock()
        with patch.object(m, "_query_device_watering", return_value=False):
            result = m.start_watering()
        assert result["ok"] is False
        assert "did not start" in result["error"]

    @patch("time.sleep")
    def test_start_verification_unavailable(self, _sleep):
        """REST query failed — report ok with warning."""
        m = _make_monitor(watch_station=1)
        m._device_id = "dev1"
        m.connected = True
        m._ws = MagicMock()
        with patch.object(m, "_query_device_watering", return_value=None):
            result = m.start_watering()
        assert result["ok"] is True
        assert "warning" in result


class TestStopWatering:
    @patch("time.sleep")
    def test_stop_success(self, _sleep):
        m = _make_monitor(watch_station=1)
        m._device_id = "dev1"
        m._token = "tok123"
        m.connected = True
        m._ws = MagicMock()
        with patch.object(m, "_query_device_watering", return_value=False):
            result = m.stop_watering()
        assert result["ok"] is True
        m._ws.send.assert_called_once()
        import json
        payload = json.loads(m._ws.send.call_args[0][0])
        assert payload["mode"] == "manual"
        assert payload["stations"] == []
        assert payload["device_id"] == "dev1"
        assert payload["orbit_session_token"] == "tok123"

    def test_stop_not_connected(self):
        m = _make_monitor()
        m._device_id = "dev1"
        m.connected = False
        result = m.stop_watering()
        assert result["ok"] is False

    def test_stop_no_device(self):
        m = _make_monitor()
        m.connected = True
        m._ws = MagicMock()
        result = m.stop_watering()
        assert result["ok"] is False

    @patch("time.sleep")
    def test_stop_verification_fails(self, _sleep):
        """Device didn't actually stop — report failure."""
        m = _make_monitor(watch_station=1)
        m._device_id = "dev1"
        m._token = "tok"
        m.connected = True
        m._ws = MagicMock()
        with patch.object(m, "_query_device_watering", return_value=True):
            result = m.stop_watering()
        assert result["ok"] is False
        assert "still watering" in result["error"]


# ---------------------------------------------------------------------------
# _login captures user_id
# ---------------------------------------------------------------------------

class TestLoginUserId:
    def test_login_captures_top_level_user_id(self):
        m = _make_monitor()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "orbit_session_token": "tok",
            "user_id": "uid123",
        }
        mock_resp.raise_for_status = MagicMock()
        with patch("requests.post", return_value=mock_resp):
            m._login()
        assert m._user_id == "uid123"

    def test_login_captures_nested_user_id(self):
        m = _make_monitor()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "orbit_session_token": "tok",
            "user": {"id": "nested_uid"},
        }
        mock_resp.raise_for_status = MagicMock()
        with patch("requests.post", return_value=mock_resp):
            m._login()
        assert m._user_id == "nested_uid"


# ---------------------------------------------------------------------------
# Callback filtering by watch_station
# ---------------------------------------------------------------------------

class TestCallbackFiltering:
    def test_start_callback_fires_for_watched_station(self):
        m = _make_monitor(watch_station=1)
        cb = MagicMock()
        m._on_spray_start = cb
        m._handle_event({
            "event": "watering_in_progress_notification",
            "device_id": "dev1",
            "current_station": 1,
        })
        cb.assert_called_once_with("1")

    def test_start_callback_skipped_for_unwatched_station(self):
        m = _make_monitor(watch_station=1)
        cb = MagicMock()
        m._on_spray_start = cb
        m._handle_event({
            "event": "watering_in_progress_notification",
            "device_id": "dev1",
            "current_station": 2,
        })
        cb.assert_not_called()
        # Device should still be tracked in _active
        assert "dev1" in m._active
        assert m._active["dev1"]["station"] == 2

    def test_stop_callback_fires_for_watched_station(self):
        m = _make_monitor(watch_station=1)
        m._active["dev1"] = {"mode": "auto", "station": 1, "started_at": 0.0}
        cb = MagicMock()
        m._on_spray_stop = cb
        m._handle_event({"event": "watering_complete", "device_id": "dev1"})
        cb.assert_called_once_with("1")

    def test_stop_callback_skipped_for_unwatched_station(self):
        m = _make_monitor(watch_station=1)
        m._active["dev1"] = {"mode": "auto", "station": 2, "started_at": 0.0}
        cb = MagicMock()
        m._on_spray_stop = cb
        m._handle_event({"event": "watering_complete", "device_id": "dev1"})
        cb.assert_not_called()

    def test_transition_to_watched_fires_start(self):
        """Device already active on zone 2, update to zone 1 fires start."""
        m = _make_monitor(watch_station=1)
        start_cb = MagicMock()
        m._on_spray_start = start_cb
        m._active["dev1"] = {"mode": "auto", "station": 2, "started_at": 0.0}
        m._handle_event({
            "event": "watering_in_progress_notification",
            "device_id": "dev1",
            "current_station": 1,
        })
        start_cb.assert_called_once_with("1")
        assert m._active["dev1"]["station"] == 1

    def test_transition_from_watched_fires_stop(self):
        """Device active on zone 1, update to zone 2 fires stop."""
        m = _make_monitor(watch_station=1)
        stop_cb = MagicMock()
        m._on_spray_stop = stop_cb
        m._active["dev1"] = {"mode": "auto", "station": 1, "started_at": 0.0}
        m._handle_event({
            "event": "watering_in_progress_notification",
            "device_id": "dev1",
            "current_station": 2,
        })
        stop_cb.assert_called_once_with("1")
        assert m._active["dev1"]["station"] == 2

    def test_callbacks_fire_for_any_station_when_watch_none(self):
        m = _make_monitor(watch_station=None)
        start_cb = MagicMock()
        stop_cb = MagicMock()
        m._on_spray_start = start_cb
        m._on_spray_stop = stop_cb
        m._handle_event({
            "event": "watering_in_progress_notification",
            "device_id": "dev1",
            "current_station": 3,
        })
        start_cb.assert_called_once_with("3")
        m._handle_event({"event": "watering_complete", "device_id": "dev1"})
        stop_cb.assert_called_once_with("3")

    def test_initial_state_callback_respects_watch_station(self):
        m = _make_monitor(watch_station=1)
        cb = MagicMock()
        m._on_spray_start = cb
        m._token = "tok"
        m._user_id = "uid"
        # Device watering on zone 2 — callback should NOT fire
        mock_resp = MagicMock()
        mock_resp.json.return_value = [{
            "id": "dev1", "type": "sprinkler_timer", "name": "Test",
            "status": {"watering_status": {"stations": [{"station": 2}]}},
        }]
        mock_resp.raise_for_status = MagicMock()
        with patch("requests.get", return_value=mock_resp):
            m._discover_devices()
        cb.assert_not_called()
        # Device is still tracked in _active
        assert "dev1" in m._active

    def test_initial_state_callback_fires_for_watched(self):
        m = _make_monitor(watch_station=1)
        cb = MagicMock()
        m._on_spray_start = cb
        m._token = "tok"
        m._user_id = "uid"
        mock_resp = MagicMock()
        mock_resp.json.return_value = [{
            "id": "dev1", "type": "sprinkler_timer", "name": "Test",
            "status": {"watering_status": {"stations": [{"station": 1}]}},
        }]
        mock_resp.raise_for_status = MagicMock()
        with patch("requests.get", return_value=mock_resp):
            m._discover_devices()
        cb.assert_called_once_with("1")
