"""Tests for social/facebook_poster.py — rate limiting and retry queue.

Uses tmp_path fixture for file operations to avoid touching real files.
"""

import json
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from social.facebook_poster import FacebookPoster


@pytest.fixture
def poster(monkeypatch):
    """Create a FacebookPoster with test credentials."""
    import config
    monkeypatch.setattr(config, "FACEBOOK_PAGE_ID", "test-page-id")
    monkeypatch.setattr(config, "FACEBOOK_PAGE_ACCESS_TOKEN", "test-token")
    monkeypatch.setattr(config, "MAX_POSTS_PER_DAY", 5)
    return FacebookPoster()


class TestCheckRateLimit:
    """Test _check_rate_limit() daily post limiting."""

    def test_allows_posts_under_limit(self, poster):
        """Posts should be allowed when under the daily limit."""
        poster._posts_today = 0
        assert poster._check_rate_limit() is True

    def test_allows_up_to_limit_minus_one(self, poster):
        """Posts at limit-1 should still be allowed."""
        poster._posts_today = 4  # limit is 5
        assert poster._check_rate_limit() is True

    def test_blocks_posts_at_limit(self, poster):
        """Posts should be blocked when at the daily limit."""
        poster._posts_today = 5  # limit is 5
        assert poster._check_rate_limit() is False

    def test_blocks_posts_over_limit(self, poster):
        """Posts should be blocked when over the daily limit."""
        poster._posts_today = 10
        assert poster._check_rate_limit() is False


class TestDailyCounterReset:
    """Test that the daily counter resets on a new day."""

    def test_counter_resets_on_new_day(self, poster):
        """When the date changes, posts_today should reset to 0."""
        from datetime import timedelta

        # Use the same timezone-aware 'today' the rate limiter uses
        today_tz = poster._date_in_local_tz()

        poster._posts_today = 5
        poster._today = today_tz - timedelta(days=1)  # one day before tz-aware today

        result = poster._check_rate_limit()
        assert result is True
        assert poster._posts_today == 0
        assert poster._today == today_tz


class TestSaveToRetryQueue:
    """Test _save_to_retry_queue() file operations."""

    def test_saves_to_json_file(self, poster, tmp_path, monkeypatch):
        """Failed posts are saved to the retry queue JSON file."""
        import config
        retry_file = tmp_path / "retry_queue.json"
        monkeypatch.setattr(config, "RETRY_QUEUE_FILE", retry_file)

        clip_path = tmp_path / "clip1.mp4"
        poster._save_to_retry_queue(clip_path, "Caption 1")

        assert retry_file.exists()
        data = json.loads(retry_file.read_text())
        assert len(data) == 1
        assert data[0]["mp4_path"] == str(clip_path)
        assert data[0]["caption"] == "Caption 1"

    def test_appends_to_existing_queue(self, poster, tmp_path, monkeypatch):
        """New entries are appended to an existing retry queue."""
        import config
        retry_file = tmp_path / "retry_queue.json"
        monkeypatch.setattr(config, "RETRY_QUEUE_FILE", retry_file)

        old_clip = tmp_path / "old.mp4"
        old_clip.write_bytes(b"fake")  # must exist on disk — pruning removes missing entries
        old_path = str(old_clip)
        retry_file.write_text(json.dumps([{"mp4_path": old_path, "caption": "Old"}]))

        new_clip = tmp_path / "new.mp4"
        poster._save_to_retry_queue(new_clip, "New caption")

        data = json.loads(retry_file.read_text())
        assert len(data) == 2
        assert data[0]["mp4_path"] == old_path
        assert data[1]["mp4_path"] == str(new_clip)

    def test_handles_corrupted_queue_file(self, poster, tmp_path, monkeypatch):
        """If the existing queue file is corrupted JSON, start fresh."""
        import config
        retry_file = tmp_path / "retry_queue.json"
        monkeypatch.setattr(config, "RETRY_QUEUE_FILE", retry_file)

        retry_file.write_text("not valid json {{{")

        clip_path = tmp_path / "clip.mp4"
        poster._save_to_retry_queue(clip_path, "Caption")

        data = json.loads(retry_file.read_text())
        assert len(data) == 1
        assert data[0]["mp4_path"] == str(clip_path)


class TestRetryFailedPosts:
    """Test retry_failed_posts() processes the queue."""

    def test_processes_queue_items(self, poster, tmp_path, monkeypatch):
        """retry_failed_posts() attempts to repost items from the queue."""
        import config
        retry_file = tmp_path / "retry_queue.json"
        monkeypatch.setattr(config, "RETRY_QUEUE_FILE", retry_file)

        # Create a fake clip file
        clip = tmp_path / "clip1.mp4"
        clip.write_bytes(b"fake video data")

        retry_file.write_text(json.dumps([{
            "mp4_path": str(clip),
            "caption": "Retry caption",
        }]))

        # Mock post_video to succeed
        with patch.object(poster, "post_video", return_value=True) as mock_post:
            poster.retry_failed_posts()

        mock_post.assert_called_once()
        # Queue should be empty after successful retry
        data = json.loads(retry_file.read_text())
        assert len(data) == 0

    def test_skips_missing_clips(self, poster, tmp_path, monkeypatch):
        """Clips that no longer exist on disk are skipped."""
        import config
        retry_file = tmp_path / "retry_queue.json"
        monkeypatch.setattr(config, "RETRY_QUEUE_FILE", retry_file)

        retry_file.write_text(json.dumps([{
            "mp4_path": str(tmp_path / "nonexistent.mp4"),
            "caption": "Gone",
        }]))

        poster.retry_failed_posts()

        data = json.loads(retry_file.read_text())
        assert len(data) == 0  # skipped, not re-queued

    def test_no_queue_file_is_noop(self, poster, tmp_path, monkeypatch):
        """When no retry queue file exists, retry is a no-op."""
        import config
        retry_file = tmp_path / "no_such_file.json"
        monkeypatch.setattr(config, "RETRY_QUEUE_FILE", retry_file)

        # Should not raise
        poster.retry_failed_posts()


class TestPostPhoto:
    """Test post_photo() image uploads."""

    def test_post_photo_success(self, poster, tmp_path):
        """post_photo() should upload image and return True on success."""
        image = tmp_path / "test.jpg"
        image.write_bytes(b"fake image data")

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"id": "photo-123", "post_id": "page_photo-123"}
        mock_resp.raise_for_status = MagicMock()

        with patch("social.facebook_poster.requests.post", return_value=mock_resp) as mock_post:
            result = poster.post_photo(image, "Test caption")

        assert result is True
        mock_post.assert_called_once()
        call_url = mock_post.call_args[0][0]
        assert "/photos" in call_url

    def test_post_photo_no_credentials(self, monkeypatch, tmp_path):
        """post_photo() returns False when no credentials."""
        import config
        monkeypatch.setattr(config, "FACEBOOK_PAGE_ID", "")
        monkeypatch.setattr(config, "FACEBOOK_PAGE_ACCESS_TOKEN", "")

        p = FacebookPoster()
        image = tmp_path / "test.jpg"
        image.write_bytes(b"data")
        assert p.post_photo(image, "test") is False

    def test_post_photo_rate_limited(self, poster, tmp_path):
        """post_photo() returns False when at daily post limit."""
        poster._posts_today = 5  # limit is 5
        image = tmp_path / "test.jpg"
        image.write_bytes(b"data")
        assert poster.post_photo(image, "test") is False

    def test_post_photo_api_error(self, poster, tmp_path):
        """post_photo() returns False and logs on API error."""
        import requests as req

        image = tmp_path / "test.jpg"
        image.write_bytes(b"data")

        with patch("social.facebook_poster.requests.post", side_effect=req.RequestException("fail")):
            result = poster.post_photo(image, "test")

        assert result is False


class TestMissingCredentials:
    """Test behavior when Facebook credentials are missing."""

    def test_post_video_no_credentials(self, monkeypatch, tmp_path):
        """post_video() returns False and saves to retry queue when no credentials."""
        import config
        monkeypatch.setattr(config, "FACEBOOK_PAGE_ID", "")
        monkeypatch.setattr(config, "FACEBOOK_PAGE_ACCESS_TOKEN", "")
        monkeypatch.setattr(config, "RETRY_QUEUE_FILE", tmp_path / "retry.json")

        poster = FacebookPoster()
        clip = tmp_path / "clip.mp4"
        clip.write_bytes(b"data")

        result = poster.post_video(clip, "test caption")
        assert result is False

    def test_post_text_no_credentials(self, monkeypatch):
        """post_text() returns False when no credentials."""
        import config
        monkeypatch.setattr(config, "FACEBOOK_PAGE_ID", "")
        monkeypatch.setattr(config, "FACEBOOK_PAGE_ACCESS_TOKEN", "")

        poster = FacebookPoster()
        result = poster.post_text("Hello world")
        assert result is False


class TestVerifyToken:
    """Test verify_token() token diagnostics."""

    def test_no_credentials_returns_not_configured(self, monkeypatch):
        """verify_token() returns not configured when credentials are missing."""
        import config
        monkeypatch.setattr(config, "FACEBOOK_PAGE_ID", "")
        monkeypatch.setattr(config, "FACEBOOK_PAGE_ACCESS_TOKEN", "")
        poster = FacebookPoster()
        result = poster.verify_token()
        assert result["configured"] is False
        assert result["valid"] is False
        assert len(result["warnings"]) > 0

    def test_valid_token_with_all_scopes(self, poster):
        """verify_token() parses valid token response with required scopes."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "data": {
                "is_valid": True,
                "scopes": ["pages_manage_posts", "pages_read_engagement"],
                "app_id": "123456",
                "expires_at": 9999999999,
            }
        }
        mock_resp.raise_for_status = MagicMock()

        with patch("social.facebook_poster.requests.get", return_value=mock_resp):
            result = poster.verify_token()

        assert result["configured"] is True
        assert result["valid"] is True
        assert "pages_manage_posts" in result["scopes"]
        assert len(result["warnings"]) == 0

    def test_missing_scopes_adds_warning(self, poster):
        """verify_token() adds warning when required scopes are missing."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "data": {
                "is_valid": True,
                "scopes": [],
                "app_id": None,
                "expires_at": 0,
            }
        }
        mock_resp.raise_for_status = MagicMock()

        with patch("social.facebook_poster.requests.get", return_value=mock_resp):
            result = poster.verify_token()

        assert any("Missing Facebook scopes" in w for w in result["warnings"])

    def test_invalid_token_adds_warning(self, poster):
        """verify_token() adds warning when token is invalid."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "data": {
                "is_valid": False,
                "scopes": ["pages_manage_posts", "pages_read_engagement"],
                "app_id": None,
                "expires_at": 0,
            }
        }
        mock_resp.raise_for_status = MagicMock()

        with patch("social.facebook_poster.requests.get", return_value=mock_resp):
            result = poster.verify_token()

        assert result["valid"] is False
        assert any("INVALID" in w for w in result["warnings"])

    def test_request_exception_adds_warning(self, poster):
        """verify_token() handles network errors gracefully."""
        import requests as req
        with patch("social.facebook_poster.requests.get", side_effect=req.RequestException("timeout")):
            result = poster.verify_token()

        assert result["valid"] is False
        assert any("Failed to verify" in w for w in result["warnings"])

    def test_fetches_app_name_when_app_id_present(self, poster):
        """verify_token() fetches app name when app_id is present."""
        token_resp = MagicMock()
        token_resp.json.return_value = {
            "data": {
                "is_valid": True,
                "scopes": ["pages_manage_posts", "pages_read_engagement"],
                "app_id": "42",
                "expires_at": 9999999999,
            }
        }
        token_resp.raise_for_status = MagicMock()

        app_resp = MagicMock()
        app_resp.status_code = 200
        app_resp.json.return_value = {"name": "TestApp"}

        with patch("social.facebook_poster.requests.get", side_effect=[token_resp, app_resp]):
            result = poster.verify_token()

        assert result["app_name"] == "TestApp"


class TestVerifyPostExists:
    """Test verify_post_exists() readback check."""

    def test_found_post_returns_found_true(self, poster):
        """verify_post_exists() returns found=True when post is found."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "id": "post-123",
            "is_published": True,
            "created_time": "2026-01-01T00:00:00+0000",
        }

        with patch("social.facebook_poster.requests.get", return_value=mock_resp):
            result = poster.verify_post_exists("post-123")

        assert result["found"] is True
        assert result["is_published"] is True

    def test_not_found_returns_found_false(self, poster):
        """verify_post_exists() returns found=False on non-200 response."""
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_resp.text = "not found"

        with patch("social.facebook_poster.requests.get", return_value=mock_resp):
            result = poster.verify_post_exists("bad-id")

        assert result["found"] is False

    def test_request_exception_returns_found_false(self, poster):
        """verify_post_exists() handles network errors gracefully."""
        import requests as req
        with patch("social.facebook_poster.requests.get", side_effect=req.RequestException("fail")):
            result = poster.verify_post_exists("post-123")

        assert result["found"] is False
        assert "error" in result


class TestPostVideo:
    """Test post_video() upload flow."""

    def test_post_video_success(self, poster, tmp_path):
        """post_video() returns True on successful upload."""
        clip = tmp_path / "clip.mp4"
        clip.write_bytes(b"fake video data")

        init_resp = MagicMock()
        init_resp.json.return_value = {"upload_session_id": "session-abc"}
        init_resp.raise_for_status = MagicMock()

        transfer_resp = MagicMock()
        transfer_resp.raise_for_status = MagicMock()

        finish_resp = MagicMock()
        finish_resp.json.return_value = {"id": "video-123"}
        finish_resp.raise_for_status = MagicMock()

        with patch("social.facebook_poster.requests.post",
                   side_effect=[init_resp, transfer_resp, finish_resp]):
            result = poster.post_video(clip, "A hummingbird!")

        assert result is True
        assert poster._posts_today == 1

    def test_post_video_api_error_saves_to_retry(self, poster, tmp_path, monkeypatch):
        """post_video() saves to retry queue and returns False on API error."""
        import config
        import requests as req
        monkeypatch.setattr(config, "RETRY_QUEUE_FILE", tmp_path / "retry.json")

        clip = tmp_path / "clip.mp4"
        clip.write_bytes(b"data")

        with patch("social.facebook_poster.requests.post", side_effect=req.RequestException("fail")):
            result = poster.post_video(clip, "caption")

        assert result is False
        assert (tmp_path / "retry.json").exists()

    def test_post_video_rate_limited_saves_to_retry(self, poster, tmp_path, monkeypatch):
        """post_video() saves to retry queue when daily limit is reached."""
        import config
        monkeypatch.setattr(config, "RETRY_QUEUE_FILE", tmp_path / "retry.json")

        poster._posts_today = 5  # limit is 5
        clip = tmp_path / "clip.mp4"
        clip.write_bytes(b"data")

        result = poster.post_video(clip, "caption")
        assert result is False


class TestPostText:
    """Test post_text() status update."""

    def test_post_text_success(self, poster):
        """post_text() returns True on successful post."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"id": "post-999"}
        mock_resp.raise_for_status = MagicMock()

        with patch("social.facebook_poster.requests.post", return_value=mock_resp), \
             patch.object(poster, "verify_post_exists", return_value={"found": True}):
            result = poster.post_text("Hello world!")

        assert result is True

    def test_post_text_api_error(self, poster):
        """post_text() returns False on API error."""
        import requests as req
        with patch("social.facebook_poster.requests.post", side_effect=req.RequestException("fail")):
            result = poster.post_text("Hello!")

        assert result is False


class TestRetryQueueEdgeCases:
    """Test edge cases for retry queue management."""

    def test_retry_queue_caps_at_max_size(self, poster, tmp_path, monkeypatch):
        """Retry queue drops oldest entries when it exceeds MAX_RETRY_QUEUE_SIZE."""
        import config
        retry_file = tmp_path / "retry.json"
        monkeypatch.setattr(config, "RETRY_QUEUE_FILE", retry_file)
        monkeypatch.setattr(config, "MAX_RETRY_QUEUE_SIZE", 3)

        # Pre-fill queue with clips that exist on disk
        existing_clips = []
        for i in range(3):
            c = tmp_path / f"old_{i}.mp4"
            c.write_bytes(b"data")
            existing_clips.append(str(c))

        retry_file.write_text(json.dumps([{"mp4_path": p, "caption": "old"} for p in existing_clips]))

        # Add one more (should drop oldest)
        new_clip = tmp_path / "new.mp4"
        poster._save_to_retry_queue(new_clip, "new caption")

        data = json.loads(retry_file.read_text())
        assert len(data) == 3
        # Newest entry should be last
        assert data[-1]["mp4_path"] == str(new_clip)

    def test_retry_failed_posts_rate_limit_keeps_in_queue(self, poster, tmp_path, monkeypatch):
        """retry_failed_posts() keeps items in queue when rate-limited."""
        import config
        retry_file = tmp_path / "retry.json"
        monkeypatch.setattr(config, "RETRY_QUEUE_FILE", retry_file)

        clip = tmp_path / "clip.mp4"
        clip.write_bytes(b"data")
        retry_file.write_text(json.dumps([{"mp4_path": str(clip), "caption": "test"}]))

        poster._posts_today = 5  # at limit

        poster.retry_failed_posts()

        data = json.loads(retry_file.read_text())
        assert len(data) == 1  # still in queue

    def test_retry_failed_posts_failed_post_stays_in_queue(self, poster, tmp_path, monkeypatch):
        """retry_failed_posts() keeps items in queue when post_video fails."""
        import config
        retry_file = tmp_path / "retry.json"
        monkeypatch.setattr(config, "RETRY_QUEUE_FILE", retry_file)

        clip = tmp_path / "clip.mp4"
        clip.write_bytes(b"data")
        retry_file.write_text(json.dumps([{"mp4_path": str(clip), "caption": "test"}]))

        with patch.object(poster, "post_video", return_value=False):
            poster.retry_failed_posts()

        data = json.loads(retry_file.read_text())
        assert len(data) == 1  # still in queue

    def test_retry_failed_posts_corrupted_json(self, poster, tmp_path, monkeypatch):
        """retry_failed_posts() is a no-op when the queue file is corrupted JSON."""
        import config
        retry_file = tmp_path / "retry.json"
        monkeypatch.setattr(config, "RETRY_QUEUE_FILE", retry_file)

        retry_file.write_text("not valid json {{{")

        # Should not raise
        poster.retry_failed_posts()

    def test_retry_failed_posts_empty_queue(self, poster, tmp_path, monkeypatch):
        """retry_failed_posts() is a no-op when the queue is empty."""
        import config
        retry_file = tmp_path / "retry.json"
        monkeypatch.setattr(config, "RETRY_QUEUE_FILE", retry_file)

        retry_file.write_text("[]")

        # Should not raise or call post_video
        with patch.object(poster, "post_video") as mock_post:
            poster.retry_failed_posts()

        mock_post.assert_not_called()
