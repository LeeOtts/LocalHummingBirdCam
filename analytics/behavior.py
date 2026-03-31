"""Behavior classification for hummingbird sightings using GPT-4o vision."""

import logging

import config

logger = logging.getLogger(__name__)

_VALID_BEHAVIORS = {"feeding", "hovering", "perching", "chasing"}


def classify_behavior(frame_path: str) -> str | None:
    """Classify hummingbird behavior from a single frame using GPT-4o vision.

    Returns one of: "feeding", "hovering", "perching", "chasing", or None.
    Called from the post worker thread (not the detection loop).
    """
    if not config.OPENAI_API_KEY or not frame_path:
        return None

    try:
        import base64
        from pathlib import Path

        img_path = Path(frame_path)
        if not img_path.exists():
            return None

        from social.comment_generator import _get_client
        client = _get_client()
        if not client:
            return None

        # Encode image as base64
        img_data = base64.b64encode(img_path.read_bytes()).decode("utf-8")

        response = client.chat.completions.create(
            model=config.AZURE_OPENAI_DEPLOYMENT or "gpt-4o",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a hummingbird behavior classifier. "
                        "Look at the image and classify the hummingbird's behavior "
                        "as exactly ONE of these words: feeding, hovering, perching, chasing. "
                        "Respond with ONLY that single word, nothing else."
                    ),
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{img_data}",
                                "detail": "low",
                            },
                        },
                    ],
                },
            ],
            max_tokens=10,
            temperature=0.1,
        )

        result = (response.choices[0].message.content or "").strip().lower()
        if result in _VALID_BEHAVIORS:
            logger.info("Behavior classified: %s", result)
            return result

        logger.debug("Unexpected behavior response: %s", result)
        return None

    except Exception:
        logger.debug("Behavior classification failed")
        return None
