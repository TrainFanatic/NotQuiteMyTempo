"""Shared helpers for datacollection/: paths, audio IO, take/guide loading, kit mapping, matching.

(Named dc_common, not common, so it never shadows live_transcription/tools/common.py.)

Time bases used throughout:
  sheet time   the .drums.txt's own seconds (bar 1 downbeat = 0, at the sheet's tempo)
  guide time   seconds from the first sample of guide.wav (make_guide.py); guide.json times
  ref time     seconds from the first sample of ref.wav (playalong.py); one clock with the guide
  quest time   seconds from the first sample of the session's prepared audio.wav (Kevin's import)
"""

from __future__ import annotations

import csv
import json
import math
import sys
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
ROD = REPO / "rushingOrDragging"
LT = REPO / "live_transcription"
LT_TOOLS = LT / "tools"
RECORDINGS = LT / "data" / "recordings"

for p in (str(ROD),):
    if p not in sys.path:
        sys.path.insert(0, p)
import compare_performance as cp  # noqa: E402

FAMILY_MIDI = {"kick": 36, "snare": 38, "hihat": 42, "tom": 47, "crash": 49, "ride": 51}


def kevin():
    """Kevin's python bridge (live_transcription/tools/common.py), imported lazily: it needs the
    native library from live_transcription/build/."""
    if str(LT_TOOLS) not in sys.path:
        sys.path.append(str(LT_TOOLS))  # append: his evaluate.py must not shadow ours
    import common as ltc  # noqa: PLC0415
    return ltc


# --------------------------------------------------------------------------- audio

def read_mono(path, sr=None):
    """(float64 mono, rate). Mean of channels; resampled (polyphase, linear phase) if sr given."""
    x, rate = sf.read(str(path), dtype="float64", always_2d=True)
    x = x.mean(axis=1)
    if sr and rate != sr:
        g = math.gcd(int(rate), int(sr))
        x = resample_poly(x, sr // g, rate // g)
        rate = sr
    return x, int(rate)


def write_notes(path, hits, source):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(f"# source: {source}\n# time_s  midi  name\n" +
                          "".join(f"{t:.6f}  {m}  {cp.drum_name(m)}\n" for t, m in sorted(hits)))


def read_notes(path):
    return [(h.time, h.midi) for h in cp.read_notes(path)]


# --------------------------------------------------------------------------- takes / guide

def load_take(take_dir):
    """take.json from playalong.py (format take/1). Also accepts a bare guide.json-style dict with
    the ref-time keys at top level."""
    take_dir = Path(take_dir)
    take = json.loads((take_dir / "take.json").read_text())
    if "guide" not in take:                       # flat variant: guide keys at top level
        take = dict(take, guide={k: v for k, v in take.items()})
    take["_dir"] = str(take_dir)
    return take


def resolve(p, *bases):
    p = Path(p)
    if p.is_absolute():
        return p
    for b in bases:
        if (Path(b) / p).exists():
            return Path(b) / p
    return Path(bases[0]) / p if bases else p


def sheet_in_guide_time(guide, base_dir=".", sheet_path=None):
    """The expanded sheet [(guide time, midi)] the drummer was asked to play: each loop of the
    first `sheet_bars` bars, scaled to the guide tempo, starting at guide t0. Same formula as
    make_guide.build_guide's drum_notes."""
    path = resolve(sheet_path or guide["sheet"], base_dir, REPO)
    notes = read_notes(path)
    scale = float(guide.get("time_scale", 1.0))
    loops = int(guide.get("sheet_loops", 1))
    t0 = guide["t0_s"] if "t0_s" in guide else guide["t0_sample"] / guide["sample_rate"]
    pass_len = guide.get("loop_len_s")
    if pass_len is None:          # constant-meter fallback: whole bars up to the last note
        bar = guide.get("beats_per_bar", 4) * 60.0 / guide["bpm"]
        last = max(t for t, _ in notes) * scale
        pass_len = (math.floor(last / bar + 1e-9) + 1) * bar
    return sorted((t0 + k * pass_len + t * scale, m) for k in range(loops)
                  for t, m in notes if t * scale < pass_len - 1e-6)


def guide_offset(take):
    """Scheduled ref time of guide sample 0 (no acoustic round trip)."""
    if "guide_offset_s" in take:
        return float(take["guide_offset_s"])
    if "guide_offset_frames" in take:
        return take["guide_offset_frames"] / take["sample_rate"]
    return 0.0


# --------------------------------------------------------------------------- kit / pieces

DEFAULT_MIDI_PIECE = {  # GM number -> preferred physical piece id, used when the kit has it
    49: "crash_1", 55: "crash_1", 52: "crash_2", 57: "crash_2",
    48: "rack_tom_1", 50: "rack_tom_1", 45: "rack_tom_2", 47: "rack_tom_2",
    41: "floor_tom", 43: "floor_tom",
}


def find_kit(path=None):
    if path:
        return Path(path)
    local = LT / "config" / "kit.local.json"
    if local.exists():
        return local
    print("warning: live_transcription/config/kit.local.json missing; using kit.example.json",
          file=sys.stderr)
    return LT / "config" / "kit.example.json"


def load_kit(path=None):
    kit = json.loads(find_kit(path).read_text())
    pieces = kit["pieces"]
    by_family = {}
    for piece, fam in pieces.items():
        by_family.setdefault(fam, []).append(piece)
    kit["_by_family"] = by_family
    return kit


def piece_for(midi, kit):
    """(piece_id or None, ambiguous?) for a GM note. Order: kit['midi_pieces'] (optional
    {"57": "crash_2"}), the default GM table if that piece is in the kit, the family's only
    piece, else the family's first piece flagged ambiguous."""
    explicit = kit.get("midi_pieces", {})
    if str(midi) in explicit:
        return explicit[str(midi)], False
    fam = cp.family(midi)
    cands = kit["_by_family"].get(fam, [])
    if not cands:
        return None, True
    pref = DEFAULT_MIDI_PIECE.get(midi)
    if pref in cands:
        return pref, False
    if fam in cands:
        return fam, len(cands) > 1 and fam not in ("kick", "snare", "hihat", "ride")
    return cands[0], len(cands) > 1


def family_midi_of_piece(piece, kit):
    return FAMILY_MIDI[kit["pieces"][piece]]


def read_labels(path, kit):
    """[(time, family-representative midi, piece)] from a labels.csv."""
    out = []
    with open(path, newline="") as f:
        r = csv.DictReader(f)
        if r.fieldnames[:2] != ["time_s", "piece_id"]:
            raise SystemExit(f"{path}: expected header time_s,piece_id")
        for row in r:
            out.append((float(row["time_s"]), family_midi_of_piece(row["piece_id"], kit), row["piece_id"]))
    return sorted(out)


# --------------------------------------------------------------------------- matching

def match_one_to_one(det, truth, tol=0.030, key=lambda m: cp.family(m)):
    """Greedy-by-distance one-to-one pairing of (t, midi) lists; same key (family) and |dt|<=tol.
    Returns [(det_t - truth_t, i_det, k_truth)]."""
    by = {}
    for k, (t, m, *_) in enumerate(truth):
        by.setdefault(key(m), []).append((t, k))
    for v in by.values():
        v.sort()
    cand = []
    for i, (t, m, *_) in enumerate(det):
        lst = by.get(key(m), [])
        ts = [x for x, _ in lst]
        lo = int(np.searchsorted(ts, t - tol))
        for tt, k in lst[lo:]:
            if tt > t + tol:
                break
            cand.append((abs(tt - t), i, k))
    used_d, used_t, pairs = set(), set(), []
    for d, i, k in sorted(cand):
        if i in used_d or k in used_t:
            continue
        used_d.add(i)
        used_t.add(k)
        pairs.append((det[i][0] - truth[k][0], i, k))
    return pairs


# --------------------------------------------------------------------------- guide echo removal

def scheduled_output(take, sr):
    """What the laptop sent to its speaker (playalong.schedule_take of the guide), at rate sr, on
    ref.wav's clock. None if the guide wav is not available."""
    gw = take.get("guide_wav") or take["guide"].get("wav")
    if not gw or not Path(gw).exists():
        return None
    if str(HERE) not in sys.path:
        sys.path.insert(0, str(HERE))
    import playalong as pa  # noqa: PLC0415
    g, gsr = read_mono(gw, sr)
    play, slates, g0 = pa.schedule_take(g, sr)
    want = take.get("guide_offset_frames")
    if want is not None and int(want) != g0:
        raise SystemExit(f"guide schedule mismatch: take.json says the guide starts at frame {want}, "
                         f"playalong.schedule_take gives {g0}")
    return play


def cancel_guide(ref, sr, play, nfft=1 << 16, max_ir_s=0.5):
    """Remove the guide (clicks, count-off, slates) from a recording made on the same clock as its
    playback: estimate the speaker->mic impulse response h (Welch cross-spectrum, drums act as
    uncorrelated noise and average out) and subtract play * h. Returns (clean, h, rt_s) where rt_s
    is the round trip: first arrival of h (envelope reaches 50 % of its max)."""
    from scipy.signal import fftconvolve, hilbert
    n = len(ref)
    p = np.zeros(n)
    p[:min(n, len(play))] = play[:n]
    win = np.hanning(nfft)
    num = np.zeros(nfft // 2 + 1, complex)
    den = np.zeros(nfft // 2 + 1)
    for s in range(0, n - nfft, nfft // 4):
        P = np.fft.rfft(win * p[s:s + nfft])
        R = np.fft.rfft(win * ref[s:s + nfft])
        num += R * np.conj(P)
        den += np.abs(P) ** 2
    H = num / (den + 1e-4 * den.max() + 1e-20)
    h = np.fft.irfft(H, nfft)
    L = int(max_ir_s * sr)
    h = h[:L] * np.r_[np.ones(L - L // 8), np.hanning(2 * (L // 8))[L // 8:]]
    env = np.abs(hilbert(h))
    rt = int(np.flatnonzero(env >= 0.5 * env.max())[0])
    # refine to sub-sample: parabola on |h| around its local max following the crossing
    k = rt + int(np.argmax(np.abs(h[rt:rt + int(0.002 * sr)])))
    if 0 < k < L - 1:
        a, b, c = np.abs(h[k - 1:k + 2])
        k = k + 0.5 * (a - c) / (a - 2 * b + c) if (a - 2 * b + c) else k
    est = fftconvolve(p, h)[:n]
    return ref - est, h, k / sr


def remove_clicks(x, sr, times, kinds=None, pre_s=0.002, length_s=0.060):
    """Subtract the guide click from a recording sample-locked to it. For each click kind, the
    template is the per-sample MEDIAN of the recording over all of that kind's instances: the
    click (same waveform, same room every time) survives, drum sound (random phase relative to
    the click) does not. times: click onsets in this file's time (scheduled + round trip)."""
    y = np.array(x, dtype=np.float64, copy=True)
    kinds = list(kinds) if kinds is not None else ["click"] * len(times)
    pre, L = int(pre_s * sr), int(length_s * sr)
    taper = np.ones(L)
    taper[-L // 4:] = np.hanning(2 * (L // 4))[L // 4:]
    for kind in set(kinds):
        idx = [int(round(t * sr)) - pre for t, k in zip(times, kinds) if k == kind]
        idx = [i for i in idx if 0 <= i and i + L <= len(y)]
        if len(idx) < 5:
            continue
        tmpl = np.median(np.stack([x[i:i + L] for i in idx]), axis=0) * taper
        for i in idx:
            y[i:i + L] -= tmpl
    return y


def load_ref(take_dir, sheet_path=None):
    """Everything about a laptop take, in ref.wav time:
    x (mono), sr, rt (measured speaker->mic round trip, s), rt_source, clean (guide clicks
    removed), sheet [(ref time, midi)] (the expanded sheet as the mic heard the guide),
    clicks [ref times], take (take.json)."""
    take = load_take(take_dir)
    x, sr = read_mono(Path(take_dir) / "ref.wav")
    play = scheduled_output(take, sr)
    rt, src = None, None
    if play is not None:
        _, _, rt = cancel_guide(x, sr, play)
        src = "measured (speaker->mic impulse response of this take)"
    if rt is None and take.get("calibration", {}).get("median_s") is not None:
        rt, src = take["calibration"]["median_s"], "playalong --calibrate"
    if rt is None and "latency_s" in take:
        rt, src = take["latency_s"]["input"] + take["latency_s"]["output"], "PortAudio reported latency"
    if rt is None:
        rt, src = 0.0, "unknown (0)"
    off = guide_offset(take) + rt
    g = take["guide"]
    clicks = [off + t for t in g.get("click_times_s", [])]
    clean = remove_clicks(x, sr, clicks, g.get("click_kinds")) if clicks else x
    sheet = [(off + t, m) for t, m in sheet_in_guide_time(g, take_dir, sheet_path)]
    return dict(x=x, sr=sr, rt=rt, rt_source=src, clean=clean, sheet=sheet, clicks=clicks,
                take=take, offset=off)
