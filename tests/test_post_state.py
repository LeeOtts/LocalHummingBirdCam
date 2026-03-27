"""Tests for _load_post_state and _save_post_state from main.py.

These methods manage daily morning/night post flags persisted as JSON.
Uses tmp_path fixture to avoid touching real state files.

Post state now uses timestamp-based tracking (Unix time) to survive clock jumps.
Posts are active if they were made < 24 hours ago.
"""

import json
import time
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def _make_monitor(state_file: Path):
    """Create a HummingbirdMonitor-like object with a custom state file path.

    We avoid instantiating the real HummingbirdMonitor (which starts camera, etc.)
    and instead create a minimal object with the methods under test.
    """
    from main import HummingbirdMonitor

    # Create a bare instance without calling __init__
    monitor = object.__new__(HummingbirdMonitor)
    monitor._state_file = state_file
    monitor._morning_posted = False
    monitor._night_posted = False
    monitor._digest_posted = False
    monitor._yesterday_detections = 0
    monitor._all_time_record = 0
    monitor._total_lifetime_detections = 0
    return monitor


class TestLoadPostState:
    """Test _load_post_state() reading persisted morning/night flags using timestamps."""

    def test_returns_false_false_when_no_file(self, tmp_path):
        """When no state file exists, returns (False, False, False)."""
        state_file = tmp_path / ".post_state.json"
        monitor = _make_monitor(state_file)

        result = monitor._load_post_state()
        assert result == (False, False, False)

    def test_returns_true_when_posted_recently(self, tmp_path):
        """When post was made < 24h ago, returns True."""
        state_file = tmp_path / ".post_state.json"
        now = time.time()
        one_hour_ago = now - 3600  # 1 hour ago
        
        state_file.write_text(json.dumps({
            "last_morning_post_ts": one_hour_ago,
            "last_night_post_ts": 0,
            "date": str(date.today()),
        }))

        monitor = _make_monitor(state_file)
        result = monitor._load_post_state()
        assert result == (True, False, False)

    def test_returns_saved_both_true_when_recent(self, tmp_path):
        """When both posts were made < 24h ago, returns (True, True, False)."""
        state_file = tmp_path / ".post_state.json"
        now = time.time()
        one_hour_ago = now - 3600
        
        state_file.write_text(json.dumps({
            "last_morning_post_ts": one_hour_ago,
            "last_night_post_ts": one_hour_ago,
            "date": str(date.today()),
        }))

        monitor = _make_monitor(state_file)
        result = monitor._load_post_state()
        assert result == (True, True, False)

    def test_returns_false_when_posted_over_24h_ago(self, tmp_path):
        """When posts were made > 24h ago, returns (False, False, False)."""
        state_file = tmp_path / ".post_state.json"
        now = time.time()
        # 25 hours ago (more than 24 hours)
        twenty_five_hours_ago = now - (25 * 3600)
        
        state_file.write_text(json.dumps({
            "last_morning_post_ts": twenty_five_hours_ago,
            "last_night_post_ts": twenty_five_hours_ago,
            "date": str(date.today()),
        }))

        monitor = _make_monitor(state_file)
        result = monitor._load_post_state()
        assert result == (False, False, False)

    def test_returns_false_when_timestamp_zero(self, tmp_path):
        """When timestamp is 0 (never posted), returns False."""
        state_file = tmp_path / ".post_state.json"
        state_file.write_text(json.dumps({
            "last_morning_post_ts": 0,
            "last_night_post_ts": 0,
            "last_digest_post_ts": 0,
            "date": str(date.today()),
        }))

        monitor = _make_monitor(state_file)
        result = monitor._load_post_state()
        assert result == (False, False, False)

    def test_handles_corrupted_json(self, tmp_path):
        """When state file contains invalid JSON, returns (False, False, False)."""
        state_file = tmp_path / ".post_state.json"
        state_file.write_text("{{not valid json!!")

        monitor = _make_monitor(state_file)
        result = monitor._load_post_state()
        assert result == (False, False, False)

    def test_handles_empty_file(self, tmp_path):
        """When state file is empty, returns (False, False, False)."""
        state_file = tmp_path / ".post_state.json"
        state_file.write_text("")

        monitor = _make_monitor(state_file)
        result = monitor._load_post_state()
        assert result == (False, False, False)

    def test_handles_missing_keys(self, tmp_path):
        """When state file has date but missing timestamp keys, defaults to False."""
        state_file = tmp_path / ".post_state.json"
        state_file.write_text(json.dumps({
            "date": str(date.today()),
        }))

        monitor = _make_monitor(state_file)
        result = monitor._load_post_state()
        assert result == (False, False, False)

    def test_mixed_recent_and_old_posts(self, tmp_path):
        """When one post is recent and one is old, returns appropriate mix."""
        state_file = tmp_path / ".post_state.json"
        now = time.time()
        one_hour_ago = now - 3600
        twenty_five_hours_ago = now - (25 * 3600)
        
        state_file.write_text(json.dumps({
            "last_morning_post_ts": one_hour_ago,
            "last_night_post_ts": twenty_five_hours_ago,
            "date": str(date.today()),
        }))

        monitor = _make_monitor(state_file)
        result = monitor._load_post_state()
        assert result == (True, False, False)


class TestSavePostState:
    """Test _save_post_state() writing state to disk using timestamps."""

    def test_saves_correct_json(self, tmp_path):
        """Saved JSON contains today's date and current timestamp flags."""
        state_file = tmp_path / ".post_state.json"
        monitor = _make_monitor(state_file)
        monitor._morning_posted = True
        monitor._night_posted = False

        monitor._save_post_state()

        assert state_file.exists()
        data = json.loads(state_file.read_text())
        assert data["date"] == str(date.today())
        # When _morning_posted is True, last_morning_post_ts should be set to now
        # When _night_posted is False, last_night_post_ts should be 0
        assert data["last_morning_post_ts"] > 0
        assert data["last_night_post_ts"] == 0

    def test_saves_both_true(self, tmp_path):
        """Both flags set to True save timestamps > 0."""
        state_file = tmp_path / ".post_state.json"
        monitor = _make_monitor(state_file)
        monitor._morning_posted = True
        monitor._night_posted = True

        monitor._save_post_state()

        data = json.loads(state_file.read_text())
        assert data["last_morning_post_ts"] > 0
        assert data["last_night_post_ts"] > 0

    def test_saves_both_false(self, tmp_path):
        """Both flags set to False save timestamps as 0."""
        state_file = tmp_path / ".post_state.json"
        monitor = _make_monitor(state_file)
        monitor._morning_posted = False
        monitor._night_posted = False

        monitor._save_post_state()

        data = json.loads(state_file.read_text())
        assert data["last_morning_post_ts"] == 0
        assert data["last_night_post_ts"] == 0

    def test_overwrites_existing_file(self, tmp_path):
        """Saving overwrites any existing state file."""
        state_file = tmp_path / ".post_state.json"
        state_file.write_text(json.dumps({"old": "data"}))

        monitor = _make_monitor(state_file)
        monitor._morning_posted = True
        monitor._night_posted = True

        monitor._save_post_state()

        data = json.loads(state_file.read_text())
        assert "old" not in data
        assert data["last_morning_post_ts"] > 0

    def test_handles_write_error_gracefully(self, tmp_path):
        """If writing fails, _save_post_state does not raise."""
        state_file = tmp_path / "nonexistent_dir" / ".post_state.json"
        monitor = _make_monitor(state_file)
        monitor._morning_posted = True
        monitor._night_posted = False

        # Should not raise (logs exception internally)
        monitor._save_post_state()

    def test_saves_engagement_stats(self, tmp_path):
        """Engagement stats (lifetime, record, yesterday) are saved."""
        state_file = tmp_path / ".post_state.json"
        monitor = _make_monitor(state_file)
        monitor._morning_posted = True
        monitor._total_lifetime_detections = 275
        monitor._all_time_record = 15
        monitor._yesterday_detections = 12

        monitor._save_post_state()

        data = json.loads(state_file.read_text())
        assert data["total_lifetime_detections"] == 275
        assert data["all_time_record"] == 15
        assert data["yesterday_detections"] == 12
