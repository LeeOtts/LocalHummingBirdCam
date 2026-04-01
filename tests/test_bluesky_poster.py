"""Tests for social/bluesky_poster.py — BlueskyPoster class."""

import io
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from social.bluesky_poster import BlueskyPoster


@pytest.fixture
def poster(monkeypatch):
    """Create a BlueskyPoster with test credentials."""
    import config
    monkeypatch.setattr(config, "BLUESKY_HANDLE", "test.bsky.social")
    monkeypatch.setattr(config, "BLUESKY_APP_PASSWORD", "test-password")
    p = BlueskyPoster()
    p._client = MagicMock()
    return p


# ---------------------------------------------------------------------------
# is_configured
# ---------------------------------------------------------------------------

class TestIsConfigured:
    def test_configured_when_both_set(self, monkeypatch):
        import config
        monkeypatch.setattr(config, "BLUESKY_HANDLE", "user.bsky.social")
        monkeypatch.setattr(config, "BLUESKY_APP_PASSWORD", "pass123")
        p = BlueskyPoster()
        assert p.is_configured() is True

    def test_not_configured_when_handle_empty(self, monkeypatch):
        import config
        monkeypatch.setattr(config, "BLUESKY_HANDLE", "")
        monkeypatch.setattr(config, "BLUESKY_APP_PASSWORD", "pass123")
        p = BlueskyPoster()
        assert p.is_configured() is False

    def test_not_configured_when_password_empty(self, monkeypatch):
        import config
        monkeypatch.setattr(config, "BLUESKY_HANDLE", "user.bsky.social")
        monkeypatch.setattr(config, "BLUESKY_APP_PASSWORD", "")
        p = BlueskyPoster()
        assert p.is_configured() is False


# ---------------------------------------------------------------------------
# _truncate
# ---------------------------------------------------------------------------

class TestTruncate:
    def test_short_text_unchanged(self):
        p = BlueskyPoster()
        assert p._truncate("hello") == "hello"

    def test_long_text_truncated(self):
        p = BlueskyPoster()
        text = "word " * 100  # 500 chars
        result = p._truncate(text)
        assert len(result) <= 300
        assert result.endswith("...")

    def test_exact_length_unchanged(self):
        p = BlueskyPoster()
        text = "a" * 300
        assert p._truncate(text) == text

    def test_custom_max_len(self):
        p = BlueskyPoster()
        text = "word " * 20
        result = p._truncate(text, max_len=20)
        assert len(result) <= 20


# ---------------------------------------------------------------------------
# _compress_image
# ---------------------------------------------------------------------------

class TestCompressImage:
    def test_small_image_returned_unchanged(self):
        data = b"\x00" * 100
        result = BlueskyPoster._compress_image(data)
        assert result == data

    def test_large_image_compressed(self, tmp_path):
        from PIL import Image
        # Create a large test image with random-ish data so it compresses meaningfully
        img = Image.new("RGB", (500, 500), color="red")
        buf = io.BytesIO()
        img.save(buf, format="BMP")  # BMP is uncompressed, guarantees large size
        img_data = buf.getvalue()

        result = BlueskyPoster._compress_image(img_data, max_bytes=5000)
        assert len(result) <= 5000

    def test_rgba_converted_to_rgb(self):
        from PIL import Image
        img = Image.new("RGBA", (100, 100), color=(255, 0, 0, 128))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        img_data = buf.getvalue()

        # Should not error even with RGBA
        result = BlueskyPoster._compress_image(img_data, max_bytes=100)
        assert isinstance(result, bytes)


# ---------------------------------------------------------------------------
# post_text
# ---------------------------------------------------------------------------

class TestPostText:
    def test_success(self, poster):
        assert poster.post_text("Hello Bluesky!") is True
        poster._client.send_post.assert_called_once()

    def test_returns_false_when_no_client(self, monkeypatch):
        import config
        monkeypatch.setattr(config, "BLUESKY_HANDLE", "user")
        monkeypatch.setattr(config, "BLUESKY_APP_PASSWORD", "pass")
        p = BlueskyPoster()
        p._client = None
        with patch.object(p, "_get_client", return_value=None):
            assert p.post_text("hello") is False

    def test_exception_resets_client(self, poster):
        poster._client.send_post.side_effect = Exception("network error")
        assert poster.post_text("hello") is False
        assert poster._client is None


# ---------------------------------------------------------------------------
# post_photo
# ---------------------------------------------------------------------------

class TestPostPhoto:
    def test_success(self, poster, tmp_path):
        from PIL import Image
        img = Image.new("RGB", (10, 10), color="blue")
        img_path = tmp_path / "test.jpg"
        img.save(img_path, format="JPEG")

        upload_result = MagicMock()
        upload_result.blob = MagicMock()
        poster._client.upload_blob.return_value = upload_result

        with patch("social.bluesky_poster.BlueskyPoster._compress_image", return_value=b"\xff"):
            with patch("atproto.models") as mock_models:
                assert poster.post_photo(img_path, "A bird!") is True

        poster._client.send_post.assert_called_once()

    def test_returns_false_when_no_client(self, monkeypatch, tmp_path):
        import config
        monkeypatch.setattr(config, "BLUESKY_HANDLE", "user")
        monkeypatch.setattr(config, "BLUESKY_APP_PASSWORD", "pass")
        p = BlueskyPoster()
        with patch.object(p, "_get_client", return_value=None):
            assert p.post_photo(tmp_path / "img.jpg", "caption") is False

    def test_exception_resets_client(self, poster, tmp_path):
        from PIL import Image
        img = Image.new("RGB", (10, 10))
        img_path = tmp_path / "test.jpg"
        img.save(img_path, format="JPEG")

        poster._client.upload_blob.side_effect = Exception("fail")
        with patch("social.bluesky_poster.BlueskyPoster._compress_image", return_value=b"\xff"):
            assert poster.post_photo(img_path, "caption") is False
        assert poster._client is None


# ---------------------------------------------------------------------------
# post_video
# ---------------------------------------------------------------------------

class TestPostVideo:
    def test_returns_false_when_no_client(self, monkeypatch, tmp_path):
        import config
        monkeypatch.setattr(config, "BLUESKY_HANDLE", "user")
        monkeypatch.setattr(config, "BLUESKY_APP_PASSWORD", "pass")
        p = BlueskyPoster()
        with patch.object(p, "_get_client", return_value=None):
            assert p.post_video(tmp_path / "vid.mp4", "caption") is False

    def test_success(self, poster, tmp_path):
        vid_path = tmp_path / "test.mp4"
        vid_path.write_bytes(b"\x00" * 100)

        upload_result = MagicMock()
        upload_result.blob = MagicMock()
        poster._client.upload_blob.return_value = upload_result

        with patch("atproto.models") as mock_models:
            assert poster.post_video(vid_path, "A hummingbird!") is True

        poster._client.send_post.assert_called_once()

    def test_oversized_video_falls_back_to_text(self, poster, tmp_path):
        vid_path = tmp_path / "big.mp4"
        # Write just enough to trigger the size check via mock
        vid_path.write_bytes(b"\x00" * 100)

        # Mock read_bytes to return oversized data
        with patch.object(Path, "read_bytes", return_value=b"\x00" * (51 * 1024 * 1024)):
            with patch.object(poster, "post_text", return_value=True) as mock_text:
                assert poster.post_video(vid_path, "big video") is True
                mock_text.assert_called_once_with("big video")

    def test_exception_resets_client(self, poster, tmp_path):
        vid_path = tmp_path / "test.mp4"
        vid_path.write_bytes(b"\x00" * 100)
        poster._client.upload_blob.side_effect = Exception("upload error")
        assert poster.post_video(vid_path, "caption") is False
        assert poster._client is None


# ---------------------------------------------------------------------------
# _get_client
# ---------------------------------------------------------------------------

class TestGetClient:
    def test_returns_cached_client(self, poster):
        client = poster._get_client()
        assert client is poster._client

    def test_login_failure_returns_none(self, monkeypatch):
        import config
        monkeypatch.setattr(config, "BLUESKY_HANDLE", "user")
        monkeypatch.setattr(config, "BLUESKY_APP_PASSWORD", "pass")
        p = BlueskyPoster()

        # Mock the atproto module so import succeeds but login raises
        mock_atproto = MagicMock()
        mock_client_instance = MagicMock()
        mock_client_instance.login.side_effect = Exception("bad credentials")
        mock_atproto.Client.return_value = mock_client_instance

        with patch.dict("sys.modules", {"atproto": mock_atproto}):
            result = p._get_client()
            assert result is None
            assert p._client is None
