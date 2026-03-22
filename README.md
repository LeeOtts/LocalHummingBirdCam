# Backyard Hummers

*Where the birds are fast and the jokes are faster.*

A Raspberry Pi-powered hummingbird feeder camera that catches those little showoffs in action. When a Ruby-throated Hummingbird swings by for a drink, the system records a 30-second clip with sound, gets ChatGPT to write something inappropriately funny about it, and posts it straight to the **Backyard Hummers** Facebook page. Because even hummingbirds deserve a social media presence.

## What This Bad Boy Does

- **Catches hummingbirds, not squirrels** — 3-stage detection pipeline means virtually zero false alarms. Motion filter, color filter, then a local AI bird classifier confirms it's actually a hummingbird before recording.
- **Records the good stuff with sound** — 30-second clips (10s before + 20s after detection) via USB camera with mic. You'll hear them humming.
- **Gets ChatGPT to talk dirty** — GPT-4o writes cheeky, on-brand captions for the Backyard Hummers page. Double entendres included.
- **Posts to Facebook automatically** — clip + caption, straight to your page. Sit back and let the engagement roll in.
- **Live dashboard** — watch the feed, see detections in real-time, manage clips, toggle posting on/off. All from your phone.
- **Trains itself** — see a hummingbird the system missed? Hit "I See a Hummingbird!" to save the frame. Building a smarter bird brain one click at a time.
- **Auto-updates** — push code to GitHub from your couch, the Pi picks it up and restarts. No SSH required.

## Hardware Shopping List

- Raspberry Pi 3B+ (or newer)
- USB webcam with built-in mic
- 32GB+ SD card
- Power supply
- Hummingbird feeder (the real star of the show)

## Fresh Install (Start Here)

### 1. Flash the SD Card

Grab [Raspberry Pi Imager](https://www.raspberrypi.com/software/):
- **OS**: Raspberry Pi OS (64-bit) Lite
- **Settings**: hostname `hummingbirdcam`, username `pi`, WiFi, enable SSH

### 2. Plug In & Connect

Plug in the USB camera, power on, wait 2 minutes:

```bash
ssh pi@hummingbirdcam.local
```

### 3. One-Liner Install

```bash
sudo apt install -y git && git clone https://github.com/LeeOtts/LocalHummingBirdCam.git && cd LocalHummingBirdCam && python3 -m venv --system-site-packages venv && source venv/bin/activate && pip install --upgrade pip && pip install -r requirements.txt
```

### 4. Set Up the Services

```bash
cd ~/LocalHummingBirdCam
sudo cp scripts/hummingbird.service /etc/systemd/system/
sudo cp scripts/hummingbird-updater.service /etc/systemd/system/
sudo cp scripts/hummingbird-updater.timer /etc/systemd/system/
sudo cp scripts/hummingbird-sudoers /etc/sudoers.d/hummingbird
sudo chmod 440 /etc/sudoers.d/hummingbird
chmod +x scripts/auto_update.sh
sudo systemctl daemon-reload
sudo systemctl enable hummingbird
sudo systemctl enable hummingbird-updater.timer
sudo systemctl start hummingbird-updater.timer
```

### 5. Add Your API Keys

```bash
cp .env.example .env
nano .env
```

Drop in your OpenAI key:
```
OPENAI_API_KEY=sk-your-key-here
```

### 6. Set Up Facebook (The Fun Part)

```bash
source venv/bin/activate
python scripts/setup_facebook_token.py
```

Grab these from [Meta Developer Dashboard](https://developers.facebook.com):
- **App ID** and **App Secret** — Settings > Basic
- **Short-lived token** — from the [Graph API Explorer](https://developers.facebook.com/tools/explorer/) with `pages_manage_posts` and `pages_read_engagement` permissions

The script swaps it for a **permanent** token and saves it. One and done.

### 7. Fire It Up

```bash
sudo systemctl start hummingbird
```

### 8. Check the Dashboard

```
http://hummingbirdcam.local:8080
```

You'll see the live feed, detection stats, clips, and all the controls. Starts in **Test Mode** (no Facebook posting) so you can make sure everything looks good first.

## How It Catches Them

The detection pipeline has 3 stages — cheap and fast first, smart second:

1. **Motion + Color** *(every frame, instant)* — looks for small moving objects with hummingbird colors (iridescent green, ruby red, orange). Needs 5 frames in a row to trigger. Ignores wind, shadows, and your neighbor walking by.

2. **Bird Species Classifier** *(local AI, ~1-2 sec on Pi)* — an EfficientNetB2 model trained on 525 bird species. Specifically looks for **Ruby-throated Hummingbird**. Runs entirely on the Pi, no internet needed. If it says "that's a cardinal," the recording doesn't happen.

3. **Record + Post** — captures 30 seconds of video with audio via ffmpeg, ChatGPT writes something witty, and it goes straight to Facebook.

## The Dashboard

Hit `http://hummingbirdcam.local:8080` and you get:

- **Live camera feed** with real-time detection overlay
  - Green border + glow = **hummingbird confirmed, recording**
  - Yellow border = **motion detected, building frames**
  - Blue border = **verifying with classifier**
  - Red border = **rejected, not a hummingbird**
- **Training buttons** — "I See a Hummingbird!" / "Not a Hummingbird" to save labeled frames
- **Status cards** — uptime, detections, rejections, posts, clips, camera type, cooldown, test mode
- **Recent clips** — watch, see the caption, delete individually or nuke them all
- **Test mode toggle** — flip Facebook posting on/off without touching config
- **Check for Update** — pull latest code and restart instantly
- **Logs** — see what's happening under the hood

## Configuration

Everything lives in `.env`:

| Setting | Default | What It Does |
|---|---|---|
| `OPENAI_API_KEY` | | Your OpenAI key for captions |
| `FACEBOOK_PAGE_ID` | | Your Facebook page ID |
| `FACEBOOK_PAGE_ACCESS_TOKEN` | | Permanent page token |
| `CAMERA_TYPE` | `usb` | `usb`, `picamera`, or `auto` |
| `USB_CAMERA_INDEX` | `0` | Which `/dev/video` to use |
| `AUDIO_ENABLED` | `true` | Record sound with clips |
| `AUDIO_DEVICE` | `default` | ALSA mic device (`arecord -l` to list) |
| `VISION_VERIFY_ENABLED` | `true` | Use bird classifier to confirm |
| `TEST_MODE` | `true` | Skip Facebook posting |
| `MOTION_THRESHOLD` | `15.0` | Motion sensitivity |
| `COLOR_MIN_AREA` | `300` | Min hummingbird-colored pixels |
| `COLOR_MAX_AREA` | `5000` | Max (rejects big stuff) |
| `DETECTION_COOLDOWN_SECONDS` | `60` | Chill time between detections |
| `MAX_POSTS_PER_DAY` | `10` | Don't spam the page |
| `CLIP_PRE_SECONDS` | `10` | Buffer before detection |
| `CLIP_POST_SECONDS` | `20` | Record after detection |
| `WEB_PORT` | `8080` | Dashboard port |

## Cheat Sheet

| What | How |
|---|---|
| Is it running? | `sudo systemctl status hummingbird` |
| Restart | `sudo systemctl restart hummingbird` |
| Stop | `sudo systemctl stop hummingbird` |
| Live logs | `journalctl -u hummingbird -f` |
| Update logs | `journalctl -t hummingbird-updater` |
| Camera detected? | `ls /dev/video*` |
| List mics | `arecord -l` |
| Pi temp | `vcgencmd measure_temp` |
| Dashboard | `http://hummingbirdcam.local:8080` |

## What's Where

```
LocalHummingBirdCam/
├── main.py                  # The brains
├── config.py                # All the knobs
├── camera/
│   ├── stream.py            # USB + Pi Camera support
│   └── recorder.py          # ffmpeg recording with audio
├── detection/
│   ├── motion_color.py      # Fast motion + color filter
│   └── vision_verify.py     # EfficientNetB2 bird classifier
├── social/
│   ├── comment_generator.py # ChatGPT caption magic
│   └── facebook_poster.py   # Posts to Facebook
├── web/
│   └── dashboard.py         # The pretty dashboard
├── scripts/
│   ├── setup_facebook_token.py  # Facebook token helper
│   ├── auto_update.sh           # Git pull + restart
│   ├── hummingbird.service      # systemd service
│   └── ...
├── clips/                   # Your hummingbird videos
├── training/                # Labeled frames for future training
│   ├── hummingbird/
│   └── not_hummingbird/
└── logs/                    # App logs
```

---

*Built for the Backyard Hummers Facebook page. If you're a hummingbird reading this — you look great today.*
