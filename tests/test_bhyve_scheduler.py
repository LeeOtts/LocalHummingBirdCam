"""Tests for bhyve/scheduler.py — WateringScheduler class."""

import threading
from datetime import datetime, date, timedelta
from unittest.mock import MagicMock, patch, PropertyMock

import pytest


def _make_scheduler(monitor=None):
    """Create a WateringScheduler without starting threads."""
    from bhyve.scheduler import WateringScheduler
    s = object.__new__(WateringScheduler)
    s._monitor = monitor or MagicMock()
    s._running = False
    s._thread = None
    s._lock = threading.Lock()
    s._schedule_date = None
    s._slots = []
    s._wake = threading.Event()
    return s


# ---------------------------------------------------------------------------
# enabled property
# ---------------------------------------------------------------------------

class TestEnabled:
    def test_enabled_true(self, monkeypatch):
        import config
        monkeypatch.setattr(config, "BHYVE_SCHEDULE_ENABLED", True)
        monkeypatch.setattr(config, "BHYVE_SEASON_STARTED", True)
        s = _make_scheduler()
        assert s.enabled is True

    def test_enabled_false(self, monkeypatch):
        import config
        monkeypatch.setattr(config, "BHYVE_SCHEDULE_ENABLED", False)
        monkeypatch.setattr(config, "BHYVE_SEASON_STARTED", True)
        s = _make_scheduler()
        assert s.enabled is False

    def test_disabled_when_season_not_started(self, monkeypatch):
        import config
        monkeypatch.setattr(config, "BHYVE_SCHEDULE_ENABLED", True)
        monkeypatch.setattr(config, "BHYVE_SEASON_STARTED", False)
        s = _make_scheduler()
        assert s.enabled is False

    def test_disabled_when_both_false(self, monkeypatch):
        import config
        monkeypatch.setattr(config, "BHYVE_SCHEDULE_ENABLED", False)
        monkeypatch.setattr(config, "BHYVE_SEASON_STARTED", False)
        s = _make_scheduler()
        assert s.enabled is False


# ---------------------------------------------------------------------------
# _build_schedule
# ---------------------------------------------------------------------------

class TestBuildSchedule:
    def test_builds_slots_from_sunrise_sunset(self, monkeypatch):
        import config
        monkeypatch.setattr(config, "BHYVE_SCHEDULE_OFFSET_HOURS", 1)
        monkeypatch.setattr(config, "BHYVE_SCHEDULE_INTERVAL_HOURS", 2)

        sunrise = datetime(2026, 4, 1, 6, 30)
        sunset = datetime(2026, 4, 1, 19, 30)

        s = _make_scheduler()
        with patch("bhyve.scheduler._get_sun_times", return_value=(sunrise, sunset)):
            s._build_schedule(date(2026, 4, 1))

        # First slot = 6:30 + 1h = 7:30, then every 2h: 9:30, 11:30, 13:30, 15:30, 17:30, 19:30
        # 19:30 is not < 19:30, so last slot is 17:30
        assert len(s._slots) == 6
        assert s._slots[0] == datetime(2026, 4, 1, 7, 30)
        assert s._slots[-1] == datetime(2026, 4, 1, 17, 30)

    def test_no_slots_when_offset_past_sunset(self, monkeypatch):
        import config
        monkeypatch.setattr(config, "BHYVE_SCHEDULE_OFFSET_HOURS", 15)
        monkeypatch.setattr(config, "BHYVE_SCHEDULE_INTERVAL_HOURS", 1)

        sunrise = datetime(2026, 4, 1, 6, 30)
        sunset = datetime(2026, 4, 1, 19, 30)

        s = _make_scheduler()
        with patch("bhyve.scheduler._get_sun_times", return_value=(sunrise, sunset)):
            s._build_schedule(date(2026, 4, 1))

        assert s._slots == []
        assert s._schedule_date == date(2026, 4, 1)

    def test_sun_times_error_gives_empty_schedule(self):
        s = _make_scheduler()
        with patch("bhyve.scheduler._get_sun_times", side_effect=Exception("no sun")):
            s._build_schedule(date(2026, 4, 1))

        assert s._slots == []
        assert s._schedule_date == date(2026, 4, 1)

    def test_interval_clamped_to_min_one_hour(self, monkeypatch):
        import config
        monkeypatch.setattr(config, "BHYVE_SCHEDULE_OFFSET_HOURS", 0)
        monkeypatch.setattr(config, "BHYVE_SCHEDULE_INTERVAL_HOURS", 0)

        sunrise = datetime(2026, 4, 1, 6, 0)
        sunset = datetime(2026, 4, 1, 8, 0)

        s = _make_scheduler()
        with patch("bhyve.scheduler._get_sun_times", return_value=(sunrise, sunset)):
            s._build_schedule(date(2026, 4, 1))

        # interval=0 clamped to 1h: slots at 6:00, 7:00
        assert len(s._slots) == 2


# ---------------------------------------------------------------------------
# _ensure_schedule
# ---------------------------------------------------------------------------

class TestEnsureSchedule:
    def test_skips_rebuild_if_same_day(self, monkeypatch):
        s = _make_scheduler()
        today = date.today()
        s._schedule_date = today
        s._slots = ["placeholder"]

        # Should not rebuild since date matches
        s._ensure_schedule()
        assert s._slots == ["placeholder"]

    def test_rebuilds_on_new_day(self, monkeypatch):
        import config
        monkeypatch.setattr(config, "BHYVE_SCHEDULE_OFFSET_HOURS", 1)
        monkeypatch.setattr(config, "BHYVE_SCHEDULE_INTERVAL_HOURS", 2)

        s = _make_scheduler()
        s._schedule_date = date(2020, 1, 1)

        sunrise = datetime(2026, 4, 1, 6, 0)
        sunset = datetime(2026, 4, 1, 19, 0)
        with patch("bhyve.scheduler._get_sun_times", return_value=(sunrise, sunset)):
            s._ensure_schedule()

        assert s._schedule_date == date.today()


# ---------------------------------------------------------------------------
# todays_schedule property
# ---------------------------------------------------------------------------

class TestTodaysSchedule:
    def test_returns_copy_of_slots(self, monkeypatch):
        import config
        monkeypatch.setattr(config, "BHYVE_SCHEDULE_OFFSET_HOURS", 1)
        monkeypatch.setattr(config, "BHYVE_SCHEDULE_INTERVAL_HOURS", 2)

        s = _make_scheduler()
        s._schedule_date = date.today()
        slots = [datetime(2026, 4, 1, 7, 0), datetime(2026, 4, 1, 9, 0)]
        s._slots = slots

        result = s.todays_schedule
        assert result == slots
        assert result is not s._slots  # should be a copy


# ---------------------------------------------------------------------------
# next_watering property
# ---------------------------------------------------------------------------

class TestNextWatering:
    def test_returns_none_when_disabled(self, monkeypatch):
        import config
        monkeypatch.setattr(config, "BHYVE_SCHEDULE_ENABLED", False)
        s = _make_scheduler()
        assert s.next_watering is None

    def test_returns_next_future_slot(self, monkeypatch):
        import config
        monkeypatch.setattr(config, "BHYVE_SCHEDULE_ENABLED", True)
        monkeypatch.setattr(config, "BHYVE_SEASON_STARTED", True)
        monkeypatch.setattr(config, "LOCATION_TIMEZONE", "US/Central")

        s = _make_scheduler()
        s._schedule_date = date.today()
        future = datetime(2099, 12, 31, 23, 59)
        s._slots = [datetime(2020, 1, 1), future]

        with patch.object(type(s), "_now", return_value=datetime(2026, 4, 1, 12, 0)):
            result = s.next_watering

        assert result == future

    def test_returns_none_when_all_slots_past(self, monkeypatch):
        import config
        monkeypatch.setattr(config, "BHYVE_SCHEDULE_ENABLED", True)
        monkeypatch.setattr(config, "BHYVE_SEASON_STARTED", True)
        monkeypatch.setattr(config, "LOCATION_TIMEZONE", "US/Central")

        s = _make_scheduler()
        s._schedule_date = date.today()
        s._slots = [datetime(2020, 1, 1)]

        with patch.object(type(s), "_now", return_value=datetime(2026, 4, 1, 12, 0)):
            assert s.next_watering is None


# ---------------------------------------------------------------------------
# recalculate
# ---------------------------------------------------------------------------

class TestRecalculate:
    def test_invalidates_cache_and_wakes(self):
        s = _make_scheduler()
        s._schedule_date = date.today()
        s.recalculate()
        assert s._schedule_date is None
        assert s._wake.is_set()


# ---------------------------------------------------------------------------
# start / stop
# ---------------------------------------------------------------------------

class TestStartStop:
    def test_start_creates_daemon_thread(self):
        s = _make_scheduler()
        with patch.object(type(s), "_run"):
            s.start()
            assert s._running is True
            assert s._thread is not None
            assert s._thread.daemon is True
            s.stop()

    def test_stop_sets_running_false(self):
        s = _make_scheduler()
        s._running = True
        s.stop()
        assert s._running is False
        assert s._wake.is_set()


# ---------------------------------------------------------------------------
# _trigger_watering
# ---------------------------------------------------------------------------

class TestIsRaining:
    def test_returns_false_when_no_weather_data(self, monkeypatch):
        import config
        monkeypatch.setattr(config, "LOCATION_LAT", 35.1)
        monkeypatch.setattr(config, "LOCATION_LNG", -89.8)

        s = _make_scheduler()
        with patch("bhyve.scheduler.get_weather", return_value=None):
            assert s._is_raining() is False

    def test_returns_true_for_rain(self, monkeypatch):
        import config
        monkeypatch.setattr(config, "LOCATION_LAT", 35.1)
        monkeypatch.setattr(config, "LOCATION_LNG", -89.8)

        s = _make_scheduler()
        with patch("bhyve.scheduler.get_weather", return_value={"condition": "Rain"}):
            assert s._is_raining() is True

    def test_returns_true_for_drizzle(self, monkeypatch):
        import config
        monkeypatch.setattr(config, "LOCATION_LAT", 35.1)
        monkeypatch.setattr(config, "LOCATION_LNG", -89.8)

        s = _make_scheduler()
        with patch("bhyve.scheduler.get_weather", return_value={"condition": "Drizzle"}):
            assert s._is_raining() is True

    def test_returns_true_for_thunderstorm(self, monkeypatch):
        import config
        monkeypatch.setattr(config, "LOCATION_LAT", 35.1)
        monkeypatch.setattr(config, "LOCATION_LNG", -89.8)

        s = _make_scheduler()
        with patch("bhyve.scheduler.get_weather", return_value={"condition": "Thunderstorm"}):
            assert s._is_raining() is True

    def test_returns_false_for_clear(self, monkeypatch):
        import config
        monkeypatch.setattr(config, "LOCATION_LAT", 35.1)
        monkeypatch.setattr(config, "LOCATION_LNG", -89.8)

        s = _make_scheduler()
        with patch("bhyve.scheduler.get_weather", return_value={"condition": "Clear"}):
            assert s._is_raining() is False

    def test_returns_false_for_clouds(self, monkeypatch):
        import config
        monkeypatch.setattr(config, "LOCATION_LAT", 35.1)
        monkeypatch.setattr(config, "LOCATION_LNG", -89.8)

        s = _make_scheduler()
        with patch("bhyve.scheduler.get_weather", return_value={"condition": "Clouds"}):
            assert s._is_raining() is False


class TestTriggerWatering:
    def test_triggers_monitor(self, monkeypatch):
        import config
        monkeypatch.setattr(config, "BHYVE_RUN_MINUTES", 5)

        monitor = MagicMock()
        monitor.is_spraying = False
        monitor.start_watering.return_value = {"ok": True}

        s = _make_scheduler(monitor=monitor)
        with patch.object(s, "_is_raining", return_value=False):
            s._trigger_watering(datetime(2026, 4, 1, 8, 0))

        monitor.start_watering.assert_called_once_with(run_time_minutes=5)

    def test_skips_when_already_spraying(self, monkeypatch):
        import config
        monkeypatch.setattr(config, "BHYVE_RUN_MINUTES", 5)

        monitor = MagicMock()
        monitor.is_spraying = True

        s = _make_scheduler(monitor=monitor)
        s._trigger_watering(datetime(2026, 4, 1, 8, 0))

        monitor.start_watering.assert_not_called()

    def test_skips_when_raining(self, monkeypatch):
        import config
        monkeypatch.setattr(config, "BHYVE_RUN_MINUTES", 5)

        monitor = MagicMock()
        monitor.is_spraying = False

        s = _make_scheduler(monitor=monitor)
        with patch.object(s, "_is_raining", return_value=True):
            s._trigger_watering(datetime(2026, 4, 1, 8, 0))

        monitor.start_watering.assert_not_called()

    def test_logs_warning_on_failure(self, monkeypatch):
        import config
        monkeypatch.setattr(config, "BHYVE_RUN_MINUTES", 3)

        monitor = MagicMock()
        monitor.is_spraying = False
        monitor.start_watering.return_value = {"ok": False, "error": "timeout"}

        s = _make_scheduler(monitor=monitor)
        with patch.object(s, "_is_raining", return_value=False):
            s._trigger_watering(datetime(2026, 4, 1, 8, 0))

        monitor.start_watering.assert_called_once()


# ---------------------------------------------------------------------------
# _now
# ---------------------------------------------------------------------------

class TestNow:
    def test_returns_aware_datetime_pytz(self, monkeypatch):
        import config
        monkeypatch.setattr(config, "LOCATION_TIMEZONE", "US/Central")

        s = _make_scheduler()
        result = s._now()
        assert result.tzinfo is not None

    def test_falls_back_to_zoneinfo(self, monkeypatch):
        import config
        monkeypatch.setattr(config, "LOCATION_TIMEZONE", "US/Central")

        s = _make_scheduler()
        # Force ImportError on pytz
        import builtins
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "pytz":
                raise ImportError("no pytz")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        result = s._now()
        assert result.tzinfo is not None
