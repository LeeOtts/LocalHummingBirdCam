#!/usr/bin/env bash
# =============================================================================
# Backyard Hummers — Install cron jobs (idempotent)
#
# Writes /etc/cron.d/hummingbird with sync + backup schedules.
# Safe to run multiple times — always overwrites with the correct config.
#
# Usage:
#   sudo bash scripts/install_crons.sh
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

CRON_FILE="/etc/cron.d/hummingbird"

echo "Installing cron jobs to ${CRON_FILE}..."

cat > "$CRON_FILE" <<EOF
# Backyard Hummers — automated schedules
# Managed by scripts/install_crons.sh — do not edit manually
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

# Sync clips + website data to SiteGround every 5 minutes
*/5 * * * * pi ${PROJECT_DIR}/scripts/sync_to_site.sh >> /tmp/hummers_sync.log 2>&1

# Daily backup at 2 AM
0 2 * * * pi ${PROJECT_DIR}/scripts/backup_to_site.sh >> /tmp/hummers_backup.log 2>&1
EOF

chmod 644 "$CRON_FILE"
echo "Cron jobs installed:"
cat "$CRON_FILE"
