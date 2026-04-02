#!/usr/bin/env python3
"""Post-sync cleanup: mark clips as synced and delete local copies.

Called by sync_to_site.sh after rsync completes. For each unsynced clip,
verifies the file exists on the remote server before deleting locally.
"""

import logging
import sys
from pathlib import Path

# Ensure project root is on the path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
from data.sightings import SightingsDB
from scripts.remote_ssh import remote_file_exists

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def main():
    if not config.WEBSITE_REMOTE_HOST or not config.WEBSITE_REMOTE_USER:
        logger.error("WEBSITE_REMOTE_HOST/USER not configured — skipping cleanup")
        return

    db = SightingsDB()
    unsynced = db.get_unsynced_clips()

    if not unsynced:
        logger.info("No unsynced clips to process")
        return

    logger.info("Checking %d unsynced clip(s)...", len(unsynced))
    cleaned = 0

    for filename in unsynced:
        remote_path = f"{config.WEBSITE_REMOTE_PATH}/clips/{filename}"

        if not remote_file_exists(remote_path):
            logger.info("  %s — not yet on remote, skipping", filename)
            continue

        # Confirmed on remote — mark synced and delete local copies
        db.mark_synced(filename)

        local_mp4 = config.CLIPS_DIR / filename
        local_thumb = config.CLIPS_DIR / filename.replace(".mp4", "_thumb.jpg")
        local_caption = config.CLIPS_DIR / filename.replace(".mp4", ".txt")

        for f in (local_mp4, local_thumb, local_caption):
            try:
                f.unlink(missing_ok=True)
            except OSError as e:
                logger.warning("  Could not delete %s: %s", f.name, e)

        logger.info("  %s — synced & cleaned up", filename)
        cleaned += 1

    logger.info("Cleanup complete: %d/%d clips processed", cleaned, len(unsynced))


if __name__ == "__main__":
    main()
