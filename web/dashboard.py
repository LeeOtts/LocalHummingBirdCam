"""Local web dashboard for monitoring the Backyard Hummers system."""

import json
import logging
import os
from datetime import datetime
from pathlib import Path

from flask import Flask, Response, render_template_string, send_from_directory

import config

logger = logging.getLogger(__name__)

app = Flask(__name__)

# Reference to the monitor instance (set by main.py)
_monitor = None


def set_monitor(monitor):
    global _monitor
    _monitor = monitor


DASHBOARD_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Backyard Hummers Dashboard</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            background: #1a1a2e;
            color: #e0e0e0;
            min-height: 100vh;
        }
        header {
            background: linear-gradient(135deg, #16213e, #0f3460);
            padding: 20px;
            text-align: center;
            border-bottom: 3px solid #e94560;
        }
        header h1 { font-size: 1.8em; color: #e94560; }
        header p { color: #a0a0b0; margin-top: 5px; }
        .container { max-width: 1000px; margin: 0 auto; padding: 20px; }

        .status-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 15px;
            margin-bottom: 30px;
        }
        .status-card {
            background: #16213e;
            border-radius: 10px;
            padding: 20px;
            text-align: center;
            border: 1px solid #0f3460;
        }
        .status-card .label { color: #a0a0b0; font-size: 0.85em; text-transform: uppercase; }
        .status-card .value { font-size: 1.8em; font-weight: bold; margin-top: 5px; }
        .status-card .value.green { color: #4ecca3; }
        .status-card .value.red { color: #e94560; }
        .status-card .value.yellow { color: #f0c040; }

        .live-feed {
            background: #16213e;
            border-radius: 10px;
            padding: 20px;
            margin-bottom: 30px;
            border: 1px solid #0f3460;
        }
        .live-feed h2 { color: #e94560; margin-bottom: 15px; }
        .live-feed img {
            width: 100%;
            max-width: 640px;
            border-radius: 8px;
            display: block;
            margin: 0 auto;
        }

        .section {
            background: #16213e;
            border-radius: 10px;
            padding: 20px;
            margin-bottom: 30px;
            border: 1px solid #0f3460;
        }
        .section h2 { color: #e94560; margin-bottom: 15px; }

        .clip-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
            gap: 15px;
        }
        .clip-card {
            background: #1a1a2e;
            border-radius: 8px;
            overflow: hidden;
            border: 1px solid #0f3460;
        }
        .clip-card video {
            width: 100%;
            display: block;
        }
        .clip-card .clip-info {
            padding: 10px;
            font-size: 0.85em;
            color: #a0a0b0;
        }

        .log-box {
            background: #0d1117;
            border-radius: 8px;
            padding: 15px;
            font-family: 'Courier New', monospace;
            font-size: 0.8em;
            max-height: 400px;
            overflow-y: auto;
            line-height: 1.6;
            white-space: pre-wrap;
            word-break: break-all;
        }
        .log-box .log-info { color: #58a6ff; }
        .log-box .log-warn { color: #f0c040; }
        .log-box .log-error { color: #e94560; }

        .refresh-bar {
            text-align: center;
            padding: 10px;
            color: #a0a0b0;
            font-size: 0.85em;
        }
        .refresh-bar a { color: #4ecca3; text-decoration: none; }
    </style>
</head>
<body>
    <header>
        <h1>Backyard Hummers</h1>
        <p>Hummingbird Feeder Camera Dashboard</p>
    </header>

    <div class="container">
        <div class="refresh-bar">
            Auto-refreshes every 10 seconds | <a href="/">Refresh now</a>
        </div>

        <div class="status-grid">
            <div class="status-card">
                <div class="label">System Status</div>
                <div class="value {{ 'green' if status.running else 'red' }}">
                    {{ 'RUNNING' if status.running else 'STOPPED' }}
                </div>
            </div>
            <div class="status-card">
                <div class="label">Detections Today</div>
                <div class="value green">{{ status.detections_today }}</div>
            </div>
            <div class="status-card">
                <div class="label">Posts Today</div>
                <div class="value yellow">{{ status.posts_today }}</div>
            </div>
            <div class="status-card">
                <div class="label">Clips Saved</div>
                <div class="value">{{ status.total_clips }}</div>
            </div>
            <div class="status-card">
                <div class="label">Uptime</div>
                <div class="value">{{ status.uptime }}</div>
            </div>
            <div class="status-card">
                <div class="label">Cooldown</div>
                <div class="value {{ 'red' if status.in_cooldown else 'green' }}">
                    {{ 'ACTIVE' if status.in_cooldown else 'READY' }}
                </div>
            </div>
        </div>

        <div class="live-feed">
            <h2>Live Camera Feed</h2>
            <img src="/feed" alt="Live camera feed">
        </div>

        <div class="section">
            <h2>Recent Clips</h2>
            {% if clips %}
            <div class="clip-grid">
                {% for clip in clips %}
                <div class="clip-card">
                    <video controls preload="metadata">
                        <source src="/clips/{{ clip.name }}" type="video/mp4">
                    </video>
                    <div class="clip-info">
                        {{ clip.name }} &mdash; {{ clip.size }} &mdash; {{ clip.time }}
                    </div>
                </div>
                {% endfor %}
            </div>
            {% else %}
            <p style="color: #a0a0b0;">No clips recorded yet. Waiting for hummingbirds...</p>
            {% endif %}
        </div>

        <div class="section">
            <h2>Recent Logs</h2>
            <div class="log-box">
                {% for line in logs %}
                <div class="{{ line.css_class }}">{{ line.text }}</div>
                {% endfor %}
            </div>
        </div>
    </div>

    <script>
        setTimeout(() => location.reload(), 10000);
    </script>
</body>
</html>
"""


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
    return s


def _get_recent_clips(limit=6):
    """Get the most recent clips."""
    if not config.CLIPS_DIR.exists():
        return []

    mp4s = sorted(config.CLIPS_DIR.glob("*.mp4"), key=os.path.getmtime, reverse=True)[:limit]
    clips = []
    for p in mp4s:
        size_mb = p.stat().st_size / 1_048_576
        mtime = datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        clips.append({"name": p.name, "size": f"{size_mb:.1f} MB", "time": mtime})
    return clips


def _get_recent_logs(limit=50):
    """Read the last N lines from the log file."""
    log_file = config.LOGS_DIR / "hummingbird.log"
    if not log_file.exists():
        return []

    try:
        lines = log_file.read_text().strip().split("\n")[-limit:]
    except OSError:
        return []

    result = []
    for line in lines:
        if "[ERROR]" in line:
            css = "log-error"
        elif "[WARNING]" in line:
            css = "log-warn"
        else:
            css = "log-info"
        result.append({"text": line, "css_class": css})
    return result


@app.route("/")
def dashboard():
    return render_template_string(
        DASHBOARD_HTML,
        status=_get_status(),
        clips=_get_recent_clips(),
        logs=_get_recent_logs(),
    )


@app.route("/clips/<filename>")
def serve_clip(filename):
    return send_from_directory(str(config.CLIPS_DIR), filename)


@app.route("/feed")
def live_feed():
    """Serve a live MJPEG stream from the camera."""
    if _monitor is None or not _monitor.running:
        return Response("Camera not running", status=503)

    def generate_frames():
        import cv2
        while _monitor and _monitor.running:
            try:
                frame = _monitor.camera.capture_lores_array()
                # Convert YUV420 to BGR
                if frame.shape[0] > frame.shape[1] * 1.2:
                    bgr = cv2.cvtColor(frame, cv2.COLOR_YUV2BGR_I420)
                else:
                    bgr = frame
                _, jpeg = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, 60])
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n\r\n" + jpeg.tobytes() + b"\r\n"
                )
            except Exception:
                break
            import time
            time.sleep(0.1)  # ~10 fps for the web feed

    return Response(
        generate_frames(),
        mimetype="multipart/x-mixed-replace; boundary=frame",
    )


@app.route("/api/status")
def api_status():
    """JSON status endpoint for programmatic access."""
    s = _get_status()
    return {
        "running": s.running,
        "in_cooldown": s.in_cooldown,
        "detections_today": s.detections_today,
        "posts_today": s.posts_today,
        "total_clips": s.total_clips,
        "uptime": s.uptime,
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
