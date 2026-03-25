"""Tests for data/sightings.py — SQLite database for hummingbird sightings."""

from datetime import date, timedelta
from unittest.mock import patch

import pytest

from data.sightings import SightingsDB


@pytest.fixture
def db(tmp_path):
    """Create a fresh SightingsDB in a temporary directory."""
    return SightingsDB(db_path=tmp_path / "test_sightings.db")


class TestInitAndConnection:
    """Test database initialization."""

    def test_creates_db_file(self, tmp_path):
        db_path = tmp_path / "subdir" / "sightings.db"
        SightingsDB(db_path=db_path)
        assert db_path.exists()

    def test_creates_tables(self, db):
        conn = db._get_conn()
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        table_names = {r["name"] for r in tables}
        conn.close()
        assert "sightings" in table_names
        assert "daily_stats" in table_names
        assert "page_views" in table_names
        assert "guestbook" in table_names
        assert "comment_replies" in table_names


class TestRecordSighting:
    """Test record_sighting() and related queries."""

    def test_record_and_count(self, db):
        sid = db.record_sighting("clip001.mp4", "First visitor!")
        assert sid == 1
        assert db.get_total_sightings() == 1

    def test_multiple_sightings(self, db):
        db.record_sighting("clip001.mp4", "First")
        db.record_sighting("clip002.mp4", "Second")
        db.record_sighting("clip003.mp4", "Third")
        assert db.get_total_sightings() == 3

    def test_record_with_all_fields(self, db):
        sid = db.record_sighting(
            "clip001.mp4", "Hovering beautifully",
            frame_path="/tmp/frame.jpg",
            confidence=0.95,
            weather_temp=78.5,
            weather_condition="Clear",
        )
        sightings = db.get_sightings(days=1)
        assert len(sightings) == 1
        assert sightings[0]["clip_filename"] == "clip001.mp4"
        assert sightings[0]["confidence"] == 0.95

    def test_get_today_count(self, db):
        db.record_sighting("clip001.mp4", "Visit 1")
        db.record_sighting("clip002.mp4", "Visit 2")
        assert db.get_today_count() == 2


class TestGetSightings:
    """Test get_sightings() retrieval."""

    def test_empty_db_returns_empty(self, db):
        assert db.get_sightings() == []

    def test_returns_recent_sightings(self, db):
        db.record_sighting("clip001.mp4", "First")
        db.record_sighting("clip002.mp4", "Second")
        results = db.get_sightings(days=1)
        assert len(results) == 2

    def test_respects_limit(self, db):
        for i in range(10):
            db.record_sighting(f"clip{i:03d}.mp4", f"Caption {i}")
        results = db.get_sightings(days=1, limit=3)
        assert len(results) == 3


class TestDailyStats:
    """Test save_daily_stats() and get_daily_totals()."""

    def test_save_and_retrieve(self, db):
        today = date.today()
        db.save_daily_stats(today, detections=12, rejected=3, peak_hour="2 PM", is_record=True)
        totals = db.get_daily_totals(days=1)
        assert len(totals) == 1
        assert totals[0]["total_detections"] == 12
        assert totals[0]["rejected"] == 3
        assert totals[0]["is_record"] == 1

    def test_upsert_replaces(self, db):
        today = date.today()
        db.save_daily_stats(today, detections=5, rejected=1)
        db.save_daily_stats(today, detections=10, rejected=2)
        totals = db.get_daily_totals(days=1)
        assert len(totals) == 1
        assert totals[0]["total_detections"] == 10

    def test_get_daily_totals_filters_old(self, db):
        old = date.today() - timedelta(days=60)
        recent = date.today()
        db.save_daily_stats(old, detections=5, rejected=1)
        db.save_daily_stats(recent, detections=10, rejected=2)
        totals = db.get_daily_totals(days=30)
        assert len(totals) == 1
        assert totals[0]["total_detections"] == 10


class TestHourlyDistribution:
    """Test get_hourly_distribution()."""

    def test_empty_db(self, db):
        assert db.get_hourly_distribution() == {}

    def test_groups_by_hour(self, db):
        # Record a few sightings (they'll all have the current hour)
        db.record_sighting("clip001.mp4", "Visit 1")
        db.record_sighting("clip002.mp4", "Visit 2")
        result = db.get_hourly_distribution(days=1)
        assert sum(result.values()) == 2


class TestAverageGap:
    """Test get_average_gap_minutes()."""

    def test_returns_none_with_fewer_than_2(self, db):
        db.record_sighting("clip001.mp4", "Only one")
        assert db.get_average_gap_minutes() is None

    def test_returns_none_with_no_sightings(self, db):
        assert db.get_average_gap_minutes() is None


class TestUpdatePostedTo:
    """Test update_posted_to() platform tracking."""

    def test_updates_platform_list(self, db):
        db.record_sighting("clip001.mp4", "Test")
        db.update_posted_to("clip001.mp4", "Facebook", "fb_post_123")
        sightings = db.get_sightings(days=1)
        import json
        platforms = json.loads(sightings[0]["posted_to"])
        assert "Facebook" in platforms

    def test_no_duplicate_platforms(self, db):
        db.record_sighting("clip001.mp4", "Test")
        db.update_posted_to("clip001.mp4", "Facebook")
        db.update_posted_to("clip001.mp4", "Facebook")
        sightings = db.get_sightings(days=1)
        import json
        platforms = json.loads(sightings[0]["posted_to"])
        assert platforms.count("Facebook") == 1

    def test_handles_missing_clip(self, db):
        # Should not raise
        db.update_posted_to("nonexistent.mp4", "Facebook")


class TestPageViews:
    """Test page view tracking."""

    def test_record_and_count(self, db):
        db.record_page_view("abc123", "/gallery")
        db.record_page_view("def456", "/gallery")
        db.record_page_view("abc123", "/analytics")  # same IP, different page
        assert db.get_total_page_views() == 2  # 2 unique IPs


class TestGuestbook:
    """Test guestbook entries."""

    def test_add_and_retrieve(self, db):
        entry_id = db.add_guestbook_entry("Alice", "Love the hummers!", "ip1")
        assert entry_id is not None
        entries = db.get_guestbook_entries()
        assert len(entries) == 1
        assert entries[0]["name"] == "Alice"

    def test_rate_limiting(self, db):
        for i in range(3):
            db.add_guestbook_entry(f"User{i}", f"Message {i}", "same_ip")
        # 4th should be rate-limited
        result = db.add_guestbook_entry("User3", "Spam", "same_ip")
        assert result is None

    def test_different_ips_not_limited(self, db):
        for i in range(5):
            result = db.add_guestbook_entry(f"User{i}", f"Message {i}", f"ip_{i}")
            assert result is not None

    def test_name_and_message_truncated(self, db):
        long_name = "A" * 100
        long_msg = "B" * 1000
        db.add_guestbook_entry(long_name, long_msg, "ip1")
        entries = db.get_guestbook_entries()
        assert len(entries[0]["name"]) == 50
        assert len(entries[0]["message"]) == 500


class TestCommentReplies:
    """Test comment reply tracking."""

    def test_has_replied_to(self, db):
        assert db.has_replied_to("comment_1") is False
        db.record_reply("comment_1", "Nice bird!", "Thanks!", "post_1")
        assert db.has_replied_to("comment_1") is True

    def test_get_replies_last_hour(self, db):
        db.record_reply("c1", "text1", "reply1", "p1")
        db.record_reply("c2", "text2", "reply2", "p1")
        assert db.get_replies_last_hour() == 2

    def test_duplicate_comment_ignored(self, db):
        db.record_reply("c1", "text", "reply", "p1")
        db.record_reply("c1", "text", "reply", "p1")  # INSERT OR IGNORE
        assert db.get_replies_last_hour() == 1


class TestWeeklySummary:
    """Test get_weekly_summary() for digest generation."""

    def test_empty_db(self, db):
        result = db.get_weekly_summary()
        assert result["total"] == 0
        assert result["last_week_total"] == 0
        assert result["trend"] == "flat"

    def test_with_sightings(self, db):
        for i in range(5):
            db.record_sighting(f"clip{i}.mp4", f"Caption {i}")
        result = db.get_weekly_summary()
        assert result["total"] == 5
        assert result["trend"] == "up"  # 5 > 0

    def test_busiest_day_included(self, db):
        today = date.today()
        db.save_daily_stats(today, detections=15, rejected=2, peak_hour="10 AM")
        result = db.get_weekly_summary()
        if result["busiest_day"]:
            assert result["busiest_day"]["total_detections"] == 15

    def test_best_caption(self, db):
        db.record_sighting("clip1.mp4", "Short")
        db.record_sighting("clip2.mp4", "This is a much longer and more descriptive caption")
        result = db.get_weekly_summary()
        assert result["best_caption"] == "This is a much longer and more descriptive caption"
