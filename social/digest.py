"""Weekly digest post generator for Backyard Hummers."""

import logging
from pathlib import Path

import config

logger = logging.getLogger(__name__)

DIGEST_PROMPT = """\
You are the voice of "Backyard Hummers," an automated hummingbird feeder camera.

Write a weekly recap post summarizing the past 7 days of hummingbird activity.

Stats:
- Total visits this week: {total}
- Last week's total: {last_week} ({trend} from last week)
- Busiest day: {busiest_day}
- Best caption of the week: "{best_caption}"

Tone & Style:
- Playful, lightly cheeky, and engaging
- Weave the numbers into a story — don't just list stats
- End with something that invites followers to stay tuned
- 2-4 sentences

Hard Rules:
- Do NOT mention AI, models, prompts, or automation
- Do NOT use hashtags
- Use 0-1 emojis maximum

Output: Return ONLY the caption text.
"""


def generate_weekly_digest(db) -> str | None:
    """Generate a weekly digest caption from the sightings database."""
    summary = db.get_weekly_summary()

    if summary["total"] == 0 and summary["last_week_total"] == 0:
        return None

    try:
        from social.comment_generator import _get_client, _OpenAIError

        client = _get_client()
        if not client:
            return _fallback_digest(summary)

        busiest = summary.get("busiest_day")
        busiest_text = f"{busiest['date']} ({busiest['total_detections']} visits)" if busiest else "N/A"

        response = client.chat.completions.create(
            model=config.AZURE_OPENAI_DEPLOYMENT or "gpt-4o",
            messages=[
                {"role": "system", "content": DIGEST_PROMPT.format(
                    total=summary["total"],
                    last_week=summary["last_week_total"],
                    trend=summary["trend"],
                    busiest_day=busiest_text,
                    best_caption=(summary.get("best_caption") or "N/A")[:200],
                )},
                {"role": "user", "content": "Write this week's recap post."},
            ],
            max_tokens=250,
            temperature=0.9,
        )
        caption = (response.choices[0].message.content or "").strip()
        logger.info("Weekly digest: %s", caption)
        return caption

    except Exception:
        logger.exception("Failed to generate weekly digest")
        return _fallback_digest(summary)


def _fallback_digest(summary: dict) -> str:
    return (
        f"Weekly recap: {summary['total']} hummingbird visits this week"
        f" ({'up' if summary['trend'] == 'up' else 'down'} from {summary['last_week_total']} last week)."
        f" See you next week."
    )


def create_thumbnail_collage(db, output_path: Path, grid_size: int = 6) -> bool:
    """Create a collage of recent sighting thumbnails using OpenCV."""
    try:
        import cv2
        import numpy as np

        sightings = db.get_sightings(days=7, limit=grid_size)
        images = []

        for s in sightings:
            frame = s.get("frame_path")
            if frame and Path(frame).exists():
                img = cv2.imread(frame)
                if img is not None:
                    images.append(img)

        if not images:
            return False

        # Pad to grid_size if needed
        while len(images) < grid_size:
            images.append(images[-1])
        images = images[:grid_size]

        # Resize all to same dimensions
        cell_w, cell_h = 320, 240
        resized = [cv2.resize(img, (cell_w, cell_h)) for img in images]

        # Arrange in 2 rows x 3 cols (or adjust)
        cols = 3
        rows = (len(resized) + cols - 1) // cols
        collage_rows = []
        for r in range(rows):
            row_imgs = resized[r * cols:(r + 1) * cols]
            while len(row_imgs) < cols:
                row_imgs.append(np.zeros((cell_h, cell_w, 3), dtype=np.uint8))
            collage_rows.append(np.hstack(row_imgs))

        collage = np.vstack(collage_rows)
        cv2.imwrite(str(output_path), collage)
        logger.info("Created thumbnail collage: %s", output_path)
        return True

    except Exception:
        logger.exception("Failed to create thumbnail collage")
        return False
