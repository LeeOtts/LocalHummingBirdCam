"""Tests for social/engagement.py — EngagementTracker."""

from unittest.mock import MagicMock, patch

import pytest


def _make_tracker(db=None, interval_hours=1.0):
    from social.engagement import EngagementTracker
    return EngagementTracker(db=db or MagicMock(), interval_hours=interval_hours)


class TestInit:
    def test_default_interval(self):
        t = _make_tracker()
        assert t._interval == 3600

    def test_custom_interval(self):
        t = _make_tracker(interval_hours=0.5)
        assert t._interval == 1800

    def test_not_running_initially(self):
        t = _make_tracker()
        assert t._running is False


class TestStartStop:
    def test_stop_clears_running(self):
        t = _make_tracker()
        t._running = True
        t.stop()
        assert t._running is False


class TestFetchFacebookEngagement:
    def test_returns_early_no_token(self):
        t = _make_tracker()
        with patch("config.FACEBOOK_PAGE_ACCESS_TOKEN", ""), \
             patch("config.FACEBOOK_PAGE_ID", "123"):
            t._fetch_facebook_engagement()
            # Should return early without calling db
            t._db.get_sightings.assert_not_called()

    def test_returns_early_no_page_id(self):
        t = _make_tracker()
        with patch("config.FACEBOOK_PAGE_ACCESS_TOKEN", "token"), \
             patch("config.FACEBOOK_PAGE_ID", ""):
            t._fetch_facebook_engagement()
            t._db.get_sightings.assert_not_called()

    def test_fetches_engagement_for_posts(self):
        mock_db = MagicMock()
        mock_db.get_sightings.return_value = [
            {"facebook_post_id": "fb123", "caption": "A hummingbird!"},
            {"facebook_post_id": None, "caption": "No post"},
        ]
        t = _make_tracker(db=mock_db)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "likes": {"summary": {"total_count": 10}},
            "shares": {"count": 2},
            "comments": {"summary": {"total_count": 3}},
        }

        with patch("config.FACEBOOK_PAGE_ACCESS_TOKEN", "token"), \
             patch("config.FACEBOOK_PAGE_ID", "page1"), \
             patch("requests.get", return_value=mock_resp):
            t._fetch_facebook_engagement()
            mock_db.record_engagement.assert_called_once()
            call_kwargs = mock_db.record_engagement.call_args
            assert call_kwargs[1]["likes"] == 10
            assert call_kwargs[1]["shares"] == 2

    def test_skips_failed_api_call(self):
        mock_db = MagicMock()
        mock_db.get_sightings.return_value = [
            {"facebook_post_id": "fb123", "caption": "Test"},
        ]
        t = _make_tracker(db=mock_db)

        mock_resp = MagicMock()
        mock_resp.status_code = 500

        with patch("config.FACEBOOK_PAGE_ACCESS_TOKEN", "token"), \
             patch("config.FACEBOOK_PAGE_ID", "page1"), \
             patch("requests.get", return_value=mock_resp):
            t._fetch_facebook_engagement()
            mock_db.record_engagement.assert_not_called()


class TestSnapshotFollowers:
    def test_returns_early_no_token(self):
        t = _make_tracker()
        with patch("config.FACEBOOK_PAGE_ACCESS_TOKEN", ""), \
             patch("config.FACEBOOK_PAGE_ID", "123"):
            t._snapshot_followers()

    def test_records_follower_count(self):
        mock_db = MagicMock()
        t = _make_tracker(db=mock_db)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"followers_count": 500}

        with patch("config.FACEBOOK_PAGE_ACCESS_TOKEN", "token"), \
             patch("config.FACEBOOK_PAGE_ID", "page1"), \
             patch("requests.get", return_value=mock_resp):
            t._snapshot_followers()
            mock_db.record_follower_snapshot.assert_called_once_with("facebook", 500)

    def test_skips_zero_followers(self):
        mock_db = MagicMock()
        t = _make_tracker(db=mock_db)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"followers_count": 0}

        with patch("config.FACEBOOK_PAGE_ACCESS_TOKEN", "token"), \
             patch("config.FACEBOOK_PAGE_ID", "page1"), \
             patch("requests.get", return_value=mock_resp):
            t._snapshot_followers()
            mock_db.record_follower_snapshot.assert_not_called()

    def test_handles_api_error(self):
        mock_db = MagicMock()
        t = _make_tracker(db=mock_db)

        with patch("config.FACEBOOK_PAGE_ACCESS_TOKEN", "token"), \
             patch("config.FACEBOOK_PAGE_ID", "page1"), \
             patch("requests.get", side_effect=Exception("timeout")):
            t._snapshot_followers()  # should not raise
            mock_db.record_follower_snapshot.assert_not_called()
