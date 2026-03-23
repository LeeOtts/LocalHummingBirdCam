"""Generate funny Facebook post captions using OpenAI / Azure OpenAI."""

import collections
import logging
import random

import config

logger = logging.getLogger(__name__)

# Singleton client — reuses connection pool, recreated when config changes
_client = None
_client_config: tuple = ()

# Rolling buffer of recent captions to avoid repetition
MAX_RECENT_CAPTIONS = 10
_recent_captions: collections.deque = collections.deque(maxlen=MAX_RECENT_CAPTIONS)


def _get_client():
    global _client, _client_config
    current_config = (
        config.OPENAI_API_KEY,
        config.AZURE_OPENAI_ENDPOINT,
        config.AZURE_OPENAI_API_VERSION,
    )

    if _client is not None and _client_config == current_config:
        return _client

    if not config.OPENAI_API_KEY:
        _client = None
        _client_config = ()
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

    _client_config = current_config
    return _client

SYSTEM_PROMPT = """\
You are the voice of a Facebook page called "Backyard Hummers," an automated \
hummingbird feeder camera.

Your job is to write short, engaging captions (1-3 sentences) for hummingbird visits.

Visit Context:
- Visit #{visit_number} today ({detections} total so far, {rejected} false alarm(s))
- Time: {time_of_day} ({day_part}) on {day_of_week}
- Month: {month}
- Time since last visitor: {since_last}
- Sunrise: {sunrise} / Sunset: {sunset}
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
- Use the context naturally — don't force every detail into every caption
- Prefer specificity over generic phrasing
- Reference behavior: hovering, quick visits, repeat visits, territorial chasing, \
  tongue work, perching, dive-bombing

Hard Rules:
- Do NOT mention AI, models, prompts, or automation
- Do NOT explain jokes
- Do NOT use hashtags
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


def generate_comment(detections: int = 0, rejected: int = 0, **kwargs) -> str:
    """Generate a funny innuendo-laden caption for a hummingbird video post."""
    if not config.OPENAI_API_KEY:
        logger.warning("No OpenAI API key configured, using fallback caption")
        return random.choice(FALLBACK_CAPTIONS)

    try:
        client = _get_client()
        # Build context with defaults for any missing fields
        visit_number = kwargs.get("visit_number", detections)
        since_last = _format_since_last(kwargs.get("seconds_since_last"))
        milestone = kwargs.get("milestone")
        milestone_line = f"- Milestone: {milestone}" if milestone else ""

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
            milestone_line=milestone_line,
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
        messages.append({"role": "user", "content":
            "Write a post for a new hummingbird sighting video."})
        response = client.chat.completions.create(
            model=config.AZURE_OPENAI_DEPLOYMENT or "gpt-4o",
            messages=messages,
            max_tokens=200,
            temperature=0.9,
        )
        caption = (response.choices[0].message.content or "").strip()
        _recent_captions.append(caption)
        logger.info("Generated caption: %s", caption)
        return caption

    except Exception:
        logger.exception("OpenAI API call failed, using fallback caption")
        return random.choice(FALLBACK_CAPTIONS)


GOOD_MORNING_PROMPT = """\
You are the voice of a Facebook page called "Backyard Hummers," an automated \
hummingbird feeder camera in {location}.

Write a "good morning" post. The camera just woke up and is ready to watch \
for hummers.

Context:
- Day: {day_of_week}
- Month: {month}
- Sunrise: {sunrise}
- Yesterday's tally: {yesterday_text}

Tone & Style:
- Playful, lightly cheeky, subtly suggestive but Facebook-safe
- Natural and conversational, like someone half-awake grabbing coffee and \
  checking the feeder
- Understated humor, never forced

Engagement Hooks (use occasionally, not every post):
- Tease a prediction ("Over/under on 5 today?")
- Reference yesterday's tally to set expectations
- Seasonal framing (spring arrivals, summer heat, fall migration)
- Day-of-week personality (lazy Sunday, Monday back to work)

Hard Rules:
- Do NOT mention AI, models, prompts, or automation
- Do NOT explain jokes
- Do NOT use hashtags
- Use 0-1 emojis maximum
- 1-3 sentences max

Style Targets (do not copy):
- "Sun's up. Feeder's full. Let's see who shows up."
- "Monday morning. The hummers don't take weekends off and neither do we."
- "Yesterday was 8 visits. Think we can beat that?"
- "April in the backyard. Migration season. The roster is about to get interesting."

Output: Return ONLY the caption text. No labels, no extra formatting.
"""

GOOD_NIGHT_PROMPT = """\
You are the voice of a Facebook page called "Backyard Hummers," an automated \
hummingbird feeder camera in {location}.

Write an end-of-day recap post.

Context:
- {detections} hummingbird visit(s) caught on camera, {rejected} false alarm(s)
- Day: {day_of_week}
- Month: {month}
- Sunset: {sunset}
- Busiest hour: {peak_hour_text}
- {record_text}

You must include the detection count naturally in the caption.

Tone & Style:
- Playful, lightly cheeky, subtly suggestive but Facebook-safe
- Natural and conversational, like wrapping up a day of backyard watching
- If 0 detections, lean into dry humor about a slow day
- If many detections, play up how busy the backyard was

Engagement Hooks (use occasionally, not every post):
- Ask followers to predict tomorrow's count
- Compare to the record if relevant
- Tease tomorrow ("Same time tomorrow?", "See you at sunrise")
- Weekend wrap-up energy vs weeknight wind-down

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
- "11 confirmed sightings. New personal best. The feeder earned its keep today."
- "7 visits, most of them before 10 AM. Early risers run this yard."
- "Think we'll top 7 tomorrow? Place your bets."

Output: Return ONLY the caption text. No labels, no extra formatting.
"""


def generate_good_morning(location: str, sunrise: str, **kwargs) -> str:
    """Generate a morning greeting post."""
    if not config.OPENAI_API_KEY:
        return f"Sun's up at {sunrise}. Feeder's full. Let's see who shows up."

    yesterday = kwargs.get("yesterday_detections")
    yesterday_text = f"{yesterday} visit(s) yesterday" if yesterday is not None else "No data"

    try:
        client = _get_client()
        response = client.chat.completions.create(
            model=config.AZURE_OPENAI_DEPLOYMENT or "gpt-4o",
            messages=[
                {"role": "system", "content": GOOD_MORNING_PROMPT.format(
                    location=location,
                    sunrise=sunrise,
                    day_of_week=kwargs.get("day_of_week", ""),
                    month=kwargs.get("month", ""),
                    yesterday_text=yesterday_text,
                )},
                {"role": "user", "content": "Write this morning's post."},
            ],
            max_tokens=200,
            temperature=0.9,
        )
        caption = (response.choices[0].message.content or "").strip()
        logger.info("Morning post: %s", caption)
        return caption

    except Exception:
        logger.exception("OpenAI API call failed for morning post")
        return f"Sun's up at {sunrise}. Feeder's full. Let's see who shows up."


def generate_good_night(location: str, sunset: str, detections: int, rejected: int,
                        **kwargs) -> str:
    """Generate an end-of-day recap post with hummer tally."""
    if not config.OPENAI_API_KEY:
        return f"{detections} hummer(s) on camera today. Sun went down at {sunset}. See you tomorrow."

    peak_hour = kwargs.get("peak_hour")
    peak_hour_text = f"{peak_hour}" if peak_hour else "N/A"
    is_record = kwargs.get("is_record", False)
    record_text = "New all-time record!" if is_record else ""

    try:
        client = _get_client()
        response = client.chat.completions.create(
            model=config.AZURE_OPENAI_DEPLOYMENT or "gpt-4o",
            messages=[
                {"role": "system", "content": GOOD_NIGHT_PROMPT.format(
                    location=location, sunset=sunset,
                    detections=detections, rejected=rejected,
                    day_of_week=kwargs.get("day_of_week", ""),
                    month=kwargs.get("month", ""),
                    peak_hour_text=peak_hour_text,
                    record_text=record_text,
                )},
                {"role": "user", "content": "Write tonight's end-of-day recap post."},
            ],
            max_tokens=200,
            temperature=0.9,
        )
        caption = (response.choices[0].message.content or "").strip()
        logger.info("Night post: %s", caption)
        return caption

    except Exception:
        logger.exception("OpenAI API call failed for night post")
        return f"{detections} hummer(s) on camera today. Sun went down at {sunset}. See you tomorrow."
