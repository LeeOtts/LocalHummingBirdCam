#!/usr/bin/env bash
# =============================================================================
# Backyard Hummers — Network Watchdog
#
# Detects WiFi connectivity loss and attempts recovery:
#   1. Disables WiFi power management (common RPi brcmfmac fix)
#   2. Pings the default gateway (falls back to 8.8.8.8)
#   3. After 3 consecutive failures (~6 min): restarts wlan0
#   4. After 5 consecutive failures (~10 min): reboots the Pi
#
# Runs via systemd timer every 2 minutes.
# State tracked in /tmp/watchdog_failures across invocations.
#
# Usage: called by network-watchdog.service (not run directly)
# =============================================================================

set -euo pipefail

LOG_TAG="hummingbird-watchdog"
STATE_FILE="/tmp/watchdog_failures"
LOG_FILE="/tmp/hummers_watchdog.log"
WIFI_RESTART_THRESHOLD=3
REBOOT_THRESHOLD=5
INTERFACE="wlan0"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

log() {
    local msg="$1"
    local ts
    ts="$(date '+%Y-%m-%d %H:%M:%S')"
    logger -t "$LOG_TAG" "$msg"
    echo "[$ts] $msg" >> "$LOG_FILE"
}

get_failures() {
    if [[ -f "$STATE_FILE" ]]; then
        cat "$STATE_FILE"
    else
        echo 0
    fi
}

set_failures() {
    echo "$1" > "$STATE_FILE"
}

# ---------------------------------------------------------------------------
# Disable WiFi power management (idempotent, runs every invocation)
# ---------------------------------------------------------------------------

if /usr/sbin/ip link show "$INTERFACE" &>/dev/null; then
    sudo /usr/sbin/iw dev "$INTERFACE" set power_save off 2>/dev/null || true
fi

# ---------------------------------------------------------------------------
# Determine ping target: default gateway, fallback to 8.8.8.8
# ---------------------------------------------------------------------------

GATEWAY=$(/usr/sbin/ip route show default 2>/dev/null | awk '{print $3; exit}')
PING_TARGET="${GATEWAY:-8.8.8.8}"

# ---------------------------------------------------------------------------
# Connectivity check
# ---------------------------------------------------------------------------

if ping -c 3 -W 5 "$PING_TARGET" &>/dev/null; then
    # Network is up
    failures=$(get_failures)
    if [[ "$failures" -gt 0 ]]; then
        log "OK: network recovered (was at $failures consecutive failures)"
    fi
    set_failures 0
    exit 0
fi

# ---------------------------------------------------------------------------
# Ping failed — escalate
# ---------------------------------------------------------------------------

failures=$(get_failures)
failures=$((failures + 1))
set_failures "$failures"

if [[ "$failures" -lt "$WIFI_RESTART_THRESHOLD" ]]; then
    log "WARNING: ping to $PING_TARGET failed (failure $failures/$WIFI_RESTART_THRESHOLD before wlan0 restart)"
    exit 0
fi

if [[ "$failures" -eq "$WIFI_RESTART_THRESHOLD" ]]; then
    log "WARNING: $failures consecutive failures — restarting $INTERFACE"
    sudo /usr/sbin/ip link set "$INTERFACE" down
    sleep 2
    sudo /usr/sbin/ip link set "$INTERFACE" up
    sleep 10

    # Verify recovery
    if ping -c 2 -W 5 "$PING_TARGET" &>/dev/null; then
        log "OK: network recovered after $INTERFACE restart"
        set_failures 0
        exit 0
    fi

    log "WARNING: $INTERFACE restart did not restore connectivity"
    failures=$((failures + 1))
    set_failures "$failures"
    exit 0
fi

if [[ "$failures" -ge "$REBOOT_THRESHOLD" ]]; then
    log "CRITICAL: $failures consecutive failures — rebooting Pi"
    sudo /usr/sbin/reboot
    exit 0
fi

# Between WIFI_RESTART_THRESHOLD and REBOOT_THRESHOLD — keep waiting
log "WARNING: ping to $PING_TARGET failed (failure $failures/$REBOOT_THRESHOLD before reboot)"
