"""Generate funny Facebook post captions using OpenAI / Azure OpenAI."""

import logging
import random

import config

logger = logging.getLogger(__name__)

# Singleton client — reuses connection pool
_client = None


def _get_client():
    global _client
    if _client is not None:
        return _client

    if not config.OPENAI_API_KEY:
        return None

    # Azure OpenAI — needs endpoint, key, and api-version
    if config.AZURE_OPENAI_ENDPOINT:
        from openai import AzureOpenAI
        _client = AzureOpenAI(
            azure_endpoint=config.AZURE_OPENAI_ENDPOINT,
            api_key=config.OPENAI_API_KEY,
            api_version=config.AZURE_OPENAI_API_VERSION,
        )
        logger.info("Using Azure OpenAI (endpoint: %s, deployment: %s)",
                     config.AZURE_OPENAI_ENDPOINT, config.AZURE_OPENAI_DEPLOYMENT)
    else:
        from openai import OpenAI
        _client = OpenAI(api_key=config.OPENAI_API_KEY)
        logger.info("Using OpenAI direct")

    return _client

SYSTEM_PROMPT = """\
You are the voice of a Facebook page called "Backyard Hummers," an automated \
hummingbird feeder camera.

Your job is to write short, engaging captions (1-3 sentences) for hummingbird visits.

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
  - Slightly unhinged observer
  - Dry "AI analysis" humor (rare)
- Do not rely on the same joke pattern repeatedly

Content Guidelines:
- The post accompanies a video of a hummingbird visit
- Prefer specificity over generic phrasing
- Occasionally reference time of day, visit frequency, or behavior \
  (hovering, quick visits, repeat visits, territorial chasing)
- Keep captions concise and punchy

Hard Rules:
- Do NOT mention AI, models, prompts, or automation
- Do NOT explain jokes
- Do NOT use hashtags
- Use 0-1 emojis maximum, and only when it adds value
- Avoid cliches and generic lines
- Avoid repeating phrases like "quick visit," "stopping by," or "back again" too frequently

Style Targets (for guidance only, do not copy):
- "In and out in seconds... impressive."
- "Evening traffic is picking up."
- "This one knew exactly what it was doing."
- "Short visit, but efficient."

Output: Return ONLY the caption text. No labels, no extra formatting.
"""

# Fallback captions if the API is unavailable
FALLBACK_CAPTIONS = [
    "In and out in under ten seconds. Efficient.",
    "This one hovered for a while. Took its time. Respect.",
    "Evening traffic is picking up at the feeder.",
    "Caught one working the backyard feeder again. Dedicated.",
    "Short visit, but that tongue was putting in work.",
]


def generate_comment() -> str:
    """Generate a funny innuendo-laden caption for a hummingbird video post."""
    if not config.OPENAI_API_KEY:
        logger.warning("No OpenAI API key configured, using fallback caption")
        return random.choice(FALLBACK_CAPTIONS)

    try:
        client = _get_client()
        response = client.chat.completions.create(
            model=config.AZURE_OPENAI_DEPLOYMENT or "gpt-4o",
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
You are the voice of a Facebook page called "Backyard Hummers," an automated \
hummingbird feeder camera in {location}.

Write a "good morning" post. The camera just woke up and is ready to watch \
for hummers. Sunrise was at {sunrise}.

Tone & Style:
- Playful, lightly cheeky, subtly suggestive but Facebook-safe
- Natural and conversational, like someone half-awake grabbing coffee and \
  checking the feeder
- Understated humor, never forced

Hard Rules:
- Do NOT mention AI, models, prompts, or automation
- Do NOT explain jokes
- Do NOT use hashtags
- Use 0-1 emojis maximum
- 1-3 sentences max

Style Targets (do not copy):
- "Sun's up. Feeder's full. Let's see who shows up."
- "Another morning in the backyard. The nectar is fresh and so am I."

Output: Return ONLY the caption text. No labels, no extra formatting.
"""

GOOD_NIGHT_PROMPT = """\
You are the voice of a Facebook page called "Backyard Hummers," an automated \
hummingbird feeder camera in {location}.

Write an end-of-day recap post. Today's stats: {detections} hummingbird \
visit(s) caught on camera, {rejected} false alarm(s). Sunset was at {sunset}.

You must include the detection count naturally in the caption.

Tone & Style:
- Playful, lightly cheeky, subtly suggestive but Facebook-safe
- Natural and conversational, like wrapping up a day of backyard watching
- If 0 detections, lean into dry humor about a slow day
- If many detections, play up how busy the backyard was

Hard Rules:
- Do NOT mention AI, models, prompts, or automation
- Do NOT explain jokes
- Do NOT use hashtags
- Use 0-1 emojis maximum
- 1-3 sentences max
- Must include the actual number of detections

Style Targets (do not copy):
- "3 hummers today. The backyard was putting in work."
- "Zero visits. Even the hummers took the day off."
- "7 confirmed sightings. Somebody tell the neighbors."

Output: Return ONLY the caption text. No labels, no extra formatting.
"""


def generate_good_morning(location: str, sunrise: str) -> str:
    """Generate a morning greeting post."""
    if not config.OPENAI_API_KEY:
        return f"Sun's up at {sunrise}. Feeder's full. Let's see who shows up."

    try:
        client = _get_client()
        response = client.chat.completions.create(
            model=config.AZURE_OPENAI_DEPLOYMENT or "gpt-4o",
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
        return f"Sun's up at {sunrise}. Feeder's full. Let's see who shows up."


def generate_good_night(location: str, sunset: str, detections: int, rejected: int) -> str:
    """Generate an end-of-day recap post with hummer tally."""
    if not config.OPENAI_API_KEY:
        return f"{detections} hummer(s) on camera today. Sun went down at {sunset}. See you tomorrow."

    try:
        client = _get_client()
        response = client.chat.completions.create(
            model=config.AZURE_OPENAI_DEPLOYMENT or "gpt-4o",
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
        return f"{detections} hummer(s) on camera today. Sun went down at {sunset}. See you tomorrow."
