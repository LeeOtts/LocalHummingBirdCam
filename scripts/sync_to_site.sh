#!/usr/bin/env bash
# =============================================================================
# Backyard Hummers — Sync clips + site data to SiteGround (backyardhummers.com)
#
# Run via cron every 5 minutes:
#   */5 * * * * /path/to/LocalHummingBirdCam/scripts/sync_to_site.sh >> /tmp/hummers_sync.log 2>&1
#
# Prerequisites:
#   - SSH key auth set up from Pi to SiteGround (ssh-copy-id user@host)
#   - WEBSITE_REMOTE_HOST and WEBSITE_REMOTE_USER set in .env
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Load config from .env
if [ -f "$PROJECT_DIR/.env" ]; then
    # shellcheck disable=SC1091
    source <(grep -E '^WEBSITE_' "$PROJECT_DIR/.env" | sed 's/^/export /')
fi

REMOTE_HOST="${WEBSITE_REMOTE_HOST:-}"
REMOTE_USER="${WEBSITE_REMOTE_USER:-}"
REMOTE_PATH="${WEBSITE_REMOTE_PATH:-public_html}"
REMOTE_PORT="${WEBSITE_REMOTE_PORT:-22}"

if [ -z "$REMOTE_HOST" ] || [ -z "$REMOTE_USER" ]; then
    echo "[$(date)] ERROR: WEBSITE_REMOTE_HOST and WEBSITE_REMOTE_USER must be set in .env"
    exit 1
fi

REMOTE="${REMOTE_USER}@${REMOTE_HOST}"
SSH_OPTS="-p ${REMOTE_PORT} -o StrictHostKeyChecking=accept-new"
CLIPS_DIR="$PROJECT_DIR/clips"
SITE_DATA="$PROJECT_DIR/website/data/site_data.json"

echo "[$(date)] Starting sync to ${REMOTE}:${REMOTE_PATH}"

# Step 1: Regenerate site_data.json
echo "[$(date)] Generating site_data.json..."
cd "$PROJECT_DIR"
python3 scripts/generate_site_data.py || echo "[$(date)] WARNING: site data generation failed"

# Step 2: Sync site_data.json
if [ -f "$SITE_DATA" ]; then
    echo "[$(date)] Syncing site_data.json..."
    rsync -az -e "ssh ${SSH_OPTS}" --timeout=30 \
        "$SITE_DATA" \
        "${REMOTE}:${REMOTE_PATH}/data/site_data.json"
fi

# Step 3: Sync video clips (only .mp4 files, skip temp files)
if [ -d "$CLIPS_DIR" ]; then
    echo "[$(date)] Syncing clips..."
    rsync -az --timeout=60 \
        --include='*.mp4' \
        --include='*_thumb.jpg' \
        --exclude='_*' \
        --exclude='*.h264' \
        --exclude='*.wav' \
        --exclude='*.txt' \
        "$CLIPS_DIR/" \
        "${REMOTE}:${REMOTE_PATH}/clips/"
fi

echo "[$(date)] Sync complete!"
