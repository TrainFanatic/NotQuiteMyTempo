"""Generate the bark library once: fletcher/barks/<clip_id>.wav + manifest.json.

    python -m fletcher.generate_barks            # all clips, best-quality v4 model
    python -m fletcher.generate_barks --only rushing,dragging
    python -m fletcher.generate_barks --dry-run  # hum placeholders, no API

Then hand the .wav files to Adam: Unity > Assets/Resources/Barks/ (file name = clip_id).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import wave

from .barks import BARK_DIR, MANIFEST, all_clips
from .config import Config
from .main import setup_logging
from .text import strip_tags
from .voice import DialogueVoice, tone_pcm

log = logging.getLogger("fletcher.barks")


async def _one(text: str):
    yield text


async def generate(cfg: Config, only: set[str] | None, dry_run: bool) -> None:
    BARK_DIR.mkdir(exist_ok=True)
    manifest = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {}
    voice = None if dry_run else DialogueVoice(cfg, model=cfg.bark_model)
    for clip_id, text, category in all_clips():
        if only and category not in only and clip_id not in only:
            continue
        if dry_run:
            pcm = tone_pcm(0.3 * len(strip_tags(text).split()) + 0.2, cfg.sample_rate)
        else:
            pcm = b""
            for attempt in range(3):
                try:
                    pcm = b"".join([c async for c in voice.speak(_one(text))])
                    break
                except Exception as e:
                    log.warning("%s attempt %d failed: %r", clip_id, attempt + 1, e)
                    await asyncio.sleep(1.0)
            if not pcm:
                log.error("skipped %s", clip_id)
                continue
        if len(pcm) % 2:
            pcm = pcm[:-1]
        path = BARK_DIR / f"{clip_id}.wav"
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(cfg.sample_rate)
            w.writeframes(pcm)
        dur = len(pcm) / 2 / cfg.sample_rate
        manifest[clip_id] = {"text": strip_tags(text), "tagged_text": text, "category": category,
                             "duration_s": round(dur, 3), "placeholder": dry_run}
        log.info("%-11s %.2f s  %s", clip_id, dur, text)
    MANIFEST.write_text(json.dumps(manifest, indent=2))
    if voice:
        await voice.close()
    log.info("wrote %d clips to %s", len(manifest), BARK_DIR)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--only", help="comma-separated categories or clip ids")
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()
    cfg = Config.from_env()
    setup_logging(cfg.log_level)
    if not a.dry_run and not (cfg.elevenlabs_api_key and cfg.elevenlabs_voice_id):
        raise SystemExit("Set ELEVENLABS_API_KEY and ELEVENLABS_VOICE_ID in fletcher/.env (or use --dry-run)")
    only = set(a.only.split(",")) if a.only else None
    asyncio.run(generate(cfg, only, a.dry_run))


if __name__ == "__main__":
    main()
