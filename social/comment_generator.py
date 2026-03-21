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
