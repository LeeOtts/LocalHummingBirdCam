#!/bin/bash
# Install system and Python dependencies for Backyard Hummers on Raspberry Pi
set -e

echo "=== Backyard Hummers - Dependency Installer ==="

# Detect current user and project directory
CURRENT_USER="$(whoami)"
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
HOME_DIR="$(eval echo ~$CURRENT_USER)"

echo "User:    $CURRENT_USER"
echo "Home:    $HOME_DIR"
echo "Project: $PROJECT_DIR"
echo ""

# System packages
echo "Installing system packages..."
sudo apt update
sudo apt install -y \
    python3-pip \
    python3-venv \
    python3-dev \
    ffmpeg \
    v4l-utils \
    libatlas-base-dev

# Try to install picamera2 packages (won't fail if unavailable)
echo "Attempting to install Pi Camera packages (optional)..."
sudo apt install -y python3-libcamera python3-picamera2 libcamera-dev 2>/dev/null || \
    echo "Pi Camera packages not available — USB camera only"

# Create virtual environment
cd "$PROJECT_DIR"

if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv --system-site-packages venv
fi

echo "Installing Python packages..."
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# Create .env from example if it doesn't exist
if [ ! -f ".env" ]; then
    cp .env.example .env
    echo ""
    echo "Created .env file — edit it with your API keys:"
    echo "  nano $PROJECT_DIR/.env"
fi

# Create directories
mkdir -p clips logs models

# ---- Generate systemd services with correct paths ----

echo "Installing systemd services for user '$CURRENT_USER'..."

sudo bash -c "cat > /etc/systemd/system/hummingbird.service << SERVICEEOF
[Unit]
Description=Backyard Hummers - Hummingbird Feeder Camera
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$CURRENT_USER
WorkingDirectory=$PROJECT_DIR
EnvironmentFile=$PROJECT_DIR/.env
ExecStart=$PROJECT_DIR/venv/bin/python main.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
SERVICEEOF"

sudo bash -c "cat > /etc/systemd/system/hummingbird-updater.service << SERVICEEOF
[Unit]
Description=Backyard Hummers - Auto Update Check
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=$CURRENT_USER
ExecStart=$PROJECT_DIR/scripts/auto_update.sh
SERVICEEOF"

sudo bash -c "cat > /etc/systemd/system/hummingbird-updater.timer << SERVICEEOF
[Unit]
Description=Check for Backyard Hummers updates every 2 minutes

[Timer]
OnBootSec=60
OnUnitActiveSec=120
AccuracySec=30

[Install]
WantedBy=timers.target
SERVICEEOF"

# Sudoers — allow this user to restart the service without a password
sudo bash -c "cat > /etc/sudoers.d/hummingbird << SUDOEOF
$CURRENT_USER ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart hummingbird
$CURRENT_USER ALL=(ALL) NOPASSWD: /usr/bin/systemctl stop hummingbird
$CURRENT_USER ALL=(ALL) NOPASSWD: /usr/bin/systemctl start hummingbird
SUDOEOF"
sudo chmod 440 /etc/sudoers.d/hummingbird

# Make auto-update script executable and patch it with correct path
chmod +x scripts/auto_update.sh

# Enable services
sudo systemctl daemon-reload
sudo systemctl enable hummingbird
sudo systemctl enable hummingbird-updater.timer
sudo systemctl start hummingbird-updater.timer

echo ""
echo "=== Installation complete! ==="
echo ""
echo "Services installed (configured for user '$CURRENT_USER'):"
echo "  - hummingbird          : main camera service (auto-starts on boot)"
echo "  - hummingbird-updater  : checks GitHub every 2 min, auto-restarts on new commits"
echo ""
echo "Next steps:"
echo "  1. Edit .env with your API keys:  nano $PROJECT_DIR/.env"
echo "  2. Plug in USB camera and check:  ls /dev/video*"
echo "  3. Start the app:                 sudo systemctl start hummingbird"
echo "  4. View dashboard:                http://$(hostname -I | awk '{print $1}'):8080"
echo "  5. Check updater status:          systemctl status hummingbird-updater.timer"
echo ""
