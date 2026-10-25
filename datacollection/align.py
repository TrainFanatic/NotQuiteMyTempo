"""Map ref.wav time onto the Quest take's time: quest_t = a * ref_t + b.

  python datacollection/align.py --session quest_X --take datacollection/takes/NAME [-o align.json]
  python datacollection/align.py REF.wav QUEST.wav            # any two recordings of one event

1. Coarse: onset-strength envelopes (log-band spectral flux, 100 frames/s) of both whole files,
   FFT cross-correlation over every lag -> b0 (the Quest is usually started seconds earlier).
2. Fine: N windows (default 10, 6 s each) spread over the ref's drum region; per window
   GCC-PHAT on 16 kHz audio, 150-6000 Hz (both mics hear that band), searched +-30 ms around
   the prediction, parabolic sub-sample peak. A second pass re-centres on the first fit.
3. Robust least squares quest = a * ref + b (drops windows > max(0.6 ms, 3 sigma_MAD) off). Fails loudly
   (SystemExit) when fewer than 6 windows survive, the rms residual exceeds 1 ms, or |a-1| > 300 ppm.

With --take, the guide's clicks are first subtracted from ref.wav (dc_common.remove_clicks): the
guide speaker sits at a different distance from the headset than the kit does, so aligning on
the click would carry that path difference into every drum label.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from scipy import signal

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dc_common as dc  # noqa: E402

FS = 16000


def onset_envelope(x, sr, hop_s=0.010):
    n_fft = 1 << int(np.ceil(np.log2(0.046 * sr)))
    hop = int(hop_s * sr)
    f, _, Z = signal.stft(x, sr, nperseg=n_fft, noverlap=n_fft - hop, boundary=None, padded=False)
    P = np.abs(Z) ** 2
    edges = np.geomspace(150, min(6000, sr / 2 - 1), 17)
    L = np.log10(np.stack([P[(f >= lo) & (f < hi)].sum(0) + 1e-10 for lo, hi in zip(edges[:-1], edges[1:])]))
    d = np.maximum(0, np.diff(L, axis=1)).sum(0)
    d -= ndmedian(d, int(1.0 / hop_s))
    return np.maximum(d, 0), hop / sr, (n_fft / 2) / sr


def ndmedian(v, n):
    from scipy.ndimage import median_filter
    return median_filter(v, size=n | 1, mode="nearest")


def coarse_offset(r, q, sr):
    er, hop, _ = onset_envelope(r, sr)
    eq, _, _ = onset_envelope(q, sr)
    n = 1 << int(np.ceil(np.log2(len(er) + len(eq))))
    c = np.fft.irfft(np.fft.rfft(eq, n) * np.conj(np.fft.rfft(er, n)), n)
    lags = np.r_[np.arange(0, len(eq)), np.arange(-len(er) + 1, 0)]
    vals = np.r_[c[:len(eq)], c[n - len(er) + 1:]]
    k = int(np.argmax(vals))
    srt = np.sort(vals)
    return lags[k] * hop, float(vals[k] / (np.median(np.abs(vals)) + 1e-12))


def gcc_phat(r, q, sr, max_lag, center, band=(150, 6000)):
    """Lag d (s) with q(t + d) ~ r(t), searched in [center - max_lag, center + max_lag], where q
    starts `center` seconds... (q is already cut so that lag 0 means q[i] ~ r[i])."""
    n = 1 << int(np.ceil(np.log2(len(r) + len(q))))
    R, Q = np.fft.rfft(r, n), np.fft.rfft(q, n)
    G = Q * np.conj(R)
    f = np.fft.rfftfreq(n, 1 / sr)
    w = ((f >= band[0]) & (f <= band[1])).astype(float)
    G = w * G / (np.abs(G) + 1e-12)
    c = np.fft.irfft(G, n)
    m = int(max_lag * sr)
    k0 = int(round(center * sr))
    idx = np.arange(k0 - m, k0 + m + 1)
    vals = c[idx % n]
    j = int(np.argmax(vals))
    frac = 0.0
    if 0 < j < len(vals) - 1:
        a, b, cc = vals[j - 1:j + 2]
        den = a - 2 * b + cc
        frac = 0.5 * (a - cc) / den if den else 0.0
    others = np.r_[vals[:max(0, j - 20)], vals[j + 21:]]
    sharp = float(vals[j] / (np.percentile(np.abs(others), 99) + 1e-12)) if len(others) else 0.0
    return (idx[j] + frac) / sr, float(vals[j]), sharp


def fit(ts, qs, w=None):
    A = np.stack([ts, np.ones_like(ts)], 1)
    keep = np.ones(len(ts), bool)
    for _ in range(5):
        a, b = np.linalg.lstsq(A[keep], qs[keep], rcond=None)[0]
        res = qs - (a * ts + b)
        mad = np.median(np.abs(res[keep] - np.median(res[keep])))
        new = np.abs(res) <= max(0.0006, 3 * 1.4826 * mad)
        if new.sum() < 3 or (new == keep).all():
            keep = new if new.sum() >= 3 else keep
            break
        keep = new
    a, b = np.linalg.lstsq(A[keep], qs[keep], rcond=None)[0]
    return float(a), float(b), qs - (a * ts + b), keep


def align(ref, ref_sr, quest, quest_sr, span=None, n_windows=10, win_s=6.0, strict=True):
    """ref/quest: mono arrays. span: (lo, hi) ref seconds to place the fine windows in (default
    the whole ref). Returns dict(a, b, ...); quest_time = a * ref_time + b."""
    g = np.gcd
    r = signal.resample_poly(ref, FS // g(FS, ref_sr), ref_sr // g(FS, ref_sr))
    q = signal.resample_poly(quest, FS // g(FS, quest_sr), quest_sr // g(FS, quest_sr))
    b0, coarse_q = coarse_offset(r, q, FS)
    lo, hi = span or (0.0, len(r) / FS)
    lo, hi = max(lo, 0) + win_s / 2, min(hi, len(r) / FS) - win_s / 2   # windows lie inside span
    if hi <= lo:
        lo = hi = (lo + hi) / 2
    centers = np.linspace(lo, hi, n_windows)
    a, b = 1.0, b0
    rows = []
    for pass_ in range(2):
        rows = []
        for c in centers:
            i0, i1 = int((c - win_s / 2) * FS), int((c + win_s / 2) * FS)
            pred = a * c + b                         # quest time of the window centre
            pad = 0.1
            j0 = int((pred - win_s / 2 - pad) * FS)
            if j0 < 0 or j0 + (i1 - i0) + int(2 * pad * FS) > len(q):
                continue
            rs = r[i0:i1] * np.hanning(i1 - i0)
            qs = q[j0:j0 + (i1 - i0) + int(2 * pad * FS)]
            # lag 0 here means q[j0 + k] ~ r[i0 + k]; the prediction is at +pad
            d, peak, sharp = gcc_phat(rs, qs, FS, 0.030 if pass_ else 0.060, pad + (pred - win_s / 2 - pad - j0 / FS))
            rows.append(dict(ref_s=float(c), quest_s=float(j0 / FS + d + win_s / 2 - (i0 / FS + win_s / 2 - c)),
                             peak=peak, sharpness=sharp))
        if len(rows) < 3:
            break
        ts = np.array([w["ref_s"] for w in rows])
        qs_ = np.array([w["quest_s"] for w in rows])
        a, b, res, keep = fit(ts, qs_)
    if len(rows) >= 3:
        for w, e, k in zip(rows, res, keep):
            w["residual_ms"] = float(e * 1000)
            w["used"] = bool(k)
    used = [w for w in rows if w.get("used")]
    rms = float(np.sqrt(np.mean([w["residual_ms"] ** 2 for w in used]))) if used else float("inf")
    out = dict(a=a, b=b, ratio_ppm=(a - 1) * 1e6, coarse_b=b0, coarse_quality=coarse_q,
               windows=rows, n_used=len(used), rms_residual_ms=rms,
               max_residual_ms=max((abs(w["residual_ms"]) for w in used), default=float("inf")),
               ref_rate=ref_sr, quest_rate=quest_sr)
    problems = []
    if len(used) < min(6, n_windows):
        problems.append(f"only {len(used)}/{len(rows)} windows agree")
    if rms > 1.0:
        problems.append(f"rms residual {rms:.2f} ms > 1 ms")
    if abs(a - 1) > 300e-6:
        problems.append(f"clock ratio {out['ratio_ppm']:+.0f} ppm is implausible")
    out["ok"] = not problems
    out["problems"] = problems
    if strict and problems:
        raise SystemExit("alignment FAILED: " + "; ".join(problems) + "\n" + report(out))
    return out


def report(al):
    lines = [f"quest_t = {al['a']:.8f} * ref_t + {al['b']:.6f} s   (clock {al['ratio_ppm']:+.1f} ppm, "
             f"coarse offset {al['coarse_b']:.3f} s)",
             f"windows used {al['n_used']}/{len(al['windows'])}, residual rms {al['rms_residual_ms']:.3f} ms, "
             f"max {al['max_residual_ms']:.3f} ms"]
    for w in al["windows"]:
        lines.append(f"  ref {w['ref_s']:8.3f} s -> quest {w['quest_s']:9.4f} s  residual "
                     f"{w.get('residual_ms', float('nan')):+7.3f} ms  sharpness {w['sharpness']:5.1f}"
                     f"{'' if w.get('used') else '  (dropped)'}")
    return "\n".join(lines)


def align_session(take_dir, session_dir, n_windows=10, strict=True, ref_info=None):
    """Align a playalong take to a Kevin session (audio.wav). Clicks are removed from ref first;
    windows cover the sheet's span."""
    ri = ref_info or dc.load_ref(take_dir)
    qx, qsr = dc.read_mono(Path(session_dir) / "audio.wav")
    # drums only: the count-off words and slates come from the guide speaker, whose path to the
    # headset differs from the kit's
    span = (ri["sheet"][0][0] - 0.05, ri["sheet"][-1][0] + 1.0) if ri["sheet"] else None
    al = align(ri["clean"], ri["sr"], qx, qsr, span, n_windows, strict=strict)
    al.update(take=str(take_dir), session=str(session_dir), ref_round_trip_s=ri["rt"],
              ref_round_trip_source=ri["rt_source"])
    return al


def session_dir(session, recordings=None):
    p = Path(recordings or dc.RECORDINGS) / session
    if not (p / "audio.wav").exists():
        raise SystemExit(f"{p}/audio.wav not found: import the Quest take with Kevin's "
                         "tools/import_quest_take.py first")
    return p


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("ref", nargs="?")
    p.add_argument("quest", nargs="?")
    p.add_argument("--session")
    p.add_argument("--take")
    p.add_argument("--recordings", help="default live_transcription/data/recordings")
    p.add_argument("--windows", type=int, default=10)
    p.add_argument("-o", "--output", help="write the fit as JSON")
    p.add_argument("--no-strict", action="store_true", help="report instead of failing")
    a = p.parse_args()
    if a.session and a.take:
        al = align_session(a.take, session_dir(a.session, a.recordings), a.windows, not a.no_strict)
    elif a.ref and a.quest:
        rx, rsr = dc.read_mono(a.ref)
        qx, qsr = dc.read_mono(a.quest)
        al = align(rx, rsr, qx, qsr, None, a.windows, strict=not a.no_strict)
    else:
        p.error("give REF QUEST, or --session and --take")
    print(report(al))
    if a.output:
        Path(a.output).write_text(json.dumps(al, indent=1) + "\n")


if __name__ == "__main__":
    main()
