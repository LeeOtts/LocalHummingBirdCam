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
    # Integrity check — catch SD card corruption before it overwrites good backups
    echo "[$(date)] Running database integrity check..."
    INTEGRITY=$(sqlite3 "$DB_FILE" "PRAGMA integrity_check" 2>&1)
    if [ "$INTEGRITY" != "ok" ]; then
        echo "[$(date)] ERROR: Database integrity check failed: $INTEGRITY"
        echo "[$(date)] Aborting backup to protect existing good backups on server"
        exit 1
    fi
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

# Bundle into tarball (write outside TEMP_DIR to avoid "file changed" warning)
echo "[$(date)] Creating tarball..."
TARBALL="/tmp/$BACKUP_NAME"
tar -czf "$TARBALL" -C "$TEMP_DIR" .

# Ensure remote backup directory exists
ssh ${SSH_OPTS} "${REMOTE}" "mkdir -p ${BACKUP_DIR}"

# Upload
echo "[$(date)] Uploading to ${REMOTE}:${BACKUP_DIR}/${BACKUP_NAME}..."
scp ${SSH_OPTS} "$TARBALL" "${REMOTE}:${BACKUP_DIR}/${BACKUP_NAME}"

SIZE="$(du -h "$TARBALL" | cut -f1)"
LOCAL_BYTES="$(stat -c%s "$TARBALL" 2>/dev/null || stat -f%z "$TARBALL" 2>/dev/null)"

# Verify upload — reject suspiciously small backups (corrupt/empty DB)
REMOTE_BYTES=$(ssh ${SSH_OPTS} "${REMOTE}" "stat -c%s ${BACKUP_DIR}/${BACKUP_NAME} 2>/dev/null || echo 0")
if [ "$REMOTE_BYTES" -lt 1024 ] || [ "$REMOTE_BYTES" != "$LOCAL_BYTES" ]; then
    echo "[$(date)] ERROR: Upload verification failed (local=${LOCAL_BYTES}B, remote=${REMOTE_BYTES}B)"
    rm -f "$TARBALL"
    exit 1
fi

rm -f "$TARBALL"

# Weekly retention — keep 4 weeks (saved on Sundays)
DAY_OF_WEEK=$(date +%u)
if [ "$DAY_OF_WEEK" -eq 7 ]; then
    WEEK_NUM=$(date +%V)
    echo "[$(date)] Sunday — saving weekly backup (week ${WEEK_NUM})..."
    ssh ${SSH_OPTS} "${REMOTE}" "
        mkdir -p ${BACKUP_DIR}/weekly
        cp ${BACKUP_DIR}/${BACKUP_NAME} ${BACKUP_DIR}/weekly/backup_week_${WEEK_NUM}.tar.gz
        # Prune weekly backups older than 4 weeks
        cd ${BACKUP_DIR}/weekly && ls -t backup_week_*.tar.gz 2>/dev/null | tail -n +5 | xargs rm -f 2>/dev/null
    "
fi

# Monthly retention — keep 12 months (saved on the 1st)
DAY_OF_MONTH=$(date +%d)
if [ "$DAY_OF_MONTH" -eq 01 ]; then
    MONTH_TAG=$(date +%Y_%m)
    echo "[$(date)] 1st of month — saving monthly backup (${MONTH_TAG})..."
    ssh ${SSH_OPTS} "${REMOTE}" "
        mkdir -p ${BACKUP_DIR}/monthly
        cp ${BACKUP_DIR}/${BACKUP_NAME} ${BACKUP_DIR}/monthly/backup_${MONTH_TAG}.tar.gz
        # Prune monthly backups older than 12 months
        cd ${BACKUP_DIR}/monthly && ls -t backup_*.tar.gz 2>/dev/null | tail -n +13 | xargs rm -f 2>/dev/null
    "
fi

echo "[$(date)] Backup complete! (${SIZE})"
