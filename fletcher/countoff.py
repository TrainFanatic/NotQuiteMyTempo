"""Fletcher's count-off: "Five, six, seven, eight" spoken ON the beat, at any tempo.

How it plugs in (proposal, not implemented here):
  - The count-off defines the shared song start. When the laptop starts (or restarts) a take,
    it publishes session.state `count_in` with `t0` = the time of the downbeat after the last
    count word ("one" of the song). Everything else (referee beat grid, Fletcher's bar numbers,
    Unity's metronome/backing track) aligns to that same t0.
  - Unity (Adam) plays the clips locally for sample-accurate timing: the laptop sends only the
    schedule (`schedule()`: beat time relative to t0 + clip_id, and each clip's `onset_s` from
    the manifest); Unity starts clip i at t0 + beat_time - onset_s on its audio DSP clock.
    The .wav files ship with the app like the barks (file name = clip_id).
  - Above ~130 bpm words get longer than their beat; Unity can stop a clip with a short fade at
    the next word's start (what `plan()` computes as `samples`), or the laptop can send the
    `render()`ed buffer instead (it ends exactly at t0). Network jitter never touches timing.

Clip placement. Each clip is placed by its ONSET, not its file start. `detect_onset` measures it
from the audio: zero-phase low-pass (<1 kHz, so the hiss of "s"/"f" is ignored), find the FIRST
syllable whose 40 ms RMS envelope reaches 50 % of the clip's max (the global peak can be the 2nd
syllable of "seven" or the stop burst of a whispered "eight"), then walk back on a 10 ms envelope
to 25 % of that syllable's peak. That is roughly the vowel onset, where listeners hear a spoken
word land (its P-center); placing by the first audible sample would make "six" sound late.

Fast tempos. Per word there is a budget: at most PRE_FRAC of a beat before its onset, and after
its onset until GAP_S before the next word starts (the last word must end GAP_S before t0).
If the word does not fit, the decay tail (below -20 dB) is simply cut; if its core still does not
fit, it is time-compressed with WSOLA (waveform-similarity overlap-add, numpy, 25 ms Hann
frames, +-8 ms similarity search) by up to MAX_RATIO; the onset is re-measured on the compressed
audio. Whatever still does not fit is cut (15 ms fade-out; 4 ms fade-in when the pre-onset hiss
is cut). Limits: WSOLA keeps pitch but smears plosives and sounds jittery past ~1.8x (the cap),
and it does nothing for noise-like whispers but shorten them. Measured on the real clips, the
cut eats into a word's core above ~180 bpm: at 260 bpm up to ~55 ms (sharp), ~105 ms
(shouting), ~150 ms (whispered "menacing" takes, which are long), e.g. "seven" loses its "-en".
"""

from __future__ import annotations

import json
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

COUNTOFF_DIR = Path(__file__).resolve().parent / "countoff"
CLIP_SR = 24000

BPM_MIN, BPM_MAX = 40.0, 260.0
PRE_FRAC = 0.35       # at most this fraction of a beat may sound before a word's onset
GAP_S = 0.010         # silence between one word's end and the next word's start
MAX_RATIO = 1.8       # WSOLA compression cap; the rest is trimmed
FADE_OUT_S = 0.015
CORE_DB = -20.0      # a word's core ends where it falls this far below its peak
FADE_IN_S = 0.004

ONSET_FRAC = 0.25
ONSET_HALF = 0.5
LOWPASS_HZ = 1000.0
ENV_WIN_S = 0.010
PEAK_WIN_S = 0.040

WORDS = ["one", "two", "three", "four", "five", "six", "seven", "eight"]
DELIVERIES = {  # delivery -> v4 audio tag, punctuation
    "quiet": ("[quietly]", "."),   # low, menacing
    "sharp": ("[sharp]", "."),
    "shout": ("[shouting]", "!"),
}
STYLES = ("menacing", "sharp", "shouting", "build")


def clip_id(word: str, delivery: str) -> str:
    return f"co_{word}_{delivery}"


def all_clips() -> list[tuple[str, str, str, str]]:
    """(clip_id, tagged_text, word, delivery) for every clip in the catalog."""
    out = []
    for w in WORDS:
        for d, (tag, punct) in DELIVERIES.items():
            out.append((clip_id(w, d), f"{tag} {w.capitalize()}{punct}", w, d))
    return out


# --------------------------------------------------------------------------- schedule

def count_words(beats_per_bar: int = 4, bars: int = 1) -> list[str]:
    """One bar of 4/4 counts "five six seven eight"; otherwise count from "one"
    (two bars of 4/4 = one..eight, 3/4 = "one two three")."""
    n = beats_per_bar * bars
    if beats_per_bar < 1 or bars < 1 or n > len(WORDS):
        raise ValueError(f"cannot count {bars} bar(s) of {beats_per_bar}: at most {len(WORDS)} words")
    if beats_per_bar == 4 and bars == 1:
        return WORDS[4:8]
    return WORDS[:n]


def _deliveries(style: str, n: int) -> list[str]:
    if style == "menacing":
        return ["quiet"] * n
    if style == "sharp":
        return ["sharp"] * n
    if style == "shouting":
        return ["shout"] * n
    if style == "build":  # quiet -> sharp -> shout, last word always shouted
        order = list(DELIVERIES)
        return [order[min(2, i * 3 // n)] for i in range(n - 1)] + ["shout"]
    raise ValueError(f"unknown style {style!r}; one of {STYLES}")


def schedule(bpm: float, beats_per_bar: int = 4, bars: int = 1,
             style: str = "sharp") -> list[tuple[float, str]]:
    """[(beat_time_s, clip_id)], t=0 = the downbeat after the last word; last word at -beat_s."""
    if not (BPM_MIN <= bpm <= BPM_MAX):
        raise ValueError(f"bpm {bpm} outside {BPM_MIN:g}-{BPM_MAX:g}")
    beat = 60.0 / bpm
    words = count_words(beats_per_bar, bars)
    n = len(words)
    return [(-(n - i) * beat, clip_id(w, d)) for i, (w, d) in enumerate(zip(words, _deliveries(style, n)))]


# --------------------------------------------------------------------------- analysis

def _lowpass(x: np.ndarray, sr: int, hz: float) -> np.ndarray:
    n = len(x)
    nfft = 1 << max(1, int(np.ceil(np.log2(max(2, n)))))
    X = np.fft.rfft(x, nfft)
    f = np.fft.rfftfreq(nfft, 1.0 / sr)
    gain = np.clip((1.5 * hz - f) / (0.5 * hz), 0.0, 1.0)  # flat to hz, linear taper to 1.5 hz
    return np.fft.irfft(X * gain, nfft)[:n]


def envelope(x: np.ndarray, sr: int, lowpass_hz: float | None = LOWPASS_HZ,
             win_s: float = ENV_WIN_S) -> np.ndarray:
    """Centered RMS envelope, one value per sample."""
    x = np.asarray(x, dtype=np.float64)
    if lowpass_hz:
        x = _lowpass(x, sr, lowpass_hz)
    w = max(1, int(win_s * sr))
    c = np.concatenate([[0.0], np.cumsum(np.pad(x * x, (w // 2, w - w // 2)))])
    return np.sqrt(np.maximum(c[w:w + len(x)] - c[:len(x)], 0.0) / w)


def detect_onset(x: np.ndarray, sr: int) -> float:
    """Seconds into x of the perceptual attack of its first word (see module doc)."""
    fine = envelope(x, sr)
    slow = envelope(x, sr, win_s=PEAK_WIN_S)
    if len(x) == 0 or slow.max() <= 1e-6:
        return 0.0
    top = slow.max()
    # The FIRST rise to half the max, on a 40 ms envelope: in "seven" the second syllable can be
    # louder, and in a whispered "six"/"eight" the stop burst after the closure gap spikes.
    p = int(np.nonzero(slow >= ONSET_HALF * top)[0][0])
    j = int(np.argmax(fine[:p + int(PEAK_WIN_S * sr)]))  # that syllable's own peak
    peak = fine[j]
    below = np.nonzero(fine[:j + 1] < ONSET_FRAC * peak)[0]
    if below.size == 0:
        return 0.0
    i = int(below[-1])  # fine[i] < thr <= fine[i+1]
    thr = ONSET_FRAC * peak
    frac = (thr - fine[i]) / max(1e-12, fine[i + 1] - fine[i])
    return (i + min(1.0, frac)) / sr


def audible_span(x: np.ndarray, sr: int, floor_db: float = -40.0,
                 lead_s: float = 0.010, tail_s: float = 0.030) -> tuple[int, int]:
    """[start, end) samples holding the sound, with a small margin either side."""
    env = envelope(x, sr, lowpass_hz=None)
    if env.max() <= 1e-6:
        return 0, len(x)
    idx = np.nonzero(env > env.max() * 10 ** (floor_db / 20))[0]
    return max(0, int(idx[0] - lead_s * sr)), min(len(x), int(idx[-1] + tail_s * sr))


# --------------------------------------------------------------------------- WSOLA

def wsola(x: np.ndarray, ratio: float, sr: int, frame_s: float = 0.025, tol_s: float = 0.008) -> np.ndarray:
    """Time-scale x by 1/ratio (ratio > 1 = shorter), keeping pitch."""
    x = np.asarray(x, dtype=np.float64)
    if ratio <= 1.0 + 1e-6:
        return x.copy()
    N = int(frame_s * sr) // 2 * 2
    hs, tol = N // 2, int(tol_s * sr)
    win = np.hanning(N + 1)[:-1]  # periodic: sums to 1 at 50 % overlap
    n_out = int(round(len(x) / ratio))
    pad = tol + 2 * N
    xp = np.pad(x, (pad, pad))
    out = np.zeros(n_out + N)
    wsum = np.zeros(n_out + N)
    prev = 0
    out[:N] += xp[pad:pad + N] * win
    wsum[:N] += win
    k = 1
    while k * hs < n_out:
        nominal = int(round(k * hs * ratio))
        ref = xp[pad + prev + hs: pad + prev + hs + N]
        cand = xp[pad + nominal - tol: pad + nominal + tol + N]
        corr = np.correlate(cand, ref, "valid")
        e = np.concatenate([[0.0], np.cumsum(cand * cand)])
        norm = np.sqrt(e[N:] - e[:-N]) + 1e-9
        best = nominal - tol + int(np.argmax(corr / norm))
        o = k * hs
        out[o:o + N] += xp[pad + best: pad + best + N] * win
        wsum[o:o + N] += win
        prev = best
        k += 1
    out, wsum = out[:n_out], wsum[:n_out]
    return np.where(wsum > 1e-3, out / np.maximum(wsum, 1e-3), 0.0)


# --------------------------------------------------------------------------- clips

@dataclass
class Clip:
    clip_id: str
    audio: np.ndarray  # float64, -1..1, at CLIP_SR
    onset_s: float


_cache: dict[tuple[str, float], dict[str, Clip]] = {}


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as w:
        sr = w.getframerate()
        x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float64) / 32768.0
    return x, sr


def write_wav(path: Path, x: np.ndarray | bytes, sr: int) -> None:
    pcm = x if isinstance(x, bytes) else to_pcm(x)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm)


def to_pcm(x: np.ndarray) -> bytes:
    return np.clip(np.round(x * 32767.0), -32768, 32767).astype("<i2").tobytes()


def load_clips(clip_dir: Path | str | None = None) -> dict[str, Clip]:
    d = Path(clip_dir) if clip_dir else COUNTOFF_DIR
    man = d / "manifest.json"
    key = (str(d), man.stat().st_mtime if man.exists() else 0.0)
    if key not in _cache:
        if not man.exists():
            raise FileNotFoundError(f"{man} missing: run python -m fletcher.generate_countoff")
        clips = {}
        for cid, meta in json.loads(man.read_text()).items():
            x, sr = read_wav(d / f"{cid}.wav")
            if sr != CLIP_SR:
                x = _resample(x, sr, CLIP_SR)
            clips[cid] = Clip(cid, x, float(meta["onset_s"]))
        _cache[key] = clips
    return _cache[key]


def _resample(x: np.ndarray, sr_in: int, sr_out: int) -> np.ndarray:
    if sr_in == sr_out:
        return x
    n = int(round(len(x) * sr_out / sr_in))
    return np.interp(np.arange(n) * sr_in / sr_out, np.arange(len(x)), x)


# --------------------------------------------------------------------------- plan / render

@dataclass
class Placed:
    clip_id: str
    beat_s: float       # where the onset lands (relative to t0)
    start_s: float      # when the processed audio starts (relative to t0)
    onset_s: float      # onset inside the processed audio
    audio: np.ndarray   # processed audio at CLIP_SR
    ratio: float        # WSOLA compression applied (1 = none)
    natural_s: float    # length before any shortening
    trimmed: bool

    @property
    def end_s(self) -> float:
        return self.start_s + len(self.audio) / CLIP_SR


def _beat_of(sched: list[tuple[float, str]]) -> float:
    if not sched:
        raise ValueError("empty schedule")
    return -sched[-1][0]


def _core_end(x: np.ndarray, sr: int) -> int:
    """Last sample within CORE_DB of the peak: what follows is the decay/breath tail."""
    env = envelope(x, sr, lowpass_hz=None)
    return int(np.nonzero(env >= env.max() * 10 ** (CORE_DB / 20))[0][-1]) + 1 if env.max() > 0 else len(x)


def plan(sched: list[tuple[float, str]], clip_dir: Path | str | None = None) -> list[Placed]:
    """Fit every word into its slot (compress / trim) and place it by its onset."""
    clips = load_clips(clip_dir)
    beat = _beat_of(sched)
    sr = CLIP_SR
    pre_budget = int(PRE_FRAC * beat * sr)
    out = []
    for i, (t, cid) in enumerate(sched):
        c = clips[cid]
        x, o = c.audio, int(round(c.onset_s * sr))
        nxt_pre = 0
        if i + 1 < len(sched):
            nc = clips[sched[i + 1][1]]
            nxt_pre = min(pre_budget, int(round(nc.onset_s * sr)))
        post_budget = int((beat - GAP_S) * sr) - nxt_pre
        pre, core = o, _core_end(x, sr) - o  # compress only for the word's core; its tail gets cut
        ratio = min(MAX_RATIO, max(1.0, pre / max(1, pre_budget), core / max(1, post_budget)))
        if ratio > 1.0 + 1e-3:
            x = wsola(x, ratio, sr)
            o = int(round(detect_onset(x, sr) * sr))
        else:
            ratio = 1.0
        trimmed = False
        if o > pre_budget:  # cut the start of the pre-onset part (fricative), short fade-in
            cut = o - pre_budget
            x, o = x[cut:].copy(), pre_budget
            f = min(len(x), int(FADE_IN_S * sr))
            x[:f] *= np.linspace(0.0, 1.0, f)
            o = min(o, int(round(detect_onset(x, sr) * sr)))  # the cut can move the measured attack
            trimmed = True
        if len(x) - o > post_budget:
            x = x[:o + post_budget].copy()
            f = min(len(x), int(FADE_OUT_S * sr))
            x[len(x) - f:] *= np.linspace(1.0, 0.0, f)
            trimmed = True
        out.append(Placed(cid, t, t - o / sr, o / sr, x, ratio, len(c.audio) / sr, trimmed))
    return out


def render_array(sched: list[tuple[float, str]], sample_rate: int = CLIP_SR,
                 clip_dir: Path | str | None = None) -> tuple[np.ndarray, float]:
    """Mix the count-off. Returns (float buffer, time of its first sample rel. to t0).
    The buffer starts one beat before the first word and ends exactly at t0."""
    placed = plan(sched, clip_dir)
    beat = _beat_of(sched)
    t_start = sched[0][0] - beat
    n = int(round(-t_start * CLIP_SR))
    buf = np.zeros(n)
    for p in placed:
        s = int(round((p.beat_s - t_start) * CLIP_SR)) - int(round(p.onset_s * CLIP_SR))
        a = p.audio[max(0, -s):]
        s = max(0, s)
        a = a[:max(0, n - s)]
        buf[s:s + len(a)] += a
    peak = np.abs(buf).max() if n else 0.0
    if peak > 0.98:
        buf *= 0.98 / peak
    if sample_rate != CLIP_SR:
        buf = _resample(buf, CLIP_SR, sample_rate)
    return buf, t_start


def render(sched: list[tuple[float, str]], sample_rate: int = CLIP_SR,
           clip_dir: Path | str | None = None) -> bytes:
    """16-bit mono PCM of the whole count-off; its last sample is at t0 (the song's downbeat)."""
    return to_pcm(render_array(sched, sample_rate, clip_dir)[0])


def measure_onsets(buf: np.ndarray, t_start: float, sched: list[tuple[float, str]],
                   sample_rate: int = CLIP_SR) -> list[float]:
    """Detect each word's onset in a rendered buffer; returns (onset - beat) in seconds.
    Each word is cut out between the silent gaps (>= 4 ms of near-zero samples) around its beat."""
    beat = _beat_of(sched)
    reach = int(beat * sample_rate)
    run = int(0.004 * sample_rate)

    def silent_runs(seg: np.ndarray) -> np.ndarray:  # start index of every all-silent run
        c = np.concatenate([[0], np.cumsum(np.abs(seg) < 1e-4)])
        return np.nonzero(c[run:] - c[:-run] == run)[0]

    errs = []
    for t, _ in sched:
        b = int(round((t - t_start) * sample_rate))
        lo, hi = max(0, b - reach // 2), min(len(buf), b + reach)
        before, after = silent_runs(buf[lo:b]), silent_runs(buf[b:hi])
        if before.size:
            lo += int(before[-1]) + run - 1
        if after.size:
            hi = b + int(after[0]) + 1
        errs.append(lo / sample_rate + detect_onset(buf[lo:hi], sample_rate) - (t - t_start))
    return errs
