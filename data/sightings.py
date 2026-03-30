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

                    CREATE TABLE IF NOT EXISTS guestbook (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT NOT NULL,
                        name TEXT NOT NULL,
                        message TEXT NOT NULL,
                        ip_hash TEXT
                    );

                    CREATE TABLE IF NOT EXISTS comment_replies (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT NOT NULL,
                        comment_id TEXT UNIQUE NOT NULL,
                        comment_text TEXT,
                        reply_text TEXT,
                        post_id TEXT
                    );

                    CREATE INDEX IF NOT EXISTS idx_sightings_timestamp
                        ON sightings(timestamp);
                    CREATE INDEX IF NOT EXISTS idx_daily_stats_date
                        ON daily_stats(date);
                """)
                conn.commit()
                logger.info("Sightings database initialized at %s", self._db_path)
            finally:
                conn.close()

    def record_sighting(self, clip_filename: str, caption: str,
                        frame_path: str | None = None,
                        confidence: float | None = None,
                        weather_temp: float | None = None,
                        weather_condition: str | None = None) -> int:
        """Record a new hummingbird sighting. Returns the sighting ID."""
        now = datetime.now(tz=_local_tz).isoformat()
        with self._lock:
            conn = self._get_conn()
            try:
                cur = conn.execute(
                    """INSERT INTO sightings
                       (timestamp, clip_filename, caption, frame_path, confidence,
                        weather_temp, weather_condition)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (now, clip_filename, caption, frame_path, confidence,
                     weather_temp, weather_condition),
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
        today = datetime.now(tz=_local_tz).strftime("%Y-%m-%d")
        with self._lock:
            conn = self._get_conn()
            try:
                row = conn.execute(
                    "SELECT COUNT(*) as cnt FROM sightings WHERE timestamp LIKE ?",
                    (f"{today}%",),
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
                    "SELECT timestamp FROM sightings WHERE timestamp >= ?",
                    (since,),
                ).fetchall()
                hours: dict[int, int] = {}
                for r in rows:
                    try:
                        h = datetime.fromisoformat(r["timestamp"]).hour
                        hours[h] = hours.get(h, 0) + 1
                    except (ValueError, TypeError):
                        pass
                return hours
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
                rows = conn.execute(
                    "SELECT timestamp FROM sightings WHERE timestamp >= ? ORDER BY timestamp",
                    (since,),
                ).fetchall()
                if len(rows) < 2:
                    return None
                gaps = []
                for i in range(1, len(rows)):
                    try:
                        t1 = datetime.fromisoformat(rows[i - 1]["timestamp"])
                        t2 = datetime.fromisoformat(rows[i]["timestamp"])
                        gap = (t2 - t1).total_seconds() / 60
                        # Only count gaps under 4 hours (skip overnight)
                        if gap < 240:
                            gaps.append(gap)
                    except (ValueError, TypeError):
                        pass
                return sum(gaps) / len(gaps) if gaps else None
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

    def add_guestbook_entry(self, name: str, message: str, ip_hash: str) -> int | None:
        """Add a guestbook entry. Returns entry ID or None if rate-limited."""
        now = datetime.now(tz=_local_tz)
        # Rate limit: max 3 entries per IP per hour
        one_hour_ago = (now - timedelta(hours=1)).isoformat()
        with self._lock:
            conn = self._get_conn()
            try:
                row = conn.execute(
                    "SELECT COUNT(*) as cnt FROM guestbook WHERE ip_hash = ? AND timestamp >= ?",
                    (ip_hash, one_hour_ago),
                ).fetchone()
                if row and row["cnt"] >= 3:
                    return None
                cur = conn.execute(
                    "INSERT INTO guestbook (timestamp, name, message, ip_hash) VALUES (?, ?, ?, ?)",
                    (now.isoformat(), name[:50], message[:500], ip_hash),
                )
                conn.commit()
                return cur.lastrowid
            finally:
                conn.close()

    def get_guestbook_entries(self, limit: int = 50) -> list[dict]:
        """Get recent guestbook entries."""
        with self._lock:
            conn = self._get_conn()
            try:
                rows = conn.execute(
                    "SELECT id, timestamp, name, message FROM guestbook ORDER BY id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
                return [dict(r) for r in rows]
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

    def export_for_website(self, sprinkler_active: bool = False) -> dict:
        """Export all data needed for the public website's site_data.json."""
        import json as _json
        from analytics.patterns import predict_next_visit
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
            "socials": {
                "facebook": getattr(config, 'WEBSITE_SOCIAL_FACEBOOK', ''),
                "instagram": getattr(config, 'WEBSITE_SOCIAL_INSTAGRAM', ''),
                "tiktok": getattr(config, 'WEBSITE_SOCIAL_TIKTOK', ''),
                "twitter": getattr(config, 'WEBSITE_SOCIAL_TWITTER', ''),
                "bluesky": getattr(config, 'WEBSITE_SOCIAL_BLUESKY', ''),
            },
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
