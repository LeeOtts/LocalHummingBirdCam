"""B-Hyve sprinkler monitor and controller.

Connects to the Orbit b-Hyve cloud API, tracks when sprinkler zones are
actively watering, and can send commands to start/stop watering.

API details (reverse-engineered, unofficial):
  REST login  : POST https://api.orbitbhyve.com/v1/session
  REST devices: GET  https://api.orbitbhyve.com/v1/devices
  WebSocket   : wss://api.orbitbhyve.com/v1/events
"""

import json
import logging
import threading
import time
from datetime import datetime, timezone

import requests

logger = logging.getLogger(__name__)

_API_BASE = "https://api.orbitbhyve.com"
_LOGIN_PATH = "/v1/session"
_DEVICES_PATH = "/v1/devices"
_WS_URL = "wss://api.orbitbhyve.com/v1/events"

_PING_INTERVAL = 25     # seconds — keep-alive ping to the WebSocket
_RECONNECT_MIN = 10     # initial delay between reconnect attempts (seconds)
_RECONNECT_MAX = 300    # cap backoff at 5 minutes

# WebSocket event names that affect watering state
_EV_WATERING_IN_PROGRESS = "watering_in_progress_notification"
_EV_WATERING_COMPLETE = "watering_complete"
_EV_DEVICE_IDLE = "device_idle"
_EV_CHANGE_MODE = "change_mode"

_HEADERS = {
    "orbit-api-key": "null",
    "Content-Type": "application/json",
    "Accept": "application/json",
    "User-Agent": "Hummingbird-Cam/1.0",
}


class BHyveMonitor:
    """Monitors Orbit b-Hyve sprinkler activity via the cloud WebSocket API.

    Usage::

        monitor = BHyveMonitor(email, password)
        monitor.start()        # starts background thread
        ...
        if monitor.is_spraying:
            print(monitor.active_zones)
        monitor.stop()
    """

    def __init__(self, email: str, password: str, watch_station: int | None = None,
                 on_spray_start=None, on_spray_stop=None):
        self._email = email
        self._password = password
        self._on_spray_start = on_spray_start  # callback(zone: str|None)
        self._on_spray_stop = on_spray_stop    # callback(zone: str|None)
        # If set, only report is_spraying=True when this station number is active.
        # None means any active station counts.
        self._watch_station = watch_station
        self._token: str | None = None
        self._user_id: str | None = None
        self._device_id: str | None = None
        self._lock = threading.Lock()
        # device_id -> {"mode": str, "station": int|None, "started_at": float}
        self._active: dict[str, dict] = {}
        self._ws = None
        self._running = False
        self._thread: threading.Thread | None = None
        self.connected = False
        self.last_event: str | None = None
        self.last_event_time: float = 0.0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def is_spraying(self) -> bool:
        """True if the watched station (or any station) is currently watering."""
        with self._lock:
            if not self._active:
                return False
            if self._watch_station is None:
                return True
            # If station is None (API didn't tell us which zone), assume it
            # matches — better to show the overlay than silently miss it.
            return any(
                info.get("station") is None
                or info.get("station") == self._watch_station
                for info in self._active.values()
            )

    @property
    def active_zones(self) -> list[dict]:
        """List of dicts describing each actively-watering device/zone."""
        with self._lock:
            result = []
            for device_id, info in self._active.items():
                result.append({"device_id": device_id, **info})
            return result

    @property
    def device_id(self) -> str | None:
        """The discovered sprinkler device ID, or None if not yet discovered."""
        return self._device_id

    def start_watering(self, station: int | None = None,
                       run_time_minutes: int = 5) -> dict:
        """Send a manual watering command for the given station/duration.

        Returns a dict with ``ok`` (bool) and optional ``error`` (str).
        """
        if not self.connected or self._ws is None:
            return {"ok": False, "error": "Not connected to B-Hyve"}
        if not self._device_id:
            return {"ok": False, "error": "No device discovered"}

        import config as _cfg
        max_run = getattr(_cfg, "BHYVE_MAX_RUN_MINUTES", 30)
        run_time_minutes = max(1, min(run_time_minutes, max_run))
        station = station or self._watch_station or 1

        payload = {
            "event": "change_mode",
            "mode": "manual",
            "device_id": self._device_id,
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "stations": [{"station": station, "run_time": run_time_minutes}],
        }
        try:
            self._ws.send(json.dumps(payload))
            logger.info(
                "B-Hyve: start watering — device=%s station=%s run_time=%d min",
                self._device_id, station, run_time_minutes,
            )
            return {"ok": True, "device_id": self._device_id,
                    "station": station, "run_time": run_time_minutes}
        except Exception as exc:
            logger.error("B-Hyve: start watering failed: %s", exc)
            return {"ok": False, "error": str(exc)}

    def stop_watering(self) -> dict:
        """Stop any manual watering by switching back to auto mode.

        Returns a dict with ``ok`` (bool) and optional ``error`` (str).
        """
        if not self.connected or self._ws is None:
            return {"ok": False, "error": "Not connected to B-Hyve"}
        if not self._device_id:
            return {"ok": False, "error": "No device discovered"}

        payload = {
            "event": "change_mode",
            "mode": "auto",
            "device_id": self._device_id,
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        try:
            self._ws.send(json.dumps(payload))
            logger.info("B-Hyve: stop watering — device=%s", self._device_id)
            return {"ok": True, "device_id": self._device_id}
        except Exception as exc:
            logger.error("B-Hyve: stop watering failed: %s", exc)
            return {"ok": False, "error": str(exc)}

    def start(self):
        """Start the background monitor thread."""
        self._running = True
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="bhyve-monitor"
        )
        self._thread.start()
        logger.info("B-Hyve monitor started")

    def stop(self):
        """Stop the monitor and close the WebSocket."""
        self._running = False
        ws = self._ws
        if ws:
            try:
                ws.close()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Internal — login
    # ------------------------------------------------------------------

    def _login(self) -> bool:
        """POST to the b-Hyve session endpoint; store orbit_session_token."""
        try:
            resp = requests.post(
                f"{_API_BASE}{_LOGIN_PATH}",
                json={"session": {"email": self._email, "password": self._password}},
                headers=_HEADERS,
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            # Token may be at top level or nested under "user"
            token = (
                data.get("orbit_session_token")
                or data.get("user", {}).get("orbit_session_token")
            )
            if token:
                self._token = token
                self._user_id = (
                    data.get("user_id")
                    or data.get("user", {}).get("id")
                )
                logger.info("B-Hyve: login successful (user_id=%s)", self._user_id)
                return True
            logger.error("B-Hyve: login response missing orbit_session_token")
            return False
        except requests.HTTPError as exc:
            logger.error("B-Hyve: login HTTP error %s", exc.response.status_code)
        except Exception:
            logger.exception("B-Hyve: login failed")
        return False

    def _discover_devices(self):
        """Fetch device list from REST API and store the sprinkler device ID."""
        if not self._token:
            return
        try:
            headers = {**_HEADERS, "orbit-session-token": self._token}
            params = {}
            if self._user_id:
                params["user_id"] = self._user_id
            resp = requests.get(
                f"{_API_BASE}{_DEVICES_PATH}",
                headers=headers,
                params=params,
                timeout=15,
            )
            resp.raise_for_status()
            devices = resp.json()
            for dev in devices:
                if dev.get("type") == "sprinkler_timer":
                    self._device_id = dev["id"]
                    logger.info(
                        "B-Hyve: discovered device '%s' (id=%s)",
                        dev.get("name", "unknown"), self._device_id,
                    )
                    return
            logger.warning("B-Hyve: no sprinkler_timer device found in %d devices", len(devices))
        except Exception:
            logger.warning("B-Hyve: device discovery failed", exc_info=True)

    # ------------------------------------------------------------------
    # Internal — WebSocket
    # ------------------------------------------------------------------

    def _run(self):
        """Main loop: authenticate then maintain the WebSocket connection."""
        delay = _RECONNECT_MIN
        while self._running:
            if not self._token:
                if not self._login():
                    logger.warning(
                        "B-Hyve: login failed, retrying in %ds", delay
                    )
                    time.sleep(delay)
                    delay = min(delay * 2, _RECONNECT_MAX)
                    continue
                # Discover devices after first successful login
                if not self._device_id:
                    self._discover_devices()

            was_connected = self.connected
            try:
                self._connect_ws()
            except Exception:
                logger.exception("B-Hyve: WebSocket exception")

            self.connected = False
            # Clear active zones on disconnect — we don't know the real state
            with self._lock:
                self._active.clear()

            if self._running:
                # Reset backoff after a successful session; increase on repeated failures
                if was_connected:
                    delay = _RECONNECT_MIN
                logger.debug(
                    "B-Hyve: WebSocket disconnected — reconnecting in %ds",
                    delay,
                )
                time.sleep(delay)
                if not was_connected:
                    delay = min(delay * 2, _RECONNECT_MAX)

    def _connect_ws(self):
        """Open WebSocket, authenticate, and block until disconnected."""
        try:
            import websocket
        except ImportError:
            logger.error(
                "B-Hyve: 'websocket-client' package not installed. "
                "Run: pip install websocket-client"
            )
            time.sleep(60)
            return

        def on_open(ws):
            self.connected = True
            logger.info("B-Hyve: WebSocket connected")
            ws.send(
                json.dumps(
                    {"event": "app_connection", "orbit_session_token": self._token}
                )
            )
            threading.Thread(
                target=self._ping_loop, args=(ws,), daemon=True
            ).start()

        def on_message(ws, raw):
            try:
                self._handle_event(json.loads(raw))
            except json.JSONDecodeError:
                logger.debug("B-Hyve: non-JSON message: %s", raw[:200])
            except Exception:
                logger.warning("B-Hyve: error handling message", exc_info=True)

        def on_error(ws, error):
            logger.debug("B-Hyve: WebSocket error: %s", error)
            self.connected = False

        def on_close(ws, code, msg):
            self.connected = False
            logger.debug("B-Hyve: WebSocket closed (code=%s)", code)

        self._ws = websocket.WebSocketApp(
            _WS_URL,
            on_open=on_open,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
        )
        self._ws.run_forever()

    def _ping_loop(self, ws):
        """Send keep-alive pings every 25 seconds."""
        while self.connected and self._running:
            time.sleep(_PING_INTERVAL)
            if self.connected and self._running:
                try:
                    ws.send(json.dumps({"event": "ping"}))
                except Exception:
                    break

    def _handle_event(self, data: dict):
        """Update internal watering state from a WebSocket event."""
        event = data.get("event", "")
        device_id = data.get("device_id") or "unknown"
        self.last_event = event
        self.last_event_time = time.time()

        if event == _EV_WATERING_IN_PROGRESS:
            mode = data.get("mode", "auto")
            # Station number can appear in multiple places depending on firmware/event
            program = data.get("program") or {}
            station = (
                data.get("current_station")
                or (program.get("current_station") if isinstance(program, dict) else None)
            )
            # Coerce to int if it came back as a string
            if station is not None:
                try:
                    station = int(station)
                except (ValueError, TypeError):
                    station = None
            logger.debug("B-Hyve: raw event payload keys=%s station=%s", list(data.keys()), station)
            with self._lock:
                if device_id in self._active:
                    # Already tracking — update silently to avoid duplicate logs
                    self._active[device_id]["station"] = station
                    return
                self._active[device_id] = {
                    "mode": mode,
                    "station": station,
                    "started_at": time.time(),
                }
            logger.info(
                "B-Hyve: watering started — device=%s mode=%s station=%s",
                device_id, mode, station,
            )
            if self._on_spray_start:
                try:
                    self._on_spray_start(str(station) if station else None)
                except Exception:
                    logger.debug("Spray start callback failed", exc_info=True)

        elif event in (_EV_WATERING_COMPLETE, _EV_DEVICE_IDLE):
            zone = None
            with self._lock:
                info = self._active.pop(device_id, None)
                if info:
                    zone = str(info.get("station")) if info.get("station") else None
            logger.info("B-Hyve: watering stopped — device=%s event=%s", device_id, event)
            if self._on_spray_stop:
                try:
                    self._on_spray_stop(zone)
                except Exception:
                    logger.debug("Spray stop callback failed", exc_info=True)

        elif event == _EV_CHANGE_MODE:
            if data.get("mode") == "off":
                with self._lock:
                    self._active.pop(device_id, None)
                logger.info("B-Hyve: device %s mode → off", device_id)
