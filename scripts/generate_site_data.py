#!/usr/bin/env python3
"""Generate site_data.json for the public website (backyardhummers.com).

Can be run standalone or called from main.py after each detection.
Outputs to website/data/site_data.json.
"""

import json
import logging
import sys
from pathlib import Path

# Add parent directory to path so we can import project modules
sys.path.insert(0, str(Path(__file__).parent.parent))

import config
from data.sightings import SightingsDB

logger = logging.getLogger(__name__)


def _atomic_write(path: Path, data: dict) -> None:
    """Write JSON atomically via a temp file."""
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, default=str))
    tmp.replace(path)


def generate_site_data(db: SightingsDB | None = None, *, sprinkler_active: bool = False) -> Path | None:
    """Generate site_data.json and guestbook.json from the sightings database.

    Args:
        db: Optional SightingsDB instance. Creates one if not provided.
        sprinkler_active: Whether the sprinkler is currently running.

    Returns:
        Path to the generated site_data.json file, or None on failure.
    """
    try:
        if db is None:
            db = SightingsDB()

        output_dir = config.WEBSITE_DATA_DIR
        output_dir.mkdir(parents=True, exist_ok=True)

        # --- site_data.json ---
        data = db.export_for_website(sprinkler_active=sprinkler_active)
        output_path = output_dir / "site_data.json"
        _atomic_write(output_path, data)
        logger.info("Generated site_data.json (%d clips, %d lifetime detections)",
                    len(data.get("clips", [])), data.get("lifetime_detections", 0))

        # --- guestbook.json ---
        entries = db.get_guestbook_entries(limit=200)
        total = db.get_total_page_views()
        guestbook_data = {
            "last_updated": data.get("last_updated", ""),
            "total_visitors": total,
            "entries": entries,
        }
        gb_path = output_dir / "guestbook.json"
        _atomic_write(gb_path, guestbook_data)
        logger.info("Generated guestbook.json (%d entries)", len(entries))

        return output_path

    except Exception:
        logger.exception("Failed to generate site data")
        return None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    result = generate_site_data()
    if result:
        print(f"Generated: {result}")
    else:
        print("Failed to generate site data")
        sys.exit(1)
