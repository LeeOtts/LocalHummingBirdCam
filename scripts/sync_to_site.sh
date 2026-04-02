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

# Auto-update from git — pull latest code before syncing
echo "[$(date)] Pulling latest from git..."
git -C "$PROJECT_DIR" pull --ff-only 2>&1 || echo "[$(date)] WARNING: git pull skipped (local changes or conflict)"

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
PYTHON="${PROJECT_DIR}/.venv/bin/python3"
[ -x "$PYTHON" ] || PYTHON="python3"
"$PYTHON" scripts/generate_site_data.py || echo "[$(date)] WARNING: site data generation failed"

# Step 2: Cache-bust CSS/JS references in index.html before syncing.
# Uses the short git hash so the version changes on every deploy.
CACHE_VER=$(git -C "$PROJECT_DIR" rev-parse --short HEAD 2>/dev/null || echo "$(date +%s)")
echo "[$(date)] Cache-busting static assets (v=$CACHE_VER)..."
sed -i "s/style\.css?v=[^\"']*/style.css?v=${CACHE_VER}/g; s/app\.js?v=[^\"']*/app.js?v=${CACHE_VER}/g" \
    "$PROJECT_DIR/website/index.html"

# Step 3: Sync static website files (HTML/CSS/JS/img — only transfers changed files)
echo "[$(date)] Syncing website files..."
rsync -az --delete -e "ssh ${SSH_OPTS}" --timeout=60 \
    --exclude='data/site_data.json' \
    --exclude='clips/' \
    "$PROJECT_DIR/website/" \
    "${REMOTE}:${REMOTE_PATH}/"

# Restore index.html so git stays clean
git -C "$PROJECT_DIR" checkout -- "$PROJECT_DIR/website/index.html" 2>/dev/null || true

# Step 4: Sync site_data.json
if [ -f "$SITE_DATA" ]; then
    echo "[$(date)] Syncing site_data.json..."
    rsync -az -e "ssh ${SSH_OPTS}" --timeout=30 \
        "$SITE_DATA" \
        "${REMOTE}:${REMOTE_PATH}/data/site_data.json"
fi

# Step 5: Sync video clips (only .mp4 files, skip temp files)
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
