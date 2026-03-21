#!/bin/bash
# Auto-update Backyard Hummers from GitHub
# Checks for new commits, pulls changes, reinstalls deps if needed, and restarts the service.
#
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

# Pull the latest changes
git pull origin "$BRANCH" --ff-only
if [ $? -ne 0 ]; then
    logger -t "$LOG_TAG" "ERROR: git pull failed, skipping update"
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

# Restart the service
logger -t "$LOG_TAG" "Restarting hummingbird service..."
sudo systemctl restart hummingbird

logger -t "$LOG_TAG" "Update complete! Now running $NEW_HEAD"
