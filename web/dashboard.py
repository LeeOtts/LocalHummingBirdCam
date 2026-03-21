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
        .feed-container {
            position: relative;
            max-width: 640px;
            margin: 0 auto;
        }
        .feed-container img {
            width: 100%;
            border-radius: 8px;
            display: block;
        }
        .feed-overlay {
            position: absolute;
            top: 0; left: 0; right: 0; bottom: 0;
            border-radius: 8px;
            border: 4px solid transparent;
            pointer-events: none;
            transition: border-color 0.3s;
        }
        .feed-overlay.state-idle { border-color: transparent; }
        .feed-overlay.state-motion { border-color: #f0c040; }
        .feed-overlay.state-verifying { border-color: #58a6ff; }
        .feed-overlay.state-confirmed { border-color: #4ecca3; box-shadow: 0 0 20px #4ecca3; }
        .feed-overlay.state-rejected { border-color: #e94560; }
        .feed-overlay .state-label {
            position: absolute;
            top: 10px; left: 10px;
            padding: 4px 12px;
            border-radius: 4px;
            font-weight: bold;
            font-size: 0.85em;
        }
        .feed-controls {
            text-align: center;
            margin-top: 12px;
            display: flex;
            gap: 10px;
            justify-content: center;
            align-items: center;
            flex-wrap: wrap;
        }
        .btn-mark-yes {
            background: #4ecca3;
            color: #1a1a2e;
            font-weight: bold;
        }
        .btn-mark-yes:hover { background: #3db890; }
        .btn-mark-no {
            background: #e94560;
            color: white;
        }
        .btn-mark-no:hover { background: #c73650; }
        .training-stats {
            color: #a0a0b0;
            font-size: 0.85em;
        }
        .detection-legend {
            text-align: center;
            margin-top: 10px;
            font-size: 0.8em;
            color: #a0a0b0;
        }
        .legend-dot {
            display: inline-block;
            width: 10px; height: 10px;
            border-radius: 50%;
            margin: 0 4px 0 12px;
            vertical-align: middle;
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
        .btn-update {
            background: #4ecca3;
            color: #1a1a2e;
        }
        .btn-update:hover { background: #3db890; }
        .btn-update:disabled { background: #555; color: #999; cursor: wait; }

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
        <p>Where the birds are fast and the jokes are faster</p>
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
                <div class="label">Rejected Today</div>
                <div class="value red">{{ status.rejected_today }}</div>
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
            <div class="status-card">
                <div class="label">Version</div>
                <div class="value" style="font-size: 0.9em;">{{ status.git_commit }}</div>
                <button class="btn btn-update"
                        style="margin-top: 10px; font-size: 0.7em;"
                        id="updateBtn"
                        onclick="checkForUpdate()">
                    Check for Update
                </button>
            </div>
        </div>

        <div class="live-feed">
            <h2>Live Camera Feed</h2>
            <div class="feed-container">
                <img src="/feed" alt="Live camera feed" id="liveFeed">
                <div class="feed-overlay" id="feedOverlay"></div>
            </div>
            <div class="feed-controls">
                <button class="btn btn-mark-yes" onclick="markHummingbird()">I See a Hummingbird!</button>
                <button class="btn btn-mark-no" onclick="markNotHummingbird()">Not a Hummingbird</button>
                <span class="training-stats">
                    Training samples: {{ status.training_count }}
                </span>
            </div>
            <div class="detection-legend">
                <span class="legend-dot" style="background:#4ecca3;"></span> Confirmed
                <span class="legend-dot" style="background:#f0c040;"></span> Motion detected
                <span class="legend-dot" style="background:#e94560;"></span> Rejected
                <span class="legend-dot" style="background:#58a6ff;"></span> Verifying...
            </div>
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

        // Poll detection state for live overlay
        function pollDetectionState() {
            fetch('/api/detection-state')
                .then(r => r.json())
                .then(data => {
                    const overlay = document.getElementById('feedOverlay');
                    overlay.className = 'feed-overlay state-' + data.state;

                    // Update or create state label
                    let label = overlay.querySelector('.state-label');
                    if (data.state !== 'idle') {
                        if (!label) {
                            label = document.createElement('div');
                            label.className = 'state-label';
                            overlay.appendChild(label);
                        }
                        const labels = {
                            'motion': 'MOTION',
                            'verifying': 'VERIFYING...',
                            'confirmed': 'HUMMINGBIRD!',
                            'rejected': 'NOT A BIRD',
                        };
                        const colors = {
                            'motion': '#f0c040',
                            'verifying': '#58a6ff',
                            'confirmed': '#4ecca3',
                            'rejected': '#e94560',
                        };
                        label.textContent = labels[data.state] || data.state;
                        label.style.background = colors[data.state] || '#555';
                        label.style.color = data.state === 'motion' ? '#1a1a2e' : 'white';
                    } else if (label) {
                        label.remove();
                    }
                })
                .catch(() => {});
        }
        setInterval(pollDetectionState, 500);

        function markHummingbird() {
            fetch('/api/training/mark', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({label: 'hummingbird'})
            })
            .then(r => r.json())
            .then(data => {
                if (data.ok) showToast('Saved as hummingbird (' + data.filename + ')');
                else showToast('Error: ' + data.error);
            })
            .catch(() => showToast('Mark failed'));
        }

        function markNotHummingbird() {
            fetch('/api/training/mark', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({label: 'not_hummingbird'})
            })
            .then(r => r.json())
            .then(data => {
                if (data.ok) showToast('Saved as NOT hummingbird (' + data.filename + ')');
                else showToast('Error: ' + data.error);
            })
            .catch(() => showToast('Mark failed'));
        }

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

        function checkForUpdate() {
            const btn = document.getElementById('updateBtn');
            btn.disabled = true;
            btn.textContent = 'Checking...';
            clearTimeout(autoRefresh);

            fetch('/api/update', { method: 'POST' })
                .then(r => r.json())
                .then(data => {
                    if (data.updated) {
                        showToast('Updated to ' + data.new_commit + ' — restarting...');
                        setTimeout(() => location.reload(), 5000);
                    } else if (data.ok) {
                        showToast('Already up to date');
                        btn.disabled = false;
                        btn.textContent = 'Check for Update';
                        autoRefresh = setTimeout(() => location.reload(), 10000);
                    } else {
                        showToast('Update failed: ' + (data.error || 'unknown'));
                        btn.disabled = false;
                        btn.textContent = 'Check for Update';
                        autoRefresh = setTimeout(() => location.reload(), 10000);
                    }
                })
                .catch(() => {
                    showToast('Update request failed');
                    btn.disabled = false;
                    btn.textContent = 'Check for Update';
                    autoRefresh = setTimeout(() => location.reload(), 10000);
                });
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
    s.rejected_today = getattr(_monitor, '_rejected_today', 0) if _monitor else 0

    # Training sample count
    training_dir = config.TRAINING_DIR
    if training_dir.exists():
        s.training_count = len(list(training_dir.glob("*.jpg")))
    else:
        s.training_count = 0

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


@app.route("/api/update", methods=["POST"])
def trigger_update():
    """Pull latest from GitHub and restart if there are changes."""
    import subprocess

    try:
        project_dir = str(config.BASE_DIR)

        # Fetch latest
        subprocess.run(
            ["git", "fetch", "origin", "main", "--quiet"],
            capture_output=True, text=True, timeout=30,
            cwd=project_dir,
        )

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

        # Pull
        pull_result = subprocess.run(
            ["git", "pull", "origin", "main", "--ff-only"],
            capture_output=True, text=True, timeout=60,
            cwd=project_dir,
        )

        if pull_result.returncode != 0:
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

        # Get current frame
        frame = None
        if hasattr(_monitor.camera, '_usb_lock'):
            with _monitor.camera._usb_lock:
                if _monitor.camera._usb_latest_frame is not None:
                    frame = _monitor.camera._usb_latest_frame.copy()

        if frame is None:
            frame = _monitor.camera.capture_lores_array()

        # Convert YUV if needed
        if frame.shape[0] > frame.shape[1] * 1.2:
            frame = cv2.cvtColor(frame, cv2.COLOR_YUV2BGR_I420)

        # Save with timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        filename = f"{label}_{timestamp}.jpg"
        filepath = label_dir / filename
        cv2.imwrite(str(filepath), frame)

        total = len(list(config.TRAINING_DIR.glob("**/*.jpg")))
        logger.info("Training sample saved: %s (%d total)", filename, total)

        return {"ok": True, "filename": filename, "total": total}

    except Exception as e:
        logger.exception("Failed to save training sample")
        return {"ok": False, "error": str(e)}, 500


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
