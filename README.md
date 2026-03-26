# Backyard Hummers

*AI-powered hummingbird surveillance based in Bartlett. Focused exclusively on Ruby-throated hummingbirds—the only ones fast (and chaotic) enough to matter.*

*Monitoring nectar activity and tracking unauthorized flight in real time.*

A Raspberry Pi with a USB camera, a bird classifier that knows 964 species, and a GPT-4o that writes captions with zero supervision. What could go wrong.

When a Ruby-throated Hummingbird hits the feeder, the system:
- detects it through a multi-stage AI pipeline (motion, color, species classification)
- records a 25-second clip with audio — because the wing buzz is evidence
- GPT-4o analyzes the actual video frames and writes a caption about what the bird is doing
- checks the weather so it can work that into the commentary
- and posts it to **Facebook**, **Twitter/X**, and **Bluesky** simultaneously before the bird even leaves

Follow the operation: [Facebook](https://www.facebook.com/backyard.hummers) | [Bluesky](https://bsky.app/profile/backyardhummers.bsky.social) | [X](https://x.com/backyardhummers) | [Instagram](https://www.instagram.com/backyard.hummers)

My wife showed me an AI hummingbird cam online. I looked at the Raspberry Pi collecting dust on my desk and said:

> "Hold my nectar — I can build that."

...and now I accidentally run a full-blown hummingbird surveillance state out of my backyard in Bartlett, TN.

## What It Does

- **Multi-stage detection pipeline** — motion, HSV color filtering, and a MobileNetV2 bird classifier running locally on the Pi. It went through an embarrassing phase of reporting leaves, shadows, and personal betrayal before we got here.
- **Records with sound** — 25-second clips (5s pre-roll + 20s post-detection) because you need the wing buzz for the full experience
- **GPT-4o writes the captions** — unhinged, slightly suggestive, occasionally better than anything a human would write. Never explains its own jokes.
- **Vision-based captions** — GPT-4o actually looks at the video frames and describes what the bird is doing. Hovering, feeding, fighting, flexing — it sees it all.
- **Weather-aware commentary** — pulls real-time weather from OpenWeatherMap so captions can reference the 97-degree heat or the surprise rain. Context matters.
- **Multi-platform posting** — auto-posts to Facebook, Twitter/X, and Bluesky simultaneously. Configure whichever platforms you want — the system auto-discovers what's enabled and posts everywhere at once.
- **AI comment replies** — GPT-4o monitors Facebook comments and fires back witty replies autonomously. Rate-limited so it doesn't go feral.
- **Morning briefings** — sunrise check-in with yesterday's tally. Posted with a live camera snapshot. The feeders are full. The operation is active.
- **Goodnight recaps** — daily stats, peak activity hour, and whether we broke the all-time record. Celebrates milestones at 100, 250, 500, and 1000+ lifetime detections.
- **Weekly digest** — auto-generated recap with a 6-image thumbnail collage, total visits, trends, and busiest day. Posted to all platforms on your configured day.
- **Feeding pattern predictions** — tracks historical visit patterns by hour and estimates when the next hummingbird will show up. Confidence levels and everything.
- **AI-powered insights** — GPT-4o analyzes your analytics data and generates narrative summaries about feeding trends, peak hours, and activity patterns.
- **Live dashboard** — real-time camera feed, audio, detection states, analytics, system vitals. Full mission control energy.
- **Trains itself** — label frames as "bird" or "not bird" from the dashboard. Teach it to stop falling for leaves.
- **Self-healing** — camera unplugged? It retries every 10 seconds. Failed Facebook post? Queued for retry. App restart? Picks up where it left off.
- **Night mode** — auto sleep at sunset, auto wake before sunrise. Even surveillance operations need rest.
- **One-button updates** — git pull, reinstall deps, restart service. From the dashboard. Because manual deployments are beneath us.

## Hardware

- Raspberry Pi 3B+ (or newer) — the field agent
- USB webcam with built-in mic — eyes and ears
- 32GB+ SD card — evidence storage
- Power supply (5V/2.5A minimum) — keeps the operation running
- Hummingbird feeder — the honeypot (technically nectarpot)

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

### 6. Set Up Social Media

**Facebook** (required for full experience):
```bash
source venv/bin/activate
python scripts/setup_facebook_token.py
```
Grab your **App ID**, **App Secret**, and **short-lived token**. The script converts it to a permanent token.

**Twitter/X** (optional): Create a Twitter Developer app, get your API keys and access tokens, add them to `.env`.

**Bluesky** (optional): Generate an app password at [bsky.app/settings/app-passwords](https://bsky.app/settings/app-passwords), add your handle and password to `.env`.

The system auto-discovers which platforms are configured and posts to all of them.

### 7. Fire It Up

```bash
sudo systemctl start hummingbird
```

### 8. Check the Dashboard

```
http://hummingbirdcam.local:8080
```

Starts in **Test Mode** so you don't accidentally spam your page on day one. Disable it when you're ready to go live.

## How It Catches Them

The detection pipeline — four layers of increasingly paranoid verification:

1. **Motion + Color** *(~1ms)* — Scans every frame for movement in the right size range, then checks for iridescent green, ruby-red, and rufous-orange hummingbird colors. Fast, cheap, and only slightly paranoid. Requires 5 consecutive frames to trigger — no more single-leaf meltdowns.

2. **Bird Species Classifier** *(~1-2 sec on Pi)* — MobileNetV2 trained on 964 bird species. Runs fully local on the Pi, no cloud needed. The "prove you're actually a hummingbird" checkpoint.

3. **Record + Post** — 25 seconds of evidence. GPT-4o analyzes the frames, checks the weather, writes the caption, and blasts it to Facebook, Twitter/X, and Bluesky. The bird has no idea it's internet famous on three platforms.

## The Dashboard

Hit `http://hummingbirdcam.local:8080` — welcome to mission control:

- **Live camera feed** with detection overlays and real-time audio surveillance
- **Detection status indicators:**
  - Green = hummingbird confirmed, recording in progress
  - Yellow = motion detected, investigating
  - Blue = running verification
  - Red = rejected (nice try, leaf)
  - Purple = night mode, system sleeping
  - Red glow = camera error, retrying
- **Camera controls** — rotation, test recording, mic test
- **Training interface** — label frames to make the classifier smarter
- **Clip browser** — review, play, delete, or admire your regulars
- **Analytics panel** — feeding patterns, hourly distribution, next-visit predictions, AI-generated insights
- **System stats** — uptime, detections today, posts today, cooldowns, schedule, git version
- **Hardware vitals** — CPU temp, RAM usage, disk space
- **Live logs** — because something always breaks eventually
- **One-click update** — pull latest code and restart without SSH

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
| `TWITTER_API_KEY` | | Twitter/X API key |
| `TWITTER_API_SECRET` | | Twitter/X API secret |
| `TWITTER_ACCESS_TOKEN` | | Twitter/X access token |
| `TWITTER_ACCESS_SECRET` | | Twitter/X access secret |
| `BLUESKY_HANDLE` | | Bluesky handle (e.g. `you.bsky.social`) |
| `BLUESKY_APP_PASSWORD` | | Bluesky app password |
| `OPENWEATHERMAP_API_KEY` | | OpenWeatherMap API key (free tier) |
| `AUTO_REPLY_ENABLED` | `false` | GPT-4o auto-replies to Facebook comments |
| `AUTO_REPLY_MAX_PER_HOUR` | `10` | Rate limit for AI comment replies |
| `WEEKLY_DIGEST_ENABLED` | `false` | Post weekly recap with thumbnail collage |
| `WEEKLY_DIGEST_DAY` | `sunday` | Day of week to post the digest |
| `VISION_CAPTION_ENABLED` | `true` | GPT-4o analyzes frames for captions |
| `CAMERA_TYPE` | `usb` | `usb`, `picamera`, or `auto` |
| `USB_CAMERA_INDEX` | `0` | Which `/dev/video` to use |
| `CAMERA_ROTATION` | `0` | 0, 90, 180, 270 degrees |
| `AUDIO_ENABLED` | `true` | Record sound with clips |
| `AUDIO_DEVICE` | `default` | ALSA mic device (`arecord -l` to list) |
| `VISION_VERIFY_ENABLED` | `true` | Use bird classifier to confirm |
| `TEST_MODE` | `true` | Record but don't post (disable when ready) |
| `MOTION_THRESHOLD` | `15.0` | Motion sensitivity |
| `COLOR_MIN_AREA` | `300` | Min hummingbird-colored pixels |
| `COLOR_MAX_AREA` | `5000` | Max (rejects big objects) |
| `DETECTION_COOLDOWN_SECONDS` | `60` | Seconds between detections |
| `MAX_POSTS_PER_DAY` | `10` | Daily Facebook post limit |
| `CLIP_PRE_SECONDS` | `5` | Pre-detection buffer |
| `CLIP_POST_SECONDS` | `20` | Post-detection recording |
| `VIDEO_WIDTH` | `1920` | Video resolution width |
| `VIDEO_HEIGHT` | `1080` | Video resolution height |
| `VIDEO_FPS` | `15` | Frames per second |
| `VIDEO_BITRATE` | `5000000` | Video bitrate (bps) |
| `MAX_CLIPS_DISK_MB` | `2000` | Auto-delete oldest clips above this |
| `NIGHT_MODE_ENABLED` | `true` | Auto sleep at sunset, wake at sunrise |
| `LOCATION_LAT` | `35.1495` | Your latitude |
| `LOCATION_LNG` | `-89.8733` | Your longitude |
| `LOCATION_TIMEZONE` | `America/Chicago` | Your timezone |
| `LOCATION_NAME` | `Bartlett, TN` | Shown on dashboard and posts |
| `WAKE_BEFORE_SUNRISE_MIN` | `30` | Minutes before sunrise to wake up |
| `SLEEP_AFTER_SUNSET_MIN` | `30` | Minutes after sunset to sleep |
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
| Set timezone | `sudo timedatectl set-timezone America/Chicago` |
| Dashboard | `http://hummingbirdcam.local:8080` |

## Project Structure

```
LocalHummingBirdCam/
├── main.py                  # The brains of the operation
├── config.py                # Every knob and dial
├── schedule.py              # Sunrise/sunset automation
├── camera/
│   ├── stream.py            # USB + Pi Camera with rotation
│   └── recorder.py          # Video + audio capture via ffmpeg
├── detection/
│   ├── detector.py          # Base detector interface
│   ├── motion_color.py      # Motion + HSV color filtering
│   └── vision_verify.py     # MobileNetV2 bird classifier (TFLite)
├── analytics/
│   └── patterns.py          # Feeding patterns, predictions, AI insights
├── data/
│   └── sightings.py         # SQLite sightings database
├── social/
│   ├── poster_manager.py    # Multi-platform auto-discovery & routing
│   ├── comment_generator.py # GPT-4o caption generation
│   ├── comment_responder.py # AI auto-replies to Facebook comments
│   ├── facebook_poster.py   # Facebook Graph API posting
│   ├── twitter_poster.py    # Twitter/X via tweepy
│   ├── bluesky_poster.py    # Bluesky via AT Protocol
│   └── digest.py            # Weekly recap with thumbnail collage
├── web/
│   ├── dashboard.py         # Flask dashboard + live feed
│   └── static/              # Banner and static assets
├── scripts/
│   ├── setup_facebook_token.py    # Facebook token setup
│   ├── auto_update.sh             # Git pull + restart
│   ├── hummingbird.service        # systemd service
│   ├── hummingbird-updater.service # Auto-update service
│   ├── hummingbird-updater.timer   # Update timer
│   ├── hummingbird-sudoers         # Passwordless restart perms
│   └── install_dependencies.sh     # Full system setup
├── tests/                   # Unit tests
├── models/                  # Bird classifier (auto-downloaded)
├── clips/                   # Hummingbird evidence
├── training/                # Labeled frames for retraining
└── logs/                    # Rotating log files
```

## Final Notes

This started as "I can do that" and turned into a multi-stage AI surveillance pipeline with multi-platform social media posting, weather-aware vision captions, autonomous comment replies, feeding pattern predictions, weekly digests, and a GPT that writes better captions than I do.

It is absolutely over-engineered. It will never not be over-engineered. That is the point.

Follow along:
- Facebook: [facebook.com/backyard.hummers](https://www.facebook.com/backyard.hummers)
- Bluesky: [backyardhummers.bsky.social](https://bsky.app/profile/backyardhummers.bsky.social)
- Twitter/X: [x.com/backyardhummers](https://x.com/backyardhummers)
- Instagram: [instagram.com/backyard.hummers](https://www.instagram.com/backyard.hummers)
- Source code: [github.com/LeeOtts/LocalHummingBirdCam](https://github.com/LeeOtts/LocalHummingBirdCam)
