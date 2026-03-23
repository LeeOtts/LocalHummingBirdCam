# Backyard Hummers

*Bartlett's finest backyard hummers.*

A Raspberry Pi-powered hummingbird feeder camera that turns your backyard into a tiny AI-powered media empire.

When a Ruby-throated Hummingbird swings by for a drink, the system:
- records a 30-second clip (with sound)
- lets GPT-4o write something cheeky about it
- and posts it straight to the **Backyard Hummers** Facebook page

Follow the chaos here: [facebook.com/backyard.hummers](https://www.facebook.com/backyard.hummers)

My wife showed me an AI hummingbird cam online. I looked at the Raspberry Pi collecting dust on my desk and said:

> "Hold my nectar — I can do that."

...and now I accidentally run Bartlett's premier hummer surveillance operation.

## What It Does

- **Catches hummingbirds, not leaves** — 3-stage detection pipeline: motion, color, AI classifier (after an embarrassing number of false alarms involving wind, shadows, and betrayal)
- **Records with sound** — 30-second clips (10s before + 20s after detection) because the wing buzz is half the drama
- **GPT-4o writes the captions** — chaotic, slightly unhinged, occasionally better than mine
- **Posts to Facebook automatically** — your hummers, their moment of fame
- **Live dashboard** — watch the feed, hear audio, and feel like you run a wildlife surveillance agency
- **Night mode** — auto sleep/wake based on sunrise and sunset (the birds rest... eventually so do I)
- **Good morning / goodnight posts** — daily check-ins with hummer stats
- **Trains itself** — teach it "bird vs leaf vs absolute nonsense"
- **One-button updates** — because I refuse to pretend I enjoy manual deployments
- **Graceful failures** — camera unplugged? It complains politely and fixes itself later

## Hardware

- Raspberry Pi 3B+ (or newer)
- USB webcam with built-in mic
- 32GB+ SD card
- Power supply (5V/2.5A minimum)
- Hummingbird feeder (the actual MVP)

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

Grab your **App ID**, **App Secret**, and **short-lived token**. The script converts it to a permanent token.

### 7. Fire It Up

```bash
sudo systemctl start hummingbird
```

### 8. Check the Dashboard

```
http://hummingbirdcam.local:8080
```

Starts in **Test Mode** so you don't accidentally spam your page.

## How It Catches Them

The detection pipeline is basically a bouncer for birds:

1. **Motion + Color** *(~1ms)* — Fast, cheap, slightly paranoid. Doesn't freak out over every leaf anymore.

2. **Bird Species Classifier** *(~1-2 sec on Pi)* — The "are you actually a hummingbird?" check. MobileNetV2 trained on 964 bird species. Fully local, no cloud needed.

3. **Record + Post** — 30 seconds of fame. GPT-4o adds commentary. Internet gets another hummer clip.

## The Dashboard

Hit `http://hummingbirdcam.local:8080` and welcome to mission control:

- **Live feed** with overlays and audio (yes, you can hear them judging you)
- **Detection states:**
  - Green = hummingbird confirmed
  - Yellow = motion detected
  - Blue = verifying
  - Red = rejected
  - Purple = sleeping
  - Red glow = camera error
- **Camera controls** — rotation, test recording, mic test
- **Training buttons** — help the AI get smarter
- **Recent clips** — review, delete, admire your regulars
- **App status** — uptime, detections, posts, cooldowns, version, schedule
- **Hardware stats** — CPU temp, RAM, disk space
- **Logs** — because something always breaks eventually

## Configuration

Everything lives in `.env`. See `.env.example` for full details.

| Setting | Default | What It Does |
|---|---|---|
| `OPENAI_API_KEY` | | API key (Azure or OpenAI) |
| `AZURE_OPENAI_ENDPOINT` | | Azure endpoint URL (blank = direct OpenAI) |
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
| `COLOR_MAX_AREA` | `5000` | Max (rejects big objects) |
| `DETECTION_COOLDOWN_SECONDS` | `60` | Seconds between detections |
| `MAX_POSTS_PER_DAY` | `10` | Daily Facebook post limit |
| `CLIP_PRE_SECONDS` | `10` | Buffer before detection |
| `CLIP_POST_SECONDS` | `20` | Record after detection |
| `NIGHT_MODE_ENABLED` | `true` | Auto sleep at sunset, wake at sunrise |
| `LOCATION_LAT` | `35.2045` | Your latitude |
| `LOCATION_LNG` | `-89.8740` | Your longitude |
| `LOCATION_TIMEZONE` | `America/Chicago` | Your timezone |
| `LOCATION_NAME` | `Bartlett, TN` | Shown on dashboard |
| `WAKE_BEFORE_SUNRISE_MIN` | `30` | Wake up early |
| `SLEEP_AFTER_SUNSET_MIN` | `30` | Stay up late |
| `WEB_PORT` | `8080` | Dashboard port |

## Cheat Sheet

| What | How |
|---|---|
| Is it running? | `sudo systemctl status hummingbird` |
| Restart | `sudo systemctl restart hummingbird` |
| Stop | `sudo systemctl stop hummingbird` |
| Live logs | `journalctl -u hummingbird -f` |
| Camera check | `ls /dev/video*` |
| List mics | `arecord -l` |
| Pi temp | `vcgencmd measure_temp` |
| Dashboard | `http://hummingbirdcam.local:8080` |

## Project Structure

```
LocalHummingBirdCam/
├── main.py                  # The brains
├── config.py                # All the knobs
├── schedule.py              # Sunrise/sunset night mode
├── camera/
│   ├── stream.py            # USB + Pi Camera with rotation
│   └── recorder.py          # Video + audio recording via ffmpeg
├── detection/
│   ├── motion_color.py      # Fast motion + color filter
│   └── vision_verify.py     # MobileNetV2 bird classifier (TFLite)
├── social/
│   ├── comment_generator.py # GPT-4o caption generation
│   └── facebook_poster.py   # Facebook Graph API video upload
├── web/
│   └── dashboard.py         # Flask dashboard with live feed
├── scripts/
│   ├── setup_facebook_token.py  # Facebook token helper
│   ├── auto_update.sh           # Git pull + restart
│   ├── hummingbird.service      # systemd service
│   └── install_dependencies.sh  # Full system setup
├── models/                  # Bird classifier (downloaded on first run)
├── clips/                   # Your hummingbird videos
├── training/                # Labeled frames for future training
└── logs/                    # App logs with rotation
```

## Final Notes

Took an embarrassing number of false alarms before it stopped getting emotionally invested in leaves.

Yes, this is completely over-engineered. No, I do not regret it.

Watch the birds in action: [facebook.com/backyard.hummers](https://www.facebook.com/backyard.hummers)

Source code: [github.com/LeeOtts/LocalHummingBirdCam](https://github.com/LeeOtts/LocalHummingBirdCam)
