"""Generate social media captions using OpenAI / Azure OpenAI."""

import base64
import collections
import logging
import random
import re
import threading
from pathlib import Path

import config

try:
    from openai import OpenAIError as _OpenAIError
except ImportError:
    _OpenAIError = Exception  # Fallback if openai not installed

logger = logging.getLogger(__name__)

# Singleton client — reuses connection pool, recreated when config changes
_client = None
_client_config: tuple = ()
_client_lock = threading.Lock()

# Rolling buffer of recent captions to avoid repetition
MAX_RECENT_CAPTIONS = 10
_recent_captions: collections.deque = collections.deque(maxlen=MAX_RECENT_CAPTIONS)

# Rolling buffer of recent vision descriptions for "return visitor" storytelling
_recent_descriptions: collections.deque = collections.deque(maxlen=5)

# Per-platform constraints for multi-platform caption generation
PLATFORM_HINTS = {
    "Facebook": {
        "max_chars": None,
        "tone": "Longer, conversational. 1-3 sentences. No hashtags.",
    },
    "Bluesky": {
        "max_chars": 300,
        "tone": "Concise, punchy. Under 300 characters. Add 1-2 relevant hashtags (e.g. #hummingbird #birdwatching).",
    },
    "Twitter": {
        "max_chars": 280,
        "tone": "Tightest. Under 280 characters. One-liner preferred. Add 1-2 relevant hashtags (count toward char limit).",
    },
    "Instagram": {
        "max_chars": 2200,
        "tone": "Warm, visual. 1-2 sentences. Add 3-5 relevant hashtags (e.g. #hummingbird #birdwatching #nature).",
    },
    "TikTok": {
        "max_chars": 2200,
        "tone": "Fun, casual, short. 1-2 sentences. Add 2-4 trending hashtags (e.g. #hummingbird #birdsoftiktok #nature).",
    },
}


def _build_platform_block(platforms: list[str]) -> str:
    """Build the multi-platform output instructions to append to a system prompt."""
    lines = [
        "\nMulti-Platform Output:",
        "You must write a UNIQUE caption for each platform below. Each should feel "
        "distinct — not just a truncated version of the same text. Tailor the voice, "
        "length, and style to the platform.",
        "",
    ]
    for name in platforms:
        hint = PLATFORM_HINTS.get(name, {"tone": "Standard caption."})
        lines.append(f"---{name.upper()}---")
        lines.append(hint["tone"])
        lines.append("")

    lines.append("Output format — return EXACTLY this structure, no other text:")
    for name in platforms:
        lines.append(f"---{name.upper()}---")
        lines.append(f"(your {name} caption here)")
    return "\n".join(lines)


def _parse_platform_captions(raw: str, platforms: list[str]) -> dict[str, str]:
    """Parse tagged multi-platform output into {platform: caption} dict.

    Falls back to returning the raw text for every platform if parsing fails.
    """
    captions: dict[str, str] = {}
    # Build regex pattern to split on ---PLATFORM--- tags (case-insensitive)
    tag_pattern = "|".join(re.escape(f"---{p.upper()}---") for p in platforms)
    parts = re.split(f"({tag_pattern})", raw, flags=re.IGNORECASE)

    # parts alternates: [preamble, tag1, content1, tag2, content2, ...]
    current_platform = None
    for part in parts:
        stripped = part.strip()
        # Check if this part is a tag
        matched_platform = None
        for p in platforms:
            if stripped.upper() == f"---{p.upper()}---":
                matched_platform = p
                break
        if matched_platform:
            current_platform = matched_platform
        elif current_platform:
            captions[current_platform] = stripped

    # If we got captions for at least one platform, fill missing ones with first available
    if captions:
        fallback_caption = next(iter(captions.values()))
        for p in platforms:
            if p not in captions or not captions[p]:
                captions[p] = fallback_caption
        return captions

    # Parsing failed entirely — return raw text for all platforms
    logger.warning("Failed to parse multi-platform captions, using raw text for all")
    cleaned = raw.strip()
    return {p: cleaned for p in platforms}


def _validate_caption(text: str) -> bool:
    """Return True if *text* looks like a coherent caption, False if gibberish.

    Uses lightweight heuristics to catch the kind of garbage that LLMs
    occasionally emit (fragmented words on separate lines, random symbols, etc.).
    """
    stripped = text.strip()

    # --- too short or too long ---
    if len(stripped) < 10:
        logger.warning("Caption rejected: too short (%d chars)", len(stripped))
        return False
    if len(stripped) > 2000:
        logger.warning("Caption rejected: too long (%d chars)", len(stripped))
        return False

    words = stripped.split()
    if not words:
        logger.warning("Caption rejected: no words")
        return False

    # --- newline-to-word ratio (gibberish had isolated words on many lines) ---
    newline_count = stripped.count("\n")
    if len(words) > 0 and newline_count / len(words) > 0.4:
        logger.warning("Caption rejected: high newline ratio (%.2f)", newline_count / len(words))
        return False

    # --- average word length (gibberish fragments are 1-2 chars) ---
    avg_word_len = sum(len(w) for w in words) / len(words)
    if avg_word_len < 2.5:
        logger.warning("Caption rejected: low avg word length (%.1f)", avg_word_len)
        return False

    # --- too many stub lines (lines with only 1-2 words) ---
    lines = [ln for ln in stripped.split("\n") if ln.strip()]
    if lines:
        stub_lines = sum(1 for ln in lines if len(ln.split()) <= 2)
        if stub_lines / len(lines) > 0.6:
            logger.warning("Caption rejected: %.0f%% stub lines", 100 * stub_lines / len(lines))
            return False

    # --- low alphabetic ratio ---
    non_ws = stripped.replace(" ", "").replace("\n", "").replace("\t", "")
    if non_ws:
        alpha_ratio = sum(c.isalpha() for c in non_ws) / len(non_ws)
        if alpha_ratio < 0.6:
            logger.warning("Caption rejected: low alpha ratio (%.2f)", alpha_ratio)
            return False

    return True


def _validate_all_captions(captions: dict[str, str]) -> bool:
    """Return True only if every platform caption passes validation."""
    for platform, text in captions.items():
        if not _validate_caption(text):
            logger.warning("Validation failed for %s caption", platform)
            return False
    return True


def _get_client():
    global _client, _client_config
    current_config = (
        config.OPENAI_API_KEY,
        config.AZURE_OPENAI_ENDPOINT,
        config.AZURE_OPENAI_API_VERSION,
    )

    if _client is not None and _client_config == current_config:
        return _client

    with _client_lock:
        # Double-check after acquiring lock
        if _client is not None and _client_config == current_config:
            return _client

        if not config.OPENAI_API_KEY:
            _client = None
            _client_config = ()
            return None

        import openai as _openai
        # Azure OpenAI — needs endpoint, key, and api-version
        if config.AZURE_OPENAI_ENDPOINT:
            _client = _openai.AzureOpenAI(
                azure_endpoint=config.AZURE_OPENAI_ENDPOINT,
                api_key=config.OPENAI_API_KEY,
                api_version=config.AZURE_OPENAI_API_VERSION,
            )
            logger.info("Using Azure OpenAI (endpoint: %s, deployment: %s)",
                         config.AZURE_OPENAI_ENDPOINT, config.AZURE_OPENAI_DEPLOYMENT)
        else:
            _client = _openai.OpenAI(api_key=config.OPENAI_API_KEY)
            logger.info("Using OpenAI direct")

        _client_config = current_config
        return _client

SYSTEM_PROMPT = """\
You are the voice of "Backyard Hummers," an automated \
hummingbird feeder camera.

Your job is to write short, engaging captions (1-3 sentences) for hummingbird visits.

Visit Context:
- Visit #{visit_number} today ({detections} total so far, {rejected} false alarm(s))
- Time: {time_of_day} ({day_part}) on {day_of_week}
- Month: {month}
- Time since last visitor: {since_last}
- Sunrise: {sunrise} / Sunset: {sunset}
{weather_line}
{milestone_line}

Tone & Style:
- Playful, lightly cheeky, and subtly suggestive (double entendre), but always \
  safe and appropriate for Facebook
- Humor should be understated and clever, never forced or explicit
- Natural, conversational, and human — not robotic or overly polished
- Confident and observant, like someone casually watching wildlife in their backyard

Variety Requirements:
- Every caption must feel distinct — avoid repeating sentence structures or key phrases
- Rotate between tones:
  - Playful / cheeky (primary)
  - Observational / nature-focused
  - Light stats or activity commentary
  - Slightly unhinged backyard observer
  - Dry "AI analysis" humor (rare)
  - Question to followers (occasional)
- Do not rely on the same joke pattern repeatedly

Engagement Hooks (use ONE of these occasionally, not every post):
- Ask followers a light question ("Anyone else's feeder this busy on a Monday?")
- Invite speculation about the bird's behavior or personality
- Reference running themes: "the regulars," "the morning shift," "the evening rush"
- React to patterns: first visit of the day, rapid-fire visits, long gaps, late visitors
- Frame the visit with the day/time ("Saturday morning regulars are here early")
- If visit #1, play up the anticipation. If visit 7+, play up how busy it's been.
- Near-sunrise or near-sunset visits are dramatic — lean into that

Format Variety (rotate naturally):
- Quick one-liner (punchy, under 10 words)
- Standard observation (1-2 sentences)
- Mini-narrative (give the bird a motivation or backstory, 2-3 sentences)
- Stats commentary (weave the numbers into a story)
- Question to followers (end with an engaging question)

Seasonal Notes:
- March-April: Spring migration, early arrivals, "the hummers are back!" energy
- May-August: Peak season, territorial battles, busy feeders, heat commentary
- September-October: Fall migration, "savoring the last visits" bittersweet energy
- November-February: Rare winter visitors are special events worth celebrating

Content Guidelines:
- The post accompanies a video of a hummingbird visit
- If an image is provided, describe what you actually SEE the bird doing — this is \
  the most important detail. Be specific: hovering, perching, tongue out, two birds, \
  chasing, feeding position, wing blur, etc.
- Note distinguishing features when visible: gorget color/iridescence, bill shape, \
  size, sex (males have bright gorgets), tail markings, any unique features
- Use the context naturally — don't force every detail into every caption
- Prefer specificity over generic phrasing
- Reference behavior: hovering, quick visits, repeat visits, territorial chasing, \
  tongue work, perching, dive-bombing
{recent_descriptions_line}

Hard Rules:
- Do NOT mention AI, models, prompts, or automation
- Do NOT explain jokes
- Use 0-1 emojis maximum, and only when it adds value
- Avoid cliches and generic lines
- Avoid repeating phrases like "quick visit," "stopping by," or "back again" too frequently

Style Targets (for guidance only, do not copy):
- "In and out in seconds... impressive."
- "Visit number 4 and it's not even noon. Somebody's thirsty."
- "First one of the day showed up right at sunrise. Punctual."
- "Three visits in ten minutes. At this point, just move in."
- "Anyone else's backyard this popular on a Wednesday?"
- "This one hovered for a solid eight seconds. That's commitment."
{platform_block}
Output: Return ONLY the caption text. No labels, no extra formatting.
"""

# Fallback captions if the API is unavailable
FALLBACK_CAPTIONS = [
    "In and out in under ten seconds. Efficient.",
    "This one hovered for a while. Took its time. Respect.",
    "Evening traffic is picking up at the feeder.",
    "Caught one working the backyard feeder again. Dedicated.",
    "Short visit, but that tongue was putting in work.",
    "Three visits before lunch. Somebody's got a routine.",
    "Just showed up like it owns the place. Maybe it does.",
    "The feeder isn't going anywhere, but this one's in a rush.",
]


def _format_since_last(seconds) -> str:
    """Convert seconds-since-last-visit to human-readable text."""
    if seconds is None:
        return "First visit of the day"
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds} seconds since last visit"
    if seconds < 3600:
        return f"{seconds // 60} minute(s) since last visit"
    return f"About {seconds // 3600} hour(s) since last visit"


def _encode_frame(frame_path: Path, max_width: int = 512) -> str | None:
    """Resize a frame and return it as a base64-encoded JPEG data URI."""
    try:
        import cv2
        img = cv2.imread(str(frame_path))
        if img is None:
            return None
        h, w = img.shape[:2]
        if w > max_width:
            scale = max_width / w
            img = cv2.resize(img, (max_width, int(h * scale)))
        _, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 70])
        b64 = base64.b64encode(buf.tobytes()).decode("ascii")
        return f"data:image/jpeg;base64,{b64}"
    except Exception:
        logger.warning("Failed to encode frame for vision caption")
        return None


def generate_comment(detections: int = 0, rejected: int = 0,
                     platforms: list[str] | None = None, **kwargs) -> dict[str, str]:
    """Generate captions for a hummingbird video post, one per platform.

    Returns ``{platform_name: caption}`` dict.  When *platforms* is ``None``
    or contains a single entry the behaviour is identical to the old single-
    caption path (wrapped in a dict).

    If ``frame_path`` is provided and VISION_CAPTION_ENABLED is true, the frame
    is sent to GPT-4o as a multimodal image so it can describe what the bird
    is actually doing.
    """
    platforms = platforms or ["Facebook"]
    multi = len(platforms) > 1

    if not config.OPENAI_API_KEY:
        logger.warning("No OpenAI API key configured, using fallback caption")
        fb = random.choice(FALLBACK_CAPTIONS)
        return {p: fb for p in platforms}

    try:
        client = _get_client()
        # Build context with defaults for any missing fields
        visit_number = kwargs.get("visit_number", detections)
        since_last = _format_since_last(kwargs.get("seconds_since_last"))
        milestone = kwargs.get("milestone")
        milestone_line = f"- Milestone: {milestone}" if milestone else ""

        weather = kwargs.get("weather")
        if weather:
            weather_line = f"- Weather: {weather['temp_f']:.0f}°F, {weather['condition']}"
        else:
            weather_line = ""

        # Build recent bird descriptions for return-visitor storytelling
        if _recent_descriptions:
            desc_text = "\n".join(f"  - {d}" for d in _recent_descriptions)
            recent_descriptions_line = (
                "- Recent bird descriptions from earlier today (if features match, "
                "reference the bird as a possible returning visitor — e.g., "
                "\"looks like our morning regular\" or \"same bright gorget as earlier\"):\n"
                + desc_text
            )
        else:
            recent_descriptions_line = ""

        platform_block = _build_platform_block(platforms) if multi else ""

        prompt = SYSTEM_PROMPT.format(
            detections=detections,
            rejected=rejected,
            visit_number=visit_number,
            time_of_day=kwargs.get("time_of_day", ""),
            day_part=kwargs.get("day_part", ""),
            day_of_week=kwargs.get("day_of_week", ""),
            month=kwargs.get("month", ""),
            since_last=since_last,
            sunrise=kwargs.get("sunrise", ""),
            sunset=kwargs.get("sunset", ""),
            weather_line=weather_line,
            milestone_line=milestone_line,
            recent_descriptions_line=recent_descriptions_line,
            platform_block=platform_block,
        )
        messages: list = [
            {"role": "system", "content": prompt},
        ]
        if _recent_captions:
            avoid_text = "\n".join(f"- {c}" for c in _recent_captions)
            messages.append({"role": "user", "content":
                f"Here are the recent captions already posted — do NOT "
                f"repeat similar phrasing or structure:\n{avoid_text}"})
            messages.append({"role": "assistant", "content":
                "Got it, I'll write something fresh."})

        # Build the user message — with or without a vision frame
        frame_path = kwargs.get("frame_path")
        image_url = None
        if frame_path and config.VISION_CAPTION_ENABLED:
            image_url = _encode_frame(Path(frame_path))

        bird_note = (
            "\nAfter all captions, on a new line starting with 'BIRD:', write a brief "
            "description of the bird's distinguishing features (gorget color, sex, size, "
            "behavior). This line won't be posted — it's just for my records."
        ) if image_url else ""

        if image_url:
            user_content: list | str = [
                {"type": "text", "text": (
                    "Write a post for this hummingbird sighting. Here's a frame from the video."
                    + bird_note
                )},
                {"type": "image_url", "image_url": {"url": image_url, "detail": config.VISION_CAPTION_DETAIL}},
            ]
            logger.info("Sending frame to GPT-4o Vision for caption (detail=%s)", config.VISION_CAPTION_DETAIL)
        else:
            user_content = "Write a post for a new hummingbird sighting video."

        messages.append({"role": "user", "content": user_content})
        max_tok = 400 if multi else 250
        response = client.chat.completions.create(
            model=config.AZURE_OPENAI_DEPLOYMENT or "gpt-4o",
            messages=messages,
            max_tokens=max_tok,
            temperature=0.9,
        )
        raw = (response.choices[0].message.content or "").strip()

        # Split out the bird description line if present
        bird_desc = None
        if "BIRD:" in raw:
            parts = raw.split("BIRD:", 1)
            raw = parts[0].strip()
            bird_desc = parts[1].strip()
            if bird_desc:
                _recent_descriptions.append(bird_desc)
                logger.info("Bird description: %s", bird_desc)

        if multi:
            captions = _parse_platform_captions(raw, platforms)
        else:
            captions = {platforms[0]: raw}

        # Validate — retry once if gibberish
        if not _validate_all_captions(captions):
            logger.warning("Sighting caption failed validation, retrying once")
            response = client.chat.completions.create(
                model=config.AZURE_OPENAI_DEPLOYMENT or "gpt-4o",
                messages=messages, max_tokens=max_tok, temperature=0.9,
            )
            raw = (response.choices[0].message.content or "").strip()
            if "BIRD:" in raw:
                parts = raw.split("BIRD:", 1)
                raw = parts[0].strip()
                desc = parts[1].strip()
                if desc:
                    _recent_descriptions.append(desc)
            if multi:
                captions = _parse_platform_captions(raw, platforms)
            else:
                captions = {platforms[0]: raw}
            if not _validate_all_captions(captions):
                logger.error("Sighting caption failed validation on retry, using fallback")
                fb = random.choice(FALLBACK_CAPTIONS)
                return {p: fb for p in platforms}

        # Store the longest caption for repetition avoidance
        longest = max(captions.values(), key=len)
        _recent_captions.append(longest)
        logger.info("Generated captions: %s", captions)
        return captions

    except _OpenAIError:
        logger.exception("OpenAI API call failed, using fallback caption")
        fb = random.choice(FALLBACK_CAPTIONS)
        return {p: fb for p in platforms}


GOOD_MORNING_PROMPT = """\
You are the voice of "Backyard Hummers," an automated \
hummingbird feeder camera in {location}.

Write a "good morning" post. The camera just woke up and is ready to watch \
for hummers.

Context:
- Day: {day_of_week}
- Month: {month}
- Sunrise: {sunrise}
- Yesterday's tally: {yesterday_text}
- Lifetime hummingbird sightings so far: {lifetime_total}
{weather_line}

Tone & Style:
- Playful, lightly cheeky, subtly suggestive but safe for social media
- Natural and conversational, like someone half-awake grabbing coffee and \
  checking the feeder
- Understated humor, never forced

{anticipation_block}\
Engagement Hooks (use occasionally, not every post):
- Tease a prediction ("Over/under on 5 today?")
- Reference yesterday's tally to set expectations
- Seasonal framing (spring arrivals, summer heat, fall migration)
- Day-of-week personality (lazy Sunday, Monday back to work)

Hard Rules:
- Do NOT mention AI, models, prompts, or automation
- Do NOT explain jokes
- Use 0-1 emojis maximum
- 1-3 sentences max

Style Targets (do not copy):
- "Sun's up. Feeder's full. Let's see who shows up."
- "Monday morning. The hummers don't take weekends off and neither do we."
- "Yesterday was 8 visits. Think we can beat that?"
- "April in the backyard. Migration season. The roster is about to get interesting."
{platform_block}
Output: Return ONLY the caption text. No labels, no extra formatting.
"""

GOOD_NIGHT_PROMPT = """\
You are the voice of "Backyard Hummers," an automated \
hummingbird feeder camera in {location}.

Write an end-of-day recap post.

Context:
- {detections} hummingbird visit(s) caught on camera, {rejected} false alarm(s)
- Day: {day_of_week}
- Month: {month}
- Sunset: {sunset}
- Busiest hour: {peak_hour_text}
- Lifetime hummingbird sightings so far: {lifetime_total}
- {record_text}

You must include the detection count naturally in the caption.

Tone & Style:
- Playful, lightly cheeky, subtly suggestive but safe for social media
- Natural and conversational, like wrapping up a day of backyard watching
- If 0 detections, lean into dry humor about a slow day
- If many detections, play up how busy the backyard was

{anticipation_block}\
Engagement Hooks (use occasionally, not every post):
- Ask followers to predict tomorrow's count
- Compare to the record if relevant
- Tease tomorrow ("Same time tomorrow?", "See you at sunrise")
- Weekend wrap-up energy vs weeknight wind-down

Hard Rules:
- Do NOT mention AI, models, prompts, or automation
- Do NOT explain jokes
- Use 0-1 emojis maximum
- 1-3 sentences max
- Must include the actual number of detections
- Do not refer to see you again tomorrow night at night or sunset. You should refer to the next morning or just say "see you tomorrow" without specifying time.

Style Targets (do not copy):
- "3 hummers today. The backyard was putting in work."
- "Zero visits. Even the hummers took the day off."
- "11 confirmed sightings. New personal best. The feeder earned its keep today."
- "7 visits, most of them before 10 AM. Early risers run this yard."
- "Think we'll top 7 tomorrow? Place your bets."
{platform_block}
Output: Return ONLY the caption text. No labels, no extra formatting.
"""


_ANTICIPATION_MORNING = """\
IMPORTANT — We have NEVER seen a hummingbird yet! Lifetime sightings: 0.
The feeder is set up, the camera is rolling, and we are WAITING for the \
first-ever visitor. Channel pure anticipation and excitement. This is the \
"any day now" era. Build hype for the first sighting like it's the most \
important event in backyard history. Every morning is another shot at \
witnessing greatness.

Style Targets for anticipation mode (do not copy):
- "Day {day_number}. Still no hummers. But the feeder is full and hope is irrational."
- "Sunrise at {sunrise}. Camera's on. Today could be THE day."
- "The feeder's been out there doing its job. Now we need the hummers to do theirs."
- "Still waiting on our first visitor. The suspense is honestly killing me."

"""

_ANTICIPATION_NIGHT = """\
IMPORTANT — We have NEVER seen a hummingbird yet! Lifetime sightings: 0.
Zero detections again today, but this isn't a slow day — we're still waiting \
for the FIRST ONE EVER. Channel hopeful anticipation, not disappointment. \
Tomorrow could be the day. The feeder is ready. We are ready. The hummers \
are just building suspense.

Style Targets for anticipation mode (do not copy):
- "0 hummers today. 0 lifetime. But the feeder is patient and so are we."
- "Still waiting on visitor #1. At this point it's going to be a whole event."
- "No hummers yet. The feeder remains undefeated in the waiting game."
- "0 sightings. The hummers are out there somewhere, probably stuck in traffic."

"""

_PRESEASON_MORNING = """\
IMPORTANT — It's pre-season! No hummingbirds spotted yet this year.
Based on {based_on_years} years of historical data, hummingbirds typically \
arrive around {predicted_date} (earliest: {earliest_date}, latest: {latest_date}).
That's about {days_until} days away. The feeder is out, the camera is ready, \
and we're in countdown mode. Channel the excitement of waiting for opening day. \
Every morning gets us one day closer.

Style Targets for pre-season mode (do not copy):
- "{days_until} days until the predicted arrival. The feeder is doing stretches."
- "Historically they show up around {predicted_date}. We're watching."
- "The countdown continues. {days_until} days to go if the hummers read the calendar."
- "Camera's on. Feeder's full. {predicted_date} is circled on the calendar."

"""

_PRESEASON_NIGHT = """\
IMPORTANT — It's pre-season! No hummingbirds spotted yet this year.
Based on {based_on_years} years of data, hummingbirds typically arrive \
around {predicted_date} (earliest: {earliest_date}, latest: {latest_date}).
That's about {days_until} days away. Zero detections today, but that's expected — \
we're still in the countdown. Channel patient anticipation, not disappointment. \
The hummers are on their way.

Style Targets for pre-season mode (do not copy):
- "0 hummers today. But they're not due for another {days_until} days."
- "No visitors yet. Predicted arrival: {predicted_date}. The wait continues."
- "Still in pre-season mode. {days_until} days to go."
- "The feeder is patient. {predicted_date} is getting closer."

"""

_ENDSEASON_BLOCK = """\
SEASONAL NOTE — We're in the final stretch of hummingbird season. \
Based on {based_on_years} years of data, the last hummingbird is usually \
spotted around {predicted_last_date} (earliest: {earliest_last}, latest: {latest_last}).
Every visit could be one of the last this year. Don't overdo it — just a subtle \
touch of bittersweet "savoring the moment" energy mixed into the regular post. \
A brief nod to the season winding down, not a eulogy.

Style hints (do not copy, just capture the vibe):
- "Could be one of the last visits of the year. Making it count."
- "Season's winding down. Every visit hits a little different now."
- "The fall visits always feel a little more special."

"""


def _get_anticipation_block(lifetime_total: int, is_morning: bool, **kwargs) -> str:
    """Return anticipation/seasonal prompt block based on current season state.

    Modes (in priority order):
    1. First-ever anticipation: lifetime_total == 0
    2. Pre-season countdown: season prediction available, no sightings this year yet
    3. End-of-season: within ~2 weeks of predicted last visit date
    4. Normal: empty string
    """
    season_prediction = kwargs.get("season_prediction")

    # Mode 1: Never seen a hummingbird
    if lifetime_total == 0 and not season_prediction:
        return _ANTICIPATION_MORNING if is_morning else _ANTICIPATION_NIGHT

    if not season_prediction:
        return ""

    today_count = kwargs.get("today_count", 0)
    days_until = season_prediction.get("days_until")
    in_season = season_prediction.get("in_season", False)

    # Mode 2: Pre-season countdown (before predicted arrival, no sightings this year)
    if not in_season and days_until is not None and days_until > 0 and today_count == 0:
        template = _PRESEASON_MORNING if is_morning else _PRESEASON_NIGHT
        return template.format(
            predicted_date=season_prediction.get("predicted_display", "mid-April"),
            earliest_date=season_prediction.get("earliest_display", "early April"),
            latest_date=season_prediction.get("latest_display", "late April"),
            days_until=days_until,
            based_on_years=season_prediction.get("based_on_years", "several"),
        )

    # Mode 3: End of season (within ~2 weeks of predicted last visit)
    end_season = kwargs.get("end_season_prediction")
    if end_season and in_season:
        days_until_end = end_season.get("days_until_last")
        if days_until_end is not None and 0 <= days_until_end <= 14:
            return _ENDSEASON_BLOCK.format(
                predicted_last_date=end_season.get("predicted_last_display", "early October"),
                earliest_last=end_season.get("earliest_last_display", "late September"),
                latest_last=end_season.get("latest_last_display", "mid-October"),
                based_on_years=end_season.get("based_on_years", "several"),
            )

    return ""


def generate_good_morning(location: str, sunrise: str,
                          platforms: list[str] | None = None, **kwargs) -> dict[str, str]:
    """Generate a morning greeting post, one caption per platform."""
    platforms = platforms or ["Facebook"]
    multi = len(platforms) > 1
    lifetime_total = kwargs.get("lifetime_total", 0)

    if lifetime_total == 0:
        fallback = f"Sunrise at {sunrise}. Camera's on. Still waiting for our first hummingbird. Today could be the day."
    else:
        fallback = f"Sun's up at {sunrise}. Feeder's full. Let's see who shows up."

    if not config.OPENAI_API_KEY:
        return {p: fallback for p in platforms}

    yesterday = kwargs.get("yesterday_detections")
    yesterday_text = f"{yesterday} visit(s) yesterday" if yesterday is not None else "No data"

    weather = kwargs.get("weather")
    if weather:
        weather_line = f"- Weather: {weather['temp_f']:.0f}°F, {weather['condition']}"
    else:
        weather_line = ""

    platform_block = _build_platform_block(platforms) if multi else ""
    anticipation_block = _get_anticipation_block(
        lifetime_total, is_morning=True,
        season_prediction=kwargs.get("season_prediction"),
        end_season_prediction=kwargs.get("end_season_prediction"),
        today_count=kwargs.get("today_count", 0),
    )

    try:
        client = _get_client()
        max_tok = 400 if multi else 200
        response = client.chat.completions.create(
            model=config.AZURE_OPENAI_DEPLOYMENT or "gpt-4o",
            messages=[
                {"role": "system", "content": GOOD_MORNING_PROMPT.format(
                    location=location,
                    sunrise=sunrise,
                    day_of_week=kwargs.get("day_of_week", ""),
                    month=kwargs.get("month", ""),
                    yesterday_text=yesterday_text,
                    lifetime_total=lifetime_total,
                    weather_line=weather_line,
                    platform_block=platform_block,
                    anticipation_block=anticipation_block,
                )},
                {"role": "user", "content": "Write this morning's post."},
            ],
            max_tokens=max_tok,
            temperature=0.9,
        )
        raw = (response.choices[0].message.content or "").strip()

        if multi:
            captions = _parse_platform_captions(raw, platforms)
        else:
            captions = {platforms[0]: raw}

        # Validate — retry once if gibberish
        if not _validate_all_captions(captions):
            logger.warning("Morning caption failed validation, retrying once")
            response = client.chat.completions.create(
                model=config.AZURE_OPENAI_DEPLOYMENT or "gpt-4o",
                messages=[
                    {"role": "system", "content": GOOD_MORNING_PROMPT.format(
                        location=location, sunrise=sunrise,
                        day_of_week=kwargs.get("day_of_week", ""),
                        month=kwargs.get("month", ""),
                        yesterday_text=yesterday_text,
                        lifetime_total=lifetime_total,
                        weather_line=weather_line,
                        platform_block=platform_block,
                        anticipation_block=anticipation_block,
                    )},
                    {"role": "user", "content": "Write this morning's post."},
                ],
                max_tokens=max_tok, temperature=0.9,
            )
            raw = (response.choices[0].message.content or "").strip()
            if multi:
                captions = _parse_platform_captions(raw, platforms)
            else:
                captions = {platforms[0]: raw}
            if not _validate_all_captions(captions):
                logger.error("Morning caption failed validation on retry, using fallback")
                return {p: fallback for p in platforms}

        logger.info("Morning post: %s", captions)
        return captions

    except _OpenAIError:
        logger.exception("OpenAI API call failed for morning post")
        return {p: fallback for p in platforms}


def generate_good_night(location: str, sunset: str, detections: int, rejected: int,
                        platforms: list[str] | None = None, **kwargs) -> dict[str, str]:
    """Generate an end-of-day recap post with hummer tally, one per platform."""
    platforms = platforms or ["Facebook"]
    multi = len(platforms) > 1
    lifetime_total = kwargs.get("lifetime_total", 0)

    if lifetime_total == 0:
        fallback = f"0 hummers today. 0 lifetime. But the feeder is patient and so are we. See you tomorrow."
    else:
        fallback = f"{detections} hummer(s) on camera today. Sun went down at {sunset}. See you tomorrow."

    if not config.OPENAI_API_KEY:
        return {p: fallback for p in platforms}

    peak_hour = kwargs.get("peak_hour")
    peak_hour_text = f"{peak_hour}" if peak_hour else "N/A"
    is_record = kwargs.get("is_record", False)
    record_text = "New all-time record!" if is_record else ""

    platform_block = _build_platform_block(platforms) if multi else ""
    anticipation_block = _get_anticipation_block(
        lifetime_total, is_morning=False,
        season_prediction=kwargs.get("season_prediction"),
        end_season_prediction=kwargs.get("end_season_prediction"),
        today_count=kwargs.get("today_count", 0),
    )

    try:
        client = _get_client()
        max_tok = 400 if multi else 200
        response = client.chat.completions.create(
            model=config.AZURE_OPENAI_DEPLOYMENT or "gpt-4o",
            messages=[
                {"role": "system", "content": GOOD_NIGHT_PROMPT.format(
                    location=location, sunset=sunset,
                    detections=detections, rejected=rejected,
                    day_of_week=kwargs.get("day_of_week", ""),
                    month=kwargs.get("month", ""),
                    peak_hour_text=peak_hour_text,
                    lifetime_total=lifetime_total,
                    record_text=record_text,
                    platform_block=platform_block,
                    anticipation_block=anticipation_block,
                )},
                {"role": "user", "content": "Write tonight's end-of-day recap post."},
            ],
            max_tokens=max_tok,
            temperature=0.9,
        )
        raw = (response.choices[0].message.content or "").strip()

        if multi:
            captions = _parse_platform_captions(raw, platforms)
        else:
            captions = {platforms[0]: raw}

        # Validate — retry once if gibberish
        if not _validate_all_captions(captions):
            logger.warning("Night caption failed validation, retrying once")
            response = client.chat.completions.create(
                model=config.AZURE_OPENAI_DEPLOYMENT or "gpt-4o",
                messages=[
                    {"role": "system", "content": GOOD_NIGHT_PROMPT.format(
                        location=location, sunset=sunset,
                        detections=detections, rejected=rejected,
                        day_of_week=kwargs.get("day_of_week", ""),
                        month=kwargs.get("month", ""),
                        peak_hour_text=peak_hour_text,
                        lifetime_total=lifetime_total,
                        record_text=record_text,
                        platform_block=platform_block,
                        anticipation_block=anticipation_block,
                    )},
                    {"role": "user", "content": "Write tonight's end-of-day recap post."},
                ],
                max_tokens=max_tok, temperature=0.9,
            )
            raw = (response.choices[0].message.content or "").strip()
            if multi:
                captions = _parse_platform_captions(raw, platforms)
            else:
                captions = {platforms[0]: raw}
            if not _validate_all_captions(captions):
                logger.error("Night caption failed validation on retry, using fallback")
                return {p: fallback for p in platforms}

        logger.info("Night post: %s", captions)
        return captions

    except _OpenAIError:
        logger.exception("OpenAI API call failed for night post")
        return {p: fallback for p in platforms}


MILESTONE_PROMPT = """\
You are the voice of "Backyard Hummers," an automated hummingbird feeder camera.

Write a celebration post — we just hit lifetime visitor #{count}!

Stats:
- Lifetime visits recorded: {count}
- Days the camera has been running: {days_running}
- Today's count so far: {today_count}

Tone & Style:
- Celebratory but not over-the-top — more "quiet pride" than "party mode"
- Playful, lightly cheeky, subtly suggestive but safe for social media
- Frame the milestone in a fun way (e.g., "That's a lot of tiny tongues")
- Acknowledge the community watching along

Hard Rules:
- Do NOT mention AI, models, prompts, or automation
- Use 0-2 emojis maximum
- 2-3 sentences max
{platform_block}
Output: Return ONLY the caption text.
"""


def generate_milestone_post(lifetime_count: int, today_count: int = 0,
                            days_running: int = 0,
                            platforms: list[str] | None = None) -> dict[str, str]:
    """Generate a dedicated celebration post for a milestone detection count."""
    platforms = platforms or ["Facebook"]
    multi = len(platforms) > 1
    fallback = f"Lifetime visitor #{lifetime_count} just showed up. Not bad for a backyard feeder."

    if not config.OPENAI_API_KEY:
        return {p: fallback for p in platforms}

    platform_block = _build_platform_block(platforms) if multi else ""

    try:
        client = _get_client()
        max_tok = 400 if multi else 200
        response = client.chat.completions.create(
            model=config.AZURE_OPENAI_DEPLOYMENT or "gpt-4o",
            messages=[
                {"role": "system", "content": MILESTONE_PROMPT.format(
                    count=lifetime_count,
                    today_count=today_count,
                    days_running=days_running,
                    platform_block=platform_block,
                )},
                {"role": "user", "content": "Write the milestone celebration post."},
            ],
            max_tokens=max_tok,
            temperature=0.9,
        )
        raw = (response.choices[0].message.content or "").strip()

        if multi:
            captions = _parse_platform_captions(raw, platforms)
        else:
            captions = {platforms[0]: raw}

        # Validate — retry once if gibberish
        if not _validate_all_captions(captions):
            logger.warning("Milestone caption failed validation, retrying once")
            response = client.chat.completions.create(
                model=config.AZURE_OPENAI_DEPLOYMENT or "gpt-4o",
                messages=[
                    {"role": "system", "content": MILESTONE_PROMPT.format(
                        count=lifetime_count, today_count=today_count,
                        days_running=days_running, platform_block=platform_block,
                    )},
                    {"role": "user", "content": "Write the milestone celebration post."},
                ],
                max_tokens=max_tok, temperature=0.9,
            )
            raw = (response.choices[0].message.content or "").strip()
            if multi:
                captions = _parse_platform_captions(raw, platforms)
            else:
                captions = {platforms[0]: raw}
            if not _validate_all_captions(captions):
                logger.error("Milestone caption failed validation on retry, using fallback")
                return {p: fallback for p in platforms}

        logger.info("Milestone post: %s", captions)
        return captions

    except _OpenAIError:
        logger.exception("OpenAI API call failed for milestone post")
        return {p: fallback for p in platforms}


MANUAL_POST_PROMPT = """\
You are the voice of "Backyard Hummers," an automated \
hummingbird feeder camera.

You are writing a manual post based on the operator's notes and any attached media.

Operator's Notes:
{user_notes}

Tone & Style:
- You are an UNHINGED HUMMINGBIRD LOVER. You are obsessed with these tiny \
  chaotic birds and you are NOT apologizing for it
- Playful, lightly cheeky, and subtly suggestive (double entendre), but always \
  safe and appropriate for social media
- The energy is "person who installed a surveillance camera for hummingbirds \
  and thinks this is completely normal behavior"
- You are emotionally invested in every visit. Every hummingbird has a \
  personality. You have opinions about their choices
- Natural, conversational, unhinged but lovable — like a neighbor who corners \
  you to talk about hummingbird drama for 20 minutes
- Channel big "I will protect these tiny dinosaurs with my life" energy

Content Guidelines:
- If an image or video is provided, describe what you actually SEE — be specific \
  about what the bird is doing, its colors, behavior
- Weave in the operator's notes naturally, don't just restate them
- Make it sound like someone running a full hummingbird surveillance operation \
  who knows their regulars by sight and has strong feelings about it
- The vibe is unhinged bird parent meets backyard private investigator
- Lean into the absurdity of being THIS invested in a feeder camera

Hard Rules:
- Do NOT mention AI, models, prompts, or automation
- Do NOT explain jokes
- Use 0-1 emojis maximum
- Avoid cliches and generic lines
- 1-3 sentences max per platform
{platform_block}
Output: Return ONLY the caption text. No labels, no extra formatting.
"""


def generate_manual_post(user_notes: str, media_path: Path | None = None,
                         platforms: list[str] | None = None) -> dict[str, str]:
    """Generate captions for a manually composed post, one per platform.

    The operator provides notes/context and optionally a photo or video frame.
    GPT-4o rewrites it in the Backyard Hummers Unhinged voice.
    """
    platforms = platforms or ["Facebook"]
    multi = len(platforms) > 1
    fallback = user_notes or "The backyard hummingbird surveillance continues."

    if not config.OPENAI_API_KEY:
        logger.warning("No OpenAI API key configured, using raw notes as caption")
        return {p: fallback for p in platforms}

    platform_block = _build_platform_block(platforms) if multi else ""

    try:
        client = _get_client()
        prompt = MANUAL_POST_PROMPT.format(
            user_notes=user_notes,
            platform_block=platform_block,
        )

        messages: list = [{"role": "system", "content": prompt}]

        # Attach image if provided (photo or extracted video frame)
        image_url = None
        if media_path and media_path.exists():
            suffix = media_path.suffix.lower()
            if suffix in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
                image_url = _encode_frame(media_path)
            elif suffix in (".mp4", ".mov", ".avi", ".mkv"):
                # Extract a frame from the video for vision analysis
                try:
                    import cv2
                    cap = cv2.VideoCapture(str(media_path))
                    # Seek to 1 second in
                    fps = cap.get(cv2.CAP_PROP_FPS) or 15
                    cap.set(cv2.CAP_PROP_POS_FRAMES, int(fps))
                    ret, frame = cap.read()
                    cap.release()
                    if ret:
                        import tempfile
                        tmp = Path(tempfile.mktemp(suffix=".jpg"))
                        cv2.imwrite(str(tmp), frame)
                        image_url = _encode_frame(tmp)
                        tmp.unlink(missing_ok=True)
                except Exception:
                    logger.warning("Failed to extract frame from video for vision")

        if image_url:
            user_content: list | str = [
                {"type": "text", "text": "Write a post based on the operator's notes and this image/video frame."},
                {"type": "image_url", "image_url": {"url": image_url, "detail": config.VISION_CAPTION_DETAIL}},
            ]
        else:
            user_content = "Write a post based on the operator's notes."

        messages.append({"role": "user", "content": user_content})

        max_tok = 500 if multi else 250
        response = client.chat.completions.create(
            model=config.AZURE_OPENAI_DEPLOYMENT or "gpt-4o",
            messages=messages,
            max_tokens=max_tok,
            temperature=0.95,
        )
        raw = (response.choices[0].message.content or "").strip()

        if multi:
            captions = _parse_platform_captions(raw, platforms)
        else:
            captions = {platforms[0]: raw}

        # Validate — retry once if gibberish
        if not _validate_all_captions(captions):
            logger.warning("Manual caption failed validation, retrying once")
            response = client.chat.completions.create(
                model=config.AZURE_OPENAI_DEPLOYMENT or "gpt-4o",
                messages=messages, max_tokens=max_tok, temperature=0.95,
            )
            raw = (response.choices[0].message.content or "").strip()
            if multi:
                captions = _parse_platform_captions(raw, platforms)
            else:
                captions = {platforms[0]: raw}
            if not _validate_all_captions(captions):
                logger.error("Manual caption failed validation on retry, using fallback")
                return {p: fallback for p in platforms}

        logger.info("Manual post captions: %s", captions)
        return captions

    except _OpenAIError:
        logger.exception("OpenAI API call failed for manual post")
        return {p: fallback for p in platforms}
