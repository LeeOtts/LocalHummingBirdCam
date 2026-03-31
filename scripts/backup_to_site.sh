#!/usr/bin/env bash
# =============================================================================
# Backyard Hummers — Backup critical data to SiteGround (7-day rolling)
#
# Backs up: sightings.db, .env, retry_queue.json, logs
# Rotation: day-of-week naming (backup_mon.tar.gz … backup_sun.tar.gz)
#           — each day overwrites the same file from last week
#
# Run via cron daily at 2 AM:
#   0 2 * * * /path/to/LocalHummingBirdCam/scripts/backup_to_site.sh >> /tmp/hummers_backup.log 2>&1
#
# Prerequisites:
#   - SSH key auth set up from Pi to SiteGround (ssh-copy-id user@host)
#   - WEBSITE_REMOTE_HOST and WEBSITE_REMOTE_USER set in .env
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Load SSH config from .env
if [ -f "$PROJECT_DIR/.env" ]; then
    # shellcheck disable=SC1091
    source <(grep -E '^WEBSITE_' "$PROJECT_DIR/.env" | sed 's/^/export /')
fi

REMOTE_HOST="${WEBSITE_REMOTE_HOST:-}"
REMOTE_USER="${WEBSITE_REMOTE_USER:-}"
REMOTE_PORT="${WEBSITE_REMOTE_PORT:-22}"

if [ -z "$REMOTE_HOST" ] || [ -z "$REMOTE_USER" ]; then
    echo "[$(date)] ERROR: WEBSITE_REMOTE_HOST and WEBSITE_REMOTE_USER must be set in .env"
    exit 1
fi

REMOTE="${REMOTE_USER}@${REMOTE_HOST}"
SSH_OPTS="-p ${REMOTE_PORT} -o StrictHostKeyChecking=accept-new"
BACKUP_DIR="~/backups"
BACKUP_NAME="backup_$(date +%a | tr '[:upper:]' '[:lower:]').tar.gz"

echo "[$(date)] Starting backup — ${BACKUP_NAME}"

# Create temp working directory
TEMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TEMP_DIR"' EXIT

# Safe SQLite backup (handles WAL mode correctly)
DB_FILE="$PROJECT_DIR/data/sightings.db"
if [ -f "$DB_FILE" ]; then
    echo "[$(date)] Backing up database..."
    sqlite3 "$DB_FILE" ".backup '$TEMP_DIR/sightings.db'"
else
    echo "[$(date)] WARNING: sightings.db not found, skipping"
fi

# Copy other files to temp dir (only if they exist)
for file in .env retry_queue.json; do
    [ -f "$PROJECT_DIR/$file" ] && cp "$PROJECT_DIR/$file" "$TEMP_DIR/"
done
[ -f "$PROJECT_DIR/logs/hummingbird.log" ] && cp "$PROJECT_DIR/logs/hummingbird.log" "$TEMP_DIR/"

# Bundle into tarball
echo "[$(date)] Creating tarball..."
tar -czf "$TEMP_DIR/$BACKUP_NAME" -C "$TEMP_DIR" \
    --exclude="$BACKUP_NAME" \
    .

# Ensure remote backup directory exists
ssh ${SSH_OPTS} "${REMOTE}" "mkdir -p ${BACKUP_DIR}"

# Upload
echo "[$(date)] Uploading to ${REMOTE}:${BACKUP_DIR}/${BACKUP_NAME}..."
scp ${SSH_OPTS} "$TEMP_DIR/$BACKUP_NAME" "${REMOTE}:${BACKUP_DIR}/${BACKUP_NAME}"

echo "[$(date)] Backup complete! ($(du -h "$TEMP_DIR/$BACKUP_NAME" | cut -f1))"
