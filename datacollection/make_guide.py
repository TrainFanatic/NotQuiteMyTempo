"""Render a play-along guide track for data collection: silence, Fletcher's count-off, then a click.

  python datacollection/make_guide.py SHEET.drums.txt --bpm 94 -o guide.wav
  python datacollection/make_guide.py SHEET.drums.txt --tempo SHEET.tempo.json --bpm 140 \
      --bars 8 --loops 2 --click-every half --style build --drums -o guide.wav

Layout of guide.wav (mono, float32, 48 kHz by default):
  [LEAD_S silence][count-off "5 6 7 8", ends exactly at t0][click on every beat ... ][tail]
t0 is the downbeat of bar 1 of the sheet. Its exact sample index is `t0_sample` in guide.json, and
every click starts on exactly sample t0_sample + round(beat_time_rel_t0 * sample_rate) (the
click waveform peaks on its first sample, so its onset is that sample).

Tempo. With a tempo map (`--tempo`, or `<stem>.tempo.json` next to the sheet if it exists) the
click follows the sheet's bar grid (meters, tempo changes), scaled uniformly by bpm / bpm of bar 1.
Without one the sheet is assumed to be written at --sheet-bpm (default: --bpm) in a constant
--beats-per-bar meter, and its length is the bar holding its last note.

guide.json (written next to the WAV, same stem) holds every time in FILE seconds (from the first
sample of guide.wav) unless the key says `_rel_t0`.

--drums mixes the sheet itself (quietly, --drums-gain) using Chris's sample renderer
(rushingOrDragging/test_file_with_audio.py). It needs his downloaded kit in
rushingOrDragging/samples/; if that is missing the drums are skipped with a warning (no download).
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import re
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from fletcher import countoff  # noqa: E402

SR = 48000
LEAD_S = 2.0
TAIL_S = 1.0
DRUM_TAIL_S = 2.5
CLICK_S = 0.015

# kind -> (fundamental Hz, amplitude). Accent = beat 1 of a bar.
CLICK_KINDS = {"accent": (4000.0, 0.9), "beat": (2000.0, 0.6), "sub": (2000.0, 0.3)}


# --------------------------------------------------------------------------- click

def click(kind: str = "beat", sr: int = SR) -> np.ndarray:
    """Short woodblock-ish tick: two inharmonic cosine partials, 3 ms exponential decay.
    It starts at full amplitude on its first sample, so its onset is unambiguous."""
    f, amp = CLICK_KINDS[kind]
    t = np.arange(int(round(CLICK_S * sr))) / sr
    x = (np.cos(2 * np.pi * f * t) + 0.4 * np.cos(2 * np.pi * 2.76 * f * t)) / 1.4
    x *= np.exp(-t / 0.003)
    fade = int(0.003 * sr)
    x[-fade:] *= np.linspace(1.0, 0.0, fade)
    return amp * x


# --------------------------------------------------------------------------- sheet / grid

def read_notes(path: Path) -> list[tuple[float, int]]:
    out = []
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            f = line.split()
            out.append((float(f[0]), int(f[1])))
    return out


def _sheet_time(tm: dict, pos: float) -> float:
    """Musical position (whole notes) -> sheet seconds, via the map's step tempo changes."""
    c = tm["tempo_changes"][0]
    for ch in tm["tempo_changes"]:
        if ch["pos_whole"] <= pos + 1e-12:
            c = ch
    return c["time_s"] + (pos - c["pos_whole"]) * 240.0 / c["bpm"]


def grid_from_tempo_map(tm: dict, bars: int | None, subdivide: bool):
    """[(sheet_time, kind, bar, beat)] for one pass, plus the pass length in sheet seconds."""
    rows = tm["bars"]
    if bars is not None:
        if bars > len(rows):
            raise SystemExit(f"--bars {bars}: the tempo map has only {len(rows)} bars")
        rows = rows[:bars]
    out = []
    for b in rows:
        step = 1.0 / b["denominator"]
        for k in range(b["numerator"]):
            p = b["pos_whole"] + k * step
            out.append((_sheet_time(tm, p), "accent" if k == 0 else "beat", b["bar"], float(k)))
            if subdivide:
                out.append((_sheet_time(tm, p + step / 2), "sub", b["bar"], k + 0.5))
    if bars is None or bars == len(tm["bars"]):
        end = tm["end_s"]
    else:
        end = tm["bars"][bars]["start_s"]
    return out, end


def grid_constant(sheet_bpm: float, beats_per_bar: int, n_bars: int, subdivide: bool):
    beat = 60.0 / sheet_bpm
    out = []
    for bar in range(n_bars):
        for k in range(beats_per_bar):
            t = (bar * beats_per_bar + k) * beat
            out.append((t, "accent" if k == 0 else "beat", bar + 1, float(k)))
            if subdivide:
                out.append((t + beat / 2, "sub", bar + 1, k + 0.5))
    return out, n_bars * beats_per_bar * beat


def find_tempo_map(sheet: Path, tempo: Path | None = None) -> tuple[dict | None, str | None]:
    """(tempo map, where it came from). Order: --tempo; `<stem>.tempo.json` next to the sheet;
    derived with Chris's songsterr.tempo_map from the track JSON named in the sheet's header
    (`# source: <path>` or `# songId=.. revisionId=.. track=..` -> rushingOrDragging/data/raw)."""
    if tempo:
        tm = json.loads(Path(tempo).read_text())
        if tm.get("format") != "tempo-map/1":
            raise SystemExit(f"{tempo}: not a tempo-map/1 file")
        return tm, str(Path(tempo).resolve())
    name = sheet.name[:-len(".drums.txt")] if sheet.name.endswith(".drums.txt") else sheet.stem
    side = sheet.with_name(name + ".tempo.json")
    if side.exists():
        return find_tempo_map(sheet, side)
    rod = REPO / "rushingOrDragging"
    cands = []
    for line in sheet.read_text().splitlines():
        if not line.startswith("#"):
            break
        m = re.match(r"#\s*source:\s*(.+?)\s{2}track=", line)
        if m:
            src = Path(m.group(1))
            cands += [src] if src.is_absolute() else [sheet.parent / src, Path.cwd() / src, rod / src]
        m = re.search(r"songId=(\d+) revisionId=(\d+) track=(\d+)", line)
        if m:
            cands.append(rod / "data" / "raw" / m.group(1) / m.group(2) / f"{m.group(3)}.json")
    for c in cands:
        if c.is_file():
            if str(rod) not in sys.path:
                sys.path.insert(0, str(rod))
            from songsterr.tempo_map import extract_tempo_map
            return extract_tempo_map(json.loads(c.read_text())), str(c.resolve())
    return None, None


# --------------------------------------------------------------------------- drums (optional)

def render_drums(notes: list[tuple[float, int]], sr: int, kit: Path | None = None):
    """Sheet notes (already in output seconds rel. t0) -> mono array at sr, hit i at round(t_i*sr).
    Returns None (with a warning) if Chris's renderer or kit is unavailable."""
    path = REPO / "rushingOrDragging" / "test_file_with_audio.py"
    try:
        spec = importlib.util.spec_from_file_location("rod_render_audio", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    except Exception as e:  # noqa: BLE001
        print(f"warning: drum renderer unavailable ({e}); skipping --drums", file=sys.stderr)
        return None
    kit_dir = Path(kit) if kit else mod.DEFAULT_KIT_DIR
    if not (kit_dir.is_dir() and any(kit_dir.glob("*.wav"))):
        print(f"warning: no drum kit in {kit_dir} (run Chris's test_file_with_audio.py once to "
              "download it); skipping --drums", file=sys.stderr)
        return None
    x = mod.render(notes, kit_dir=kit_dir)
    if mod.SAMPLE_RATE != sr:
        from scipy.signal import resample_poly
        g = math.gcd(sr, mod.SAMPLE_RATE)
        x = resample_poly(x, sr // g, mod.SAMPLE_RATE // g)  # linear-phase: timing preserved
    return x


# --------------------------------------------------------------------------- build

def build_guide(sheet: Path, bpm: float | None = None, tempo: Path | None = None,
                bars: int | None = None, loops: int = 1, click_every: str = "beat",
                style: str = "sharp", sheet_bpm: float | None = None, beats_per_bar: int = 4,
                drums: bool = False, drums_gain: float = 0.25, kit: Path | None = None,
                countoff_clicks: bool = False, sr: int = SR, lead_s: float = LEAD_S):
    """Returns (audio float32 array, guide dict)."""
    sheet = Path(sheet)
    notes = read_notes(sheet)
    subdivide = click_every == "half"
    tm, tempo_src = find_tempo_map(sheet, tempo)

    if tm is not None:
        b1 = tm["bars"][0]
        sheet_bpm = float(b1["bpm"])
        beats_per_bar = int(b1["numerator"])
        bpm = float(bpm) if bpm else sheet_bpm
        grid, pass_len = grid_from_tempo_map(tm, bars, subdivide)
        scale = sheet_bpm / bpm
        count_beat_s = b1["beat_s"] * scale
    else:
        if not bpm:
            raise SystemExit("--bpm is required when there is no tempo map")
        bpm = float(bpm)
        if not sheet_bpm:
            print(f"warning: no tempo map for {sheet}; assuming it is written at {bpm:g} bpm "
                  "(pass --tempo or --sheet-bpm if not)", file=sys.stderr)
        sheet_bpm = float(sheet_bpm or bpm)
        if bars is None:
            last = max((t for t, _ in notes), default=0.0)
            bars = max(1, math.floor(last / (beats_per_bar * 60.0 / sheet_bpm) + 1e-9) + 1)
        grid, pass_len = grid_constant(sheet_bpm, beats_per_bar, bars, subdivide)
        scale = sheet_bpm / bpm
        count_beat_s = 60.0 / bpm

    n_bars = len({g[2] for g in grid})
    pass_len *= scale

    # count-off: one bar, its last sample at t0
    sched = countoff.schedule(60.0 / count_beat_s, beats_per_bar, 1, style)
    co, _ = countoff.render_array(sched, sr)
    lead = int(round(lead_s * sr))
    t0 = lead + len(co)

    clicks = []  # (file sample, kind, loop, bar, beat)
    for loop in range(loops):
        for t, kind, bar, beat in grid:
            clicks.append((t0 + int(round((loop * pass_len + t * scale) * sr)), kind, loop + 1, bar, beat))
    if countoff_clicks:
        for i, (bt, _) in enumerate(sched):
            clicks.append((t0 + int(round(bt * sr)), "accent" if i == 0 else "beat", 0, 0, float(i)))
    clicks.sort()

    drum_notes = [(loop * pass_len + t * scale, m) for loop in range(loops)
                  for t, m in notes if t * scale < pass_len - 1e-6]
    drum_audio = render_drums(drum_notes, sr, kit) if drums else None

    end = t0 + int(round((loops * pass_len + (DRUM_TAIL_S if drum_audio is not None else TAIL_S)) * sr))
    audio = np.zeros(end)
    audio[lead:t0] += co * 0.9
    for s, kind, *_ in clicks:
        c = click(kind, sr)
        n = min(len(c), end - s)
        audio[s:s + n] += c[:n]
    if drum_audio is not None:
        n = min(len(drum_audio), end - t0)
        audio[t0:t0 + n] += drums_gain * drum_audio[:n]
    peak = np.abs(audio).max()
    if peak > 0.99:
        audio *= 0.99 / peak

    guide = {
        "format": "guide/1",
        "t0_sample": t0,
        "t0_s": t0 / sr,
        "sample_rate": sr,
        "bpm": bpm,
        "sheet_bpm": sheet_bpm,
        "time_scale": scale,
        "beats_per_bar": beats_per_bar,
        "sheet": str(sheet.resolve()),
        "tempo_map": tempo_src,
        "sheet_bars": n_bars,
        "sheet_loops": loops,
        "loop_len_s": pass_len,
        "click_every": click_every,
        "click_times_s": [s / sr for s, *_ in clicks],
        "click_samples": [s for s, *_ in clicks],
        "click_kinds": [k for _, k, *_ in clicks],
        "click_bar_beat": [[lp, bar, beat] for _, _, lp, bar, beat in clicks],
        "countoff_style": style,
        "countoff_clips": [cid for _, cid in sched],
        "countoff_times_s": [t0 / sr + bt for bt, _ in sched],
        "countoff_clicks": countoff_clicks,
        "lead_s": lead / sr,
        "drums_rendered": drum_audio is not None,
        "drums_gain": drums_gain if drum_audio is not None else 0.0,
        "duration_s": end / sr,
        "frames": end,
    }
    return audio.astype(np.float32), guide


def write_guide(out: Path, audio: np.ndarray, guide: dict) -> Path:
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(out), audio, guide["sample_rate"], subtype="FLOAT")
    js = out.with_suffix(".json")
    guide = dict(guide, wav=str(out.resolve()))
    js.write_text(json.dumps(guide, indent=1) + "\n")
    return js


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("sheet", type=Path, help="SHEET.drums.txt")
    p.add_argument("--bpm", type=float, help="play-along tempo (default: the tempo map's bar-1 bpm)")
    p.add_argument("--tempo", type=Path, help="SHEET.tempo.json (default: sidecar next to the sheet, if any)")
    p.add_argument("--bars", type=int, help="use only the first N bars of the sheet")
    p.add_argument("--loops", type=int, default=1, help="play the sheet K times back to back")
    p.add_argument("--click-every", choices=("beat", "half"), default="beat")
    p.add_argument("--style", choices=countoff.STYLES, default="sharp", help="count-off delivery")
    p.add_argument("--sheet-bpm", type=float, help="tempo the sheet is written at (no tempo map only)")
    p.add_argument("--beats-per-bar", type=int, default=4, help="meter (no tempo map only)")
    p.add_argument("--countoff-clicks", action="store_true", help="also click under the count-off words")
    p.add_argument("--drums", action="store_true", help="mix the sheet's drums in quietly (needs Chris's kit)")
    p.add_argument("--drums-gain", type=float, default=0.25)
    p.add_argument("--kit", type=Path, help="drum sample folder (default: Chris's downloaded kit)")
    p.add_argument("--sr", type=int, default=SR)
    p.add_argument("-o", "--output", type=Path, default=Path("guide.wav"))
    a = p.parse_args(argv)
    audio, guide = build_guide(a.sheet, a.bpm, a.tempo, a.bars, a.loops, a.click_every, a.style,
                               a.sheet_bpm, a.beats_per_bar, a.drums, a.drums_gain, a.kit,
                               a.countoff_clicks, a.sr)
    js = write_guide(a.output, audio, guide)
    print(f"wrote {a.output} ({guide['duration_s']:.2f} s, t0 = {guide['t0_s']:.4f} s = sample "
          f"{guide['t0_sample']}, {guide['sheet_bars']} bars x {guide['sheet_loops']} at "
          f"{guide['bpm']:g} bpm, {len(guide['click_times_s'])} clicks"
          f"{', drums' if guide['drums_rendered'] else ''}) and {js}")


if __name__ == "__main__":
    main()
