"""Social media engagement tracker — fetches likes, shares, comments, followers."""

import logging
import time
import threading

import config

logger = logging.getLogger(__name__)

_GRAPH_API_BASE = f"https://graph.facebook.com/{config.FACEBOOK_API_VERSION}"


class EngagementTracker:
    """Periodically fetches engagement metrics from social platforms."""

    def __init__(self, db, interval_hours: float = 1.0):
        self._db = db
        self._interval = interval_hours * 3600
        self._running = False
        self._thread = None
        self._session = None  # lazy-init requests.Session to reuse connections

    def start(self):
        """Start the background engagement tracking thread."""
        self._running = True
        self._thread = threading.Thread(
            target=self._run_loop, daemon=True, name="engagement-tracker"
        )
        self._thread.start()
        logger.info("Engagement tracker started (every %.0f hours)", self._interval / 3600)

    def stop(self):
        self._running = False

    def _run_loop(self):
        """Main loop: fetch engagement + follower counts periodically."""
        while self._running:
            try:
                self._fetch_facebook_engagement()
            except Exception:
                logger.debug("Facebook engagement fetch failed", exc_info=True)
            try:
                self._snapshot_followers()
            except Exception:
                logger.debug("Follower snapshot failed", exc_info=True)
            # Sleep in small intervals so we can stop promptly
            end_time = time.time() + self._interval
            while self._running and time.time() < end_time:
                time.sleep(30)

    def _get_session(self):
        """Return a reusable requests.Session (lazy-initialized)."""
        if self._session is None:
            import requests
            self._session = requests.Session()
        return self._session

    def _fetch_facebook_engagement(self):
        """Fetch engagement metrics for recent Facebook posts."""
        if not config.FACEBOOK_PAGE_ACCESS_TOKEN or not config.FACEBOOK_PAGE_ID:
            return

        session = self._get_session()

        # Get recent sightings that were posted to Facebook
        sightings = self._db.get_sightings(days=7, limit=50)
        import json as _json
        for s in sightings:
            fb_post_id = s.get("facebook_post_id")
            if not fb_post_id:
                continue

            try:
                resp = session.get(
                    f"{_GRAPH_API_BASE}/{fb_post_id}",
                    params={
                        "fields": "likes.summary(true),shares,comments.summary(true)",
                        "access_token": config.FACEBOOK_PAGE_ACCESS_TOKEN,
                    },
                    timeout=10,
                )
                if resp.status_code != 200:
                    continue

                data = resp.json()
                likes = data.get("likes", {}).get("summary", {}).get("total_count", 0)
                shares = data.get("shares", {}).get("count", 0)
                comments = data.get("comments", {}).get("summary", {}).get("total_count", 0)

                caption_excerpt = (s.get("caption") or "")[:200]
                self._db.record_engagement(
                    platform="facebook",
                    post_id=fb_post_id,
                    likes=likes,
                    shares=shares,
                    comments=comments,
                    caption_excerpt=caption_excerpt,
                )
            except Exception:
                continue

        logger.debug("Facebook engagement fetch complete")

    def _snapshot_followers(self):
        """Snapshot follower count for Facebook page."""
        if not config.FACEBOOK_PAGE_ACCESS_TOKEN or not config.FACEBOOK_PAGE_ID:
            return

        session = self._get_session()
        try:
            resp = session.get(
                f"{_GRAPH_API_BASE}/{config.FACEBOOK_PAGE_ID}",
                params={
                    "fields": "followers_count",
                    "access_token": config.FACEBOOK_PAGE_ACCESS_TOKEN,
                },
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json()
                count = data.get("followers_count", 0)
                if count > 0:
                    self._db.record_follower_snapshot("facebook", count)
                    logger.debug("Facebook followers: %d", count)
        except Exception:
            pass
