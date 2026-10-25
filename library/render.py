"""Audio rendering: MIDI -> backing.ogg with FluidSynth, and a click track from a tempo map.

Output is Ogg Vorbis, 48 kHz stereo. Sample 0 is song time 0 (the downbeat of bar 1): the
renderer is told where song time 0 falls in the MIDI (`start_s`) and drops everything before it.
FluidSynth's fast render starts MIDI time 0 at sample 0 (tests/test_render.py measures it).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

from . import soundfont

SR = 48_000
# Backing loudness: every song is normalised to TARGET_LUFS integrated (ITU-R BS.1770), but its
# sample peak never above PEAK, so a dynamic orchestral MIDI can end up a little quieter. Peak
# normalising alone left songs 5 dB apart (-21 to -16 LUFS). Levels: quest_app/AUDIO.md.
TARGET_LUFS = -18.0
PEAK = 10 ** (-1 / 20)
# FluidSynth renders in 64-sample blocks: an event sounds at the start of the block after the one
# it falls in, i.e. 64 samples late plus up to 63 more (measured, tests/test_render.py). The fixed
# 64 is removed below; the rest (< 1.33 ms at 48 kHz, always late) remains.
FLUID_BLOCK = 64


class RenderError(RuntimeError):
    pass


def fluidsynth_bin() -> str | None:
    return os.environ.get("NQMT_FLUIDSYNTH") or shutil.which("fluidsynth") or (
        "/opt/homebrew/bin/fluidsynth" if Path("/opt/homebrew/bin/fluidsynth").exists() else None)


def available() -> str | None:
    """None if backing can be rendered, else the reason it can't."""
    if fluidsynth_bin() is None:
        return "fluidsynth not found (brew install fluid-synth)"
    if soundfont.path() is None:
        return "no soundfont (python -m library.soundfont)"
    return None


def render_midi(midi_bytes: bytes, start_s: float = 0.0, sr: int = SR) -> np.ndarray:
    """MIDI bytes -> float32 (n, 2) array at `sr`, sample 0 = MIDI time `start_s`."""
    why = available()
    if why:
        raise RenderError(why)
    with tempfile.TemporaryDirectory() as td:
        mid, wav = Path(td) / "in.mid", Path(td) / "out.wav"
        mid.write_bytes(midi_bytes)
        cmd = [fluidsynth_bin(), "-ni", "-q", "-g", "0.6", "-r", str(sr), "-T", "wav", "-O", "float",
               "-F", str(wav), str(soundfont.path()), str(mid)]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if r.returncode != 0 or not wav.exists():
            raise RenderError(f"fluidsynth failed ({r.returncode}): {r.stderr.strip()[-400:]}")
        audio, got_sr = sf.read(str(wav), dtype="float32", always_2d=True)
    if got_sr != sr:
        raise RenderError(f"fluidsynth wrote {got_sr} Hz, wanted {sr}")
    if audio.shape[1] == 1:
        audio = np.repeat(audio, 2, axis=1)
    skip = round(start_s * sr) + FLUID_BLOCK
    if skip > 0:
        audio = audio[skip:]
    elif skip < 0:
        audio = np.concatenate([np.zeros((-skip, 2), np.float32), audio])
    return audio[:, :2]


def loudness(audio: np.ndarray, sr: int = SR) -> float:
    """Integrated loudness in LUFS (BS.1770-4: K-weighting, 400 ms blocks, -70 LUFS absolute and
    -10 LU relative gates); -inf for silence."""
    from scipy.signal import lfilter

    x = audio if audio.ndim == 2 else audio[:, None]
    f0, gain, q = 1681.974450955533, 3.999843853973347, 0.7071752369554196  # high shelf
    k = np.tan(np.pi * f0 / sr)
    vh, vb = 10 ** (gain / 20), 10 ** (gain / 20 * 0.4996667741545416)
    a0 = 1 + k / q + k * k
    y = lfilter([(vh + vb * k / q + k * k) / a0, 2 * (k * k - vh) / a0, (vh - vb * k / q + k * k) / a0],
                [1, 2 * (k * k - 1) / a0, (1 - k / q + k * k) / a0], x, axis=0)
    f0, q = 38.13547087602444, 0.5003270373238773  # RLB high-pass
    k = np.tan(np.pi * f0 / sr)
    a0 = 1 + k / q + k * k
    y = lfilter([1, -2, 1], [1, 2 * (k * k - 1) / a0, (1 - k / q + k * k) / a0], y, axis=0)
    n, hop = int(0.4 * sr), int(0.1 * sr)
    if len(y) < n:
        return float("-inf")
    c = np.cumsum(np.concatenate([np.zeros((1, y.shape[1])), y.astype(np.float64) ** 2]), axis=0)
    z = ((c[n::hop] - c[:-n:hop][:len(c[n::hop])]) / n).sum(axis=1)
    lk = lambda v: -0.691 + 10 * np.log10(v)  # noqa: E731
    with np.errstate(divide="ignore"):
        z = z[lk(z) > -70]
        if not len(z):
            return float("-inf")
        z = z[lk(z) > lk(z.mean()) - 10]
    return float(lk(z.mean()))


def normalise(audio: np.ndarray, target_lufs: float = TARGET_LUFS, peak: float = PEAK) -> np.ndarray:
    """Gain to target_lufs, capped so the sample peak stays at or under `peak`."""
    m = float(np.max(np.abs(audio))) if audio.size else 0.0
    if m < 1e-6:
        return audio
    gain = peak / m
    lufs = loudness(audio)
    if np.isfinite(lufs):
        gain = min(gain, 10 ** ((target_lufs - lufs) / 20))
    return (audio * gain).astype(np.float32)


def write_ogg(path: Path, audio: np.ndarray, sr: int = SR, min_s: float = 0.0) -> None:
    """min_s pads with silence so the file (and a player's playback clock) covers the whole song."""
    want = max(int(np.ceil(min_s * sr)), sr // 10)
    if len(audio) < want:
        audio = np.concatenate([audio, np.zeros((want - len(audio), 2), np.float32)])
    audio = np.clip(audio, -1, 1)
    # libsndfile's Vorbis encoder segfaults on one huge write (seen with a 4-minute song): chunk it
    with sf.SoundFile(str(path), "w", sr, audio.shape[1], format="OGG", subtype="VORBIS") as f:
        for i in range(0, len(audio), 8192):
            f.write(audio[i:i + 8192])


def click_track(tempo_map: dict, sr: int = SR) -> np.ndarray:
    """A click on every beat of every bar (accented downbeats), stereo float32."""
    end = tempo_map["end_s"]
    out = np.zeros((int(end * sr) + sr // 2, 2), np.float32)
    n = int(0.03 * sr)
    t = np.arange(n) / sr
    env = np.exp(-t * 150)
    hi = (np.sin(2 * np.pi * 1760 * t) * env * 0.7).astype(np.float32)
    lo = (np.sin(2 * np.pi * 1175 * t) * env * 0.45).astype(np.float32)
    for b in tempo_map["bars"]:
        for k in range(b["numerator"]):
            # beats inside a bar use the bar's own tempo; a mid-bar tempo change shifts at most
            # the clicks after it within that bar (bar starts stay exact).
            ts = b["start_s"] + k * b["bar_s"] / b["numerator"]
            i = round(ts * sr)
            if 0 <= i < len(out):
                s = hi if k == 0 else lo
                m = min(n, len(out) - i)
                out[i:i + m] += s[:m, None]
    return out
