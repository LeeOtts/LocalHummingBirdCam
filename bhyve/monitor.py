"""B-Hyve sprinkler monitor — WebSocket-based, read-only.

Connects to the Orbit b-Hyve cloud API and tracks when sprinkler zones are
actively watering.  No commands are sent to the device; this is purely a
listener so the hummingbird dashboard can show a "Sprinkler: SPRAYING / IDLE"
indicator.

API details (reverse-engineered, unofficial):
  REST login : POST https://api.orbitbhyve.com/v1/session
  WebSocket  : wss://api.orbitbhyve.com/v1/events
"""

import json
import logging
import threading
import time

import requests

logger = logging.getLogger(__name__)

_API_BASE = "https://api.orbitbhyve.com"
_LOGIN_PATH = "/v1/session"
_WS_URL = "wss://api.orbitbhyve.com/v1/events"

_PING_INTERVAL = 25     # seconds — keep-alive ping to the WebSocket
_RECONNECT_DELAY = 10   # seconds between reconnect attempts

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

    def __init__(self, email: str, password: str, watch_station: int | None = None):
        self._email = email
        self._password = password
        # If set, only report is_spraying=True when this station number is active.
        # None means any active station counts.
        self._watch_station = watch_station
        self._token: str | None = None
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
            return any(
                info.get("station") == self._watch_station
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
                logger.info("B-Hyve: login successful")
                return True
            logger.error("B-Hyve: login response missing orbit_session_token")
            return False
        except requests.HTTPError as exc:
            logger.error("B-Hyve: login HTTP error %s", exc.response.status_code)
        except Exception:
            logger.exception("B-Hyve: login failed")
        return False

    # ------------------------------------------------------------------
    # Internal — WebSocket
    # ------------------------------------------------------------------

    def _run(self):
        """Main loop: authenticate then maintain the WebSocket connection."""
        while self._running:
            if not self._token:
                if not self._login():
                    logger.warning(
                        "B-Hyve: login failed, retrying in %ds", _RECONNECT_DELAY
                    )
                    time.sleep(_RECONNECT_DELAY)
                    continue

            try:
                self._connect_ws()
            except Exception:
                logger.exception("B-Hyve: WebSocket exception")

            self.connected = False
            # Clear active zones on disconnect — we don't know the real state
            with self._lock:
                self._active.clear()

            if self._running:
                logger.info(
                    "B-Hyve: WebSocket disconnected — reconnecting in %ds",
                    _RECONNECT_DELAY,
                )
                time.sleep(_RECONNECT_DELAY)

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
            logger.warning("B-Hyve: WebSocket error: %s", error)
            self.connected = False

        def on_close(ws, code, msg):
            self.connected = False
            logger.info("B-Hyve: WebSocket closed (code=%s)", code)

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
            program = data.get("program") or {}
            station = program.get("current_station") if isinstance(program, dict) else None
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

        elif event in (_EV_WATERING_COMPLETE, _EV_DEVICE_IDLE):
            with self._lock:
                self._active.pop(device_id, None)
            logger.info("B-Hyve: watering stopped — device=%s event=%s", device_id, event)

        elif event == _EV_CHANGE_MODE:
            if data.get("mode") == "off":
                with self._lock:
                    self._active.pop(device_id, None)
                logger.info("B-Hyve: device %s mode → off", device_id)
