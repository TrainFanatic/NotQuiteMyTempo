"""Simulate one recording session end to end, in the exact file formats of the real thing.

  python datacollection/sim_session.py OUT [--loops 6 --seed 5 --drift-sd 0.025]

What it builds (all times are known exactly, so every later stage can be scored):
  OUT/guide/guide.{wav,json}   make_guide.py's real guide for the fixture sheet (count-off + click)
  OUT/takes/sim/{ref.wav,take.json}
        playalong.py's real schedule (slates, tap window, guide), "played" through a speaker into a
        laptop mic sample-locked to it (round trip RT_REF), plus a drummer: Chris's make_drifting
        performance of the tiled sheet + 3 rim taps after the slates, rendered with his sample kit
        (velocity layers vary per hit). Clean: light room, -70 dB noise.
  OUT/quest_raw/TAKE.{wav,capture.json}
        the same acoustic events heard by the headset: its recording starts QUEST_START s before
        ref.wav (so quest = QUEST_START + RATIO * ref), 44.1 kHz stereo float32, room reverb,
        pink noise, mains hum, speech bursts, band-limit 120-7000 Hz. The guide speaker is a
        different distance from the head than from the laptop (SPEAKER_EXTRA_S), and each piece
        has its own small head-path delay (PIECE_DELAY_S), as in a real room.
  OUT/recordings/quest_sim/   Kevin's import_quest_take.py run on that pair (audio.wav 48k mono,
        suggestions.csv)
  OUT/truth/truth_labels.csv  every hit in audio.wav time with its piece id
  OUT/truth/truth.json        ratio, offset, per-hit drift, ...
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import shutil
import sys
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy import signal

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import dc_common as dc  # noqa: E402

sys.path[:0] = [str(dc.ROD), str(dc.ROD / "tests")]
import test_file_with_audio as ta  # noqa: E402

FIXTURES = HERE / "tests" / "fixtures"
KIT = FIXTURES / "kit" / ta.KIT_NAME
SHEET = FIXTURES / "back_in_black_4bars.drums.txt"

SR = 48000            # laptop
QSR = 44100           # Quest (Godot mix rate)
RT_REF = 0.021337     # speaker -> laptop mic round trip (output + input latency + air)
QUEST_START = 3.217   # the headset started recording this long before ref.wav frame 0
RATIO = 1.00004       # quest clock / laptop clock
SPEAKER_EXTRA_S = 0.0047                 # guide speaker is farther from the head than the kit
PIECE_DELAY_S = {36: 0.0006, 37: -0.0004, 38: -0.0004, 42: 0.0, 57: 0.0003}
LAYERS = ([2, 3, 4, 5], [0.15, 0.3, 0.35, 0.2])
KIT_JSON = dc.LT / "config" / "kit.example.json"


def frac_kernel(frac, half=32):
    """Windowed sinc; convolving with it delays a signal by half + frac samples (0 <= frac < 1)."""
    n = np.arange(-half, half + 1)
    h = np.sinc(n - frac) * np.hanning(len(n) + 2)[1:-1]
    return h / h.sum()


def delay(x, d, length, half=32):
    """x delayed by d (fractional) samples: out[n] = x(n - d), cut/padded to length."""
    i = math.floor(d)
    y = signal.fftconvolve(x, frac_kernel(d - i, half))
    out = np.zeros(length)
    s0 = half - i                 # out[n] = y[n + s0]
    dst0 = max(0, -s0)
    n = min(len(y) - (dst0 + s0), length - dst0)
    if n > 0:
        out[dst0:dst0 + n] = y[dst0 + s0:dst0 + s0 + n]
    return out


def room_ir(rng, sr, rt60, drr_db, er):
    n = int(rt60 * 1.5 * sr)
    t = np.arange(n) / sr
    ir = rng.normal(size=n) * np.exp(-6.9 * t / rt60)
    ir[: int(0.005 * sr)] = 0
    ir *= 10 ** (-drr_db / 20) / np.sqrt(np.sum(ir ** 2))
    ir[0] = 1.0
    for d_ms, g in er:
        ir[int(d_ms / 1000 * sr)] += g
    return ir


def pink(n, rng):
    b = [0.049922035, -0.095993537, 0.050612699, -0.004408786]
    a = [1, -2.494956002, 2.017265875, -0.522189400]
    y = signal.lfilter(b, a, rng.normal(size=n))
    return y / np.std(y)


def speech(n, sr, rng, starts, level):
    """Talking stand-in: harmonic buzz (f0 110-180 Hz, formant-filtered) gated at a syllable rate."""
    out = np.zeros(n)
    for s0, dur in starts:
        i0, m = int(s0 * sr), int(dur * sr)
        if i0 + m > n:
            continue
        tt = np.arange(m) / sr
        f0 = rng.uniform(110, 180) * (1 + 0.08 * np.sin(2 * np.pi * 0.7 * tt))
        ph = 2 * np.pi * np.cumsum(f0) / sr
        buzz = sum(np.sin(k * ph) / k for k in range(1, 25))
        buzz = signal.lfilter(*signal.butter(2, [300, 2500], "band", fs=sr), buzz)
        gate = np.clip(np.sin(2 * np.pi * 4.3 * tt + rng.uniform(0, 6)), 0, 1) ** 1.5
        out[i0:i0 + m] += buzz * gate
    nz = out[out != 0]
    return out * level / (np.std(nz) + 1e-12) if len(nz) else out


_cache = {}


def sample(midi, layer, sr):
    """Kit sample at sr and its lead-in (samples before the 2 %-of-peak attack start)."""
    k = (midi, layer, sr)
    if k not in _cache:
        x = ta.sound_for(midi, KIT, layer)
        lead = ta.lead_in(midi, KIT, layer)
        if sr != ta.SAMPLE_RATE:
            g = math.gcd(sr, ta.SAMPLE_RATE)
            x = signal.resample_poly(x, sr // g, ta.SAMPLE_RATE // g)
            lead = lead * sr / ta.SAMPLE_RATE
        _cache[k] = (x, lead)
    return _cache[k]


def render(events, sr, length, time_of):
    """events: [(ref_time, midi, layer)]; time_of(ref_time, midi) -> time in this file. Each sample's
    attack start (2 % of its peak) lands exactly (sub-sample) on that time."""
    out = np.zeros(length)
    half = 16
    for t, m, layer in events:
        x, lead = sample(m, layer, sr)
        pos = time_of(t, m) * sr - lead
        i = math.floor(pos)
        y = np.convolve(x, frac_kernel(pos - i, half))
        j = i - half                  # out[j + k] = y[k]
        if j < 0:
            y, j = y[-j:], 0
        n = min(len(y), length - j)
        if n > 0:
            out[j:j + n] += y[:n]
    return out


def build(out: Path, loops=6, seed=5, drift_sd=0.025, quest_start=QUEST_START, ratio=RATIO,
          session="quest_sim", quiet=False, guide_gain=0.25):
    import make_guide as mg
    import playalong as pa
    from test_drift import make_drifting

    out = Path(out)
    if out.exists():
        shutil.rmtree(out)
    rng = np.random.default_rng(seed + 77)
    log = (lambda *a: None) if quiet else (lambda *a: print(*a, file=sys.stderr))

    # ---- guide + playalong schedule (the real code) -------------------------------------------
    gdir = out / "guide"
    gdir.mkdir(parents=True)
    sheet = gdir / SHEET.name
    shutil.copy(SHEET, sheet)
    # the fixture's header names Chris's tempo-map fixture relative to rushingOrDragging/
    gaudio, guide = mg.build_guide(sheet, bpm=94, loops=loops, sr=SR)
    mg.write_guide(gdir / "guide.wav", gaudio, guide)
    play, slates, g0 = pa.schedule_take(gaudio.astype(np.float64), SR)
    off = g0 / SR

    # ---- the performance, in ref time ---------------------------------------------------------
    sheet_g = dc.sheet_in_guide_time(guide)
    rel = gdir / "sheet_rel_t0.drums.txt"
    dc.write_notes(rel, [(t - guide["t0_s"], m) for t, m in sheet_g], "expanded sheet, t0 = 0")
    truth = make_drifting(rel, gdir / "played_rel_t0.drums.txt", seed, drift_sd)
    t0_ref = off + guide["t0_s"] + RT_REF
    events = [(t0_ref + t, m, int(rng.choice(LAYERS[0], p=LAYERS[1]))) for (t, m) in sorted(truth)]
    taps = [slates[-1] / SR + RT_REF + 0.7 + 0.75 * k + rng.uniform(-0.05, 0.05) for k in range(3)]
    events += [(t, 37, 4) for t in taps]
    events.sort()

    # ---- ref.wav: speaker (guide) through a small room + drums + a little noise -------------------
    n_ref = len(play) + int(1.0 * SR)
    spk = delay(play, RT_REF * SR, n_ref)
    spk = signal.fftconvolve(spk, room_ir(rng, SR, 0.25, 8, [(2, 0.3)]))[:n_ref]
    drums_ref = render(events, SR, n_ref, lambda t, m: t)
    drums_ref = signal.fftconvolve(drums_ref, room_ir(rng, SR, 0.3, 10, [(3, 0.3), (6, 0.2)]))[:n_ref]
    talk = [(0.3, 0.6), (slates[-1] / SR + 0.1, 0.5)]
    ref = 0.5 * drums_ref + guide_gain * spk + speech(n_ref, SR, rng, talk, 0.01)
    ref += 10 ** (-70 / 20) * pink(n_ref, rng)
    ref *= 0.8 / np.abs(ref).max()

    tdir = out / "takes" / "sim"
    tdir.mkdir(parents=True)
    sf.write(tdir / "ref.wav", np.stack([ref, ref], 1).astype(np.float32), SR, subtype="FLOAT")
    take = {
        "format": "take/1", "session": "sim", "guide_wav": str((gdir / "guide.wav").resolve()),
        "guide": guide, "sample_rate": SR, "latency_s": {"input": 0.006, "output": 0.012},
        "frames": n_ref, "status_flags_count": 0, "interrupted": False,
        "guide_offset_s": off, "guide_offset_frames": g0,
        "slate_times_s": [f / SR for f in slates], "slate_frames": slates,
        "tap_window_s": [slates[-1] / SR + pa.SLATE_S, g0 / SR],
        "t0_ref_s": off + guide["t0_s"],
        "click_times_ref_s": [off + t for t in guide["click_times_s"]],
        "countoff_times_ref_s": [off + t for t in guide["countoff_times_s"]],
        "simulated": True,
    }
    (tdir / "take.json").write_text(json.dumps(take, indent=1) + "\n")

    # ---- the headset's view ----------------------------------------------------------------------
    def q_of(t, m=None):
        return quest_start + ratio * (t + PIECE_DELAY_S.get(m, 0.0))

    dur_q = q_of(n_ref / SR) + 1.5
    n_q = int(dur_q * QSR)
    drums_q = render(events, QSR, n_q, q_of)
    # speaker: play[] (ref clock, scheduled) arrives at the head RT_REF + SPEAKER_EXTRA_S later
    up = 4
    play4 = signal.resample_poly(play, up, 1)
    tq = np.arange(n_q) / QSR
    tr = (tq - quest_start) / ratio - RT_REF - SPEAKER_EXTRA_S
    spk_q = np.interp(tr * SR * up, np.arange(len(play4)), play4, left=0, right=0)
    ir_q = room_ir(rng, QSR, 0.6, 2, [(3, 0.6), (7, 0.4), (12, 0.25)])
    sig = signal.fftconvolve(drums_q + 0.6 * spk_q, ir_q)[:n_q]
    rms = np.sqrt(np.mean(sig[np.abs(sig) > 1e-4] ** 2))
    q = sig + 10 ** (-22 / 20) * rms * pink(n_q, rng)
    q += 0.003 * rms / 0.1 * np.sin(2 * np.pi * 60 * tq)
    talk_q = [(0.4, 2.2), (quest_start + 0.3, 0.6), (q_of(t0_ref) + 12.0, 1.2),
              (q_of(t0_ref) + 31.0, 0.9), (dur_q - 1.4, 1.2)]
    q += speech(n_q, QSR, rng, talk_q, 0.6 * rms)
    q = signal.sosfiltfilt(signal.butter(4, [120, 7000], "band", fs=QSR, output="sos"), q)
    q *= 0.5 / np.abs(q).max()
    stereo = np.stack([q, q + 1e-4 * rng.normal(size=n_q)], 1).astype(np.float32)
    rdir = out / "quest_raw"
    rdir.mkdir()
    sf.write(rdir / "TAKE.wav", stereo, QSR, subtype="FLOAT")
    meta = {"session_id": "TAKE", "player_id": "sim", "kit_id": "black_pearl_sim",
            "room_and_settings": "simulated", "app": "NotQuiteMyTempo quest_recorder (simulated)",
            "mic_only": True, "sample_rate": QSR, "channels": 2, "wav_format": "IEEE float32 PCM",
            "frames": n_q, "duration_s": n_q / QSR, "peak": float(np.abs(stereo).max()),
            "near_clip_fraction": 0.0, "discarded_frames": 0, "write_failed": False,
            "stop_reason": "Stopped by player", "valid_for_training": True}
    (rdir / "TAKE.capture.json").write_text(json.dumps(meta, indent=2) + "\n")

    # ---- Kevin's importer ------------------------------------------------------------------------
    if str(dc.LT_TOOLS) not in sys.path:
        sys.path.append(str(dc.LT_TOOLS))  # append: his evaluate.py must not shadow ours
    from import_quest_take import import_take
    target, report, n_sugg = import_take(rdir / "TAKE.wav", rdir / "TAKE.capture.json", session,
                                         out / "recordings")

    # ---- truth -----------------------------------------------------------------------------------
    kit = dc.load_kit(KIT_JSON)
    vdir = out / "truth"
    vdir.mkdir()
    with open(vdir / "truth_labels.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["time_s", "piece_id"])
        for t, m, _ in events:
            w.writerow([f"{q_of(t, m):.6f}", dc.piece_for(m, kit)[0]])
    dc.write_notes(vdir / "truth_ref.drums.txt", [(t, m) for t, m, _ in events], "sim truth, ref time")
    json.dump({
        "quest_start_s": quest_start, "ratio": ratio, "rt_ref_s": RT_REF,
        "speaker_extra_s": SPEAKER_EXTRA_S, "piece_delay_s": PIECE_DELAY_S,
        "t0_ref_acoustic_s": t0_ref, "taps_ref_s": taps,
        "ref_span_s": [events[0][0], events[-1][0]],
        # per sheet note (guide-relative sheet time, midi): drift at that time
        "drift": [[s["sheet"], m, s["drift"]] for (t, m), s in sorted(truth.items())],
        "kit": str(KIT_JSON), "session_dir": str(target),
    }, open(vdir / "truth.json", "w"), indent=1)
    log(f"sim: {len(events)} hits ({len(taps)} taps), ref {n_ref / SR:.1f}s @ {SR}, quest "
        f"{n_q / QSR:.1f}s @ {QSR}; imported {target} ({n_sugg} suggestions)")
    return out


def build_single(out: Path, midi=38, piece="snare", n_hits=40, seed=3, session="single_sim"):
    """A one-drum take (label.py single): isolated hits at all five velocity layers, 0.6-1.3 s
    apart, plus talking before, between and after; headset-like room, noise and band-limit.
    Writes OUT/quest_raw_single/TAKE.*, imports it as OUT/recordings/<session>, and
    OUT/truth/single_truth.csv (the hits) + single_speech.json (speech spans)."""
    out = Path(out)
    rng = np.random.default_rng(seed)
    t, hits = 3.0, []
    for _ in range(n_hits):
        hits.append((t, midi, int(rng.integers(1, 6))))
        t += rng.uniform(0.6, 1.3)
        if len(hits) == n_hits // 2:
            t += 3.0                                   # a pause with talking
    talk = [(0.5, 2.0), (hits[n_hits // 2 - 1][0] + 0.8, 2.0), (t + 0.5, 2.0)]
    n = int((t + 3.0) * QSR)
    dry = render(hits, QSR, n, lambda tt, m: tt)
    sig = signal.fftconvolve(dry, room_ir(rng, QSR, 0.6, 2, [(3, 0.6), (7, 0.4)]))[:n]
    rms = np.sqrt(np.mean(sig[np.abs(sig) > 1e-4] ** 2))
    q = sig + 10 ** (-25 / 20) * rms * pink(n, rng) + speech(n, QSR, rng, talk, 0.8 * rms)
    q = signal.sosfiltfilt(signal.butter(4, [120, 7000], "band", fs=QSR, output="sos"), q)
    q *= 0.5 / np.abs(q).max()
    rdir = out / "quest_raw_single"
    rdir.mkdir(parents=True, exist_ok=True)
    st = np.stack([q, q], 1).astype(np.float32)
    sf.write(rdir / "TAKE.wav", st, QSR, subtype="FLOAT")
    meta = {"mic_only": True, "sample_rate": QSR, "channels": 2, "frames": n, "peak": float(np.abs(st).max()),
            "discarded_frames": 0, "write_failed": False, "valid_for_training": True,
            "app": "simulated", "kit_id": "sim", "room_and_settings": "sim", "player_id": "sim"}
    (rdir / "TAKE.capture.json").write_text(json.dumps(meta) + "\n")
    if str(dc.LT_TOOLS) not in sys.path:
        sys.path.append(str(dc.LT_TOOLS))  # append: his evaluate.py must not shadow ours
    from import_quest_take import import_take
    target, _, _ = import_take(rdir / "TAKE.wav", rdir / "TAKE.capture.json", session, out / "recordings")
    vdir = out / "truth"
    vdir.mkdir(parents=True, exist_ok=True)
    with open(vdir / "single_truth.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["time_s", "piece_id"])
        w.writerows((f"{tt:.6f}", piece) for tt, _, _ in hits)
    json.dump(talk, open(vdir / "single_speech.json", "w"))
    return target


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("out", type=Path)
    p.add_argument("--loops", type=int, default=6)
    p.add_argument("--seed", type=int, default=5)
    p.add_argument("--drift-sd", type=float, default=0.025)
    p.add_argument("--quest-start", type=float, default=QUEST_START)
    p.add_argument("--ratio", type=float, default=RATIO)
    p.add_argument("--session", default="quest_sim")
    a = p.parse_args()
    build(a.out, a.loops, a.seed, a.drift_sd, a.quest_start, a.ratio, a.session)


if __name__ == "__main__":
    main()
