"""Tests for social/twitter_poster.py — TwitterPoster class."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from social.twitter_poster import TwitterPoster


@pytest.fixture
def poster(monkeypatch):
    """Create a TwitterPoster with test credentials and mock clients."""
    import config
    monkeypatch.setattr(config, "TWITTER_API_KEY", "test-key")
    monkeypatch.setattr(config, "TWITTER_API_SECRET", "test-secret")
    monkeypatch.setattr(config, "TWITTER_ACCESS_TOKEN", "test-token")
    monkeypatch.setattr(config, "TWITTER_ACCESS_SECRET", "test-access-secret")
    p = TwitterPoster()
    p._client = MagicMock()
    p._api_v1 = MagicMock()
    return p


# ---------------------------------------------------------------------------
# is_configured
# ---------------------------------------------------------------------------

class TestIsConfigured:
    def test_configured_when_all_set(self, monkeypatch):
        import config
        monkeypatch.setattr(config, "TWITTER_API_KEY", "k")
        monkeypatch.setattr(config, "TWITTER_API_SECRET", "s")
        monkeypatch.setattr(config, "TWITTER_ACCESS_TOKEN", "t")
        monkeypatch.setattr(config, "TWITTER_ACCESS_SECRET", "a")
        p = TwitterPoster()
        assert p.is_configured() is True

    def test_not_configured_when_key_missing(self, monkeypatch):
        import config
        monkeypatch.setattr(config, "TWITTER_API_KEY", "")
        monkeypatch.setattr(config, "TWITTER_API_SECRET", "s")
        monkeypatch.setattr(config, "TWITTER_ACCESS_TOKEN", "t")
        monkeypatch.setattr(config, "TWITTER_ACCESS_SECRET", "a")
        p = TwitterPoster()
        assert p.is_configured() is False

    def test_not_configured_when_all_empty(self, monkeypatch):
        import config
        monkeypatch.setattr(config, "TWITTER_API_KEY", "")
        monkeypatch.setattr(config, "TWITTER_API_SECRET", "")
        monkeypatch.setattr(config, "TWITTER_ACCESS_TOKEN", "")
        monkeypatch.setattr(config, "TWITTER_ACCESS_SECRET", "")
        p = TwitterPoster()
        assert p.is_configured() is False


# ---------------------------------------------------------------------------
# _truncate
# ---------------------------------------------------------------------------

class TestTruncate:
    def test_short_text_unchanged(self):
        p = TwitterPoster()
        assert p._truncate("hello") == "hello"

    def test_long_text_truncated_to_280(self):
        p = TwitterPoster()
        text = "word " * 100  # 500 chars
        result = p._truncate(text)
        assert len(result) <= 280
        assert result.endswith("...")

    def test_exact_280_unchanged(self):
        p = TwitterPoster()
        text = "a" * 280
        assert p._truncate(text) == text

    def test_custom_max_len(self):
        p = TwitterPoster()
        result = p._truncate("a" * 50, max_len=20)
        assert len(result) <= 20


# ---------------------------------------------------------------------------
# post_text
# ---------------------------------------------------------------------------

class TestPostText:
    def test_success(self, poster):
        assert poster.post_text("Hello Twitter!") is True
        poster._client.create_tweet.assert_called_once()

    def test_returns_false_when_no_client(self, monkeypatch):
        import config
        monkeypatch.setattr(config, "TWITTER_API_KEY", "k")
        monkeypatch.setattr(config, "TWITTER_API_SECRET", "s")
        monkeypatch.setattr(config, "TWITTER_ACCESS_TOKEN", "t")
        monkeypatch.setattr(config, "TWITTER_ACCESS_SECRET", "a")
        p = TwitterPoster()
        with patch.object(p, "_get_clients", return_value=(None, None)):
            assert p.post_text("hello") is False

    def test_exception_resets_client(self, poster):
        poster._client.create_tweet.side_effect = Exception("rate limit")
        assert poster.post_text("hello") is False
        assert poster._client is None


# ---------------------------------------------------------------------------
# post_photo
# ---------------------------------------------------------------------------

class TestPostPhoto:
    def test_success(self, poster, tmp_path):
        img_path = tmp_path / "test.jpg"
        img_path.write_bytes(b"\xff\xd8\xff")

        media = MagicMock()
        media.media_id = 12345
        poster._api_v1.media_upload.return_value = media

        assert poster.post_photo(img_path, "A bird photo!") is True
        poster._client.create_tweet.assert_called_once()

    def test_returns_false_when_no_client(self, monkeypatch, tmp_path):
        import config
        monkeypatch.setattr(config, "TWITTER_API_KEY", "k")
        monkeypatch.setattr(config, "TWITTER_API_SECRET", "s")
        monkeypatch.setattr(config, "TWITTER_ACCESS_TOKEN", "t")
        monkeypatch.setattr(config, "TWITTER_ACCESS_SECRET", "a")
        p = TwitterPoster()
        with patch.object(p, "_get_clients", return_value=(None, None)):
            assert p.post_photo(tmp_path / "img.jpg", "caption") is False

    def test_exception_resets_clients(self, poster, tmp_path):
        img_path = tmp_path / "test.jpg"
        img_path.write_bytes(b"\xff")
        poster._api_v1.media_upload.side_effect = Exception("fail")
        assert poster.post_photo(img_path, "caption") is False
        assert poster._client is None
        assert poster._api_v1 is None


# ---------------------------------------------------------------------------
# post_video
# ---------------------------------------------------------------------------

class TestPostVideo:
    def test_success(self, poster, tmp_path):
        vid_path = tmp_path / "test.mp4"
        vid_path.write_bytes(b"\x00" * 100)

        media = MagicMock(spec=[])  # no processing_info attr
        media.media_id = 99999
        poster._api_v1.chunked_upload.return_value = media

        assert poster.post_video(vid_path, "A hummingbird!") is True
        poster._client.create_tweet.assert_called_once()

    def test_returns_false_when_no_client(self, monkeypatch, tmp_path):
        import config
        monkeypatch.setattr(config, "TWITTER_API_KEY", "k")
        monkeypatch.setattr(config, "TWITTER_API_SECRET", "s")
        monkeypatch.setattr(config, "TWITTER_ACCESS_TOKEN", "t")
        monkeypatch.setattr(config, "TWITTER_ACCESS_SECRET", "a")
        p = TwitterPoster()
        with patch.object(p, "_get_clients", return_value=(None, None)):
            assert p.post_video(tmp_path / "vid.mp4", "caption") is False

    def test_oversized_video_falls_back_to_text(self, poster, tmp_path):
        vid_path = tmp_path / "big.mp4"
        vid_path.write_bytes(b"\x00" * 100)

        # Mock stat to return oversized file
        mock_stat = MagicMock()
        mock_stat.st_size = 513 * 1024 * 1024
        with patch.object(Path, "stat", return_value=mock_stat):
            with patch.object(poster, "post_text", return_value=True) as mock_text:
                assert poster.post_video(vid_path, "big video") is True
                mock_text.assert_called_once_with("big video")

    def test_exception_resets_clients(self, poster, tmp_path):
        vid_path = tmp_path / "test.mp4"
        vid_path.write_bytes(b"\x00" * 100)
        poster._api_v1.chunked_upload.side_effect = Exception("timeout")
        assert poster.post_video(vid_path, "caption") is False
        assert poster._client is None
        assert poster._api_v1 is None

    def test_waits_for_processing(self, poster, tmp_path):
        vid_path = tmp_path / "test.mp4"
        vid_path.write_bytes(b"\x00" * 100)

        media = MagicMock()
        media.media_id = 99999
        media.processing_info = {"state": "pending"}
        poster._api_v1.chunked_upload.return_value = media

        with patch.object(poster, "_wait_for_processing"):
            assert poster.post_video(vid_path, "video") is True


# ---------------------------------------------------------------------------
# _wait_for_processing
# ---------------------------------------------------------------------------

class TestWaitForProcessing:
    def test_returns_immediately_when_no_info(self, poster):
        status = MagicMock(spec=[])  # no processing_info
        poster._api_v1.get_media_upload_status.return_value = status
        poster._wait_for_processing(poster._api_v1, 12345)

    def test_returns_on_succeeded(self, poster):
        status = MagicMock()
        status.processing_info = {"state": "succeeded"}
        poster._api_v1.get_media_upload_status.return_value = status
        poster._wait_for_processing(poster._api_v1, 12345)

    def test_raises_on_failed(self, poster):
        status = MagicMock()
        status.processing_info = {"state": "failed", "error": "bad format"}
        poster._api_v1.get_media_upload_status.return_value = status
        with pytest.raises(RuntimeError, match="processing failed"):
            poster._wait_for_processing(poster._api_v1, 12345)

    @patch("social.twitter_poster.time.sleep")
    def test_polls_with_check_after_secs(self, mock_sleep, poster):
        pending = MagicMock()
        pending.processing_info = {"state": "pending", "check_after_secs": 3}
        succeeded = MagicMock()
        succeeded.processing_info = {"state": "succeeded"}

        poster._api_v1.get_media_upload_status.side_effect = [pending, succeeded]
        poster._wait_for_processing(poster._api_v1, 12345, max_wait=30)
        mock_sleep.assert_called_once_with(3)


# ---------------------------------------------------------------------------
# _get_clients
# ---------------------------------------------------------------------------

class TestGetClients:
    def test_returns_cached_clients(self, poster):
        client, api_v1 = poster._get_clients()
        assert client is poster._client
        assert api_v1 is poster._api_v1

    def test_auth_failure_returns_none(self, monkeypatch):
        import config
        monkeypatch.setattr(config, "TWITTER_API_KEY", "k")
        monkeypatch.setattr(config, "TWITTER_API_SECRET", "s")
        monkeypatch.setattr(config, "TWITTER_ACCESS_TOKEN", "t")
        monkeypatch.setattr(config, "TWITTER_ACCESS_SECRET", "a")
        p = TwitterPoster()

        # Mock tweepy so import succeeds but Client() raises
        mock_tweepy = MagicMock()
        mock_tweepy.Client.side_effect = Exception("auth failed")

        with patch.dict("sys.modules", {"tweepy": mock_tweepy}):
            client, api_v1 = p._get_clients()
            assert client is None
            assert api_v1 is None
