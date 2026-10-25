"""Score the live detectors on a labeled Quest session.

  python datacollection/evaluate.py --session quest_X --take datacollection/takes/NAME \
      [--kit ...] [--model live_transcription/models/quest2_v1.bin] [--mic quest|ref] [-o report.json]

Truth = the session's labels.csv (from label.py, or hand-made). Detectors:
  score_flux   training-free, score-informed, on the raw Quest audio; the sheet is placed in Quest
               time through the take alignment (quest = a * ref + b)
  kevin_core   Kevin's native core (tools/common.py extract). With a trained --model its family
               events are scored per drum; without one only its onset candidates (any drum).
Per drum: recall, precision, timing error (detector - label) mean / |p95| for one-to-one matches
within 30 ms. Then labels and each detector are fed through Chris's LiveMatcher + displayed-state
logic (live_compare, tolerance 10 ms, smooth 0.5 s, hysteresis 2 ms) and the per-hit verdict
(RUSHING/DRAGGING/PERFECT) and displayed state are compared note by note.
Latency: the laptop's speaker->mic round trip (measured from the take), and how much later the
guide speaker reaches the headset than the drum-based alignment predicts (room geometry).
--mic ref scores the same detectors on ref.wav instead (score_flux there is circular: the labels
came from it; Kevin's core is not).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import deque
from pathlib import Path

import numpy as np
from scipy import signal

sys.path.insert(0, str(Path(__file__).resolve().parent))
import align as al  # noqa: E402
import dc_common as dc  # noqa: E402
import score_flux as sfx  # noqa: E402
from dc_common import cp  # noqa: E402

TOL = 0.030


def stats(det, truth, key=cp.family):
    pairs = dc.match_one_to_one(det, truth, TOL, key)
    rows = {}
    fams = sorted({key(m) for _, m, *_ in truth} | {key(m) for _, m, *_ in det})
    for fam in fams + ["ALL"]:
        sel = (lambda m: True) if fam == "ALL" else (lambda m, f=fam: key(m) == f)
        nt = sum(sel(m) for _, m, *_ in truth)
        nd = sum(sel(m) for _, m, *_ in det)
        e = np.array([d for d, i, k in pairs if sel(truth[k][1])]) * 1000
        rows[fam] = dict(n_truth=nt, n_det=nd, matched=len(e),
                         recall=len(e) / nt if nt else None, precision=len(e) / nd if nd else None,
                         err_mean_ms=float(e.mean()) if len(e) else None,
                         abs_p95_ms=float(np.percentile(np.abs(e), 95)) if len(e) else None)
    return rows


def onset_groups(labels, merge=0.020):
    """Distinct onsets in the labels (chord notes within `merge` count once), as (t, 0)."""
    out = []
    for t, *_ in sorted(labels):
        if not out or t - out[-1][0] > merge:
            out.append((t, 0))
    return out


def live_states(sheet, hits):
    """Per sheet note: (verdict, displayed state, error) as live_compare.run computes them."""
    import live_compare as lc
    m = lc.LiveMatcher([cp.Hit(t, mi) for t, mi in sheet])
    recent, state, res = deque(), "waiting", {}
    for t, midi in sorted(hits):
        hit, err = m.feed(t, midi)
        if err is not None:
            recent.append((t, err))
        while recent and recent[0][0] < t - 0.5:
            recent.popleft()
        if recent:
            state = lc.smoothed_state(state, lc.recent_median(recent, t, 0.5), 0.010, 0.002)
        if err is not None:
            res[(round(hit.match.time, 6), hit.match.midi)] = (lc.verdict(err, 0.010), state, err)
    return res


def verdict_agreement(sheet, truth_hits, det_hits):
    gt, dt = live_states(sheet, truth_hits), live_states(sheet, det_hits)
    common = set(gt) & set(dt)
    if not common:
        return dict(judged_truth=len(gt), judged_det=len(dt), both=0)
    d = np.array([dt[k][2] - gt[k][2] for k in common]) * 1000
    return dict(judged_truth=len(gt), judged_det=len(dt), both=len(common),
                verdict_agree=float(np.mean([gt[k][0] == dt[k][0] for k in common])),
                state_agree=float(np.mean([gt[k][1] == dt[k][1] for k in common])),
                err_diff_abs_p95_ms=float(np.percentile(np.abs(d), 95)))


def guide_path_delay(qx, qsr, play, ref_sr, rt, a, b, max_lag=0.030):
    """How much later than (a * (scheduled + rt) + b) the guide speaker's sound shows up in the
    Quest audio: GCC-PHAT between the Quest audio and the guide output warped into Quest time."""
    fs = 16000
    q = signal.resample_poly(qx, fs // np.gcd(fs, qsr), qsr // np.gcd(fs, qsr))
    p = signal.resample_poly(play, 4, 1)
    tq = np.arange(len(q)) / fs
    tr = (tq - b) / a - rt
    pq = np.interp(tr * ref_sr * 4, np.arange(len(p)), p, left=0, right=0)
    act = np.flatnonzero(np.abs(pq) > 0.05 * np.abs(pq).max())
    if not len(act):
        return None
    lo, hi = act[0], act[-1]
    d, peak, sharp = al.gcc_phat(pq[lo:hi], q[lo:hi], fs, max_lag, 0.0, band=(300, 6000))
    return dict(delay_ms=d * 1000, sharpness=sharp)


def evaluate(session_dir, take_dir, kit, model=None, mic="quest", quiet=False):
    sdir = Path(session_dir)
    labels = dc.read_labels(sdir / "labels.csv", kit)          # (t, family midi, piece), quest time
    ri = dc.load_ref(take_dir)
    cached = Path(take_dir) / f"align_{sdir.name}.json"
    if cached.exists():
        alj = json.loads(cached.read_text())["alignment"]
        a, b = alj["a"], alj["b"]
        al_src = str(cached)
    else:
        alj = al.align_session(take_dir, sdir, ref_info=ri)
        a, b, al_src = alj["a"], alj["b"], "computed now"
    ltc = dc.kevin()
    if mic == "quest":
        x, sr = dc.read_mono(sdir / "audio.wav")
        truth = [(t, m) for t, m, _ in labels]
        sheet = [(a * t + b, m) for t, m in ri["sheet"]]
        to_ref = lambda t: (t - b) / a  # noqa: E731
    else:
        x, sr = dc.read_mono(Path(take_dir) / "ref.wav", 48000)
        truth = [((t - b) / a, m) for t, m, _ in labels]
        sheet = ri["sheet"]
        to_ref = lambda t: t  # noqa: E731
    out = dict(session=str(sdir), take=str(take_dir), mic=mic, alignment=dict(a=a, b=b, source=al_src),
               n_labels=len(truth))

    det_sf = sfx.detect(x, sr, sheet)
    out["score_flux"] = stats(det_sf, truth)
    truth_ref = [(to_ref(t), m) for t, m in truth]
    out["score_flux"]["live_compare"] = verdict_agreement(ri["sheet"], truth_ref,
                                                          [(to_ref(t), m) for t, m in det_sf])

    audio = x.astype(np.float32) if sr == ltc.RATE else dc.read_mono(sdir / "audio.wav")[0].astype(np.float32)
    model = Path(model) if model else None
    cands = ltc.extract(np.clip(audio, -1, 1), str(model) if model else None)
    if model:
        ev = ltc.events(cands)
        out["kevin_core"] = stats(ev, truth)
        out["kevin_core"]["live_compare"] = verdict_agreement(ri["sheet"], truth_ref,
                                                              [(to_ref(t), m) for t, m in ev])
    else:
        on = [(c.time_s, 0) for c in cands]
        out["kevin_core_onsets"] = stats(on, onset_groups(truth), key=lambda m: "onset")
    lat = dict(ref_round_trip_ms=ri["rt"] * 1000, ref_round_trip_source=ri["rt_source"])
    play = dc.scheduled_output(ri["take"], ri["sr"])
    if play is not None and mic == "quest":
        g = guide_path_delay(x, sr, play, ri["sr"], ri["rt"], a, b)
        if g:
            lat["guide_at_headset_vs_drum_alignment_ms"] = g["delay_ms"]
            lat["guide_at_headset_sharpness"] = g["sharpness"]
    out["latency"] = lat
    if not quiet:
        print(report(out))
    return out


def _fmt(v, f="{:.3f}"):
    return "-" if v is None else f.format(v)


def report(o):
    L = [f"{o['session']}  ({o['mic']} mic, {o['n_labels']} labels; quest = {o['alignment']['a']:.8f} * ref "
         f"+ {o['alignment']['b']:.6f})"]
    for name in ("score_flux", "kevin_core", "kevin_core_onsets"):
        if name not in o:
            continue
        L.append(f"{name}:")
        L.append(f"  {'drum':8s} {'truth':>5s} {'det':>5s} {'recall':>7s} {'prec':>7s} {'err mean':>9s} {'|err| p95':>9s}")
        for fam, r in o[name].items():
            if fam == "live_compare":
                continue
            L.append(f"  {fam:8s} {r['n_truth']:5d} {r['n_det']:5d} {_fmt(r['recall']):>7s} {_fmt(r['precision']):>7s} "
                     f"{_fmt(r['err_mean_ms'], '{:+.2f}'):>9s} {_fmt(r['abs_p95_ms'], '{:.2f}'):>9s}")
        lc = o[name].get("live_compare")
        if lc and lc.get("both"):
            L.append(f"  live_compare vs labels: notes judged {lc['judged_truth']} / {lc['judged_det']} (both "
                     f"{lc['both']}); per-hit verdict agree {lc['verdict_agree']:.3f}, displayed state agree "
                     f"{lc['state_agree']:.3f}, |error diff| p95 {lc['err_diff_abs_p95_ms']:.1f} ms")
    if "kevin_core_onsets" in o:
        L.append("  (no --model: onsets of any drum vs distinct label onsets; chord notes within 20 ms count once)")
    lat = o["latency"]
    L.append(f"latency: laptop speaker->mic round trip {lat['ref_round_trip_ms']:.2f} ms ({lat['ref_round_trip_source']})")
    if "guide_at_headset_vs_drum_alignment_ms" in lat:
        L.append(f"         guide speaker reaches the headset {lat['guide_at_headset_vs_drum_alignment_ms']:+.2f} ms "
                 f"relative to the drum alignment (sharpness {lat['guide_at_headset_sharpness']:.1f})")
    return "\n".join(L)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--session", required=True)
    p.add_argument("--take", required=True)
    p.add_argument("--recordings")
    p.add_argument("--kit")
    p.add_argument("--model", help="Kevin's trained model (default models/quest2_v1.bin if present)")
    p.add_argument("--mic", choices=("quest", "ref"), default="quest")
    p.add_argument("-o", "--output")
    a = p.parse_args()
    model = a.model
    if model is None and (dc.LT / "models" / "quest2_v1.bin").exists():
        model = dc.LT / "models" / "quest2_v1.bin"
    o = evaluate(al.session_dir(a.session, a.recordings), a.take, dc.load_kit(a.kit), model, a.mic)
    if a.output:
        Path(a.output).write_text(json.dumps(o, indent=1, default=float) + "\n")


if __name__ == "__main__":
    main()
