"""Tests for analytics/behavior.py — behavior classification."""

from unittest.mock import MagicMock, patch

import pytest


class TestClassifyBehavior:
    def test_returns_none_when_no_api_key(self):
        with patch("config.OPENAI_API_KEY", ""):
            from analytics.behavior import classify_behavior
            assert classify_behavior("/some/path.jpg") is None

    def test_returns_none_when_no_frame_path(self):
        with patch("config.OPENAI_API_KEY", "sk-test"):
            from analytics.behavior import classify_behavior
            assert classify_behavior("") is None

    def test_returns_none_when_file_not_exists(self, tmp_path):
        with patch("config.OPENAI_API_KEY", "sk-test"):
            from analytics.behavior import classify_behavior
            assert classify_behavior(str(tmp_path / "missing.jpg")) is None

    def test_returns_none_when_no_client(self, tmp_path):
        img = tmp_path / "frame.jpg"
        img.write_bytes(b"\xff\xd8\xff\xe0fake")
        with patch("config.OPENAI_API_KEY", "sk-test"), \
             patch("social.comment_generator._get_client", return_value=None):
            from analytics.behavior import classify_behavior
            assert classify_behavior(str(img)) is None

    def test_returns_valid_behavior(self, tmp_path):
        img = tmp_path / "frame.jpg"
        img.write_bytes(b"\xff\xd8\xff\xe0fake")

        mock_client = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = "feeding"
        mock_client.chat.completions.create.return_value = MagicMock(choices=[mock_choice])

        with patch("config.OPENAI_API_KEY", "sk-test"), \
             patch("config.AZURE_OPENAI_DEPLOYMENT", ""), \
             patch("social.comment_generator._get_client", return_value=mock_client):
            from analytics.behavior import classify_behavior
            assert classify_behavior(str(img)) == "feeding"

    def test_returns_none_for_invalid_behavior(self, tmp_path):
        img = tmp_path / "frame.jpg"
        img.write_bytes(b"\xff\xd8\xff\xe0fake")

        mock_client = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = "flying around wildly"
        mock_client.chat.completions.create.return_value = MagicMock(choices=[mock_choice])

        with patch("config.OPENAI_API_KEY", "sk-test"), \
             patch("config.AZURE_OPENAI_DEPLOYMENT", ""), \
             patch("social.comment_generator._get_client", return_value=mock_client):
            from analytics.behavior import classify_behavior
            assert classify_behavior(str(img)) is None

    def test_returns_none_on_exception(self, tmp_path):
        img = tmp_path / "frame.jpg"
        img.write_bytes(b"\xff\xd8\xff\xe0fake")

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = RuntimeError("API down")

        with patch("config.OPENAI_API_KEY", "sk-test"), \
             patch("social.comment_generator._get_client", return_value=mock_client):
            from analytics.behavior import classify_behavior
            assert classify_behavior(str(img)) is None
