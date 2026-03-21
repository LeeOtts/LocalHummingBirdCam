"""Local web dashboard for monitoring the Backyard Hummers system."""

import json
import logging
import os
from datetime import datetime
from pathlib import Path

from flask import Flask, Response, render_template_string, send_from_directory, request, redirect, url_for

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
        .section .section-actions {
            float: right;
        }

        .clip-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
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
        .clip-card .clip-caption {
            padding: 8px 10px;
            font-size: 0.9em;
            color: #e0e0e0;
            background: #0f3460;
            border-top: 1px solid #16213e;
            font-style: italic;
            line-height: 1.4;
        }
        .clip-card .clip-actions {
            padding: 8px 10px;
            display: flex;
            gap: 8px;
            border-top: 1px solid #0f3460;
        }
        .btn {
            padding: 6px 14px;
            border: none;
            border-radius: 5px;
            cursor: pointer;
            font-size: 0.8em;
            font-weight: 600;
            text-decoration: none;
            display: inline-block;
        }
        .btn-delete {
            background: #e94560;
            color: white;
        }
        .btn-delete:hover { background: #c73650; }
        .btn-delete-all {
            background: #e94560;
            color: white;
            font-size: 0.85em;
            padding: 6px 16px;
        }
        .btn-delete-all:hover { background: #c73650; }

        .confirm-overlay {
            display: none;
            position: fixed;
            top: 0; left: 0; right: 0; bottom: 0;
            background: rgba(0,0,0,0.7);
            z-index: 100;
            justify-content: center;
            align-items: center;
        }
        .confirm-overlay.active { display: flex; }
        .confirm-box {
            background: #16213e;
            border: 2px solid #e94560;
            border-radius: 12px;
            padding: 30px;
            text-align: center;
            max-width: 400px;
        }
        .confirm-box p { margin-bottom: 20px; font-size: 1.1em; }
        .confirm-box .btn { margin: 0 8px; padding: 10px 24px; font-size: 1em; }
        .btn-cancel {
            background: #0f3460;
            color: #e0e0e0;
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

        .toast {
            position: fixed;
            bottom: 30px;
            left: 50%;
            transform: translateX(-50%);
            background: #4ecca3;
            color: #1a1a2e;
            padding: 12px 24px;
            border-radius: 8px;
            font-weight: 600;
            z-index: 200;
            display: none;
        }
        .toast.active { display: block; }
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
                <div class="label">Camera</div>
                <div class="value">{{ status.camera_type }}</div>
            </div>
            <div class="status-card">
                <div class="label">Cooldown</div>
                <div class="value {{ 'red' if status.in_cooldown else 'green' }}">
                    {{ 'ACTIVE' if status.in_cooldown else 'READY' }}
                </div>
            </div>
            <div class="status-card">
                <div class="label">Facebook Posting</div>
                <div class="value {{ 'yellow' if status.test_mode else 'green' }}">
                    {{ 'TEST MODE' if status.test_mode else 'LIVE' }}
                </div>
                <button class="btn {{ 'btn-cancel' if status.test_mode else 'btn-delete' }}"
                        style="margin-top: 10px; font-size: 0.7em;"
                        onclick="toggleTestMode()">
                    {{ 'Go Live' if status.test_mode else 'Switch to Test' }}
                </button>
            </div>
        </div>

        <div class="live-feed">
            <h2>Live Camera Feed</h2>
            <img src="/feed" alt="Live camera feed">
        </div>

        <div class="section">
            <h2 style="display:inline-block;">Recent Clips</h2>
            {% if clips %}
            <span class="section-actions">
                <button class="btn btn-delete-all" onclick="showDeleteAll()">Delete All Clips</button>
            </span>
            <div style="clear:both; margin-bottom: 15px;"></div>
            <div class="clip-grid">
                {% for clip in clips %}
                <div class="clip-card" id="clip-{{ loop.index }}">
                    <video controls preload="metadata">
                        <source src="/clips/{{ clip.name }}" type="video/mp4">
                    </video>
                    {% if clip.caption %}
                    <div class="clip-caption">"{{ clip.caption }}"</div>
                    {% endif %}
                    <div class="clip-info">
                        {{ clip.name }} &mdash; {{ clip.size }} &mdash; {{ clip.time }}
                    </div>
                    <div class="clip-actions">
                        <button class="btn btn-delete" onclick="deleteClip('{{ clip.name }}', 'clip-{{ loop.index }}')">Delete Clip</button>
                    </div>
                </div>
                {% endfor %}
            </div>
            {% else %}
            <p style="color: #a0a0b0; margin-top: 15px;">No clips recorded yet. Waiting for hummingbirds...</p>
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

    <!-- Delete All confirmation -->
    <div class="confirm-overlay" id="deleteAllOverlay">
        <div class="confirm-box">
            <p>Delete ALL clips and their captions?</p>
            <button class="btn btn-cancel" onclick="hideDeleteAll()">Cancel</button>
            <button class="btn btn-delete" onclick="deleteAll()">Delete All</button>
        </div>
    </div>

    <div class="toast" id="toast"></div>

    <script>
        // Pause auto-refresh while user is interacting
        let autoRefresh = setTimeout(() => location.reload(), 10000);

        function showToast(msg) {
            const t = document.getElementById('toast');
            t.textContent = msg;
            t.classList.add('active');
            setTimeout(() => t.classList.remove('active'), 3000);
        }

        function deleteClip(filename, cardId) {
            if (!confirm('Delete ' + filename + ' and its caption?')) return;
            clearTimeout(autoRefresh);

            fetch('/api/clips/' + filename, { method: 'DELETE' })
                .then(r => r.json())
                .then(data => {
                    if (data.ok) {
                        document.getElementById(cardId).remove();
                        showToast('Deleted ' + filename);
                    } else {
                        showToast('Error: ' + data.error);
                    }
                    autoRefresh = setTimeout(() => location.reload(), 10000);
                })
                .catch(() => {
                    showToast('Delete failed');
                    autoRefresh = setTimeout(() => location.reload(), 10000);
                });
        }

        function showDeleteAll() {
            clearTimeout(autoRefresh);
            document.getElementById('deleteAllOverlay').classList.add('active');
        }

        function hideDeleteAll() {
            document.getElementById('deleteAllOverlay').classList.remove('active');
            autoRefresh = setTimeout(() => location.reload(), 10000);
        }

        function toggleTestMode() {
            fetch('/api/test-mode', { method: 'POST' })
                .then(r => r.json())
                .then(data => {
                    if (data.ok) {
                        showToast(data.test_mode ? 'Switched to TEST MODE' : 'Facebook posting is LIVE');
                        setTimeout(() => location.reload(), 1000);
                    }
                })
                .catch(() => showToast('Toggle failed'));
        }

        function deleteAll() {
            fetch('/api/clips', { method: 'DELETE' })
                .then(r => r.json())
                .then(data => {
                    if (data.ok) {
                        showToast('Deleted ' + data.deleted + ' clips');
                        hideDeleteAll();
                        setTimeout(() => location.reload(), 1000);
                    } else {
                        showToast('Error: ' + data.error);
                        hideDeleteAll();
                    }
                })
                .catch(() => {
                    showToast('Delete failed');
                    hideDeleteAll();
                });
        }
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
    s.camera_type = _monitor.camera.camera_type.upper() if _monitor and _monitor.camera.camera_type else "N/A"
    s.test_mode = _monitor.test_mode if _monitor else True
    return s


def _get_recent_clips(limit=12):
    """Get the most recent clips with their captions."""
    if not config.CLIPS_DIR.exists():
        return []

    mp4s = sorted(config.CLIPS_DIR.glob("*.mp4"), key=os.path.getmtime, reverse=True)[:limit]
    clips = []
    for p in mp4s:
        size_mb = p.stat().st_size / 1_048_576
        mtime = datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")

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


@app.route("/api/test-mode", methods=["POST"])
def toggle_test_mode():
    """Toggle test mode on/off."""
    if _monitor is None:
        return {"ok": False, "error": "Monitor not running"}, 503

    _monitor.test_mode = not _monitor.test_mode
    state = "TEST MODE" if _monitor.test_mode else "LIVE"
    logger.info("Facebook posting toggled to: %s", state)
    return {"ok": True, "test_mode": _monitor.test_mode}


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
