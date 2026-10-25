"""Generate the count-off word clips once: fletcher/countoff/<clip_id>.wav + manifest.json.

    python -m fletcher.generate_countoff                 # all 24 words, Eleven v4 (bark_model)
    python -m fletcher.generate_countoff --only co_six_sharp,co_seven_shout
    python -m fletcher.generate_countoff --dry-run       # synthetic words, no API
    python -m fletcher.generate_countoff --demo 70,94,140,220   # render demo_<bpm>.wav
    python -m fletcher.generate_countoff --reanalyze     # re-measure onsets, no API

Each word is its own request, so every clip holds exactly one word. Clips are trimmed to the
audible part; onset_s (see countoff.detect_onset) is measured on the trimmed file.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from pathlib import Path

import numpy as np

from . import countoff as co
from .config import Config
from .text import strip_tags

log = logging.getLogger("fletcher.countoff")

CHAR_BUDGET = 3000


def _analyze(x: np.ndarray, sr: int) -> tuple[np.ndarray, dict]:
    s, e = co.audible_span(x, sr)
    x = x[s:e]
    return x, {"onset_s": round(co.detect_onset(x, sr), 5), "duration_s": round(len(x) / sr, 4),
               "peak": round(float(np.abs(x).max()), 4)}


def _bursts(x: np.ndarray, sr: int, gap_s: float = 0.18) -> int:
    """Number of sound bursts separated by > gap_s of near-silence (1 = a single word)."""
    env = co.envelope(x, sr, lowpass_hz=None)
    on = env > env.max() * 0.05
    n, quiet, seen = 0, 10 ** 9, False
    for v in on[::24]:  # 1 ms steps
        if v:
            if not seen or quiet > gap_s * 1000:
                n += 1
            seen, quiet = True, 0
        else:
            quiet += 1
    return n


_DUR = {"one": .30, "two": .28, "three": .32, "four": .34, "five": .40, "six": .30, "seven": .48, "eight": .36}
_LEVEL = {"quiet": (0.30, 120.0, 1.1), "sharp": (0.55, 150.0, 0.9), "shout": (0.85, 200.0, 1.2)}


def synth_word(word: str, delivery: str, sr: int = co.CLIP_SR) -> np.ndarray:
    """Dry-run stand-in: silence, a hiss for f/s words, then a decaying harmonic 'vowel'."""
    amp, f0, stretch = _LEVEL[delivery]
    rng = np.random.default_rng(co.WORDS.index(word) * 7 + list(_LEVEL).index(delivery))
    parts = [np.zeros(int(0.08 * sr))]
    if word[0] in "fs":
        parts.append(0.03 * rng.standard_normal(int(0.07 * sr)))
    n = int(_DUR[word] * stretch * sr)
    t = np.arange(n) / sr
    env = np.minimum(1.0, t / 0.004) * np.exp(-t / (0.5 * n / sr))
    vowel = sum(np.sin(2 * np.pi * f0 * h * t) / h for h in (1, 2, 3)) / 1.84
    parts += [amp * env * vowel, np.zeros(int(0.2 * sr))]
    return np.concatenate(parts)


async def _speak(voice, text: str) -> bytes:
    async def one():
        yield text
    pcm = b"".join([c async for c in voice.speak(one()) if isinstance(c, bytes)])
    return pcm[:len(pcm) // 2 * 2]


async def generate(cfg: Config, out_dir: Path, only: set[str] | None, dry_run: bool) -> int:
    """Returns ElevenLabs characters sent."""
    out_dir.mkdir(parents=True, exist_ok=True)
    man_path = out_dir / "manifest.json"
    manifest = json.loads(man_path.read_text()) if man_path.exists() else {}
    voice = None
    if not dry_run:
        from .voice import DialogueVoice
        voice = DialogueVoice(cfg, model=cfg.bark_model)
    chars = 0
    sr = co.CLIP_SR
    try:
        for cid, text, word, delivery in co.all_clips():
            if only and cid not in only and word not in only and delivery not in only:
                continue
            if dry_run:
                x, meta = _analyze(synth_word(word, delivery, sr), sr)
            else:
                x = None
                for attempt in range(3):
                    if chars + len(text) > CHAR_BUDGET:
                        raise SystemExit(f"character budget {CHAR_BUDGET} reached")
                    chars += len(text)
                    try:
                        raw = await _speak(voice, text)
                    except Exception as e:
                        log.warning("%s attempt %d failed: %r", cid, attempt + 1, e)
                        await asyncio.sleep(1.0)
                        continue
                    y = np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32768.0
                    if cfg.sample_rate != sr:
                        y = co._resample(y, cfg.sample_rate, sr)
                    y, meta = _analyze(y, sr)
                    b = _bursts(y, sr)
                    if 0.12 <= meta["duration_s"] <= 1.6 and b == 1:
                        x = y
                        break
                    log.warning("%s attempt %d rejected: %.2f s, %d bursts", cid, attempt + 1,
                                meta["duration_s"], b)
                    x = y  # keep the last take if all are odd
                if x is None:
                    log.error("skipped %s", cid)
                    continue
            co.write_wav(out_dir / f"{cid}.wav", x, sr)
            manifest[cid] = {"text": strip_tags(text),
                             "tagged_text": text, "word": word, "delivery": delivery,
                             **meta, "placeholder": dry_run}
            log.info("%-16s %.3f s  onset %.3f s  %s", cid, meta["duration_s"], meta["onset_s"], text)
    finally:
        man_path.write_text(json.dumps(manifest, indent=2))
        if voice:
            await voice.close()
    log.info("wrote %d clips to %s (%d characters)", len(manifest), out_dir, chars)
    return chars


def reanalyze(out_dir: Path) -> None:
    """Re-measure onset_s / duration_s of existing clips (after changing the detector). No API."""
    man_path = out_dir / "manifest.json"
    manifest = json.loads(man_path.read_text())
    for cid, meta in manifest.items():
        x, sr = co.read_wav(out_dir / f"{cid}.wav")
        new = {"onset_s": round(co.detect_onset(x, sr), 5), "duration_s": round(len(x) / sr, 4)}
        if abs(new["onset_s"] - meta.get("onset_s", -1)) > 0.0005:
            log.info("%-16s onset %.3f -> %.3f s", cid, meta.get("onset_s", -1), new["onset_s"])
        meta.update(new)
    man_path.write_text(json.dumps(manifest, indent=2))


def demo(out_dir: Path, bpms: list[float], style: str) -> dict[float, float]:
    """Render demo_<bpm>.wav and return the max |onset - beat| (ms) per bpm."""
    errs = {}
    for bpm in bpms:
        sched = co.schedule(bpm, style=style)
        buf, t0 = co.render_array(sched, clip_dir=out_dir)
        co.write_wav(out_dir / f"demo_{bpm:g}.wav", buf, co.CLIP_SR)
        e = [1000 * v for v in co.measure_onsets(buf, t0, sched)]
        errs[bpm] = max(abs(v) for v in e)
        placed = co.plan(sched, out_dir)
        log.info("demo %g bpm: onset errors %s ms; ratios %s; trimmed %s", bpm,
                 [round(float(v), 2) for v in e], [round(p.ratio, 2) for p in placed], [p.trimmed for p in placed])
    return errs


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--only", help="comma-separated clip ids, words or deliveries")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--out", default=str(co.COUNTOFF_DIR))
    p.add_argument("--demo", help="comma-separated bpms: render demo_<bpm>.wav instead of generating")
    p.add_argument("--reanalyze", action="store_true", help="re-measure onsets of existing clips")
    p.add_argument("--style", default="build", choices=co.STYLES)
    a = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    out = Path(a.out)
    if a.reanalyze:
        reanalyze(out)
        return
    if a.demo:
        for bpm, ms in demo(out, [float(b) for b in a.demo.split(",")], a.style).items():
            print(f"{bpm:g} bpm: max onset error {ms:.3f} ms")
        return
    cfg = Config.from_env()
    if not a.dry_run and not (cfg.elevenlabs_api_key and cfg.elevenlabs_voice_id):
        raise SystemExit("Set ELEVENLABS_API_KEY and ELEVENLABS_VOICE_ID in fletcher/.env (or use --dry-run)")
    only = set(a.only.split(",")) if a.only else None
    chars = asyncio.run(generate(cfg, out, only, a.dry_run))
    print(f"ElevenLabs characters sent: {chars}")


if __name__ == "__main__":
    main()
