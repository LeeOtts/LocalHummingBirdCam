"""SSH helper for remote operations against the SiteGround website server."""

import logging
import subprocess
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config

logger = logging.getLogger(__name__)


def _ssh_cmd() -> list:
    """Build the SSH command prefix from config."""
    return [
        "ssh",
        "-p", str(config.WEBSITE_REMOTE_PORT),
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "BatchMode=yes",
        f"{config.WEBSITE_REMOTE_USER}@{config.WEBSITE_REMOTE_HOST}",
    ]


def remote_file_exists(remote_path: str) -> bool:
    """Check if a file exists on the remote server. Returns False on any error."""
    try:
        result = subprocess.run(
            _ssh_cmd() + [f"test -f {remote_path}"],
            timeout=15,
            capture_output=True,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, OSError) as e:
        logger.warning("SSH check failed for %s: %s", remote_path, e)
        return False


def remote_delete_files(remote_paths: list) -> bool:
    """Delete files on the remote server. Returns True on success."""
    if not remote_paths:
        return True
    try:
        result = subprocess.run(
            _ssh_cmd() + ["rm", "-f"] + remote_paths,
            timeout=30,
            capture_output=True,
        )
        if result.returncode != 0:
            logger.error("Remote delete failed: %s", result.stderr.decode(errors="replace"))
        return result.returncode == 0
    except (subprocess.TimeoutExpired, OSError) as e:
        logger.error("Remote delete error: %s", e)
        return False


def rsync_site_data() -> bool:
    """Push site_data.json to the remote server."""
    site_data = config.WEBSITE_DATA_DIR / "site_data.json"
    if not site_data.exists():
        logger.warning("site_data.json not found at %s", site_data)
        return False

    remote = f"{config.WEBSITE_REMOTE_USER}@{config.WEBSITE_REMOTE_HOST}"
    remote_dest = f"{remote}:{config.WEBSITE_REMOTE_PATH}/data/site_data.json"
    ssh_opts = f"ssh -p {config.WEBSITE_REMOTE_PORT} -o StrictHostKeyChecking=accept-new"

    try:
        result = subprocess.run(
            ["rsync", "-az", "-e", ssh_opts, "--timeout=30",
             str(site_data), remote_dest],
            timeout=60,
            capture_output=True,
        )
        if result.returncode != 0:
            logger.error("rsync site_data failed: %s", result.stderr.decode(errors="replace"))
        return result.returncode == 0
    except (subprocess.TimeoutExpired, OSError) as e:
        logger.error("rsync site_data error: %s", e)
        return False
