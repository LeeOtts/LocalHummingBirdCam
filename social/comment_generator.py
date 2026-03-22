"""Generate funny Facebook post captions using OpenAI ChatGPT."""

import logging
import random

from openai import OpenAI

import config

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You write short, funny Facebook posts for a page called "Backyard Hummers" \
that features hummingbird feeder camera footage.

The humor plays on the double meaning of "hummers" and "backyard." Keep it \
PG-13 — suggestive and cheeky, never explicit. Think dad-joke-meets-innuendo.

Weave in real hummingbird facts when you can (wing speed, tongue length, \
migration, nectar, hovering ability, territorial behavior, etc.) but twist \
them into double entendres.

Rules:
- 1-3 sentences max
- Can include 1-2 relevant emojis
- Vary your style: sometimes a one-liner, sometimes a mini-story, sometimes \
  a fake quote from the bird
- Never repeat the same joke structure twice
- The post accompanies a video, so you can reference "caught on camera" or \
  "check out this visitor"
"""

# Fallback captions if the API is unavailable
FALLBACK_CAPTIONS = [
    "Caught another hummer showing off in the backyard! Those tongue skills are unmatched. 🐦",
    "This little visitor really knows how to work a tube. 80 licks per second! 👀",
    "Nothing like a backyard hummer going at it for 30 seconds straight. Nature is beautiful. 🌺",
    "Someone's thirsty in the backyard again. Can't blame 'em — it's hot out there! 💦",
    "This hummer came in fast, hovered like a pro, and drained the whole thing. Impressive stamina! 🐦",
]


def generate_comment() -> str:
    """Generate a funny innuendo-laden caption for a hummingbird video post."""
    if not config.OPENAI_API_KEY:
        logger.warning("No OpenAI API key configured, using fallback caption")
        return random.choice(FALLBACK_CAPTIONS)

    try:
        client = OpenAI(api_key=config.OPENAI_API_KEY)
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": "Write a post for a new hummingbird sighting video."},
            ],
            max_tokens=200,
            temperature=1.0,
        )
        caption = response.choices[0].message.content.strip()
        logger.info("Generated caption: %s", caption)
        return caption

    except Exception:
        logger.exception("OpenAI API call failed, using fallback caption")
        return random.choice(FALLBACK_CAPTIONS)


GOOD_MORNING_PROMPT = """\
You write short, funny Facebook posts for a page called "Backyard Hummers" \
that features hummingbird feeder camera footage from a backyard in {location}.

Write a "good morning" post announcing the camera is waking up and ready \
to catch some hummers today. Play on the double meaning of "hummers" and \
"backyard." Keep it PG-13 — suggestive and cheeky, never explicit.

Mention that sunrise was at {sunrise} and you're up and ready for action.

Rules:
- 1-3 sentences max
- Can include 1-2 relevant emojis
- Never repeat the same joke structure twice
"""

GOOD_NIGHT_PROMPT = """\
You write short, funny Facebook posts for a page called "Backyard Hummers" \
that features hummingbird feeder camera footage from a backyard in {location}.

Write a "good night / end of day recap" post. Today we spotted {detections} \
hummer(s) and rejected {rejected} false alarm(s). Sunset was at {sunset}.

Include the tally: "{detections} hummers caught on camera today!"
Play on the double meaning of "hummers" and "backyard." Keep it PG-13 — \
suggestive and cheeky, never explicit. Refer to the day's tally in a fun way.

If 0 detections, make it a "dry spell" or "blue balls" type joke.
If lots of detections, make it a "busy day in the backyard" type joke.

Rules:
- 1-3 sentences max
- Must include the detection count
- Can include 1-2 relevant emojis
- Never repeat the same joke structure twice
"""


def generate_good_morning(location: str, sunrise: str) -> str:
    """Generate a morning greeting post."""
    if not config.OPENAI_API_KEY:
        return f"Rise and shine! The backyard cam is up and ready to catch some hummers. Sunrise at {sunrise}! ☀️🐦"

    try:
        client = OpenAI(api_key=config.OPENAI_API_KEY)
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": GOOD_MORNING_PROMPT.format(
                    location=location, sunrise=sunrise
                )},
                {"role": "user", "content": "Write this morning's post."},
            ],
            max_tokens=200,
            temperature=1.0,
        )
        caption = response.choices[0].message.content.strip()
        logger.info("Morning post: %s", caption)
        return caption

    except Exception:
        logger.exception("OpenAI API call failed for morning post")
        return f"Rise and shine! The backyard cam is up and ready to catch some hummers. Sunrise at {sunrise}! ☀️🐦"


def generate_good_night(location: str, sunset: str, detections: int, rejected: int) -> str:
    """Generate an end-of-day recap post with hummer tally."""
    if not config.OPENAI_API_KEY:
        return f"That's a wrap! {detections} hummer(s) caught on camera today. Sunset at {sunset}. See you tomorrow! 🌅🐦"

    try:
        client = OpenAI(api_key=config.OPENAI_API_KEY)
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": GOOD_NIGHT_PROMPT.format(
                    location=location, sunset=sunset,
                    detections=detections, rejected=rejected,
                )},
                {"role": "user", "content": "Write tonight's end-of-day recap post."},
            ],
            max_tokens=200,
            temperature=1.0,
        )
        caption = response.choices[0].message.content.strip()
        logger.info("Night post: %s", caption)
        return caption

    except Exception:
        logger.exception("OpenAI API call failed for night post")
        return f"That's a wrap! {detections} hummer(s) caught on camera today. Sunset at {sunset}. See you tomorrow! 🌅🐦"
