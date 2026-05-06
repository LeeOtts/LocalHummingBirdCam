"""Automated watering scheduler — triggers the B-Hyve mister on a
sunrise-relative schedule.

Computes watering slots daily:
  first slot = sunrise + offset_hours
  repeat every interval_hours until sunset

Each slot triggers ``monitor.start_watering()`` for the configured run time.
Settings are read from ``config`` at each decision point so admin-panel
changes take effect immediately.
"""

import logging
import threading
import time
from datetime import datetime, timedelta, date

import config
from schedule import _get_sun_times
from analytics.patterns import get_weather

logger = logging.getLogger(__name__)


class WateringScheduler:
    """Background thread that waters the mister on a sunrise-relative schedule.

    Usage::

        scheduler = WateringScheduler(bhyve_monitor)
        scheduler.start()
        ...
        scheduler.stop()
    """

    def __init__(self, monitor):
        self._monitor = monitor
        self._running = False
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        # Cached schedule for today
        self._schedule_date: date | None = None
        self._slots: list[datetime] = []
        self._wake = threading.Event()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def enabled(self) -> bool:
        return config.BHYVE_SCHEDULE_ENABLED and config.BHYVE_SEASON_STARTED

    @property
    def todays_schedule(self) -> list[datetime]:
        """Return today's watering slot times (may be empty if disabled)."""
        self._ensure_schedule()
        with self._lock:
            return list(self._slots)

    @property
    def next_watering(self) -> datetime | None:
        """Return the next upcoming watering time, or None."""
        if not self.enabled:
            return None
        self._ensure_schedule()
        now = self._now()
        with self._lock:
            for slot in self._slots:
                if slot > now:
                    return slot
        return None

    def recalculate(self):
        """Force recalculation of today's schedule (call after admin changes)."""
        with self._lock:
            self._schedule_date = None  # invalidate cache
        self._wake.set()  # wake the sleep loop so it picks up new times

    def start(self):
        """Start the scheduler background thread."""
        self._running = True
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="watering-scheduler",
        )
        self._thread.start()
        logger.info("Watering scheduler started")

    def stop(self):
        self._running = False
        self._wake.set()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _now(self) -> datetime:
        """Current time, timezone-aware in the configured timezone."""
        try:
            import pytz
            tz = pytz.timezone(config.LOCATION_TIMEZONE)
            return datetime.now(tz)
        except ImportError:
            from zoneinfo import ZoneInfo
            tz = ZoneInfo(config.LOCATION_TIMEZONE)
            return datetime.now(tz)

    def _ensure_schedule(self):
        """Recalculate slots if the date changed or cache was invalidated."""
        today = date.today()
        with self._lock:
            if self._schedule_date == today:
                return
        self._build_schedule(today)

    def _build_schedule(self, today: date):
        """Compute watering slots for *today* from sunrise/sunset + config."""
        try:
            sunrise, sunset = _get_sun_times(today)
        except Exception:
            logger.warning("Watering scheduler: cannot get sun times", exc_info=True)
            with self._lock:
                self._schedule_date = today
                self._slots = []
            return

        offset = timedelta(hours=config.BHYVE_SCHEDULE_OFFSET_HOURS)
        interval = timedelta(hours=max(1, config.BHYVE_SCHEDULE_INTERVAL_HOURS))

        slots: list[datetime] = []
        t = sunrise + offset
        while t < sunset:
            slots.append(t)
            t += interval

        with self._lock:
            self._schedule_date = today
            self._slots = slots

        if slots:
            times_str = ", ".join(s.strftime("%I:%M %p") for s in slots)
            logger.info("Watering schedule for %s: %s", today.isoformat(), times_str)
        else:
            logger.info("Watering schedule for %s: no slots (offset=%sh, interval=%sh)",
                        today.isoformat(), config.BHYVE_SCHEDULE_OFFSET_HOURS,
                        config.BHYVE_SCHEDULE_INTERVAL_HOURS)

    def _run(self):
        """Main scheduler loop — sleep until each slot, then water."""
        while self._running:
            if not self.enabled:
                # Disabled or season not started — check again in 60s
                self._wake.wait(timeout=60)
                self._wake.clear()
                continue

            self._ensure_schedule()
            now = self._now()

            # Find the next slot
            next_slot = None
            with self._lock:
                for slot in self._slots:
                    if slot > now:
                        next_slot = slot
                        break

            if next_slot is None:
                # No more slots today — sleep until midnight + a small buffer
                tomorrow = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1, seconds=30)
                wait_secs = (tomorrow - now).total_seconds()
                logger.debug("Watering scheduler: no more slots today, sleeping %.0fs", wait_secs)
                with self._lock:
                    self._schedule_date = None  # force recalc tomorrow
                self._wake.wait(timeout=min(wait_secs, 3600))
                self._wake.clear()
                continue

            # Sleep until the slot
            wait_secs = (next_slot - now).total_seconds()
            if wait_secs > 0:
                logger.debug("Watering scheduler: next slot at %s (in %.0fs)",
                             next_slot.strftime("%I:%M %p"), wait_secs)
                self._wake.wait(timeout=wait_secs)
                self._wake.clear()
                if not self._running:
                    break

            # Re-check: is it still enabled? Did the schedule change?
            if not self.enabled:
                continue
            now = self._now()
            # Allow a 2-minute window around the slot
            if next_slot and abs((now - next_slot).total_seconds()) < 120:
                self._trigger_watering(next_slot)

    def _is_raining(self) -> bool:
        """Return True if current weather indicates precipitation."""
        weather = get_weather(config.LOCATION_LAT, config.LOCATION_LNG)
        if weather is None:
            return False
        condition = weather.get("condition", "")
        return condition in ("Rain", "Drizzle", "Thunderstorm")

    def _trigger_watering(self, slot: datetime):
        """Actually start the mister for this slot."""
        if self._monitor.is_spraying:
            logger.info("Watering scheduler: skipping %s — already spraying",
                        slot.strftime("%I:%M %p"))
            return

        if self._is_raining():
            logger.info("Watering scheduler: skipping %s — it's raining, no need to waste water",
                        slot.strftime("%I:%M %p"))
            return

        run_time = config.BHYVE_RUN_MINUTES
        logger.info("Watering scheduler: triggering watering at %s for %d min",
                     slot.strftime("%I:%M %p"), run_time)
        result = self._monitor.start_watering(run_time_minutes=run_time)
        if not result.get("ok"):
            logger.warning("Watering scheduler: start failed — %s", result.get("error"))
