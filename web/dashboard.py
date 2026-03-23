"""Local web dashboard for monitoring the Backyard Hummers system."""

import json
import logging
import os
from datetime import datetime
from pathlib import Path

from flask import Flask, Response, render_template_string, send_from_directory, request, redirect, url_for

import config
from schedule import get_schedule_info

logger = logging.getLogger(__name__)

app = Flask(__name__)

# Reference to the monitor instance (set by main.py)
_monitor = None


def _update_env_value(key: str, value) -> bool:
    """Update or add a key=value in the .env file so it persists across restarts."""
    env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
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
        with open(env_path, "w") as f:
            f.writelines(lines)
        return True
    except Exception:
        logger.exception("Failed to save %s to .env", key)
        return False


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
            background: #0f1a0f;
            color: #e0e0e0;
            min-height: 100vh;
        }
        header {
            background: linear-gradient(135deg, #1a2e1a, #0d2818);
            padding: 20px;
            text-align: center;
            border-bottom: 3px solid #b91c1c;
        }
        header h1 { font-size: 1.8em; color: #ffffff; }
        header p { color: #8faa8f; margin-top: 5px; }
        .container { max-width: 1000px; margin: 0 auto; padding: 20px; }

        .status-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 15px;
            margin-bottom: 30px;
        }
        .status-card {
            background: #1a2e1a;
            border-radius: 10px;
            padding: 20px;
            text-align: center;
            border: 1px solid #2d4a2d;
        }
        .status-card .label { color: #8faa8f; font-size: 0.85em; text-transform: uppercase; }
        .status-card .value { font-size: 1.8em; font-weight: bold; margin-top: 5px; }
        .status-card .value.green { color: #22c55e; }
        .status-card .value.red { color: #ef4444; }
        .status-card .value.yellow { color: #f0c040; }
        .status-card .value.purple { color: #a78bfa; }

        .live-feed {
            background: #1a2e1a;
            border-radius: 10px;
            padding: 20px;
            margin-bottom: 30px;
            border: 1px solid #2d4a2d;
        }
        .live-feed h2 { color: #ffffff; margin-bottom: 15px; }
        .feed-container {
            position: relative;
            max-width: {{ video_width }}px;
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
        .feed-overlay.state-sleeping { border-color: #a78bfa; box-shadow: 0 0 15px #a78bfa; }
        .feed-overlay.state-motion { border-color: #f0c040; }
        .feed-overlay.state-verifying { border-color: #22c55e; }
        .feed-overlay.state-confirmed { border-color: #22c55e; box-shadow: 0 0 20px #22c55e; }
        .feed-overlay.state-rejected { border-color: #dc2626; }
        .feed-overlay.state-camera_error { border-color: #ef4444; box-shadow: 0 0 20px #ef4444; }
        .feed-overlay .state-label {
            position: absolute;
            top: 10px; left: 10px;
            padding: 4px 12px;
            border-radius: 4px;
            font-weight: bold;
            font-size: 0.85em;
        }
        .audio-toggle {
            position: absolute;
            top: 12px; right: 12px;
            z-index: 10;
            background: rgba(0,0,0,0.6);
            border: 1px solid #555;
            border-radius: 50%;
            width: 40px; height: 40px;
            font-size: 1.2em;
            cursor: pointer;
            color: #e0e0e0;
            padding: 0;
            line-height: 40px;
            text-align: center;
        }
        .audio-toggle.active {
            background: rgba(34, 197, 94, 0.7);
            border-color: #22c55e;
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
            background: #22c55e;
            color: #0f1a0f;
            font-weight: bold;
        }
        .btn-mark-yes:hover { background: #16a34a; }
        .btn-mark-no {
            background: #dc2626;
            color: white;
        }
        .btn-mark-no:hover { background: #b91c1c; }
        .training-stats {
            color: #8faa8f;
            font-size: 0.85em;
        }
        .detection-legend {
            text-align: center;
            margin-top: 10px;
            font-size: 0.8em;
            color: #8faa8f;
        }
        .legend-dot {
            display: inline-block;
            width: 10px; height: 10px;
            border-radius: 50%;
            margin: 0 4px 0 12px;
            vertical-align: middle;
        }

        .section {
            background: #1a2e1a;
            border-radius: 10px;
            padding: 20px;
            margin-bottom: 30px;
            border: 1px solid #2d4a2d;
        }
        .section h2 { color: #ffffff; margin-bottom: 15px; }
        .section .section-actions {
            float: right;
        }

        .clip-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
            gap: 15px;
        }
        .clip-card {
            background: #0f1a0f;
            border-radius: 8px;
            overflow: hidden;
            border: 1px solid #2d4a2d;
        }
        .clip-card video {
            width: 100%;
            display: block;
        }
        .clip-card .clip-info {
            padding: 10px;
            font-size: 0.85em;
            color: #8faa8f;
        }
        .clip-card .clip-caption {
            padding: 8px 10px;
            font-size: 0.9em;
            color: #e0e0e0;
            background: #1a3a1a;
            border-top: 1px solid #2d4a2d;
            font-style: italic;
            line-height: 1.4;
        }
        .clip-card .clip-actions {
            padding: 8px 10px;
            display: flex;
            gap: 8px;
            border-top: 1px solid #2d4a2d;
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
            background: #dc2626;
            color: white;
        }
        .btn-delete:hover { background: #b91c1c; }
        .btn-delete-all {
            background: #dc2626;
            color: white;
            font-size: 0.85em;
            padding: 6px 16px;
        }
        .btn-delete-all:hover { background: #b91c1c; }
        .btn-update {
            background: #22c55e;
            color: #0f1a0f;
        }
        .btn-update:hover { background: #16a34a; }
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
            background: #1a2e1a;
            border: 2px solid #dc2626;
            border-radius: 12px;
            padding: 30px;
            text-align: center;
            max-width: 400px;
        }
        .confirm-box p { margin-bottom: 20px; font-size: 1.1em; }
        .confirm-box .btn { margin: 0 8px; padding: 10px 24px; font-size: 1em; }
        .btn-cancel {
            background: #2d4a2d;
            color: #e0e0e0;
        }

        .log-box {
            background: #0a140a;
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
        .log-box .log-info { color: #4ade80; }
        .log-box .log-warn { color: #f0c040; }
        .log-box .log-error { color: #ef4444; }

        .refresh-bar {
            text-align: center;
            padding: 10px;
            color: #8faa8f;
            font-size: 0.85em;
        }
        .refresh-bar a { color: #22c55e; text-decoration: none; }

        .toast {
            position: fixed;
            bottom: 30px;
            left: 50%;
            transform: translateX(-50%);
            background: #22c55e;
            color: #0f1a0f;
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
        <p>Bartlett's finest backyard hummers.</p>
    </header>

    <div class="container">
        <div class="refresh-bar">
            Status updates live | <a href="/">Full refresh</a>
        </div>

        <div class="live-feed">
            <h2>Live Camera Feed</h2>
            <div class="feed-container">
                {% if status.camera_error %}
                <div style="width:100%; height:360px; background:#0a140a; border-radius:8px; display:flex; align-items:center; justify-content:center; flex-direction:column;">
                    <div style="font-size:3em; margin-bottom:15px;">&#128247;</div>
                    <div style="color:#dc2626; font-size:1.2em; font-weight:bold;">Camera Unavailable</div>
                    <div style="color:#8faa8f; margin-top:8px; font-size:0.9em;">{{ status.camera_error }}</div>
                    <div style="color:#8faa8f; margin-top:5px; font-size:0.8em;">Retrying every 10 seconds...</div>
                </div>
                {% else %}
                <img src="/feed" alt="Live camera feed" id="liveFeed">
                {% endif %}
                <div class="feed-overlay" id="feedOverlay"></div>
                <audio id="liveAudio" preload="none" muted></audio>
                <button class="btn audio-toggle" id="audioToggle" onclick="toggleAudio()" title="Toggle live audio">
                    <span id="audioIcon">&#128263;</span>
                </button>
            </div>
            <div class="feed-controls">
                <button class="btn btn-mark-yes" onclick="markHummingbird()">I See a Hummingbird!</button>
                <button class="btn btn-mark-no" onclick="markNotHummingbird()">Not a Hummingbird</button>
                <button class="btn" onclick="testMic()" id="testMicBtn" style="background:#2d4a2d;">Test Mic</button>
                <button class="btn" onclick="testRecord()" id="testRecordBtn" style="background:#2d4a2d;">Test Record</button>
                <select id="rotateSelect" onchange="rotateCamera(this.value)" style="background:#2d4a2d; color:#e0e0e0; border:1px solid #333; border-radius:4px; padding:6px 10px; font-size:0.85em;">
                    <option value="0" {% if status.camera_rotation == 0 %}selected{% endif %}>0° (Normal)</option>
                    <option value="90" {% if status.camera_rotation == 90 %}selected{% endif %}>90° CW</option>
                    <option value="180" {% if status.camera_rotation == 180 %}selected{% endif %}>180° (Upside Down)</option>
                    <option value="270" {% if status.camera_rotation == 270 %}selected{% endif %}>270° CW</option>
                </select>
                <span class="training-stats">
                    Training samples: {{ status.training_count }}
                </span>
            </div>
            <div class="detection-legend">
                <span class="legend-dot" style="background:#22c55e;"></span> Confirmed
                <span class="legend-dot" style="background:#f0c040;"></span> Motion detected
                <span class="legend-dot" style="background:#dc2626;"></span> Rejected
                <span class="legend-dot" style="background:#58a6ff;"></span> Verifying...
                <span class="legend-dot" style="background:#a78bfa;"></span> Sleeping
                <span class="legend-dot" style="background:#ef4444;"></span> Camera Error
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
            <p style="color: #8faa8f; margin-top: 15px;">No clips recorded yet. Waiting for hummingbirds...</p>
            {% endif %}
        </div>

        <div class="section">
            <h2>App Status</h2>
            <div class="status-grid">
                <div class="status-card">
                    <div class="label">System Status</div>
                    <div class="value {{ 'green' if status.running else 'red' }}" id="stat-running">
                        {{ 'RUNNING' if status.running else 'STOPPED' }}
                    </div>
                </div>
                <div class="status-card">
                    <div class="label">Detections Today</div>
                    <div class="value green" id="stat-detections">{{ status.detections_today }}</div>
                </div>
                <div class="status-card">
                    <div class="label">Posts Today</div>
                    <div class="value yellow" id="stat-posts">{{ status.posts_today }}</div>
                </div>
                <div class="status-card">
                    <div class="label">Clips Saved</div>
                    <div class="value" id="stat-clips">{{ status.total_clips }}</div>
                </div>
                <div class="status-card">
                    <div class="label">Uptime</div>
                    <div class="value" id="stat-uptime">{{ status.uptime }}</div>
                </div>
                <div class="status-card">
                    <div class="label">Rejected Today</div>
                    <div class="value red" id="stat-rejected">{{ status.rejected_today }}</div>
                </div>
                <div class="status-card">
                    <div class="label">Cooldown</div>
                    <div class="value {{ 'red' if status.in_cooldown else 'green' }}" id="stat-cooldown">
                        {{ 'ACTIVE' if status.in_cooldown else 'READY' }}
                    </div>
                </div>
                <div class="status-card">
                    <div class="label">Facebook Posting</div>
                    <div class="value {{ 'yellow' if status.test_mode else 'green' }}" id="stat-testmode">
                        {{ 'TEST MODE' if status.test_mode else 'LIVE' }}
                    </div>
                    <button class="btn {{ 'btn-cancel' if status.test_mode else 'btn-delete' }}"
                            style="margin-top: 10px; font-size: 0.7em;"
                            id="testModeBtn"
                            onclick="toggleTestMode()">
                        {{ 'Go Live' if status.test_mode else 'Switch to Test' }}
                    </button>
                </div>
                <div class="status-card">
                    <div class="label">Version</div>
                    <div class="value" style="font-size: 0.9em;" id="stat-version">{{ status.git_commit }}</div>
                    <button class="btn btn-update"
                            style="margin-top: 10px; font-size: 0.7em;"
                            id="updateBtn"
                            onclick="checkForUpdate()">
                        Check for Update
                    </button>
                </div>
                <div class="status-card">
                    <div class="label">Schedule</div>
                    <div class="value {{ 'green' if status.schedule.is_daytime else 'purple' }}" style="font-size: 1em;" id="stat-schedule">
                        {{ status.schedule.status }}
                    </div>
                    <div style="font-size: 0.75em; color: #8faa8f; margin-top: 8px;" id="stat-schedule-detail">
                        {{ status.schedule.location }}<br>
                        Rise {{ status.schedule.sunrise }} / Set {{ status.schedule.sunset }}<br>
                        Active {{ status.schedule.wake_time }} - {{ status.schedule.sleep_time }}
                    </div>
                </div>
            </div>
        </div>

        <div class="section">
            <h2>Hardware</h2>
            <div class="status-grid">
                <div class="status-card">
                    <div class="label">CPU Temp</div>
                    <div class="value {{ 'red' if status.cpu_temp and status.cpu_temp > 75 else 'yellow' if status.cpu_temp and status.cpu_temp > 60 else 'green' }}" id="stat-temp">
                        {{ status.cpu_temp_str }}
                    </div>
                </div>
                <div class="status-card">
                    <div class="label">RAM</div>
                    <div class="value {{ 'red' if status.ram_percent > 85 else 'yellow' if status.ram_percent > 70 else 'green' }}" style="font-size: 1.2em;" id="stat-ram">
                        {{ status.ram_str }}
                    </div>
                    <div style="font-size: 0.75em; color: #8faa8f; margin-top: 4px;" id="stat-ram-pct">{{ "%.0f"|format(status.ram_percent) }}% used</div>
                </div>
                <div class="status-card">
                    <div class="label">SD Card</div>
                    <div class="value {{ 'red' if status.disk_percent > 90 else 'yellow' if status.disk_percent > 75 else 'green' }}" style="font-size: 1.2em;" id="stat-disk">
                        {{ status.disk_str }}
                    </div>
                    <div style="font-size: 0.75em; color: #8faa8f; margin-top: 4px;" id="stat-disk-pct">{{ "%.0f"|format(status.disk_percent) }}% used ({{ "%.1f"|format(status.disk_free_gb) }} GB free)</div>
                </div>
                <div class="status-card">
                    <div class="label">Camera</div>
                    <div class="value {{ 'red' if status.camera_error else '' }}" id="stat-camera">{{ status.camera_type }}</div>
                </div>
            </div>
        </div>

        <div class="section">
            <h2>Recent Logs
                <div style="float:right; display:flex; gap:8px;">
                    <select id="logFilter" onchange="filterLogs()" style="background:#0d1117; color:#e0e0e0; border:1px solid #333; border-radius:4px; padding:4px 8px; font-size:0.75em;">
                        <option value="all">All</option>
                        <option value="log-error">Errors</option>
                        <option value="log-warn">Warnings</option>
                        <option value="log-info">Info</option>
                    </select>
                    <button class="btn btn-delete" onclick="clearLogs()" style="font-size:0.6em; padding:4px 12px;">Clear Logs</button>
                </div>
            </h2>
            <div class="log-box" id="logBox">
                {% for line in logs %}
                <div class="log-line {{ line.css_class }}">{{ line.text }}</div>
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
        // Escape HTML to prevent XSS in dynamic content
        function escapeHtml(str) {
            const div = document.createElement('div');
            div.textContent = str;
            return div.innerHTML;
        }

        // Live-update status cards via AJAX (no page reload, no stream flicker)
        function pollStatus() {
            fetch('/api/status')
                .then(r => r.json())
                .then(d => {
                    const el = id => document.getElementById(id);

                    el('stat-running').textContent = d.running ? 'RUNNING' : 'STOPPED';
                    el('stat-running').className = 'value ' + (d.running ? 'green' : 'red');

                    el('stat-detections').textContent = d.detections_today;
                    el('stat-posts').textContent = d.posts_today;
                    el('stat-clips').textContent = d.total_clips;
                    el('stat-uptime').textContent = d.uptime;
                    el('stat-camera').textContent = d.camera_type;
                    el('stat-camera').className = 'value ' + (d.camera_error ? 'red' : '');

                    // If camera was in error and just recovered, reload to show live feed
                    if (window._cameraWasError && !d.camera_error) {
                        location.reload();
                        return;
                    }
                    window._cameraWasError = !!d.camera_error;

                    el('stat-rejected').textContent = d.rejected_today;

                    el('stat-cooldown').textContent = d.in_cooldown ? 'ACTIVE' : 'READY';
                    el('stat-cooldown').className = 'value ' + (d.in_cooldown ? 'red' : 'green');

                    el('stat-testmode').textContent = d.test_mode ? 'TEST MODE' : 'LIVE';
                    el('stat-testmode').className = 'value ' + (d.test_mode ? 'yellow' : 'green');

                    const tmBtn = el('testModeBtn');
                    tmBtn.textContent = d.test_mode ? 'Go Live' : 'Switch to Test';
                    tmBtn.className = 'btn ' + (d.test_mode ? 'btn-cancel' : 'btn-delete');

                    el('stat-version').textContent = d.git_commit;

                    // Pi health
                    if (d.cpu_temp_str) {
                        el('stat-temp').textContent = d.cpu_temp_str;
                        el('stat-temp').className = 'value ' + (d.cpu_temp > 75 ? 'red' : d.cpu_temp > 60 ? 'yellow' : 'green');
                    }
                    if (d.ram_str) {
                        el('stat-ram').textContent = d.ram_str;
                        el('stat-ram').className = 'value ' + (d.ram_percent > 85 ? 'red' : d.ram_percent > 70 ? 'yellow' : 'green');
                        el('stat-ram-pct').textContent = Math.round(d.ram_percent) + '% used';
                    }
                    if (d.disk_str) {
                        el('stat-disk').textContent = d.disk_str;
                        el('stat-disk').className = 'value ' + (d.disk_percent > 90 ? 'red' : d.disk_percent > 75 ? 'yellow' : 'green');
                        el('stat-disk-pct').textContent = Math.round(d.disk_percent) + '% used (' + d.disk_free_gb.toFixed(1) + ' GB free)';
                    }

                    if (d.schedule) {
                        el('stat-schedule').textContent = d.schedule.status;
                        el('stat-schedule').className = 'value ' + (d.schedule.is_daytime ? 'green' : 'purple');
                        el('stat-schedule-detail').innerHTML =
                            d.schedule.location + '<br>' +
                            'Rise ' + d.schedule.sunrise + ' / Set ' + d.schedule.sunset + '<br>' +
                            'Active ' + d.schedule.wake_time + ' - ' + d.schedule.sleep_time;
                    }
                })
                .catch(() => {});
        }
        setInterval(pollStatus, 5000);

        // Poll clips list and update if changed
        let knownClips = new Set([{% for clip in clips %}'{{ clip.name }}',{% endfor %}]);

        function pollClips() {
            fetch('/api/clips/list')
                .then(r => r.json())
                .then(data => {
                    const newNames = new Set(data.clips.map(c => c.name));
                    // Check if clips changed
                    if (newNames.size !== knownClips.size || ![...newNames].every(n => knownClips.has(n))) {
                        // Rebuild clips section
                        const grid = document.querySelector('.clip-grid');
                        if (!grid && data.clips.length > 0) {
                            location.reload();
                            return;
                        }
                        if (grid) {
                            grid.innerHTML = data.clips.map((c, i) => `
                                <div class="clip-card" id="clip-dyn-${i}">
                                    <video controls preload="metadata">
                                        <source src="/clips/${c.name}" type="video/mp4">
                                    </video>
                                    ${c.caption ? `<div class="clip-caption">"${escapeHtml(c.caption)}"</div>` : ''}
                                    <div class="clip-info">${c.name} &mdash; ${c.size} &mdash; ${c.time}</div>
                                    <div class="clip-actions">
                                        <button class="btn btn-delete" onclick="deleteClip('${c.name}', 'clip-dyn-${i}')">Delete Clip</button>
                                    </div>
                                </div>
                            `).join('');
                        }
                        knownClips = newNames;
                    }
                })
                .catch(() => {});
        }
        setInterval(pollClips, 10000);

        // Poll logs and update
        function pollLogs() {
            const filter = document.getElementById('logFilter').value;
            fetch('/api/logs')
                .then(r => r.json())
                .then(data => {
                    const box = document.getElementById('logBox');
                    box.innerHTML = data.logs.map(l => {
                        const display = (filter === 'all' || l.css_class === filter) ? '' : 'display:none;';
                        return `<div class="log-line ${escapeHtml(l.css_class)}" style="${display}">${escapeHtml(l.text)}</div>`;
                    }).join('');
                })
                .catch(() => {});
        }
        setInterval(pollLogs, 5000);

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
                            'sleeping': 'SLEEPING',
                            'camera_error': 'CAMERA ERROR',
                        };
                        const colors = {
                            'motion': '#f0c040',
                            'verifying': '#58a6ff',
                            'confirmed': '#22c55e',
                            'rejected': '#dc2626',
                            'sleeping': '#a78bfa',
                            'camera_error': '#ef4444',
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

        // Live audio toggle
        let audioActive = false;
        function toggleAudio() {
            const audio = document.getElementById('liveAudio');
            const btn = document.getElementById('audioToggle');
            const icon = document.getElementById('audioIcon');

            if (!audioActive) {
                audio.src = '/feed/audio';
                audio.muted = false;
                audio.play().catch(() => showToast('Browser blocked autoplay — click again'));
                btn.classList.add('active');
                icon.innerHTML = '&#128264;';
                audioActive = true;
            } else {
                audio.pause();
                audio.src = '';
                btn.classList.remove('active');
                icon.innerHTML = '&#128263;';
                audioActive = false;
            }
        }

        // Test mic — record 3 seconds and play back
        function testMic() {
            const btn = document.getElementById('testMicBtn');
            btn.textContent = 'Recording...';
            btn.disabled = true;

            fetch('/api/audio/test')
                .then(r => {
                    if (!r.ok) throw new Error('Mic test failed');
                    return r.blob();
                })
                .then(blob => {
                    const url = URL.createObjectURL(blob);
                    const testAudio = new Audio(url);
                    testAudio.play();
                    testAudio.onended = () => URL.revokeObjectURL(url);
                    btn.textContent = 'Test Mic';
                    btn.disabled = false;
                    showToast('Playing mic test...');
                })
                .catch(() => {
                    btn.textContent = 'Test Mic';
                    btn.disabled = false;
                    showToast('Mic test failed — check logs');
                });
        }

        // Rotate camera
        function rotateCamera(angle) {
            fetch('/api/camera/rotate', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({rotation: parseInt(angle)})
            })
            .then(r => r.json())
            .then(data => {
                if (data.ok) showToast('Camera rotated to ' + angle + '°');
                else showToast('Rotation failed');
            })
            .catch(() => showToast('Rotation failed'));
        }

        // Test Record — trigger full pipeline
        function testRecord() {
            const btn = document.getElementById('testRecordBtn');
            btn.textContent = 'Recording...';
            btn.disabled = true;

            fetch('/api/test-record', { method: 'POST' })
                .then(r => r.json())
                .then(data => {
                    if (data.ok) {
                        showToast('Recording started! Clip will appear in ~25 seconds.');
                    } else {
                        showToast('Error: ' + (data.error || 'unknown'));
                    }
                    btn.textContent = 'Test Record';
                    btn.disabled = false;
                })
                .catch(() => {
                    btn.textContent = 'Test Record';
                    btn.disabled = false;
                    showToast('Test record failed — check logs');
                });
        }

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

        function clearLogs() {
            if (!confirm('Clear all log entries?')) return;
            fetch('/api/logs/clear', { method: 'POST' })
                .then(r => r.json())
                .then(data => {
                    if (data.ok) {
                        document.getElementById('logBox').innerHTML = '';
                        showToast('Logs cleared');
                    }
                })
                .catch(() => showToast('Failed to clear logs'));
        }

        function filterLogs() {
            const filter = document.getElementById('logFilter').value;
            document.querySelectorAll('#logBox .log-line').forEach(el => {
                if (filter === 'all' || el.classList.contains(filter)) {
                    el.style.display = '';
                } else {
                    el.style.display = 'none';
                }
            });
        }

        function showToast(msg) {
            const t = document.getElementById('toast');
            t.textContent = msg;
            t.classList.add('active');
            setTimeout(() => t.classList.remove('active'), 3000);
        }

        function deleteClip(filename, cardId) {
            if (!confirm('Delete ' + filename + ' and its caption?')) return;

            fetch('/api/clips/' + filename, { method: 'DELETE' })
                .then(r => r.json())
                .then(data => {
                    if (data.ok) {
                        document.getElementById(cardId).remove();
                        showToast('Deleted ' + filename);
                    } else {
                        showToast('Error: ' + data.error);
                    }
                })
                .catch(() => showToast('Delete failed'));
        }

        function showDeleteAll() {
            document.getElementById('deleteAllOverlay').classList.add('active');
        }

        function hideDeleteAll() {
            document.getElementById('deleteAllOverlay').classList.remove('active');
        }

        function toggleTestMode() {
            fetch('/api/test-mode', { method: 'POST' })
                .then(r => r.json())
                .then(data => {
                    if (data.ok) {
                        showToast(data.test_mode ? 'Switched to TEST MODE' : 'Facebook posting is LIVE');
                        pollStatus();
                    }
                })
                .catch(() => showToast('Toggle failed'));
        }

        function checkForUpdate() {
            const btn = document.getElementById('updateBtn');
            btn.disabled = true;
            btn.textContent = 'Checking...';

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
                    } else {
                        showToast('Update failed: ' + (data.error || 'unknown'));
                        btn.disabled = false;
                        btn.textContent = 'Check for Update';
                    }
                })
                .catch(() => {
                    showToast('Update request failed');
                    btn.disabled = false;
                    btn.textContent = 'Check for Update';
                });
        }

        function deleteAll() {
            fetch('/api/clips', { method: 'DELETE' })
                .then(r => r.json())
                .then(data => {
                    if (data.ok) {
                        showToast('Deleted ' + data.deleted + ' clips');
                        hideDeleteAll();
                        // Remove all clip cards from the DOM
                        document.querySelectorAll('.clip-card').forEach(c => c.remove());
                        pollStatus();
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
    return render_template_string(
        DASHBOARD_HTML,
        status=_get_status(),
        clips=_get_recent_clips(),
        logs=_get_recent_logs(),
        video_width=config.VIDEO_WIDTH,
    )


@app.route("/clips/<filename>")
def serve_clip(filename):
    if "/" in filename or "\\" in filename or ".." in filename:
        return {"ok": False, "error": "Invalid filename"}, 400
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
    if _monitor is None or not _monitor.running or not _monitor.camera.is_available:
        return Response("Camera not available", status=503)

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
    """Trigger a test recording — full pipeline without Facebook posting."""
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
                # Generate caption
                caption = generate_comment()
                # Save caption alongside clip
                caption_file = clip_path.with_suffix(".txt")
                caption_file.write_text(caption)
                logger.info("Test record complete: %s — caption: %s", clip_path.name, caption)
            else:
                logger.warning("Test record failed — no clip produced")
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


@app.route("/api/clips/list")
def api_clips_list():
    """JSON list of recent clips for live updates."""
    clips = _get_recent_clips()
    return {"clips": [
        {"name": c["name"], "size": c["size"], "time": c["time"], "caption": c.get("caption", "")}
        for c in clips
    ]}


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
