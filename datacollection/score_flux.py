"""Score-informed drum hit detector: spectral-flux onsets + sheet-music alignment, no training.

  python datacollection/score_flux.py perf.wav sheet.drums.txt -o detected.drums.txt \
      [--sheet-offset S] [--latency-ms 0] [--gate]

  from score_flux import detect
  hits = detect(x, sr, sheet_notes, sheet_offset=S)      # [(file time, midi)]

--sheet-offset S: the sheet's t=0 sits at file time S (e.g. guide t0 in ref.wav time), so a real
take with lead-in / slates / count-off can be fed directly; output times are file times.

1. Onset times: log-magnitude spectral flux summed in 4 bands, each normalised by its own running
   scale, so a soft kick counts as much as a loud crash. Causal adaptive threshold
   (running mean + k*MAD of the past 0.5 s, plus a floor relative to the running 95th percentile);
   peak must be a local max over [-10 ms, +LOOKAHEAD]; 25 ms refractory gap.
2. Time refinement: inside [-25 ms, +8 ms] of the flux peak, the first sample where a 0.5 ms RMS
   envelope crosses pre + 25% of (local peak - pre). Done per note in that note's own band, so a
   kick a few ms after the hi-hat of the same "chord" gets its own time.
3. Identity from the score: each onset is paired with the nearest unconsumed sheet time-group within
   +-WINDOW of (sheet time + running drift), monotonic in time, exactly as a drummer reads the
   chart. All notes of that group are emitted, except that in multi-note groups a note is dropped
   if its band shows no attack (band flux < 20% of that band's running median at confirmed hits).
   An onset with no group is emitted as an EXTRA with a coarse band-energy guess (or dropped
   with --gate).
Everything except the 10 ms peak lookahead and refinement window is causal: algorithmic latency
~15-25 ms, so it can run on blocks in the hub.
"""
import argparse, sys
from collections import deque
from pathlib import Path
import numpy as np
from scipy import ndimage, signal

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dc_common as dc  # noqa: E402
from dc_common import cp  # noqa: E402  (Chris's compare_performance)

N_FFT, HOP = 1024, 64
BANDS = {"low": (30, 150), "lowmid": (150, 600), "mid": (600, 4000), "high": (5000, 16000)}
FAMILY_BAND = {"kick": "low", "tom": "lowmid", "snare": "mid", "hihat": "high",
               "crash": "high", "ride": "high"}
REFINE_BAND = {"low": "all"}   # 30-150 Hz envelope rises ~8 ms late; time kicks broadband
GUESS = {"low": 36, "lowmid": 45, "mid": 38, "high": 42}
REFRACTORY, LOOKAHEAD, WINDOW = 0.025, 0.010, 0.100
ODF_WEIGHTS = {"low": 1.0, "lowmid": 1.0, "mid": 1.0, "high": 1.0}
EXTRA_Z = 2.5        # an off-sheet attack must be this many times stronger than z_min to count as EXTRA
MAD_K, MASK, MASK_TAU, REL = 4.0, 0.25, 0.08, 0.02


def read_wav(path):
    """Mono float64 + rate; any format soundfile reads (int16/24/32, float32 WAV, ...)."""
    return dc.read_mono(path)


def _groups(sheet, sheet_offset=0.0, sheet_scale=1.0):
    """sheet: path or [(t, midi)] / [Hit]; returns {file time: [midi, ...]}."""
    if isinstance(sheet, (str, Path)):
        sheet = cp.read_notes(sheet)
    groups = {}
    for h in sheet:
        t, m = (h.time, h.midi) if hasattr(h, "time") else h
        groups.setdefault(round(sheet_offset + sheet_scale * t, 9), []).append(m)
    return groups


def band_flux(x, sr):
    """Per-family-band SuperFlux on a log filterbank (24 log-spaced bands, 30 Hz-16 kHz).
    Pooling bins *before* the log is what keeps noise from producing large flux peaks."""
    f, t, Z = signal.stft(x, sr, window="hann", nperseg=N_FFT, noverlap=N_FFT - HOP,
                          boundary=None, padded=False)
    P = np.abs(Z) ** 2
    edges = np.geomspace(30, min(16000, sr / 2 - 1), 25)
    fb = np.stack([P[(f >= lo) & (f < hi)].sum(0) + 1e-12 for lo, hi in zip(edges[:-1], edges[1:])])
    centres = np.sqrt(edges[:-1] * edges[1:])
    L = np.log10(fb)
    # SuperFlux (Boeck & Widmer 2013): compare with a max-filtered frame MU earlier, so beating
    # partials and slow decays don't look like attacks
    mu = max(1, round(0.010 * sr / HOP))
    ref = ndimage.maximum_filter1d(L, 3, axis=0)
    d = np.zeros_like(L)
    d[:, mu:] = np.maximum(0, L[:, mu:] - ref[:, :-mu])
    flux = {b: d[(centres >= lo) & (centres < hi)].mean(0) for b, (lo, hi) in BANDS.items()}
    return t, flux


def running_scale(v, n):
    """Causal running 90th percentile (approx via block updates) used to normalise a band."""
    out, buf = np.empty_like(v), deque(maxlen=n)
    q = 1e-6
    for i, s in enumerate(v):
        buf.append(s)
        if i % 32 == 0:
            q = max(np.percentile(buf, 90), 1e-6)
        out[i] = q
    return out


def detect_onsets(x, sr):
    t, flux = band_flux(x, sr)
    fps = sr / HOP
    # the 30-150 Hz band is only ~3 FFT bins: too noisy to vote on attacks (beating kick partials,
    # room rumble). It is kept for kick evidence; the kick beater click shows up in the other bands.
    odf = sum(w * flux[b] for b, w in ODF_WEIGHTS.items())
    past, look = int(0.5 * fps), max(1, int(LOOKAHEAD * fps))
    onsets, last, last_pk = [], -1.0, 0.0
    hist = deque(maxlen=past)
    big = deque(maxlen=int(3 * fps))
    for i in range(len(odf)):
        v = odf[i]
        if len(hist) > 10:
            h = np.fromiter(hist, float)
            med = np.median(h)
            # noise floor + masking by the previous hit's decay + a floor relative to recent peaks
            thr = (med + MAD_K * np.median(np.abs(h - med))
                   + MASK * last_pk * np.exp(-(t[i] - last) / MASK_TAU)
                   + REL * max(big))
            lo, hi = max(0, i - int(0.010 * fps)), min(len(odf), i + look + 1)
            if v > thr and v == odf[lo:hi].max() and t[i] - last > REFRACTORY:
                onsets.append(i)
                last, last_pk = t[i], v
        hist.append(v)
        big.append(v)
    return t, flux, odf, onsets


class Refiner:
    def __init__(self, x, sr):
        self.sr = sr
        self.env = {}
        w = max(1, int(0.0005 * sr))
        for b, (lo, hi) in [("all", (40, 16000))] + list(BANDS.items()):
            y = signal.sosfiltfilt(signal.butter(2, [lo, min(hi, sr / 2 - 100)], "band", fs=sr, output="sos"), x)
            self.env[b] = np.sqrt(np.convolve(y ** 2, np.ones(w) / w, "same"))

    def time(self, t0, band="all"):
        """Attack time near flux-peak frame t0: find the envelope peak in [t0-5, t0+20] ms, then
        walk BACK from it to where the envelope was last below 25% of the way up from the trough
        before it. (Walking forward from the window start fires on the previous hit's tail.)"""
        e, sr = self.env[band], self.sr
        a = max(int((t0 - 0.025) * sr), 0)
        p0, p1 = int((t0 - 0.005) * sr), int((t0 + 0.020) * sr)
        if p1 - p0 < 4 or p0 <= a:
            return t0
        kp = p0 + int(np.argmax(e[p0:p1]))
        pk = e[kp]
        pre = e[a:kp + 1].min()
        if pk <= pre * 1.5:
            return None if band != "all" else t0
        below = np.flatnonzero(e[a:kp + 1] <= pre + 0.05 * (pk - pre))
        return (a + below[-1] + 1) / sr


def run(x, sr, sheet, latency=0.0, gate=False, per_band=True, verify=True, sheet_offset=0.0,
        sheet_scale=1.0):
    t, flux, odf, onsets = detect_onsets(x, sr)
    ref = Refiner(x, sr)
    groups = _groups(sheet, sheet_offset, sheet_scale)
    gtimes = np.array(sorted(groups))
    nxt = 0                       # first unconsumed group
    recent = deque()              # (onset time, error) for drift
    seen = {b: deque(maxlen=20) for b in BANDS}
    out = []
    for i in onsets:
        frame_t = t[i]
        tt = ref.time(frame_t) - latency
        while recent and recent[0][0] < tt - 2.0:
            recent.popleft()
        drift = float(np.median([e for _, e in recent])) if recent else 0.0
        target = tt - drift
        # skip groups we have clearly passed (missed by the player)
        while nxt < len(gtimes) and gtimes[nxt] < target - WINDOW:
            nxt += 1
        cand = [j for j in range(nxt, len(gtimes)) if abs(gtimes[j] - target) <= WINDOW]
        if not cand:
            if not gate:
                bf = {b: flux[b][i] / (np.median(seen[b]) if seen[b] else 1) for b in BANDS}
                out.append((tt, GUESS[max(bf, key=bf.get)]))
            continue
        j = min(cand, key=lambda j: abs(gtimes[j] - target))
        nxt = j + 1
        recent.append((tt, tt - gtimes[j]))
        mids = groups[gtimes[j]]
        for m in mids:
            band = FAMILY_BAND.get(cp.family(m), "mid")
            ev = max(flux[band][max(0, i - 3):i + 4])
            if verify and len(set(FAMILY_BAND.get(cp.family(q), "mid") for q in mids)) > 1 \
                    and len(seen[band]) >= 4 and ev < 0.2 * np.median(seen[band]):
                continue
            seen[band].append(ev)
            tm = tt
            if per_band and len(mids) > 1:
                r = ref.time(frame_t, band)
                if r is not None and abs(r - latency - tt) < 0.030:
                    tm = r - latency
            out.append((tm, m))
    return sorted(out)


def znorm(v, fps, past_s=1.0, step=16):
    """Causal per-band z-score: (v - running median) / running MAD over the past second."""
    n = int(past_s * fps)
    out = np.empty_like(v)
    med = mad = None
    for i in range(0, len(v), step):
        h = v[max(0, i - n):i] if i > 8 else v[:8]
        med = np.median(h)
        mad = np.median(np.abs(h - med)) + 1e-3
        out[i:i + step] = (v[i:i + step] - med) / mad
    return np.maximum(out, 0)


def run_align(x, sr, sheet, latency=0.0, per_band=True, verify=True, window=0.070,
              z_min=6.0, extras=True, sheet_offset=0.0, sheet_scale=1.0, extras_span=None,
              return_groups=False):
    """For each expected sheet time-group, in order: look in +-window around (sheet time +
    running drift) for the strongest attack in the bands that group's drums live in; emit when
    the window has closed (latency ~ window + 10 ms).
    extras_span=(lo, hi): only report EXTRA hits inside this file-time span (default: the
    sheet's span, -0.15 s .. +1 s, so slates/count-off/talk before the music are not reported).
    return_groups: also return one dict per sheet group (and per EXTRA): gt (sheet group time,
    None for an extra), mids, tt (group onset or None if nothing was played there), drift,
    emitted {midi: time} (a chord note whose band showed no attack is absent)."""
    t, flux = band_flux(x, sr)
    fps = sr / HOP
    z = {b: znorm(v, fps) for b, v in flux.items()}
    z_any = sum(ODF_WEIGHTS[b] * z[b] for b in BANDS) / len(BANDS)
    ref = Refiner(x, sr)
    groups = _groups(sheet, sheet_offset, sheet_scale)
    gtimes = sorted(groups)
    if extras_span is None and gtimes:
        extras_span = (gtimes[0] - 0.15, gtimes[-1] + 1.0)
    ginfo = []   # one dict per sheet group: what was found there (for offline refinement / review)
    recent, seen, out, chosen = deque(), {b: deque(maxlen=20) for b in BANDS}, [], []
    floor_t = -1.0
    for g, gt in enumerate(gtimes):
        mids = groups[gt]
        bands = sorted({FAMILY_BAND.get(cp.family(m), "mid") for m in mids})
        while recent and recent[0][0] < gt - 2.0:
            recent.popleft()
        drift = float(np.median([e for _, e in recent])) if recent else 0.0
        target = gt + drift + latency
        lo_t = max(target - window, floor_t + REFRACTORY)
        hi_t = target + window
        if g + 1 < len(gtimes):   # never reach past halfway to the next written note
            hi_t = min(hi_t, target + (gtimes[g + 1] - gt) / 2)
        lo, hi = int(np.searchsorted(t, lo_t)), int(np.searchsorted(t, hi_t))
        if hi - lo < 3:
            ginfo.append({"gt": gt, "mids": mids, "tt": None, "drift": drift, "emitted": {}})
            continue
        s = sum(z[b][lo:hi] for b in bands) / len(bands) + 0.5 * z_any[lo:hi]
        k = int(np.argmax(s))
        if s[k] < z_min:
            ginfo.append({"gt": gt, "mids": mids, "tt": None, "drift": drift, "emitted": {}})
            continue                                   # nothing played: a miss
        i = lo + k
        # a cymbal's flux keeps climbing as it blooms: start from where it first reached half its peak
        back = np.flatnonzero(s[max(0, k - int(0.030 * fps)):k + 1] >= 0.5 * s[k])
        i0 = lo + max(0, k - int(0.030 * fps)) + int(back[0])
        tt = ref.time(t[i0])
        floor_t = tt
        tt -= latency
        recent.append((tt, tt - gt))
        chosen.append(tt)
        gi = {"gt": gt, "mids": mids, "tt": tt, "drift": drift, "emitted": {},
              "window": (lo_t - latency, hi_t - latency)}
        ginfo.append(gi)
        for m in mids:
            band = FAMILY_BAND.get(cp.family(m), "mid")
            ev = z[band][max(0, i - 4):i + 5].max()
            if verify and len(bands) > 1 and ev < 0.25 * z_min:
                continue                               # that drum's band shows no attack
            tm = tt
            if per_band and len(bands) > 1:
                # each limb lands a few ms apart: time this drum from its own band's attack
                w = int(0.040 * fps)
                a2, b2 = max(lo, i - w), min(hi, i + w)
                j = a2 + int(np.argmax(z[band][a2:b2]))
                r = ref.time(t[j], REFINE_BAND.get(band, band))
                if r is not None and abs(r - latency - tt) < 0.045:
                    tm = r - latency
            out.append((tm, m))
            gi["emitted"][m] = tm
    if extras:   # strong attacks nowhere near any written note -> EXTRA with a band-energy guess
        _, _, _, onsets = detect_onsets(x, sr)
        ch = np.array(chosen)
        for i in onsets:
            tt = ref.time(t[i]) - latency
            if len(ch) and np.min(np.abs(ch - tt)) < 0.060:
                continue
            if z_any[i] * 2 < 12 * z_min:
                continue
            if extras_span and not extras_span[0] <= tt <= extras_span[1]:
                continue
            bf = {b: z[b][i] for b in BANDS}
            out.append((tt, GUESS[max(bf, key=bf.get)]))
            ginfo.append({"gt": None, "mids": [GUESS[max(bf, key=bf.get)]], "tt": tt, "drift": None,
                          "emitted": {GUESS[max(bf, key=bf.get)]: tt}, "extra": True})
    ginfo.sort(key=lambda g: g["tt"] if g["tt"] is not None else g["gt"])
    return (sorted(out), ginfo) if return_groups else sorted(out)


def detect(x, sr, sheet, sheet_offset=0.0, sheet_scale=1.0, mode="align", latency=0.0,
           window=0.070, extras=True, gate=False, per_band=True, verify=True, **kw):
    """Importable entry point. x: mono float array at rate sr; sheet: path, [(t, midi)] or [Hit]
    in sheet seconds; the sheet's t is placed at file time sheet_offset + sheet_scale * t.
    Returns sorted [(file time s, midi)]; EXTRA (off-sheet) hits carry a band-guess midi
    (36/45/38/42). mode 'align' (default) or 'greedy'."""
    x = np.asarray(x, dtype=np.float64)
    if mode == "greedy":
        return run(x, sr, sheet, latency, gate, per_band, verify, sheet_offset, sheet_scale)
    return run_align(x, sr, sheet, latency, per_band, verify, window, extras=extras and not gate,
                     sheet_offset=sheet_offset, sheet_scale=sheet_scale, **kw)



# --------------------------------------------------------------------------- offline refinement

REFINE_BANDS = {"low": (30, 200), "lowmid": (140, 600), "mid": (600, 4500), "high": (6000, 16000),
                "all": (40, 16000)}
FAMILY_REFINE = {"kick": "low", "tom": "lowmid", "snare": "mid", "hihat": "high",
                 "crash": "high", "ride": "high"}


class AttackRefiner:
    """Non-causal attack timing for LABELING (not for the live path). Per band: zero-phase
    band-pass, 0.5 ms RMS envelope. An attack is a point where the envelope's log jumps: rise(k) =
    log(max env over the next 3 ms) - log(mean env over the 6..1 ms before). The labeled time is
    where the envelope first gets 10 % of the way from that pre-level to the local peak."""

    def __init__(self, x, sr):
        self.sr = sr
        w = max(1, int(0.0005 * sr))
        self.env, self.rise = {}, {}
        a, b, c = int(0.003 * sr), int(0.006 * sr), int(0.001 * sr)
        for name, (lo, hi) in REFINE_BANDS.items():
            sos = signal.butter(2 if lo < 100 else 4, [lo, min(hi, sr / 2 * 0.95)], "band", fs=sr, output="sos")
            y = signal.sosfiltfilt(sos, x)
            e = np.convolve(np.abs(signal.hilbert(y)), np.ones(w) / w, "same") + 1e-7
            m = ndimage.maximum_filter1d(e, a)               # centred: max e[k-a//2 : k+a-a//2]
            fut = np.r_[m[a // 2:], np.full(a // 2, m[-1])]  # max e[k : k+a]
            cs = np.r_[0.0, np.cumsum(e)]
            k = np.arange(len(e))
            i0, i1 = np.clip(k - b, 0, len(e)), np.clip(k - c, 1, len(e))
            past = (cs[i1] - cs[i0]) / np.maximum(1, i1 - i0)  # mean e[k-b : k-c]
            self.env[name], self.rise[name] = e, np.log(fut / past)

    def candidates(self, band, lo_t, hi_t, min_rise=np.log(2.0)):
        """[(time of rise peak, rise, absolute jump)] inside [lo_t, hi_t]."""
        sr, r, e = self.sr, self.rise[band], self.env[band]
        lo, hi = max(1, int(lo_t * sr)), min(len(r) - 1, int(hi_t * sr))
        if hi - lo < 3:
            return []
        seg = r[lo:hi]
        pk = signal.find_peaks(seg, height=min_rise, distance=max(1, int(0.002 * sr)))[0]
        a = int(0.003 * sr)
        return [((lo + k) / sr, float(seg[k]), float(e[lo + k:lo + k + a].max() - e[max(0, lo + k - a):lo + k].mean()))
                for k in pk]

    def onset(self, band, t_peak):
        """Walk back from a rise peak to the 10 % point of the attack."""
        sr, e = self.sr, self.env[band]
        k = int(round(t_peak * sr))
        b, c, a = int(0.006 * sr), int(0.001 * sr), int(0.004 * sr)
        pre = e[max(0, k - b):max(1, k - c)].mean()
        top = k + int(np.argmax(e[k:k + a]))
        thr = pre + 0.10 * (e[top] - pre)
        j = top
        lim = max(0, k - b)
        while j > lim and e[j - 1] > thr:
            j -= 1
        return j / sr

    def time_note(self, midi, t_est, lo_t, hi_t):
        """(refined time, confidence 'ok'|'weak'|'none') for one note near t_est."""
        band = FAMILY_REFINE.get(cp.family(midi), "all")
        cands = self.candidates(band, lo_t, hi_t)
        if not cands:
            return t_est, "none"
        # strongest absolute jump, mildly preferring the estimate's neighbourhood
        best = max(cands, key=lambda c: c[2] / (1 + abs(c[0] - t_est) / 0.020))
        return self.onset(band, best[0]), "ok" if best[1] > np.log(4.0) else "weak"


BAND_ORDER = {"low": 0, "lowmid": 1, "mid": 2, "high": 3, "all": 4}


def refine(x, sr, ginfo, search=0.035, avoid=0.002):
    """Offline re-timing of run_align(return_groups=True) output, for LABELING.

    Per played sheet group, every written note (also chord notes the causal pass dropped) is
    timed from its own band's attack. Notes are timed in band order low -> high; a later note
    avoids attacks within `avoid` of one already given to its chord partner when its band offers
    another clear attack (a hi-hat 6 ms after the snare otherwise inherits the snare's highs).
    Returns [dict(t, midi, coarse, conf, gt, n_cands)], conf in ok | weak | none | recovered | extra.
    """
    ar = AttackRefiner(np.asarray(x, dtype=np.float64), sr)
    played = [g for g in ginfo if g["tt"] is not None]
    exp = [g["tt"] for g in played]
    out, jumps = [], {}
    for n, g in enumerate(played):
        c = g["tt"]
        lo = max(c - search, (exp[n - 1] + c) / 2 if n else c - search)
        hi = min(c + search, (exp[n + 1] + c) / 2 if n + 1 < len(exp) else c + search)
        if g.get("extra"):
            cands = ar.candidates("all", c - 0.015, c + 0.015)
            t = ar.onset("all", max(cands, key=lambda q: q[2])[0]) if cands else c
            out.append(dict(t=t, midi=g["mids"][0], coarse=c, conf="extra", gt=None, n_cands=len(cands)))
            continue
        assigned = []
        notes = sorted(set(g["mids"]), key=lambda m: BAND_ORDER[FAMILY_REFINE.get(cp.family(m), "all")])
        for m in notes:
            band = FAMILY_REFINE.get(cp.family(m), "all")
            cands = ar.candidates(band, lo, hi)
            was = g["emitted"].get(m)
            if not cands:
                if was is not None:
                    out.append(dict(t=was, midi=m, coarse=was, conf="none", gt=g["gt"], n_cands=0))
                continue
            top = max(q[2] for q in cands)
            free = [q for q in cands if all(abs(q[0] - a) > avoid for a in assigned)
                    and q[2] >= 0.4 * top and q[1] > np.log(3.0)]
            pool = free or cands
            best = max(pool, key=lambda q: q[2] / (1 + abs(q[0] - c) / 0.020))
            t = ar.onset(band, best[0])
            conf = "ok" if best[1] > np.log(4.0) else "weak"
            if was is None:      # the causal pass dropped it: only keep a clear, typical attack
                typ = np.median(jumps.get(band, [best[2]]))
                if conf != "ok" or best[2] < 0.3 * typ:
                    continue
                conf = "recovered"
            else:
                jumps.setdefault(band, []).append(best[2])
            assigned.append(best[0])
            strong = sum(q[2] >= 0.5 * top for q in cands)
            out.append(dict(t=t, midi=m, coarse=was if was is not None else c, conf=conf,
                            gt=g["gt"], n_cands=strong))
    return sorted(out, key=lambda d: (d["t"], d["midi"]))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("wav")
    p.add_argument("sheet")
    p.add_argument("-o", "--output", required=True)
    p.add_argument("--latency-ms", type=float, default=0.0, help="constant capture latency to subtract")
    p.add_argument("--gate", action="store_true", help="drop onsets that match no sheet note")
    p.add_argument("--no-per-band", action="store_true")
    p.add_argument("--no-verify", action="store_true")
    p.add_argument("--mode", choices=("align", "greedy"), default="align",
                   help="align: search each written note's window (default); greedy: onsets first")
    p.add_argument("--window-ms", type=float, default=70)
    p.add_argument("--no-extras", action="store_true")
    p.add_argument("--sheet-offset", type=float, default=0.0,
                   help="file time (s) of the sheet's t=0, e.g. the guide's t0 in this recording")
    p.add_argument("--sheet-scale", type=float, default=1.0,
                   help="sheet seconds -> file seconds factor (tempo change / clock ratio)")
    a = p.parse_args()
    x, sr = read_wav(a.wav)
    hits = detect(x, sr, a.sheet, a.sheet_offset, a.sheet_scale, a.mode, a.latency_ms / 1000,
                  a.window_ms / 1000, extras=not a.no_extras, gate=a.gate,
                  per_band=not a.no_per_band, verify=not a.no_verify)
    dc.write_notes(a.output, hits, f"score_flux.py {Path(a.wav).name} (sheet offset {a.sheet_offset:g} s)")
    print(f"wrote {len(hits)} hits to {a.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
