#!/bin/bash
# Setup Tailscale for remote access to Backyard Hummers dashboard + SSH
# Usage:
#   bash scripts/setup_tailscale.sh            # Basic setup with Tailscale SSH
#   bash scripts/setup_tailscale.sh --funnel   # Also enable Tailscale Funnel on port 8080
set -e

ENABLE_FUNNEL=false
for arg in "$@"; do
    case "$arg" in
        --funnel) ENABLE_FUNNEL=true ;;
        *) echo "Unknown option: $arg"; exit 1 ;;
    esac
done

echo "=== Backyard Hummers — Tailscale Remote Access Setup ==="
echo ""

# Check platform
if [[ "$(uname)" != "Linux" ]]; then
    echo "ERROR: This script is intended for Linux (Raspberry Pi)."
    echo "Install Tailscale manually: https://tailscale.com/download"
    exit 1
fi

# Install Tailscale if not already installed
if command -v tailscale &>/dev/null; then
    echo "Tailscale is already installed: $(tailscale version | head -1)"
else
    echo "Installing Tailscale..."
    curl -fsSL https://tailscale.com/install.sh | sh
    echo "Tailscale installed successfully."
fi

# Enable and start the daemon
echo "Enabling tailscaled service..."
sudo systemctl enable --now tailscaled

# Check if already authenticated
if tailscale status &>/dev/null; then
    echo "Tailscale is already connected."
else
    echo ""
    echo "Authenticating with Tailscale (with SSH enabled)..."
    echo "A browser URL will appear below — open it to log in."
    echo ""
    sudo tailscale up --ssh
fi

# Show connection info
echo ""
echo "=== Tailscale Connected ==="
TS_IP=$(tailscale ip -4 2>/dev/null || echo "unknown")
TS_HOSTNAME=$(tailscale status --json 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin)['Self']['DNSName'].rstrip('.'))" 2>/dev/null || echo "unknown")
echo "  Tailscale IP:       $TS_IP"
echo "  Tailscale Hostname: $TS_HOSTNAME"
echo "  Dashboard URL:      http://${TS_IP}:8080"
echo "  SSH:                ssh pi@${TS_HOSTNAME}"

# Enable Funnel if requested
if [ "$ENABLE_FUNNEL" = true ]; then
    echo ""
    echo "Enabling Tailscale Funnel on port 8080..."
    sudo tailscale funnel 8080 &
    FUNNEL_PID=$!
    sleep 2
    echo "Funnel enabled — dashboard is publicly accessible via HTTPS."
    echo "  Note: Funnel runs in the background (PID $FUNNEL_PID)."
    echo "  Stop with: sudo tailscale funnel off"
fi

# Security reminder: check for WEB_PASSWORD
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
if [ -f "$PROJECT_DIR/.env" ]; then
    WEB_PW=$(grep -E "^WEB_PASSWORD=" "$PROJECT_DIR/.env" 2>/dev/null | cut -d= -f2-)
    if [ -z "$WEB_PW" ]; then
        echo ""
        echo "⚠  WARNING: WEB_PASSWORD is not set in .env"
        echo "   Your dashboard is now accessible remotely WITHOUT a password."
        echo "   Set a password:  echo 'WEB_PASSWORD=your-secret' >> $PROJECT_DIR/.env"
    fi
fi

# Remind user to enable in config
echo ""
echo "To show Tailscale status on the dashboard, add to .env:"
echo "  TAILSCALE_ENABLED=true"
echo ""
echo "=== Setup complete! ==="
