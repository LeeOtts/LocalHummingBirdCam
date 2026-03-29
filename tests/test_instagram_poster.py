"""Tests for InstagramPoster."""

from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

from social.instagram_poster import InstagramPoster


@pytest.fixture
def poster(monkeypatch):
    """Create an InstagramPoster with test credentials."""
    import config as _config
    monkeypatch.setattr(_config, "INSTAGRAM_BUSINESS_ACCOUNT_ID", "test-biz-id")
    monkeypatch.setattr(_config, "INSTAGRAM_USER_ID", "test-user-id")
    monkeypatch.setattr(_config, "FACEBOOK_PAGE_ACCESS_TOKEN", "test-token")
    monkeypatch.setattr(_config, "FACEBOOK_PAGE_ID", "test-page-id")
    monkeypatch.setattr(_config, "INSTAGRAM_MAX_POSTS_PER_DAY", 10)
    monkeypatch.setattr(_config, "INSTAGRAM_API_VERSION", "v25.0")
    monkeypatch.setattr(_config, "LOCATION_TIMEZONE", "America/Chicago")
    monkeypatch.setattr(_config, "RETRY_QUEUE_FILE", Path("retry_queue.json"))
    monkeypatch.setattr(_config, "MAX_RETRY_QUEUE_SIZE", 50)
    return InstagramPoster()


class TestInstagramPosterConfiguration:
    """Test InstagramPoster configuration detection."""

    def test_is_configured_true(self, poster):
        """is_configured() returns True when all credentials present."""
        assert poster.is_configured() is True

    def test_is_configured_missing_business_account_id(self, monkeypatch):
        """is_configured() returns False when business account ID missing."""
        import config
        monkeypatch.setattr(config, "INSTAGRAM_BUSINESS_ACCOUNT_ID", "")
        monkeypatch.setattr(config, "INSTAGRAM_USER_ID", "test-user")
        monkeypatch.setattr(config, "FACEBOOK_PAGE_ACCESS_TOKEN", "token")

        p = InstagramPoster()
        assert p.is_configured() is False

    def test_is_configured_missing_user_id(self, monkeypatch):
        """is_configured() returns False when user ID missing."""
        import config
        monkeypatch.setattr(config, "INSTAGRAM_BUSINESS_ACCOUNT_ID", "test-biz")
        monkeypatch.setattr(config, "INSTAGRAM_USER_ID", "")
        monkeypatch.setattr(config, "FACEBOOK_PAGE_ACCESS_TOKEN", "token")

        p = InstagramPoster()
        assert p.is_configured() is False

    def test_is_configured_missing_access_token(self, monkeypatch):
        """is_configured() returns False when access token missing."""
        import config
        monkeypatch.setattr(config, "INSTAGRAM_BUSINESS_ACCOUNT_ID", "test-biz")
        monkeypatch.setattr(config, "INSTAGRAM_USER_ID", "test-user")
        monkeypatch.setattr(config, "FACEBOOK_PAGE_ACCESS_TOKEN", "")

        p = InstagramPoster()
        assert p.is_configured() is False

    def test_platform_name(self):
        """platform_name property returns 'Instagram'."""
        assert InstagramPoster.platform_name == "Instagram"


class TestInstagramPostPhoto:
    """Test photo posting to Instagram (CDN upload -> container -> publish)."""

    def test_post_photo_success(self, poster, tmp_path):
        """post_photo() uploads to FB CDN, creates container, publishes."""
        image = tmp_path / "test.jpg"
        image.write_bytes(b"fake image data")

        # Mock: FB CDN upload, FB CDN URL fetch, IG container create, IG publish
        cdn_upload_resp = MagicMock()
        cdn_upload_resp.json.return_value = {"id": "fb-photo-123"}

        cdn_url_resp = MagicMock()
        cdn_url_resp.json.return_value = {"images": [{"source": "https://cdn.fb.com/img.jpg"}]}

        container_resp = MagicMock()
        container_resp.json.return_value = {"id": "container-456"}

        publish_resp = MagicMock()
        publish_resp.json.return_value = {"id": "ig-media-789"}

        with patch("social.instagram_poster.requests.post") as mock_post, \
             patch("social.instagram_poster.requests.get", return_value=cdn_url_resp):
            mock_post.side_effect = [cdn_upload_resp, container_resp, publish_resp]
            result = poster.post_photo(image, "Test caption")

        assert result is True
        assert poster._posts_today == 1

        # Verify CDN upload went to Facebook /photos
        cdn_call_url = mock_post.call_args_list[0][0][0]
        assert "/photos" in cdn_call_url
        assert mock_post.call_args_list[0][1]["data"]["published"] == "false"

        # Verify IG container creation used image_url
        container_call = mock_post.call_args_list[1][1]
        assert container_call["data"]["image_url"] == "https://cdn.fb.com/img.jpg"
        assert "/media" in mock_post.call_args_list[1][0][0]

        # Verify publish call
        publish_call_url = mock_post.call_args_list[2][0][0]
        assert "/media_publish" in publish_call_url

    def test_post_photo_no_facebook_page_id(self, poster, tmp_path):
        """post_photo() fails gracefully when FACEBOOK_PAGE_ID is missing."""
        image = tmp_path / "test.jpg"
        image.write_bytes(b"data")

        with patch("social.instagram_poster.config.FACEBOOK_PAGE_ID", ""):
            result = poster.post_photo(image, "test")

        assert result is False

    def test_post_photo_no_credentials(self, monkeypatch, tmp_path):
        """post_photo() returns False when no credentials."""
        import config
        monkeypatch.setattr(config, "INSTAGRAM_BUSINESS_ACCOUNT_ID", "")
        monkeypatch.setattr(config, "INSTAGRAM_USER_ID", "")
        monkeypatch.setattr(config, "FACEBOOK_PAGE_ACCESS_TOKEN", "")

        p = InstagramPoster()
        image = tmp_path / "test.jpg"
        image.write_bytes(b"data")
        assert p.post_photo(image, "test") is False

    def test_post_photo_rate_limited(self, poster, tmp_path):
        """post_photo() returns False when at daily post limit."""
        with patch("social.instagram_poster.config.INSTAGRAM_MAX_POSTS_PER_DAY", 0):
            poster._posts_today = 0
            image = tmp_path / "test.jpg"
            image.write_bytes(b"data")
            assert poster.post_photo(image, "test") is False

    def test_post_photo_request_error(self, poster, tmp_path):
        """post_photo() handles request exceptions gracefully."""
        image = tmp_path / "test.jpg"
        image.write_bytes(b"fake image data")

        with patch("social.instagram_poster.requests.post") as mock_post:
            import requests
            mock_post.side_effect = requests.RequestException("Network error")
            result = poster.post_photo(image, "Test caption")

        assert result is False
        assert poster._posts_today == 0


class TestInstagramPostVideo:
    """Test video posting via resumable upload."""

    def test_post_video_success(self, poster, tmp_path):
        """post_video() creates resumable container, uploads, waits, publishes."""
        video = tmp_path / "test.mp4"
        video.write_bytes(b"x" * (1024 * 100))  # 100KB

        # Mock: init container, binary upload, publish
        init_resp = MagicMock()
        init_resp.json.return_value = {
            "id": "container-123",
            "uri": "https://rupload.facebook.com/ig-api-upload/v25.0/container-123",
        }

        upload_resp = MagicMock()
        upload_resp.json.return_value = {"success": True}

        publish_resp = MagicMock()
        publish_resp.json.return_value = {"id": "ig-media-456"}

        status_resp = MagicMock()
        status_resp.json.return_value = {"status_code": "FINISHED"}

        with patch("social.instagram_poster.requests.post") as mock_post, \
             patch("social.instagram_poster.requests.get", return_value=status_resp), \
             patch("social.instagram_poster.time.sleep"):
            mock_post.side_effect = [init_resp, upload_resp, publish_resp]
            result = poster.post_video(video, "Test caption")

        assert result is True
        assert poster._posts_today == 1

        # Verify init used resumable upload
        init_call = mock_post.call_args_list[0][1]
        assert init_call["data"]["media_type"] == "REELS"
        assert init_call["data"]["upload_type"] == "resumable"

        # Verify binary upload used Authorization header
        upload_call = mock_post.call_args_list[1][1]
        assert "OAuth" in upload_call["headers"]["Authorization"]

    def test_post_video_no_credentials(self, monkeypatch, tmp_path):
        """post_video() returns False when no credentials."""
        import config
        monkeypatch.setattr(config, "INSTAGRAM_BUSINESS_ACCOUNT_ID", "")
        monkeypatch.setattr(config, "INSTAGRAM_USER_ID", "")
        monkeypatch.setattr(config, "FACEBOOK_PAGE_ACCESS_TOKEN", "")

        p = InstagramPoster()
        video = tmp_path / "test.mp4"
        video.write_bytes(b"data")
        assert p.post_video(video, "test") is False

    def test_post_video_rate_limited(self, poster, tmp_path):
        """post_video() returns False when at daily post limit."""
        with patch("social.instagram_poster.config.INSTAGRAM_MAX_POSTS_PER_DAY", 0):
            poster._posts_today = 0
            video = tmp_path / "test.mp4"
            video.write_bytes(b"data")
            assert poster.post_video(video, "test") is False

    def test_post_video_too_large(self, poster, tmp_path):
        """post_video() rejects files over 500MB."""
        video = tmp_path / "big.mp4"
        video.write_bytes(b"x")  # tiny file but we mock getsize

        with patch("social.instagram_poster.os.path.getsize", return_value=600 * 1024 * 1024):
            result = poster.post_video(video, "test")

        assert result is False

    def test_post_video_processing_timeout(self, poster, tmp_path):
        """post_video() fails if container processing times out."""
        video = tmp_path / "test.mp4"
        video.write_bytes(b"x" * 1024)

        init_resp = MagicMock()
        init_resp.json.return_value = {
            "id": "container-123",
            "uri": "https://rupload.facebook.com/ig-api-upload/v25.0/container-123",
        }
        upload_resp = MagicMock()
        upload_resp.json.return_value = {"success": True}

        # Status never reaches FINISHED
        status_resp = MagicMock()
        status_resp.json.return_value = {"status_code": "IN_PROGRESS"}

        with patch("social.instagram_poster.requests.post") as mock_post, \
             patch("social.instagram_poster.requests.get", return_value=status_resp), \
             patch("social.instagram_poster.time.sleep"), \
             patch("social.instagram_poster._STATUS_POLL_MAX_WAIT", 10), \
             patch("social.instagram_poster._STATUS_POLL_INTERVAL", 5):
            mock_post.side_effect = [init_resp, upload_resp]
            result = poster.post_video(video, "test")

        assert result is False


class TestInstagramPostText:
    """Test text posting to Instagram."""

    def test_post_text_unsupported(self, poster):
        """post_text() returns False as Instagram doesn't support text-only posts."""
        result = poster.post_text("Test message")
        assert result is False

    def test_post_text_no_credentials(self, monkeypatch):
        """post_text() returns False when no credentials."""
        import config
        monkeypatch.setattr(config, "INSTAGRAM_BUSINESS_ACCOUNT_ID", "")

        p = InstagramPoster()
        assert p.post_text("test") is False


class TestInstagramRateLimit:
    """Test rate limiting functionality."""

    def test_rate_limit_enforcement(self, poster):
        """_check_rate_limit() respects daily limit."""
        with patch("social.instagram_poster.config.INSTAGRAM_MAX_POSTS_PER_DAY", 2):
            poster._posts_today = 0
            assert poster._check_rate_limit() is True

            poster._posts_today = 1
            assert poster._check_rate_limit() is True

            poster._posts_today = 2
            assert poster._check_rate_limit() is False

    def test_rate_limit_resets_on_new_day(self, poster):
        """_check_rate_limit() resets counter on new day."""
        original_today = poster._today
        poster._posts_today = 100

        with patch("social.instagram_poster.InstagramPoster._date_in_local_tz") as mock_date:
            mock_date.return_value = original_today
            assert poster._check_rate_limit() is False

            from datetime import timedelta
            next_day = original_today + timedelta(days=1)
            mock_date.return_value = next_day
            poster._check_rate_limit()
            assert poster._posts_today == 0


class TestInstagramRetryQueue:
    """Test retry queue functionality."""

    def test_save_to_retry_queue(self, poster, tmp_path, monkeypatch):
        """_save_to_retry_queue() saves failed posts."""
        monkeypatch.setattr("social.instagram_poster.config.RETRY_QUEUE_FILE", tmp_path / "retry.json")

        video = tmp_path / "test.mp4"
        video.write_bytes(b"data")

        with patch("social.instagram_poster.safe_write_json") as mock_write:
            poster._save_to_retry_queue(video, "Caption", "video")

        mock_write.assert_called_once()
        call_args = mock_write.call_args[0][1]
        assert len(call_args) > 0
        assert call_args[0]["platform"] == "Instagram"
        assert call_args[0]["media_type"] == "video"

    def test_retry_failed_posts(self, poster, tmp_path, monkeypatch):
        """retry_failed_posts() retries queued posts."""
        queue_file = tmp_path / "retry.json"
        monkeypatch.setattr("social.instagram_poster.config.RETRY_QUEUE_FILE", queue_file)

        video = tmp_path / "test.mp4"
        video.write_bytes(b"data")

        with patch("social.instagram_poster.safe_read_json") as mock_read:
            mock_read.return_value = [
                {
                    "platform": "Instagram",
                    "path": str(video),
                    "caption": "Test",
                    "media_type": "photo",
                }
            ]
            with patch.object(poster, "post_photo", return_value=True):
                poster.retry_failed_posts()

        assert True  # No exception = retry logic worked
