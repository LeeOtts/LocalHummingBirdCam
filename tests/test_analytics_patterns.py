"""Tests for analytics/patterns.py — feeding pattern analytics and prediction engine."""

from datetime import datetime
from unittest.mock import MagicMock, patch

import config

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from pytz import timezone as _pytz_tz
    ZoneInfo = lambda key: _pytz_tz(key)

_local_tz = ZoneInfo(config.LOCATION_TIMEZONE)


def _local_hour():
    """Return the current hour in the project's configured timezone."""
    return datetime.now(tz=_local_tz).hour

import pytest


@pytest.fixture(autouse=True)
def _reset_insight_cache():
    """Reset the AI insight cache between tests."""
    from analytics.patterns import _insight_cache
    orig = dict(_insight_cache)
    _insight_cache["text"] = None
    _insight_cache["expires"] = 0.0
    yield
    _insight_cache.update(orig)


def _mock_db(hourly=None, daily=None, avg_gap=None, total=0, today=0):
    """Create a mock sightings DB with configurable return values."""
    db = MagicMock()
    db.get_hourly_distribution.return_value = hourly or {}
    db.get_daily_totals.return_value = daily or []
    db.get_average_gap_minutes.return_value = avg_gap
    db.get_total_sightings.return_value = total
    db.get_today_count.return_value = today
    return db


# ---------------------------------------------------------------------------
# get_analytics_summary
# ---------------------------------------------------------------------------
class TestGetAnalyticsSummary:
    """Test get_analytics_summary() aggregation logic."""

    def test_returns_all_expected_keys(self):
        from analytics.patterns import get_analytics_summary
        db = _mock_db(total=42, today=5)
        result = get_analytics_summary(db)
        assert result["total_all_time"] == 42
        assert result["today_count"] == 5
        assert "hourly_distribution" in result
        assert "daily_totals" in result
        assert "prediction" in result

    def test_peak_hour_am(self):
        from analytics.patterns import get_analytics_summary
        db = _mock_db(hourly={9: 15, 14: 8}, total=100)
        result = get_analytics_summary(db)
        assert result["peak_hour"] == "9 AM"

    def test_peak_hour_pm(self):
        from analytics.patterns import get_analytics_summary
        db = _mock_db(hourly={9: 5, 14: 20}, total=100)
        result = get_analytics_summary(db)
        assert result["peak_hour"] == "2 PM"

    def test_peak_hour_noon(self):
        from analytics.patterns import get_analytics_summary
        db = _mock_db(hourly={12: 30}, total=100)
        result = get_analytics_summary(db)
        assert result["peak_hour"] == "12 PM"

    def test_peak_hour_midnight(self):
        from analytics.patterns import get_analytics_summary
        db = _mock_db(hourly={0: 10}, total=100)
        result = get_analytics_summary(db)
        assert result["peak_hour"] == "12 AM"

    def test_no_hourly_data(self):
        from analytics.patterns import get_analytics_summary
        db = _mock_db(hourly={}, total=100)
        result = get_analytics_summary(db)
        assert result["peak_hour"] is None

    def test_avg_gap_rounded(self):
        from analytics.patterns import get_analytics_summary
        db = _mock_db(avg_gap=12.567)
        result = get_analytics_summary(db)
        assert result["avg_gap_minutes"] == 12.6

    def test_avg_gap_none(self):
        from analytics.patterns import get_analytics_summary
        db = _mock_db(avg_gap=None)
        result = get_analytics_summary(db)
        assert result["avg_gap_minutes"] is None

    def test_busiest_day_of_week(self):
        from analytics.patterns import get_analytics_summary
        daily = [
            {"date": "2025-03-17", "total_detections": 5},   # Monday
            {"date": "2025-03-18", "total_detections": 3},   # Tuesday
            {"date": "2025-03-24", "total_detections": 8},   # Monday
        ]
        db = _mock_db(daily=daily)
        result = get_analytics_summary(db)
        assert result["busiest_day_of_week"] == "Monday"

    def test_busiest_day_none_when_no_daily(self):
        from analytics.patterns import get_analytics_summary
        db = _mock_db(daily=[])
        result = get_analytics_summary(db)
        assert result["busiest_day_of_week"] is None

    def test_invalid_date_in_daily_skipped(self):
        from analytics.patterns import get_analytics_summary
        daily = [
            {"date": "not-a-date", "total_detections": 5},
            {"date": "2025-03-18", "total_detections": 3},
        ]
        db = _mock_db(daily=daily)
        result = get_analytics_summary(db)
        assert result["busiest_day_of_week"] == "Tuesday"


# ---------------------------------------------------------------------------
# predict_next_visit
# ---------------------------------------------------------------------------
class TestPredictNextVisit:
    """Test predict_next_visit() prediction engine."""

    def test_returns_none_when_no_hourly_data(self):
        from analytics.patterns import predict_next_visit
        db = _mock_db(hourly={})
        assert predict_next_visit(db) is None

    def test_returns_none_when_current_hour_missing(self):
        from analytics.patterns import predict_next_visit
        other_hour = (_local_hour() + 6) % 24
        db = _mock_db(hourly={other_hour: 10}, avg_gap=15.0)
        assert predict_next_visit(db) is None

    def test_returns_none_when_fewer_than_3_visits(self):
        from analytics.patterns import predict_next_visit
        db = _mock_db(hourly={_local_hour(): 2}, avg_gap=15.0)
        assert predict_next_visit(db) is None

    def test_returns_none_when_no_avg_gap(self):
        from analytics.patterns import predict_next_visit
        db = _mock_db(hourly={_local_hour(): 10}, avg_gap=None)
        assert predict_next_visit(db) is None

    def test_returns_prediction_with_enough_data(self):
        from analytics.patterns import predict_next_visit
        db = _mock_db(hourly={_local_hour(): 15}, avg_gap=20.0)
        result = predict_next_visit(db)
        assert result is not None
        assert "estimate_minutes" in result
        assert 5 <= result["estimate_minutes"] <= 120
        assert result["confidence"] == "high"
        assert result["based_on_days"] == 14

    def test_medium_confidence(self):
        from analytics.patterns import predict_next_visit
        db = _mock_db(hourly={_local_hour(): 7}, avg_gap=20.0)
        result = predict_next_visit(db)
        assert result is not None
        assert result["confidence"] == "medium"

    def test_low_confidence(self):
        from analytics.patterns import predict_next_visit
        db = _mock_db(hourly={_local_hour(): 4}, avg_gap=20.0)
        result = predict_next_visit(db)
        assert result is not None
        assert result["confidence"] == "low"

    def test_estimate_clamped_minimum(self):
        from analytics.patterns import predict_next_visit
        # Very high activity → very small gap → clamped to 5
        db = _mock_db(hourly={_local_hour(): 500}, avg_gap=0.5)
        result = predict_next_visit(db)
        assert result is not None
        assert result["estimate_minutes"] >= 5

    def test_estimate_clamped_maximum(self):
        from analytics.patterns import predict_next_visit
        # Very low activity → large gap → clamped to 120
        db = _mock_db(hourly={_local_hour(): 3}, avg_gap=500.0)
        result = predict_next_visit(db)
        assert result is not None
        assert result["estimate_minutes"] <= 120


# ---------------------------------------------------------------------------
# generate_ai_insight
# ---------------------------------------------------------------------------
class TestGenerateAiInsight:
    """Test generate_ai_insight() with mocked OpenAI client."""

    def test_returns_none_when_no_api_key(self, monkeypatch):
        import config
        from analytics.patterns import generate_ai_insight
        monkeypatch.setattr(config, "OPENAI_API_KEY", "")
        result = generate_ai_insight({"total_all_time": 100, "today_count": 5})
        assert result is None

    def test_returns_none_when_no_data(self, monkeypatch):
        import config
        from analytics.patterns import generate_ai_insight
        monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")
        result = generate_ai_insight({"total_all_time": 0})
        assert result is None

    def test_returns_insight_from_api(self, monkeypatch):
        import config
        from analytics.patterns import generate_ai_insight
        monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Mornings are busiest."
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response

        with patch("analytics.patterns._get_client", mock_client, create=True), \
             patch("social.comment_generator._get_client", return_value=mock_client):
            result = generate_ai_insight({
                "total_all_time": 500, "today_count": 10,
                "peak_hour": "9 AM", "avg_gap_minutes": 15.0,
                "busiest_day_of_week": "Wednesday",
            })
        assert result == "Mornings are busiest."

    def test_returns_cached_result(self, monkeypatch):
        import time
        from analytics.patterns import generate_ai_insight, _insight_cache
        _insight_cache["text"] = "Cached insight"
        _insight_cache["expires"] = time.time() + 3600
        result = generate_ai_insight({"total_all_time": 100})
        assert result == "Cached insight"

    def test_returns_none_when_client_unavailable(self, monkeypatch):
        import config
        from analytics.patterns import generate_ai_insight
        monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")
        with patch("social.comment_generator._get_client", return_value=None):
            result = generate_ai_insight({"total_all_time": 100, "today_count": 5})
        assert result is None

    def test_returns_none_on_api_exception(self, monkeypatch):
        import config
        from analytics.patterns import generate_ai_insight
        monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = Exception("API down")
        with patch("social.comment_generator._get_client", return_value=mock_client):
            result = generate_ai_insight({
                "total_all_time": 100, "today_count": 5,
            })
        assert result is None


# ---------------------------------------------------------------------------
# get_weather
# ---------------------------------------------------------------------------
class TestGetWeather:
    """Test get_weather() OpenWeatherMap integration."""

    def test_returns_none_when_no_api_key(self, monkeypatch):
        import config
        from analytics.patterns import get_weather
        monkeypatch.setattr(config, "OPENWEATHERMAP_API_KEY", "")
        assert get_weather(35.0, -89.0) is None

    def test_returns_weather_data(self, monkeypatch):
        import config
        import requests
        from analytics.patterns import get_weather
        monkeypatch.setattr(config, "OPENWEATHERMAP_API_KEY", "test-key")

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "main": {"temp": 78.5, "humidity": 55},
            "weather": [{"main": "Clear", "description": "clear sky"}],
        }
        with patch.object(requests, "get", return_value=mock_resp):
            result = get_weather(35.0, -89.0)

        assert result["temp_f"] == 78.5
        assert result["condition"] == "Clear"
        assert result["humidity"] == 55

    def test_returns_none_on_request_failure(self, monkeypatch):
        import config
        import requests
        from analytics.patterns import get_weather
        monkeypatch.setattr(config, "OPENWEATHERMAP_API_KEY", "test-key")
        with patch.object(requests, "get", side_effect=Exception("timeout")):
            assert get_weather(35.0, -89.0) is None
