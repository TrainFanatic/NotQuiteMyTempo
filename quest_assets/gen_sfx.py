#!/usr/bin/env python3
"""Procedural sound effects for Not Quite My Tempo.

Every sound is synthesized from seeded noise, modal resonator banks, pitch-swept
oscillators, Karplus-Strong strings, stick-slip friction pulses and a tiny
synthetic room impulse. No recorded audio is used anywhere.

Run with the system python3 (numpy and scipy required):

    python3 gen_sfx.py

Output goes to sfx/<name>.wav (mono, 16-bit PCM, 22050 Hz) plus
sfx/sfx_manifest.json and sfx/README.md. Analysis of every sound (peak, RMS,
duration, spectral centroid, onset level) is printed at the end, and the
script raises on any sound that is silent, clipped, mistimed or off-spec.
"""

import json
import os
import sys
import wave
import zlib

import numpy as np
from scipy import signal

SR = 22050
NYQ = SR / 2.0
HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "sfx")
DEFAULT_PEAK_DBFS = -3.0
FADE_IN_S = 0.004


class SfxError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Basic helpers
# ---------------------------------------------------------------------------

def n_of(sec):
    n = int(round(sec * SR))
    if n <= 0:
        raise SfxError(f"non-positive length for {sec} s")
    return n


def t_axis(n):
    return np.arange(n) / SR


def nz(x):
    """Normalise to unit peak. Raises on a silent signal."""
    p = np.max(np.abs(x))
    if not np.isfinite(p) or p < 1e-12:
        raise SfxError("tried to normalise a silent or non-finite signal")
    return x / p


def _check_hz(*fs):
    for f in fs:
        if not (0 < f < NYQ):
            raise SfxError(f"filter frequency {f} Hz outside (0, {NYQ})")


def lp(x, fc, order=2):
    _check_hz(fc)
    return signal.sosfilt(signal.butter(order, fc, "lowpass", fs=SR, output="sos"), x)


def hp(x, fc, order=2):
    _check_hz(fc)
    return signal.sosfilt(signal.butter(order, fc, "highpass", fs=SR, output="sos"), x)


def bp(x, lo, hi, order=2):
    _check_hz(lo, hi)
    if lo >= hi:
        raise SfxError(f"band-pass lo {lo} >= hi {hi}")
    return signal.sosfilt(signal.butter(order, [lo, hi], "bandpass", fs=SR, output="sos"), x)


def peak_res(x, f, q):
    """Resonant peak (scipy iirpeak) used for cavity resonances."""
    _check_hz(f)
    b, a = signal.iirpeak(f, q, fs=SR)
    return signal.lfilter(b, a, x)


def tail_fade(x, sec=0.008):
    """Short raised-cosine fade at the end of a truncated component so that
    cutting a decaying tail never leaves a click."""
    m = min(len(x) // 4, n_of(sec))
    if m < 2:
        return x
    x = x.copy()
    x[-m:] *= 0.5 + 0.5 * np.cos(np.pi * np.arange(m) / m)
    return x


def place(out, sig, t0, gain=1.0):
    """Add sig into out starting at t0 seconds, truncating at the end of out."""
    i = int(round(t0 * SR))
    if i < 0 or i >= len(out):
        raise SfxError(f"placement at {t0} s outside buffer of {len(out) / SR:.3f} s")
    m = min(len(sig), len(out) - i)
    if m < len(sig) and np.max(np.abs(sig[m:])) > 0.05 * np.max(np.abs(sig)):
        raise SfxError(f"component placed at {t0:.3f} s is cut off while still loud; lengthen the buffer")
    seg = sig[:m] if m == len(sig) else tail_fade(sig[:m])
    out[i:i + m] += gain * seg
    return out


def crunch(x, drive):
    """Gentle tanh saturation for a little PS2-era grit. Drive is bounded so
    nothing turns into a harsh square wave."""
    if not (1.0 <= drive <= 3.0):
        raise SfxError(f"crunch drive {drive} outside the 1..3 safe range")
    return np.tanh(drive * nz(x)) / np.tanh(drive)


def white(rng, n):
    return rng.standard_normal(n)


# ---------------------------------------------------------------------------
# Synthesis building blocks
# ---------------------------------------------------------------------------

def pulse(width_ms):
    """Half-sine contact pulse. Width sets hardness (short = hard stick)."""
    n = max(2, int(round(width_ms / 1000.0 * SR)))
    return np.sin(np.pi * (np.arange(n) + 0.5) / n)


def burst(rng, dur, tau, lo=None, hi=None, attack=0.0002):
    """Noise burst with near-instant attack and exponential decay."""
    n = n_of(dur)
    t = t_axis(n)
    e = np.exp(-t / tau) * np.minimum(1.0, (t + 1.0 / SR) / attack)
    x = white(rng, n) * e
    if lo is not None and hi is not None:
        x = bp(x, lo, hi)
    elif lo is not None:
        x = hp(x, lo)
    elif hi is not None:
        x = lp(x, hi)
    return tail_fade(x, 0.003)


def modal_ir(modes, dur):
    """Impulse response of a bank of damped sinusoids [(freq, tau, amp)]."""
    n = n_of(dur)
    t = t_axis(n)
    ir = np.zeros(n)
    resid = sum(abs(a) * np.exp(-dur / tau) for f, tau, a in modes) / sum(abs(a) for f, tau, a in modes)
    if resid > 0.03:
        raise SfxError(f"modal ring truncated at {dur} s while still at {20 * np.log10(resid):.0f} dB; render longer")
    for f, tau, a in modes:
        _check_hz(f)
        if tau <= 0:
            raise SfxError(f"mode {f} Hz has non-positive decay")
        ir += a * np.exp(-t / tau) * np.sin(2 * np.pi * f * t)
    return nz(tail_fade(ir))


def strike(exc, modes, dur):
    """Modal synthesis: convolve an excitation with a modal impulse response.
    The render is stretched to at least five time constants of the slowest
    mode so a ring is never chopped off."""
    dur = max(dur, 5.0 * max(tau for f, tau, a in modes))
    n = n_of(dur)
    y = signal.fftconvolve(exc, modal_ir(modes, dur))[:n]
    if len(y) < n:
        y = np.pad(y, (0, n - len(y)))
    return nz(tail_fade(y))


def jitter_modes(rng, modes, f_spread=0.04, tau_spread=0.2, amp_spread=0.4, f_scale=1.0, tau_scale=1.0):
    out = []
    for f, tau, a in modes:
        out.append((f * f_scale * rng.uniform(1 - f_spread, 1 + f_spread),
                    tau * tau_scale * rng.uniform(1 - tau_spread, 1 + tau_spread),
                    a * rng.uniform(1 - amp_spread, 1 + amp_spread)))
    return out


def thump(f0, f1, tau_f, tau_a, dur, attack=0.0008):
    """Sine with an exponential pitch drop from f0 to f1 (kick-like body)."""
    _check_hz(f0, f1)
    n = n_of(dur)
    t = t_axis(n)
    f = f1 + (f0 - f1) * np.exp(-t / tau_f)
    ph = 2 * np.pi * np.cumsum(f) / SR
    env = np.exp(-t / tau_a) * np.minimum(1.0, (t + 1.0 / SR) / attack)
    return tail_fade(np.sin(ph) * env)


def sweep_bp(x, fc, q):
    """Chamberlin state-variable band-pass with a per-sample centre frequency."""
    fc = np.asarray(fc, dtype=float)
    if fc.shape != x.shape:
        raise SfxError("sweep_bp needs one centre frequency per sample")
    if np.max(fc) > SR / 6.0 or np.min(fc) <= 0:
        raise SfxError(f"sweep_bp centre outside stable range (0, {SR / 6:.0f}] Hz")
    f = 2.0 * np.sin(np.pi * fc / SR)
    qi = 1.0 / q
    y = np.empty_like(x)
    low = band = 0.0
    for i in range(len(x)):
        high = x[i] - low - qi * band
        band += f[i] * high
        low += f[i] * band
        y[i] = band
    return y


def curve(n, points):
    """Piecewise linear curve through [(t_frac, value)] over n samples."""
    xs = np.array([p[0] for p in points]) * (n - 1)
    ys = np.array([p[1] for p in points])
    return np.interp(np.arange(n), xs, ys)


def log_curve(n, points):
    xs = np.array([p[0] for p in points]) * (n - 1)
    ys = np.log(np.array([p[1] for p in points]))
    return np.exp(np.interp(np.arange(n), xs, ys))


def room_ir(rng, rt60, wet_dur=None, damp_hz=3200.0):
    """Tiny synthetic wood-panelled room: a handful of early reflections plus a
    low-passed exponentially decaying noise tail. The direct path is excluded
    (it is the dry signal)."""
    dur = wet_dur if wet_dur is not None else rt60
    n = n_of(dur)
    t = t_axis(n)
    tail = lp(white(rng, n), damp_hz) * np.exp(-6.91 * t / rt60)
    tail *= np.clip((t - 0.008) / 0.012, 0.0, 1.0)
    tail = 0.6 * tail / np.sqrt(np.sum(tail ** 2))
    ir = tail
    for d, g in ((0.0037, 0.42), (0.0068, -0.30), (0.0105, 0.26), (0.0152, -0.2), (0.0213, 0.15)):
        ir[int(round(d * SR))] += g * rng.uniform(0.8, 1.2)
    return ir


def add_room(rng, x, wet, rt60, damp_hz=3200.0):
    ir = room_ir(rng, rt60, damp_hz=damp_hz)
    w = signal.fftconvolve(x, ir)
    y = np.zeros(len(w))
    y[:len(x)] += x
    return y + wet * w * (np.max(np.abs(x)) / max(np.max(np.abs(w)), 1e-12))


def poisson_clicks(rng, n, rate_env, amp_sigma=0.8):
    """Sparse impulse train whose density follows rate_env (clicks per second)."""
    prob = np.clip(rate_env / SR, 0.0, 1.0)
    hits = rng.random(n) < prob
    amps = np.minimum(rng.lognormal(0.0, amp_sigma, n), 2.5) * rng.choice([-1.0, 1.0], n)
    return np.where(hits, amps, 0.0)


def crinkle(rng, n, rate_env, lo=1500, hi=7500):
    """Paper or cloth crackle: Poisson clicks through two resonant bands."""
    c1 = poisson_clicks(rng, n, rate_env * 0.6)
    c2 = poisson_clicks(rng, n, rate_env * 0.4)
    mid = (lo * hi) ** 0.5
    y = bp(c1, lo, mid * 1.2) + 0.8 * bp(c2, mid * 0.8, hi)
    return y


def stick_slip(rng, n, rate_hz, amp_env, jitter=0.25):
    """Friction creak excitation: impulses spaced by a wandering period."""
    x = np.zeros(n)
    pos = 0.0
    while True:
        i = int(pos)
        if i >= n:
            break
        x[i] += amp_env[i] * rng.uniform(0.5, 1.2)
        pos += SR / (rate_hz[i] * rng.uniform(1 - jitter, 1 + jitter))
    return x


def karplus(rng, f, dur, t60, bright=0.5, pluck_noise_lp=4000.0):
    """Karplus-Strong plucked string, block-vectorised. t60 is the time for the
    loop gain alone to drop the string by 60 dB."""
    _check_hz(f)
    n = n_of(dur)
    period = int(round(SR / f))
    damp = 10.0 ** (-3.0 / (t60 * SR / period))
    buf = lp(white(rng, period), pluck_noise_lp, order=1)
    y = np.zeros(n + period + 1)
    y[:period] = buf
    a = 0.5 + 0.5 * bright
    i = period
    while i < n + period:
        j = min(i + period, n + period)
        prev = y[i - period:j - period]
        prev2 = y[i - period - 1:j - period - 1] if i - period - 1 >= 0 else np.concatenate(([0.0], y[i - period:j - period - 1]))
        y[i:j] = damp * (a * prev + (1 - a) * prev2)
        i = j
    return tail_fade(y[period:period + n], 0.05)


# ---------------------------------------------------------------------------
# Shared physical models
# ---------------------------------------------------------------------------

FLOOR_MODES = [(108, 0.02, 0.3), (181, 0.018, 0.45), (266, 0.016, 0.6), (418, 0.014, 0.85),
               (637, 0.012, 1.0), (979, 0.01, 1.0), (1490, 0.007, 0.8), (2280, 0.005, 0.55),
               (3310, 0.003, 0.3)]

# Thin pressed-steel music stand desk (inharmonic plate modes).
DESK_MODES = [(312, 0.22, 1.0), (505, 0.19, 0.8), (721, 0.16, 0.75), (904, 0.14, 0.55),
              (1123, 0.12, 0.5), (1372, 0.1, 0.42), (1655, 0.085, 0.35), (2063, 0.07, 0.3),
              (2527, 0.055, 0.22), (3024, 0.045, 0.16)]

# Free-free steel tube (bar ratios 1, 2.756, 5.404, 8.933).
def tube_modes(f0, tau0, amp=1.0):
    ratios = (1.0, 2.756, 5.404, 8.933)
    out = []
    for k, r in enumerate(ratios):
        f = f0 * r
        if f < NYQ * 0.95:
            out.append((f, tau0 / (1 + 1.5 * k), amp / (1 + 0.8 * k)))
    return out


def floor_hit(rng, dur=0.15, f_scale=1.0, tau_scale=1.0, width_ms=1.5, noise_lp=2500):
    exc = np.concatenate([pulse(width_ms), np.zeros(8)])
    exc = exc + 0.6 * burst(rng, len(exc) / SR, 0.0015, hi=noise_lp)
    return strike(exc, jitter_modes(rng, FLOOR_MODES, f_scale=f_scale, tau_scale=tau_scale), dur)


def metal_hit(rng, modes, dur, width_ms=0.4, spread=0.45):
    """One metal impact. The mode amplitudes are re-weighted every hit to mimic
    a different contact point."""
    m = jitter_modes(rng, modes, f_spread=0.01, tau_spread=0.15, amp_spread=spread)
    exc = np.concatenate([pulse(width_ms), np.zeros(4)])
    exc = exc + 0.15 * burst(rng, len(exc) / SR + 0.001, 0.0006)[:len(exc)]
    return strike(exc, m, dur)


def bounce_times(rng, first, ratio, count, t0=0.0):
    ts, gap, t = [], first, t0
    for _ in range(count):
        t += gap * rng.uniform(0.85, 1.15)
        ts.append(t)
        gap *= ratio
    return ts


# ---------------------------------------------------------------------------
# Footsteps
# ---------------------------------------------------------------------------

STEP_VARIANTS = {
    1: dict(f_scale=1.00, gap=0.068, toe=0.45, click=0.4, heel_w=0.4),
    2: dict(f_scale=0.90, gap=0.085, toe=0.30, click=0.55, heel_w=0.32),
    3: dict(f_scale=1.10, gap=0.056, toe=0.58, click=0.3, heel_w=0.5),
    4: dict(f_scale=0.96, gap=0.098, toe=0.38, click=0.48, heel_w=0.28),
}


def foot_step(rng, v):
    p = STEP_VARIANTS[v]
    n = n_of(0.32)
    out = np.zeros(n)
    heel = floor_hit(rng, 0.2, p["f_scale"], 1.0, p["heel_w"])
    tick = burst(rng, 0.02, 0.0011, 1600, 6000)
    body = thump(150 * p["f_scale"], 95 * p["f_scale"], 0.01, 0.022, 0.1)
    place(out, heel, 0.0)
    place(out, nz(tick), 0.0, p["click"] * 1.6)
    place(out, body, 0.0, 0.22)
    knock = burst(rng, 0.03, 0.0028, 350, 3200)
    place(out, nz(knock + 0.8 * peak_res(knock, 760 * p["f_scale"], 3.0)), 0.0, 0.5)
    toe = floor_hit(rng, 0.15, p["f_scale"] * 1.15, 0.7, 0.9, noise_lp=2200)
    place(out, toe, p["gap"], p["toe"])
    place(out, nz(burst(rng, 0.015, 0.0007, 2000, 6000)), p["gap"], p["toe"] * p["click"] * 0.6)
    out = add_room(rng, out, 0.16, 0.38, 2800)
    return crunch(out, 1.6)


def foot_stomp(rng):
    n = n_of(0.6)
    out = np.zeros(n)
    heel = floor_hit(rng, 0.4, 0.82, 2.0, 1.2, noise_lp=2000)
    place(out, heel, 0.0)
    place(out, thump(130, 62, 0.018, 0.06, 0.3), 0.0, 0.45)
    place(out, nz(burst(rng, 0.04, 0.004, 500, 3500)), 0.0, 0.55)
    place(out, nz(burst(rng, 0.03, 0.002, 1500, 6000)), 0.0, 0.3)
    # Boards rattle against the joists for a moment.
    rat = np.zeros(n_of(0.2))
    for k, tt in enumerate(np.arange(0.012, 0.15, 0.021)):
        place(rat, pulse(0.6), tt, rng.uniform(0.4, 1.0) * np.exp(-k / 3))
    place(out, nz(strike(rat, jitter_modes(rng, FLOOR_MODES[3:], f_scale=1.3), 0.2)), 0.0, 0.12)
    out = add_room(rng, out, 0.28, 0.5, 2600)
    return crunch(out, 2.4)


def foot_scuff(rng):
    n = n_of(0.36)
    t = t_axis(n)
    out = np.zeros(n)
    place(out, floor_hit(rng, 0.12, 1.05, 0.8, 1.8), 0.0, 0.8)
    rate = curve(n, [(0, 380), (0.3, 820), (0.7, 560), (1, 300)])
    env = curve(n, [(0, 0.5), (0.12, 1.0), (0.45, 0.7), (0.85, 0.15), (1, 0.0)])
    fr = stick_slip(rng, n, rate, env, 0.45)
    fr = bp(fr, 500, 3000, 3) + 0.35 * bp(white(rng, n) * env, 1200, 4000)
    place(out, nz(fr), 0.0, 0.42)
    # Leather squeak, short and wavering.
    sq_n = n_of(0.09)
    st = t_axis(sq_n)
    fsq = 1650 + 220 * np.sin(2 * np.pi * 23 * st) + rng.uniform(-80, 80)
    sq = np.sin(2 * np.pi * np.cumsum(fsq) / SR) * np.sin(np.pi * st / st[-1]) ** 2
    place(out, crunch(sq, 2.0), 0.11, 0.28)
    out = add_room(rng, out, 0.12, 0.35, 3000)
    return crunch(out, 1.5)


# ---------------------------------------------------------------------------
# Hands
# ---------------------------------------------------------------------------

CLAP_VARIANTS = {
    1: dict(cav=1120, q=4.5, tau=0.0072, flam=0.0032, flam_amp=0.45, low=0.1),
    2: dict(cav=1350, q=6.0, tau=0.0055, flam=None, flam_amp=0.0, low=0.08),
    3: dict(cav=980, q=3.5, tau=0.0085, flam=0.0058, flam_amp=0.32, low=0.12),
}


def clap_core(rng, cav, q, tau, flam, flam_amp, low, dur):
    n = n_of(dur)
    out = np.zeros(n)
    b = burst(rng, 0.08, tau, 600, 7500)
    b = b + 1.6 * peak_res(b, cav, q) + 0.6 * peak_res(b, cav * 2.1, q * 1.4)
    place(out, nz(b), 0.0)
    if flam is not None:
        b2 = burst(rng, 0.06, tau * 0.8, 800, 7000)
        place(out, nz(b2 + peak_res(b2, cav * 1.1, q)), flam, flam_amp)
    place(out, thump(260, 170, 0.006, 0.012, 0.05), 0.0, low)
    return out


def clap(rng, v):
    p = CLAP_VARIANTS[v]
    out = clap_core(rng, dur=0.12, **p)
    out = add_room(rng, out, 0.22, 0.42, 3600)
    return crunch(out, 1.7)


def clap_big(rng):
    out = clap_core(rng, cav=760, q=3.0, tau=0.014, flam=0.0025, flam_amp=0.6, low=0.4, dur=0.2)
    body = clap_core(rng, cav=1550, q=4.0, tau=0.009, flam=None, flam_amp=0, low=0, dur=0.2)
    out = nz(out) + 0.5 * nz(body)
    out = add_room(rng, out, 0.5, 0.75, 3400)
    return crunch(out, 2.3)


SLAP_VARIANTS = {
    1: dict(flesh=(780, 1250), low_f=170, crack=0.9, tau=0.0045, wet=3),
    2: dict(flesh=(620, 1020), low_f=145, crack=0.75, tau=0.006, wet=5),
    3: dict(flesh=(900, 1500), low_f=190, crack=1.0, tau=0.0035, wet=2),
}


def slap(rng, v):
    p = SLAP_VARIANTS[v]
    n = n_of(0.2)
    out = np.zeros(n)
    crack = burst(rng, 0.04, p["tau"], 1800, 7000)
    place(out, nz(crack), 0.0, p["crack"])
    fl = burst(rng, 0.1, p["tau"] * 2.2, 250, 5000)
    fl = fl + 1.5 * peak_res(fl, p["flesh"][0], 2.2) + 1.0 * peak_res(fl, p["flesh"][1], 2.5)
    place(out, nz(fl), 0.0, 1.0)
    place(out, thump(p["low_f"] * 1.4, p["low_f"], 0.008, 0.02, 0.1), 0.0, 0.4)
    # Wet skin micro-clicks right after contact.
    for _ in range(p["wet"]):
        place(out, nz(burst(rng, 0.006, 0.0006, 2500, 8000)), rng.uniform(0.004, 0.02), rng.uniform(0.1, 0.25))
    out = add_room(rng, out, 0.18, 0.4, 3200)
    return crunch(out, 2.0)


def fist_slam_stand(rng):
    n = n_of(0.75)
    out = np.zeros(n)
    exc = np.concatenate([pulse(2.5), np.zeros(10)])
    desk = strike(exc + 0.1 * burst(rng, len(exc) / SR, 0.002)[:len(exc)], jitter_modes(rng, DESK_MODES, 0.02, 0.1, 0.3), 0.6)
    place(out, desk, 0.0, 1.0)
    place(out, thump(130, 85, 0.01, 0.03, 0.12), 0.0, 0.4)
    # Paper on the desk: a flat slap plus a crackle.
    place(out, nz(burst(rng, 0.05, 0.008, 900, 6000)), 0.0, 0.45)
    cn = n_of(0.18)
    place(out, nz(crinkle(rng, cn, 900 * np.exp(-t_axis(cn) / 0.05))), 0.003, 0.25)
    # Loose desk clamp chatters on the shaft.
    rat = np.zeros(n_of(0.3))
    tt, k = 0.02, 0
    while tt < 0.26:
        place(rat, pulse(0.3), tt, np.exp(-tt / 0.07) * rng.uniform(0.6, 1.0))
        tt += rng.uniform(0.018, 0.03)
        k += 1
    place(out, strike(rat, [(1870, 0.02, 1.0), (3150, 0.012, 0.6), (4400, 0.008, 0.4)], 0.3), 0.0, 0.22)
    out = add_room(rng, out, 0.22, 0.45, 3200)
    return crunch(out, 2.2)


# ---------------------------------------------------------------------------
# Movement
# ---------------------------------------------------------------------------

def whoosh(rng, dur, fc_pts, env_pts, q, rumble=0.0, rumble_lp=320):
    n = n_of(dur)
    env = curve(n, env_pts)
    fc = log_curve(n, fc_pts)
    x = white(rng, n)
    y = nz(sweep_bp(sweep_bp(x, fc, q), fc, q)) + 0.3 * nz(sweep_bp(white(rng, n), np.minimum(fc * 1.9, SR / 6), q * 1.3))
    y = lp(y, 6000)
    if rumble > 0:
        y = nz(y) + rumble * nz(lp(white(rng, n), rumble_lp, 2))
    return nz(y) * env


def whoosh_small(rng):
    y = whoosh(rng, 0.26, [(0, 700), (0.3, 2200), (1, 900)],
               [(0, 0.35), (0.28, 1.0), (0.75, 0.25), (1, 0.0)], 1.6)
    return crunch(y, 1.3)


def whoosh_big(rng):
    y = whoosh(rng, 0.52, [(0, 330), (0.38, 1450), (1, 420)],
               [(0, 0.3), (0.4, 1.0), (0.7, 0.45), (1, 0.0)], 1.2, rumble=0.7)
    return crunch(y, 1.8)


def whoosh_spin(rng):
    n = n_of(0.62)
    t = t_axis(n)
    rot = curve(n, [(0, 19.0), (0.45, 17.5), (1, 14.5)])  # Doppler drop as it passes
    ph = 2 * np.pi * np.cumsum(rot) / SR
    am = (0.5 + 0.5 * np.cos(ph)) ** 2
    am = 0.18 + 0.82 * am
    fc = log_curve(n, [(0, 2600), (0.45, 1800), (1, 850)])
    y = nz(sweep_bp(sweep_bp(white(rng, n), fc, 2.2), fc, 2.2)) + 0.4 * nz(sweep_bp(white(rng, n), fc * 0.5, 3.0))
    y = lp(y, 6500)
    env = curve(n, [(0, 0.45), (0.45, 1.0), (0.8, 0.4), (1, 0.0)])
    return crunch(nz(y) * am * env, 1.6)


def cloth_rustle(rng, v):
    if v == 1:  # shirt: brighter, shorter
        dur, lo, hi, rate, pts = 0.45, 1300, 6500, 480, [(0, 0.7), (0.2, 1.0), (0.45, 0.25), (0.7, 0.8), (1, 0)]
    else:  # trousers: heavier fabric, lower and longer
        dur, lo, hi, rate, pts = 0.62, 600, 4200, 650, [(0, 0.5), (0.35, 1.0), (0.6, 0.35), (0.75, 0.8), (1, 0)]
    n = n_of(dur)
    env = curve(n, pts)
    # Slow random swell so it reads as fabric moving rather than steady hiss.
    slow = lp(np.abs(white(rng, n)), 9.0, 2)
    slow = np.clip(slow / np.max(slow), 0.0, 1.0) ** 1.5
    floor = lp(bp(white(rng, n), lo, hi), hi * 0.7) * env * slow
    ck = crinkle(rng, n, rate * env * (0.3 + slow), lo, hi)
    y = 0.35 * nz(floor) + nz(lp(ck, hi * 0.8))
    place(y, nz(burst(rng, 0.03, 0.006, lo, hi)), 0.0, 0.8)
    return crunch(y, 1.2)


def body_thud(rng):
    n = n_of(0.55)
    out = np.zeros(n)
    place(out, thump(120, 58, 0.02, 0.06, 0.35), 0.0, 0.55)
    place(out, floor_hit(rng, 0.35, 0.85, 1.8, 1.6, noise_lp=1400), 0.0, 1.0)
    place(out, nz(bp(burst(rng, 0.08, 0.012), 200, 1800)), 0.0, 0.5)
    place(out, cloth_rustle(rng, 2)[:n_of(0.3)] * np.exp(-t_axis(n_of(0.3)) / 0.08), 0.005, 0.12)
    out = add_room(rng, out, 0.25, 0.48, 2400)
    return crunch(out, 2.2)


# ---------------------------------------------------------------------------
# Props
# ---------------------------------------------------------------------------

STAND_TUBE = tube_modes(640, 0.25)


def stand_rattle(rng):
    n = n_of(1.0)
    out = np.zeros(n)
    times = [0.0, 0.048, 0.093, 0.152, 0.214, 0.29, 0.36]
    amps = [1.0, 0.55, 0.78, 0.42, 0.5, 0.28, 0.18]
    for tt, a in zip(times, amps):
        tt = tt if tt == 0 else tt + rng.uniform(-0.008, 0.008)
        h = metal_hit(rng, [(f, tau * 0.55, a_) for f, tau, a_ in DESK_MODES + STAND_TUBE], 0.6, 0.35)
        place(out, h, tt, a)
        place(out, nz(burst(rng, 0.01, 0.0008, 2500, 8000)), tt, 0.25 * a)
    out = add_room(rng, out, 0.18, 0.4, 3400)
    return crunch(out, 1.9)


def stand_crash(rng):
    n = n_of(1.6)
    out = np.zeros(n)
    # The desk edge strikes the floor first and hardest.
    place(out, metal_hit(rng, DESK_MODES + STAND_TUBE, 0.9, 0.5), 0.0, 1.0)
    place(out, floor_hit(rng, 0.3, 0.9, 1.6, 2.0), 0.0, 0.8)
    place(out, thump(120, 60, 0.015, 0.05, 0.2), 0.0, 0.5)
    # Base and shaft land, then the whole thing bounces and chatters.
    ts = bounce_times(rng, 0.13, 0.72, 8)
    for k, tt in enumerate(ts):
        a = 0.75 * (0.74 ** k)
        modes = DESK_MODES if k % 2 == 0 else STAND_TUBE + DESK_MODES[:4]
        place(out, metal_hit(rng, modes, 1.0, 0.35, 0.6), tt, a)
        place(out, floor_hit(rng, 0.12, 1.0, 1.0, 1.2), tt, 0.5 * a)
    # Small loose-part rattles between bounces.
    for _ in range(14):
        tt = rng.uniform(0.05, 1.0)
        place(out, metal_hit(rng, [(2200, 0.02, 1), (3400, 0.015, 0.7), (4700, 0.01, 0.5)], 0.06, 0.2), tt,
              0.12 * np.exp(-tt / 0.4))
    out = add_room(rng, out, 0.3, 0.55, 3000)
    return crunch(out, 2.3)


def paper_grab(rng):
    n = n_of(0.3)
    t = t_axis(n)
    out = np.zeros(n)
    place(out, nz(burst(rng, 0.03, 0.004, 900, 6500)), 0.0, 0.8)
    ck = crinkle(rng, n, 1400 * np.exp(-t / 0.07) + 60, 1200, 7500)
    place(out, nz(ck), 0.0, 1.0)
    sw = whoosh(rng, 0.2, [(0, 1500), (0.4, 3200), (1, 1800)], [(0, 0.6), (0.3, 1.0), (1, 0)], 1.5)
    place(out, sw, 0.0, 0.35)
    out = add_room(rng, out, 0.12, 0.35, 3800)
    return crunch(out, 1.6)


def paper_flutter(rng):
    n = n_of(0.8)
    t = t_axis(n)
    out = np.zeros(n)
    env = curve(n, [(0, 0.85), (0.15, 1.0), (0.6, 0.7), (0.9, 0.3), (1, 0.0)])
    # Irregular flaps from several sheets tumbling.
    tt = 0.0
    while tt < 0.76:
        fl = burst(rng, 0.05, rng.uniform(0.006, 0.014), 450, rng.uniform(2500, 5000))
        fl = fl + 0.8 * peak_res(fl, rng.uniform(700, 1600), 2.0)
        place(out, nz(fl), tt, rng.uniform(0.55, 1.0) * env[min(int(tt * SR), n - 1)])
        tt += 1.0 / rng.uniform(9.0, 16.0)
    air = lp(sweep_bp(white(rng, n), log_curve(n, [(0, 1500), (0.5, 2400), (1, 1100)]), 1.4), 4500)
    out += 0.14 * nz(air) * env
    out += 0.35 * nz(crinkle(rng, n, 250 * env, 1500, 7500))
    return crunch(out, 1.5)


def paper_land(rng):
    n = n_of(0.45)
    t = t_axis(n)
    out = np.zeros(n)
    for tt, a in ((0.0, 1.0), (0.028, 0.55), (0.071, 0.42), (0.158, 0.26)):
        s = burst(rng, 0.06, 0.0045, 300, 4500)
        s = s + 0.7 * peak_res(s, rng.uniform(800, 1300), 2.0)
        place(out, nz(s), tt, a)
        place(out, nz(lp(burst(rng, 0.05, 0.012), 380)), tt, 0.45 * a)
    out += 0.12 * nz(crinkle(rng, n, 180 * np.exp(-t / 0.15), 1500, 7000))
    out = add_room(rng, out, 0.15, 0.38, 3400)
    return crunch(out, 1.7)


def page_flip(rng):
    n = n_of(0.44)
    t = t_axis(n)
    out = np.zeros(n)
    snap = nz(burst(rng, 0.02, 0.0018, 1500, 7000)) + 0.6 * nz(crinkle(rng, n_of(0.02), 3000 * np.ones(n_of(0.02)), 2000, 8000))
    place(out, nz(snap), 0.0, 1.0)
    sw = whoosh(rng, 0.34, [(0, 1100), (0.4, 2600), (1, 1300)], [(0, 0.4), (0.45, 1.0), (1, 0.0)], 1.8)
    place(out, sw, 0.01, 0.55)
    soft = burst(rng, 0.05, 0.006, 400, 3500)
    place(out, nz(soft), 0.3, 0.35)
    out += 0.15 * nz(crinkle(rng, n, 160 * np.exp(-t / 0.2), 1500, 7000))
    out = add_room(rng, out, 0.1, 0.32, 3600)
    return crunch(out, 1.4)


CHAIR_TUBE = tube_modes(395, 0.32) + tube_modes(455, 0.28, 0.8)
CHAIR_SEAT = [(540, 0.12, 0.7), (870, 0.1, 0.55), (1310, 0.08, 0.45), (1960, 0.06, 0.3), (2780, 0.04, 0.2)]


def chair_grab(rng):
    n = n_of(0.6)
    out = np.zeros(n)
    held = [(f, tau * 0.45, a) for f, tau, a in CHAIR_TUBE + CHAIR_SEAT]  # the hand damps the frame
    place(out, metal_hit(rng, held, 0.5, 0.6), 0.0, 1.0)
    place(out, nz(lp(burst(rng, 0.04, 0.008), 600)), 0.0, 0.45)
    place(out, metal_hit(rng, [(f, tau * 0.7, a) for f, tau, a in CHAIR_TUBE], 0.45, 0.4), 0.034, 0.62)  # legs knock together
    for k in range(4):  # hinge chatter
        place(out, metal_hit(rng, [(2600, 0.015, 1), (3900, 0.01, 0.6)], 0.05, 0.2), 0.06 + 0.022 * k + rng.uniform(0, 0.006),
              0.18 * np.exp(-k / 2))
    out = add_room(rng, out, 0.2, 0.45, 3200)
    return crunch(out, 2.0)


def chair_impact(rng):
    n = n_of(1.6)
    out = np.zeros(n)
    place(out, metal_hit(rng, CHAIR_TUBE + CHAIR_SEAT, 0.9, 0.8), 0.0, 1.0)
    place(out, thump(140, 62, 0.012, 0.05, 0.2), 0.0, 0.75)
    place(out, nz(burst(rng, 0.03, 0.003, 1200, 7000)), 0.0, 0.35)
    for k, tt in enumerate(bounce_times(rng, 0.2, 0.68, 7)):
        a = 0.8 * (0.72 ** k)
        place(out, metal_hit(rng, CHAIR_TUBE + CHAIR_SEAT, 0.6, 0.45, 0.6), tt, a)
        place(out, floor_hit(rng, 0.15, 0.95, 1.2, 1.5), tt, 0.55 * a)
        for j in range(2):
            place(out, metal_hit(rng, [(2600, 0.015, 1), (3900, 0.01, 0.6)], 0.05, 0.2), tt + 0.015 + 0.02 * j, 0.15 * a)
    # Final short scrape as it settles.
    sn = n_of(0.18)
    scr = bp(stick_slip(rng, sn, np.full(sn, 520.0), np.linspace(1, 0, sn), 0.5), 1200, 4000, 3)
    place(out, nz(scr), 1.18, 0.035)
    out = add_room(rng, out, 0.32, 0.55, 3000)
    return crunch(out, 2.4)


SNARE_HEAD = [(1.0, 0.22, 1.0), (1.593, 0.16, 0.6), (2.136, 0.12, 0.5), (2.296, 0.11, 0.45),
              (2.653, 0.09, 0.35), (2.918, 0.08, 0.3), (3.156, 0.07, 0.22)]


def snare_head(f0, tau_scale, dur, bend=0.0, bend_tau=0.03):
    n = n_of(dur)
    t = t_axis(n)
    y = np.zeros(n)
    bendc = 1.0 + bend * np.exp(-t / bend_tau)
    for r, tau, a in SNARE_HEAD:
        ph = 2 * np.pi * np.cumsum(f0 * r * bendc) / SR
        y += a * np.exp(-t / (tau * tau_scale)) * np.sin(ph)
    if dur < 4.5 * SNARE_HEAD[0][1] * tau_scale:
        raise SfxError(f"snare head render {dur} s too short for its ring")
    return nz(tail_fade(y, 0.02))


def snare_wires(rng, drive_env, dur):
    """Snare wires buzz in proportion to head motion plus their own chatter."""
    n = n_of(dur)
    chatter = stick_slip(rng, n, np.full(n, 180.0), np.ones(n), 0.6)
    nse = white(rng, n) + 1.5 * chatter
    env = lp(np.abs(drive_env[:n]), 60.0, 1)
    return nz(tail_fade(bp(nse, 2200, 8500) * env, 0.05))


def drum_grab(rng):
    dur = 0.45
    n = n_of(dur)
    out = np.zeros(n)
    head = snare_head(195, 0.3, dur)
    head = signal.fftconvolve(lp(burst(rng, 0.01, 0.003), 800), head)[:n]
    place(out, nz(head), 0.0, 0.45)
    place(out, strike(pulse(1.2), [(690, 0.03, 1), (1120, 0.02, 0.6), (1760, 0.012, 0.4)], 0.1), 0.0, 0.8)
    place(out, nz(bp(burst(rng, 0.03, 0.006), 200, 1500)), 0.0, 0.4)
    wires = snare_wires(rng, head * np.exp(-t_axis(n) / 0.07), dur)
    place(out, wires, 0.0, 0.35)
    out = add_room(rng, out, 0.15, 0.4, 3200)
    return crunch(out, 1.7)


def drum_impact(rng):
    dur = 1.8
    n = n_of(dur)
    out = np.zeros(n)
    hits = [(0.0, 1.0)] + [(tt, 0.6 * 0.62 ** k) for k, tt in enumerate(bounce_times(rng, 0.22, 0.7, 5))]
    for k, (tt, a) in enumerate(hits):
        hd = snare_head(205 * rng.uniform(0.98, 1.02), 1.0 if k == 0 else 0.6, 1.0, bend=0.12 * a, bend_tau=0.04)
        hd = nz(signal.fftconvolve(pulse(1.2 if k == 0 else 2.0), hd)[:len(hd)])
        place(out, hd, tt, a)
        place(out, snare_wires(rng, hd, 1.0) * np.exp(-t_axis(n_of(1.0)) / 0.11), tt, 0.5 * a)
        place(out, strike(pulse(0.6), jitter_modes(rng, [(720, 0.04, 1), (1180, 0.03, 0.7), (1830, 0.02, 0.5), (2700, 0.012, 0.35)]), 0.12), tt, 0.45 * a)
        place(out, floor_hit(rng, 0.15, 0.9, 1.4, 2.5), tt, 0.4 * a)
    place(out, nz(burst(rng, 0.02, 0.0015, 1500, 7500)), 0.0, 0.4)
    out = add_room(rng, out, 0.3, 0.55, 3000)
    return crunch(out, 2.3)


def cymbal_partials(rng, count, lo, hi, tau_base, dur, tilt=-0.15, tau_exp=-0.3, beat=True):
    n = n_of(dur)
    t = t_axis(n)
    y = np.zeros(n)
    fs = np.exp(rng.uniform(np.log(lo), np.log(hi), count))
    for f in fs:
        a = (f / 1000.0) ** tilt * rng.uniform(0.3, 1.0)
        tau = tau_base * (f / 1000.0) ** tau_exp * rng.uniform(0.6, 1.25)
        ph = rng.uniform(0, 2 * np.pi)
        y += a * np.exp(-t / tau) * np.sin(2 * np.pi * f * t + ph)
        if beat:  # detuned twin for the shimmering beat
            y += 0.6 * a * np.exp(-t / tau) * np.sin(2 * np.pi * (f + rng.uniform(0.6, 3.5)) * t + ph)
    return nz(y)


def cymbal_grab(rng):
    dur = 0.5
    n = n_of(dur)
    t = t_axis(n)
    out = np.zeros(n)
    place(out, nz(burst(rng, 0.02, 0.0025, 900, 3500)), 0.0, 1.0)
    place(out, nz(lp(burst(rng, 0.03, 0.006), 500)), 0.0, 0.5)
    sh = cymbal_partials(rng, 60, 2500, 10000, 0.6, dur) * np.exp(-t / 0.12) * np.minimum(1, t / 0.004 + 0.2)
    place(out, sh, 0.0, 0.22)
    out = add_room(rng, out, 0.1, 0.35, 4000)
    return crunch(out, 1.3)


def cymbal_impact(rng):
    dur = 2.8
    n = n_of(dur)
    t = t_axis(n)
    ring = cymbal_partials(rng, 180, 1800, 10400, 1.05, dur, tilt=0.25, tau_exp=-0.2)
    ring = 0.8 * ring + 0.2 * cymbal_partials(rng, 30, 420, 1800, 0.8, dur)
    # Bright wash on top, decaying a little faster than the partials.
    wash = hp(white(rng, n), 4000) * np.exp(-t / 0.5)
    attack = burst(rng, 0.08, 0.012, 1800, 9500)
    bell = strike(pulse(0.4), [(452, 0.9, 1.0), (623, 0.7, 0.7), (889, 0.6, 0.6)], dur)
    y = 0.9 * ring + 0.3 * nz(wash)
    place(y, nz(attack), 0.0, 0.8)
    place(y, bell, 0.0, 0.12)
    place(y, floor_hit(rng, 0.12, 1.2, 0.8, 1.0), 0.0, 0.2)
    y = add_room(rng, y, 0.12, 0.6, 5000)[:n]
    return crunch(y, 1.6)


COWBELL_VARIANTS = {
    1: dict(f=(540.0, 800.0), taus=(0.24, 0.17), upper=0.25, width=0.25, drive=1.8),
    2: dict(f=(548.0, 812.0), taus=(0.15, 0.11), upper=0.42, width=0.18, drive=2.2),
}


def cowbell(rng, v):
    p = COWBELL_VARIANTS[v]
    f1, f2 = p["f"]
    u = p["upper"]
    modes = [(f1, p["taus"][0], 1.0), (f2, p["taus"][1], 0.85),
             (1372, 0.07, u), (1714, 0.055, u * 0.8), (2453, 0.04, u * 0.5), (3311, 0.03, u * 0.35)]
    n = n_of(1.0)
    out = np.zeros(n)
    exc = np.concatenate([pulse(p["width"]), np.zeros(4)])
    place(out, strike(exc, modes, 1.0), 0.0, 1.0)
    place(out, nz(burst(rng, 0.015, 0.0008, 2500, 8000)), 0.0, 0.35)  # stick tip click
    out = add_room(rng, out, 0.12, 0.38, 3800)
    return crunch(out, p["drive"])


# ---------------------------------------------------------------------------
# Small
# ---------------------------------------------------------------------------

def watch_tap(rng):
    n = n_of(0.1)
    out = np.zeros(n)
    place(out, nz(burst(rng, 0.006, 0.0005, 3000, 9000)), 0.0, 1.0)
    place(out, strike(pulse(0.15), [(2870, 0.012, 1.0), (4690, 0.008, 0.7), (6810, 0.005, 0.45), (1820, 0.015, 0.3)], 0.08), 0.0, 0.8)
    place(out, thump(320, 260, 0.005, 0.008, 0.03), 0.0, 0.12)
    out = add_room(rng, out, 0.06, 0.25, 4500)
    return crunch(out, 1.3)


def finger_snap(rng):
    n = n_of(0.16)
    out = np.zeros(n)
    b = burst(rng, 0.05, 0.003, 1100, 6000)
    b = b + 1.8 * peak_res(b, 2250, 5.0) + 0.6 * peak_res(b, 3600, 4.0)
    place(out, nz(b), 0.0, 1.0)
    place(out, thump(230, 180, 0.004, 0.008, 0.04), 0.003, 0.15)
    out = add_room(rng, out, 0.2, 0.4, 3600)
    return crunch(out, 1.8)


# ---------------------------------------------------------------------------
# Band
# ---------------------------------------------------------------------------

def creak(rng, dur, rate_pts, modes, env_pts, jitter=0.25):
    n = n_of(dur)
    rate = curve(n, rate_pts)
    env = curve(n, env_pts)
    exc = stick_slip(rng, n, rate, env, jitter)
    return strike(exc, modes, dur)


def chair_creak(rng):
    dur = 0.5
    n = n_of(dur)
    out = np.zeros(n)
    place(out, metal_hit(rng, [(1350, 0.03, 1), (2420, 0.02, 0.6), (3700, 0.012, 0.4)], 0.08, 0.3), 0.0, 0.7)
    cr = creak(rng, 0.46, [(0, 95), (0.4, 165), (1, 70)],
               [(520, 0.02, 1.0), (1150, 0.015, 0.7), (1890, 0.01, 0.5), (2810, 0.007, 0.3)],
               [(0, 0.5), (0.3, 1.0), (0.8, 0.6), (1, 0)])
    place(out, cr, 0.0, 0.75)
    sq = lp(white(rng, n), 1400) * curve(n, [(0, 0.3), (0.3, 1.0), (1, 0)])
    place(out, nz(sq), 0.0, 0.25)
    out = add_room(rng, out, 0.12, 0.38, 3200)
    return crunch(out, 1.6)


def instrument_handle(rng):
    n = n_of(0.3)
    out = np.zeros(n)
    exc = lp(burst(rng, 0.012, 0.004), 900) + 0.5 * np.pad(pulse(4.0), (0, n_of(0.012) - len(pulse(4.0))))
    place(out, strike(exc, [(212, 0.04, 0.8), (365, 0.035, 0.9), (640, 0.025, 0.7), (1050, 0.018, 0.45)], 0.25), 0.0, 1.0)
    place(out, metal_hit(rng, [(1830, 0.025, 1.0), (3120, 0.018, 0.6), (4410, 0.012, 0.4)], 0.08, 0.4), 0.006, 0.3)
    out = add_room(rng, out, 0.14, 0.38, 3000)
    return crunch(out, 1.5)


def key_clack(rng, pitch=1.0):
    n = n_of(0.08)
    out = np.zeros(n)
    place(out, thump(620 * pitch, 470 * pitch, 0.003, 0.009, 0.06), 0.0, 1.0)
    place(out, nz(burst(rng, 0.008, 0.0012, 2200, 5500)), 0.0, 0.4)
    place(out, metal_hit(rng, [(3300 * pitch, 0.015, 1.0), (4800 * pitch, 0.01, 0.5)], 0.05, 0.2), 0.0, 0.18)
    return out


def sax_keys(rng):
    n = n_of(0.45)
    out = np.zeros(n)
    for tt, a in zip((0.0, 0.058, 0.112, 0.188, 0.243), (1.0, 0.7, 0.85, 0.6, 0.75)):
        tt = tt if tt == 0 else tt + rng.uniform(-0.006, 0.006)
        place(out, key_clack(rng, rng.uniform(0.8, 1.25)), tt, a)
    out = add_room(rng, out, 0.12, 0.36, 3200)
    return crunch(out, 1.5)


def valve(rng, pitch):
    n = n_of(0.12)
    out = np.zeros(n)
    place(out, thump(850 * pitch, 700 * pitch, 0.003, 0.007, 0.05), 0.0, 1.0)
    place(out, metal_hit(rng, [(2620 * pitch, 0.012, 1), (3940 * pitch, 0.008, 0.5)], 0.05, 0.25), 0.0, 0.3)
    place(out, nz(lp(burst(rng, 0.03, 0.01), 900)), 0.0, 0.15)  # piston air
    rel = rng.uniform(0.045, 0.06)  # spring return, felt stopped
    place(out, thump(760 * pitch, 640 * pitch, 0.003, 0.006, 0.04), rel, 0.42)
    place(out, metal_hit(rng, [(2620 * pitch, 0.01, 1)], 0.03, 0.25), rel, 0.12)
    return out


def trumpet_valves(rng):
    n = n_of(0.46)
    out = np.zeros(n)
    for k, tt in enumerate((0.0, 0.108, 0.221)):
        place(out, valve(rng, (1.0, 0.94, 1.07)[k]), tt, (1.0, 0.85, 0.92)[k])
    out = add_room(rng, out, 0.12, 0.36, 3200)
    return crunch(out, 1.5)


def trombone_slide(rng):
    dur = 0.33
    n = n_of(dur)
    env = curve(n, [(0, 0.6), (0.3, 1.0), (0.74, 0.55), (0.8, 0.12), (1, 0.0)])
    fc = log_curve(n, [(0, 950), (0.8, 1700), (1, 1750)])
    fr = nz(lp(sweep_bp(sweep_bp(white(rng, n), fc, 2.2), fc, 2.2), 3500))
    tone = nz(sweep_bp(white(rng, n), fc * 0.42, 9.0))  # tube resonance moving with slide length
    out = (fr + 0.45 * tone) * env
    place(out, nz(burst(rng, 0.01, 0.0015, 2000, 7000)), 0.0, 0.5)
    place(out, metal_hit(rng, [(1420, 0.012, 1), (2290, 0.009, 0.6)], 0.04, 0.4), 0.262, 0.35)  # hits the stop
    return crunch(out, 1.5)


def spit_valve(rng):
    n = n_of(1.05)
    t = t_axis(n)
    out = np.zeros(n)
    place(out, metal_hit(rng, [(2410, 0.012, 1), (3880, 0.008, 0.6)], 0.05, 0.2), 0.0, 0.9)
    pn = n_of(0.3)
    pt = t_axis(pn)
    penv = np.minimum(1, pt / 0.006 + 0.5) * np.exp(-pt / 0.07)
    gurgle = 0.6 + 0.4 * np.sign(np.sin(2 * np.pi * np.cumsum(rng.uniform(35, 60, pn)) / SR))
    pf = bp(white(rng, pn), 700, 5000) * penv * lp(gurgle, 200, 1)
    place(out, nz(pf), 0.0, 1.0)
    for tt in (0.45, 0.61, 0.79, 0.93):
        dn = n_of(0.03)
        dt = t_axis(dn)
        f = 900 + 1400 * dt / 0.012
        f = np.minimum(f, 2600) * rng.uniform(0.85, 1.15)
        drop = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-dt / 0.008)
        place(out, drop, tt + rng.uniform(-0.02, 0.02), rng.uniform(0.18, 0.3))
    out = add_room(rng, out, 0.1, 0.32, 3400)
    return crunch(out, 1.4)


def guitar_handle(rng):
    dur = 1.1
    n = n_of(dur)
    t = t_axis(n)
    out = np.zeros(n)
    exc = lp(burst(rng, 0.015, 0.005), 600)
    place(out, strike(exc, [(98, 0.07, 1.0), (196, 0.05, 0.75), (243, 0.04, 0.6), (412, 0.03, 0.4)], 0.4), 0.0, 1.0)
    for k, f in enumerate((82.41, 110.0, 146.83, 196.0, 246.94, 329.63)):
        s = karplus(rng, f, dur - 0.1, t60=1.1, bright=0.4)
        place(out, s, 0.012 + 0.011 * k + rng.uniform(0, 0.003), 0.16)
    out = add_room(rng, out, 0.12, 0.4, 3000)[:n]
    return crunch(out, 1.6)


def bass_handle(rng):
    dur = 1.15
    n = n_of(dur)
    out = np.zeros(n)
    exc = lp(burst(rng, 0.025, 0.008), 400)
    place(out, strike(exc, [(64, 0.1, 1.0), (108, 0.08, 0.8), (161, 0.06, 0.6), (232, 0.04, 0.4)], 0.5), 0.0, 1.0)
    st = (karplus(rng, 41.2, dur - 0.03, t60=0.75, bright=0.2, pluck_noise_lp=1500)
          + 0.7 * karplus(rng, 55.0, dur - 0.03, t60=0.65, bright=0.2, pluck_noise_lp=1500))
    st = nz(st)
    th = 0.35
    buzz = hp(np.sign(st) * np.maximum(np.abs(st) - th, 0.0), 350)  # string slapping the fingerboard
    buzz = lp(buzz, 4000) * np.exp(-t_axis(len(buzz)) / 0.15)
    place(out, st, 0.02, 0.45)
    place(out, nz(buzz), 0.02, 0.25)
    out = add_room(rng, out, 0.14, 0.42, 2600)[:n]
    return crunch(out, 1.8)


def piano_bench_creak(rng):
    dur = 0.58
    n = n_of(dur)
    out = np.zeros(n)
    place(out, floor_hit(rng, 0.12, 1.4, 0.8, 3.0, noise_lp=900), 0.0, 0.8)
    cr = creak(rng, 0.55, [(0, 45), (0.35, 92), (1, 52)],
               [(232, 0.035, 1.0), (468, 0.025, 0.8), (821, 0.018, 0.55), (1347, 0.012, 0.35), (2080, 0.007, 0.2)],
               [(0, 0.55), (0.35, 1.0), (0.85, 0.5), (1, 0)], jitter=0.35)
    place(out, cr, 0.0, 0.9)
    out = add_room(rng, out, 0.14, 0.4, 2800)
    return crunch(out, 1.7)


# ---------------------------------------------------------------------------
# Room
# ---------------------------------------------------------------------------

def door_slam(rng):
    n = n_of(1.0)
    out = np.zeros(n)
    door = [(62, 0.12, 0.6), (98, 0.1, 0.75), (147, 0.09, 0.85), (214, 0.07, 0.9),
            (338, 0.05, 0.8), (529, 0.035, 0.6), (812, 0.022, 0.4), (1230, 0.014, 0.25)]
    exc = lp(burst(rng, 0.015, 0.004), 1200) + np.pad(pulse(3.0), (0, n_of(0.015) - len(pulse(3.0))))
    place(out, strike(exc, jitter_modes(rng, door, 0.03, 0.1, 0.2), 0.7), 0.0, 1.0)
    place(out, thump(95, 52, 0.025, 0.08, 0.5), 0.0, 0.5)
    place(out, nz(bp(burst(rng, 0.06, 0.01), 250, 2500)), 0.0, 0.6)  # slab of wood crack
    place(out, metal_hit(rng, [(1910, 0.022, 1.0), (3120, 0.016, 0.7), (4330, 0.01, 0.5)], 0.1, 0.25), 0.004, 0.4)  # latch
    place(out, nz(burst(rng, 0.02, 0.002, 1500, 6000)), 0.0, 0.25)
    rat = np.zeros(n_of(0.35))
    tt = 0.03
    while tt < 0.3:  # frame and panels chatter
        place(rat, pulse(0.8), tt, np.exp(-tt / 0.09) * rng.uniform(0.5, 1.0))
        tt += rng.uniform(0.03, 0.05)
    place(out, strike(rat, [(332, 0.03, 1), (521, 0.025, 0.8), (806, 0.02, 0.6), (1240, 0.012, 0.4)], 0.35), 0.0, 0.25)
    out = add_room(rng, out, 0.45, 0.75, 2500)
    return crunch(out, 2.5)


# ---------------------------------------------------------------------------
# Registry. Names are a contract shared with the cue tables and Godot.
# (name, generator, category, description, peak_dbfs, (min_s, max_s), keep_full_length)
# ---------------------------------------------------------------------------

def _v(fn, v):
    return lambda rng: fn(rng, v)


SOUNDS = [
    ("foot_step_1", _v(foot_step, 1), "footsteps", "Dress shoe heel and toe on hardwood, variant 1 (mid board resonance).", -3.0, (0.1, 0.6), False),
    ("foot_step_2", _v(foot_step, 2), "footsteps", "Dress shoe step on hardwood, variant 2 (lower boards, longer roll, clickier heel).", -3.0, (0.1, 0.6), False),
    ("foot_step_3", _v(foot_step, 3), "footsteps", "Dress shoe step on hardwood, variant 3 (tighter boards, quick heavy toe).", -3.0, (0.1, 0.6), False),
    ("foot_step_4", _v(foot_step, 4), "footsteps", "Dress shoe step on hardwood, variant 4 (slow heel to toe roll, light toe).", -3.0, (0.1, 0.6), False),
    ("foot_stomp", foot_stomp, "footsteps", "Heavy aggressive stomp on hardwood with board rattle and room.", -3.0, (0.3, 1.0), False),
    ("foot_scuff", foot_scuff, "footsteps", "Shoe twist and drag on hardwood with a short leather squeak.", -3.0, (0.2, 0.7), False),
    ("clap_1", _v(clap, 1), "hands", "Single sharp hand clap, mid cavity with a tiny flam.", -3.0, (0.1, 0.6), False),
    ("clap_2", _v(clap, 2), "hands", "Single sharp hand clap, brighter and tighter, no flam.", -3.0, (0.1, 0.6), False),
    ("clap_3", _v(clap, 3), "hands", "Single sharp hand clap, lower and fuller, wider flam.", -3.0, (0.1, 0.6), False),
    ("clap_big", clap_big, "hands", "One huge cupped stopping clap with a big room bloom.", -3.0, (0.3, 1.2), False),
    ("slap_1", _v(slap, 1), "hands", "Open-hand face slap, meaty skin smack, variant 1.", -3.0, (0.1, 0.6), False),
    ("slap_2", _v(slap, 2), "hands", "Open-hand face slap, heavier and fleshier, variant 2.", -3.0, (0.1, 0.6), False),
    ("slap_3", _v(slap, 3), "hands", "Open-hand face slap, sharper crack, variant 3.", -3.0, (0.1, 0.6), False),
    ("fist_slam_stand", fist_slam_stand, "hands", "Fist hammering a steel music stand desk with sheet music, desk ring and clamp chatter.", -3.0, (0.4, 1.2), False),
    ("whoosh_small", whoosh_small, "movement", "Quick arm flick whoosh.", -3.0, (0.2, 0.35), True),
    ("whoosh_big", whoosh_big, "movement", "Violent full-arm swing whoosh with low air rumble.", -3.0, (0.4, 0.7), True),
    ("whoosh_spin", whoosh_spin, "movement", "Spinning flat object flying past, pulsing whirr with a Doppler drop, about 0.6 s.", -3.0, (0.55, 0.7), True),
    ("cloth_rustle_1", _v(cloth_rustle, 1), "movement", "Shirt movement rustle, quiet by design (peak -12 dBFS).", -12.0, (0.3, 0.6), True),
    ("cloth_rustle_2", _v(cloth_rustle, 2), "movement", "Trousers movement rustle, heavier and lower, quiet by design (peak -12 dBFS).", -12.0, (0.4, 0.8), True),
    ("body_thud", body_thud, "movement", "Heavy weight shift or lunge landing on the wooden floor.", -3.0, (0.3, 1.0), False),
    ("stand_rattle", stand_rattle, "props", "Metal music stand jostled, a burst of small clanks.", -3.0, (0.4, 1.4), False),
    ("stand_crash", stand_crash, "props", "Metal music stand toppling and clattering on the wood floor.", -3.0, (1.0, 2.2), False),
    ("paper_grab", paper_grab, "props", "Snatching sheet music, crackle and swish.", -3.0, (0.2, 0.6), False),
    ("paper_flutter", paper_flutter, "props", "Pages flying through the air, irregular flaps, about 0.8 s.", -3.0, (0.75, 0.85), True),
    ("paper_land", paper_land, "props", "Pages slapping onto the floor one after another.", -3.0, (0.25, 0.8), False),
    ("page_flip", page_flip, "props", "Turning one page on a stand, quieter by design (peak -6 dBFS).", -6.0, (0.3, 0.8), False),
    ("chair_grab", chair_grab, "props", "Grabbing a folded metal chair, tube clank and hinge chatter.", -3.0, (0.4, 1.0), False),
    ("chair_impact", chair_impact, "props", "Folded metal chair slamming into something and clattering on the floor.", -3.0, (1.0, 2.2), False),
    ("drum_grab", drum_grab, "props", "Picking up a snare drum, shell knock and a slight snare buzz.", -3.0, (0.3, 0.9), False),
    ("drum_impact", drum_impact, "props", "Snare drum crashing to the floor and bouncing, snares rattling.", -3.0, (0.9, 2.0), False),
    ("cymbal_grab", cymbal_grab, "props", "Gripping a crash cymbal edge, finger tick and a faint choked shimmer.", -3.0, (0.3, 0.9), False),
    ("cymbal_impact", cymbal_impact, "props", "Crash cymbal hitting and ringing, bright shimmering tail of about 2.8 s.", -3.0, (2.0, 3.0), True),
    ("cowbell_hit_1", _v(cowbell, 1), "props", "Hard stick hit on a cowbell, partials near 540 and 800 Hz, open ring.", -3.0, (0.3, 1.2), False),
    ("cowbell_hit_2", _v(cowbell, 2), "props", "Hard stick hit on a cowbell nearer the mouth, brighter and shorter.", -3.0, (0.3, 1.2), False),
    ("watch_tap", watch_tap, "small", "Fingernail tapping a watch face, quieter by design (peak -9 dBFS).", -9.0, (0.05, 0.4), False),
    ("finger_snap", finger_snap, "small", "Finger snap, bright crack with a small palm thump.", -3.0, (0.1, 0.6), False),
    ("chair_creak", chair_creak, "band", "Player shifting on a padded metal chair, vinyl and joint creak, quieter by design (peak -6 dBFS).", -6.0, (0.3, 0.9), False),
    ("instrument_handle", instrument_handle, "band", "Soft clunk of picking up or setting down an instrument.", -3.0, (0.2, 0.7), False),
    ("sax_keys", sax_keys, "band", "Saxophone key pads clacking, quieter by design (peak -6 dBFS).", -6.0, (0.3, 0.8), False),
    ("trumpet_valves", trumpet_valves, "band", "Three trumpet valves pressed and released, quieter by design (peak -6 dBFS).", -6.0, (0.3, 0.8), False),
    ("trombone_slide", trombone_slide, "band", "Trombone slide swish ending on the stop, about 0.3 s.", -3.0, (0.28, 0.36), True),
    ("spit_valve", spit_valve, "band", "Trombone water key click, a pfft and a few drips.", -3.0, (0.8, 1.3), False),
    ("guitar_handle", guitar_handle, "band", "Guitar body thump with open strings brushed.", -3.0, (0.4, 1.4), False),
    ("bass_handle", bass_handle, "band", "Upright bass body thump with a low string buzz.", -3.0, (0.4, 1.5), False),
    ("piano_bench_creak", piano_bench_creak, "band", "Wooden piano bench creak with a small leg knock, quieter by design (peak -6 dBFS).", -6.0, (0.3, 0.9), False),
    ("door_slam", door_slam, "room", "Heavy wooden door slammed shut in a wood-panelled room, latch click and frame rattle.", -3.0, (0.7, 1.8), False),
]


# ---------------------------------------------------------------------------
# Finishing and validation
# ---------------------------------------------------------------------------

def finish(name, x, peak_dbfs, keep_full):
    x = np.asarray(x, dtype=float)
    if x.ndim != 1 or len(x) < 16:
        raise SfxError(f"{name}: generator returned an invalid buffer")
    if not np.all(np.isfinite(x)):
        raise SfxError(f"{name}: non-finite samples")
    x = hp(x, 30.0, 2)
    pk = np.max(np.abs(x))
    if pk < 1e-9:
        raise SfxError(f"{name}: silent after filtering")
    # Leading silence: start exactly at the first sample above -34 dB of peak.
    start = int(np.argmax(np.abs(x) > pk * 0.02))
    x = x[start:]
    if not keep_full:
        above = np.nonzero(np.abs(x) > pk * 0.00316)[0]  # -50 dB
        x = x[:min(len(x), above[-1] + n_of(0.01))]
    n = len(x)
    fi = n_of(FADE_IN_S)
    fo = min(n_of(0.04), max(n_of(0.005), n // 6))
    if keep_full and n > n_of(1.0):
        fo = n_of(0.3)
    win = np.ones(n)
    win[:fi] = 0.5 - 0.5 * np.cos(np.pi * np.arange(fi) / fi)
    win[n - fo:] = 0.5 + 0.5 * np.cos(np.pi * np.arange(fo) / fo)
    x = x * win
    # DC removal shaped by a Hann window so the endpoints stay at zero.
    h = np.hanning(n)
    x = x - h * (np.mean(x) / np.mean(h))
    x = x * (10 ** (peak_dbfs / 20.0) / np.max(np.abs(x)))
    return x


def to_int16(x):
    q = np.round(x * 32767.0)
    if np.max(np.abs(q)) >= 32767:
        raise SfxError("sample reached full scale")
    return q.astype(np.int16)


def analyse(x):
    n = len(x)
    pk = np.max(np.abs(x))
    rms = np.sqrt(np.mean(x ** 2))
    spec = np.abs(np.fft.rfft(x * np.hanning(n)))
    fr = np.fft.rfftfreq(n, 1.0 / SR)
    power = spec ** 2
    cent = float(np.sum(fr * power) / np.sum(power))
    low = float(np.sum(power[fr < 500]) / np.sum(power))
    high = float(np.sum(power[fr > 3000]) / np.sum(power))
    i_pk = int(np.argmax(np.abs(x)))
    onset = float(np.max(np.abs(x[:n_of(0.015)])) / pk)
    return dict(seconds=n / SR, peak_dbfs=20 * np.log10(pk), rms_dbfs=20 * np.log10(rms),
                crest_db=20 * np.log10(pk / rms), centroid_hz=cent, low_frac=low, high_frac=high,
                t_peak_ms=1000 * i_pk / SR, onset_rel=onset, dc=float(np.mean(x)))


def validate(name, x, q, a, peak_dbfs, dur_range):
    if abs(a["peak_dbfs"] - peak_dbfs) > 0.05:
        raise SfxError(f"{name}: peak {a['peak_dbfs']:.2f} dBFS, expected {peak_dbfs}")
    if a["rms_dbfs"] < peak_dbfs - 40:
        raise SfxError(f"{name}: nearly silent, RMS {a['rms_dbfs']:.1f} dBFS")
    if a["crest_db"] < 6.0:
        raise SfxError(f"{name}: crest factor {a['crest_db']:.1f} dB, too squashed")
    if abs(a["dc"]) > 1e-4:
        raise SfxError(f"{name}: DC offset {a['dc']:.2e}")
    if a["onset_rel"] < 0.06:
        raise SfxError(f"{name}: weak onset, first 15 ms peaks at {20 * np.log10(a['onset_rel']):.1f} dB of peak")
    if not (dur_range[0] <= a["seconds"] <= dur_range[1]):
        raise SfxError(f"{name}: duration {a['seconds']:.3f} s outside {dur_range}")
    if q[0] != 0 or abs(int(q[-1])) > 2:
        raise SfxError(f"{name}: endpoints not faded to zero")


def spectral_peaks(x, lo, hi, dur=0.3):
    seg = x[:n_of(dur)]
    spec = np.abs(np.fft.rfft(seg * np.hanning(len(seg)), 8 * len(seg)))
    fr = np.fft.rfftfreq(8 * len(seg), 1.0 / SR)
    m = (fr >= lo) & (fr <= hi)
    i = np.argmax(spec[m])
    return float(fr[m][i]), float(spec[m][i])


def check_content(results):
    """Sanity checks on character so a regression fails loudly."""
    def need(cond, msg):
        if not cond:
            raise SfxError(msg)

    for name in ("cowbell_hit_1", "cowbell_hit_2"):
        x = results[name]["x"]
        f1, a1 = spectral_peaks(x, 450, 650)
        f2, a2 = spectral_peaks(x, 700, 900)
        _, a_other = spectral_peaks(x, 950, 5000)
        results[name]["partials"] = (f1, f2)
        need(abs(f1 - 540) < 25 and abs(f2 - 800) < 30, f"{name}: partials at {f1:.0f} and {f2:.0f} Hz")
        need(a_other < min(a1, a2), f"{name}: an upper partial dominates the 540/800 pair")
    cy = results["cymbal_impact"]
    x = cy["x"]
    seg = lambda a, b: np.sqrt(np.mean(x[int(a * SR):int(b * SR)] ** 2))
    d1 = 20 * np.log10(seg(1.0, 1.2) / seg(0.0, 0.2))
    d2 = 20 * np.log10(seg(2.0, 2.2) / seg(0.0, 0.2))
    cy["decay_db"] = (d1, d2)
    need(-24 < d1 < -4 and -45 < d2 < -12, f"cymbal_impact decay {d1:.1f} dB at 1 s, {d2:.1f} dB at 2 s")
    need(cy["a"]["centroid_hz"] > 3000, "cymbal_impact not bright enough")
    for k in range(1, 5):
        a = results[f"foot_step_{k}"]["a"]
        need(300 < a["centroid_hz"] < 1500 and a["low_frac"] > 0.25, f"foot_step_{k} not low and woody")
    for k in range(1, 4):
        a = results[f"clap_{k}"]["a"]
        need(a["centroid_hz"] > 1200 and a["low_frac"] < 0.2, f"clap_{k} not bright")
    for name in ("door_slam", "foot_stomp", "body_thud", "bass_handle"):
        need(results[name]["a"]["centroid_hz"] < 1200, f"{name} not low enough")
    # Variants must differ in content, not just gain.
    groups = [[f"foot_step_{k}" for k in range(1, 5)], ["clap_1", "clap_2", "clap_3"],
              ["slap_1", "slap_2", "slap_3"], ["cloth_rustle_1", "cloth_rustle_2"], ["cowbell_hit_1", "cowbell_hit_2"]]
    for g in groups:
        for i in range(len(g)):
            for j in range(i + 1, len(g)):
                a, b = results[g[i]]["x"], results[g[j]]["x"]
                m = min(len(a), len(b))
                c = np.corrcoef(a[:m], b[:m])[0, 1]
                need(abs(c) < 0.9, f"{g[i]} and {g[j]} nearly identical (corr {c:.2f})")


def write_wav(path, q):
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(q.tobytes())


README_HEAD = """# Not Quite My Tempo sound effects

Procedurally synthesized by `../gen_sfx.py`. No recorded or downloaded audio is used.
Every random generator is seeded from the sound name, so output is bit-identical on every run.

Format is mono 16-bit PCM WAV at 22050 Hz. Each file starts on its transient at t=0
(4 ms fade-in), ends with a fade-out, has no DC offset and peaks at -3 dBFS unless
marked quieter by design below. `sfx_manifest.json` lists duration, peak, category and
description for every file.

## Regenerate

```
cd ~/not-quite-my-tempo-assets
python3 gen_sfx.py
```

The script prints an analysis table (peak, RMS, crest, spectral centroid, onset) and
raises if any sound is silent, clipped, mistimed, off its duration range or loses its
expected character (cowbell partials, cymbal decay, low footsteps, bright claps).

## Sounds

| name | seconds | peak dBFS | description |
|---|---|---|---|
"""


def main():
    names = [s[0] for s in SOUNDS]
    if len(names) != len(set(names)):
        raise SfxError("duplicate sound names in registry")
    os.makedirs(OUT_DIR, exist_ok=True)
    results = {}
    for name, fn, cat, desc, pk_db, dur_range, keep_full in SOUNDS:
        rng = np.random.default_rng(zlib.crc32(name.encode()))
        try:
            raw = fn(rng)
        except SfxError as e:
            raise SfxError(f"{name}: {e}") from e
        x = finish(name, raw, pk_db, keep_full)
        q = to_int16(x)
        xq = q.astype(float) / 32767.0
        a = analyse(xq)
        validate(name, xq, q, a, pk_db, dur_range)
        results[name] = dict(x=xq, q=q, a=a, cat=cat, desc=desc, pk=pk_db)
    print(f"{'name':20s} {'sec':>6s} {'peak':>6s} {'rms':>6s} {'crest':>6s} {'cent':>6s} {'<500':>5s} {'>3k':>5s} {'tpk':>6s} {'onset':>6s}")
    for name, r in results.items():
        a = r["a"]
        print(f"{name:20s} {a['seconds']:6.3f} {a['peak_dbfs']:6.1f} {a['rms_dbfs']:6.1f} {a['crest_db']:6.1f} "
              f"{a['centroid_hz']:6.0f} {a['low_frac']:5.2f} {a['high_frac']:5.2f} {a['t_peak_ms']:6.1f} "
              f"{20 * np.log10(a['onset_rel']):6.1f}")
    check_content(results)

    manifest = {"sample_rate": SR, "sounds": {}}
    for name, r in results.items():
        write_wav(os.path.join(OUT_DIR, f"{name}.wav"), r["q"])
        manifest["sounds"][name] = {"file": f"{name}.wav", "seconds": round(r["a"]["seconds"], 4),
                                    "peak_dbfs": round(r["a"]["peak_dbfs"], 2), "category": r["cat"],
                                    "description": r["desc"]}
    with open(os.path.join(OUT_DIR, "sfx_manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2)
        f.write("\n")
    with open(os.path.join(OUT_DIR, "README.md"), "w") as f:
        f.write(README_HEAD)
        for name, r in results.items():
            f.write(f"| `{name}` | {r['a']['seconds']:.3f} | {r['a']['peak_dbfs']:.1f} | {r['desc']} |\n")

    for name in ("cowbell_hit_1", "cowbell_hit_2"):
        f1, f2 = results[name]["partials"]
        print(f"{name} partials {f1:.0f} Hz and {f2:.0f} Hz")
    d1, d2 = results["cymbal_impact"]["decay_db"]
    print(f"cymbal_impact decay {d1:.1f} dB at 1 s, {d2:.1f} dB at 2 s")
    total = sum(os.path.getsize(os.path.join(OUT_DIR, f)) for f in os.listdir(OUT_DIR))
    print(f"{len(results)} sounds, folder {total / 1e6:.2f} MB")


if __name__ == "__main__":
    try:
        main()
    except SfxError as e:
        print(f"gen_sfx FAILED: {e}", file=sys.stderr)
        raise
