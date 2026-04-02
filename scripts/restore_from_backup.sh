#!/usr/bin/env bash
# =============================================================================
# Backyard Hummers — Restore from SiteGround backup
#
# Usage:
#   bash scripts/restore_from_backup.sh              # lists available backups
#   bash scripts/restore_from_backup.sh mon           # restores Monday's backup
#   bash scripts/restore_from_backup.sh backup_tue.tar.gz  # also works
#   bash scripts/restore_from_backup.sh --latest      # restores most recent backup
#
# Flags:
#   --latest       Auto-select the most recent backup by file modification time
#   --no-confirm   Skip the confirmation prompt (for scripted/automated use)
#
# What it restores: sightings.db, .env, retry_queue.json
# What it skips: logs (informational only, not worth restoring)
#
# Safety:
#   - Stops the hummingbird service before restoring
#   - Creates a local snapshot of current files before overwriting
#   - Restarts the service after restore
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

AUTO_LATEST=false
NO_CONFIRM=false
INPUT=""

# Parse flags
while [[ $# -gt 0 ]]; do
    case "$1" in
        --latest)    AUTO_LATEST=true; shift ;;
        --no-confirm) NO_CONFIRM=true; shift ;;
        *)           INPUT="$1"; shift ;;
    esac
done

# SSH config can come from .env OR environment variables (for bootstrap)
if [ -f "$PROJECT_DIR/.env" ]; then
    # shellcheck disable=SC1091
    source <(grep -E '^WEBSITE_' "$PROJECT_DIR/.env" | sed 's/^/export /')
fi

REMOTE_HOST="${WEBSITE_REMOTE_HOST:-}"
REMOTE_USER="${WEBSITE_REMOTE_USER:-}"
REMOTE_PORT="${WEBSITE_REMOTE_PORT:-22}"

if [ -z "$REMOTE_HOST" ] || [ -z "$REMOTE_USER" ]; then
    echo "ERROR: WEBSITE_REMOTE_HOST and WEBSITE_REMOTE_USER must be set in .env or environment"
    exit 1
fi

REMOTE="${REMOTE_USER}@${REMOTE_HOST}"
SSH_OPTS="-p ${REMOTE_PORT} -o StrictHostKeyChecking=accept-new"
BACKUP_DIR="~/backups"

# --- No argument and no --latest: list available backups ---
if [ -z "$INPUT" ] && [ "$AUTO_LATEST" = false ]; then
    echo "Available backups on SiteGround:"
    echo "─────────────────────────────────"
    ssh ${SSH_OPTS} "${REMOTE}" "ls -lh ${BACKUP_DIR}/*.tar.gz 2>/dev/null" || echo "  (none found)"
    echo ""
    echo "Usage: $0 <day>"
    echo "  e.g.  $0 mon        # restore Monday's backup"
    echo "        $0 wed        # restore Wednesday's backup"
    echo "        $0 --latest   # restore most recent backup"
    exit 0
fi

# --- Determine which backup to restore ---
if [ "$AUTO_LATEST" = true ]; then
    # Find the most recent backup across daily, weekly, and monthly
    BACKUP_NAME=$(ssh ${SSH_OPTS} "${REMOTE}" "
        ls -t ${BACKUP_DIR}/backup_*.tar.gz \
              ${BACKUP_DIR}/weekly/backup_*.tar.gz \
              ${BACKUP_DIR}/monthly/backup_*.tar.gz 2>/dev/null \
        | head -1
    ")
    if [ -z "$BACKUP_NAME" ]; then
        echo "ERROR: No backups found on SiteGround"
        exit 1
    fi
    # BACKUP_NAME is now the full remote path — extract just the path for scp
    BACKUP_REMOTE_PATH="$BACKUP_NAME"
    BACKUP_DISPLAY="$(basename "$BACKUP_NAME")"
    echo "Auto-selected most recent backup: ${BACKUP_DISPLAY}"
else
    # Accept "mon" or "backup_mon.tar.gz"
    if [[ "$INPUT" == backup_* ]]; then
        BACKUP_NAME="$INPUT"
    else
        BACKUP_NAME="backup_${INPUT}.tar.gz"
    fi
    BACKUP_REMOTE_PATH="${BACKUP_DIR}/${BACKUP_NAME}"
    BACKUP_DISPLAY="$BACKUP_NAME"
fi

echo "╔══════════════════════════════════════════════╗"
echo "║  RESTORE FROM BACKUP: ${BACKUP_DISPLAY}"
echo "╚══════════════════════════════════════════════╝"
echo ""

# Verify backup exists on remote
if ! ssh ${SSH_OPTS} "${REMOTE}" "test -f ${BACKUP_REMOTE_PATH}"; then
    echo "ERROR: ${BACKUP_DISPLAY} not found on SiteGround"
    echo "Run '$0' with no arguments to list available backups."
    exit 1
fi

# Confirm with user (unless --no-confirm)
REMOTE_SIZE=$(ssh ${SSH_OPTS} "${REMOTE}" "du -h ${BACKUP_REMOTE_PATH} | cut -f1")
echo "Backup found: ${BACKUP_DISPLAY} (${REMOTE_SIZE})"
echo ""
if [ "$NO_CONFIRM" = false ]; then
    read -rp "This will overwrite your current database and config. Continue? (y/N) " CONFIRM
    if [[ ! "$CONFIRM" =~ ^[Yy]$ ]]; then
        echo "Restore cancelled."
        exit 0
    fi
fi

# Create temp directory
TEMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TEMP_DIR"' EXIT

# Download backup
echo ""
echo "[$(date)] Downloading ${BACKUP_DISPLAY}..."
scp ${SSH_OPTS} "${REMOTE}:${BACKUP_REMOTE_PATH}" "$TEMP_DIR/backup.tar.gz"

# Extract
echo "[$(date)] Extracting..."
tar -xzf "$TEMP_DIR/backup.tar.gz" -C "$TEMP_DIR"
rm "$TEMP_DIR/backup.tar.gz"

echo "[$(date)] Backup contains:"
ls -la "$TEMP_DIR/"
echo ""

# Stop service
echo "[$(date)] Stopping hummingbird service..."
if sudo systemctl is-active --quiet hummingbird 2>/dev/null; then
    sudo systemctl stop hummingbird
    SERVICE_WAS_RUNNING=true
else
    SERVICE_WAS_RUNNING=false
    echo "  (service was not running)"
fi

# Snapshot current files before overwriting
SNAPSHOT_DIR="$PROJECT_DIR/data/pre_restore_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$SNAPSHOT_DIR"
echo "[$(date)] Saving current files to ${SNAPSHOT_DIR}..."
[ -f "$PROJECT_DIR/data/sightings.db" ] && cp "$PROJECT_DIR/data/sightings.db" "$SNAPSHOT_DIR/"
[ -f "$PROJECT_DIR/.env" ] && cp "$PROJECT_DIR/.env" "$SNAPSHOT_DIR/"
[ -f "$PROJECT_DIR/retry_queue.json" ] && cp "$PROJECT_DIR/retry_queue.json" "$SNAPSHOT_DIR/"

# Restore files
echo "[$(date)] Restoring files..."
[ -f "$TEMP_DIR/sightings.db" ] && cp "$TEMP_DIR/sightings.db" "$PROJECT_DIR/data/sightings.db" && echo "  ✓ sightings.db"
[ -f "$TEMP_DIR/.env" ] && cp "$TEMP_DIR/.env" "$PROJECT_DIR/.env" && echo "  ✓ .env"
[ -f "$TEMP_DIR/retry_queue.json" ] && cp "$TEMP_DIR/retry_queue.json" "$PROJECT_DIR/retry_queue.json" && echo "  ✓ retry_queue.json"

# Restart service
if [ "$SERVICE_WAS_RUNNING" = true ]; then
    echo "[$(date)] Restarting hummingbird service..."
    sudo systemctl start hummingbird
fi

echo ""
echo "═══════════════════════════════════════════════"
echo "  Restore complete!"
echo "  Pre-restore snapshot saved to: ${SNAPSHOT_DIR}"
echo "═══════════════════════════════════════════════"
