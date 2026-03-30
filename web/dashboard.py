"""Local web dashboard for monitoring the Backyard Hummers system."""

import hashlib
import json
import logging
import os
import threading
import time as _time
from datetime import datetime
from pathlib import Path
from queue import Queue, Empty

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from pytz import timezone as _pytz_tz
    ZoneInfo = lambda key: _pytz_tz(key)

from flask import Flask, Response, render_template, send_from_directory, request, redirect, url_for, session, jsonify

import config

_local_tz = ZoneInfo(config.LOCATION_TIMEZONE)
from schedule import get_schedule_info

logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.urandom(24)  # ephemeral — sessions last until restart

# Reference to the monitor instance (set by main.py)
_monitor = None

# Routes that are always public (live camera feed, gallery, SSE events)
_PUBLIC_ROUTES = {"/feed", "/feed/audio", "/gallery", "/api/events"}

# Simple in-memory rate limiter for failed auth attempts
_AUTH_FAIL_WINDOW = 300  # 5 minutes
_AUTH_FAIL_MAX = 5
_auth_failures: dict[str, list[float]] = {}  # ip -> [timestamps]

# Cached weather (avoid hitting OpenWeatherMap API on every 5s status poll)
_weather_cache: dict = {"data": None, "fetched_at": 0}
_WEATHER_CACHE_TTL = 600  # 10 minutes


def _get_cached_weather():
    """Return cached weather data, refreshing at most once every 10 minutes."""
    import time
    if not config.OPENWEATHERMAP_API_KEY:
        return None
    now = time.time()
    if _weather_cache["data"] is not None and (now - _weather_cache["fetched_at"]) < _WEATHER_CACHE_TTL:
        return _weather_cache["data"]
    try:
        from analytics.patterns import get_weather
        data = get_weather(config.LOCATION_LAT, config.LOCATION_LNG)
        if data:
            _weather_cache["data"] = data
            _weather_cache["fetched_at"] = now
        return data
    except Exception:
        return _weather_cache["data"]  # return stale data on error


@app.before_request
def _enforce_auth():
    """Enforce HTTP Basic Auth when WEB_PASSWORD is configured, with rate limiting."""
    password = config.WEB_PASSWORD
    if not password:
        return  # auth disabled — open access (default)
    if request.path in _PUBLIC_ROUTES or request.path.startswith("/gallery"):
        return  # public routes remain accessible

    # Check rate limit before processing auth
    ip = request.remote_addr or "unknown"
    now = _time.time()
    if ip in _auth_failures:
        # Prune old entries
        _auth_failures[ip] = [t for t in _auth_failures[ip] if now - t < _AUTH_FAIL_WINDOW]
        if len(_auth_failures[ip]) >= _AUTH_FAIL_MAX:
            return Response("Too many failed login attempts. Try again later.", 429)

    auth = request.authorization
    if not auth or auth.password != password:
        # Track failed attempt
        _auth_failures.setdefault(ip, []).append(now)
        return Response(
            "Authentication required",
            401,
            {"WWW-Authenticate": 'Basic realm="Backyard Hummers"'},
        )


def _update_env_value(key: str, value) -> bool:
    """Update or add a key=value in the .env file so it persists across restarts.
    
    Uses atomic write (temp file + rename) to prevent corruption if process crashes mid-write.
    """
    env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
    tmp_path = env_path + ".tmp"
    try:
        lines = []
        found = False
        if os.path.exists(env_path):
            with open(env_path, "r") as f:
                lines = f.readlines()
            for i, line in enumerate(lines):
                stripped = line.strip().lstrip("# ")
                if stripped.startswith(f"{key}="):
                    lines[i] = f"{key}={value}\n"
                    found = True
                    break
        if not found:
            lines.append(f"{key}={value}\n")
        
        # Atomic write: temp file then replace (safe from crash mid-write)
        with open(tmp_path, "w") as f:
            f.writelines(lines)
        os.replace(tmp_path, env_path)  # Atomic on Windows/POSIX
        return True
    except OSError:
        logger.exception("Failed to save %s to .env", key)
        # Clean up temp file if it exists
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass
        return False


def set_monitor(monitor):
    global _monitor
    _monitor = monitor





def _get_tailscale_status():
    """Query Tailscale daemon for connection status. Returns None if disabled."""
    if not config.TAILSCALE_ENABLED:
        return None
    try:
        import subprocess as _ts_sp
        result = _ts_sp.run(
            ["tailscale", "status", "--json"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return {"connected": False, "ip": None, "hostname": None}
        import json as _ts_json
        data = _ts_json.loads(result.stdout)
        self_node = data.get("Self", {})
        ts_ips = self_node.get("TailscaleIPs", [])
        hostname = self_node.get("DNSName", "").rstrip(".")
        return {
            "connected": data.get("BackendState") == "Running",
            "ip": ts_ips[0] if ts_ips else None,
            "hostname": hostname,
            "version": data.get("Version", "unknown"),
        }
    except FileNotFoundError:
        return {"connected": False, "ip": None, "hostname": None}
    except Exception:
        return {"connected": False, "ip": None, "hostname": None}


def _get_status():
    """Gather system status info."""
    import time

    running = _monitor is not None and _monitor.running
    in_cooldown = False
    detections_today = 0
    posts_today = 0
    uptime = "N/A"

    if _monitor:
        in_cooldown = not _monitor._cooldown_elapsed()
        posts_today = _monitor.poster._posts_today
        detections_today = getattr(_monitor, '_detections_today', 0)
        if hasattr(_monitor, '_start_time'):
            elapsed = int(time.time() - _monitor._start_time)
            hours, remainder = divmod(elapsed, 3600)
            minutes, seconds = divmod(remainder, 60)
            uptime = f"{hours}h {minutes}m {seconds}s"

    total_clips = len(list(config.CLIPS_DIR.glob("*.mp4"))) if config.CLIPS_DIR.exists() else 0

    class Status:
        pass

    s = Status()
    s.running = running
    s.in_cooldown = in_cooldown
    s.detections_today = detections_today
    s.posts_today = posts_today
    s.total_clips = total_clips
    s.uptime = uptime
    if _monitor and _monitor.camera.is_available:
        s.camera_type = _monitor.camera.camera_type.upper()
    elif _monitor and _monitor.camera.error:
        s.camera_type = "ERROR"
    else:
        s.camera_type = "N/A"
    s.camera_error = _monitor.camera.error if _monitor else None
    s.camera_rotation = getattr(_monitor.camera, 'rotation', 0) if _monitor else 0
    s.test_mode = _monitor.test_mode if _monitor else True
    s.rejected_today = getattr(_monitor, '_rejected_today', 0) if _monitor else 0

    # Training sample count (files are in subdirectories: training/hummingbird/, training/not_hummingbird/)
    training_dir = config.TRAINING_DIR
    if training_dir.exists():
        s.training_count = len(list(training_dir.glob("**/*.jpg")))
    else:
        s.training_count = 0

    # Schedule info
    s.schedule = get_schedule_info()

    # Git commit
    try:
        import subprocess
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
            cwd=str(config.BASE_DIR),
        )
        s.git_commit = result.stdout.strip() if result.returncode == 0 else "unknown"
    except Exception:
        s.git_commit = "unknown"

    # Weather (cached — fetched at most once every 10 minutes)
    s.weather = _get_cached_weather()

    # Pi CPU temperature
    try:
        import subprocess
        result = subprocess.run(
            ["vcgencmd", "measure_temp"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            # Output: "temp=45.6'C"
            temp_str = result.stdout.strip().replace("temp=", "").replace("'C", "")
            s.cpu_temp = float(temp_str)
            s.cpu_temp_str = f"{s.cpu_temp:.1f}°C"
        else:
            s.cpu_temp = None
            s.cpu_temp_str = "N/A"
    except Exception:
        s.cpu_temp = None
        s.cpu_temp_str = "N/A"

    # SD card / disk usage
    try:
        import shutil
        usage = shutil.disk_usage("/")
        s.disk_total_gb = usage.total / (1024 ** 3)
        s.disk_used_gb = usage.used / (1024 ** 3)
        s.disk_free_gb = usage.free / (1024 ** 3)
        s.disk_percent = (usage.used / usage.total) * 100
        s.disk_str = f"{s.disk_used_gb:.1f}/{s.disk_total_gb:.1f} GB"
    except Exception:
        s.disk_total_gb = 0
        s.disk_used_gb = 0
        s.disk_free_gb = 0
        s.disk_percent = 0
        s.disk_str = "N/A"

    # RAM usage
    try:
        with open("/proc/meminfo") as f:
            meminfo = {}
            for line in f:
                parts = line.split()
                if len(parts) >= 2:
                    meminfo[parts[0].rstrip(":")] = int(parts[1])
        total_mb = meminfo.get("MemTotal", 0) / 1024
        available_mb = meminfo.get("MemAvailable", 0) / 1024
        used_mb = total_mb - available_mb
        s.ram_total_mb = total_mb
        s.ram_used_mb = used_mb
        s.ram_percent = (used_mb / total_mb * 100) if total_mb > 0 else 0
        s.ram_str = f"{used_mb:.0f}/{total_mb:.0f} MB"
    except Exception:
        s.ram_total_mb = 0
        s.ram_used_mb = 0
        s.ram_percent = 0
        s.ram_str = "N/A"

    # Tailscale VPN status
    s.tailscale = _get_tailscale_status()

    return s


def _get_recent_clips(limit=6):
    """Get the most recent clips with their captions."""
    if not config.CLIPS_DIR.exists():
        return []

    mp4s = sorted(config.CLIPS_DIR.glob("*.mp4"), key=os.path.getmtime, reverse=True)[:limit]
    clips = []
    for p in mp4s:
        size_mb = p.stat().st_size / 1_048_576
        mtime = datetime.fromtimestamp(p.stat().st_mtime, tz=_local_tz).strftime("%Y-%m-%d %H:%M:%S")

        # Load caption if it exists
        caption_path = p.with_suffix(".txt")
        caption = ""
        if caption_path.exists():
            try:
                caption = caption_path.read_text().strip()
            except OSError:
                pass

        clips.append({
            "name": p.name,
            "size": f"{size_mb:.1f} MB",
            "time": mtime,
            "caption": caption,
        })
    return clips


def _get_recent_logs(limit=50):
    """Read the last N lines from the log file using tail (avoids loading full 5MB file)."""
    log_file = config.LOGS_DIR / "hummingbird.log"
    if not log_file.exists():
        return []

    try:
        import subprocess
        result = subprocess.run(
            ["tail", "-n", str(limit), str(log_file)],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return []
        lines = result.stdout.strip().split("\n")
    except Exception:
        return []

    output = []
    for line in reversed(lines):  # Newest first
        if "[ERROR]" in line:
            css = "log-error"
        elif "[WARNING]" in line:
            css = "log-warn"
        else:
            css = "log-info"
        output.append({"text": line, "css_class": css})
    return output


@app.route("/")
def dashboard():
    return render_template(
        "dashboard.html",
        status=_get_status(),
        clips=_get_recent_clips(),
        logs=_get_recent_logs(),
        video_width=config.VIDEO_WIDTH,
    )


@app.route("/clips/<filename>")
def serve_clip(filename):
    if "/" in filename or "\\" in filename or ".." in filename:
        return {"ok": False, "error": "Invalid filename"}, 400
    resp = send_from_directory(str(config.CLIPS_DIR), filename)
    resp.cache_control.max_age = 86400 * 30  # cache 30 days — clips don't change
    resp.cache_control.public = True
    return resp


@app.route("/static/<filename>")
def serve_static(filename):
    if "/" in filename or "\\" in filename or ".." in filename:
        return {"ok": False, "error": "Invalid filename"}, 400
    # Check web/static/ first, then fall back to repo root (e.g. banner image)
    static_dir = os.path.join(os.path.dirname(__file__), "static")
    if os.path.isfile(os.path.join(str(config.BASE_DIR), filename)):
        resp = send_from_directory(str(config.BASE_DIR), filename)
    else:
        resp = send_from_directory(static_dir, filename)
    resp.cache_control.max_age = 3600  # cache 1 hour (pick up banner changes sooner)
    resp.cache_control.public = True
    return resp


@app.route("/api/clips/<filename>", methods=["DELETE"])
def delete_clip(filename):
    """Delete a single clip and its caption file."""
    # Sanitize filename — only allow expected patterns
    if "/" in filename or "\\" in filename or ".." in filename:
        return {"ok": False, "error": "Invalid filename"}, 400

    mp4_path = config.CLIPS_DIR / filename
    if not mp4_path.exists():
        return {"ok": False, "error": "Clip not found"}, 404

    try:
        mp4_path.unlink()
        # Also delete caption and any h264 leftover
        caption_path = mp4_path.with_suffix(".txt")
        h264_path = mp4_path.with_suffix(".h264")
        caption_path.unlink(missing_ok=True)
        h264_path.unlink(missing_ok=True)

        logger.info("Deleted clip: %s", filename)
        return {"ok": True, "deleted": filename}
    except OSError as e:
        logger.error("Failed to delete %s: %s", filename, e)
        return {"ok": False, "error": str(e)}, 500


@app.route("/api/clips", methods=["DELETE"])
def delete_all_clips():
    """Delete all clips and their captions."""
    if not config.CLIPS_DIR.exists():
        return {"ok": True, "deleted": 0}

    count = 0
    try:
        for f in config.CLIPS_DIR.iterdir():
            if f.suffix in (".mp4", ".h264", ".txt"):
                f.unlink()
                count += 1

        logger.info("Deleted all clips (%d files)", count)
        return {"ok": True, "deleted": count}
    except OSError as e:
        logger.error("Failed to delete all clips: %s", e)
        return {"ok": False, "error": str(e)}, 500


@app.route("/feed")
def live_feed():
    """Serve a live MJPEG stream from the camera."""
    if _monitor is None or not _monitor.running or not _monitor.camera.is_available:
        return Response("Camera not available", status=503)

    def generate_frames():
        import cv2
        import time
        while _monitor and _monitor.running:
            try:
                # Use full-res frame for public feed; fall back to lores
                frame = _monitor.camera.get_full_res_frame()
                if frame is None:
                    frame = _monitor.camera.capture_lores_array()
                    if frame.shape[0] > frame.shape[1] * 1.2:
                        frame = cv2.cvtColor(frame, cv2.COLOR_YUV2BGR_I420)
                _, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n\r\n" + jpeg.tobytes() + b"\r\n"
                )
            except Exception:
                break
            time.sleep(0.1)  # ~10 fps for the web feed

    return Response(
        generate_frames(),
        mimetype="multipart/x-mixed-replace; boundary=frame",
    )


@app.route("/feed/audio")
def live_audio():
    """Stream live audio from the USB mic as MP3."""
    import subprocess as sp

    # Detect audio device
    from camera.recorder import ClipRecorder
    device = ClipRecorder._detect_audio_device()
    device = device.replace("hw:", "plughw:")

    def generate_audio():
        # arecord -> ffmpeg -> mp3 stream (no shell=True to prevent injection)
        arecord = sp.Popen(
            ["arecord", "-D", device, "-f", "S16_LE", "-r", "16000", "-c", "2", "-t", "raw"],
            stdout=sp.PIPE,
            stderr=sp.DEVNULL,
        )
        ffmpeg = sp.Popen(
            ["ffmpeg", "-f", "s16le", "-ar", "16000", "-ac", "2", "-i", "-", "-f", "mp3", "-ab", "64k", "-"],
            stdin=arecord.stdout,
            stdout=sp.PIPE,
            stderr=sp.DEVNULL,
        )
        arecord.stdout.close()  # Allow arecord to receive SIGPIPE if ffmpeg exits
        try:
            while True:
                chunk = ffmpeg.stdout.read(4096)
                if not chunk:
                    break
                yield chunk
        finally:
            ffmpeg.kill()
            ffmpeg.wait()
            arecord.kill()
            arecord.wait()

    return Response(
        generate_audio(),
        mimetype="audio/mpeg",
        headers={"Cache-Control": "no-cache"},
    )


@app.route("/api/audio/test")
def test_mic():
    """Record 3 seconds from the mic and return the WAV file."""
    import subprocess as sp
    import tempfile

    from camera.recorder import ClipRecorder
    device = ClipRecorder._detect_audio_device()
    device = device.replace("hw:", "plughw:")

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_path = tmp.name

        result = sp.run(
            ["arecord", "-D", device, "-f", "S16_LE", "-r", "16000", "-c", "2", "-d", "3", tmp_path],
            capture_output=True, text=True, timeout=10,
        )

        if result.returncode != 0:
            logger.warning("Mic test failed: %s", result.stderr[-200:])
            return {"ok": False, "error": "arecord failed"}, 500

        from flask import send_file, after_this_request

        @after_this_request
        def _cleanup(response):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            return response

        return send_file(tmp_path, mimetype="audio/wav", as_attachment=False)

    except Exception as e:
        logger.exception("Mic test error")
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        return {"ok": False, "error": str(e)}, 500


@app.route("/api/camera/rotate", methods=["POST"])
def rotate_camera():
    """Change camera rotation at runtime."""
    if _monitor is None:
        return {"ok": False, "error": "Monitor not running"}, 503

    data = request.get_json() or {}
    angle = data.get("rotation", 0)
    if angle not in (0, 90, 180, 270):
        return {"ok": False, "error": "Invalid rotation (0, 90, 180, 270)"}, 400

    _monitor.camera.rotation = angle
    config.CAMERA_ROTATION = angle
    _update_env_value("CAMERA_ROTATION", angle)
    logger.info("Camera rotation changed to %d° (saved to .env)", angle)
    return {"ok": True, "rotation": angle}


_recording_lock = __import__("threading").Lock()
_recording_in_progress = False


@app.route("/api/test-record", methods=["POST"])
def test_record():
    """Trigger a test recording — full pipeline with posting to all platforms."""
    global _recording_in_progress

    if _monitor is None or not _monitor.running:
        return {"ok": False, "error": "Monitor not running"}, 503

    with _recording_lock:
        if _recording_in_progress:
            return {"ok": False, "error": "Recording already in progress"}, 429
        _recording_in_progress = True

    import threading
    from camera.recorder import ClipRecorder
    from social.comment_generator import generate_comment

    def _do_record():
        global _recording_in_progress
        try:
            recorder = ClipRecorder(_monitor.camera)
            logger.info("Test record triggered from dashboard")
            clip_path = recorder.record_clip()

            if clip_path and clip_path.exists():
                # Build time-of-day context so the AI knows the real time
                now = datetime.now(tz=_local_tz)
                schedule_info = get_schedule_info()
                hour = now.hour
                if hour < 8:
                    day_part = "early morning"
                elif hour < 11:
                    day_part = "mid-morning"
                elif hour < 14:
                    day_part = "midday"
                elif hour < 17:
                    day_part = "afternoon"
                else:
                    day_part = "evening"

                det = getattr(_monitor, '_detections_today', 0)
                rej = getattr(_monitor, '_rejected_today', 0)

                # Extract a frame for vision captions
                frame_path = None
                try:
                    frame_path = _monitor._extract_frame(clip_path)
                except Exception:
                    pass

                # Fetch weather if configured
                weather = None
                if config.OPENWEATHERMAP_API_KEY:
                    try:
                        from analytics.patterns import get_weather
                        weather = get_weather(config.LOCATION_LAT, config.LOCATION_LNG)
                    except Exception:
                        pass

                # Generate caption with full context
                captions = generate_comment(
                    detections=det,
                    rejected=rej,
                    platforms=_monitor.poster_manager.platform_names or None,
                    visit_number=det,
                    time_of_day=now.strftime("%I:%M %p").lstrip("0"),
                    day_part=day_part,
                    day_of_week=now.strftime("%A"),
                    month=now.strftime("%B"),
                    sunrise=schedule_info.get("sunrise", ""),
                    sunset=schedule_info.get("sunset", ""),
                    frame_path=frame_path,
                    weather=weather,
                )
                caption = captions.get("Facebook") or next(iter(captions.values()))
                # Save caption alongside clip
                caption_file = clip_path.with_suffix(".txt")
                caption_file.write_text(caption)
                logger.info("Test record complete: %s — caption: %s", clip_path.name, caption)

                # Post to all configured platforms — full pipeline test
                if _monitor.poster_manager.platform_names:
                    logger.info("Posting test clip to all platforms...")
                    results = _monitor.poster_manager.post_video(clip_path, captions)
                    posted = [p for p, ok in results.items() if ok]
                    failed = [p for p, ok in results.items() if not ok]
                    if posted:
                        logger.info("Test clip posted to: %s", ", ".join(posted))
                    if failed:
                        logger.warning("Test clip failed on: %s", ", ".join(failed))
                else:
                    logger.warning("No social platforms configured — skipping posting")
            else:
                logger.warning("Test record failed — no clip produced")
                # Clean up extracted frame
                if frame_path:
                    try:
                        Path(frame_path).unlink(missing_ok=True)
                    except Exception:
                        pass
        except Exception:
            logger.exception("Test record failed")
        finally:
            _recording_in_progress = False

    threading.Thread(target=_do_record, daemon=True).start()
    return {"ok": True, "message": "Recording started — clip will appear in ~25 seconds"}


@app.route("/api/logs")
def api_logs():
    """JSON log lines for live refresh."""
    logs = _get_recent_logs()
    return {"logs": [{"text": l["text"], "css_class": l["css_class"]} for l in logs]}


@app.route("/api/logs/clear", methods=["POST"])
def clear_logs():
    """Clear the log file."""
    log_file = config.LOGS_DIR / "hummingbird.log"
    try:
        log_file.write_text("")
        logger.info("Logs cleared from dashboard")
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}, 500


@app.route("/api/test-mode", methods=["POST"])
def toggle_test_mode():
    """Toggle test mode on/off."""
    if _monitor is None:
        return {"ok": False, "error": "Monitor not running"}, 503

    _monitor.test_mode = not _monitor.test_mode
    config.TEST_MODE = _monitor.test_mode
    _update_env_value("TEST_MODE", "true" if _monitor.test_mode else "false")
    state = "TEST MODE" if _monitor.test_mode else "LIVE"
    logger.info("Facebook posting toggled to: %s (saved to .env)", state)
    return {"ok": True, "test_mode": _monitor.test_mode}


@app.route("/api/facebook/debug", methods=["GET"])
def facebook_debug():
    """Return Facebook token diagnostics — scopes, validity, app info."""
    if _monitor is None:
        return {"ok": False, "error": "Monitor not running"}, 503

    try:
        info = _monitor.poster.verify_token()
        return {"ok": True, "facebook": info}
    except Exception as e:
        logger.exception("Facebook debug check failed")
        return {"ok": False, "error": str(e)}, 500


@app.route("/api/facebook/test", methods=["POST"])
def test_facebook_post():
    """Send a quick test text post to Facebook to verify credentials."""
    if _monitor is None:
        return {"ok": False, "error": "Monitor not running"}, 503

    try:
        from datetime import datetime
        try:
            from zoneinfo import ZoneInfo
            tz = ZoneInfo(config.LOCATION_TIMEZONE)
        except ImportError:
            import pytz
            tz = pytz.timezone(config.LOCATION_TIMEZONE)
        now = datetime.now(tz).strftime("%I:%M %p")
    except (KeyError, ValueError):
        now = "now"

    message = f"🧪 Test post from Backyard Hummers dashboard at {now} — if you see this, Facebook posting is working!"
    success = _monitor.poster.post_text(message)
    if success:
        logger.info("Test Facebook post sent successfully")
        return {"ok": True}
    else:
        return {"ok": False, "error": "Post failed — check logs for Facebook response"}, 500


def _local_time_str() -> str:
    """Return current local time as a human-readable string."""
    try:
        from datetime import datetime
        try:
            from zoneinfo import ZoneInfo
            tz = ZoneInfo(config.LOCATION_TIMEZONE)
        except ImportError:
            import pytz
            tz = pytz.timezone(config.LOCATION_TIMEZONE)
        return datetime.now(tz).strftime("%I:%M %p")
    except (KeyError, ValueError):
        return "now"


@app.route("/api/bluesky/test", methods=["POST"])
def test_bluesky_post():
    """Send a quick test text post to Bluesky to verify credentials."""
    if _monitor is None:
        return {"ok": False, "error": "Monitor not running"}, 503

    poster = _monitor.poster_manager.get_poster("Bluesky")
    if poster is None:
        return {"ok": False, "error": "Bluesky not configured"}, 404

    now = _local_time_str()
    message = f"🧪 Test post from Backyard Hummers dashboard at {now} — if you see this, Bluesky posting is working!"
    success = poster.post_text(message)
    if success:
        logger.info("Test Bluesky post sent successfully")
        return {"ok": True}
    else:
        return {"ok": False, "error": "Post failed — check logs for Bluesky response"}, 500


@app.route("/api/network/speedtest", methods=["POST"])
def network_speed_test():
    """Run a lightweight download/upload speed test."""
    import subprocess
    import time

    results = {"download_mbps": None, "upload_mbps": None, "ping_ms": None, "error": None}

    # --- Ping test ---
    try:
        ping = subprocess.run(
            ["ping", "-c", "3", "-W", "5", "8.8.8.8"],
            capture_output=True, text=True, timeout=20,
        )
        if ping.returncode == 0:
            for line in ping.stdout.splitlines():
                if "avg" in line:
                    # "rtt min/avg/max/mdev = 12.3/15.6/18.9/2.1 ms"
                    parts = line.split("=")[1].strip().split("/")
                    results["ping_ms"] = round(float(parts[1]), 1)
                    break
    except Exception:
        pass

    # --- Download test (fetch a ~10 MB file from a CDN) ---
    test_urls = [
        "https://speed.cloudflare.com/__down?bytes=10000000",
        "http://speedtest.tele2.net/10MB.zip",
    ]
    for url in test_urls:
        try:
            import requests as req
            start = time.time()
            resp = req.get(url, timeout=30, stream=True)
            total = 0
            for chunk in resp.iter_content(chunk_size=65536):
                total += len(chunk)
            elapsed = time.time() - start
            if elapsed > 0 and total > 0:
                results["download_mbps"] = round((total * 8) / (elapsed * 1_000_000), 2)
                break
        except Exception:
            continue

    # --- Upload test (POST ~2 MB of data to Cloudflare) ---
    try:
        import requests as req
        payload = b"\x00" * 2_000_000  # 2 MB
        start = time.time()
        req.post("https://speed.cloudflare.com/__up", data=payload, timeout=60)
        elapsed = time.time() - start
        if elapsed > 0:
            results["upload_mbps"] = round((len(payload) * 8) / (elapsed * 1_000_000), 2)
    except Exception:
        pass

    if results["download_mbps"] is None and results["upload_mbps"] is None:
        results["error"] = "Speed test failed — check internet connection"
        return results, 500

    logger.info("Speed test: ↓ %s Mbps  ↑ %s Mbps  ping %s ms",
                results["download_mbps"], results["upload_mbps"], results["ping_ms"])
    return results


@app.route("/api/twitter/test", methods=["POST"])
def test_twitter_post():
    """Send a quick test text post to Twitter/X to verify credentials."""
    if _monitor is None:
        return {"ok": False, "error": "Monitor not running"}, 503

    poster = _monitor.poster_manager.get_poster("Twitter")
    if poster is None:
        return {"ok": False, "error": "Twitter not configured"}, 404

    now = _local_time_str()
    message = f"🧪 Test post from Backyard Hummers dashboard at {now} — if you see this, Twitter posting is working!"
    success = poster.post_text(message)
    if success:
        logger.info("Test Twitter post sent successfully")
        return {"ok": True}
    else:
        return {"ok": False, "error": "Post failed — check logs for Twitter response"}, 500


@app.route("/api/update", methods=["POST"])
def trigger_update():
    """Pull latest from GitHub and restart if there are changes."""
    import subprocess

    try:
        project_dir = str(config.BASE_DIR)

        # Fetch latest
        fetch_result = subprocess.run(
            ["git", "fetch", "origin", "main", "--quiet"],
            capture_output=True, text=True, timeout=30,
            cwd=project_dir,
        )
        if fetch_result.returncode != 0:
            logger.error("git fetch failed: %s", fetch_result.stderr)
            return {"ok": False, "error": "git fetch failed"}, 500

        # Compare
        local = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5,
            cwd=project_dir,
        ).stdout.strip()

        remote = subprocess.run(
            ["git", "rev-parse", "origin/main"],
            capture_output=True, text=True, timeout=5,
            cwd=project_dir,
        ).stdout.strip()

        if local == remote:
            return {"ok": True, "updated": False, "message": "Already up to date"}

        # Clean up files that are no longer tracked but may cause merge conflicts
        # (e.g., retry_queue.json which is now in .gitignore)
        conflict_files = []
        try:
            # Check for conflicting untracked files that would block merge
            status_result = subprocess.run(
                ["git", "status", "--porcelain"],
                capture_output=True, text=True, timeout=5,
                cwd=project_dir,
            )
            # If retry_queue.json shows as modified or is tracked, remove it
            if "retry_queue.json" in status_result.stdout:
                retry_queue_path = Path(project_dir) / "retry_queue.json"
                if retry_queue_path.exists():
                    logger.info("Removing retry_queue.json to allow clean merge (will be recreated at runtime)")
                    retry_queue_path.unlink()
                    conflict_files.append("retry_queue.json")
        except Exception as e:
            logger.warning("Failed to check git status: %s", e)

        # Pull
        pull_result = subprocess.run(
            ["git", "pull", "origin", "main", "--ff-only"],
            capture_output=True, text=True, timeout=60,
            cwd=project_dir,
        )

        if pull_result.returncode != 0:
            # If pull failed and we removed files, log that info
            if conflict_files:
                logger.error("Git pull failed after cleanup: %s", pull_result.stderr)
            else:
                logger.error("Git pull failed: %s", pull_result.stderr)
            return {"ok": False, "error": "git pull failed"}, 500

        new_commit = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
            cwd=project_dir,
        ).stdout.strip()

        logger.info("Updated from %s to %s, restarting...", local[:7], new_commit)

        # Check if requirements changed
        diff_result = subprocess.run(
            ["git", "diff", local, remote, "--name-only"],
            capture_output=True, text=True, timeout=10,
            cwd=project_dir,
        )
        if "requirements.txt" in diff_result.stdout:
            logger.info("requirements.txt changed, reinstalling deps...")
            subprocess.run(
                [str(config.BASE_DIR / "venv" / "bin" / "pip"), "install", "-r", "requirements.txt", "--quiet"],
                capture_output=True, text=True, timeout=120,
                cwd=project_dir,
            )

        # Restart service in background (this will kill our process)
        subprocess.Popen(
            ["sudo", "systemctl", "restart", "hummingbird"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

        return {"ok": True, "updated": True, "new_commit": new_commit}

    except Exception as e:
        logger.exception("Update failed")
        return {"ok": False, "error": str(e)}, 500


@app.route("/api/detection-state")
def detection_state():
    """Return the current detection state for live overlay."""
    if _monitor is None:
        return {"state": "idle"}
    return {"state": _monitor.detection_state}


@app.route("/api/training/mark", methods=["POST"])
def mark_training():
    """Save the current frame with a label for training data."""
    import cv2
    from datetime import datetime

    if _monitor is None or not _monitor.running:
        return {"ok": False, "error": "Camera not running"}, 503

    data = request.get_json() or {}
    label = data.get("label", "unknown")

    if label not in ("hummingbird", "not_hummingbird"):
        return {"ok": False, "error": "Invalid label"}, 400

    try:
        # Create training subdirectories
        label_dir = config.TRAINING_DIR / label
        label_dir.mkdir(parents=True, exist_ok=True)

        # Get current frame (prefer full-res, fall back to lores)
        frame = _monitor.camera.get_full_res_frame()
        if frame is None:
            frame = _monitor.camera.capture_lores_array()

        # Convert YUV if needed
        if frame.shape[0] > frame.shape[1] * 1.2:
            frame = cv2.cvtColor(frame, cv2.COLOR_YUV2BGR_I420)

        # Save with timestamp
        timestamp = datetime.now(tz=_local_tz).strftime("%Y%m%d_%H%M%S_%f")
        filename = f"{label}_{timestamp}.jpg"
        filepath = label_dir / filename
        cv2.imwrite(str(filepath), frame)

        total = len(list(config.TRAINING_DIR.glob("**/*.jpg")))
        logger.info("Training sample saved: %s (%d total)", filename, total)

        return {"ok": True, "filename": filename, "total": total}

    except Exception as e:
        logger.exception("Failed to save training sample")
        return {"ok": False, "error": str(e)}, 500


_retrain_in_progress = False
_retrain_lock = threading.Lock()


@app.route("/api/training/retrain", methods=["POST"])
def api_retrain():
    """Kick off custom classifier retraining in a background thread."""
    global _retrain_in_progress

    if _retrain_in_progress:
        return {"ok": False, "error": "Retrain already in progress"}, 409

    def _do_retrain():
        global _retrain_in_progress
        try:
            from detection.custom_classifier import retrain
            result = retrain()
            if result.get("ok"):
                logger.info("Retrain complete: %s", result)
            else:
                logger.warning("Retrain failed: %s", result.get("error"))
        except Exception:
            logger.exception("Retrain crashed")
        finally:
            _retrain_in_progress = False

    with _retrain_lock:
        _retrain_in_progress = True
        t = threading.Thread(target=_do_retrain, daemon=True)
        t.start()

    return {"ok": True, "message": "Retraining started..."}


@app.route("/api/training/retrain/status")
def api_retrain_status():
    """Return custom classifier status and training data counts."""
    from detection.custom_classifier import get_classifier_meta, get_training_counts

    counts = get_training_counts()
    meta = get_classifier_meta()

    return {
        "retrain_in_progress": _retrain_in_progress,
        "classifier_active": meta is not None,
        "meta": meta,
        "counts": counts,
        "min_samples": config.CUSTOM_CLF_MIN_SAMPLES,
        "min_per_class": config.CUSTOM_CLF_MIN_PER_CLASS,
        "can_retrain": (
            counts["hummingbird"] >= config.CUSTOM_CLF_MIN_PER_CLASS
            and counts["not_hummingbird"] >= config.CUSTOM_CLF_MIN_PER_CLASS
            and (counts["hummingbird"] + counts["not_hummingbird"])
            >= config.CUSTOM_CLF_MIN_SAMPLES
        ),
    }


@app.route("/api/training/retrain/revert", methods=["POST"])
def api_retrain_revert():
    """Revert to the previous custom classifier."""
    from detection.custom_classifier import revert_classifier
    if revert_classifier():
        return {"ok": True, "message": "Reverted to previous classifier"}
    return {"ok": False, "error": "No previous classifier to revert to"}, 404


@app.route("/api/clips/list")
def api_clips_list():
    """JSON list of recent clips for live updates."""
    clips = _get_recent_clips()
    return {"clips": [
        {"name": c["name"], "size": c["size"], "time": c["time"], "caption": c.get("caption", "")}
        for c in clips
    ]}


@app.route("/gallery")
def gallery():
    """Public clip gallery with shareable links."""
    page = request.args.get("page", 1, type=int)
    per_page = 12
    clips = _get_recent_clips(limit=per_page * page)
    start = (page - 1) * per_page
    page_clips = clips[start:start + per_page]

    # Track page view
    if _monitor and _monitor.sightings_db:
        ip_hash = hashlib.sha256((request.remote_addr or "").encode()).hexdigest()[:16]
        _monitor.sightings_db.record_page_view(ip_hash, "/gallery")

    return render_template(
        "gallery.html",
        clips=page_clips,
        page=page,
        has_more=len(clips) > start + per_page,
    )


@app.route("/gallery/<clip_name>")
def gallery_clip(clip_name):
    """Individual clip page with Open Graph meta tags for sharing."""
    if "/" in clip_name or "\\" in clip_name or ".." in clip_name:
        return {"error": "Invalid"}, 400

    clip_path = config.CLIPS_DIR / clip_name
    if not clip_path.exists():
        return "Clip not found", 404

    caption = ""
    caption_path = clip_path.with_suffix(".txt")
    if caption_path.exists():
        try:
            caption = caption_path.read_text().strip()
        except OSError:
            pass

    mtime = datetime.fromtimestamp(clip_path.stat().st_mtime, tz=_local_tz).strftime("%Y-%m-%d %H:%M:%S")
    size_mb = clip_path.stat().st_size / 1_048_576

    return render_template(
        "clip_detail.html",
        clip={"name": clip_name, "caption": caption, "time": mtime, "size": f"{size_mb:.1f} MB"},
        host=request.host_url.rstrip("/"),
    )


@app.route("/analytics")
def analytics_page():
    """Analytics dashboard with feeding patterns and predictions."""
    if not _monitor or not _monitor.sightings_db:
        return render_template("analytics.html", analytics=None)

    from analytics.patterns import get_analytics_summary, generate_ai_insight
    summary = get_analytics_summary(_monitor.sightings_db)
    summary["ai_insight"] = generate_ai_insight(summary)
    return render_template("analytics.html", analytics=summary)


@app.route("/api/analytics")
def api_analytics():
    """JSON analytics endpoint."""
    if not _monitor or not _monitor.sightings_db:
        return {"error": "No data available"}, 503

    from analytics.patterns import get_analytics_summary, generate_ai_insight
    summary = get_analytics_summary(_monitor.sightings_db)
    summary["ai_insight"] = generate_ai_insight(summary)
    return summary


# Guestbook routes removed — just the two of us watching 🐦


@app.route("/api/events")
def sse_events():
    """Server-Sent Events endpoint for live detection notifications."""
    if not _monitor:
        return Response("Monitor not running", status=503)

    q: Queue = Queue(maxsize=50)
    _monitor._sse_subscribers.append(q)

    def stream():
        try:
            while True:
                try:
                    data = q.get(timeout=30)
                    yield f"data: {data}\n\n"
                except Empty:
                    yield ": keepalive\n\n"
        except GeneratorExit:
            pass
        finally:
            try:
                _monitor._sse_subscribers.remove(q)
            except (ValueError, AttributeError):
                pass

    return Response(
        stream(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/compose")
def compose_page():
    """Manual post composer — upload media, generate AI captions, post to socials."""
    return render_template("compose.html")


@app.route("/api/compose/upload", methods=["POST"])
def api_compose_upload():
    """Handle media upload for manual posts. Saves to clips/ directory."""
    if "media" not in request.files:
        return {"ok": False, "error": "No file provided"}, 400

    f = request.files["media"]
    if not f.filename:
        return {"ok": False, "error": "Empty filename"}, 400

    # Sanitize filename and save
    import re as _re
    from werkzeug.utils import secure_filename
    safe_name = secure_filename(f.filename)
    # Prefix with timestamp to avoid collisions
    ts = datetime.now(tz=_local_tz).strftime("%Y%m%d_%H%M%S")
    dest_name = f"manual_{ts}_{safe_name}"
    dest_path = config.CLIPS_DIR / dest_name
    config.CLIPS_DIR.mkdir(parents=True, exist_ok=True)
    f.save(str(dest_path))

    logger.info("Compose upload saved: %s (%.1f MB)", dest_name, dest_path.stat().st_size / 1_048_576)
    return {"ok": True, "filename": dest_name}


@app.route("/api/compose/generate", methods=["POST"])
def api_compose_generate():
    """Generate AI captions from user notes + optional uploaded media."""
    data = request.get_json(silent=True) or {}
    notes = data.get("notes", "").strip()
    filename = data.get("filename")

    if not notes and not filename:
        return {"ok": False, "error": "Provide notes or upload media"}, 400

    # Determine active platforms
    platforms = ["Facebook"]
    if _monitor and _monitor.poster_manager:
        platforms = _monitor.poster_manager.platform_names or ["Facebook"]

    # Resolve media path
    media_path = None
    if filename:
        candidate = config.CLIPS_DIR / filename
        if candidate.exists():
            media_path = candidate

    try:
        from social.comment_generator import generate_manual_post
        captions = generate_manual_post(
            user_notes=notes or "(no notes — just vibes and this media)",
            media_path=media_path,
            platforms=platforms,
        )
        return {"ok": True, "captions": captions}
    except Exception as e:
        logger.exception("Failed to generate manual post captions")
        return {"ok": False, "error": str(e)}, 500


@app.route("/api/compose/post", methods=["POST"])
def api_compose_post():
    """Post manually composed content to all enabled social platforms."""
    data = request.get_json(silent=True) or {}
    captions = data.get("captions", {})
    filename = data.get("filename")

    if not captions:
        return {"ok": False, "error": "No captions provided"}, 400

    if not _monitor or not _monitor.poster_manager:
        return {"ok": False, "error": "Monitor not running"}, 503

    pm = _monitor.poster_manager

    # Resolve media path
    media_path = None
    if filename:
        candidate = config.CLIPS_DIR / filename
        if candidate.exists():
            media_path = candidate

    try:
        if media_path:
            suffix = media_path.suffix.lower()
            if suffix in (".mp4", ".mov", ".avi", ".mkv"):
                results = pm.post_video(media_path, captions)
            elif suffix in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
                results = pm.post_photo(media_path, captions)
            else:
                # Unknown media type — post as text
                results = pm.post_text(captions)
        else:
            # Text-only post
            results = pm.post_text(captions)

        logger.info("Manual compose post results: %s", results)
        return {"ok": True, "results": results}
    except Exception as e:
        logger.exception("Failed to post manual compose")
        return {"ok": False, "error": str(e)}, 500


@app.route("/api/platforms")
def api_platforms():
    """Return list of active social media platforms."""
    if not _monitor:
        return {"platforms": []}
    return {"platforms": _monitor.poster_manager.platform_names}


@app.route("/api/status")
def api_status():
    """JSON status endpoint for live dashboard updates (no page reload)."""
    s = _get_status()
    return {
        "running": s.running,
        "in_cooldown": s.in_cooldown,
        "detections_today": s.detections_today,
        "posts_today": s.posts_today,
        "total_clips": s.total_clips,
        "uptime": s.uptime,
        "camera_type": s.camera_type,
        "camera_error": s.camera_error,
        "test_mode": s.test_mode,
        "rejected_today": s.rejected_today,
        "training_count": s.training_count,
        "git_commit": s.git_commit,
        "cpu_temp": s.cpu_temp,
        "cpu_temp_str": s.cpu_temp_str,
        "ram_str": s.ram_str,
        "ram_percent": s.ram_percent,
        "disk_str": s.disk_str,
        "disk_percent": s.disk_percent,
        "disk_free_gb": s.disk_free_gb,
        "schedule": s.schedule,
        "weather": s.weather,
        "tailscale": s.tailscale,
    }


def start_web_server(monitor, host="0.0.0.0", port=8080):
    """Start the dashboard web server in a background thread."""
    set_monitor(monitor)

    # Suppress Flask's default request logging to keep logs clean
    werkzeug_logger = logging.getLogger("werkzeug")
    werkzeug_logger.setLevel(logging.WARNING)

    import threading
    thread = threading.Thread(
        target=lambda: app.run(host=host, port=port, debug=False, use_reloader=False),
        daemon=True,
    )
    thread.start()
    logger.info("Dashboard running at http://%s:%d", host, port)
