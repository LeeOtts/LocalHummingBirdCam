#!/usr/bin/env bash
# =============================================================================
# HLS Sync — push HLS segments + periodic data to SiteGround
#
# Runs a tight rsync loop (every 2-3s) to push .m3u8 + .ts segments.
# Every ~30 seconds, also pushes site_data.json for near-real-time status.
# Uses SSH ControlMaster to avoid per-rsync SSH handshake overhead.
#
# Usage: called by hummingbird-hls.service (not run directly)
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Load config from .env
if [ -f "$PROJECT_DIR/.env" ]; then
    # shellcheck disable=SC1091
    source <(grep -E '^(WEBSITE_|HLS_)' "$PROJECT_DIR/.env" | sed 's/^/export /')
fi

REMOTE_HOST="${WEBSITE_REMOTE_HOST:-}"
REMOTE_USER="${WEBSITE_REMOTE_USER:-}"
REMOTE_PATH="${WEBSITE_REMOTE_PATH:-public_html}"
REMOTE_PORT="${WEBSITE_REMOTE_PORT:-22}"
HLS_OUTPUT_DIR="${HLS_OUTPUT_DIR:-/tmp/hls}"
SITE_DATA="$PROJECT_DIR/website/data/site_data.json"

if [ -z "$REMOTE_HOST" ] || [ -z "$REMOTE_USER" ]; then
    echo "[$(date)] ERROR: WEBSITE_REMOTE_HOST and WEBSITE_REMOTE_USER must be set in .env"
    exit 1
fi

REMOTE="${REMOTE_USER}@${REMOTE_HOST}"
SSH_CTL="/tmp/hls_ssh_ctl"

# Clean up SSH control socket on exit
cleanup() {
    echo "[$(date)] HLS sync stopping..."
    ssh -S "$SSH_CTL" -O exit "$REMOTE" 2>/dev/null || true
    rm -f "$SSH_CTL"
}
trap cleanup EXIT

# Start persistent SSH connection (ControlMaster)
echo "[$(date)] Establishing persistent SSH connection to ${REMOTE}..."
ssh -M -S "$SSH_CTL" -fN \
    -p "$REMOTE_PORT" \
    -o StrictHostKeyChecking=accept-new \
    -o ServerAliveInterval=30 \
    -o ServerAliveCountMax=3 \
    -o ControlPersist=yes \
    "$REMOTE"

echo "[$(date)] SSH ControlMaster connected"

# Ensure remote hls directory exists
ssh -S "$SSH_CTL" "$REMOTE" "mkdir -p ${REMOTE_PATH}/hls" 2>/dev/null || true

SYNC_INTERVAL=2
DATA_SYNC_COUNTER=0
DATA_SYNC_EVERY=15  # Every 15 iterations (30s) push site_data.json

echo "[$(date)] HLS sync loop started (every ${SYNC_INTERVAL}s, data every $((DATA_SYNC_EVERY * SYNC_INTERVAL))s)"

while true; do
    # Sync HLS segments
    if [ -d "$HLS_OUTPUT_DIR" ] && ls "$HLS_OUTPUT_DIR"/*.m3u8 &>/dev/null; then
        rsync -a --timeout=10 \
            -e "ssh -S $SSH_CTL" \
            --include='*.m3u8' \
            --include='*.ts' \
            --exclude='*' \
            "$HLS_OUTPUT_DIR/" \
            "${REMOTE}:${REMOTE_PATH}/hls/" 2>/dev/null || \
            echo "[$(date)] WARNING: HLS rsync failed"
    fi

    # Periodically sync site_data.json for near-real-time status
    DATA_SYNC_COUNTER=$((DATA_SYNC_COUNTER + 1))
    if [ $DATA_SYNC_COUNTER -ge $DATA_SYNC_EVERY ] && [ -f "$SITE_DATA" ]; then
        rsync -a --timeout=10 \
            -e "ssh -S $SSH_CTL" \
            "$SITE_DATA" \
            "${REMOTE}:${REMOTE_PATH}/data/site_data.json" 2>/dev/null || \
            echo "[$(date)] WARNING: site_data rsync failed"
        DATA_SYNC_COUNTER=0
    fi

    sleep "$SYNC_INTERVAL"
done
