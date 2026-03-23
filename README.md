# Backyard Hummers

*Where the birds are fast and the jokes are faster.*

A Raspberry Pi-powered hummingbird feeder camera that catches those little showoffs in action. When a Ruby-throated Hummingbird swings by for a drink, the system records a 30-second clip with sound, gets GPT-4o to write something inappropriately funny about it, and posts it straight to the **Backyard Hummers** Facebook page.

My wife mentioned she saw an AI-powered hummingbird feeder camera online. I looked at the Raspberry Pi collecting dust on my desk and said "I can build that." So here we are.

## What It Does

- **Catches hummingbirds, not leaves** — 3-stage detection pipeline: motion filter, color filter, then a local AI bird species classifier confirms it's actually a Ruby-throated Hummingbird before recording
- **Records with sound** — 30-second clips (10s before + 20s after detection) via USB camera with mic
- **GPT-4o writes the captions** — cheeky, on-brand captions for the Backyard Hummers page via Azure OpenAI
- **Posts to Facebook automatically** — clip + caption, straight to your page with daily rate limiting
- **Live dashboard** — watch the feed, hear live audio, see detections in real-time, manage clips, toggle posting on/off
- **Night mode** — auto sleep/wake based on sunrise and sunset at your location
- **Good morning / goodnight posts** — daily greeting and recap with hummer tallies
- **Trains itself** — see a hummingbird the system missed? Hit "I See a Hummingbird!" to save the frame for future training
- **One-button updates** — push code to GitHub, hit "Check for Update" on the dashboard, done
- **Graceful failures** — camera unplugged? Dashboard still works, shows the error, and auto-recovers when you plug it back in

## Hardware

- Raspberry Pi 3B+ (or newer)
- USB webcam with built-in mic
- 32GB+ SD card
- Power supply (5V/2.5A minimum)
- Hummingbird feeder (the real star of the show)

## Fresh Install

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
sudo cp scripts/hummingbird-sudoers /etc/sudoers.d/hummingbird
sudo chmod 440 /etc/sudoers.d/hummingbird
chmod +x scripts/auto_update.sh
sudo systemctl daemon-reload
sudo systemctl enable hummingbird
```

### 5. Add Your API Keys

```bash
cp .env.example .env
nano .env
```

For **Azure OpenAI** (recommended):
```
OPENAI_API_KEY=your-azure-api-key
AZURE_OPENAI_ENDPOINT=https://your-resource.cognitiveservices.azure.com/
AZURE_OPENAI_DEPLOYMENT=gpt-4o
AZURE_OPENAI_API_VERSION=2024-12-01-preview
```

For **direct OpenAI**:
```
OPENAI_API_KEY=sk-your-key-here
```

### 6. Set Up Facebook

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

Starts in **Test Mode** (no Facebook posting) so you can make sure everything works first.

## How It Catches Them

The detection pipeline has 3 stages — cheap and fast first, smart second:

1. **Motion + Color** *(every frame, ~1ms)* — looks for small moving objects with hummingbird colors (iridescent green, ruby red, orange). Needs 5 consecutive frames to trigger. Ignores wind, shadows, and your neighbor walking by.

2. **Bird Species Classifier** *(local AI, ~1-2 sec on Pi)* — a MobileNetV2 model trained on 964 bird species via iNaturalist. Specifically looks for **Ruby-throated Hummingbird** with 25% minimum confidence. Runs entirely on the Pi via TFLite, no internet needed.

3. **Record + Post** — captures 30 seconds of video with audio, GPT-4o writes something witty, and it goes to Facebook.

## The Dashboard

Hit `http://hummingbirdcam.local:8080` and you get:

- **Live camera feed** with real-time detection overlay and live audio (muted by default)
  - Green border = hummingbird confirmed, recording
  - Yellow border = motion detected
  - Blue border = verifying with classifier
  - Red border = rejected, not a hummingbird
  - Purple border = sleeping (night mode)
  - Red glow = camera error
- **Camera controls** — rotation (0/90/180/270), test record, test mic
- **Training buttons** — "I See a Hummingbird!" / "Not a Hummingbird" to save labeled frames
- **Recent clips** — watch, see the caption, delete individually or all at once
- **App Status** — uptime, detections, rejections, posts, clips, cooldown, test mode toggle, version, schedule, update button
- **Hardware** — CPU temperature, RAM usage, SD card space, camera status
- **Logs** — filterable (all/errors/warnings/info), clearable, newest first

## Configuration

Everything lives in `.env`. See `.env.example` for all options:

| Setting | Default | What It Does |
|---|---|---|
| `OPENAI_API_KEY` | | API key (Azure or OpenAI) |
| `AZURE_OPENAI_ENDPOINT` | | Azure endpoint URL (leave blank for direct OpenAI) |
| `AZURE_OPENAI_DEPLOYMENT` | `gpt-4o` | Azure model deployment name |
| `AZURE_OPENAI_API_VERSION` | `2024-12-01-preview` | Azure API version |
| `FACEBOOK_PAGE_ID` | | Your Facebook page ID |
| `FACEBOOK_PAGE_ACCESS_TOKEN` | | Permanent page token |
| `CAMERA_TYPE` | `usb` | `usb`, `picamera`, or `auto` |
| `USB_CAMERA_INDEX` | `0` | Which `/dev/video` to use |
| `CAMERA_ROTATION` | `0` | 0, 90, 180, 270 degrees |
| `AUDIO_ENABLED` | `true` | Record sound with clips |
| `AUDIO_DEVICE` | `default` | ALSA mic device (`arecord -l` to list) |
| `VISION_VERIFY_ENABLED` | `true` | Use bird classifier to confirm |
| `TEST_MODE` | `true` | Skip Facebook posting |
| `MOTION_THRESHOLD` | `15.0` | Motion sensitivity |
| `COLOR_MIN_AREA` | `300` | Min hummingbird-colored pixels |
| `COLOR_MAX_AREA` | `5000` | Max (rejects big objects like shirts) |
| `DETECTION_COOLDOWN_SECONDS` | `60` | Seconds between detections |
| `MAX_POSTS_PER_DAY` | `10` | Daily Facebook post limit |
| `CLIP_PRE_SECONDS` | `10` | Buffer before detection |
| `CLIP_POST_SECONDS` | `20` | Record after detection |
| `NIGHT_MODE_ENABLED` | `true` | Auto sleep at sunset, wake at sunrise |
| `LOCATION_LAT` | `35.2045` | Your latitude |
| `LOCATION_LNG` | `-89.8740` | Your longitude |
| `LOCATION_TIMEZONE` | `America/Chicago` | Your timezone |
| `LOCATION_NAME` | `Bartlett, TN` | Shown on dashboard |
| `WAKE_BEFORE_SUNRISE_MIN` | `30` | Wake up this many min before sunrise |
| `SLEEP_AFTER_SUNSET_MIN` | `30` | Sleep this many min after sunset |
| `WEB_PORT` | `8080` | Dashboard port |

## Cheat Sheet

| What | How |
|---|---|
| Is it running? | `sudo systemctl status hummingbird` |
| Restart | `sudo systemctl restart hummingbird` |
| Stop | `sudo systemctl stop hummingbird` |
| Live logs | `journalctl -u hummingbird -f` |
| Camera detected? | `ls /dev/video*` |
| List mics | `arecord -l` |
| Pi temp | `vcgencmd measure_temp` |
| Dashboard | `http://hummingbirdcam.local:8080` |

## Project Structure

```
LocalHummingBirdCam/
├── main.py                  # Entry point, detection loop
├── config.py                # All settings from .env
├── schedule.py              # Sunrise/sunset night mode
├── camera/
│   ├── stream.py            # USB + Pi Camera with rotation
│   └── recorder.py          # Video + audio recording via ffmpeg
├── detection/
│   ├── motion_color.py      # Fast motion + color filter
│   └── vision_verify.py     # MobileNetV2 bird classifier (TFLite)
├── social/
│   ├── comment_generator.py # GPT-4o caption generation (Azure/OpenAI)
│   └── facebook_poster.py   # Facebook Graph API video upload
├── web/
│   └── dashboard.py         # Flask dashboard with live feed
├── scripts/
│   ├── setup_facebook_token.py  # Facebook token helper
│   ├── auto_update.sh           # Git pull + restart
│   ├── hummingbird.service      # systemd service
│   └── install_dependencies.sh  # Full system setup
├── models/                  # Bird classifier (downloaded on first run)
├── clips/                   # Recorded hummingbird videos
├── training/                # Labeled frames for future training
│   ├── hummingbird/
│   └── not_hummingbird/
└── logs/                    # App logs with rotation
```

---

*Built for the Backyard Hummers Facebook page. Source code: [github.com/LeeOtts/LocalHummingBirdCam](https://github.com/LeeOtts/LocalHummingBirdCam)*
