"""Tests for TikTokPoster."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from social.tiktok_poster import TikTokPoster


@pytest.fixture
def poster(tmp_path):
    """Create a TikTokPoster with test credentials."""
    with patch("social.tiktok_poster.config") as mock_config:
        mock_config.TIKTOK_CLIENT_KEY = "test-client-key"
        mock_config.TIKTOK_CLIENT_SECRET = "test-client-secret"
        mock_config.TIKTOK_ACCESS_TOKEN = "test-access-token"
        mock_config.TIKTOK_REFRESH_TOKEN = "test-refresh-token"
        mock_config.TIKTOK_MAX_POSTS_PER_DAY = 10
        mock_config.TIKTOK_PRIVACY_LEVEL = "PUBLIC_TO_EVERYONE"
        mock_config.LOCATION_TIMEZONE = "America/Chicago"
        mock_config.BASE_DIR = tmp_path
        p = TikTokPoster()
        return p


class TestTikTokConfiguration:
    """Test TikTokPoster configuration detection."""

    def test_is_configured_true(self, poster):
        assert poster.is_configured() is True

    def test_is_configured_missing_client_key(self, tmp_path):
        with patch("social.tiktok_poster.config") as mock_config:
            mock_config.TIKTOK_CLIENT_KEY = ""
            mock_config.TIKTOK_CLIENT_SECRET = "secret"
            mock_config.TIKTOK_ACCESS_TOKEN = "token"
            mock_config.TIKTOK_REFRESH_TOKEN = ""
            mock_config.BASE_DIR = tmp_path
            p = TikTokPoster()
            assert p.is_configured() is False

    def test_is_configured_missing_access_token(self, tmp_path):
        with patch("social.tiktok_poster.config") as mock_config:
            mock_config.TIKTOK_CLIENT_KEY = "key"
            mock_config.TIKTOK_CLIENT_SECRET = "secret"
            mock_config.TIKTOK_ACCESS_TOKEN = ""
            mock_config.TIKTOK_REFRESH_TOKEN = ""
            mock_config.BASE_DIR = tmp_path
            p = TikTokPoster()
            assert p.is_configured() is False

    def test_platform_name(self):
        assert TikTokPoster.platform_name == "TikTok"


class TestTikTokPostVideo:
    """Test video posting to TikTok."""

    def test_post_video_success(self, poster, tmp_path):
        """post_video() initializes, uploads chunks, checks status."""
        video = tmp_path / "test.mp4"
        video.write_bytes(b"x" * (1024 * 100))  # 100KB

        init_resp = MagicMock()
        init_resp.status_code = 200
        init_resp.json.return_value = {
            "data": {
                "publish_id": "pub-123",
                "upload_url": "https://upload.tiktok.com/video/123",
            }
        }

        # Chunk upload (single chunk for small file)
        chunk_resp = MagicMock()
        chunk_resp.status_code = 201

        # Status check
        status_resp = MagicMock()
        status_resp.status_code = 200
        status_resp.json.return_value = {
            "data": {"status": "PUBLISH_COMPLETE"}
        }

        with patch("social.tiktok_poster.requests.post") as mock_post, \
             patch("social.tiktok_poster.requests.put", return_value=chunk_resp), \
             patch("social.tiktok_poster.time.sleep"):
            mock_post.side_effect = [init_resp, status_resp]
            result = poster.post_video(video, "Test caption")

        assert result is True
        assert poster._posts_today == 1

        # Verify init call
        init_call = mock_post.call_args_list[0]
        assert "/post/publish/video/init/" in init_call[0][0]

    def test_post_video_no_credentials(self, tmp_path):
        with patch("social.tiktok_poster.config") as mock_config:
            mock_config.TIKTOK_CLIENT_KEY = ""
            mock_config.TIKTOK_CLIENT_SECRET = ""
            mock_config.TIKTOK_ACCESS_TOKEN = ""
            mock_config.TIKTOK_REFRESH_TOKEN = ""
            mock_config.BASE_DIR = tmp_path
            p = TikTokPoster()
            video = tmp_path / "test.mp4"
            video.write_bytes(b"data")
            assert p.post_video(video, "test") is False

    def test_post_video_rate_limited(self, poster, tmp_path):
        with patch("social.tiktok_poster.config.TIKTOK_MAX_POSTS_PER_DAY", 0):
            poster._posts_today = 0
            video = tmp_path / "test.mp4"
            video.write_bytes(b"data")
            assert poster.post_video(video, "test") is False

    def test_post_video_too_large(self, poster, tmp_path):
        video = tmp_path / "big.mp4"
        video.write_bytes(b"x")

        with patch("social.tiktok_poster.os.path.getsize",
                    return_value=5 * 1024 * 1024 * 1024):  # 5GB
            result = poster.post_video(video, "test")
        assert result is False

    def test_post_video_api_error(self, poster, tmp_path):
        video = tmp_path / "test.mp4"
        video.write_bytes(b"x" * 1024)

        with patch("social.tiktok_poster.requests.post") as mock_post:
            import requests
            mock_post.side_effect = requests.RequestException("API error")
            result = poster.post_video(video, "test")

        assert result is False

    def test_post_video_token_refresh_on_401(self, poster, tmp_path):
        """post_video() refreshes token on 401 and retries."""
        video = tmp_path / "test.mp4"
        video.write_bytes(b"x" * 1024)

        # First init returns 401, refresh succeeds, retry succeeds
        resp_401 = MagicMock()
        resp_401.status_code = 401

        refresh_resp = MagicMock()
        refresh_resp.status_code = 200
        refresh_resp.json.return_value = {
            "access_token": "new-token",
            "refresh_token": "new-refresh",
        }

        init_resp = MagicMock()
        init_resp.status_code = 200
        init_resp.json.return_value = {
            "data": {
                "publish_id": "pub-456",
                "upload_url": "https://upload.tiktok.com/video/456",
            }
        }

        chunk_resp = MagicMock()
        chunk_resp.status_code = 201

        status_resp = MagicMock()
        status_resp.status_code = 200
        status_resp.json.return_value = {"data": {"status": "PUBLISH_COMPLETE"}}

        with patch("social.tiktok_poster.requests.post") as mock_post, \
             patch("social.tiktok_poster.requests.put", return_value=chunk_resp), \
             patch("social.tiktok_poster.time.sleep"):
            mock_post.side_effect = [resp_401, refresh_resp, init_resp, status_resp]
            result = poster.post_video(video, "Test")

        assert result is True


class TestTikTokPostPhoto:
    """Test photo posting to TikTok."""

    def test_post_photo_success(self, poster, tmp_path):
        image = tmp_path / "test.jpg"
        image.write_bytes(b"fake image data")

        init_resp = MagicMock()
        init_resp.status_code = 200
        init_resp.json.return_value = {
            "data": {
                "publish_id": "pub-photo-123",
                "upload_url": ["https://upload.tiktok.com/photo/123"],
            }
        }

        upload_resp = MagicMock()
        upload_resp.status_code = 201

        status_resp = MagicMock()
        status_resp.status_code = 200
        status_resp.json.return_value = {"data": {"status": "PUBLISH_COMPLETE"}}

        with patch("social.tiktok_poster.requests.post") as mock_post, \
             patch("social.tiktok_poster.requests.put", return_value=upload_resp), \
             patch("social.tiktok_poster.time.sleep"):
            mock_post.side_effect = [init_resp, status_resp]
            result = poster.post_photo(image, "Test photo")

        assert result is True
        assert poster._posts_today == 1

    def test_post_photo_no_credentials(self, tmp_path):
        with patch("social.tiktok_poster.config") as mock_config:
            mock_config.TIKTOK_CLIENT_KEY = ""
            mock_config.TIKTOK_CLIENT_SECRET = ""
            mock_config.TIKTOK_ACCESS_TOKEN = ""
            mock_config.TIKTOK_REFRESH_TOKEN = ""
            mock_config.BASE_DIR = tmp_path
            p = TikTokPoster()
            image = tmp_path / "test.jpg"
            image.write_bytes(b"data")
            assert p.post_photo(image, "test") is False

    def test_post_photo_api_error(self, poster, tmp_path):
        image = tmp_path / "test.jpg"
        image.write_bytes(b"data")

        with patch("social.tiktok_poster.requests.post") as mock_post:
            import requests
            mock_post.side_effect = requests.RequestException("fail")
            result = poster.post_photo(image, "test")

        assert result is False


class TestTikTokPostText:
    """TikTok doesn't support text-only posts."""

    def test_post_text_unsupported(self, poster):
        assert poster.post_text("Test") is False

    def test_post_text_no_credentials(self, tmp_path):
        with patch("social.tiktok_poster.config") as mock_config:
            mock_config.TIKTOK_CLIENT_KEY = ""
            mock_config.TIKTOK_CLIENT_SECRET = ""
            mock_config.TIKTOK_ACCESS_TOKEN = ""
            mock_config.TIKTOK_REFRESH_TOKEN = ""
            mock_config.BASE_DIR = tmp_path
            p = TikTokPoster()
            assert p.post_text("test") is False


class TestTikTokRateLimit:
    """Test rate limiting."""

    def test_rate_limit_enforcement(self, poster):
        with patch("social.tiktok_poster.config.TIKTOK_MAX_POSTS_PER_DAY", 2):
            poster._posts_today = 0
            assert poster._check_rate_limit() is True
            poster._posts_today = 2
            assert poster._check_rate_limit() is False

    def test_rate_limit_resets_on_new_day(self, poster):
        original_today = poster._today
        poster._posts_today = 100

        with patch("social.tiktok_poster.TikTokPoster._date_in_local_tz") as mock_date:
            from datetime import timedelta
            next_day = original_today + timedelta(days=1)
            mock_date.return_value = next_day
            poster._check_rate_limit()
            assert poster._posts_today == 0


class TestTikTokTokenRefresh:
    """Test OAuth token refresh."""

    def test_refresh_access_token_success(self, poster):
        refresh_resp = MagicMock()
        refresh_resp.status_code = 200
        refresh_resp.json.return_value = {
            "access_token": "new-access-token",
            "refresh_token": "new-refresh-token",
        }

        with patch("social.tiktok_poster.requests.post", return_value=refresh_resp):
            result = poster._refresh_access_token()

        assert result is True
        assert poster._access_token == "new-access-token"
        assert poster._refresh_token == "new-refresh-token"

    def test_refresh_access_token_no_refresh_token(self, tmp_path):
        with patch("social.tiktok_poster.config") as mock_config:
            mock_config.TIKTOK_CLIENT_KEY = "key"
            mock_config.TIKTOK_CLIENT_SECRET = "secret"
            mock_config.TIKTOK_ACCESS_TOKEN = "token"
            mock_config.TIKTOK_REFRESH_TOKEN = ""
            mock_config.BASE_DIR = tmp_path
            p = TikTokPoster()
            assert p._refresh_access_token() is False

    def test_refresh_access_token_api_error(self, poster):
        with patch("social.tiktok_poster.requests.post") as mock_post:
            import requests
            mock_post.side_effect = requests.RequestException("fail")
            result = poster._refresh_access_token()

        assert result is False


class TestTikTokChunking:
    """Test chunk computation."""

    def test_small_file_single_chunk(self):
        chunk_size, count = TikTokPoster._compute_chunks(1024 * 1024)  # 1MB
        assert count == 1
        assert chunk_size == 1024 * 1024

    def test_medium_file_default_chunks(self):
        size = 50 * 1024 * 1024  # 50MB
        chunk_size, count = TikTokPoster._compute_chunks(size)
        assert chunk_size == 10 * 1024 * 1024  # 10MB default
        assert count == 5

    def test_exact_boundary(self):
        size = 10 * 1024 * 1024  # Exactly 10MB
        chunk_size, count = TikTokPoster._compute_chunks(size)
        assert count == 1
