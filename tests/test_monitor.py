"""Tests for HummingbirdMonitor helper methods in main.py."""

import os
import threading
import time
from pathlib import Path
from queue import Queue
from unittest.mock import MagicMock, patch, call

import pytest


def _make_monitor(tmp_path):
    """Create a minimal HummingbirdMonitor without starting camera/web server."""
    from main import HummingbirdMonitor
    m = object.__new__(HummingbirdMonitor)
    m._state_file = tmp_path / ".post_state.json"
    m._morning_posted = False
    m._night_posted = False
    m._yesterday_detections = 0
    m._all_time_record = 0
    m._total_lifetime_detections = 0
    m._detections_today = 0
    m._rejected_today = 0
    m._detection_hours = []
    m._last_detection_time = 0.0
    m._counter_lock = threading.Lock()
    m.test_mode = False
    m.poster = MagicMock()
    m.poster_manager = MagicMock()
    m.sightings_db = MagicMock()
    m.camera = MagicMock()
    m._sse_subscribers = []
    return m


class TestGetDayPart:
    """Test _get_day_part() returns the correct label for each hour."""

    @pytest.mark.parametrize("hour,expected", [
        (0, "early morning"),
        (7, "early morning"),
        (8, "mid-morning"),
        (10, "mid-morning"),
        (11, "midday"),
        (13, "midday"),
        (14, "afternoon"),
        (16, "afternoon"),
        (17, "evening"),
        (23, "evening"),
    ])
    def test_day_part(self, hour, expected):
        from main import _get_day_part
        assert _get_day_part(hour) == expected


class TestCooldownElapsed:
    """Test _cooldown_elapsed() timing logic."""

    def test_elapsed_when_enough_time_passed(self, tmp_path, monkeypatch):
        import config
        import main
        m = _make_monitor(tmp_path)
        m._last_detection_time = 1000.0
        monkeypatch.setattr(config, "DETECTION_COOLDOWN_SECONDS", 60)
        with patch("main.time.time", return_value=1061.0):
            assert m._cooldown_elapsed() is True

    def test_not_elapsed_when_too_soon(self, tmp_path, monkeypatch):
        import config
        m = _make_monitor(tmp_path)
        m._last_detection_time = 1000.0
        monkeypatch.setattr(config, "DETECTION_COOLDOWN_SECONDS", 60)
        with patch("main.time.time", return_value=1059.0):
            assert m._cooldown_elapsed() is False

    def test_elapsed_when_never_detected(self, tmp_path, monkeypatch):
        import config
        m = _make_monitor(tmp_path)
        m._last_detection_time = 0.0
        monkeypatch.setattr(config, "DETECTION_COOLDOWN_SECONDS", 60)
        with patch("main.time.time", return_value=999999.0):
            assert m._cooldown_elapsed() is True


class TestCleanupOldClips:
    """Test _cleanup_old_clips() disk-management logic."""

    def test_no_deletion_when_under_quota(self, tmp_path, monkeypatch):
        import config
        monkeypatch.setattr(config, "CLIPS_DIR", tmp_path)
        monkeypatch.setattr(config, "MAX_CLIPS_DISK_MB", 1)  # 1 MB limit
        m = _make_monitor(tmp_path / "state")
        (tmp_path / "state").mkdir()

        clip = tmp_path / "hummer_001.mp4"
        clip.write_bytes(b"x" * 10)  # 10 bytes, well under 1 MB

        m._cleanup_old_clips()
        assert clip.exists()

    def test_deletes_oldest_when_over_quota(self, tmp_path, monkeypatch):
        import config
        monkeypatch.setattr(config, "CLIPS_DIR", tmp_path)
        # Set quota so only about 2 files fit (each ~100 bytes, 3 total = 300 bytes)
        monkeypatch.setattr(config, "MAX_CLIPS_DISK_MB", 200 / 1_048_576)
        m = _make_monitor(tmp_path / "state")
        (tmp_path / "state").mkdir()

        old = tmp_path / "hummer_001.mp4"
        mid = tmp_path / "hummer_002.mp4"
        new = tmp_path / "hummer_003.mp4"

        old.write_bytes(b"x" * 100)
        time.sleep(0.02)
        mid.write_bytes(b"x" * 100)
        time.sleep(0.02)
        new.write_bytes(b"x" * 100)

        m._cleanup_old_clips()

        assert not old.exists()
        assert mid.exists()
        assert new.exists()

    def test_deletes_caption_txt_alongside_clip(self, tmp_path, monkeypatch):
        import config
        monkeypatch.setattr(config, "CLIPS_DIR", tmp_path)
        monkeypatch.setattr(config, "MAX_CLIPS_DISK_MB", 0.000001)  # ~1 byte
        m = _make_monitor(tmp_path / "state")
        (tmp_path / "state").mkdir()

        clip = tmp_path / "hummer_001.mp4"
        txt = tmp_path / "hummer_001.txt"
        clip.write_bytes(b"x" * 100)
        txt.write_text("caption text")

        m._cleanup_old_clips()

        assert not clip.exists()
        assert not txt.exists()

    def test_no_error_when_clips_dir_missing(self, tmp_path, monkeypatch):
        import config
        monkeypatch.setattr(config, "CLIPS_DIR", tmp_path / "nonexistent")
        monkeypatch.setattr(config, "MAX_CLIPS_DISK_MB", 1)
        m = _make_monitor(tmp_path / "state")
        (tmp_path / "state").mkdir()

        # Should not raise
        m._cleanup_old_clips()

    def test_stops_deleting_when_under_quota(self, tmp_path, monkeypatch):
        import config
        monkeypatch.setattr(config, "CLIPS_DIR", tmp_path)
        # Allow 2 files worth of space (each ~100 bytes)
        monkeypatch.setattr(config, "MAX_CLIPS_DISK_MB", 200 / 1_048_576)
        m = _make_monitor(tmp_path / "state")
        (tmp_path / "state").mkdir()

        clips = []
        for i in range(3):
            c = tmp_path / f"hummer_00{i}.mp4"
            c.write_bytes(b"x" * 100)
            time.sleep(0.02)
            clips.append(c)

        m._cleanup_old_clips()

        # Only the oldest is deleted
        assert not clips[0].exists()
        assert clips[1].exists()
        assert clips[2].exists()


class TestGetTodaysClip:
    """Test _get_todays_clip() returns the most recent clip from today."""

    def _today_str(self):
        from datetime import datetime
        try:
            from zoneinfo import ZoneInfo
        except ImportError:
            from pytz import timezone as _pytz_tz
            ZoneInfo = lambda k: _pytz_tz(k)
        import config
        tz = ZoneInfo(config.LOCATION_TIMEZONE)
        return datetime.now(tz=tz).strftime("%Y%m%d")

    def test_returns_none_when_no_clips(self, tmp_path, monkeypatch):
        import config
        monkeypatch.setattr(config, "CLIPS_DIR", tmp_path)
        m = _make_monitor(tmp_path / "state")
        (tmp_path / "state").mkdir()

        assert m._get_todays_clip() is None

    def test_returns_most_recent_clip_from_today(self, tmp_path, monkeypatch):
        import config
        monkeypatch.setattr(config, "CLIPS_DIR", tmp_path)
        m = _make_monitor(tmp_path / "state")
        (tmp_path / "state").mkdir()

        today = self._today_str()
        clip1 = tmp_path / f"hummer_{today}_090000.mp4"
        clip2 = tmp_path / f"hummer_{today}_100000.mp4"
        clip1.write_bytes(b"data1")
        time.sleep(0.02)
        clip2.write_bytes(b"data2")

        # Set mtimes explicitly
        os.utime(clip1, (1000, 1000))
        os.utime(clip2, (2000, 2000))

        result = m._get_todays_clip()
        assert result == clip2

    def test_ignores_clips_from_other_days(self, tmp_path, monkeypatch):
        import config
        monkeypatch.setattr(config, "CLIPS_DIR", tmp_path)
        m = _make_monitor(tmp_path / "state")
        (tmp_path / "state").mkdir()

        # Use yesterday's date
        clip = tmp_path / "hummer_20200101_090000.mp4"
        clip.write_bytes(b"old data")

        assert m._get_todays_clip() is None

    def test_returns_none_when_dir_missing(self, tmp_path, monkeypatch):
        import config
        monkeypatch.setattr(config, "CLIPS_DIR", tmp_path / "nonexistent")
        m = _make_monitor(tmp_path / "state")
        (tmp_path / "state").mkdir()

        assert m._get_todays_clip() is None


class TestPostGoodnight:
    """Test _post_goodnight() peak hour, records, stat rotation, and media fallback."""

    _SCHEDULE = {
        "sunset": "7:30 PM",
        "sunrise": "6:00 AM",
        "wake_time": "5:30 AM",
        "sleep_time": "8:00 PM",
    }

    def _make(self, tmp_path):
        m = _make_monitor(tmp_path)
        # Avoid actual file I/O
        m._save_post_state = MagicMock()
        return m

    def test_calculates_peak_hour_correctly(self, tmp_path):
        m = self._make(tmp_path)
        m._detection_hours = [9, 9, 9, 14, 14]
        with patch("main.generate_good_night") as mock_gn, \
             patch("main.get_schedule_info", return_value=self._SCHEDULE):
            m._post_goodnight(self._SCHEDULE)
        call_kwargs = mock_gn.call_args[1]
        assert call_kwargs["peak_hour"] == "9 AM"

    def test_no_peak_hour_when_no_detections(self, tmp_path):
        m = self._make(tmp_path)
        m._detection_hours = []
        with patch("main.generate_good_night") as mock_gn, \
             patch("main.get_schedule_info", return_value=self._SCHEDULE):
            m._post_goodnight(self._SCHEDULE)
        call_kwargs = mock_gn.call_args[1]
        assert call_kwargs["peak_hour"] is None

    def test_pm_peak_hour(self, tmp_path):
        m = self._make(tmp_path)
        m._detection_hours = [14, 14, 14]
        with patch("main.generate_good_night") as mock_gn, \
             patch("main.get_schedule_info", return_value=self._SCHEDULE):
            m._post_goodnight(self._SCHEDULE)
        call_kwargs = mock_gn.call_args[1]
        assert call_kwargs["peak_hour"] == "2 PM"

    def test_noon_peak_hour(self, tmp_path):
        m = self._make(tmp_path)
        m._detection_hours = [12, 12]
        with patch("main.generate_good_night") as mock_gn, \
             patch("main.get_schedule_info", return_value=self._SCHEDULE):
            m._post_goodnight(self._SCHEDULE)
        call_kwargs = mock_gn.call_args[1]
        assert call_kwargs["peak_hour"] == "12 PM"

    def test_sets_is_record_when_new_high(self, tmp_path):
        m = self._make(tmp_path)
        m._detections_today = 10
        m._all_time_record = 5
        with patch("main.generate_good_night") as mock_gn, \
             patch("main.get_schedule_info", return_value=self._SCHEDULE):
            m._post_goodnight(self._SCHEDULE)
        call_kwargs = mock_gn.call_args[1]
        assert call_kwargs["is_record"] is True

    def test_not_record_when_not_new_high(self, tmp_path):
        m = self._make(tmp_path)
        m._detections_today = 3
        m._all_time_record = 10
        with patch("main.generate_good_night") as mock_gn, \
             patch("main.get_schedule_info", return_value=self._SCHEDULE):
            m._post_goodnight(self._SCHEDULE)
        call_kwargs = mock_gn.call_args[1]
        assert call_kwargs["is_record"] is False

    def test_not_record_when_zero_detections(self, tmp_path):
        m = self._make(tmp_path)
        m._detections_today = 0
        m._all_time_record = 0
        with patch("main.generate_good_night") as mock_gn, \
             patch("main.get_schedule_info", return_value=self._SCHEDULE):
            m._post_goodnight(self._SCHEDULE)
        call_kwargs = mock_gn.call_args[1]
        assert call_kwargs["is_record"] is False

    def test_rotates_yesterday_detections(self, tmp_path):
        m = self._make(tmp_path)
        m._detections_today = 7
        with patch("main.generate_good_night"), \
             patch("main.get_schedule_info", return_value=self._SCHEDULE):
            m._post_goodnight(self._SCHEDULE)
        assert m._yesterday_detections == 7

    def test_updates_all_time_record(self, tmp_path):
        m = self._make(tmp_path)
        m._detections_today = 10
        m._all_time_record = 5
        with patch("main.generate_good_night"), \
             patch("main.get_schedule_info", return_value=self._SCHEDULE):
            m._post_goodnight(self._SCHEDULE)
        assert m._all_time_record == 10

    def test_resets_daily_counters(self, tmp_path):
        m = self._make(tmp_path)
        m._detections_today = 5
        m._rejected_today = 2
        m._detection_hours = [8, 9]
        with patch("main.generate_good_night"), \
             patch("main.get_schedule_info", return_value=self._SCHEDULE):
            m._post_goodnight(self._SCHEDULE)
        assert m._detections_today == 0
        assert m._rejected_today == 0
        assert m._detection_hours == []

    def test_test_mode_skips_posting(self, tmp_path):
        m = self._make(tmp_path)
        m.test_mode = True
        with patch("main.generate_good_night"), \
             patch("main.get_schedule_info", return_value=self._SCHEDULE):
            m._post_goodnight(self._SCHEDULE)
        m.poster_manager.post_video.assert_not_called()

    def test_posts_video_when_clip_available(self, tmp_path):
        m = self._make(tmp_path)
        fake_clip = Path("/tmp/clip.mp4")
        m._get_todays_clip = MagicMock(return_value=fake_clip)
        m.poster_manager.post_video.return_value = {"Facebook": True}
        with patch("main.generate_good_night", return_value="caption"), \
             patch("main.get_schedule_info", return_value=self._SCHEDULE):
            m._post_goodnight(self._SCHEDULE)
        m.poster_manager.post_video.assert_called_once_with(fake_clip, "caption")

    def test_falls_back_to_snapshot_when_no_clip(self, tmp_path):
        m = self._make(tmp_path)
        m._get_todays_clip = MagicMock(return_value=None)
        m.camera.capture_snapshot.return_value = True
        m.poster_manager.post_photo.return_value = {"Facebook": True}
        with patch("main.generate_good_night", return_value="caption"), \
             patch("main.get_schedule_info", return_value=self._SCHEDULE):
            m._post_goodnight(self._SCHEDULE)
        m.poster_manager.post_photo.assert_called_once()

    def test_falls_back_to_text_when_no_media(self, tmp_path):
        m = self._make(tmp_path)
        m._get_todays_clip = MagicMock(return_value=None)
        m.camera.capture_snapshot.return_value = False
        with patch("main.generate_good_night", return_value="caption"), \
             patch("main.get_schedule_info", return_value=self._SCHEDULE):
            m._post_goodnight(self._SCHEDULE)
        m.poster_manager.post_text.assert_called_once_with("caption")


class TestPostWorker:
    """Test _post_worker() processes clips from the queue."""

    def _run_worker_with_clip(self, monitor, clip_path, timeout=3.0):
        """Put clip in queue, run worker in thread, stop after processing."""
        monitor.running = True
        monitor.clip_queue = Queue()
        monitor.clip_queue.put(clip_path)

        t = threading.Thread(target=monitor._post_worker, daemon=True)
        t.start()

        # Give the worker time to process the item
        time.sleep(0.5)
        monitor.running = False
        t.join(timeout=timeout)

    def test_milestone_included_in_caption(self, tmp_path):
        m = _make_monitor(tmp_path)
        m._total_lifetime_detections = 100
        m._last_detection_time = time.time() - 30

        clip_path = tmp_path / "hummer_test.mp4"
        clip_path.write_bytes(b"fake video data")

        with patch("main.generate_comment", return_value="test caption") as mock_gen, \
             patch("main.get_schedule_info", return_value={"sunrise": "6 AM", "sunset": "8 PM"}):
            self._run_worker_with_clip(m, clip_path)

        mock_gen.assert_called_once()
        call_kwargs = mock_gen.call_args[1]
        assert call_kwargs.get("milestone") == "Lifetime visitor #100!"

    def test_no_milestone_when_not_at_threshold(self, tmp_path):
        m = _make_monitor(tmp_path)
        m._total_lifetime_detections = 99
        m._last_detection_time = time.time() - 30

        clip_path = tmp_path / "hummer_test.mp4"
        clip_path.write_bytes(b"fake video data")

        with patch("main.generate_comment", return_value="test caption") as mock_gen, \
             patch("main.get_schedule_info", return_value={"sunrise": "6 AM", "sunset": "8 PM"}):
            self._run_worker_with_clip(m, clip_path)

        call_kwargs = mock_gen.call_args[1]
        assert call_kwargs.get("milestone") is None

    def test_seconds_since_last_in_range(self, tmp_path):
        m = _make_monitor(tmp_path)
        m._last_detection_time = time.time() - 30

        clip_path = tmp_path / "hummer_test.mp4"
        clip_path.write_bytes(b"fake video data")

        with patch("main.generate_comment", return_value="test caption") as mock_gen, \
             patch("main.get_schedule_info", return_value={"sunrise": "6 AM", "sunset": "8 PM"}):
            self._run_worker_with_clip(m, clip_path)

        call_kwargs = mock_gen.call_args[1]
        secs = call_kwargs.get("seconds_since_last")
        assert secs is not None
        # Allow some tolerance for test timing
        assert 25 <= secs <= 40

    def test_seconds_since_last_none_when_over_2h(self, tmp_path):
        m = _make_monitor(tmp_path)
        m._last_detection_time = time.time() - 8000

        clip_path = tmp_path / "hummer_test.mp4"
        clip_path.write_bytes(b"fake video data")

        with patch("main.generate_comment", return_value="test caption") as mock_gen, \
             patch("main.get_schedule_info", return_value={"sunrise": "6 AM", "sunset": "8 PM"}):
            self._run_worker_with_clip(m, clip_path)

        call_kwargs = mock_gen.call_args[1]
        assert call_kwargs.get("seconds_since_last") is None

    def test_seconds_since_last_none_when_under_5s(self, tmp_path):
        m = _make_monitor(tmp_path)
        m._last_detection_time = time.time() - 2

        clip_path = tmp_path / "hummer_test.mp4"
        clip_path.write_bytes(b"fake video data")

        with patch("main.generate_comment", return_value="test caption") as mock_gen, \
             patch("main.get_schedule_info", return_value={"sunrise": "6 AM", "sunset": "8 PM"}):
            self._run_worker_with_clip(m, clip_path)

        call_kwargs = mock_gen.call_args[1]
        assert call_kwargs.get("seconds_since_last") is None

    def test_test_mode_skips_posting(self, tmp_path):
        m = _make_monitor(tmp_path)
        m.test_mode = True
        m._last_detection_time = time.time() - 30

        clip_path = tmp_path / "hummer_test.mp4"
        clip_path.write_bytes(b"fake video data")

        with patch("main.generate_comment", return_value="test caption"), \
             patch("main.get_schedule_info", return_value={"sunrise": "6 AM", "sunset": "8 PM"}):
            self._run_worker_with_clip(m, clip_path)

        m.poster.post_video.assert_not_called()

    def test_saves_caption_file(self, tmp_path):
        m = _make_monitor(tmp_path)
        m._last_detection_time = time.time() - 30

        clip_path = tmp_path / "hummer_test.mp4"
        clip_path.write_bytes(b"fake video data")

        with patch("main.generate_comment", return_value="test caption here"), \
             patch("main.get_schedule_info", return_value={"sunrise": "6 AM", "sunset": "8 PM"}):
            self._run_worker_with_clip(m, clip_path)

        txt_path = clip_path.with_suffix(".txt")
        assert txt_path.exists()
        assert txt_path.read_text() == "test caption here"
