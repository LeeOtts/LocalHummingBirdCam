"""Tests for caption validation and retry logic in comment_generator."""

import types
from unittest.mock import MagicMock, patch

import pytest

from social.comment_generator import (
    _validate_caption,
    _validate_all_captions,
    generate_good_night,
)


# ── _validate_caption unit tests ──────────────────────────────────────────

class TestValidateCaption:
    def test_valid_short_caption(self):
        assert _validate_caption("5 hummers today. Feeder's quiet now. See you tomorrow.") is True

    def test_valid_longer_caption(self):
        assert _validate_caption(
            "What a day at the feeder! 12 visitors stopped by, with the busiest "
            "hour around 3 PM. The sugar water is running low."
        ) is True

    def test_empty_string(self):
        assert _validate_caption("") is False

    def test_too_short(self):
        assert _validate_caption("Hi") is False

    def test_too_long(self):
        assert _validate_caption("word " * 500) is False

    def test_high_newline_ratio(self):
        # Lots of newlines with isolated words — like the incident
        text = "G\nextended\nMad\nes\nlo\n75\nYum\n40\nCURRENT\nM\nSellers"
        assert _validate_caption(text) is False

    def test_low_avg_word_length(self):
        # All 1-2 character "words"
        text = "a b c d e f g h i j k l m n o p q r s t u v w x y z"
        assert _validate_caption(text) is False

    def test_stub_lines(self):
        # Every line has just 1-2 words
        text = "Get 65\nM\nSkip\nAm\nHotel\nAngles 99\nAnth\nMinute\nLove\nOr\nVarios"
        assert _validate_caption(text) is False

    def test_low_alpha_ratio(self):
        text = "47% --- 55 --- 40 --- 34 --- 12 --- 99 --- 65 --- 28 --- 59"
        assert _validate_caption(text) is False

    def test_actual_incident_gibberish(self):
        """The actual gibberish from the 2026-04-03 incident."""
        gibberish = (
            "G extended  \n\n\n- Mad\n\nes lo!\n\n\n75  \nYum  \n40  \nCURRENT \n"
            "M  Sellers  \nM  \n\n\nBut  \n  \nW  \n\n\n40  \n\n\n5  \n& view  \n"
            "United  \nP aceite\n\nIf  \nDo  \nNOYes \n\n\n\n keeping  \n-  \n"
        )
        assert _validate_caption(gibberish) is False

    def test_caption_with_emoji(self):
        assert _validate_caption("3 visitors today and the feeder is empty already! 🐦") is True

    def test_caption_with_url(self):
        assert _validate_caption(
            "Check out today's highlights at the feeder. Link in bio for the full video."
        ) is True

    def test_single_sentence_valid(self):
        assert _validate_caption("Zero hummers today but the feeder stays full.") is True


# ── _validate_all_captions tests ──────────────────────────────────────────

class TestValidateAllCaptions:
    def test_all_valid(self):
        captions = {
            "Facebook": "Great day at the feeder with 5 visitors.",
            "Twitter": "5 hummers stopped by today!",
        }
        assert _validate_all_captions(captions) is True

    def test_one_invalid(self):
        captions = {
            "Facebook": "Great day at the feeder with 5 visitors.",
            "Twitter": "Hi",  # too short
        }
        assert _validate_all_captions(captions) is False

    def test_all_invalid(self):
        captions = {
            "Facebook": "a\nb\nc\nd\ne\nf\ng\nh",
            "Twitter": "a\nb\nc\nd\ne\nf\ng\nh",
        }
        assert _validate_all_captions(captions) is False


# ── Retry logic integration tests ─────────────────────────────────────────

class TestRetryLogic:
    """Test that generators retry once on gibberish then fall back."""

    GIBBERISH = (
        "G extended\n- Mad\nes lo!\n75\nYum\n40\nCURRENT\nM Sellers\nM\n"
        "But\nW\n40\n5\n& view\nUnited\nP aceite"
    )
    VALID_MULTI = (
        "---FACEBOOK---\n5 hummers on camera today. Quiet evening ahead.\n"
        "---BLUESKY---\nFeeder's done for the day. 5 visitors total.\n"
        "---TWITTER---\n5 hummers. Calling it a night."
    )

    @pytest.fixture(autouse=True)
    def _patch_config(self, monkeypatch):
        """Ensure API key is set and client is mocked."""
        import config as cfg
        monkeypatch.setattr(cfg, "OPENAI_API_KEY", "test-key")
        monkeypatch.setattr(cfg, "AZURE_OPENAI_ENDPOINT", "https://fake.openai.azure.com/")
        monkeypatch.setattr(cfg, "AZURE_OPENAI_DEPLOYMENT", "gpt-4o")
        monkeypatch.setattr(cfg, "AZURE_OPENAI_API_VERSION", "2024-12-01-preview")

    def _make_response(self, text):
        msg = MagicMock()
        msg.content = text
        choice = MagicMock()
        choice.message = msg
        resp = MagicMock()
        resp.choices = [choice]
        return resp

    @patch("social.comment_generator._get_client")
    def test_retry_succeeds_on_second_attempt(self, mock_get_client):
        client = MagicMock()
        mock_get_client.return_value = client

        # First call returns gibberish, second returns valid
        client.chat.completions.create.side_effect = [
            self._make_response(self.GIBBERISH),
            self._make_response(self.VALID_MULTI),
        ]

        result = generate_good_night(
            location="Memphis, TN", sunset="7:22 PM",
            detections=5, rejected=0,
            platforms=["Facebook", "Bluesky", "Twitter"],
            lifetime_total=100,
        )

        assert client.chat.completions.create.call_count == 2
        assert "hummers" in result["Facebook"].lower() or "feeder" in result["Facebook"].lower()

    @patch("social.comment_generator._get_client")
    def test_falls_back_after_two_failures(self, mock_get_client):
        client = MagicMock()
        mock_get_client.return_value = client

        # Both calls return gibberish
        client.chat.completions.create.side_effect = [
            self._make_response(self.GIBBERISH),
            self._make_response(self.GIBBERISH),
        ]

        result = generate_good_night(
            location="Memphis, TN", sunset="7:22 PM",
            detections=5, rejected=0,
            platforms=["Facebook", "Bluesky", "Twitter"],
            lifetime_total=100,
        )

        assert client.chat.completions.create.call_count == 2
        # Should use the static fallback
        for platform in ["Facebook", "Bluesky", "Twitter"]:
            assert "5 hummer(s)" in result[platform]
            assert "See you tomorrow" in result[platform]

    @patch("social.comment_generator._get_client")
    def test_no_retry_when_valid(self, mock_get_client):
        client = MagicMock()
        mock_get_client.return_value = client

        client.chat.completions.create.return_value = self._make_response(self.VALID_MULTI)

        result = generate_good_night(
            location="Memphis, TN", sunset="7:22 PM",
            detections=5, rejected=0,
            platforms=["Facebook", "Bluesky", "Twitter"],
            lifetime_total=100,
        )

        # Only one API call — no retry needed
        assert client.chat.completions.create.call_count == 1
        assert "hummers" in result["Facebook"].lower() or "feeder" in result["Facebook"].lower()
