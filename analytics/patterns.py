"""Feeding pattern analytics and prediction engine for Backyard Hummers."""

import logging
from datetime import datetime, timedelta

import config

logger = logging.getLogger(__name__)

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from pytz import timezone as _pytz_tz
    ZoneInfo = lambda key: _pytz_tz(key)

_local_tz = ZoneInfo(config.LOCATION_TIMEZONE)


def _enrich_season_dates(seasons: list[dict]) -> list[dict]:
    """Add formatted display dates and season length to season records."""
    enriched = []
    for s in seasons:
        row = dict(s)
        if s["first_visit"]:
            dt = datetime.strptime(s["first_visit"], "%Y-%m-%d")
            row["first_display"] = f"{dt.strftime('%B')} {dt.day}, {dt.year}"
        else:
            row["first_display"] = None
        if s["last_visit"]:
            dt = datetime.strptime(s["last_visit"], "%Y-%m-%d")
            row["last_display"] = f"{dt.strftime('%B')} {dt.day}, {dt.year}"
        else:
            row["last_display"] = None
        if s["first_visit"] and s["last_visit"]:
            f = datetime.strptime(s["first_visit"], "%Y-%m-%d")
            l = datetime.strptime(s["last_visit"], "%Y-%m-%d")
            row["season_length"] = (l - f).days
        else:
            row["season_length"] = None
        enriched.append(row)
    return enriched


def get_analytics_summary(db) -> dict:
    """Get a comprehensive analytics summary for the dashboard."""
    now = datetime.now(tz=_local_tz)

    hourly = db.get_hourly_distribution(days=14)
    daily = db.get_daily_totals(days=30)
    avg_gap = db.get_average_gap_minutes(days=14)
    total = db.get_total_sightings()
    today_count = db.get_today_count()

    # Peak hour
    peak_hour = max(hourly, key=hourly.get) if hourly else None
    peak_hour_label = None
    if peak_hour is not None:
        h = peak_hour % 12 or 12
        ampm = "AM" if peak_hour < 12 else "PM"
        peak_hour_label = f"{h} {ampm}"

    # Busiest day of week from daily stats
    day_totals: dict[str, int] = {}
    for d in daily:
        try:
            dt = datetime.strptime(d["date"], "%Y-%m-%d")
            dow = dt.strftime("%A")
            day_totals[dow] = day_totals.get(dow, 0) + d["total_detections"]
        except (ValueError, TypeError):
            pass
    busiest_dow = max(day_totals, key=day_totals.get) if day_totals else None

    return {
        "total_all_time": total,
        "today_count": today_count,
        "hourly_distribution": hourly,
        "daily_totals": daily,
        "avg_gap_minutes": round(avg_gap, 1) if avg_gap else None,
        "peak_hour": peak_hour_label,
        "busiest_day_of_week": busiest_dow,
        "prediction": predict_next_visit(db),
        "season_dates": _enrich_season_dates(db.get_season_dates()),
        "season_prediction": predict_season_arrival(db),
        "end_season_prediction": predict_season_end(db),
    }


def predict_next_visit(db) -> dict | None:
    """Predict when the next hummingbird visit might happen.

    Uses historical inter-arrival times for the current hour window.
    Returns a dict with 'estimate_minutes' and 'confidence' or None
    if insufficient data.
    """
    now = datetime.now(tz=_local_tz)
    current_hour = now.hour

    hourly = db.get_hourly_distribution(days=14)
    if not hourly or current_hour not in hourly:
        return None

    visits_this_hour = hourly.get(current_hour, 0)
    if visits_this_hour < 3:
        return None  # Not enough data for this time slot

    # Average gap for this time window
    avg_gap = db.get_average_gap_minutes(days=14)
    if not avg_gap:
        return None

    # Adjust based on how active this particular hour is relative to average
    total_visits = sum(hourly.values())
    hours_with_data = len(hourly)
    avg_per_hour = total_visits / hours_with_data if hours_with_data else 0

    if avg_per_hour > 0:
        hour_factor = visits_this_hour / (14 * avg_per_hour)  # normalized over 14 days
        adjusted_gap = avg_gap / max(hour_factor, 0.1)
    else:
        adjusted_gap = avg_gap

    # Clamp to reasonable range
    adjusted_gap = max(5, min(adjusted_gap, 120))

    return {
        "estimate_minutes": round(adjusted_gap),
        "confidence": "high" if visits_this_hour > 10 else "medium" if visits_this_hour > 5 else "low",
        "based_on_days": 14,
        "visits_this_hour_slot": visits_this_hour,
    }


def predict_season_arrival(db) -> dict | None:
    """Predict when hummingbirds will arrive based on historical first-visit dates.

    Returns a dict with predicted arrival date, range, countdown, and season stats.
    """
    from statistics import mean
    now = datetime.now(tz=_local_tz)
    seasons = db.get_season_dates()
    if not seasons:
        return None

    # Collect day-of-year for each year with a first_visit
    first_doys = []
    season_lengths = []
    for s in seasons:
        if s["first_visit"]:
            dt = datetime.strptime(s["first_visit"], "%Y-%m-%d")
            first_doys.append(dt.timetuple().tm_yday)
        if s["first_visit"] and s["last_visit"]:
            f = datetime.strptime(s["first_visit"], "%Y-%m-%d")
            l = datetime.strptime(s["last_visit"], "%Y-%m-%d")
            season_lengths.append((l - f).days)

    if not first_doys:
        return None

    mean_doy = round(mean(first_doys))
    earliest_doy = min(first_doys)
    latest_doy = max(first_doys)

    # Determine the target year for prediction
    target_year = now.year
    predicted = datetime(target_year, 1, 1) + timedelta(days=mean_doy - 1)
    earliest = datetime(target_year, 1, 1) + timedelta(days=earliest_doy - 1)
    latest = datetime(target_year, 1, 1) + timedelta(days=latest_doy - 1)

    # If we're past the latest arrival and past last-visit season, use next year
    if now.date() > latest.date() + timedelta(days=30):
        target_year = now.year + 1
        predicted = datetime(target_year, 1, 1) + timedelta(days=mean_doy - 1)
        earliest = datetime(target_year, 1, 1) + timedelta(days=earliest_doy - 1)
        latest = datetime(target_year, 1, 1) + timedelta(days=latest_doy - 1)

    days_until = (predicted.date() - now.date()).days
    avg_season_length = round(mean(season_lengths)) if season_lengths else None

    # Determine if we're currently in season
    current_year_season = next((s for s in seasons if s["year"] == now.year), None)
    in_season = False
    if current_year_season and current_year_season["first_visit"]:
        first_dt = datetime.strptime(current_year_season["first_visit"], "%Y-%m-%d").date()
        last_dt = None
        if current_year_season["last_visit"]:
            last_dt = datetime.strptime(current_year_season["last_visit"], "%Y-%m-%d").date()
        if now.date() >= first_dt and (last_dt is None or now.date() <= last_dt):
            in_season = True

    return {
        "predicted_date": predicted.strftime("%Y-%m-%d"),
        "predicted_display": f"{predicted.strftime('%B')} {predicted.day}",
        "earliest_date": earliest.strftime("%Y-%m-%d"),
        "earliest_display": f"{earliest.strftime('%B')} {earliest.day}",
        "latest_date": latest.strftime("%Y-%m-%d"),
        "latest_display": f"{latest.strftime('%B')} {latest.day}",
        "based_on_years": len(first_doys),
        "days_until": days_until,
        "avg_season_length_days": avg_season_length,
        "in_season": in_season,
    }


def predict_season_end(db) -> dict | None:
    """Predict when the last hummingbird visit of the season will be.

    Uses historical last-visit dates to compute average, earliest, and latest.
    Returns days_until_last for end-of-season awareness in prompts.
    """
    from statistics import mean
    now = datetime.now(tz=_local_tz)
    seasons = db.get_season_dates()
    if not seasons:
        return None

    last_doys = []
    for s in seasons:
        if s["last_visit"]:
            dt = datetime.strptime(s["last_visit"], "%Y-%m-%d")
            last_doys.append(dt.timetuple().tm_yday)

    if not last_doys:
        return None

    mean_doy = round(mean(last_doys))
    earliest_doy = min(last_doys)
    latest_doy = max(last_doys)

    target_year = now.year
    predicted = datetime(target_year, 1, 1) + timedelta(days=mean_doy - 1)
    earliest = datetime(target_year, 1, 1) + timedelta(days=earliest_doy - 1)
    latest = datetime(target_year, 1, 1) + timedelta(days=latest_doy - 1)

    days_until_last = (predicted.date() - now.date()).days

    return {
        "predicted_last_date": predicted.strftime("%Y-%m-%d"),
        "predicted_last_display": f"{predicted.strftime('%B')} {predicted.day}",
        "earliest_last_date": earliest.strftime("%Y-%m-%d"),
        "earliest_last_display": f"{earliest.strftime('%B')} {earliest.day}",
        "latest_last_date": latest.strftime("%Y-%m-%d"),
        "latest_last_display": f"{latest.strftime('%B')} {latest.day}",
        "based_on_years": len(last_doys),
        "days_until_last": days_until_last,
    }


_insight_cache: dict = {"text": None, "expires": 0.0}


def generate_ai_insight(summary: dict) -> str | None:
    """Generate a short AI narrative from analytics data. Cached for 1 hour."""
    import time as _time

    if _insight_cache["text"] and _time.time() < _insight_cache["expires"]:
        return _insight_cache["text"]

    if not config.OPENAI_API_KEY:
        return None

    if not summary.get("total_all_time"):
        return None

    try:
        from social.comment_generator import _get_client

        client = _get_client()
        if not client:
            return None

        stats_text = (
            f"All-time sightings: {summary['total_all_time']}\n"
            f"Today: {summary['today_count']}\n"
            f"Peak hour: {summary.get('peak_hour', 'N/A')}\n"
            f"Average gap between visits: {summary.get('avg_gap_minutes', 'N/A')} minutes\n"
            f"Busiest day of week: {summary.get('busiest_day_of_week', 'N/A')}\n"
        )

        response = client.chat.completions.create(
            model=config.AZURE_OPENAI_DEPLOYMENT or "gpt-4o",
            messages=[
                {"role": "system", "content":
                    "You analyze hummingbird feeding patterns for the 'Backyard Hummers' camera. "
                    "Write 1-2 sentences of interesting, specific observations based on the stats. "
                    "Be insightful and conversational — like a naturalist sharing a fun finding. "
                    "Do NOT mention AI or automation. No hashtags. No emojis."},
                {"role": "user", "content": f"Here are the current feeding stats:\n{stats_text}"},
            ],
            max_tokens=100,
            temperature=0.8,
        )
        insight = (response.choices[0].message.content or "").strip()
        _insight_cache["text"] = insight
        _insight_cache["expires"] = _time.time() + 3600  # 1 hour
        logger.info("AI insight: %s", insight)
        return insight

    except Exception:
        logger.debug("Failed to generate AI insight")
        return None


def get_weather(lat: float, lng: float) -> dict | None:
    """Fetch current weather from OpenWeatherMap (free tier)."""
    if not config.OPENWEATHERMAP_API_KEY:
        return None

    try:
        import requests
        resp = requests.get(
            "https://api.openweathermap.org/data/2.5/weather",
            params={
                "lat": lat,
                "lon": lng,
                "appid": config.OPENWEATHERMAP_API_KEY,
                "units": "imperial",
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        return {
            "temp_f": data["main"]["temp"],
            "condition": data["weather"][0]["main"] if data.get("weather") else "Unknown",
            "description": data["weather"][0]["description"] if data.get("weather") else "",
            "humidity": data["main"].get("humidity"),
        }
    except Exception:
        logger.debug("Weather fetch failed")
        return None
