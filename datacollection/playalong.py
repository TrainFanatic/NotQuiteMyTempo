"""Play a guide track and record a reference mic in ONE duplex stream (one device clock).

  python datacollection/playalong.py --list-devices
  python datacollection/playalong.py guide.wav --session alice_bib_94_01 [--input-device 0 --output-device 1]
  python datacollection/playalong.py --calibrate          # speaker -> mic round trip of this laptop

What gets played (all on the stream's sample clock, frame 0 = first output frame):
  PRE_S silence | 3 loud slate clicks SLATE_GAP_S apart | TAP_S window for 3 snare rim clicks | guide.wav
and the mic is recorded for exactly the same frames, so ref.wav frame n was captured in the same
callback in which output frame n was handed to the speaker. A sound scheduled at output frame n
shows up in ref.wav at about n + round_trip (measure it with --calibrate; it includes PortAudio's
reported output + input latency plus the acoustic path).

Output: <out>/<session>/ref.wav (float32, stream rate, all input channels) and take.json:
  guide           the guide.json contents (times in GUIDE-file seconds)
  guide_offset_s / guide_offset_frames   where guide.wav's first sample sits in ref.wav time
  click_times_ref_s, countoff_times_ref_s, t0_ref_s   the guide's times shifted into ref.wav time
                  (scheduled, i.e. without the round trip)
  slate_times_s   scheduled slate onsets in ref.wav time
  sample_rate, input/output device names, latency (PortAudio's stream.latency), first callback's
  inputBufferAdcTime / outputBufferDacTime, start_unix (wall clock of the first callback),
  frames, status_flags_count + status_flags (xruns), interrupted (Ctrl-C), calibration (if any).

Two devices (e.g. MacBook mic + speakers) are opened in one PortAudio duplex stream; on macOS the
built-in mic and speakers share a hardware clock. For a USB mic + other speaker, make an
Aggregate Device (Audio MIDI Setup, "drift correction" on) or the two clocks may drift.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from pathlib import Path

import numpy as np
import soundfile as sf
import sounddevice as sd

HERE = Path(__file__).resolve().parent
DEFAULT_OUT = HERE / "takes"

PRE_S = 1.0
SLATES = 3
SLATE_GAP_S = 0.5
SLATE_S = 0.012
TAP_S = 3.0          # after the last slate: drummer taps 3 rim clicks
COUNTDOWN_S = 3
BLOCK = 256

CAL_CLICKS = 10
CAL_GAP_S = 0.5
CAL_PRE_S = 1.0
CAL_SEARCH_S = 0.30  # look for each click up to this long after it was scheduled

BIG = {  # 5-line digits for the countdown
    "3": ["█████", "    █", " ████", "    █", "█████"],
    "2": ["█████", "    █", "█████", "█    ", "█████"],
    "1": ["  █  ", " ██  ", "  █  ", "  █  ", " ███ "],
}


# --------------------------------------------------------------------------- signals

def slate(sr: int) -> np.ndarray:
    """Loud broadband tick (deterministic noise burst x fast decay): sharp in cross-correlation
    and easy to see on both the laptop mic and the Quest mic."""
    n = int(SLATE_S * sr)
    rng = np.random.default_rng(1234)
    x = rng.uniform(-1, 1, n) * np.exp(-np.arange(n) / (0.003 * sr))
    x[0] = 1.0
    return 0.95 * x / np.abs(x).max()


def schedule_take(guide: np.ndarray, sr: int):
    """Full output buffer, slate onset frames, frame where the guide starts."""
    pre = int(PRE_S * sr)
    s = slate(sr)
    slates = [pre + int(round(i * SLATE_GAP_S * sr)) for i in range(SLATES)]
    g0 = slates[-1] + int(round(TAP_S * sr))
    out = np.zeros(g0 + len(guide), dtype=np.float32)
    for f in slates:
        out[f:f + len(s)] += s
    out[g0:] = guide
    return out, slates, g0


def detect_onset(x: np.ndarray, sr: int, lo: int, hi: int, noise: float) -> tuple[int | None, float]:
    """First sample in x[lo:hi] of a click: high-passed |x| crosses 30 % of the window's peak.
    Returns (frame or None if the peak isn't clearly above the noise floor, peak/noise ratio)."""
    from scipy.signal import butter, sosfilt
    seg = x[max(0, lo - 256):hi]
    y = np.abs(sosfilt(butter(4, 800, "highpass", fs=sr, output="sos"), seg))[min(256, lo):]
    if len(y) == 0:
        return None, 0.0
    pk = float(y.max())
    snr = pk / max(noise, 1e-9)
    if snr < 6.0:
        return None, snr
    return lo + int(np.nonzero(y >= 0.3 * pk)[0][0]), snr


def noise_floor(x: np.ndarray, sr: int, upto: int) -> float:
    from scipy.signal import butter, sosfilt
    seg = x[: max(upto, 1)]
    y = np.abs(sosfilt(butter(4, 800, "highpass", fs=sr, output="sos"), seg))[int(0.1 * sr):]
    return float(np.percentile(y, 99.5)) if len(y) else 0.0


# --------------------------------------------------------------------------- duplex

def _dev_name(idx, kind):
    try:
        return sd.query_devices(idx, kind)["name"]
    except Exception:  # noqa: BLE001
        return str(idx)


def run_duplex(play: np.ndarray, sr: int, input_device=None, output_device=None,
               in_channels: int | None = None, progress=None):
    """Play `play` (mono) and record the same number of frames in one sd.Stream.
    Returns (rec[frames, ch] float32, info). Ctrl-C stops early and keeps what was recorded."""
    din = sd.query_devices(input_device, "input")
    dout = sd.query_devices(output_device, "output")
    in_ch = in_channels or min(2, int(din["max_input_channels"]))
    out_ch = min(2, int(dout["max_output_channels"]))
    n = len(play)
    rec = np.zeros((n, in_ch), dtype=np.float32)
    st = {"pos": 0, "flags": 0, "flag_names": {}, "first": None, "start_unix": None}
    done = threading.Event()

    def cb(indata, outdata, frames, t, status):
        p = st["pos"]
        if st["first"] is None:
            st["start_unix"] = time.time()
            st["first"] = {"currentTime": t.currentTime, "inputBufferAdcTime": t.inputBufferAdcTime,
                           "outputBufferDacTime": t.outputBufferDacTime}
        if status:
            st["flags"] += 1
            for name in ("input_underflow", "input_overflow", "output_underflow",
                         "output_overflow", "priming_output"):
                if getattr(status, name, False):
                    st["flag_names"][name] = st["flag_names"].get(name, 0) + 1
        k = max(0, min(frames, n - p))
        outdata.fill(0)
        outdata[:k] = play[p:p + k, None]
        rec[p:p + k] = indata[:k]
        st["pos"] = p + k
        if p + k >= n:
            done.set()
            raise sd.CallbackStop

    stream = sd.Stream(samplerate=sr, blocksize=BLOCK, device=(input_device, output_device),
                       channels=(in_ch, out_ch), dtype="float32", callback=cb,
                       finished_callback=done.set)
    interrupted = False
    with stream:
        latency = list(stream.latency)
        try:
            while not done.wait(0.05):
                if progress:
                    progress(st["pos"])
        except KeyboardInterrupt:
            interrupted = True
            stream.abort()  # stop now; frames already recorded are kept
    frames = st["pos"]
    info = {
        "sample_rate": sr,
        "input_device": {"index": din.get("index", input_device), "name": din["name"], "channels": in_ch},
        "output_device": {"index": dout.get("index", output_device), "name": dout["name"], "channels": out_ch},
        "latency_s": {"input": latency[0], "output": latency[1]},
        "blocksize": BLOCK,
        "first_callback_time": st["first"],
        "start_unix": st["start_unix"],
        "frames": frames,
        "scheduled_frames": n,
        "status_flags_count": st["flags"],
        "status_flags": st["flag_names"],
        "interrupted": interrupted,
    }
    return rec[:frames], info


def pick_rate(guide_sr: int, input_device, output_device) -> int:
    try:
        sd.check_input_settings(device=input_device, samplerate=guide_sr)
        sd.check_output_settings(device=output_device, samplerate=guide_sr)
        return guide_sr
    except Exception:  # noqa: BLE001
        return int(sd.query_devices(output_device, "output")["default_samplerate"])


def resample(x: np.ndarray, sr_in: int, sr_out: int) -> np.ndarray:
    if sr_in == sr_out:
        return x
    from math import gcd
    from scipy.signal import resample_poly
    g = gcd(sr_in, sr_out)
    return resample_poly(x, sr_out // g, sr_in // g).astype(np.float32)


# --------------------------------------------------------------------------- UI

def countdown(seconds: int = COUNTDOWN_S):
    for i in range(seconds, 0, -1):
        rows = BIG.get(str(i), [str(i)])
        print("\n" + "\n".join("        " + r for r in rows), flush=True)
        time.sleep(1.0)
    print("\n        >>> GO <<<\n", flush=True)


def phase_printer(sr, slates, g0, guide):
    marks = [(slates[0], "SLATE clicks (laptop)"),
             (slates[-1] + int(0.4 * sr), "TAP 3 RIM CLICKS ON THE SNARE NOW  x  x  x"),
             (g0 + int(guide["countoff_times_s"][0] * sr) - int(0.3 * sr), "COUNT-OFF ... five six seven eight"),
             (g0 + guide["t0_sample"], "PLAY! (bar 1)")]
    t0, bar_len = g0 + guide["t0_sample"], None
    if guide.get("loop_len_s") and guide.get("sheet_bars"):
        bar_len = guide["loop_len_s"] / guide["sheet_bars"]
    state = {"i": 0, "bar": 0}

    def cb(pos):
        while state["i"] < len(marks) and pos >= marks[state["i"]][0]:
            print(f"  [{pos / sr:6.1f}s]  {marks[state['i']][1]}", flush=True)
            state["i"] += 1
        if bar_len and pos >= t0:
            bar = int((pos - t0) / sr / bar_len) + 1
            total = guide["sheet_bars"] * guide["sheet_loops"]
            if bar != state["bar"] and bar <= total:
                state["bar"] = bar
                print(f"  [{pos / sr:6.1f}s]  bar {bar}/{total}", flush=True)
    return cb


# --------------------------------------------------------------------------- commands

def record_take(guide_wav: Path, session: str, out_dir: Path = DEFAULT_OUT, input_device=None,
                output_device=None, in_channels=None, countdown_s: int = COUNTDOWN_S) -> Path:
    guide_wav = Path(guide_wav)
    gjson = guide_wav.with_suffix(".json")
    if not gjson.exists():
        raise SystemExit(f"{gjson} missing: make the guide with make_guide.py")
    guide = json.loads(gjson.read_text())
    audio, gsr = sf.read(str(guide_wav), dtype="float32", always_2d=True)
    audio = audio.mean(axis=1)
    sr = pick_rate(gsr, input_device, output_device)
    audio = resample(audio, gsr, sr)
    play, slates, g0 = schedule_take(audio, sr)
    k = sr / gsr  # guide-file seconds are unchanged by resampling; only frame indices scale

    take_dir = Path(out_dir) / session
    if take_dir.exists() and any(take_dir.iterdir()):
        raise SystemExit(f"{take_dir} already exists; pick another --session")
    take_dir.mkdir(parents=True, exist_ok=True)

    print(f"\nSession {session}: {len(play) / sr:.1f} s at {sr} Hz -> {take_dir}")
    print("  1. Quest: start recording in the NQMT recorder app now (headset on).")
    print(f"  2. The laptop plays {SLATES} loud SLATE clicks, then you TAP 3 RIM CLICKS on the snare.")
    print("  3. Fletcher counts you in; play the sheet with the click. Ctrl-C stops and saves.")
    if countdown_s:
        countdown(countdown_s)
    rec, info = run_duplex(play, sr, input_device, output_device, in_channels,
                           phase_printer(sr, slates, g0, dict(guide, t0_sample=int(round(guide.get("t0_sample", 0) * k)))))
    sf.write(str(take_dir / "ref.wav"), rec, sr, subtype="FLOAT")
    off = g0 / sr
    take = {
        "format": "take/1",
        "session": session,
        "guide_wav": str(guide_wav.resolve()),
        "guide": guide,
        **info,
        "guide_offset_s": off,
        "guide_offset_frames": g0,
        "slate_times_s": [f / sr for f in slates],
        "slate_frames": slates,
        "tap_window_s": [slates[-1] / sr + SLATE_S, g0 / sr],
        "t0_ref_s": off + guide["t0_s"] if "t0_s" in guide else None,
        "click_times_ref_s": [off + t for t in guide.get("click_times_s", [])],
        "countoff_times_ref_s": [off + t for t in guide.get("countoff_times_s", [])],
        "times_note": "*_ref_s and slate_times_s are SCHEDULED output times on ref.wav's clock; "
                      "the sound reaches the mic about calibration.median_s later",
    }
    cal = DEFAULT_OUT / "calibration.json"
    if cal.exists():
        take["calibration"] = json.loads(cal.read_text())
    (take_dir / "take.json").write_text(json.dumps(take, indent=1) + "\n")
    peak = float(np.abs(rec).max()) if rec.size else 0.0
    print(f"\nsaved {take_dir}/ref.wav ({info['frames'] / sr:.1f} s, peak {peak:.3f}"
          f"{', INTERRUPTED' if info['interrupted'] else ''}, {info['status_flags_count']} status flags)")
    if peak < 1e-4:
        print("WARNING: the recording is silent. Check the terminal's microphone permission "
              "(System Settings > Privacy & Security > Microphone) and --input-device.")
    return take_dir


def calibrate(input_device=None, output_device=None, sr: int = 48000, out: Path | None = None,
              in_channels=None) -> dict:
    sr = pick_rate(sr, input_device, output_device)
    pre, gap = int(CAL_PRE_S * sr), int(CAL_GAP_S * sr)
    s = slate(sr)
    sched = [pre + i * gap for i in range(CAL_CLICKS)]
    play = np.zeros(sched[-1] + gap + int(CAL_SEARCH_S * sr), dtype=np.float32)
    for f in sched:
        play[f:f + len(s)] += s
    print(f"Calibrating: {CAL_CLICKS} clicks through the speaker, recording the mic ({sr} Hz)...")
    rec, info = run_duplex(play, sr, input_device, output_device, in_channels)
    x = rec.mean(axis=1) if rec.ndim == 2 else rec
    noise = noise_floor(x, sr, pre - int(0.05 * sr))
    offs, snrs = [], []
    for f in sched:
        on, snr = detect_onset(x, sr, f, min(len(x), f + int(CAL_SEARCH_S * sr)), noise)
        snrs.append(round(snr, 1))
        offs.append(None if on is None else (on - f) / sr)
    good = np.array([o for o in offs if o is not None])
    res = {
        "format": "calibration/1",
        "measured_unix": time.time(),
        **info,
        "scheduled_frames": sched,
        "offsets_s": offs,
        "peak_to_noise": snrs,
        "detected": int(len(good)),
        "median_s": float(np.median(good)) if len(good) else None,
        "spread_s": {"std": float(good.std()), "min": float(good.min()), "max": float(good.max()),
                     "mad": float(np.median(np.abs(good - np.median(good))))} if len(good) else None,
        "recording_peak": float(np.abs(x).max()) if x.size else 0.0,
        "note": "offset = detected onset in the recording - scheduled output frame: PortAudio "
                "output+input latency plus the acoustic speaker->mic path",
    }
    ok = len(good) >= CAL_CLICKS // 2
    if out:  # a failed run must not overwrite a good calibration that take.json would copy
        out = Path(out) if ok else Path(out).with_name(Path(out).stem + ".failed.json")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(res, indent=1) + "\n")
        res["path"] = str(out)
    if not ok:
        print(f"Only {len(good)}/{CAL_CLICKS} clicks detected (recording peak {res['recording_peak']:.2e}; "
              f"peak/noise per click {snrs}). Peak ~0 = no microphone permission for this terminal; "
              "a normal peak but no clicks = speaker muted / volume too low or wrong output device. "
              f"Saved to {res.get('path')}.")
        return res
    else:
        sp = res["spread_s"]
        print(f"round trip: median {res['median_s'] * 1e3:.2f} ms, std {sp['std'] * 1e3:.3f} ms, "
              f"range {sp['min'] * 1e3:.2f}..{sp['max'] * 1e3:.2f} ms over {len(good)}/{CAL_CLICKS} clicks; "
              f"PortAudio reports in {info['latency_s']['input'] * 1e3:.1f} + out "
              f"{info['latency_s']['output'] * 1e3:.1f} ms")
    return res


def _dev(v):
    if v is None:
        return None
    try:
        return int(v)
    except ValueError:
        return v


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("guide", nargs="?", type=Path, help="guide.wav from make_guide.py (guide.json next to it)")
    p.add_argument("--session", help="take name -> <out>/<session>/")
    p.add_argument("--input-device", help="PortAudio device index or name substring")
    p.add_argument("--output-device")
    p.add_argument("--channels", type=int, help="input channels to record (default min(2, device))")
    p.add_argument("--list-devices", action="store_true")
    p.add_argument("--calibrate", action="store_true", help="measure speaker->mic round trip")
    p.add_argument("--countdown", type=int, default=COUNTDOWN_S)
    p.add_argument("--out", type=Path, default=DEFAULT_OUT)
    a = p.parse_args(argv)
    if a.list_devices:
        print(sd.query_devices())
        print(f"default (input, output): {sd.default.device}")
        return
    ind, outd = _dev(a.input_device), _dev(a.output_device)
    if a.calibrate:
        calibrate(ind, outd, out=a.out / "calibration.json", in_channels=a.channels)
        return
    if not a.guide or not a.session:
        p.error("guide.wav and --session are required (or --list-devices / --calibrate)")
    record_take(a.guide, a.session, a.out, ind, outd, a.channels, a.countdown)


if __name__ == "__main__":
    main()
