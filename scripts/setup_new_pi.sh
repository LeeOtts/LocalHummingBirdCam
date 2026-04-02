#!/usr/bin/env bash
# =============================================================================
# Backyard Hummers — New Pi Bootstrap
#
# Takes a fresh Raspberry Pi OS install to a fully running hummingbird camera
# system with historical data restored from the latest SiteGround backup.
#
# Usage (from any directory on a fresh Pi):
#   curl -fsSL https://raw.githubusercontent.com/<owner>/LocalHummingBirdCam/main/scripts/setup_new_pi.sh | bash
#   — or —
#   bash scripts/setup_new_pi.sh
#
# What it does:
#   1. Installs git and sqlite3 (needed before anything else)
#   2. Clones the repo (or uses existing checkout)
#   3. Sets up SSH key and connects to SiteGround
#   4. Restores database + .env from the latest backup
#   5. Installs all dependencies (venv, systemd services, cron)
#   6. Starts the hummingbird service
#
# The only thing you need: your SiteGround SSH credentials (host, user, port)
# and the SiteGround account password (one-time, for ssh-copy-id).
# =============================================================================

set -euo pipefail

PROJECT_DIR="/home/pi/LocalHummingBirdCam"
REPO_URL="https://github.com/leeotx/LocalHummingBirdCam.git"

echo "╔══════════════════════════════════════════════════════════╗"
echo "║  Backyard Hummers — New Pi Setup                        ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""

# ── Phase 1: System prerequisites ────────────────────────────────
echo "=== Phase 1: System prerequisites ==="
sudo apt update -qq
sudo apt install -y -qq git sqlite3

# ── Phase 2: Get the code ────────────────────────────────────────
echo ""
echo "=== Phase 2: Getting the code ==="
if [ -d "$PROJECT_DIR/.git" ]; then
    echo "Repo already exists at $PROJECT_DIR — pulling latest..."
    git -C "$PROJECT_DIR" pull --ff-only
else
    echo "Cloning repo to $PROJECT_DIR..."
    git clone "$REPO_URL" "$PROJECT_DIR"
fi
cd "$PROJECT_DIR"

# ── Phase 3: SSH key bootstrap ───────────────────────────────────
echo ""
echo "=== Phase 3: SSH key setup ==="
echo "To restore your data, we need SSH access to SiteGround."
echo ""

read -rp "SiteGround SSH host (e.g., ssh123.siteground.us): " SG_HOST
read -rp "SiteGround SSH user: " SG_USER
read -rp "SiteGround SSH port [18765]: " SG_PORT
SG_PORT="${SG_PORT:-18765}"

# Generate SSH key if needed
if [ ! -f ~/.ssh/id_ed25519 ]; then
    echo "Generating SSH key..."
    ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519 -N "" -C "hummers@$(hostname)"
fi

# Deploy key to SiteGround
echo ""
echo "Deploying SSH key to SiteGround (you'll be prompted for your password once)..."
ssh-copy-id -p "$SG_PORT" "${SG_USER}@${SG_HOST}"

# Verify connectivity
echo "Verifying SSH connection..."
if ssh -p "$SG_PORT" -o StrictHostKeyChecking=accept-new "${SG_USER}@${SG_HOST}" "echo 'SSH OK'"; then
    echo "SSH connection verified!"
else
    echo "ERROR: Could not connect to SiteGround. Check credentials and try again."
    exit 1
fi

# ── Phase 4: Restore from backup ────────────────────────────────
echo ""
echo "=== Phase 4: Restoring data from backup ==="

# Export SSH config so restore script can use it (before .env exists)
export WEBSITE_REMOTE_HOST="$SG_HOST"
export WEBSITE_REMOTE_USER="$SG_USER"
export WEBSITE_REMOTE_PORT="$SG_PORT"

# Create data directory
mkdir -p data

# Restore the latest backup (non-interactive)
bash scripts/restore_from_backup.sh --latest --no-confirm

# Verify .env was restored
if [ ! -f .env ]; then
    echo ""
    echo "WARNING: No .env found in backup. Creating from template..."
    cp .env.example .env
    echo "You'll need to fill in API keys manually: nano $PROJECT_DIR/.env"
fi

# Make sure .env has the correct SiteGround SSH config (in case it changed)
# Update or add WEBSITE_REMOTE_HOST/USER/PORT
for VAR_NAME in WEBSITE_REMOTE_HOST WEBSITE_REMOTE_USER WEBSITE_REMOTE_PORT; do
    VAR_VAL="${!VAR_NAME}"
    if grep -q "^${VAR_NAME}=" .env 2>/dev/null; then
        sed -i "s|^${VAR_NAME}=.*|${VAR_NAME}=${VAR_VAL}|" .env
    else
        echo "${VAR_NAME}=${VAR_VAL}" >> .env
    fi
done

# ── Phase 5: Install dependencies ───────────────────────────────
echo ""
echo "=== Phase 5: Installing dependencies ==="
bash scripts/install_dependencies.sh

# ── Phase 6: Install cron jobs ───────────────────────────────────
echo ""
echo "=== Phase 6: Installing cron jobs ==="
sudo bash scripts/install_crons.sh

# ── Phase 7: Start and verify ───────────────────────────────────
echo ""
echo "=== Phase 7: Starting service ==="
sudo systemctl start hummingbird

sleep 3

if sudo systemctl is-active --quiet hummingbird; then
    STATUS="RUNNING"
else
    STATUS="NOT RUNNING (check: sudo journalctl -u hummingbird -n 50)"
fi

# Count restored sightings
DB_COUNT=$(sqlite3 data/sightings.db "SELECT COUNT(*) FROM sightings" 2>/dev/null || echo "?")

echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║  Setup Complete!                                        ║"
echo "╠══════════════════════════════════════════════════════════╣"
echo "║  Service:    ${STATUS}"
echo "║  Sightings:  ${DB_COUNT} records restored"
echo "║  Dashboard:  http://$(hostname -I | awk '{print $1}'):8080"
echo "║  Cron:       sync every 5min, backup daily at 2AM"
echo "╠══════════════════════════════════════════════════════════╣"
echo "║  Next steps:                                            ║"
echo "║  1. Plug in USB camera:  ls /dev/video*                 ║"
echo "║  2. Check logs:  sudo journalctl -fu hummingbird        ║"
echo "║  3. Verify .env API keys:  nano $PROJECT_DIR/.env"
echo "╚══════════════════════════════════════════════════════════╝"
