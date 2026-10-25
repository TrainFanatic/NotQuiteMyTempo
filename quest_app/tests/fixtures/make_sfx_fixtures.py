"""Write the SFX fixtures for tests/test_sfx.gd (stdlib only):  python3 make_sfx_fixtures.py

sfx/tick.wav            50 ms decaying 2 kHz sine, transient at t=0
sfx/clap.wav            150 ms decaying noise burst (fixed seed), transient at t=0
sfx/sfx_manifest.json   the two sounds, in the asset repo's manifest format
sfx/sfx_cues.json       cues for the test stand-ins' animations ("loop_a" 2 s looping,
                        "shot_b" 1 s one-shot), plus band tables that exercise the
                        member -> kind -> default lookup
Both WAVs are mono, 16-bit, 22,050 Hz, like the real assets.
"""

import json
import math
import random
import struct
import wave
from pathlib import Path

HERE = Path(__file__).resolve().parent / "sfx"
RATE = 22050


def write_wav(path, samples):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(b"".join(struct.pack("<h", max(-32767, min(32767, round(s * 32767)))) for s in samples))


def tick():
    n = round(0.05 * RATE)
    return [0.8 * math.exp(-i / (0.008 * RATE)) * math.sin(2 * math.pi * 2000 * i / RATE) for i in range(n)]


def clap():
    rng = random.Random(7)
    n = round(0.15 * RATE)
    return [0.7 * math.exp(-i / (0.03 * RATE)) * rng.uniform(-1, 1) for i in range(n)]


def peak_dbfs(samples):
    return round(20 * math.log10(max(abs(s) for s in samples)), 2)


def cue(frame, sfx, anchor, gain_db=0.0, pitch_jitter=0.0):
    return {"frame": frame, "sfx": sfx, "anchor": anchor, "gain_db": gain_db, "pitch_jitter": pitch_jitter}


def main():
    HERE.mkdir(exist_ok=True)
    sounds = {}
    for name, fn, category, desc in [("tick", tick, "click", "test click"), ("clap", clap, "clap", "test clap")]:
        s = fn()
        write_wav(HERE / f"{name}.wav", s)
        sounds[name] = {"file": f"{name}.wav", "seconds": round(len(s) / RATE, 4),
                        "peak_dbfs": peak_dbfs(s), "category": category, "description": desc}
    (HERE / "sfx_manifest.json").write_text(json.dumps({"sample_rate": RATE, "sounds": sounds}, indent=2))
    cues = {
        "fps": 24, "version": 1,
        "terence": {
            # 0 s, 0.5 s and 1.5 s into the 2 s loop
            "loop_a": [cue(0, "tick", "RightHand"), cue(12, "clap", "RightHand", -3.0, 0.05),
                       cue(36, "tick", "Hips")],
            # 0.25 s on the prop, 0.5 s at a point 1 m behind him (Blender axes: +Y is behind), 1.0 s (the last frame)
            "shot_b": [cue(6, "clap", "PropR"), cue(12, "tick", "world:0,1,0.5"), cue(24, "tick", "Hips")],
        },
        "band": {
            "default": {"loop_a": [cue(0, "tick", "Hips")], "shot_b": [cue(12, "clap", "RightHand")]},
            "trombone": {"loop_a": [cue(6, "clap", "RightHand")]},
            "band_trombone_2": {"loop_a": [cue(18, "tick", "Hips")]},
        },
    }
    (HERE / "sfx_cues.json").write_text(json.dumps(cues, indent=2))
    print("wrote fixtures:", sorted(p.name for p in HERE.iterdir()))


if __name__ == "__main__":
    main()
