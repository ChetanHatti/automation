"""
video_generator.py – Core pipeline for generating a Tech Fact short-form video.

Pipeline steps:
  1. Generate a ~30-word tech fact via Google Gemini (free tier).
  2. Synthesise voiceover with edge-tts (free, no key).
  3. Download a vertical tech/abstract background from Pexels (free).
  4. Composite everything with moviepy → final_video.mp4
"""

import os
import re
import json
import asyncio
import logging
import textwrap
import random
import time

import requests
import google.generativeai as genai
from moviepy import (
    VideoFileClip,
    AudioFileClip,
    TextClip,
    CompositeVideoClip,
    concatenate_videoclips,
    ColorClip,
)
import edge_tts

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")
AUDIO_PATH = os.path.join(OUTPUT_DIR, "audio.mp3")
BG_VIDEO_PATH = os.path.join(OUTPUT_DIR, "bg.mp4")
FINAL_VIDEO_PATH = os.path.join(OUTPUT_DIR, "final_video.mp4")

VIDEO_WIDTH = 1080
VIDEO_HEIGHT = 1920
FPS = 30

# edge-tts voice -- cheerful, friendly female narrator (great for kids)
TTS_VOICE = "en-US-AnaNeural"

logger = logging.getLogger(__name__)


def _find_bold_font() -> str:
    """Locate a bold sans-serif .ttf font on the current platform."""
    candidates = [
        # Windows
        r"C:\Windows\Fonts\arialbd.ttf",
        r"C:\Windows\Fonts\Arial Bold.ttf",
        # Linux (common DejaVu fallback)
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        # macOS
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/Library/Fonts/Arial Bold.ttf",
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    # Last resort – let Pillow try to resolve it
    return "Arial-Bold"


# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  STEP 1 -- Generate Fun Fact for Kids via Gemini                       ║
# ╚══════════════════════════════════════════════════════════════════════════╝

def generate_tech_fact() -> str:
    """Query Google Gemini (free tier) for a fun, kid-friendly fact.

    Returns:
        A clean string (no emojis, no hashtags) under 40 words.
    """
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise EnvironmentError("GEMINI_API_KEY is not set.")

    genai.configure(api_key=api_key)

    prompt = (
        "You are a fun educational content writer for children aged 5 to 12. "
        "Generate ONE amazing, mind-blowing fact that kids would LOVE. "
        "Pick randomly from these topics: animals, space, dinosaurs, "
        "the ocean, the human body, nature, weather, bugs, robots, or food.\n"
        "Rules:\n"
        "- Exactly 20-35 words.\n"
        "- No emojis, no hashtags, no quotation marks.\n"
        "- Use simple, exciting words a 7-year-old can understand.\n"
        "- Start with a WOW hook like 'Did you know' or 'Guess what'.\n"
        "- Make it feel magical and fun.\n"
        "- Output ONLY the fact text, nothing else."
    )

    # Model fallback chain – ordered newest → oldest (stable SDK names).
    # If Google deprecates the top model, the next one is tried automatically.
    GEMINI_MODELS = [
        "gemini-1.5-flash",        # fast, free, always available
        "gemini-1.5-flash-8b",     # lighter free-tier variant
        "gemini-1.5-pro",          # higher quality fallback
        "gemini-pro",              # legacy but reliable last resort
    ]

    response = None
    last_error = None

    for model_name in GEMINI_MODELS:
        max_retries = 3
        for attempt in range(1, max_retries + 1):
            try:
                logger.info("Trying Gemini model: %s (attempt %d/%d)", model_name, attempt, max_retries)
                model = genai.GenerativeModel(model_name)
                response = model.generate_content(prompt)
                logger.info("Success with model: %s", model_name)
                break  # inner retry loop
            except Exception as e:
                last_error = e
                err_str = str(e)
                # 404 / NOT_FOUND = model deprecated → skip to next immediately
                if any(x in err_str for x in ["404", "NOT_FOUND", "no longer available", "not found for API"]):
                    logger.warning(
                        "Model '%s' unavailable (deprecated/removed). Trying next model ...",
                        model_name,
                    )
                    break  # skip retries, go to next model
                # Other errors (rate limit, overload) → retry with backoff
                if attempt < max_retries:
                    wait = attempt * 15
                    logger.warning(
                        "Gemini error on '%s' (attempt %d/%d): %s – retrying in %ds ...",
                        model_name, attempt, max_retries, type(e).__name__, wait,
                    )
                    time.sleep(wait)
                else:
                    logger.warning("All retries exhausted for '%s'. Trying next model ...", model_name)
        if response is not None:
            break  # got a response, exit model loop

    if response is None:
        raise RuntimeError(
            f"All Gemini models failed. Last error: {last_error}\n"
            "Check https://ai.google.dev/gemini-api/docs/models for currently available models."
        )

    fact = response.text.strip()

    # Defensive cleanup – strip residual emojis/hashtags if the model slips
    fact = re.sub(r"#\w+", "", fact)                      # hashtags
    fact = re.sub(r"[^\x00-\x7F]+", "", fact)             # non-ASCII (emojis)
    fact = re.sub(r'["""]', "", fact)                      # smart/straight quotes
    fact = fact.strip()

    word_count = len(fact.split())
    logger.info("Generated tech fact (%d words): %s", word_count, fact)

    if word_count > 50:
        logger.warning("Fact exceeds 50 words – truncating to first 40.")
        fact = " ".join(fact.split()[:40])

    return fact


# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  STEP 2 – Text-to-Speech via edge-tts                                  ║
# ╚══════════════════════════════════════════════════════════════════════════╝

async def _synthesise_speech(text: str, output_path: str) -> None:
    """Internal async helper – edge-tts requires an event loop."""
    communicate = edge_tts.Communicate(text, TTS_VOICE)
    await communicate.save(output_path)


def _run_async(coro):
    """Safely run a coroutine whether or not an event loop already exists.

    asyncio.run() raises RuntimeError when called inside an already-running
    loop (e.g. Jupyter, some CI environments). This helper handles both cases.
    """
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # We're inside a running loop – schedule as a task
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(asyncio.run, coro)
                return future.result()
        else:
            return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)


def generate_voiceover(text: str) -> str:
    """Generate an MP3 voiceover from *text* using Microsoft Edge TTS.

    Returns:
        Absolute path to the saved audio file.
    """
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    _run_async(_synthesise_speech(text, AUDIO_PATH))
    if not os.path.exists(AUDIO_PATH) or os.path.getsize(AUDIO_PATH) < 100:
        raise RuntimeError(f"Voiceover generation failed – file missing or empty: {AUDIO_PATH}")
    logger.info("Voiceover saved -> %s", AUDIO_PATH)
    return AUDIO_PATH


# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  STEP 3 – Fetch Background Video from Pexels                           ║
# ╚══════════════════════════════════════════════════════════════════════════╝

# Curated search terms for colorful, kid-friendly background footage
_PEXELS_QUERIES = [
    "colorful abstract",
    "rainbow colors",
    "ocean underwater",
    "stars galaxy space",
    "cute animals",
    "nature flowers colorful",
    "jellyfish underwater",
    "northern lights aurora",
    "bubbles colorful",
    "butterfly nature",
]


def fetch_background_video() -> str:
    """Download a random vertical video from Pexels.

    Filters for portrait orientation so it suits 9:16 shorts.

    Returns:
        Absolute path to the downloaded background video.
    """
    api_key = os.environ.get("PEXELS_API_KEY")
    if not api_key:
        raise EnvironmentError("PEXELS_API_KEY is not set.")

    query = random.choice(_PEXELS_QUERIES)
    logger.info("Searching Pexels for: '%s'", query)

    headers = {"Authorization": api_key}
    params = {
        "query": query,
        "orientation": "portrait",
        "size": "medium",
        "per_page": 15,
        "page": random.randint(1, 3),
    }
    resp = requests.get(
        "https://api.pexels.com/videos/search",
        headers=headers,
        params=params,
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    videos = data.get("videos", [])
    if not videos:
        raise RuntimeError(f"No videos found on Pexels for query '{query}'.")

    # Pick a random video and grab the best-quality file link
    chosen = random.choice(videos)
    video_files = chosen.get("video_files", [])

    # Prefer HD portrait files; fall back to first available
    best = None
    for vf in video_files:
        w = vf.get("width", 0)
        h = vf.get("height", 0)
        if h > w:  # portrait
            if best is None or vf.get("height", 0) > best.get("height", 0):
                best = vf
    if best is None:
        best = video_files[0]

    download_url = best["link"]
    logger.info("Downloading background video (%dp) ...", best.get("height", 0))

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    vid_resp = requests.get(download_url, stream=True, timeout=120)
    vid_resp.raise_for_status()
    with open(BG_VIDEO_PATH, "wb") as f:
        for chunk in vid_resp.iter_content(chunk_size=1024 * 1024):
            f.write(chunk)

    logger.info("Background video saved -> %s", BG_VIDEO_PATH)
    return BG_VIDEO_PATH


# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  STEP 4 & 5 – Composite Final Video with moviepy                       ║
# ╚══════════════════════════════════════════════════════════════════════════╝

def _wrap_text(text: str, max_chars_per_line: int = 28) -> str:
    """Word-wrap *text* so it fits neatly inside the 9:16 frame."""
    return "\n".join(textwrap.wrap(text, width=max_chars_per_line))


def _loop_clip_to_duration(clip: VideoFileClip, target_duration: float) -> VideoFileClip:
    """Loop or trim *clip* so its duration matches *target_duration*.

    BUG FIX: moviepy v2 does not support reusing the same clip instance
    multiple times in concatenate_videoclips – each element must be an
    independent object. We reload from the source file for each copy.
    """
    if clip.duration >= target_duration:
        return clip.subclipped(0, target_duration)

    # Need to loop – create fresh copies from the same source file
    loops_needed = int(target_duration // clip.duration) + 1
    source_file = clip.filename
    copies = [VideoFileClip(source_file) for _ in range(loops_needed)]
    looped = concatenate_videoclips(copies)
    return looped.subclipped(0, target_duration)


def compose_video(fact_text: str) -> str:
    """Build the final 9:16 video: background + voiceover + text overlay.

    Returns:
        Absolute path to the rendered final video.
    """
    # ── Load audio & measure duration ──
    audio = AudioFileClip(AUDIO_PATH)
    duration = audio.duration

    # Pad with a tiny buffer so the last word doesn't feel cut off
    duration += 0.8
    logger.info("Target video duration: %.1fs", duration)

    # Enforce 15–30s range
    if duration < 15:
        duration = 15.0
    elif duration > 30:
        duration = 30.0
        logger.warning("Audio exceeds 30s – video will be capped at 30s.")

    # ── Load & prepare background clip ──
    bg = VideoFileClip(BG_VIDEO_PATH)
    bg = _loop_clip_to_duration(bg, duration)

    # Scale/crop to exact 1080×1920
    # Resize to fill width, then crop vertically (center)
    scale_factor = max(VIDEO_WIDTH / bg.w, VIDEO_HEIGHT / bg.h)
    bg = bg.resized(scale_factor)
    bg = bg.cropped(
        x_center=bg.w / 2,
        y_center=bg.h / 2,
        width=VIDEO_WIDTH,
        height=VIDEO_HEIGHT,
    )

    # ── Semi-transparent dark overlay for text readability ──
    overlay = ColorClip(
        size=(VIDEO_WIDTH, VIDEO_HEIGHT),
        color=(0, 0, 0),
    ).with_opacity(0.35).with_duration(duration)

    # ── Text overlay – shadow layer (offset for depth) ──
    wrapped = _wrap_text(fact_text)
    bold_font = _find_bold_font()

    # BUG FIX: Build shadow first, capture its height BEFORE using it in
    # a lambda – referencing `shadow_clip.h` inside the lambda it's
    # defined on causes an AttributeError in moviepy v2.
    shadow_clip = TextClip(
        text=wrapped,
        font_size=62,
        color="black",
        font=bold_font,
        method="caption",
        size=(VIDEO_WIDTH - 120, None),
        text_align="center",
    ).with_duration(duration)

    # Capture height now (after clip is created) and use a static position
    shadow_y = VIDEO_HEIGHT // 2 - shadow_clip.h // 2 + 4
    shadow_clip = shadow_clip.with_position(("center", shadow_y))

    # -- Text overlay - main bright text --
    text_clip = TextClip(
        text=wrapped,
        font_size=62,
        color="white",
        font=bold_font,
        method="caption",
        size=(VIDEO_WIDTH - 120, None),
        text_align="center",
    ).with_position(("center", "center")).with_duration(duration)

    # BUG FIX: Clamp audio to the final video duration so subclipped
    # never receives an end time beyond the audio's actual length.
    audio_duration = min(audio.duration, duration)
    synced_audio = audio.subclipped(0, audio_duration)

    # ── Composite everything ──
    final = CompositeVideoClip(
        [bg, overlay, shadow_clip, text_clip],
        size=(VIDEO_WIDTH, VIDEO_HEIGHT),
    ).with_audio(synced_audio)

    # BUG FIX: Do NOT set final.duration manually after with_audio();
    # moviepy v2 computes duration from clips – overwriting it corrupts
    # the timeline. Use with_end() instead if truncation is needed.
    if final.duration > duration:
        final = final.with_end(duration)

    # ── Render ──
    logger.info("Rendering final video ...")
    final.write_videofile(
        FINAL_VIDEO_PATH,
        fps=FPS,
        codec="libx264",
        audio_codec="aac",
        preset="medium",
        threads=4,
        logger=None,  # suppress moviepy's verbose bar in CI
    )

    # Verify output was actually created
    if not os.path.exists(FINAL_VIDEO_PATH) or os.path.getsize(FINAL_VIDEO_PATH) < 1000:
        raise RuntimeError(f"Video render failed – output file missing or too small: {FINAL_VIDEO_PATH}")

    # Cleanup moviepy resources
    audio.close()
    bg.close()
    final.close()

    logger.info("Final video saved -> %s", FINAL_VIDEO_PATH)
    return FINAL_VIDEO_PATH
