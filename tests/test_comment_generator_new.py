"""Tests for new comment_generator functions: milestone posts, weather params, bird personality."""

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _reset_client():
    """Reset singleton client and buffers before each test."""
    import social.comment_generator as cg
    orig_client = cg._client
    cg._client = None
    cg._recent_captions.clear()
    cg._recent_descriptions.clear()
    yield
    cg._client = orig_client
    cg._recent_captions.clear()
    cg._recent_descriptions.clear()


def _mock_api_response(text):
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = text
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = mock_response
    return mock_client


# ---------------------------------------------------------------------------
# generate_milestone_post
# ---------------------------------------------------------------------------
class TestGenerateMilestonePost:
    """Test generate_milestone_post() milestone caption generation."""

    def test_successful_generation(self, monkeypatch):
        import config
        from social.comment_generator import generate_milestone_post
        monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")

        mock_client = _mock_api_response("Visitor #1000! That's a lot of tiny tongues.")
        with patch("social.comment_generator._get_client", return_value=mock_client):
            result = generate_milestone_post(1000, today_count=5, days_running=90)

        assert result == "Visitor #1000! That's a lot of tiny tongues."
        mock_client.chat.completions.create.assert_called_once()

    def test_prompt_contains_milestone_count(self, monkeypatch):
        import config
        from social.comment_generator import generate_milestone_post
        monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")

        mock_client = _mock_api_response("Congrats!")
        with patch("social.comment_generator._get_client", return_value=mock_client):
            generate_milestone_post(500, today_count=3, days_running=60)

        call_args = mock_client.chat.completions.create.call_args
        messages = call_args.kwargs.get("messages") or call_args[1].get("messages")
        system_msg = messages[0]["content"]
        assert "500" in system_msg
        assert "60" in system_msg

    def test_fallback_when_no_api_key(self, monkeypatch):
        import config
        from social.comment_generator import generate_milestone_post
        monkeypatch.setattr(config, "OPENAI_API_KEY", "")
        result = generate_milestone_post(250)
        assert "250" in result

    def test_fallback_on_api_error(self, monkeypatch):
        import config
        from social.comment_generator import generate_milestone_post
        monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")

        try:
            from openai import OpenAIError
        except ImportError:
            OpenAIError = Exception

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = OpenAIError("fail")
        with patch("social.comment_generator._get_client", return_value=mock_client):
            result = generate_milestone_post(100)
        assert "100" in result


# ---------------------------------------------------------------------------
# Weather parameter integration
# ---------------------------------------------------------------------------
class TestWeatherInCaptions:
    """Test that weather data flows into caption prompts."""

    def test_weather_included_in_prompt(self, monkeypatch):
        import config
        from social.comment_generator import generate_comment
        monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")

        mock_client = _mock_api_response("Hot day at the feeder.")
        with patch("social.comment_generator._get_client", return_value=mock_client):
            generate_comment(
                detections=3, rejected=1,
                weather={"temp_f": 95.0, "condition": "Clear"},
            )

        call_args = mock_client.chat.completions.create.call_args
        messages = call_args.kwargs.get("messages") or call_args[1].get("messages")
        system_msg = messages[0]["content"]
        assert "95" in system_msg
        assert "Clear" in system_msg

    def test_no_weather_still_works(self, monkeypatch):
        import config
        from social.comment_generator import generate_comment
        monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")

        mock_client = _mock_api_response("Normal caption.")
        with patch("social.comment_generator._get_client", return_value=mock_client):
            result = generate_comment(detections=1, rejected=0)
        assert result == "Normal caption."

    def test_weather_in_morning_prompt(self, monkeypatch):
        import config
        from social.comment_generator import generate_good_morning
        monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")

        mock_client = _mock_api_response("Chilly morning!")
        with patch("social.comment_generator._get_client", return_value=mock_client):
            generate_good_morning(
                location="Bartlett, TN", sunrise="6:30 AM",
                weather={"temp_f": 45.0, "condition": "Fog"},
            )

        call_args = mock_client.chat.completions.create.call_args
        messages = call_args.kwargs.get("messages") or call_args[1].get("messages")
        system_msg = messages[0]["content"]
        assert "45" in system_msg
        assert "Fog" in system_msg


# ---------------------------------------------------------------------------
# Bird personality / return visitor tracking
# ---------------------------------------------------------------------------
class TestBirdPersonality:
    """Test bird description extraction and return-visitor storytelling."""

    def test_bird_description_extracted(self, monkeypatch):
        import config
        import social.comment_generator as cg
        from social.comment_generator import generate_comment
        monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")
        monkeypatch.setattr(config, "VISION_CAPTION_ENABLED", True)

        # Simulate GPT-4o response with BIRD: line
        mock_client = _mock_api_response(
            "Hovering with intent.\nBIRD: Male, bright magenta gorget, medium size, hovering at feeder"
        )
        with patch("social.comment_generator._get_client", return_value=mock_client), \
             patch("social.comment_generator._encode_frame", return_value="data:image/jpeg;base64,abc"):
            result = generate_comment(
                detections=1, rejected=0, frame_path="/tmp/frame.jpg",
            )

        # Caption should NOT include the BIRD: line
        assert "BIRD:" not in result
        assert result == "Hovering with intent."
        # Description should be stored
        assert len(cg._recent_descriptions) == 1
        assert "magenta gorget" in cg._recent_descriptions[0]

    def test_no_bird_line_no_description(self, monkeypatch):
        import config
        import social.comment_generator as cg
        from social.comment_generator import generate_comment
        monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")

        mock_client = _mock_api_response("Just a regular caption.")
        with patch("social.comment_generator._get_client", return_value=mock_client):
            result = generate_comment(detections=1, rejected=0)

        assert result == "Just a regular caption."
        assert len(cg._recent_descriptions) == 0

    def test_recent_descriptions_fed_into_prompt(self, monkeypatch):
        import config
        import social.comment_generator as cg
        from social.comment_generator import generate_comment
        monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")

        # Pre-populate descriptions
        cg._recent_descriptions.append("Male, bright red gorget, aggressive feeder")
        cg._recent_descriptions.append("Female, green back, no gorget")

        mock_client = _mock_api_response("Looks like our regular.")
        with patch("social.comment_generator._get_client", return_value=mock_client):
            generate_comment(detections=3, rejected=0)

        call_args = mock_client.chat.completions.create.call_args
        messages = call_args.kwargs.get("messages") or call_args[1].get("messages")
        system_msg = messages[0]["content"]
        assert "bright red gorget" in system_msg
        assert "returning visitor" in system_msg
