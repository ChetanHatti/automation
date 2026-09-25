"""
main.py – Entry point for the daily Tech Fact video pipeline.

Execution order:
  1. Generate a tech fact (Gemini)
  2. Synthesise voiceover (edge-tts)
  3. Download background video (Pexels)
  4. Composite final video (moviepy)
  5. Upload to YouTube (optional – skipped if credentials are missing)

All steps are wrapped in structured error handling with full logging so
failures in CI/CD are easy to diagnose.
"""

import os
import sys
import logging
from pathlib import Path

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Logging – readable in both terminal and GitHub Actions log viewer
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("pipeline")

# Load .env (no-op if file doesn't exist, e.g. in CI where env vars are
# injected by GitHub Secrets)
load_dotenv()


def main() -> None:
    """Run the full video-generation-and-upload pipeline."""

    # -- Step 1: Generate the tech fact --
    logger.info("=== STEP 1/5 - Generating tech fact via Gemini ...")
    try:
        from video_generator import generate_tech_fact
        fact_text = generate_tech_fact()
        logger.info("Fact: %s", fact_text)
    except Exception:
        logger.exception("Failed to generate tech fact.")
        sys.exit(1)

    # -- Step 2: Text-to-speech --
    logger.info("=== STEP 2/5 - Generating voiceover via edge-tts ...")
    try:
        from video_generator import generate_voiceover
        audio_path = generate_voiceover(fact_text)
    except Exception:
        logger.exception("Failed to generate voiceover.")
        sys.exit(1)

    # -- Step 3: Fetch background video --
    logger.info("=== STEP 3/5 - Fetching background video from Pexels ...")
    try:
        from video_generator import fetch_background_video
        bg_path = fetch_background_video()
    except Exception:
        logger.exception("Failed to fetch background video.")
        sys.exit(1)

    # -- Step 4 & 5: Composite final video --
    logger.info("=== STEP 4/5 - Compositing final video ...")
    try:
        from video_generator import compose_video
        final_path = compose_video(fact_text)
    except Exception:
        logger.exception("Failed to composite video.")
        sys.exit(1)

    # -- Step 5: Upload to YouTube --
    logger.info("=== STEP 5/5 - Uploading to YouTube ...")
    token_available = (
        os.path.exists("token.json")
        or os.environ.get("YOUTUBE_OAUTH_TOKEN")
    )
    if not token_available:
        logger.warning(
            "YouTube upload SKIPPED - no OAuth token found. "
            "Run locally once to complete the OAuth flow, then store "
            "the token as a GitHub Secret."
        )
    else:
        try:
            from youtube_uploader import upload_to_youtube
            video_id = upload_to_youtube(final_path, fact_text)
            logger.info("YouTube video ID: %s", video_id)
        except Exception:
            logger.exception("Failed to upload to YouTube (non-fatal).")
            # Upload failure shouldn't crash the whole pipeline -- the video
            # is still saved locally and can be uploaded manually.

    logger.info("=== PIPELINE COMPLETE ===")
    logger.info("Output -> %s", final_path)


if __name__ == "__main__":
    main()
