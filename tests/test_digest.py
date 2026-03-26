"""Tests for social/digest.py — weekly digest generation and thumbnail collage."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


class TestGenerateWeeklyDigest:
    """Test generate_weekly_digest() caption generation."""

    def test_returns_none_when_no_data(self):
        from social.digest import generate_weekly_digest
        db = MagicMock()
        db.get_weekly_summary.return_value = {"total": 0, "last_week_total": 0}
        assert generate_weekly_digest(db) is None

    def test_returns_caption_from_api(self, monkeypatch):
        import config
        from social.digest import generate_weekly_digest
        monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")

        db = MagicMock()
        db.get_weekly_summary.return_value = {
            "total": 42,
            "last_week_total": 35,
            "trend": "up",
            "busiest_day": {"date": "2025-03-20", "total_detections": 12},
            "best_caption": "Tongue out, no shame.",
        }

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "42 visits this week — up from 35!"
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response

        with patch("social.comment_generator._get_client", return_value=mock_client):
            result = generate_weekly_digest(db)

        assert isinstance(result, dict)
        assert result["Facebook"] == "42 visits this week — up from 35!"

    def test_fallback_when_no_api_key(self, monkeypatch):
        import config
        from social.digest import generate_weekly_digest
        monkeypatch.setattr(config, "OPENAI_API_KEY", "")

        db = MagicMock()
        db.get_weekly_summary.return_value = {
            "total": 10,
            "last_week_total": 15,
            "trend": "down",
            "busiest_day": None,
            "best_caption": None,
        }

        result = generate_weekly_digest(db)
        assert result is not None
        assert isinstance(result, dict)
        caption = result["Facebook"]
        assert "10" in caption
        assert "down" in caption

    def test_fallback_on_api_exception(self, monkeypatch):
        import config
        from social.digest import generate_weekly_digest
        monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")

        db = MagicMock()
        db.get_weekly_summary.return_value = {
            "total": 20,
            "last_week_total": 25,
            "trend": "down",
            "busiest_day": None,
            "best_caption": None,
        }

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = Exception("API error")

        with patch("social.comment_generator._get_client", return_value=mock_client):
            result = generate_weekly_digest(db)

        assert result is not None
        assert isinstance(result, dict)
        assert "20" in result["Facebook"]

    def test_no_busiest_day_handled(self, monkeypatch):
        import config
        from social.digest import generate_weekly_digest
        monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")

        db = MagicMock()
        db.get_weekly_summary.return_value = {
            "total": 5,
            "last_week_total": 0,
            "trend": "up",
            "busiest_day": None,
            "best_caption": None,
        }

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "First week on the job!"
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response

        with patch("social.comment_generator._get_client", return_value=mock_client):
            result = generate_weekly_digest(db)
        assert isinstance(result, dict)
        assert result["Facebook"] == "First week on the job!"


class TestCreateThumbnailCollage:
    """Test create_thumbnail_collage() image generation."""

    def test_returns_false_when_no_images(self, tmp_path):
        from social.digest import create_thumbnail_collage
        db = MagicMock()
        db.get_sightings.return_value = []
        output = tmp_path / "collage.jpg"
        assert create_thumbnail_collage(db, output) is False

    def test_returns_false_when_no_existing_frames(self, tmp_path):
        from social.digest import create_thumbnail_collage
        db = MagicMock()
        db.get_sightings.return_value = [
            {"frame_path": "/nonexistent/frame.jpg"},
        ]
        output = tmp_path / "collage.jpg"
        assert create_thumbnail_collage(db, output) is False

    def test_creates_collage_with_valid_frames(self, tmp_path):
        from social.digest import create_thumbnail_collage
        import cv2
        import numpy as np

        # Create test images
        frames = []
        for i in range(3):
            frame_path = tmp_path / f"frame_{i}.jpg"
            img = np.zeros((240, 320, 3), dtype=np.uint8)
            img[:] = (i * 50, i * 50, i * 50)
            cv2.imwrite(str(frame_path), img)
            frames.append({"frame_path": str(frame_path)})

        db = MagicMock()
        db.get_sightings.return_value = frames

        output = tmp_path / "collage.jpg"
        result = create_thumbnail_collage(db, output, grid_size=6)
        assert result is True
        assert output.exists()
