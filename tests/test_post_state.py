"""Tests for _load_post_state and _save_post_state from main.py.

These methods manage daily morning/night post flags persisted as JSON.
Uses tmp_path fixture to avoid touching real state files.
"""

import json
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
    return monitor


class TestLoadPostState:
    """Test _load_post_state() reading persisted morning/night flags."""

    def test_returns_false_false_when_no_file(self, tmp_path):
        """When no state file exists, returns (False, False)."""
        state_file = tmp_path / ".post_state.json"
        monitor = _make_monitor(state_file)

        result = monitor._load_post_state()
        assert result == (False, False)

    def test_returns_saved_values_when_date_matches(self, tmp_path):
        """When state file date matches today, returns saved values."""
        state_file = tmp_path / ".post_state.json"
        state_file.write_text(json.dumps({
            "date": str(date.today()),
            "morning_posted": True,
            "night_posted": False,
        }))

        monitor = _make_monitor(state_file)
        result = monitor._load_post_state()
        assert result == (True, False)

    def test_returns_saved_both_true(self, tmp_path):
        """When both flags are True and date matches, returns (True, True)."""
        state_file = tmp_path / ".post_state.json"
        state_file.write_text(json.dumps({
            "date": str(date.today()),
            "morning_posted": True,
            "night_posted": True,
        }))

        monitor = _make_monitor(state_file)
        result = monitor._load_post_state()
        assert result == (True, True)

    def test_returns_false_false_when_stale_date(self, tmp_path):
        """When state file date is yesterday, returns (False, False)."""
        state_file = tmp_path / ".post_state.json"
        yesterday = date.today() - timedelta(days=1)
        state_file.write_text(json.dumps({
            "date": str(yesterday),
            "morning_posted": True,
            "night_posted": True,
        }))

        monitor = _make_monitor(state_file)
        result = monitor._load_post_state()
        assert result == (False, False)

    def test_returns_false_false_when_old_date(self, tmp_path):
        """When state file date is several days old, returns (False, False)."""
        state_file = tmp_path / ".post_state.json"
        old_date = date.today() - timedelta(days=30)
        state_file.write_text(json.dumps({
            "date": str(old_date),
            "morning_posted": True,
            "night_posted": True,
        }))

        monitor = _make_monitor(state_file)
        result = monitor._load_post_state()
        assert result == (False, False)

    def test_handles_corrupted_json(self, tmp_path):
        """When state file contains invalid JSON, returns (False, False)."""
        state_file = tmp_path / ".post_state.json"
        state_file.write_text("{{not valid json!!")

        monitor = _make_monitor(state_file)
        result = monitor._load_post_state()
        assert result == (False, False)

    def test_handles_empty_file(self, tmp_path):
        """When state file is empty, returns (False, False)."""
        state_file = tmp_path / ".post_state.json"
        state_file.write_text("")

        monitor = _make_monitor(state_file)
        result = monitor._load_post_state()
        assert result == (False, False)

    def test_handles_missing_keys(self, tmp_path):
        """When state file has date but missing morning/night keys, defaults to False."""
        state_file = tmp_path / ".post_state.json"
        state_file.write_text(json.dumps({
            "date": str(date.today()),
        }))

        monitor = _make_monitor(state_file)
        result = monitor._load_post_state()
        assert result == (False, False)


class TestSavePostState:
    """Test _save_post_state() writing state to disk."""

    def test_saves_correct_json(self, tmp_path):
        """Saved JSON contains today's date and the current flag values."""
        state_file = tmp_path / ".post_state.json"
        monitor = _make_monitor(state_file)
        monitor._morning_posted = True
        monitor._night_posted = False

        monitor._save_post_state()

        assert state_file.exists()
        data = json.loads(state_file.read_text())
        assert data["date"] == str(date.today())
        assert data["morning_posted"] is True
        assert data["night_posted"] is False

    def test_saves_both_true(self, tmp_path):
        """Both flags set to True are saved correctly."""
        state_file = tmp_path / ".post_state.json"
        monitor = _make_monitor(state_file)
        monitor._morning_posted = True
        monitor._night_posted = True

        monitor._save_post_state()

        data = json.loads(state_file.read_text())
        assert data["morning_posted"] is True
        assert data["night_posted"] is True

    def test_saves_both_false(self, tmp_path):
        """Both flags set to False are saved correctly."""
        state_file = tmp_path / ".post_state.json"
        monitor = _make_monitor(state_file)
        monitor._morning_posted = False
        monitor._night_posted = False

        monitor._save_post_state()

        data = json.loads(state_file.read_text())
        assert data["morning_posted"] is False
        assert data["night_posted"] is False

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
        assert data["morning_posted"] is True

    def test_handles_write_error_gracefully(self, tmp_path):
        """If writing fails, _save_post_state does not raise."""
        state_file = tmp_path / "nonexistent_dir" / ".post_state.json"
        monitor = _make_monitor(state_file)
        monitor._morning_posted = True
        monitor._night_posted = False

        # Should not raise (logs exception internally)
        monitor._save_post_state()
