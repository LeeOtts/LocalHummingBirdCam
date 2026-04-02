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


class TestSeasonDates:
    """Test season date CRUD operations."""

    def test_upsert_and_retrieve(self, db):
        # DB seeds 6 rows; upsert a new year
        db.upsert_season_date(2099, "2099-03-15", "2099-10-20")
        dates = db.get_season_dates()
        newest = dates[0]
        assert newest["year"] == 2099
        assert newest["first_visit"] == "2099-03-15"

    def test_upsert_replaces(self, db):
        db.upsert_season_date(2099, "2099-03-15", "2099-10-20")
        db.upsert_season_date(2099, "2099-03-10", "2099-10-25")
        dates = db.get_season_dates()
        match = [d for d in dates if d["year"] == 2099]
        assert len(match) == 1
        assert match[0]["first_visit"] == "2099-03-10"

    def test_upsert_invalid_year_raises(self, db):
        with pytest.raises(ValueError):
            db.upsert_season_date(1999, "1999-03-15", None)

    def test_upsert_with_none_dates(self, db):
        db.upsert_season_date(2099, None, None)
        match = [d for d in db.get_season_dates() if d["year"] == 2099]
        assert match[0]["first_visit"] is None

    def test_delete_existing(self, db):
        db.upsert_season_date(2099, "2099-03-15", "2099-10-20")
        assert db.delete_season_date(2099) is True
        match = [d for d in db.get_season_dates() if d["year"] == 2099]
        assert match == []

    def test_delete_nonexistent(self, db):
        assert db.delete_season_date(2098) is False


class TestHeatmap:
    """Test position heatmap."""

    def test_record_and_retrieve(self, db):
        db.record_heatmap_point(0.5, 0.5)
        grid = db.get_heatmap(days=1)
        assert grid[4][4] == 1  # 0.5 * 8 = 4

    def test_multiple_points_accumulate(self, db):
        db.record_heatmap_point(0.1, 0.1)
        db.record_heatmap_point(0.1, 0.1)
        grid = db.get_heatmap(days=1)
        assert grid[0][0] == 2

    def test_empty_heatmap(self, db):
        grid = db.get_heatmap(days=1)
        assert len(grid) == 8
        assert all(all(c == 0 for c in row) for row in grid)

    def test_edge_values_clamped(self, db):
        db.record_heatmap_point(1.0, 1.0)  # Should clamp to grid_size-1
        grid = db.get_heatmap(days=1)
        assert grid[7][7] == 1


class TestFeederStats:
    """Test feeder management stats."""

    def test_empty_feeder_stats(self, db):
        stats = db.get_feeder_stats()
        assert stats["feeder_count"] == 0
        assert stats["days_since_refill"] is None
        assert stats["nectar_produced_oz"] == 0
        assert stats["estimated_consumption_oz"] == 0.0

    def test_with_feeders(self, db):
        db.add_feeder("Front Yard", port_count=4)
        db.add_feeder("Back Yard", port_count=6)
        stats = db.get_feeder_stats()
        assert stats["feeder_count"] == 2


class TestWateringEvents:
    """Test watering event recording."""

    def test_record_start_and_retrieve(self, db):
        eid = db.record_watering_start(zone="1")
        assert eid is not None
        events = db.get_watering_events(days=1)
        assert len(events) == 1
        assert events[0]["zone"] == "1"

    def test_record_end(self, db):
        eid = db.record_watering_start(zone="1")
        db.record_watering_end(eid)
        events = db.get_watering_events(days=1)
        assert events[0]["end_time"] is not None

    def test_record_end_nonexistent(self, db):
        db.record_watering_end(999)  # should not raise

    def test_empty_events(self, db):
        assert db.get_watering_events(days=1) == []

    def test_clear_watering_events(self, db):
        db.record_watering_start(zone="1")
        db.record_watering_start(zone="2")
        assert len(db.get_watering_events(days=1)) == 2
        deleted = db.clear_watering_events()
        assert deleted == 2
        assert db.get_watering_events(days=1) == []

    def test_clear_empty(self, db):
        assert db.clear_watering_events() == 0


class TestEngagementDB:
    """Test social engagement DB operations."""

    def test_record_and_summary(self, db):
        db.record_engagement("facebook", "post1", likes=10, shares=2, comments=3)
        summary = db.get_engagement_summary(days=1)
        assert summary["total_likes"] == 10
        assert summary["total_shares"] == 2
        assert summary["total_comments"] == 3
        assert summary["post_count"] == 1

    def test_upsert_engagement(self, db):
        db.record_engagement("facebook", "post1", likes=5)
        db.record_engagement("facebook", "post1", likes=15)
        summary = db.get_engagement_summary(days=1)
        assert summary["total_likes"] == 15

    def test_empty_summary(self, db):
        summary = db.get_engagement_summary(days=1)
        assert summary["total_likes"] == 0
        assert summary["top_post"] is None

    def test_top_post(self, db):
        db.record_engagement("facebook", "post1", likes=5, shares=1, comments=1)
        db.record_engagement("facebook", "post2", likes=20, shares=5, comments=10)
        summary = db.get_engagement_summary(days=1)
        assert summary["top_post"]["post_id"] == "post2"


class TestFollowerSnapshots:
    """Test follower tracking."""

    def test_record_and_retrieve(self, db):
        db.record_follower_snapshot("facebook", 500)
        history = db.get_follower_history(days=1)
        assert len(history) == 1
        assert history[0]["follower_count"] == 500

    def test_empty_history(self, db):
        assert db.get_follower_history(days=1) == []


class TestPredictions:
    """Test prediction logging and accuracy."""

    def test_log_and_get_accuracy(self, db):
        db.log_prediction("next_visit", "10:00", "10:15", error_minutes=15.0)
        db.log_prediction("next_visit", "14:00", "14:30", error_minutes=30.0)
        acc = db.get_prediction_accuracy(days=1)
        assert "next_visit" in acc
        assert acc["next_visit"]["avg_error_minutes"] == 22.5
        assert acc["next_visit"]["predictions_logged"] == 2

    def test_empty_accuracy(self, db):
        assert db.get_prediction_accuracy(days=1) == {}

    def test_null_error_excluded(self, db):
        db.log_prediction("next_visit", "10:00", None, error_minutes=None)
        assert db.get_prediction_accuracy(days=1) == {}


class TestAnalyticsQueries:
    """Test behavior/species breakdowns and visit stats."""

    def test_behavior_breakdown_empty(self, db):
        assert db.get_behavior_breakdown(days=1) == {}

    def _update_sighting(self, db, sid, **kwargs):
        """Helper to update sighting fields without locking issues."""
        conn = db._get_conn()
        for key, val in kwargs.items():
            conn.execute(f"UPDATE sightings SET {key} = ? WHERE id = ?", (val, sid))
        conn.commit()
        conn.close()

    def test_behavior_breakdown_with_data(self, db):
        sid = db.record_sighting("clip1.mp4", "feeding bird")
        self._update_sighting(db, sid, behavior="feeding")
        sid2 = db.record_sighting("clip2.mp4", "hovering bird")
        self._update_sighting(db, sid2, behavior="hovering")
        sid3 = db.record_sighting("clip3.mp4", "another feeder")
        self._update_sighting(db, sid3, behavior="feeding")
        result = db.get_behavior_breakdown(days=1)
        assert result["feeding"] == 2
        assert result["hovering"] == 1

    def test_species_breakdown_empty(self, db):
        assert db.get_species_breakdown(days=1) == {}

    def test_species_breakdown_with_data(self, db):
        sid = db.record_sighting("clip1.mp4", "ruby-throated")
        self._update_sighting(db, sid, species="Ruby-throated")
        result = db.get_species_breakdown(days=1)
        assert result["Ruby-throated"] == 1

    def test_visit_stats_empty(self, db):
        stats = db.get_visit_stats(days=1)
        assert stats["avg_birds_per_visit"] is None
        assert stats["max_simultaneous"] == 0
        assert stats["avg_visit_duration_sec"] is None

    def test_visit_stats_with_data(self, db):
        sid = db.record_sighting("clip1.mp4", "visit")
        self._update_sighting(db, sid, bird_count=3, visit_duration_frames=150)
        stats = db.get_visit_stats(days=1)
        assert stats["avg_birds_per_visit"] == 3.0
        assert stats["max_simultaneous"] == 3
        assert stats["avg_visit_duration_sec"] == 10.0  # 150/15

    def test_sunrise_offset_avg_empty(self, db):
        assert db.get_sunrise_offset_avg(days=1) is None

    def test_sunrise_offset_avg_with_data(self, db):
        sid1 = db.record_sighting("clip1.mp4", "early")
        self._update_sighting(db, sid1, sunrise_offset_min=30)
        sid2 = db.record_sighting("clip2.mp4", "later")
        self._update_sighting(db, sid2, sunrise_offset_min=60)
        result = db.get_sunrise_offset_avg(days=1)
        assert result == 45.0


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
