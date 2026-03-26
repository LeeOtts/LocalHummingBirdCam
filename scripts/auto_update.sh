#!/bin/bash
# Auto-update Backyard Hummers from GitHub
# Detects project directory dynamically from script location.
# Runs via systemd timer every 2 minutes.

PROJECT_DIR="/home/pi/LocalHummingBirdCam"
BRANCH="main"
LOG_TAG="hummingbird-updater"

cd "$PROJECT_DIR" || exit 1

# Fetch latest from remote
git fetch origin "$BRANCH" --quiet 2>/dev/null

# Compare local HEAD vs remote HEAD
LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse "origin/$BRANCH")

if [ "$LOCAL" = "$REMOTE" ]; then
    exit 0
fi

logger -t "$LOG_TAG" "New commit detected: $LOCAL -> $REMOTE"

# Reset to remote HEAD (avoids conflicts when tracked files like this script change)
git reset --hard "origin/$BRANCH"
if [ $? -ne 0 ]; then
    logger -t "$LOG_TAG" "ERROR: git reset failed, skipping update"
    exit 1
fi

NEW_HEAD=$(git rev-parse --short HEAD)
COMMIT_MSG=$(git log -1 --pretty=%s)
logger -t "$LOG_TAG" "Updated to $NEW_HEAD: $COMMIT_MSG"

# Reinstall Python deps if requirements.txt changed
if git diff "$LOCAL" "$REMOTE" --name-only | grep -q "requirements.txt"; then
    logger -t "$LOG_TAG" "requirements.txt changed, reinstalling dependencies..."
    source "$PROJECT_DIR/venv/bin/activate"
    pip install -r requirements.txt --quiet
fi

# Re-run install script if install_dependencies.sh changed (updates service files)
if git diff "$LOCAL" "$REMOTE" --name-only | grep -q "install_dependencies.sh"; then
    logger -t "$LOG_TAG" "install script changed, re-running installer..."
    bash "$PROJECT_DIR/scripts/install_dependencies.sh"
fi

# Restart the service
logger -t "$LOG_TAG" "Restarting hummingbird service..."
sudo systemctl restart hummingbird

# Restart Tailscale daemon if its setup script changed
if command -v tailscale &>/dev/null; then
    if git diff "$LOCAL" "$REMOTE" --name-only | grep -q "setup_tailscale.sh"; then
        logger -t "$LOG_TAG" "Tailscale setup script changed, restarting tailscaled..."
        sudo systemctl restart tailscaled 2>/dev/null || true
    fi
fi

logger -t "$LOG_TAG" "Update complete! Now running $NEW_HEAD"
