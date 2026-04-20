#!/usr/bin/env bash
# =============================================================================
# HLS Stream Encoder — reads raw video from CameraStream pipe, encodes to HLS
#
# The Python CameraStream writes raw BGR frames to /tmp/hls_input.pipe.
# This script reads from that pipe and produces HLS segments via ffmpeg.
# Audio is optionally muxed from the USB microphone (controlled by HLS_AUDIO).
#
# Usage: called by hummingbird-hls.service (not run directly)
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Load config from .env
if [ -f "$PROJECT_DIR/.env" ]; then
    # shellcheck disable=SC1091
    source <(grep -E '^(HLS_|AUDIO_)' "$PROJECT_DIR/.env" | sed 's/^/export /')
fi

# HLS config with defaults
HLS_OUTPUT_DIR="${HLS_OUTPUT_DIR:-/tmp/hls}"
HLS_SEGMENT_TIME="${HLS_SEGMENT_TIME:-4}"
HLS_LIST_SIZE="${HLS_LIST_SIZE:-10}"
HLS_VIDEO_BITRATE="${HLS_VIDEO_BITRATE:-1200k}"
HLS_RESOLUTION="${HLS_RESOLUTION:-1280x720}"
HLS_FRAMERATE="${HLS_FRAMERATE:-10}"
HLS_AUDIO="${HLS_AUDIO:-false}"
AUDIO_DEVICE="${AUDIO_DEVICE:-default}"

# Auto-detect USB audio device (mirrors camera/recorder.py _detect_audio_device)
detect_audio_device() {
    local device="$AUDIO_DEVICE"

    # If user explicitly set a device (not "default"), use it as-is
    if [ "$device" != "default" ]; then
        echo "$device"
        return
    fi

    # Try to auto-detect a USB audio capture device via arecord
    if command -v arecord &>/dev/null; then
        local arecord_output
        arecord_output=$(arecord -l 2>/dev/null) || true

        local line
        while IFS= read -r line; do
            if echo "$line" | grep -qi "card" && echo "$line" | grep -qi "usb"; then
                local card_num device_num
                card_num=$(echo "$line" | sed -n 's/.*card[[:space:]]*\([0-9]*\).*/\1/ip')
                device_num=$(echo "$line" | sed -n 's/.*device[[:space:]]*\([0-9]*\).*/\1/ip')
                if [ -n "$card_num" ] && [ -n "$device_num" ]; then
                    echo "hw:${card_num},${device_num}"
                    return
                fi
            fi
        done <<< "$arecord_output"
    fi

    echo "default"
}

# Validate that an ALSA capture device is accessible
validate_audio_device() {
    local device="$1"

    if [ "$device" = "default" ]; then
        return 0
    fi

    if command -v arecord &>/dev/null; then
        if arecord -D "$device" -d 0 /dev/null 2>/dev/null; then
            return 0
        fi
    fi

    return 1
}

PIPE_PATH="/tmp/hls_input.pipe"
WIDTH="${HLS_RESOLUTION%%x*}"
HEIGHT="${HLS_RESOLUTION##*x}"

# Create output directory (use tmpfs to avoid SD card wear)
mkdir -p "$HLS_OUTPUT_DIR"

echo "[$(date)] HLS encoder starting: ${WIDTH}x${HEIGHT}@${HLS_FRAMERATE}fps, bitrate=${HLS_VIDEO_BITRATE}, audio=${HLS_AUDIO}, audio_device=${AUDIO_DEVICE}"

# Wait for the pipe to exist (CameraStream creates it)
WAIT_COUNT=0
while [ ! -p "$PIPE_PATH" ]; do
    if [ $WAIT_COUNT -ge 60 ]; then
        echo "[$(date)] ERROR: Timed out waiting for $PIPE_PATH"
        exit 1
    fi
    echo "[$(date)] Waiting for camera pipe at $PIPE_PATH..."
    sleep 2
    WAIT_COUNT=$((WAIT_COUNT + 1))
done

# Use libx264 for HLS — h264_v4l2m2m omits SPS/PPS NAL units from the
# bitstream, making segments undecodable in browsers.  libx264 ultrafast
# at 10 fps 720p is well within the Pi 4's CPU budget.
ENCODER="libx264"
ENCODER_OPTS="-preset ultrafast -tune zerolatency -pix_fmt yuv420p"
echo "[$(date)] Using libx264 (browser-compatible HLS)"

# Build ffmpeg command
FFMPEG_CMD=(
    ffmpeg -y -hide_banner -loglevel warning
    # Video input: raw BGR frames from pipe
    -f rawvideo -pix_fmt bgr24 -video_size "${WIDTH}x${HEIGHT}"
    -framerate "$HLS_FRAMERATE" -i "$PIPE_PATH"
)

# Optionally add audio input
if [ "$HLS_AUDIO" = "true" ] || [ "$HLS_AUDIO" = "1" ] || [ "$HLS_AUDIO" = "yes" ]; then
    # Detect and resolve the audio device
    RAW_DEVICE=$(detect_audio_device)
    echo "[$(date)] Audio: detected device '$RAW_DEVICE'"

    # Convert hw: to plughw: for automatic format conversion (matches recorder.py pattern)
    ALSA_DEVICE="${RAW_DEVICE/hw:/plughw:}"

    # Validate the device actually exists before passing to ffmpeg
    if validate_audio_device "$ALSA_DEVICE"; then
        FFMPEG_CMD+=(
            -f alsa -ac 1 -ar 44100 -i "$ALSA_DEVICE"
        )
        AUDIO_OPTS=(-c:a aac -b:a 64k -ar 44100)
        echo "[$(date)] Audio enabled: device='$ALSA_DEVICE'"
    else
        AUDIO_OPTS=(-an)
        echo "[$(date)] WARNING: Audio device '$ALSA_DEVICE' not accessible — falling back to no audio"
    fi
else
    AUDIO_OPTS=(-an)
    echo "[$(date)] Audio disabled"
fi

# Output HLS
FFMPEG_CMD+=(
    -c:v "$ENCODER" $ENCODER_OPTS -b:v "$HLS_VIDEO_BITRATE"
    -g "$((HLS_FRAMERATE * 2))"
    "${AUDIO_OPTS[@]}"
    -f hls
    -hls_time "$HLS_SEGMENT_TIME"
    -hls_list_size "$HLS_LIST_SIZE"
    -hls_flags delete_segments+append_list
    -hls_segment_filename "${HLS_OUTPUT_DIR}/stream_%05d.ts"
    "${HLS_OUTPUT_DIR}/stream.m3u8"
)

echo "[$(date)] Running: ${FFMPEG_CMD[*]}"
exec "${FFMPEG_CMD[@]}"
