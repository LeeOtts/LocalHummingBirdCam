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
        # New analytics
        "behavior_breakdown": db.get_behavior_breakdown(days=30),
        "species_breakdown": db.get_species_breakdown(days=30),
        "visit_stats": db.get_visit_stats(days=30),
        "position_heatmap": db.get_heatmap(days=30),
        "weather_correlations": get_weather_correlations(db, days=30),
        "sprinkler_effect": get_sprinkler_correlation(db, days=30),
        "activity_streaks": get_activity_streaks(db),
        "yoy_comparison": get_yoy_comparison(db),
        "monthly_totals": get_monthly_totals(db),
        "quiet_periods": get_quiet_periods(db),
        "prediction_accuracy": db.get_prediction_accuracy(days=30),
        "feeder_stats": db.get_feeder_stats(days=30),
        "sunrise_offset_avg_min": db.get_sunrise_offset_avg(days=30),
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

    # Determine if we're currently in season.
    # Requires BOTH a current-year season record AND recent actual detections.
    # A season record alone isn't enough — someone may have entered a
    # first_visit date speculatively, or the season may have ended without
    # the last_visit being recorded yet.
    current_year_season = next((s for s in seasons if s["year"] == now.year), None)
    in_season = False
    if current_year_season and current_year_season["first_visit"]:
        first_dt = datetime.strptime(current_year_season["first_visit"], "%Y-%m-%d").date()
        if current_year_season["last_visit"]:
            last_dt = datetime.strptime(current_year_season["last_visit"], "%Y-%m-%d").date()
            in_season = first_dt <= now.date() <= last_dt
        elif now.date() >= first_dt:
            # No last_visit yet — only mark in-season if there have been
            # actual detections recently (at least 1 in the last 14 days)
            recent = db.get_sightings(days=14, limit=1)
            in_season = len(recent) > 0

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
            "pressure": data["main"].get("pressure"),
            "wind_speed": data.get("wind", {}).get("speed"),
            "clouds": data.get("clouds", {}).get("all"),
        }
    except Exception:
        logger.debug("Weather fetch failed")
        return None


def get_moon_phase() -> float:
    """Get current moon phase as 0.0 to 1.0 (0=new, 0.5=full, 1=new).

    Uses the astral library already installed for sunrise/sunset.
    """
    try:
        from astral import moon
        # astral moon.phase() returns 0-27.99 where 0=new, 14=full
        phase = moon.phase(datetime.now(tz=_local_tz).date())
        return round(phase / 27.99, 3)
    except Exception:
        logger.debug("Moon phase calculation failed")
        return 0.0


def get_sunrise_offset_minutes() -> int | None:
    """Get minutes since sunrise for the current time.

    Returns negative if before sunrise, positive if after.
    """
    try:
        from schedule import _get_sun_times
        from astral import LocationInfo
        from astral.sun import sun

        try:
            import pytz
            tz = pytz.timezone(config.LOCATION_TIMEZONE)
        except ImportError:
            from zoneinfo import ZoneInfo
            tz = ZoneInfo(config.LOCATION_TIMEZONE)

        location = LocationInfo(
            name=config.LOCATION_NAME,
            region="USA",
            timezone=config.LOCATION_TIMEZONE,
            latitude=config.LOCATION_LAT,
            longitude=config.LOCATION_LNG,
        )
        s = sun(location.observer, date=datetime.now(tz=_local_tz).date(), tzinfo=tz)
        now = datetime.now(tz=tz)
        offset = (now - s["sunrise"]).total_seconds() / 60
        return round(offset)
    except Exception:
        logger.debug("Sunrise offset calculation failed")
        return None


# ------------------------------------------------------------------
# Weather-activity correlations
# ------------------------------------------------------------------

def get_weather_correlations(db, days: int = 30) -> dict[str, float | None]:
    """Compute Pearson correlation between weather variables and daily sighting counts.

    Returns dict like {"temperature": 0.72, "humidity": -0.31, ...}.
    """
    from statistics import correlation
    since = (datetime.now(tz=_local_tz) - timedelta(days=days)).isoformat()

    try:
        with db._lock:
            conn = db._get_conn()
            try:
                rows = conn.execute(
                    """SELECT DATE(timestamp) as dt,
                       AVG(weather_temp) as avg_temp,
                       AVG(weather_humidity) as avg_humidity,
                       AVG(weather_pressure) as avg_pressure,
                       AVG(weather_wind_speed) as avg_wind,
                       AVG(weather_clouds) as avg_clouds,
                       COUNT(*) as sighting_count
                       FROM sightings
                       WHERE timestamp >= ?
                       AND weather_temp IS NOT NULL
                       GROUP BY DATE(timestamp)
                       HAVING sighting_count >= 1""",
                    (since,),
                ).fetchall()
            finally:
                conn.close()

        if len(rows) < 5:
            return {}

        counts = [r["sighting_count"] for r in rows]
        result = {}
        for field, key in [("avg_temp", "temperature"), ("avg_humidity", "humidity"),
                           ("avg_pressure", "pressure"), ("avg_wind", "wind_speed"),
                           ("avg_clouds", "clouds")]:
            values = [r[field] for r in rows]
            if all(v is not None for v in values) and len(set(values)) > 1:
                try:
                    result[key] = round(correlation(values, counts), 2)
                except Exception:
                    result[key] = None
            else:
                result[key] = None

        return result
    except Exception:
        logger.debug("Weather correlation calculation failed")
        return {}


def get_sprinkler_correlation(db, days: int = 30) -> dict:
    """Compare sighting activity before vs after watering events."""
    events = db.get_watering_events(days=days)
    if not events:
        return {"events": 0, "avg_before": 0, "avg_after": 0, "change_pct": 0}

    completed = [e for e in events if e.get("sightings_before_30min") is not None
                 and e.get("sightings_after_30min") is not None]
    if not completed:
        return {"events": len(events), "avg_before": 0, "avg_after": 0, "change_pct": 0}

    avg_before = sum(e["sightings_before_30min"] for e in completed) / len(completed)
    avg_after = sum(e["sightings_after_30min"] for e in completed) / len(completed)
    change_pct = round(((avg_after - avg_before) / max(avg_before, 0.1)) * 100, 1)

    return {
        "events": len(completed),
        "avg_before": round(avg_before, 1),
        "avg_after": round(avg_after, 1),
        "change_pct": change_pct,
    }


# ------------------------------------------------------------------
# Advanced analytics
# ------------------------------------------------------------------

def get_activity_streaks(db) -> dict:
    """Calculate current and longest consecutive-day activity streaks."""
    with db._lock:
        conn = db._get_conn()
        try:
            rows = conn.execute(
                "SELECT DISTINCT DATE(timestamp) as dt FROM sightings ORDER BY dt"
            ).fetchall()
        finally:
            conn.close()

    if not rows:
        return {"current_streak": 0, "longest_streak": 0,
                "longest_start": None, "longest_end": None}

    from datetime import date as _date
    dates = []
    for r in rows:
        try:
            dates.append(datetime.strptime(r["dt"], "%Y-%m-%d").date())
        except (ValueError, TypeError):
            pass

    if not dates:
        return {"current_streak": 0, "longest_streak": 0,
                "longest_start": None, "longest_end": None}

    dates.sort()

    # Find streaks
    longest = 1
    longest_start = longest_end = dates[0]
    current = 1
    current_start = dates[0]

    for i in range(1, len(dates)):
        if (dates[i] - dates[i - 1]).days == 1:
            current += 1
        else:
            if current > longest:
                longest = current
                longest_start = current_start
                longest_end = dates[i - 1]
            current = 1
            current_start = dates[i]

    if current > longest:
        longest = current
        longest_start = current_start
        longest_end = dates[-1]

    # Current streak (must include today or yesterday)
    today = _date.today()
    current_streak = 0
    if dates[-1] >= today - timedelta(days=1):
        current_streak = 1
        for i in range(len(dates) - 2, -1, -1):
            if (dates[i + 1] - dates[i]).days == 1:
                current_streak += 1
            else:
                break

    return {
        "current_streak": current_streak,
        "longest_streak": longest,
        "longest_start": longest_start.isoformat() if longest_start else None,
        "longest_end": longest_end.isoformat() if longest_end else None,
    }


def get_quiet_periods(db, min_gap_hours: float = 8, limit: int = 5) -> list[dict]:
    """Find the longest gaps between sightings with weather context."""
    since = (datetime.now(tz=_local_tz) - timedelta(days=90)).isoformat()
    with db._lock:
        conn = db._get_conn()
        try:
            rows = conn.execute(
                """SELECT timestamp, weather_condition FROM sightings
                   WHERE timestamp >= ? ORDER BY timestamp""",
                (since,),
            ).fetchall()
        finally:
            conn.close()

    if len(rows) < 2:
        return []

    gaps = []
    for i in range(1, len(rows)):
        try:
            t1 = datetime.fromisoformat(rows[i - 1]["timestamp"])
            t2 = datetime.fromisoformat(rows[i]["timestamp"])
            hours = (t2 - t1).total_seconds() / 3600
            if hours >= min_gap_hours:
                gaps.append({
                    "start": rows[i - 1]["timestamp"],
                    "end": rows[i]["timestamp"],
                    "hours": round(hours, 1),
                    "weather": rows[i - 1]["weather_condition"] or "Unknown",
                })
        except (ValueError, TypeError):
            pass

    gaps.sort(key=lambda g: g["hours"], reverse=True)
    return gaps[:limit]


def get_yoy_comparison(db) -> dict:
    """Compare this week's activity to the same week last year."""
    now = datetime.now(tz=_local_tz)
    week_start = now - timedelta(days=now.weekday())
    week_end = week_start + timedelta(days=7)
    last_year_start = week_start.replace(year=week_start.year - 1)
    last_year_end = last_year_start + timedelta(days=7)

    with db._lock:
        conn = db._get_conn()
        try:
            this_week = conn.execute(
                "SELECT COUNT(*) as cnt FROM sightings WHERE timestamp >= ? AND timestamp < ?",
                (week_start.isoformat(), week_end.isoformat()),
            ).fetchone()["cnt"]

            last_year_week = conn.execute(
                "SELECT COUNT(*) as cnt FROM sightings WHERE timestamp >= ? AND timestamp < ?",
                (last_year_start.isoformat(), last_year_end.isoformat()),
            ).fetchone()["cnt"]

            return {
                "this_week": this_week,
                "last_year_same_week": last_year_week,
                "week_label": f"{week_start.strftime('%b %d')} - {week_end.strftime('%b %d')}",
            }
        finally:
            conn.close()


def get_monthly_totals(db, months: int = 12) -> list[dict]:
    """Get monthly sighting totals."""
    since = (datetime.now(tz=_local_tz) - timedelta(days=months * 31)).isoformat()
    with db._lock:
        conn = db._get_conn()
        try:
            rows = conn.execute(
                """SELECT STRFTIME('%Y-%m', timestamp) as month, COUNT(*) as count
                   FROM sightings WHERE timestamp >= ?
                   GROUP BY month ORDER BY month""",
                (since,),
            ).fetchall()
            return [{"month": r["month"], "count": r["count"]} for r in rows]
        finally:
            conn.close()
