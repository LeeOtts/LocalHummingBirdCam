"""Shared utility functions for safe file I/O."""

import json
import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)


def safe_read_json(path: Path, default=None):
    """Read and parse a JSON file, returning *default* on any failure.

    On parse error the corrupt file is backed up to ``<name>.corrupt.<timestamp>``
    so the caller can inspect it later.
    """
    if default is None:
        default = {}
    try:
        if not path.exists():
            return default
        data = json.loads(path.read_text())
        return data
    except json.JSONDecodeError:
        # Back up the corrupt file for debugging
        backup = path.with_suffix(f".corrupt.{int(time.time())}")
        try:
            path.rename(backup)
            logger.warning("Corrupt JSON in %s — backed up to %s", path, backup.name)
        except OSError:
            logger.warning("Corrupt JSON in %s — could not create backup", path)
        return default
    except OSError as e:
        logger.warning("Could not read %s: %s", path, e)
        return default


def safe_write_json(path: Path, data, indent: int = 2):
    """Atomically write *data* as JSON to *path* via a temporary file.

    Writes to ``<path>.tmp`` first then atomically replaces the target,
    so a crash mid-write never leaves a corrupt file.
    """
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(data, indent=indent))
        tmp.replace(path)
    except OSError:
        logger.exception("Failed to write %s", path)
