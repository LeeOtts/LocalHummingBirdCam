"""Tests for schedule.py — sunrise/sunset logic and daytime checks.

Mocks datetime and astral for deterministic, timezone-safe tests.
"""

from datetime import datetime, date, timedelta
from unittest.mock import MagicMock, patch

import pytest

import schedule as sched


@pytest.fixture(autouse=True)
def _clear_cache():
    """Clear the module-level cache before each test."""
    sched._cached_date = None
    sched._cached_sunrise = None
    sched._cached_sunset = None
    yield
    sched._cached_date = None
    sched._cached_sunrise = None
    sched._cached_sunset = None


def _make_tz():
    """Create a mock timezone for testing."""
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo("America/Chicago")
    except ImportError:
        import pytz
        return pytz.timezone("America/Chicago")


def _make_aware_dt(hour, minute=0, tz=None):
    """Create a timezone-aware datetime for today at the given hour."""
    if tz is None:
        tz = _make_tz()
    dt = datetime(2025, 6, 15, hour, minute, tzinfo=tz)
    return dt


class TestIsDaytimeNightModeDisabled:
    """When night mode is disabled, is_daytime() always returns True."""

    def test_returns_true_when_disabled(self, monkeypatch):
        """is_daytime() returns True regardless of time when night mode is off."""
        import config
        monkeypatch.setattr(config, "NIGHT_MODE_ENABLED", False)
        assert sched.is_daytime() is True


class TestIsDaytimeDuringDay:
    """is_daytime() should return True during daytime hours."""

    def test_returns_true_at_noon(self, monkeypatch):
        """Noon is clearly daytime."""
        import config
        monkeypatch.setattr(config, "NIGHT_MODE_ENABLED", True)
        monkeypatch.setattr(config, "LOCATION_LAT", 35.1495)
        monkeypatch.setattr(config, "LOCATION_LNG", -89.8733)
        monkeypatch.setattr(config, "LOCATION_TIMEZONE", "America/Chicago")
        monkeypatch.setattr(config, "LOCATION_NAME", "Bartlett, TN")
        monkeypatch.setattr(config, "WAKE_BEFORE_SUNRISE_MIN", 30)
        monkeypatch.setattr(config, "SLEEP_AFTER_SUNSET_MIN", 30)

        tz = _make_tz()

        # Mock sun() to return known sunrise/sunset
        sunrise = _make_aware_dt(6, 0, tz)
        sunset = _make_aware_dt(20, 0, tz)

        mock_sun_result = {"sunrise": sunrise, "sunset": sunset}

        with patch("schedule.sun", return_value=mock_sun_result):
            with patch("schedule.datetime") as mock_dt:
                mock_dt.now.return_value = _make_aware_dt(12, 0, tz)
                mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
                result = sched.is_daytime()

        assert result is True


class TestIsDaytimeAtNight:
    """is_daytime() should return False at night."""

    def test_returns_false_late_night(self, monkeypatch):
        """11 PM is after sunset + offset, so it should be night."""
        import config
        monkeypatch.setattr(config, "NIGHT_MODE_ENABLED", True)
        monkeypatch.setattr(config, "LOCATION_LAT", 35.1495)
        monkeypatch.setattr(config, "LOCATION_LNG", -89.8733)
        monkeypatch.setattr(config, "LOCATION_TIMEZONE", "America/Chicago")
        monkeypatch.setattr(config, "LOCATION_NAME", "Bartlett, TN")
        monkeypatch.setattr(config, "WAKE_BEFORE_SUNRISE_MIN", 30)
        monkeypatch.setattr(config, "SLEEP_AFTER_SUNSET_MIN", 30)

        tz = _make_tz()
        sunrise = _make_aware_dt(6, 0, tz)
        sunset = _make_aware_dt(20, 0, tz)

        mock_sun_result = {"sunrise": sunrise, "sunset": sunset}

        with patch("schedule.sun", return_value=mock_sun_result):
            with patch("schedule.datetime") as mock_dt:
                mock_dt.now.return_value = _make_aware_dt(23, 0, tz)
                mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
                result = sched.is_daytime()

        assert result is False


class TestWakeSleepOffsets:
    """Test that WAKE_BEFORE_SUNRISE_MIN and SLEEP_AFTER_SUNSET_MIN are applied."""

    def test_wake_offset_applied(self, monkeypatch):
        """Wake time = sunrise - WAKE_BEFORE_SUNRISE_MIN."""
        import config
        monkeypatch.setattr(config, "NIGHT_MODE_ENABLED", True)
        monkeypatch.setattr(config, "LOCATION_LAT", 35.1495)
        monkeypatch.setattr(config, "LOCATION_LNG", -89.8733)
        monkeypatch.setattr(config, "LOCATION_TIMEZONE", "America/Chicago")
        monkeypatch.setattr(config, "LOCATION_NAME", "Bartlett, TN")
        monkeypatch.setattr(config, "WAKE_BEFORE_SUNRISE_MIN", 45)
        monkeypatch.setattr(config, "SLEEP_AFTER_SUNSET_MIN", 15)

        tz = _make_tz()
        sunrise = _make_aware_dt(6, 0, tz)  # 6:00 AM
        sunset = _make_aware_dt(20, 0, tz)   # 8:00 PM

        mock_sun_result = {"sunrise": sunrise, "sunset": sunset}

        with patch("schedule.sun", return_value=mock_sun_result):
            with patch("schedule.date") as mock_date:
                mock_date.today.return_value = date(2025, 6, 15)
                wake, sleep = sched._get_sun_times()

        # Wake = 6:00 - 45min = 5:15 AM
        assert wake.hour == 5
        assert wake.minute == 15

        # Sleep = 20:00 + 15min = 20:15
        assert sleep.hour == 20
        assert sleep.minute == 15


class TestGetScheduleInfo:
    """Test get_schedule_info() return structure."""

    def test_disabled_returns_correct_dict(self, monkeypatch):
        """When night mode is disabled, returns a dict with enabled=False."""
        import config
        monkeypatch.setattr(config, "NIGHT_MODE_ENABLED", False)
        monkeypatch.setattr(config, "LOCATION_NAME", "Bartlett, TN")

        info = sched.get_schedule_info()

        assert info["enabled"] is False
        assert info["status"] == "Disabled (24/7)"
        assert info["is_daytime"] is True
        assert info["location"] == "Bartlett, TN"
        assert info["wake_time"] == "N/A"
        assert info["sleep_time"] == "N/A"

    def test_enabled_returns_all_keys(self, monkeypatch):
        """When night mode is enabled, all expected keys are present."""
        import config
        monkeypatch.setattr(config, "NIGHT_MODE_ENABLED", True)
        monkeypatch.setattr(config, "LOCATION_LAT", 35.1495)
        monkeypatch.setattr(config, "LOCATION_LNG", -89.8733)
        monkeypatch.setattr(config, "LOCATION_TIMEZONE", "America/Chicago")
        monkeypatch.setattr(config, "LOCATION_NAME", "Bartlett, TN")
        monkeypatch.setattr(config, "WAKE_BEFORE_SUNRISE_MIN", 30)
        monkeypatch.setattr(config, "SLEEP_AFTER_SUNSET_MIN", 30)

        tz = _make_tz()
        sunrise = _make_aware_dt(6, 0, tz)
        sunset = _make_aware_dt(20, 0, tz)

        mock_sun_result = {"sunrise": sunrise, "sunset": sunset}

        with patch("schedule.sun", return_value=mock_sun_result):
            with patch("schedule.datetime") as mock_dt:
                mock_dt.now.return_value = _make_aware_dt(12, 0, tz)
                mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
                info = sched.get_schedule_info()

        expected_keys = {
            "enabled", "status", "location",
            "wake_time", "sleep_time",
            "sunrise", "sunset", "is_daytime",
        }
        assert set(info.keys()) == expected_keys
        assert info["enabled"] is True
        assert info["is_daytime"] is True


class TestCacheBehavior:
    """Test that sun times are cached for the same day."""

    def test_same_day_returns_cached(self, monkeypatch):
        """Calling _get_sun_times() twice on the same day uses cached values."""
        import config
        monkeypatch.setattr(config, "LOCATION_LAT", 35.1495)
        monkeypatch.setattr(config, "LOCATION_LNG", -89.8733)
        monkeypatch.setattr(config, "LOCATION_TIMEZONE", "America/Chicago")
        monkeypatch.setattr(config, "LOCATION_NAME", "Bartlett, TN")
        monkeypatch.setattr(config, "WAKE_BEFORE_SUNRISE_MIN", 30)
        monkeypatch.setattr(config, "SLEEP_AFTER_SUNSET_MIN", 30)

        tz = _make_tz()
        sunrise = _make_aware_dt(6, 0, tz)
        sunset = _make_aware_dt(20, 0, tz)
        mock_sun_result = {"sunrise": sunrise, "sunset": sunset}

        test_date = date(2025, 6, 15)

        with patch("schedule.sun", return_value=mock_sun_result) as mock_sun:
            wake1, sleep1 = sched._get_sun_times(for_date=test_date)
            wake2, sleep2 = sched._get_sun_times(for_date=test_date)

        # sun() should only be called once (second call uses cache)
        assert mock_sun.call_count == 1
        assert wake1 == wake2
        assert sleep1 == sleep2

    def test_different_day_recalculates(self, monkeypatch):
        """Calling _get_sun_times() for a different day recalculates."""
        import config
        monkeypatch.setattr(config, "LOCATION_LAT", 35.1495)
        monkeypatch.setattr(config, "LOCATION_LNG", -89.8733)
        monkeypatch.setattr(config, "LOCATION_TIMEZONE", "America/Chicago")
        monkeypatch.setattr(config, "LOCATION_NAME", "Bartlett, TN")
        monkeypatch.setattr(config, "WAKE_BEFORE_SUNRISE_MIN", 30)
        monkeypatch.setattr(config, "SLEEP_AFTER_SUNSET_MIN", 30)

        tz = _make_tz()
        sunrise = _make_aware_dt(6, 0, tz)
        sunset = _make_aware_dt(20, 0, tz)
        mock_sun_result = {"sunrise": sunrise, "sunset": sunset}

        with patch("schedule.sun", return_value=mock_sun_result) as mock_sun:
            sched._get_sun_times(for_date=date(2025, 6, 15))
            sched._get_sun_times(for_date=date(2025, 6, 16))

        # sun() should be called twice (different days)
        assert mock_sun.call_count == 2
