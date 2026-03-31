"""
Night mode scheduler — pauses detection between sunset and sunrise.

Uses the astral library to calculate sunrise/sunset times locally
(no internet needed). Recalculates daily.
"""

import logging
import threading
from datetime import datetime, timedelta, date

from astral import LocationInfo
from astral.sun import sun

import config

logger = logging.getLogger(__name__)

# Cache today's sun times so we don't recalculate every frame
_cached_date = None
_cached_sunrise = None
_cached_sunset = None
_cache_lock = threading.Lock()


def _get_sun_times(for_date=None):
    """Calculate sunrise/sunset for the configured location."""
    global _cached_date, _cached_sunrise, _cached_sunset

    today = for_date or date.today()

    with _cache_lock:
        # Return cached if same day
        if _cached_date == today:
            return _cached_sunrise, _cached_sunset

        try:
            import pytz
            tz = pytz.timezone(config.LOCATION_TIMEZONE)
        except ImportError:
            from zoneinfo import ZoneInfo
            tz = ZoneInfo(config.LOCATION_TIMEZONE)

        location = LocationInfo(
            name=config.LOCATION_NAME,
            region="USA",
            timezone=config.LOCATION_TIMEZONE,
            latitude=config.LOCATION_LAT,
            longitude=config.LOCATION_LNG,
        )

        s = sun(location.observer, date=today, tzinfo=tz)

        # Apply wake/sleep offsets
        wake_time = s["sunrise"] - timedelta(minutes=config.WAKE_BEFORE_SUNRISE_MIN)
        sleep_time = s["sunset"] + timedelta(minutes=config.SLEEP_AFTER_SUNSET_MIN)

        _cached_date = today
        _cached_sunrise = wake_time
        _cached_sunset = sleep_time

        logger.info(
            "Sun times for %s (%s): wake at %s, sleep at %s (sunrise %s, sunset %s)",
            today.isoformat(),
            config.LOCATION_NAME,
            wake_time.strftime("%H:%M"),
            sleep_time.strftime("%H:%M"),
            s["sunrise"].strftime("%H:%M"),
            s["sunset"].strftime("%H:%M"),
        )

        return wake_time, sleep_time


def is_daytime():
    """
    Returns True if detection should be active (daytime).
    Returns True always if night mode is disabled.
    """
    if not config.NIGHT_MODE_ENABLED:
        return True

    try:
        wake_time, sleep_time = _get_sun_times()

        # Make now timezone-aware to match
        try:
            import pytz
            tz = pytz.timezone(config.LOCATION_TIMEZONE)
            now = datetime.now(tz)
        except ImportError:
            from zoneinfo import ZoneInfo
            tz = ZoneInfo(config.LOCATION_TIMEZONE)
            now = datetime.now(tz)

        return wake_time <= now <= sleep_time

    except Exception:
        logger.exception("Failed to calculate sun times — defaulting to active")
        return True


def get_schedule_info():
    """Return schedule info dict for the dashboard."""
    if not config.NIGHT_MODE_ENABLED:
        return {
            "enabled": False,
            "status": "Disabled (24/7)",
            "location": config.LOCATION_NAME,
            "wake_time": "N/A",
            "sleep_time": "N/A",
            "sunrise": "N/A",
            "sunset": "N/A",
            "is_daytime": True,
        }

    try:
        wake_time, sleep_time = _get_sun_times()

        try:
            import pytz
            tz = pytz.timezone(config.LOCATION_TIMEZONE)
            now = datetime.now(tz)
        except ImportError:
            from zoneinfo import ZoneInfo
            tz = ZoneInfo(config.LOCATION_TIMEZONE)
            now = datetime.now(tz)

        daytime = wake_time <= now <= sleep_time

        # Get raw sunrise/sunset (without offset)
        location = LocationInfo(
            name=config.LOCATION_NAME,
            region="USA",
            timezone=config.LOCATION_TIMEZONE,
            latitude=config.LOCATION_LAT,
            longitude=config.LOCATION_LNG,
        )
        s = sun(location.observer, date=date.today(), tzinfo=tz)

        return {
            "enabled": True,
            "status": "Awake" if daytime else "Sleeping",
            "location": config.LOCATION_NAME,
            "wake_time": wake_time.strftime("%I:%M %p"),
            "sleep_time": sleep_time.strftime("%I:%M %p"),
            "sunrise": s["sunrise"].strftime("%I:%M %p"),
            "sunset": s["sunset"].strftime("%I:%M %p"),
            "is_daytime": daytime,
        }

    except Exception:
        logger.exception("Failed to get schedule info")
        return {
            "enabled": True,
            "status": "Error",
            "location": config.LOCATION_NAME,
            "wake_time": "Error",
            "sleep_time": "Error",
            "sunrise": "Error",
            "sunset": "Error",
            "is_daytime": True,
        }
