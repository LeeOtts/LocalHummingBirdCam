"""Tests for the /compose page and manual social media posting feature."""

import io
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def mock_monitor():
    m = MagicMock()
    m.running = True
    m.detection_state = "idle"
    m.test_mode = False
    m._detections_today = 0
    m._rejected_today = 0
    m._start_time = 0.0
    m._last_detection_time = 0.0
    m._total_lifetime_detections = 0
    m._all_time_record = 0
    m._yesterday_detections = 0
    m.camera.is_available = True
    m.camera.camera_type = "usb"
    m.camera.error = None
    m.poster._posts_today = 0
    m._cooldown_elapsed.return_value = True
    m.poster_manager.platform_names = ["Facebook", "Twitter", "Bluesky"]
    m.poster_manager.post_video.return_value = {"Facebook": True, "Twitter": True, "Bluesky": True}
    m.poster_manager.post_photo.return_value = {"Facebook": True, "Twitter": True, "Bluesky": True}
    m.poster_manager.post_text.return_value = {"Facebook": True, "Twitter": True, "Bluesky": True}
    return m


@pytest.fixture
def client(tmp_path, monkeypatch, mock_monitor):
    import config
    from web import dashboard

    monkeypatch.setattr(config, "CLIPS_DIR", tmp_path)
    monkeypatch.setattr(config, "LOGS_DIR", tmp_path)
    monkeypatch.setattr(config, "TRAINING_DIR", tmp_path / "training")

    dashboard.set_monitor(mock_monitor)
    dashboard.app.config["TESTING"] = True
    # Clear rate-limit state so tests aren't throttled
    dashboard._rate_limits.clear()

    with dashboard.app.test_client() as c:
        yield c, mock_monitor, tmp_path


class TestComposePage:
    """Test GET /compose serves the compose page."""

    def test_returns_200(self, client):
        c, m, _ = client
        resp = c.get("/compose")
        assert resp.status_code == 200

    def test_contains_compose_form(self, client):
        c, m, _ = client
        resp = c.get("/compose")
        html = resp.data.decode()
        assert "Generate Unhinged Captions" in html
        assert "Post to All Socials" in html

    def test_contains_upload_area(self, client):
        c, m, _ = client
        resp = c.get("/compose")
        html = resp.data.decode()
        assert 'id="media-file"' in html
        assert "user-notes" in html


class TestComposeUpload:
    """Test POST /api/compose/upload endpoint."""

    def test_upload_photo(self, client):
        c, m, tmp_path = client
        data = {"media": (io.BytesIO(b"fake image data"), "hummingbird.jpg")}
        resp = c.post("/api/compose/upload", data=data, content_type="multipart/form-data")
        assert resp.status_code == 200
        result = resp.get_json()
        assert result["ok"] is True
        assert "hummingbird.jpg" in result["filename"]
        # Verify file was saved
        saved = tmp_path / result["filename"]
        assert saved.exists()
        assert saved.read_bytes() == b"fake image data"

    def test_upload_video(self, client):
        c, m, tmp_path = client
        data = {"media": (io.BytesIO(b"fake video data" * 100), "clip.mp4")}
        resp = c.post("/api/compose/upload", data=data, content_type="multipart/form-data")
        assert resp.status_code == 200
        result = resp.get_json()
        assert result["ok"] is True
        assert "clip.mp4" in result["filename"]

    def test_upload_no_file_returns_400(self, client):
        c, m, _ = client
        resp = c.post("/api/compose/upload", data={}, content_type="multipart/form-data")
        assert resp.status_code == 400
        result = resp.get_json()
        assert result["ok"] is False

    def test_upload_empty_filename_returns_400(self, client):
        c, m, _ = client
        data = {"media": (io.BytesIO(b"data"), "")}
        resp = c.post("/api/compose/upload", data=data, content_type="multipart/form-data")
        assert resp.status_code == 400

    def test_upload_prefixes_with_timestamp(self, client):
        c, m, _ = client
        data = {"media": (io.BytesIO(b"data"), "test.jpg")}
        resp = c.post("/api/compose/upload", data=data, content_type="multipart/form-data")
        result = resp.get_json()
        assert result["filename"].startswith("manual_")


class TestComposeGenerate:
    """Test POST /api/compose/generate endpoint."""

    def test_generate_with_notes(self, client):
        c, m, _ = client
        mock_captions = {
            "Facebook": "Two hummers throwing hands at the feeder.",
            "Twitter": "Feeder fight club. No rules.",
            "Bluesky": "Territorial dispute at the sugar water bar.",
        }
        with patch("social.comment_generator.generate_manual_post", return_value=mock_captions) as mock_gen:
            resp = c.post(
                "/api/compose/generate",
                json={"notes": "Two hummers fighting at the feeder"},
                content_type="application/json",
            )
        assert resp.status_code == 200
        result = resp.get_json()
        assert result["ok"] is True
        assert result["captions"]["Facebook"] == mock_captions["Facebook"]
        assert result["captions"]["Twitter"] == mock_captions["Twitter"]
        mock_gen.assert_called_once()

    def test_generate_with_media_file(self, client):
        c, m, tmp_path = client
        # Create a fake uploaded file
        media = tmp_path / "manual_20260330_test.jpg"
        media.write_bytes(b"fake image")

        mock_captions = {"Facebook": "Look at this absolute unit.", "Twitter": "Unit.", "Bluesky": "Absolute unit."}
        with patch("social.comment_generator.generate_manual_post", return_value=mock_captions) as mock_gen:
            resp = c.post(
                "/api/compose/generate",
                json={"notes": "Check this out", "filename": media.name},
                content_type="application/json",
            )
        assert resp.status_code == 200
        result = resp.get_json()
        assert result["ok"] is True
        # Verify media_path was passed
        call_kwargs = mock_gen.call_args
        assert call_kwargs.kwargs.get("media_path") == media or call_kwargs[1].get("media_path") == media

    def test_generate_no_notes_no_file_returns_400(self, client):
        c, m, _ = client
        resp = c.post(
            "/api/compose/generate",
            json={"notes": "", "filename": None},
            content_type="application/json",
        )
        assert resp.status_code == 400
        result = resp.get_json()
        assert result["ok"] is False

    def test_generate_uses_active_platforms(self, client):
        c, m, _ = client
        m.poster_manager.platform_names = ["Facebook", "Instagram", "TikTok"]
        mock_captions = {"Facebook": "fb", "Instagram": "ig", "TikTok": "tt"}
        with patch("social.comment_generator.generate_manual_post", return_value=mock_captions) as mock_gen:
            resp = c.post(
                "/api/compose/generate",
                json={"notes": "testing platforms"},
                content_type="application/json",
            )
        assert resp.status_code == 200
        call_kwargs = mock_gen.call_args
        platforms_arg = call_kwargs.kwargs.get("platforms") or call_kwargs[1].get("platforms")
        assert "Instagram" in platforms_arg
        assert "TikTok" in platforms_arg

    def test_generate_handles_api_error(self, client):
        c, m, _ = client
        with patch("social.comment_generator.generate_manual_post", side_effect=RuntimeError("API down")):
            resp = c.post(
                "/api/compose/generate",
                json={"notes": "test"},
                content_type="application/json",
            )
        assert resp.status_code == 500
        result = resp.get_json()
        assert result["ok"] is False


class TestComposePost:
    """Test POST /api/compose/post endpoint."""

    def test_post_text_only(self, client):
        c, m, _ = client
        captions = {"Facebook": "Just vibes.", "Twitter": "Vibes.", "Bluesky": "Pure vibes."}
        resp = c.post(
            "/api/compose/post",
            json={"captions": captions},
            content_type="application/json",
        )
        assert resp.status_code == 200
        result = resp.get_json()
        assert result["ok"] is True
        assert result["results"]["Facebook"] is True
        m.poster_manager.post_text.assert_called_once_with(captions)

    def test_post_with_video(self, client):
        c, m, tmp_path = client
        video = tmp_path / "manual_test.mp4"
        video.write_bytes(b"fake video" * 100)

        captions = {"Facebook": "Video post!", "Twitter": "Video!"}
        resp = c.post(
            "/api/compose/post",
            json={"captions": captions, "filename": video.name},
            content_type="application/json",
        )
        assert resp.status_code == 200
        result = resp.get_json()
        assert result["ok"] is True
        m.poster_manager.post_video.assert_called_once()

    def test_post_with_photo(self, client):
        c, m, tmp_path = client
        photo = tmp_path / "manual_test.jpg"
        photo.write_bytes(b"fake photo")

        captions = {"Facebook": "Photo post!"}
        resp = c.post(
            "/api/compose/post",
            json={"captions": captions, "filename": photo.name},
            content_type="application/json",
        )
        assert resp.status_code == 200
        result = resp.get_json()
        assert result["ok"] is True
        m.poster_manager.post_photo.assert_called_once()

    def test_post_with_png(self, client):
        c, m, tmp_path = client
        photo = tmp_path / "manual_test.png"
        photo.write_bytes(b"fake png")

        captions = {"Facebook": "PNG post!"}
        resp = c.post(
            "/api/compose/post",
            json={"captions": captions, "filename": photo.name},
            content_type="application/json",
        )
        assert resp.status_code == 200
        m.poster_manager.post_photo.assert_called_once()

    def test_post_no_captions_returns_400(self, client):
        c, m, _ = client
        resp = c.post(
            "/api/compose/post",
            json={"captions": {}},
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_post_no_monitor_returns_503(self, client):
        import web.dashboard as dashboard
        c, m, _ = client
        old = dashboard._monitor
        dashboard._monitor = None
        try:
            resp = c.post(
                "/api/compose/post",
                json={"captions": {"Facebook": "test"}},
                content_type="application/json",
            )
            assert resp.status_code == 503
        finally:
            dashboard._monitor = old

    def test_post_partial_failure(self, client):
        c, m, _ = client
        m.poster_manager.post_text.return_value = {"Facebook": True, "Twitter": False, "Bluesky": True}
        captions = {"Facebook": "fb", "Twitter": "tw", "Bluesky": "bs"}
        resp = c.post(
            "/api/compose/post",
            json={"captions": captions},
            content_type="application/json",
        )
        assert resp.status_code == 200
        result = resp.get_json()
        assert result["ok"] is True
        assert result["results"]["Twitter"] is False
        assert result["results"]["Facebook"] is True

    def test_post_missing_media_falls_back_to_text(self, client):
        c, m, _ = client
        captions = {"Facebook": "text fallback"}
        resp = c.post(
            "/api/compose/post",
            json={"captions": captions, "filename": "nonexistent.mp4"},
            content_type="application/json",
        )
        assert resp.status_code == 200
        m.poster_manager.post_text.assert_called_once()

    def test_post_handles_exception(self, client):
        c, m, _ = client
        m.poster_manager.post_text.side_effect = RuntimeError("Network error")
        resp = c.post(
            "/api/compose/post",
            json={"captions": {"Facebook": "test"}},
            content_type="application/json",
        )
        assert resp.status_code == 500
        result = resp.get_json()
        assert result["ok"] is False


class TestGenerateManualPost:
    """Test generate_manual_post() in comment_generator.py."""

    def test_returns_fallback_without_api_key(self, monkeypatch):
        import config
        monkeypatch.setattr(config, "OPENAI_API_KEY", "")
        from social.comment_generator import generate_manual_post

        result = generate_manual_post("Two hummers fighting")
        assert "Facebook" in result
        assert "Two hummers fighting" in result["Facebook"]

    def test_returns_captions_for_all_platforms(self, monkeypatch):
        import config
        monkeypatch.setattr(config, "OPENAI_API_KEY", "sk-test")
        monkeypatch.setattr(config, "AZURE_OPENAI_ENDPOINT", "")
        monkeypatch.setattr(config, "AZURE_OPENAI_DEPLOYMENT", "")

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = (
            "---FACEBOOK---\nFB caption here\n"
            "---TWITTER---\nTW caption here\n"
            "---BLUESKY---\nBS caption here"
        )

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response

        from social.comment_generator import generate_manual_post
        with patch("social.comment_generator._get_client", return_value=mock_client):
            result = generate_manual_post(
                "Hummer showed up at dawn",
                platforms=["Facebook", "Twitter", "Bluesky"],
            )

        assert result["Facebook"] == "FB caption here"
        assert result["Twitter"] == "TW caption here"
        assert result["Bluesky"] == "BS caption here"

    def test_single_platform_returns_raw(self, monkeypatch):
        import config
        monkeypatch.setattr(config, "OPENAI_API_KEY", "sk-test")
        monkeypatch.setattr(config, "AZURE_OPENAI_ENDPOINT", "")
        monkeypatch.setattr(config, "AZURE_OPENAI_DEPLOYMENT", "")

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Just a single caption."

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response

        from social.comment_generator import generate_manual_post
        with patch("social.comment_generator._get_client", return_value=mock_client):
            result = generate_manual_post("notes", platforms=["Facebook"])

        assert result["Facebook"] == "Just a single caption."

    def test_handles_api_error_returns_fallback(self, monkeypatch):
        import config
        monkeypatch.setattr(config, "OPENAI_API_KEY", "sk-test")
        monkeypatch.setattr(config, "AZURE_OPENAI_ENDPOINT", "")
        monkeypatch.setattr(config, "AZURE_OPENAI_DEPLOYMENT", "")

        # openai is optional in dev; production code wraps its import the same way.
        openai_module = pytest.importorskip("openai")
        OpenAIError = openai_module.OpenAIError

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = OpenAIError("API down")

        from social.comment_generator import generate_manual_post
        with patch("social.comment_generator._get_client", return_value=mock_client):
            result = generate_manual_post(
                "test notes",
                platforms=["Facebook", "Twitter"],
            )

        # Should fall back to user notes
        assert result["Facebook"] == "test notes"
        assert result["Twitter"] == "test notes"

    def test_with_image_sends_vision_content(self, monkeypatch, tmp_path):
        import config
        monkeypatch.setattr(config, "OPENAI_API_KEY", "sk-test")
        monkeypatch.setattr(config, "AZURE_OPENAI_ENDPOINT", "")
        monkeypatch.setattr(config, "AZURE_OPENAI_DEPLOYMENT", "")
        monkeypatch.setattr(config, "VISION_CAPTION_ENABLED", True)
        monkeypatch.setattr(config, "VISION_CAPTION_DETAIL", "low")

        # Create a fake image
        img_path = tmp_path / "test.jpg"
        img_path.write_bytes(b"fake jpg data")

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Caption with vision."

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response

        from social.comment_generator import generate_manual_post
        with patch("social.comment_generator._get_client", return_value=mock_client), \
             patch("social.comment_generator._encode_frame", return_value="data:image/jpeg;base64,abc123"):
            result = generate_manual_post("bird at feeder", media_path=img_path)

        assert result["Facebook"] == "Caption with vision."
        # Verify multimodal content was sent
        call_args = mock_client.chat.completions.create.call_args
        messages = call_args.kwargs.get("messages") or call_args[1].get("messages")
        user_msg = messages[-1]
        assert isinstance(user_msg["content"], list)
        assert user_msg["content"][1]["type"] == "image_url"

    def test_defaults_to_facebook_platform(self, monkeypatch):
        import config
        monkeypatch.setattr(config, "OPENAI_API_KEY", "")
        from social.comment_generator import generate_manual_post

        result = generate_manual_post("test")
        assert "Facebook" in result
        assert len(result) == 1
