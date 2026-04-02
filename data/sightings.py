"""SQLite database for tracking hummingbird sightings and analytics."""

import logging
import sqlite3
import threading
from datetime import datetime, date, timedelta
from pathlib import Path

import config

logger = logging.getLogger(__name__)

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from pytz import timezone as _pytz_tz
    ZoneInfo = lambda key: _pytz_tz(key)

_local_tz = ZoneInfo(config.LOCATION_TIMEZONE)

DB_PATH = config.BASE_DIR / "data" / "sightings.db"
SCHEMA_VERSION = 1


class SightingsDB:
    """Thread-safe SQLite database for hummingbird sightings."""

    def __init__(self, db_path: Path = DB_PATH):
        self._db_path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self):
        with self._lock:
            conn = self._get_conn()
            try:
                conn.executescript("""
                    CREATE TABLE IF NOT EXISTS sightings (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT NOT NULL,
                        clip_filename TEXT,
                        caption TEXT,
                        frame_path TEXT,
                        confidence REAL,
                        weather_temp REAL,
                        weather_condition TEXT,
                        posted_to TEXT DEFAULT '[]',
                        facebook_post_id TEXT
                    );

                    CREATE TABLE IF NOT EXISTS daily_stats (
                        date TEXT PRIMARY KEY,
                        total_detections INTEGER DEFAULT 0,
                        rejected INTEGER DEFAULT 0,
                        peak_hour TEXT,
                        is_record INTEGER DEFAULT 0
                    );

                    CREATE TABLE IF NOT EXISTS page_views (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT NOT NULL,
                        ip_hash TEXT,
                        page TEXT
                    );

                    CREATE TABLE IF NOT EXISTS comment_replies (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT NOT NULL,
                        comment_id TEXT UNIQUE NOT NULL,
                        comment_text TEXT,
                        reply_text TEXT,
                        post_id TEXT
                    );

                    CREATE TABLE IF NOT EXISTS season_dates (
                        year INTEGER PRIMARY KEY,
                        first_visit TEXT,
                        last_visit TEXT
                    );

                    CREATE INDEX IF NOT EXISTS idx_sightings_timestamp
                        ON sightings(timestamp);
                    CREATE INDEX IF NOT EXISTS idx_daily_stats_date
                        ON daily_stats(date);

                    CREATE TABLE IF NOT EXISTS feeders (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT NOT NULL,
                        location_description TEXT,
                        port_count INTEGER DEFAULT 4,
                        active INTEGER DEFAULT 1,
                        camera_feeder INTEGER DEFAULT 0,
                        created_at TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS feeder_refills (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT NOT NULL,
                        feeder_id INTEGER,
                        amount_oz REAL,
                        notes TEXT,
                        FOREIGN KEY (feeder_id) REFERENCES feeders(id)
                    );

                    CREATE TABLE IF NOT EXISTS nectar_production (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT NOT NULL,
                        amount_oz REAL,
                        sugar_ratio TEXT DEFAULT '1:4',
                        notes TEXT
                    );

                    CREATE TABLE IF NOT EXISTS social_engagement (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT NOT NULL,
                        platform TEXT NOT NULL,
                        post_id TEXT UNIQUE,
                        likes INTEGER DEFAULT 0,
                        shares INTEGER DEFAULT 0,
                        comments INTEGER DEFAULT 0,
                        reach INTEGER DEFAULT 0,
                        caption_excerpt TEXT
                    );

                    CREATE TABLE IF NOT EXISTS follower_snapshots (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT NOT NULL,
                        platform TEXT NOT NULL,
                        follower_count INTEGER DEFAULT 0
                    );

                    CREATE TABLE IF NOT EXISTS position_heatmap (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        date TEXT NOT NULL,
                        grid_row INTEGER NOT NULL,
                        grid_col INTEGER NOT NULL,
                        count INTEGER DEFAULT 1,
                        UNIQUE(date, grid_row, grid_col)
                    );

                    CREATE TABLE IF NOT EXISTS predictions_log (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT NOT NULL,
                        prediction_type TEXT NOT NULL,
                        predicted_value TEXT,
                        actual_value TEXT,
                        error_minutes REAL
                    );

                    CREATE TABLE IF NOT EXISTS watering_events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        start_time TEXT NOT NULL,
                        end_time TEXT,
                        zone TEXT,
                        sightings_before_30min INTEGER,
                        sightings_after_30min INTEGER
                    );

                    CREATE INDEX IF NOT EXISTS idx_feeder_refills_ts
                        ON feeder_refills(timestamp);
                    CREATE INDEX IF NOT EXISTS idx_social_engagement_platform
                        ON social_engagement(platform, post_id);
                    CREATE INDEX IF NOT EXISTS idx_position_heatmap_date
                        ON position_heatmap(date);
                    CREATE INDEX IF NOT EXISTS idx_watering_events_start
                        ON watering_events(start_time);

                    CREATE TABLE IF NOT EXISTS schema_version (
                        version INTEGER NOT NULL DEFAULT 0
                    );
                """)
                # Add new columns to sightings table (idempotent — ignore if already exist)
                _new_columns = [
                    ("weather_humidity", "REAL"),
                    ("weather_pressure", "REAL"),
                    ("weather_wind_speed", "REAL"),
                    ("weather_clouds", "INTEGER"),
                    ("bird_count", "INTEGER DEFAULT 1"),
                    ("visit_duration_frames", "INTEGER"),
                    ("behavior", "TEXT"),
                    ("position_x", "REAL"),
                    ("position_y", "REAL"),
                    ("species", "TEXT"),
                    ("species_confidence", "REAL"),
                    ("moon_phase", "REAL"),
                    ("sunrise_offset_min", "INTEGER"),
                ]
                for col_name, col_type in _new_columns:
                    try:
                        conn.execute(f"ALTER TABLE sightings ADD COLUMN {col_name} {col_type}")
                    except sqlite3.OperationalError:
                        pass  # Column already exists

                # Add camera_feeder column to feeders table (idempotent)
                try:
                    conn.execute("ALTER TABLE feeders ADD COLUMN camera_feeder INTEGER DEFAULT 0")
                except sqlite3.OperationalError:
                    pass  # Column already exists

                # Indexes on columns added by ALTER TABLE above
                for idx_sql in [
                    "CREATE INDEX IF NOT EXISTS idx_sightings_behavior ON sightings(behavior)",
                    "CREATE INDEX IF NOT EXISTS idx_sightings_species ON sightings(species)",
                    "CREATE INDEX IF NOT EXISTS idx_follower_snapshots_ts ON follower_snapshots(timestamp)",
                ]:
                    conn.execute(idx_sql)

                # Track schema version for future migrations
                row = conn.execute("SELECT version FROM schema_version").fetchone()
                if row is None:
                    conn.execute("INSERT INTO schema_version (version) VALUES (?)",
                                 (SCHEMA_VERSION,))
                else:
                    current_ver = row[0]
                    # Future migrations go here:
                    # if current_ver < 2:
                    #     conn.execute("ALTER TABLE ...")
                    if current_ver < SCHEMA_VERSION:
                        conn.execute("UPDATE schema_version SET version = ?",
                                     (SCHEMA_VERSION,))

                # Seed historical season data if table is empty
                row = conn.execute("SELECT COUNT(*) as cnt FROM season_dates").fetchone()
                if row[0] == 0:
                    conn.executemany(
                        "INSERT OR IGNORE INTO season_dates (year, first_visit, last_visit) VALUES (?, ?, ?)",
                        [
                            (2020, None, "2020-10-07"),
                            (2021, "2021-04-13", "2021-10-11"),
                            (2022, "2022-04-05", "2022-09-26"),
                            (2023, "2023-04-05", "2023-10-08"),
                            (2024, "2024-04-12", "2024-09-30"),
                            (2025, "2025-04-21", "2025-09-28"),
                        ],
                    )
                conn.commit()
                logger.info("Sightings database initialized at %s", self._db_path)
            finally:
                conn.close()

    def record_sighting(self, clip_filename: str, caption: str,
                        frame_path: str | None = None,
                        confidence: float | None = None,
                        weather_temp: float | None = None,
                        weather_condition: str | None = None,
                        weather_humidity: float | None = None,
                        weather_pressure: float | None = None,
                        weather_wind_speed: float | None = None,
                        weather_clouds: int | None = None,
                        bird_count: int | None = None,
                        visit_duration_frames: int | None = None,
                        behavior: str | None = None,
                        position_x: float | None = None,
                        position_y: float | None = None,
                        species: str | None = None,
                        species_confidence: float | None = None,
                        moon_phase: float | None = None,
                        sunrise_offset_min: int | None = None) -> int:
        """Record a new hummingbird sighting. Returns the sighting ID."""
        if position_x is not None:
            position_x = max(0.0, min(1.0, position_x))
        if position_y is not None:
            position_y = max(0.0, min(1.0, position_y))
        now = datetime.now(tz=_local_tz).isoformat()
        with self._lock:
            conn = self._get_conn()
            try:
                cur = conn.execute(
                    """INSERT INTO sightings
                       (timestamp, clip_filename, caption, frame_path, confidence,
                        weather_temp, weather_condition, weather_humidity,
                        weather_pressure, weather_wind_speed, weather_clouds,
                        bird_count, visit_duration_frames, behavior,
                        position_x, position_y, species, species_confidence,
                        moon_phase, sunrise_offset_min)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (now, clip_filename, caption, frame_path, confidence,
                     weather_temp, weather_condition, weather_humidity,
                     weather_pressure, weather_wind_speed, weather_clouds,
                     bird_count, visit_duration_frames, behavior,
                     position_x, position_y, species, species_confidence,
                     moon_phase, sunrise_offset_min),
                )
                conn.commit()
                sighting_id = cur.lastrowid
                logger.info("Recorded sighting #%d: %s", sighting_id, clip_filename)
                return sighting_id
            finally:
                conn.close()

    def update_posted_to(self, clip_filename: str, platform: str, post_id: str | None = None):
        """Mark a sighting as posted to a platform."""
        import json
        with self._lock:
            conn = self._get_conn()
            try:
                row = conn.execute(
                    "SELECT id, posted_to, facebook_post_id FROM sightings WHERE clip_filename = ? ORDER BY id DESC LIMIT 1",
                    (clip_filename,),
                ).fetchone()
                if not row:
                    return
                platforms = json.loads(row["posted_to"] or "[]")
                if platform not in platforms:
                    platforms.append(platform)
                updates = {"posted_to": json.dumps(platforms)}
                if platform == "facebook" and post_id:
                    updates["facebook_post_id"] = post_id
                set_clause = ", ".join(f"{k} = ?" for k in updates)
                conn.execute(
                    f"UPDATE sightings SET {set_clause} WHERE id = ?",
                    (*updates.values(), row["id"]),
                )
                conn.commit()
            finally:
                conn.close()

    def save_daily_stats(self, dt: date, detections: int, rejected: int,
                         peak_hour: str | None = None, is_record: bool = False):
        """Save or update daily stats."""
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute(
                    """INSERT OR REPLACE INTO daily_stats
                       (date, total_detections, rejected, peak_hour, is_record)
                       VALUES (?, ?, ?, ?, ?)""",
                    (str(dt), detections, rejected, peak_hour, int(is_record)),
                )
                conn.commit()
            finally:
                conn.close()

    def get_sightings(self, days: int = 7, limit: int = 100) -> list[dict]:
        """Get recent sightings."""
        since = (datetime.now(tz=_local_tz) - timedelta(days=days)).isoformat()
        with self._lock:
            conn = self._get_conn()
            try:
                rows = conn.execute(
                    """SELECT * FROM sightings WHERE timestamp >= ?
                       ORDER BY timestamp DESC LIMIT ?""",
                    (since, limit),
                ).fetchall()
                return [dict(r) for r in rows]
            finally:
                conn.close()

    def get_today_count(self) -> int:
        """Get today's sighting count."""
        today = datetime.now(tz=_local_tz).date()
        tomorrow = today + timedelta(days=1)
        with self._lock:
            conn = self._get_conn()
            try:
                row = conn.execute(
                    "SELECT COUNT(*) as cnt FROM sightings WHERE timestamp >= ? AND timestamp < ?",
                    (f"{today}T00:00:00", f"{tomorrow}T00:00:00"),
                ).fetchone()
                return row["cnt"] if row else 0
            finally:
                conn.close()

    def get_hourly_distribution(self, days: int = 14) -> dict[int, int]:
        """Get detection counts by hour of day over the last N days."""
        since = (datetime.now(tz=_local_tz) - timedelta(days=days)).isoformat()
        with self._lock:
            conn = self._get_conn()
            try:
                rows = conn.execute(
                    """SELECT CAST(SUBSTR(timestamp, 12, 2) AS INTEGER) as hour,
                              COUNT(*) as cnt
                       FROM sightings WHERE timestamp >= ?
                       GROUP BY hour""",
                    (since,),
                ).fetchall()
                return {r["hour"]: r["cnt"] for r in rows}
            finally:
                conn.close()

    def get_daily_totals(self, days: int = 30) -> list[dict]:
        """Get daily detection totals for the last N days."""
        since = str(date.today() - timedelta(days=days))
        with self._lock:
            conn = self._get_conn()
            try:
                rows = conn.execute(
                    """SELECT date, total_detections, rejected, is_record
                       FROM daily_stats WHERE date >= ? ORDER BY date""",
                    (since,),
                ).fetchall()
                return [dict(r) for r in rows]
            finally:
                conn.close()

    def get_average_gap_minutes(self, days: int = 14) -> float | None:
        """Calculate the average time between sightings in minutes."""
        since = (datetime.now(tz=_local_tz) - timedelta(days=days)).isoformat()
        with self._lock:
            conn = self._get_conn()
            try:
                row = conn.execute(
                    """SELECT AVG(gap_min) as avg_gap FROM (
                           SELECT (julianday(timestamp) -
                                   julianday(LAG(timestamp) OVER (ORDER BY timestamp)))
                                  * 1440.0 as gap_min
                           FROM sightings WHERE timestamp >= ?
                       ) WHERE gap_min IS NOT NULL AND gap_min > 0 AND gap_min < 240""",
                    (since,),
                ).fetchone()
                return row["avg_gap"] if row and row["avg_gap"] is not None else None
            finally:
                conn.close()

    def get_total_sightings(self) -> int:
        """Get all-time total sighting count."""
        with self._lock:
            conn = self._get_conn()
            try:
                row = conn.execute("SELECT COUNT(*) as cnt FROM sightings").fetchone()
                return row["cnt"] if row else 0
            finally:
                conn.close()

    def prune_old_data(self, page_views_days: int = 90,
                       predictions_days: int = 180,
                       heatmap_days: int = 365):
        """Delete old rows from high-volume tables to keep DB size bounded."""
        cutoffs = {
            "page_views": (datetime.now(tz=_local_tz) - timedelta(days=page_views_days)).isoformat(),
            "predictions_log": (datetime.now(tz=_local_tz) - timedelta(days=predictions_days)).isoformat(),
            "position_heatmap": str(date.today() - timedelta(days=heatmap_days)),
        }
        with self._lock:
            conn = self._get_conn()
            try:
                for table, cutoff in cutoffs.items():
                    col = "date" if table == "position_heatmap" else "timestamp"
                    cur = conn.execute(
                        f"DELETE FROM {table} WHERE {col} < ?", (cutoff,))
                    if cur.rowcount:
                        logger.info("Pruned %d old rows from %s", cur.rowcount, table)
                conn.commit()
            finally:
                conn.close()

    def record_page_view(self, ip_hash: str, page: str):
        """Record a dashboard page view."""
        now = datetime.now(tz=_local_tz).isoformat()
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute(
                    "INSERT INTO page_views (timestamp, ip_hash, page) VALUES (?, ?, ?)",
                    (now, ip_hash, page),
                )
                conn.commit()
            finally:
                conn.close()

    def get_total_page_views(self) -> int:
        """Get total unique visitor count (by IP hash)."""
        with self._lock:
            conn = self._get_conn()
            try:
                row = conn.execute(
                    "SELECT COUNT(DISTINCT ip_hash) as cnt FROM page_views"
                ).fetchone()
                return row["cnt"] if row else 0
            finally:
                conn.close()

    def has_replied_to(self, comment_id: str) -> bool:
        """Check if we've already replied to a Facebook comment."""
        with self._lock:
            conn = self._get_conn()
            try:
                row = conn.execute(
                    "SELECT 1 FROM comment_replies WHERE comment_id = ?",
                    (comment_id,),
                ).fetchone()
                return row is not None
            finally:
                conn.close()

    def record_reply(self, comment_id: str, comment_text: str,
                     reply_text: str, post_id: str):
        """Record a comment reply we made."""
        now = datetime.now(tz=_local_tz).isoformat()
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute(
                    """INSERT OR IGNORE INTO comment_replies
                       (timestamp, comment_id, comment_text, reply_text, post_id)
                       VALUES (?, ?, ?, ?, ?)""",
                    (now, comment_id, comment_text, reply_text, post_id),
                )
                conn.commit()
            finally:
                conn.close()

    def get_season_dates(self) -> list[dict]:
        """Get all season date records ordered by year descending."""
        with self._lock:
            conn = self._get_conn()
            try:
                rows = conn.execute(
                    "SELECT year, first_visit, last_visit FROM season_dates ORDER BY year DESC"
                ).fetchall()
                return [dict(r) for r in rows]
            finally:
                conn.close()

    def upsert_season_date(self, year: int, first_visit: str | None, last_visit: str | None):
        """Insert or update a season date record."""
        if not (2000 <= year <= 2099):
            raise ValueError(f"Year must be 2000-2099, got {year}")
        # Validate date formats if provided
        for val in (first_visit, last_visit):
            if val:
                datetime.strptime(val, "%Y-%m-%d")
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO season_dates (year, first_visit, last_visit) VALUES (?, ?, ?)",
                    (year, first_visit or None, last_visit or None),
                )
                conn.commit()
            finally:
                conn.close()

    def delete_season_date(self, year: int) -> bool:
        """Delete a season date record. Returns True if deleted."""
        with self._lock:
            conn = self._get_conn()
            try:
                cur = conn.execute("DELETE FROM season_dates WHERE year = ?", (year,))
                conn.commit()
                return cur.rowcount > 0
            finally:
                conn.close()

    def export_for_website(self, sprinkler_active: bool = False) -> dict:
        """Export all data needed for the public website's site_data.json."""
        import json as _json
        from analytics.patterns import predict_next_visit, predict_season_arrival
        from schedule import is_daytime

        lifetime = self.get_total_sightings()
        today = self.get_today_count()
        hourly = self.get_hourly_distribution(days=14)
        daily = self.get_daily_totals(days=30)
        avg_gap = self.get_average_gap_minutes(days=14)

        # Week count
        week_ago = (datetime.now(tz=_local_tz) - timedelta(days=7)).isoformat()
        with self._lock:
            conn = self._get_conn()
            try:
                row = conn.execute(
                    "SELECT COUNT(*) as cnt FROM sightings WHERE timestamp >= ?",
                    (week_ago,),
                ).fetchone()
                week_count = row["cnt"] if row else 0
            finally:
                conn.close()

        # Recent clips (last 100)
        sightings = self.get_sightings(days=90, limit=100)
        clips = []
        for s in sightings:
            platforms = []
            try:
                platforms = _json.loads(s.get("posted_to") or "[]")
            except (ValueError, TypeError):
                pass
            clips.append({
                "filename": s.get("clip_filename", ""),
                "timestamp": s.get("timestamp", ""),
                "caption": s.get("caption", ""),
                "confidence": s.get("confidence"),
                "platforms_posted": platforms,
                "thumbnail": (s.get("clip_filename", "").replace(".mp4", "_thumb.jpg")
                              if s.get("clip_filename") else ""),
            })

        # Hourly pattern as 24-element array
        hourly_arr = [hourly.get(h, 0) for h in range(24)]

        # Next visit prediction
        next_visit = None
        try:
            pred = predict_next_visit()
            if pred:
                next_visit = {
                    "time": pred.get("time", ""),
                    "confidence": pred.get("confidence", "unknown"),
                }
        except Exception:
            pass

        # Season arrival prediction (authoritative, shared with analytics page)
        season_prediction = None
        try:
            season_prediction = predict_season_arrival(self)
        except Exception:
            pass

        # Milestones
        milestone_thresholds = [100, 250, 500, 1000, 1500, 2000, 2500, 3000, 5000, 10000]
        latest_milestone = 0
        next_milestone = milestone_thresholds[0] if milestone_thresholds else 1000
        for m in milestone_thresholds:
            if lifetime >= m:
                latest_milestone = m
            else:
                next_milestone = m
                break

        # New analytics data for expanded website
        from analytics.patterns import (
            get_weather_correlations, get_sprinkler_correlation,
            get_activity_streaks, get_yoy_comparison, get_monthly_totals,
            get_quiet_periods,
        )

        return {
            "last_updated": datetime.now(tz=_local_tz).isoformat(),
            "live_feed_url": config.WEBSITE_LIVE_FEED_URL if hasattr(config, 'WEBSITE_LIVE_FEED_URL') else "",
            "lifetime_detections": lifetime,
            "today_detections": today,
            "this_week_detections": week_count,
            "next_predicted_visit": next_visit,
            "hourly_pattern": hourly_arr,
            "daily_counts_30d": daily,
            "avg_gap_minutes": avg_gap,
            "milestones": {"latest": latest_milestone, "next": next_milestone},
            "sleeping": not is_daytime(),
            "sprinkler_active": sprinkler_active,
            "clips": clips,
            "season_dates": self.get_season_dates(),
            "season_prediction": season_prediction,
            "socials": {
                "facebook": getattr(config, 'WEBSITE_SOCIAL_FACEBOOK', ''),
                "instagram": getattr(config, 'WEBSITE_SOCIAL_INSTAGRAM', ''),
                "tiktok": getattr(config, 'WEBSITE_SOCIAL_TIKTOK', ''),
                "twitter": getattr(config, 'WEBSITE_SOCIAL_TWITTER', ''),
                "bluesky": getattr(config, 'WEBSITE_SOCIAL_BLUESKY', ''),
            },
            # Expanded analytics
            "behavior_breakdown": self.get_behavior_breakdown(days=30),
            "species_breakdown": self.get_species_breakdown(days=30),
            "visit_stats": self.get_visit_stats(days=30),
            "position_heatmap": self.get_heatmap(days=30),
            "weather_correlations": get_weather_correlations(self, days=30),
            "sprinkler_effect": get_sprinkler_correlation(self, days=30),
            "activity_streaks": get_activity_streaks(self),
            "yoy_comparison": get_yoy_comparison(self),
            "monthly_totals": get_monthly_totals(self),
            "quiet_periods": get_quiet_periods(self),
            "prediction_accuracy": self.get_prediction_accuracy(days=30),
            "social_engagement": self.get_engagement_summary(days=30),
            "follower_history": self.get_follower_history(days=90),
            "feeder_stats": self.get_feeder_stats(days=30),
            "sunrise_offset_avg_min": self.get_sunrise_offset_avg(days=30),
        }

    def get_replies_last_hour(self) -> int:
        """Count replies made in the last hour (for rate limiting)."""
        one_hour_ago = (datetime.now(tz=_local_tz) - timedelta(hours=1)).isoformat()
        with self._lock:
            conn = self._get_conn()
            try:
                row = conn.execute(
                    "SELECT COUNT(*) as cnt FROM comment_replies WHERE timestamp >= ?",
                    (one_hour_ago,),
                ).fetchone()
                return row["cnt"] if row else 0
            finally:
                conn.close()

    def get_weekly_summary(self) -> dict:
        """Get a summary of the past 7 days for weekly digest posts."""
        now = datetime.now(tz=_local_tz)
        week_ago = (now - timedelta(days=7)).isoformat()
        two_weeks_ago = (now - timedelta(days=14)).isoformat()

        with self._lock:
            conn = self._get_conn()
            try:
                # This week's sightings
                this_week = conn.execute(
                    "SELECT COUNT(*) as cnt FROM sightings WHERE timestamp >= ?",
                    (week_ago,),
                ).fetchone()["cnt"]

                # Last week's sightings (for trend)
                last_week = conn.execute(
                    "SELECT COUNT(*) as cnt FROM sightings WHERE timestamp >= ? AND timestamp < ?",
                    (two_weeks_ago, week_ago),
                ).fetchone()["cnt"]

                # Busiest day this week
                rows = conn.execute(
                    "SELECT date, total_detections FROM daily_stats WHERE date >= ? ORDER BY total_detections DESC LIMIT 1",
                    (str((now - timedelta(days=7)).date()),),
                ).fetchall()
                busiest_day = dict(rows[0]) if rows else None

                # Best caption this week (longest = most descriptive)
                best = conn.execute(
                    "SELECT caption FROM sightings WHERE timestamp >= ? AND caption IS NOT NULL ORDER BY LENGTH(caption) DESC LIMIT 1",
                    (week_ago,),
                ).fetchone()

                return {
                    "total": this_week,
                    "last_week_total": last_week,
                    "trend": "up" if this_week > last_week else "down" if this_week < last_week else "flat",
                    "busiest_day": busiest_day,
                    "best_caption": best["caption"] if best else None,
                }
            finally:
                conn.close()

    # ------------------------------------------------------------------
    # Position heatmap
    # ------------------------------------------------------------------

    def record_heatmap_point(self, x: float, y: float, grid_size: int = 8):
        """Record a detection position to the heatmap grid (8x8 by default)."""
        x = max(0.0, min(1.0, x))
        y = max(0.0, min(1.0, y))
        today = date.today().isoformat()
        row = min(int(y * grid_size), grid_size - 1)
        col = min(int(x * grid_size), grid_size - 1)
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute(
                    """INSERT INTO position_heatmap (date, grid_row, grid_col, count)
                       VALUES (?, ?, ?, 1)
                       ON CONFLICT(date, grid_row, grid_col)
                       DO UPDATE SET count = count + 1""",
                    (today, row, col),
                )
                conn.commit()
            finally:
                conn.close()

    def get_heatmap(self, days: int = 30, grid_size: int = 8) -> list[list[int]]:
        """Get aggregated heatmap as a grid_size x grid_size 2D array."""
        since = str(date.today() - timedelta(days=days))
        grid = [[0] * grid_size for _ in range(grid_size)]
        with self._lock:
            conn = self._get_conn()
            try:
                rows = conn.execute(
                    """SELECT grid_row, grid_col, SUM(count) as total
                       FROM position_heatmap WHERE date >= ?
                       GROUP BY grid_row, grid_col""",
                    (since,),
                ).fetchall()
                for r in rows:
                    gr, gc = r["grid_row"], r["grid_col"]
                    if 0 <= gr < grid_size and 0 <= gc < grid_size:
                        grid[gr][gc] = r["total"]
                return grid
            finally:
                conn.close()

    # ------------------------------------------------------------------
    # Feeder management
    # ------------------------------------------------------------------

    def add_feeder(self, name: str, location_description: str = "",
                   port_count: int = 4, camera_feeder: bool = False) -> int:
        """Add a new feeder. Returns the feeder ID."""
        now = datetime.now(tz=_local_tz).isoformat()
        with self._lock:
            conn = self._get_conn()
            try:
                # If marking as camera feeder, clear any existing camera feeder
                if camera_feeder:
                    conn.execute("UPDATE feeders SET camera_feeder = 0")
                cur = conn.execute(
                    """INSERT INTO feeders (name, location_description, port_count, active, camera_feeder, created_at)
                       VALUES (?, ?, ?, 1, ?, ?)""",
                    (name, location_description, port_count, int(camera_feeder), now),
                )
                conn.commit()
                return cur.lastrowid
            finally:
                conn.close()

    def get_feeders(self) -> list[dict]:
        """Get all feeders."""
        with self._lock:
            conn = self._get_conn()
            try:
                rows = conn.execute(
                    "SELECT * FROM feeders ORDER BY active DESC, name"
                ).fetchall()
                return [dict(r) for r in rows]
            finally:
                conn.close()

    def update_feeder(self, feeder_id: int, name: str | None = None,
                      location_description: str | None = None,
                      port_count: int | None = None,
                      active: bool | None = None,
                      camera_feeder: bool | None = None):
        """Update a feeder's details."""
        updates = {}
        if name is not None:
            updates["name"] = name
        if location_description is not None:
            updates["location_description"] = location_description
        if port_count is not None:
            updates["port_count"] = port_count
        if active is not None:
            updates["active"] = int(active)
        if camera_feeder is not None:
            updates["camera_feeder"] = int(camera_feeder)
        if not updates:
            return
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        with self._lock:
            conn = self._get_conn()
            try:
                # If marking as camera feeder, clear any existing one first
                if camera_feeder:
                    conn.execute("UPDATE feeders SET camera_feeder = 0")
                conn.execute(
                    f"UPDATE feeders SET {set_clause} WHERE id = ?",
                    (*updates.values(), feeder_id),
                )
                conn.commit()
            finally:
                conn.close()

    def record_refill(self, feeder_id: int | None, amount_oz: float,
                      notes: str = "", timestamp: str | None = None) -> int:
        """Record a feeder refill. Returns the refill ID."""
        ts = timestamp or datetime.now(tz=_local_tz).isoformat()
        with self._lock:
            conn = self._get_conn()
            try:
                cur = conn.execute(
                    """INSERT INTO feeder_refills (timestamp, feeder_id, amount_oz, notes)
                       VALUES (?, ?, ?, ?)""",
                    (ts, feeder_id, amount_oz, notes),
                )
                conn.commit()
                return cur.lastrowid
            finally:
                conn.close()

    def get_refills(self, days: int = 90, limit: int = 50) -> list[dict]:
        """Get recent feeder refills."""
        since = (datetime.now(tz=_local_tz) - timedelta(days=days)).isoformat()
        with self._lock:
            conn = self._get_conn()
            try:
                rows = conn.execute(
                    """SELECT r.*, f.name as feeder_name
                       FROM feeder_refills r
                       LEFT JOIN feeders f ON r.feeder_id = f.id
                       WHERE r.timestamp >= ?
                       ORDER BY r.timestamp DESC LIMIT ?""",
                    (since, limit),
                ).fetchall()
                return [dict(r) for r in rows]
            finally:
                conn.close()

    def record_production(self, amount_oz: float, sugar_ratio: str = "1:4",
                          notes: str = "", timestamp: str | None = None) -> int:
        """Record nectar production. Returns the production ID."""
        ts = timestamp or datetime.now(tz=_local_tz).isoformat()
        with self._lock:
            conn = self._get_conn()
            try:
                cur = conn.execute(
                    """INSERT INTO nectar_production (timestamp, amount_oz, sugar_ratio, notes)
                       VALUES (?, ?, ?, ?)""",
                    (ts, amount_oz, sugar_ratio, notes),
                )
                conn.commit()
                return cur.lastrowid
            finally:
                conn.close()

    def get_production_log(self, days: int = 90, limit: int = 50) -> list[dict]:
        """Get recent nectar production log."""
        since = (datetime.now(tz=_local_tz) - timedelta(days=days)).isoformat()
        with self._lock:
            conn = self._get_conn()
            try:
                rows = conn.execute(
                    """SELECT * FROM nectar_production WHERE timestamp >= ?
                       ORDER BY timestamp DESC LIMIT ?""",
                    (since, limit),
                ).fetchall()
                return [dict(r) for r in rows]
            finally:
                conn.close()

    def get_feeder_stats(self, days: int = 30) -> dict:
        """Get feeder management statistics."""
        since = (datetime.now(tz=_local_tz) - timedelta(days=days)).isoformat()
        with self._lock:
            conn = self._get_conn()
            try:
                feeders = conn.execute(
                    "SELECT COUNT(*) as cnt FROM feeders WHERE active = 1"
                ).fetchone()["cnt"]

                # Last refill
                last_refill = conn.execute(
                    "SELECT timestamp FROM feeder_refills ORDER BY timestamp DESC LIMIT 1"
                ).fetchone()
                days_since_refill = None
                if last_refill:
                    lr = datetime.fromisoformat(last_refill["timestamp"])
                    days_since_refill = (datetime.now(tz=_local_tz) - lr).days

                # Total nectar produced in period
                prod = conn.execute(
                    "SELECT SUM(amount_oz) as total FROM nectar_production WHERE timestamp >= ?",
                    (since,),
                ).fetchone()
                nectar_produced_oz = prod["total"] or 0

                # Estimated consumption (visits × ~0.02 oz per visit)
                visits = conn.execute(
                    "SELECT COUNT(*) as cnt FROM sightings WHERE timestamp >= ?",
                    (since,),
                ).fetchone()["cnt"]
                estimated_consumption_oz = round(visits * 0.02, 1)

                # Recent refills
                refills = conn.execute(
                    """SELECT r.timestamp, r.amount_oz, f.name as feeder_name
                       FROM feeder_refills r
                       LEFT JOIN feeders f ON r.feeder_id = f.id
                       ORDER BY r.timestamp DESC LIMIT 5""",
                ).fetchall()

                return {
                    "feeder_count": feeders,
                    "days_since_refill": days_since_refill,
                    "nectar_produced_oz": round(nectar_produced_oz, 1),
                    "estimated_consumption_oz": estimated_consumption_oz,
                    "visits_in_period": visits,
                    "recent_refills": [dict(r) for r in refills],
                }
            finally:
                conn.close()

    # ------------------------------------------------------------------
    # Watering events
    # ------------------------------------------------------------------

    def record_watering_start(self, zone: str | None = None) -> int:
        """Record the start of a watering event. Returns event ID."""
        now = datetime.now(tz=_local_tz).isoformat()
        with self._lock:
            conn = self._get_conn()
            try:
                cur = conn.execute(
                    "INSERT INTO watering_events (start_time, zone) VALUES (?, ?)",
                    (now, zone),
                )
                conn.commit()
                return cur.lastrowid
            finally:
                conn.close()

    def record_watering_end(self, event_id: int):
        """Record the end of a watering event and compute sighting correlation."""
        now = datetime.now(tz=_local_tz)
        with self._lock:
            conn = self._get_conn()
            try:
                row = conn.execute(
                    "SELECT start_time FROM watering_events WHERE id = ?",
                    (event_id,),
                ).fetchone()
                if not row:
                    return

                start = datetime.fromisoformat(row["start_time"])
                before_start = (start - timedelta(minutes=30)).isoformat()
                after_end = (now + timedelta(minutes=30)).isoformat()

                before_count = conn.execute(
                    "SELECT COUNT(*) as cnt FROM sightings WHERE timestamp >= ? AND timestamp < ?",
                    (before_start, row["start_time"]),
                ).fetchone()["cnt"]

                after_count = conn.execute(
                    "SELECT COUNT(*) as cnt FROM sightings WHERE timestamp > ? AND timestamp <= ?",
                    (now.isoformat(), after_end),
                ).fetchone()["cnt"]

                conn.execute(
                    """UPDATE watering_events
                       SET end_time = ?, sightings_before_30min = ?, sightings_after_30min = ?
                       WHERE id = ?""",
                    (now.isoformat(), before_count, after_count, event_id),
                )
                conn.commit()
            finally:
                conn.close()

    def get_watering_events(self, days: int = 30) -> list[dict]:
        """Get recent watering events with sighting correlations."""
        since = (datetime.now(tz=_local_tz) - timedelta(days=days)).isoformat()
        with self._lock:
            conn = self._get_conn()
            try:
                rows = conn.execute(
                    """SELECT * FROM watering_events WHERE start_time >= ?
                       ORDER BY start_time DESC""",
                    (since,),
                ).fetchall()
                return [dict(r) for r in rows]
            finally:
                conn.close()

    def clear_watering_events(self) -> int:
        """Delete all watering events. Returns number of rows deleted."""
        with self._lock:
            conn = self._get_conn()
            try:
                cur = conn.execute("DELETE FROM watering_events")
                conn.commit()
                return cur.rowcount
            finally:
                conn.close()

    # ------------------------------------------------------------------
    # Social engagement
    # ------------------------------------------------------------------

    def record_engagement(self, platform: str, post_id: str,
                          likes: int = 0, shares: int = 0,
                          comments: int = 0, reach: int = 0,
                          caption_excerpt: str = ""):
        """Record or update social media engagement for a post."""
        now = datetime.now(tz=_local_tz).isoformat()
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute(
                    """INSERT INTO social_engagement
                       (timestamp, platform, post_id, likes, shares, comments, reach, caption_excerpt)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(post_id) DO UPDATE SET
                       likes = excluded.likes, shares = excluded.shares,
                       comments = excluded.comments, reach = excluded.reach,
                       timestamp = excluded.timestamp""",
                    (now, platform, post_id, likes, shares, comments, reach, caption_excerpt[:200]),
                )
                conn.commit()
            finally:
                conn.close()

    def get_engagement_summary(self, days: int = 30) -> dict:
        """Get aggregated social engagement metrics."""
        since = (datetime.now(tz=_local_tz) - timedelta(days=days)).isoformat()
        with self._lock:
            conn = self._get_conn()
            try:
                row = conn.execute(
                    """SELECT SUM(likes) as total_likes, SUM(shares) as total_shares,
                       SUM(comments) as total_comments, SUM(reach) as total_reach,
                       COUNT(*) as post_count
                       FROM social_engagement WHERE timestamp >= ?""",
                    (since,),
                ).fetchone()

                top = conn.execute(
                    """SELECT * FROM social_engagement WHERE timestamp >= ?
                       ORDER BY (likes + shares + comments) DESC LIMIT 1""",
                    (since,),
                ).fetchone()

                return {
                    "total_likes": row["total_likes"] or 0,
                    "total_shares": row["total_shares"] or 0,
                    "total_comments": row["total_comments"] or 0,
                    "total_reach": row["total_reach"] or 0,
                    "post_count": row["post_count"] or 0,
                    "top_post": dict(top) if top else None,
                }
            finally:
                conn.close()

    def record_follower_snapshot(self, platform: str, count: int):
        """Record a follower count snapshot."""
        now = datetime.now(tz=_local_tz).isoformat()
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute(
                    "INSERT INTO follower_snapshots (timestamp, platform, follower_count) VALUES (?, ?, ?)",
                    (now, platform, count),
                )
                conn.commit()
            finally:
                conn.close()

    def get_follower_history(self, days: int = 90) -> list[dict]:
        """Get follower count history."""
        since = (datetime.now(tz=_local_tz) - timedelta(days=days)).isoformat()
        with self._lock:
            conn = self._get_conn()
            try:
                rows = conn.execute(
                    """SELECT timestamp, platform, follower_count
                       FROM follower_snapshots WHERE timestamp >= ?
                       ORDER BY timestamp""",
                    (since,),
                ).fetchall()
                return [dict(r) for r in rows]
            finally:
                conn.close()

    # ------------------------------------------------------------------
    # Predictions log
    # ------------------------------------------------------------------

    def log_prediction(self, prediction_type: str, predicted_value: str,
                       actual_value: str | None = None, error_minutes: float | None = None):
        """Log a prediction for accuracy tracking."""
        now = datetime.now(tz=_local_tz).isoformat()
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute(
                    """INSERT INTO predictions_log
                       (timestamp, prediction_type, predicted_value, actual_value, error_minutes)
                       VALUES (?, ?, ?, ?, ?)""",
                    (now, prediction_type, predicted_value, actual_value, error_minutes),
                )
                conn.commit()
            finally:
                conn.close()

    def get_prediction_accuracy(self, days: int = 30) -> dict:
        """Get prediction accuracy stats."""
        since = (datetime.now(tz=_local_tz) - timedelta(days=days)).isoformat()
        with self._lock:
            conn = self._get_conn()
            try:
                rows = conn.execute(
                    """SELECT prediction_type, AVG(error_minutes) as avg_error,
                       COUNT(*) as count
                       FROM predictions_log
                       WHERE timestamp >= ? AND error_minutes IS NOT NULL
                       GROUP BY prediction_type""",
                    (since,),
                ).fetchall()
                result = {}
                for r in rows:
                    result[r["prediction_type"]] = {
                        "avg_error_minutes": round(r["avg_error"], 1) if r["avg_error"] else None,
                        "predictions_logged": r["count"],
                    }
                return result
            finally:
                conn.close()

    # ------------------------------------------------------------------
    # Advanced queries for analytics
    # ------------------------------------------------------------------

    def get_behavior_breakdown(self, days: int = 30) -> dict[str, int]:
        """Get counts of each behavior type."""
        since = (datetime.now(tz=_local_tz) - timedelta(days=days)).isoformat()
        with self._lock:
            conn = self._get_conn()
            try:
                rows = conn.execute(
                    """SELECT behavior, COUNT(*) as cnt FROM sightings
                       WHERE timestamp >= ? AND behavior IS NOT NULL
                       GROUP BY behavior""",
                    (since,),
                ).fetchall()
                return {r["behavior"]: r["cnt"] for r in rows}
            finally:
                conn.close()

    def get_species_breakdown(self, days: int = 30) -> dict[str, int]:
        """Get counts of each species."""
        since = (datetime.now(tz=_local_tz) - timedelta(days=days)).isoformat()
        with self._lock:
            conn = self._get_conn()
            try:
                rows = conn.execute(
                    """SELECT species, COUNT(*) as cnt FROM sightings
                       WHERE timestamp >= ? AND species IS NOT NULL
                       GROUP BY species""",
                    (since,),
                ).fetchall()
                return {r["species"]: r["cnt"] for r in rows}
            finally:
                conn.close()

    def get_visit_stats(self, days: int = 30) -> dict:
        """Get visit duration and bird count statistics."""
        since = (datetime.now(tz=_local_tz) - timedelta(days=days)).isoformat()
        with self._lock:
            conn = self._get_conn()
            try:
                row = conn.execute(
                    """SELECT AVG(bird_count) as avg_birds,
                       MAX(bird_count) as max_birds,
                       AVG(visit_duration_frames) as avg_duration_frames
                       FROM sightings
                       WHERE timestamp >= ? AND bird_count IS NOT NULL""",
                    (since,),
                ).fetchone()
                # Convert frames to seconds (~15 fps on lores stream)
                avg_dur_frames = row["avg_duration_frames"]
                avg_dur_sec = round(avg_dur_frames / 15.0, 1) if avg_dur_frames else None
                return {
                    "avg_birds_per_visit": round(row["avg_birds"], 1) if row["avg_birds"] else None,
                    "max_simultaneous": row["max_birds"] or 0,
                    "avg_visit_duration_sec": avg_dur_sec,
                }
            finally:
                conn.close()

    def get_sunrise_offset_avg(self, days: int = 30) -> float | None:
        """Get average minutes after sunrise for first daily visit."""
        since = (datetime.now(tz=_local_tz) - timedelta(days=days)).isoformat()
        with self._lock:
            conn = self._get_conn()
            try:
                row = conn.execute(
                    """SELECT AVG(sunrise_offset_min) as avg_offset
                       FROM sightings
                       WHERE timestamp >= ? AND sunrise_offset_min IS NOT NULL""",
                    (since,),
                ).fetchone()
                return round(row["avg_offset"], 0) if row["avg_offset"] else None
            finally:
                conn.close()
