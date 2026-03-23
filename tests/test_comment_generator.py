"""Tests for social/comment_generator.py — caption generation with OpenAI.

Mocks the OpenAI client to avoid real API calls.
"""

from unittest.mock import MagicMock, patch

import pytest

from social.comment_generator import (
    FALLBACK_CAPTIONS,
    generate_comment,
    generate_good_morning,
    generate_good_night,
)


@pytest.fixture(autouse=True)
def _reset_client():
    """Reset the singleton _client before each test."""
    import social.comment_generator as cg
    orig = cg._client
    cg._client = None
    yield
    cg._client = orig


class TestNoApiKey:
    """When no API key is configured, fallback captions should be used."""

    def test_generate_comment_no_key_returns_fallback(self, monkeypatch):
        """generate_comment() returns a FALLBACK_CAPTIONS entry when API key is empty."""
        import config
        monkeypatch.setattr(config, "OPENAI_API_KEY", "")
        result = generate_comment(detections=1, rejected=0)
        assert result in FALLBACK_CAPTIONS

    def test_generate_good_morning_no_key(self, monkeypatch):
        """generate_good_morning() returns a default string when API key is empty."""
        import config
        monkeypatch.setattr(config, "OPENAI_API_KEY", "")
        result = generate_good_morning(location="Bartlett, TN", sunrise="6:30 AM")
        assert "6:30 AM" in result
        assert isinstance(result, str)

    def test_generate_good_night_no_key(self, monkeypatch):
        """generate_good_night() returns a default string when API key is empty."""
        import config
        monkeypatch.setattr(config, "OPENAI_API_KEY", "")
        result = generate_good_night(
            location="Bartlett, TN", sunset="7:45 PM",
            detections=5, rejected=2,
        )
        assert "5" in result
        assert isinstance(result, str)


class TestFallbackCaptions:
    """Verify the fallback captions list."""

    def test_fallback_list_is_nonempty(self):
        """FALLBACK_CAPTIONS should have at least one entry."""
        assert len(FALLBACK_CAPTIONS) > 0

    def test_all_fallbacks_are_strings(self):
        """Every fallback caption should be a non-empty string."""
        for caption in FALLBACK_CAPTIONS:
            assert isinstance(caption, str)
            assert len(caption) > 0


class TestGenerateCommentWithMock:
    """Test generate_comment() with a mocked OpenAI client."""

    def test_successful_generation(self, monkeypatch):
        """When OpenAI API succeeds, the generated caption is returned."""
        import config
        monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key-123")
        monkeypatch.setattr(config, "AZURE_OPENAI_ENDPOINT", "")

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Test caption from API"

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response

        with patch("social.comment_generator._get_client", return_value=mock_client):
            result = generate_comment(detections=3, rejected=1)

        assert result == "Test caption from API"
        mock_client.chat.completions.create.assert_called_once()

    def test_api_failure_returns_fallback(self, monkeypatch):
        """When OpenAI API raises an exception, a fallback caption is returned."""
        import config
        monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key-123")
        monkeypatch.setattr(config, "AZURE_OPENAI_ENDPOINT", "")

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = Exception("API Error")

        with patch("social.comment_generator._get_client", return_value=mock_client):
            result = generate_comment(detections=1, rejected=0)

        assert result in FALLBACK_CAPTIONS

    def test_accepts_detections_and_rejected_params(self, monkeypatch):
        """generate_comment() passes detections/rejected to the prompt."""
        import config
        monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key-123")
        monkeypatch.setattr(config, "AZURE_OPENAI_ENDPOINT", "")

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Visit 7 today"

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response

        with patch("social.comment_generator._get_client", return_value=mock_client):
            result = generate_comment(detections=7, rejected=3)

        # Verify the system prompt was formatted with stats
        call_args = mock_client.chat.completions.create.call_args
        messages = call_args.kwargs.get("messages") or call_args[1].get("messages")
        system_msg = messages[0]["content"]
        assert "7" in system_msg  # detections
        assert "3" in system_msg  # rejected


class TestGoodMorningGoodNight:
    """Test morning and night post generation."""

    def test_good_morning_prompt_contains_location(self, monkeypatch):
        """generate_good_morning() includes location in the prompt."""
        import config
        monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key-123")
        monkeypatch.setattr(config, "AZURE_OPENAI_ENDPOINT", "")

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Morning post"

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response

        with patch("social.comment_generator._get_client", return_value=mock_client):
            result = generate_good_morning(location="Memphis, TN", sunrise="6:15 AM")

        call_args = mock_client.chat.completions.create.call_args
        messages = call_args.kwargs.get("messages") or call_args[1].get("messages")
        system_msg = messages[0]["content"]
        assert "Memphis, TN" in system_msg
        assert "6:15 AM" in system_msg

    def test_good_night_prompt_contains_stats(self, monkeypatch):
        """generate_good_night() includes detection stats in the prompt."""
        import config
        monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key-123")
        monkeypatch.setattr(config, "AZURE_OPENAI_ENDPOINT", "")

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Goodnight post"

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response

        with patch("social.comment_generator._get_client", return_value=mock_client):
            result = generate_good_night(
                location="Bartlett, TN", sunset="7:30 PM",
                detections=12, rejected=4,
            )

        call_args = mock_client.chat.completions.create.call_args
        messages = call_args.kwargs.get("messages") or call_args[1].get("messages")
        system_msg = messages[0]["content"]
        assert "12" in system_msg
        assert "4" in system_msg
        assert "7:30 PM" in system_msg


class TestAzureVsDirectClient:
    """Test that Azure vs direct OpenAI client is selected correctly."""

    def test_azure_client_when_endpoint_set(self, monkeypatch):
        """When AZURE_OPENAI_ENDPOINT is set, AzureOpenAI client is created."""
        import config
        import social.comment_generator as cg

        monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")
        monkeypatch.setattr(config, "AZURE_OPENAI_ENDPOINT", "https://my-resource.openai.azure.com/")
        monkeypatch.setattr(config, "AZURE_OPENAI_API_VERSION", "2024-10-21")

        cg._client = None  # force re-creation

        mock_azure_cls = MagicMock()
        with patch.dict("sys.modules", {"openai": MagicMock(AzureOpenAI=mock_azure_cls)}):
            client = cg._get_client()

        mock_azure_cls.assert_called_once()
        assert client is not None

    def test_direct_client_when_no_endpoint(self, monkeypatch):
        """When AZURE_OPENAI_ENDPOINT is empty, direct OpenAI client is created."""
        import config
        import social.comment_generator as cg

        monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")
        monkeypatch.setattr(config, "AZURE_OPENAI_ENDPOINT", "")

        cg._client = None

        mock_openai_cls = MagicMock()
        with patch.dict("sys.modules", {"openai": MagicMock(OpenAI=mock_openai_cls)}):
            client = cg._get_client()

        mock_openai_cls.assert_called_once_with(api_key="test-key")
        assert client is not None

    def test_no_client_when_no_key(self, monkeypatch):
        """When OPENAI_API_KEY is empty, _get_client() returns None."""
        import config
        import social.comment_generator as cg

        monkeypatch.setattr(config, "OPENAI_API_KEY", "")
        cg._client = None

        client = cg._get_client()
        assert client is None
