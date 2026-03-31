"""Tests for social/comment_responder.py — CommentResponder."""

from unittest.mock import MagicMock, patch

import pytest


def _make_responder():
    from social.comment_responder import CommentResponder
    mock_poster = MagicMock()
    mock_db = MagicMock()
    r = CommentResponder(mock_poster, mock_db)
    # Pre-create a mock session so tests can control it
    r._session = MagicMock()
    return r


class TestInit:
    def test_default_poll_interval(self):
        r = _make_responder()
        assert r._poll_interval == 300


class TestCheckAndReply:
    def test_returns_early_when_not_configured(self):
        r = _make_responder()
        r._poster.is_configured.return_value = False
        r._check_and_reply()
        r._db.get_replies_last_hour.assert_not_called()

    def test_returns_early_at_rate_limit(self):
        r = _make_responder()
        r._poster.is_configured.return_value = True
        r._db.get_replies_last_hour.return_value = 999
        with patch("config.AUTO_REPLY_MAX_PER_HOUR", 5):
            r._check_and_reply()

    def test_skips_already_replied(self):
        r = _make_responder()
        r._poster.is_configured.return_value = True
        r._db.get_replies_last_hour.return_value = 0
        r._db.has_replied_to.return_value = True

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "data": [{
                "id": "post1",
                "message": "Check this out",
                "comments": {"data": [{"id": "c1", "message": "Cool!", "from": {"id": "user1"}}]},
            }]
        }
        r._session.get.return_value = mock_resp

        with patch("config.AUTO_REPLY_MAX_PER_HOUR", 10), \
             patch("config.FACEBOOK_PAGE_ACCESS_TOKEN", "token"), \
             patch("config.FACEBOOK_PAGE_ID", "page1"):
            r._check_and_reply()
            r._db.has_replied_to.assert_called_with("c1")

    def test_skips_own_comments(self):
        r = _make_responder()
        r._poster.is_configured.return_value = True
        r._db.get_replies_last_hour.return_value = 0
        r._db.has_replied_to.return_value = False

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "data": [{
                "id": "post1",
                "message": "Check this out",
                "comments": {"data": [{"id": "c1", "message": "Our reply", "from": {"id": "page1"}}]},
            }]
        }
        r._session.get.return_value = mock_resp

        with patch("config.AUTO_REPLY_MAX_PER_HOUR", 10), \
             patch("config.FACEBOOK_PAGE_ACCESS_TOKEN", "token"), \
             patch("config.FACEBOOK_PAGE_ID", "page1"):
            r._check_and_reply()

    def test_handles_request_error(self):
        import requests as req
        r = _make_responder()
        r._poster.is_configured.return_value = True
        r._db.get_replies_last_hour.return_value = 0
        r._session.get.side_effect = req.RequestException("fail")

        with patch("config.AUTO_REPLY_MAX_PER_HOUR", 10), \
             patch("config.FACEBOOK_PAGE_ACCESS_TOKEN", "token"), \
             patch("config.FACEBOOK_PAGE_ID", "page1"):
            r._check_and_reply()  # should not raise


class TestGenerateReply:
    def test_returns_none_when_no_client(self):
        r = _make_responder()
        with patch("social.comment_generator._get_client", return_value=None):
            result = r._generate_reply("caption", "comment")
            assert result is None

    def test_returns_reply_text(self):
        r = _make_responder()
        mock_client = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = "Thanks for watching!"
        mock_client.chat.completions.create.return_value = MagicMock(choices=[mock_choice])

        with patch("social.comment_generator._get_client", return_value=mock_client), \
             patch("config.AZURE_OPENAI_DEPLOYMENT", ""):
            result = r._generate_reply("A hummingbird!", "So cool!")
            assert result == "Thanks for watching!"

    def test_returns_none_on_exception(self):
        r = _make_responder()
        with patch("social.comment_generator._get_client", side_effect=RuntimeError("fail")):
            result = r._generate_reply("caption", "comment")
            assert result is None


class TestPostReply:
    def test_success(self):
        r = _make_responder()
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        r._session.post.return_value = mock_resp
        with patch("config.FACEBOOK_PAGE_ACCESS_TOKEN", "token"):
            assert r._post_reply("c1", "Nice!") is True

    def test_failure(self):
        import requests as req
        r = _make_responder()
        r._session.post.side_effect = req.RequestException("fail")
        with patch("config.FACEBOOK_PAGE_ACCESS_TOKEN", "token"):
            assert r._post_reply("c1", "Nice!") is False
