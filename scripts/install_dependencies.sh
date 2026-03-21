#!/bin/bash
# Install system and Python dependencies for Backyard Hummers on Raspberry Pi
set -e

echo "=== Backyard Hummers - Dependency Installer ==="

# System packages
echo "Installing system packages..."
sudo apt update
sudo apt install -y \
    python3-pip \
    python3-venv \
    python3-libcamera \
    python3-picamera2 \
    libcamera-dev \
    ffmpeg

# Set GPU memory (needed for camera + H264 encoding)
if ! grep -q "gpu_mem=256" /boot/config.txt 2>/dev/null; then
    echo "Setting GPU memory to 256MB..."
    echo "gpu_mem=256" | sudo tee -a /boot/config.txt
    echo "NOTE: Reboot required for GPU memory change to take effect."
fi

# Create virtual environment
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv --system-site-packages venv
fi

echo "Installing Python packages..."
source venv/bin/activate
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

echo ""
echo "=== Installation complete! ==="
echo ""
echo "Next steps:"
echo "  1. Edit .env with your OpenAI and Facebook API keys"
echo "  2. Test the camera: libcamera-hello"
echo "  3. Run the app: source venv/bin/activate && python main.py"
echo "  4. Install as service: sudo cp scripts/hummingbird.service /etc/systemd/system/"
echo "     sudo systemctl enable hummingbird && sudo systemctl start hummingbird"
