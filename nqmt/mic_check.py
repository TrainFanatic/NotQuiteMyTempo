"""Headset mic checks (nqmt/HEADSET_TEST.md): turn recorded mic.pcm into a WAV, and compare
Kevin's transcriber on it with and without the Quest mute filter.

    python -m nqmt.mic_check wav recordings/<time>.jsonl take.wav     # needs --record-mic-pcm
    python -m nqmt.mic_check compare take.wav [--bpm 100 --sub 4]     # filter on vs off
    python -m nqmt.mic_check count <file.drums.txt> [--from T --to T] # onsets (+ IOIs) in a window

`wav` writes the headset's samples at 48 kHz mono (gaps as silence, per the frames' first_sample),
the prepared format transcribe_wav.py takes. `compare` runs it twice (`--onsets-only`, default
mute filter vs `--mute-window-ms 0`) and prints both counts, what the filter dropped, and the
inter-onset intervals; with --bpm/--sub, how many onsets a steady pattern of that grid implies.
"""

from __future__ import annotations

import argparse
import base64
import json
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

from .launcher import LT, PY


def to_wav(jsonl: Path, out: Path) -> dict:
    import soundfile as sf
    frames, rate = [], None
    for line in jsonl.open():
        if '"mic.pcm"' not in line[:96]:
            continue
        try:
            d = json.loads(line).get("data") or {}
            pcm = np.frombuffer(base64.b64decode(d["pcm_b64"]), "<i2").astype(np.float32) / 32768.0
        except (ValueError, KeyError):
            continue
        rate = rate or int(d.get("rate", 48000))
        frames.append((int(d["first_sample"]), pcm))
    if not frames:
        raise SystemExit(f"no mic.pcm frames in {jsonl} (record with python -m nqmt --record-mic-pcm)")
    frames.sort(key=lambda f: f[0])
    # a restart of the stream (first_sample back near 0) starts a new segment: keep the longest
    segs, cur = [], [frames[0]]
    for f in frames[1:]:
        if f[0] < cur[-1][0]:
            segs.append(cur)
            cur = []
        cur.append(f)
    segs.append(cur)
    seg = max(segs, key=lambda s: s[-1][0] - s[0][0])
    base = seg[0][0]
    x = np.zeros(seg[-1][0] - base + len(seg[-1][1]), dtype=np.float32)
    for first, pcm in seg:
        x[first - base:first - base + len(pcm)] = pcm
    if rate != 48000:
        n = int(len(x) * 48000 / rate)
        x = np.interp(np.arange(n) * rate / 48000, np.arange(len(x)), x).astype(np.float32)
    sf.write(str(out), x, 48000, subtype="PCM_16")
    return {"seconds": round(len(x) / 48000, 2), "frames": len(seg), "source_rate": rate, "segments": len(segs)}


def read_onsets(path: Path, t_from: float = -1e18, t_to: float = 1e18) -> list[float]:
    out = []
    for line in Path(path).read_text().splitlines():
        if line.strip() and not line.startswith("#"):
            t = float(line.split()[0])
            if t_from <= t <= t_to:
                out.append(t)
    return out


def describe(ts: list[float]) -> str:
    if len(ts) < 2:
        return f"{len(ts)} onsets"
    ioi = np.diff(ts) * 1000
    return (f"{len(ts)} onsets, IOI median {statistics.median(ioi):.0f} ms, p10 {np.percentile(ioi, 10):.0f}, "
            f"p90 {np.percentile(ioi, 90):.0f}, span {ts[-1] - ts[0]:.1f} s")


def transcribe(wav: Path, out: Path, extra: list[str]) -> list[float]:
    subprocess.run([PY, "tools/transcribe_wav.py", str(wav.resolve()), "--onsets-only", "--output",
                    str(out.resolve()), *extra], cwd=LT, check=True, capture_output=True, text=True)
    return read_onsets(out)


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="python -m nqmt.mic_check", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("wav")
    w.add_argument("jsonl", type=Path)
    w.add_argument("out", type=Path)
    c = sub.add_parser("compare")
    c.add_argument("wav", type=Path)
    c.add_argument("--bpm", type=float)
    c.add_argument("--sub", type=int, default=4, help="notes per beat (4 = 16ths)")
    n = sub.add_parser("count")
    n.add_argument("drums", type=Path, nargs="+")
    n.add_argument("--from", dest="t_from", type=float, default=-1e18)
    n.add_argument("--to", dest="t_to", type=float, default=1e18)
    a = p.parse_args(argv)
    if a.cmd == "wav":
        print(json.dumps(to_wav(a.jsonl, a.out)))
    elif a.cmd == "count":
        for f in a.drums:
            print(f"{f}: {describe(read_onsets(f, a.t_from, a.t_to))}")
    else:
        with tempfile.TemporaryDirectory() as d:
            on = transcribe(a.wav, Path(d) / "on.drums.txt", [])
            off = transcribe(a.wav, Path(d) / "off.drums.txt", ["--mute-window-ms", "0"])
        print(f"filter ON : {describe(on)}")
        print(f"filter OFF: {describe(off)}")
        dropped = [t for t in off if not any(abs(t - u) < 0.005 for u in on)]
        print(f"dropped by the filter: {len(dropped)}" + (f" (first at {', '.join(f'{t:.3f}' for t in dropped[:8])} s)"
                                                         if dropped else ""))
        if a.bpm and len(off) >= 2:
            grid = 60.0 / a.bpm / a.sub
            span = off[-1] - off[0]
            print(f"a steady 1/{a.sub * 4} grid at {a.bpm:g} BPM over {span:.1f} s: ~{round(span / grid) + 1} "
                  f"notes ({grid * 1000:.0f} ms apart)")


if __name__ == "__main__":
    sys.exit(main())
