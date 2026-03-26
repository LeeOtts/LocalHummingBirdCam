"""Tests for social/poster_manager.py — multi-platform fan-out posting."""

from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from social.base_poster import SocialPoster


class FakePoster(SocialPoster):
    """A fake poster for testing."""

    def __init__(self, name="FakeBook", configured=True):
        self._name = name
        self._configured = configured

    @property
    def platform_name(self) -> str:
        return self._name

    def is_configured(self) -> bool:
        return self._configured

    def post_video(self, mp4_path, caption):
        return True

    def post_photo(self, image_path, caption):
        return True

    def post_text(self, message):
        return True


class FailPoster(FakePoster):
    """A poster that always fails."""

    def __init__(self, name="FailGram"):
        super().__init__(name)

    def post_video(self, mp4_path, caption):
        return False

    def post_photo(self, image_path, caption):
        return False

    def post_text(self, message):
        return False


class ExplodePoster(FakePoster):
    """A poster that raises exceptions."""

    def __init__(self, name="ExplodeBook"):
        super().__init__(name)

    def post_video(self, mp4_path, caption):
        raise RuntimeError("kaboom")

    def post_photo(self, image_path, caption):
        raise RuntimeError("kaboom")

    def post_text(self, message):
        raise RuntimeError("kaboom")


def _make_manager(posters):
    """Create a PosterManager with injected posters, bypassing discovery."""
    from social.poster_manager import PosterManager
    with patch.object(PosterManager, "_discover_platforms"):
        mgr = PosterManager()
    mgr._posters = posters
    return mgr


class TestPosterManagerProperties:
    """Test PosterManager properties and get_poster."""

    def test_platform_names(self):
        mgr = _make_manager([FakePoster("A"), FakePoster("B")])
        assert mgr.platform_names == ["A", "B"]

    def test_posters_returns_copy(self):
        posters = [FakePoster("A")]
        mgr = _make_manager(posters)
        result = mgr.posters
        assert result == posters
        assert result is not mgr._posters  # copy, not same list

    def test_get_poster_found(self):
        fb = FakePoster("Facebook")
        mgr = _make_manager([fb, FakePoster("Bluesky")])
        assert mgr.get_poster("facebook") is fb

    def test_get_poster_not_found(self):
        mgr = _make_manager([FakePoster("Facebook")])
        assert mgr.get_poster("Twitter") is None

    def test_empty_manager(self):
        mgr = _make_manager([])
        assert mgr.platform_names == []
        assert mgr.get_poster("anything") is None


class TestPostVideo:
    """Test PosterManager.post_video() fan-out behavior."""

    def test_all_succeed(self, tmp_path):
        mgr = _make_manager([FakePoster("A"), FakePoster("B")])
        results = mgr.post_video(tmp_path / "clip.mp4", "caption")
        assert results == {"A": True, "B": True}

    def test_mixed_results(self, tmp_path):
        mgr = _make_manager([FakePoster("Good"), FailPoster("Bad")])
        results = mgr.post_video(tmp_path / "clip.mp4", "caption")
        assert results == {"Good": True, "Bad": False}

    def test_exception_caught(self, tmp_path):
        mgr = _make_manager([ExplodePoster("Boom"), FakePoster("OK")])
        results = mgr.post_video(tmp_path / "clip.mp4", "caption")
        assert results["Boom"] is False
        assert results["OK"] is True

    def test_empty_manager_returns_empty(self, tmp_path):
        mgr = _make_manager([])
        results = mgr.post_video(tmp_path / "clip.mp4", "caption")
        assert results == {}


class TestPostPhoto:
    """Test PosterManager.post_photo() fan-out behavior."""

    def test_all_succeed(self, tmp_path):
        mgr = _make_manager([FakePoster("A"), FakePoster("B")])
        results = mgr.post_photo(tmp_path / "photo.jpg", "caption")
        assert results == {"A": True, "B": True}

    def test_exception_caught(self, tmp_path):
        mgr = _make_manager([ExplodePoster("Boom")])
        results = mgr.post_photo(tmp_path / "photo.jpg", "caption")
        assert results["Boom"] is False


class TestPostText:
    """Test PosterManager.post_text() fan-out behavior."""

    def test_all_succeed(self):
        mgr = _make_manager([FakePoster("A"), FakePoster("B")])
        results = mgr.post_text("Hello world")
        assert results == {"A": True, "B": True}

    def test_failure_isolated(self):
        mgr = _make_manager([FailPoster("Bad"), FakePoster("Good")])
        results = mgr.post_text("Hello")
        assert results == {"Bad": False, "Good": True}

    def test_exception_caught(self):
        mgr = _make_manager([ExplodePoster("Boom")])
        results = mgr.post_text("Hello")
        assert results["Boom"] is False


class TestPerPlatformCaptions:
    """Test PosterManager with per-platform caption dicts."""

    def test_dict_caption_routes_correctly(self, tmp_path):
        """Each poster gets its own caption from the dict."""
        poster_a = FakePoster("A")
        poster_b = FakePoster("B")
        # Track what caption each poster receives
        received = {}
        orig_a = poster_a.post_video
        orig_b = poster_b.post_video
        poster_a.post_video = lambda path, cap: (received.update({"A": cap}), True)[1]
        poster_b.post_video = lambda path, cap: (received.update({"B": cap}), True)[1]

        mgr = _make_manager([poster_a, poster_b])
        captions = {"A": "Caption for A", "B": "Caption for B"}
        results = mgr.post_video(tmp_path / "clip.mp4", captions)

        assert results == {"A": True, "B": True}
        assert received["A"] == "Caption for A"
        assert received["B"] == "Caption for B"

    def test_dict_caption_falls_back_for_unknown_platform(self, tmp_path):
        """If platform not in dict, first dict value is used as fallback."""
        poster = FakePoster("Unknown")
        received = {}
        poster.post_text = lambda msg: (received.update({"text": msg}), True)[1]

        mgr = _make_manager([poster])
        captions = {"Facebook": "FB caption", "Twitter": "TW caption"}
        mgr.post_text(captions)

        assert received["text"] == "FB caption"  # first value as fallback

    def test_string_caption_still_works(self, tmp_path):
        """Backward compat: passing a plain string still works."""
        mgr = _make_manager([FakePoster("A")])
        results = mgr.post_video(tmp_path / "clip.mp4", "plain string")
        assert results == {"A": True}


class TestDiscoverPlatforms:
    """Test _discover_platforms() auto-discovery logic."""

    def test_discovers_configured_facebook(self, monkeypatch):
        import config
        monkeypatch.setattr(config, "FACEBOOK_PAGE_ID", "test-id")
        monkeypatch.setattr(config, "FACEBOOK_PAGE_ACCESS_TOKEN", "test-token")
        monkeypatch.setattr(config, "BLUESKY_HANDLE", "")
        monkeypatch.setattr(config, "BLUESKY_APP_PASSWORD", "")
        monkeypatch.setattr(config, "TWITTER_API_KEY", "")

        from social.poster_manager import PosterManager
        mgr = PosterManager()
        assert "Facebook" in mgr.platform_names

    def test_skips_unconfigured_platforms(self, monkeypatch):
        import config
        monkeypatch.setattr(config, "FACEBOOK_PAGE_ID", "")
        monkeypatch.setattr(config, "FACEBOOK_PAGE_ACCESS_TOKEN", "")
        monkeypatch.setattr(config, "BLUESKY_HANDLE", "")
        monkeypatch.setattr(config, "BLUESKY_APP_PASSWORD", "")
        monkeypatch.setattr(config, "TWITTER_API_KEY", "")
        monkeypatch.setattr(config, "TWITTER_API_SECRET", "")
        monkeypatch.setattr(config, "TWITTER_ACCESS_TOKEN", "")
        monkeypatch.setattr(config, "TWITTER_ACCESS_SECRET", "")

        from social.poster_manager import PosterManager
        mgr = PosterManager()
        assert mgr.platform_names == []
