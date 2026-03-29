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


def generate_site_data(db: SightingsDB | None = None) -> Path | None:
    """Generate site_data.json from the sightings database.

    Args:
        db: Optional SightingsDB instance. Creates one if not provided.

    Returns:
        Path to the generated JSON file, or None on failure.
    """
    try:
        if db is None:
            db = SightingsDB()

        data = db.export_for_website()

        output_dir = config.WEBSITE_DATA_DIR
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / "site_data.json"

        # Atomic write to prevent partial reads during sync
        tmp_path = output_path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(data, indent=2, default=str))
        tmp_path.replace(output_path)

        logger.info("Generated site_data.json (%d clips, %d lifetime detections)",
                     len(data.get("clips", [])), data.get("lifetime_detections", 0))
        return output_path

    except Exception:
        logger.exception("Failed to generate site_data.json")
        return None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    result = generate_site_data()
    if result:
        print(f"Generated: {result}")
    else:
        print("Failed to generate site data")
        sys.exit(1)
