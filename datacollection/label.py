"""Write Kevin's labels.csv for a Quest session, from the laptop reference take or by fiat.

  python datacollection/label.py score --session quest_X --take datacollection/takes/NAME \
      [--kit live_transcription/config/kit.local.json] [--snap confirm|move|off] [--force]
  python datacollection/label.py single --session quest_X --piece snare [--floor-db -45] [--force]

score: the guide's clicks are subtracted from ref.wav (sample-locked, so the click is the same
  waveform every time), score_flux finds every written note on that close mic (score-informed,
  no model), each note is then re-timed offline from its own band's attack (score_flux.refine),
  the drummer's 3 rim taps after the slates are added as snare hits, and everything is mapped into
  the Quest audio.wav time through align.py's fit quest = a * ref + b.
  Kevin's onset core (tools/common.py extract(audio, None)) is run on the Quest audio and each
  label is checked against its nearest candidate within +-15 ms:
    --snap confirm (default)  keep the mapped time; candidate only drives the review flags
    --snap move               replace the time with the candidate's (measured worse in the
                              simulation: Kevin's core marks one onset per chord and ~ms-late
                              onsets under reverb; see README)
    --snap off                no core run
  labels_review.csv lists low-confidence rows (severity=check: listen; info: only the core disagrees): no core candidate within 15 ms, two or more,
  label/candidate disagreement > 5 ms, band attack weak/absent, notes recovered or EXTRA hits
  off the sheet, sheet notes where nothing was found (not labeled), taps.
single: every hit of a one-drum take becomes --piece. Hits are found from the level (the Quest
  mic is gated between hits, so each hit is a 40-80 dB jump), not from Kevin's core, which missed
  and doubled real Quest kicks. Very quiet ones (below --floor-db relative to the take's loud
  hits) go to the review file instead of labels.csv.

Never overwrites labels.csv / labels_review.csv unless --force.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import align as al  # noqa: E402
import dc_common as dc  # noqa: E402
import score_flux as sfx  # noqa: E402

SNAP_S = 0.015
DISAGREE_S = 0.005
REVIEW_FIELDS = ["time_s", "piece_id", "status", "severity", "reason", "ref_time_s", "candidate_dt_ms", "detail"]


def core_candidates(audio_path):
    ltc = dc.kevin()
    return np.array(sorted(c.time_s for c in ltc.extract(ltc.read_audio(audio_path)))), ltc


def check_outputs(sdir, force):
    for name in ("labels.csv", "labels_review.csv"):
        if (sdir / name).exists() and not force:
            raise SystemExit(f"{sdir / name} exists; pass --force to overwrite")


CHECK = {"precursor", "attack_weak", "attack_none", "recovered", "extra", "ambiguous_piece", "sheet_note_not_found",
         "no_piece_in_kit", "very_low", "slow_attack", "voiced"}


def severity(reason):
    """'check' = a human should listen; 'info' = only Kevin's core disagrees with the reference
    (it marks one onset per chord and misses some hits, so this is mostly about the core)."""
    return "check" if set(reason.split(";")) & CHECK else "info"


def write_outputs(sdir, labels, review, duration):
    for r in review:
        r["severity"] = severity(r["reason"])
    labels = sorted((t, p) for t, p in labels if 0 <= t < duration)
    with open(sdir / "labels.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["time_s", "piece_id"])
        w.writerows((f"{t:.6f}", p) for t, p in labels)
    with open(sdir / "labels_review.csv", "w", newline="") as f:
        w = csv.DictWriter(f, REVIEW_FIELDS)
        w.writeheader()
        for r in sorted(review, key=lambda r: r["time_s"]):
            w.writerow({k: (f"{v:.6f}" if k == "time_s" else v) for k, v in r.items()})
    return labels


def find_taps(clean, sr, take, rt):
    """The drummer's 3 snare rim clicks inside playalong's tap window."""
    win = take.get("tap_window_s")
    if not win:
        return []
    ar = sfx.AttackRefiner(clean[int(max(0, (win[0] + rt - 0.1)) * sr):int((win[1] + rt + 0.1) * sr)], sr)
    base = max(0, win[0] + rt - 0.1)
    cands = ar.candidates("mid", 0.1 + 0.02, win[1] - win[0] + 0.1, min_rise=np.log(4.0))
    if not cands:
        return []
    top = max(c[2] for c in cands)
    strong = sorted(sorted(cands, key=lambda c: -c[2])[:3])
    return [base + ar.onset("mid", c[0]) for c in strong if c[2] >= 0.2 * top]


def label_score(session_dir, take_dir, kit, snap="confirm", n_windows=10, force=False, quiet=False):
    sdir = Path(session_dir)
    check_outputs(sdir, force)
    log = (lambda *a: None) if quiet else (lambda *a: print(*a, file=sys.stderr))
    ri = dc.load_ref(take_dir)
    alignment = al.align_session(take_dir, sdir, n_windows, ref_info=ri)
    log(al.report(alignment).split("\n", 2)[0] + "\n" + al.report(alignment).split("\n")[1])
    a, b = alignment["a"], alignment["b"]
    clean, sr = ri["clean"], ri["sr"]
    _, ginfo = sfx.run_align(clean, sr, ri["sheet"], return_groups=True)
    notes = sfx.refine(clean, sr, ginfo)
    for t in find_taps(clean, sr, ri["take"], ri["rt"]):
        notes.append(dict(t=t, midi=37, coarse=t, conf="tap", gt=None, n_cands=1))
    misses = [g for g in ginfo if g["tt"] is None and g["gt"] is not None]

    qx, qsr = dc.read_mono(sdir / "audio.wav")
    duration = len(qx) / qsr
    cand = core_candidates(sdir / "audio.wav")[0] if snap != "off" else None
    labels, review = [], []
    for n in notes:
        piece, ambiguous = dc.piece_for(n["midi"], kit)
        tq = a * n["t"] + b
        reasons, dt_ms = [], ""
        if piece is None:
            review.append(dict(time_s=tq, piece_id="", status="not_labeled", reason="no_piece_in_kit",
                               ref_time_s=f"{n['t']:.6f}", candidate_dt_ms="", detail=f"midi {n['midi']}"))
            continue
        if cand is not None:
            near = cand[np.abs(cand - tq) <= SNAP_S]
            if len(near) == 0:
                reasons.append("no_candidate")
            else:
                c = near[np.argmin(np.abs(near - tq))]
                dt_ms = f"{(c - tq) * 1000:+.2f}"
                if len(near) > 1:
                    reasons.append("two_candidates")
                if abs(c - tq) > DISAGREE_S:
                    reasons.append("big_disagreement")
                if snap == "move":
                    tq = float(c)
        if n["conf"] in ("weak", "none"):
            reasons.append(f"attack_{n['conf']}")
        if n["conf"] in ("recovered", "extra", "tap"):
            reasons.append(n["conf"])
        if ambiguous:
            reasons.append("ambiguous_piece")
        labels.append((tq, piece))
        if reasons:
            review.append(dict(time_s=tq, piece_id=piece, status="labeled", reason=";".join(reasons),
                               ref_time_s=f"{n['t']:.6f}", candidate_dt_ms=dt_ms,
                               detail=f"midi {n['midi']}, coarse {1000 * (n['t'] - n['coarse']):+.1f} ms"))
    for g in misses:
        review.append(dict(time_s=a * g["gt"] + b, piece_id="", status="not_labeled",
                           reason="sheet_note_not_found", ref_time_s=f"{g['gt']:.6f}", candidate_dt_ms="",
                           detail="written " + "+".join(dc.cp.drum_name(m) for m in g["mids"])))
    labels = write_outputs(sdir, labels, review, duration)
    meta = dict(alignment={k: v for k, v in alignment.items() if k != "windows"},
                windows=alignment["windows"], ref_round_trip_s=ri["rt"],
                ref_round_trip_source=ri["rt_source"], sheet_offset_ref_s=ri["offset"], snap=snap,
                n_labels=len(labels), n_review=len(review))
    (Path(take_dir) / f"align_{sdir.name}.json").write_text(json.dumps(meta, indent=1) + "\n")
    n_check = sum(r["severity"] == "check" for r in review)
    log(f"wrote {len(labels)} labels to {sdir / 'labels.csv'}; {len(review)} rows in "
        f"{sdir / 'labels_review.csv'}, {n_check} marked severity=check (listen to those)")
    return labels, review, alignment


def attack_features(x, sr, t):
    i = int(t * sr)
    seg = lambda a, b: x[max(0, i + int(a * sr)):max(1, i + int(b * sr))]  # noqa: E731
    rms = lambda v: float(np.sqrt(np.mean(v * v)) + 1e-9)  # noqa: E731
    level = rms(seg(0, 0.05))
    attack = rms(seg(0, 0.015)) / rms(seg(-0.025, -0.005))
    v = seg(0.02, 0.08)
    voiced = 0.0
    if len(v) > int(0.03 * sr):
        v = v - v.mean()
        ac = np.correlate(v, v, "full")[len(v) - 1:]
        lo, hi = int(sr / 400), int(sr / 80)
        if ac[0] > 0 and hi < len(ac):
            voiced = float(ac[lo:hi].max() / ac[0])
    return level, attack, voiced


def level_onsets(x, sr, rise_db=15.0, lookback_s=0.04, refractory_s=0.15, hop_s=0.002,
                 decay_db_s=40.0, margin_db=3.0, resume_db=6.0, unmute_s=0.24, mute_db=-75.0, mute_drop_db=35.0):
    """Hit onsets of a one-drum take from the level alone: a frame whose level is rise_db above
    the quietest frame of the previous lookback_s starts a hit, if it also comes within margin_db
    of a peak follower that decays at decay_db_s (slower than a real drum decays). The Quest's
    noise gate makes a ringing tail drop out and pop back in; those pops jump too, but stay well
    under the follower, while the next real hit, even a soft one, reaches it. A rise that peaks
    less than resume_db above the ring just before the dip is the gate letting the same ring back
    in, not a stroke. The Quest also MUTES the mic outright (to the noise floor, about -90 dB)
    (or nearly: below mute_db, or mute_drop_db under the hit) roughly 0.1 s after a loud
    attack and un-mutes it ~0.05 s later, so a still-ringing drum
    seems to start again ~0.18 s after the hit, sometimes LOUDER (its gain comes back up): a rise
    within unmute_s of the last hit after a dip below mute_db is that, not a stroke (confirmed
    with the drummer, 2026-10-24: every take was single strokes). A real double stroke that
    close would be dropped too; the drum is still ringing then, so the Quest mutes it all the same. One hit
    per refractory_s. Each time is refined to where the waveform first reaches 10% of the
    hit's peak (about 1 ms). Returns [(time_s, peak)]."""
    hop = max(1, int(hop_s * sr))
    n = len(x) // hop
    lvl = 10 * np.log10(np.mean(x[:n * hop].reshape(n, hop) ** 2, axis=1) + 1e-12)
    back = max(1, int(lookback_s / hop_s))
    out, last, last_peak_db = [], -1e9, 0.0
    ax = np.abs(x)
    follow, step = -120.0, decay_db_s * hop / sr
    for i in range(back, n):
        prev, follow = follow - step, max(lvl[i], follow - step)
        if (lvl[i] - lvl[i - back:i].min() < rise_db or lvl[i] < prev - margin_db
                or (i * hop) / sr - last < refractory_s):
            continue
        ring = lvl[max(0, i - 2 * back):max(1, i - back // 2)].max()
        peak_db = lvl[i:i + 15].max()
        if peak_db - ring < resume_db:
            continue  # the gate dropped the ring out and let it back in: same hit, no new attack
        if out and (i * hop) / sr - last < unmute_s and lvl[int(last * sr / hop) + 5:i].min() < max(
                mute_db, last_peak_db - mute_drop_db):
            continue  # Quest muted the mic right after the last hit, then let its ring back in
        last_peak_db = lvl[i:i + 15].max()
        a, b = max(0, (i - back) * hop), min(len(x), (i + 15) * hop)  # hit peak within ~30 ms
        pk = a + int(np.argmax(ax[a:b]))
        thr = 0.1 * ax[pk]
        j = pk
        while j > a and ax[j - 1] >= thr or (j > a and np.any(ax[max(a, j - int(0.0015 * sr)):j] >= thr)):
            j -= 1
        t = j / sr
        last = t
        out.append((t, float(ax[pk])))
    return out


def label_single(session_dir, piece, kit, floor_db=-45.0, force=False, quiet=False, precursor_s=0.5):
    """Every hit of a one-drum take is `piece`. Hits come from level_onsets (Kevin's core missed
    and doubled real Quest kicks); very quiet ones go to the review file instead."""
    sdir = Path(session_dir)
    check_outputs(sdir, force)
    if piece not in kit["pieces"]:
        raise SystemExit(f"{piece!r} is not a piece in the kit config ({', '.join(kit['pieces'])})")
    x, sr = dc.read_mono(sdir / "audio.wav")
    fam = kit["pieces"][piece]
    cymbal = fam in ("crash", "ride")
    # Cymbals ring for seconds and the Quest's gate chops the ring into bursts; a slow follower
    # keeps those bursts from counting as hits, and their un-mute can come up to 0.3 s late; a
    # closed hi-hat dies fast and is played faster, so drums use a 0.24 s window. The first
    # second is the Start pinch, not a hit.
    hits = [h for h in level_onsets(x, sr, decay_db_s=5.0 if cymbal else 40.0, unmute_s=0.3 if cymbal else 0.24) if h[0] >= 1.0]
    ref = np.percentile([p for _, p in hits], 95) if hits else 1.0
    labels, review = [], []
    for t, peak in hits:
        db = 20 * np.log10(peak / ref)
        if db < floor_db - 15:
            continue  # mic noise between hits (about -80 dB on the Quest), not a drum
        voiced = attack_features(x, sr, t)[2]
        why = (["very_low"] if db < floor_db else []) + (["voiced"] if voiced > 0.7 and db < -10 and fam not in ("tom", "kick") else [])
        if why:  # voiced: pitched like speech and quieter than the hits (a ringing snare can be pitched too)
            review.append(dict(time_s=float(t), piece_id=piece, ref_time_s="", candidate_dt_ms="", status="excluded",
                               reason=";".join(why), detail=f"{db:+.1f} dB vs the take's loud hits, voicing {voiced:.2f}"))
        else:
            labels.append((float(t), piece))
    # A quiet sound shortly before a much louder hit is the stroke getting ready (beater or stick
    # moving, pedal squeak), not a stroke: on the real takes they sit 0.3-0.4 s before a hit,
    # ~40 dB under it. Quiet sounds spaced like strokes are kept: those are real soft hits.
    db_of = {t: 20 * np.log10(p / ref) for t, p in hits}
    times = sorted(t for t, _ in labels)
    for t, nxt in zip(times, times[1:]):
        if db_of[t] < -20 and nxt - t < precursor_s and db_of[nxt] - db_of[t] > 15:
            labels.remove((t, piece))
            review.append(dict(time_s=t, piece_id=piece, ref_time_s="", candidate_dt_ms="", status="excluded",
                               reason="precursor", detail=f"{db_of[t]:+.1f} dB, {1000 * (nxt - t):.0f} ms before a hit"))
    labels = write_outputs(sdir, labels, review, len(x) / sr)
    if not quiet:
        print(f"wrote {len(labels)} '{piece}' labels to {sdir / 'labels.csv'}; {len(review)} very quiet "
              f"onsets -> {sdir / 'labels_review.csv'} (a real soft hit belongs back in)", file=sys.stderr)
    return labels, review


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="mode", required=True)
    for name in ("score", "single"):
        s = sub.add_parser(name)
        s.add_argument("--session", required=True)
        s.add_argument("--recordings", help="default live_transcription/data/recordings")
        s.add_argument("--kit", help="kit config (default config/kit.local.json, else kit.example.json)")
        s.add_argument("--force", action="store_true", help="overwrite labels.csv / labels_review.csv")
        if name == "score":
            s.add_argument("--take", required=True, help="datacollection/takes/NAME (ref.wav + take.json)")
            s.add_argument("--snap", choices=("confirm", "move", "off"), default="confirm")
            s.add_argument("--windows", type=int, default=10)
        else:
            s.add_argument("--piece", required=True)
            s.add_argument("--floor-db", type=float, default=-45.0)
    a = p.parse_args()
    kit = dc.load_kit(a.kit)
    sdir = al.session_dir(a.session, a.recordings)
    if a.mode == "score":
        label_score(sdir, a.take, kit, a.snap, a.windows, a.force)
    else:
        label_single(sdir, a.piece, kit, a.floor_db, a.force)


if __name__ == "__main__":
    main()
