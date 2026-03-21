#!/bin/bash
# Install system and Python dependencies for Backyard Hummers on Raspberry Pi
# Username: pi
set -e

echo "=== Backyard Hummers - Dependency Installer ==="

PROJECT_DIR="/home/pi/LocalHummingBirdCam"
cd "$PROJECT_DIR"

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

# Try to install picamera2 packages (optional, for Pi Camera Module)
echo "Attempting to install Pi Camera packages (optional)..."
sudo apt install -y python3-libcamera python3-picamera2 libcamera-dev 2>/dev/null || \
    echo "Pi Camera packages not available — USB camera only"

# Create virtual environment
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

# Install systemd services
echo "Installing systemd services..."
sudo cp scripts/hummingbird.service /etc/systemd/system/
sudo cp scripts/hummingbird-updater.service /etc/systemd/system/
sudo cp scripts/hummingbird-updater.timer /etc/systemd/system/

# Sudoers — allow pi to restart the service without a password
sudo cp scripts/hummingbird-sudoers /etc/sudoers.d/hummingbird
sudo chmod 440 /etc/sudoers.d/hummingbird

# Make auto-update script executable
chmod +x scripts/auto_update.sh

# Enable services
sudo systemctl daemon-reload
sudo systemctl enable hummingbird
sudo systemctl enable hummingbird-updater.timer
sudo systemctl start hummingbird-updater.timer

echo ""
echo "=== Installation complete! ==="
echo ""
echo "Next steps:"
echo "  1. Edit .env with your API keys:  nano $PROJECT_DIR/.env"
echo "  2. Plug in USB camera and check:  ls /dev/video*"
echo "  3. Start the app:                 sudo systemctl start hummingbird"
echo "  4. View dashboard:                http://$(hostname -I | awk '{print $1}'):8080"
echo "  5. Check updater status:          systemctl status hummingbird-updater.timer"
echo ""
