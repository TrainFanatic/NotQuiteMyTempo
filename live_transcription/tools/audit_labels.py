"""Audit labels.csv against the native onset candidates before training on them.

For every session in the kit's splits it reports what a hit/no-hit model would learn wrongly:
labels with no candidate within 20 ms (never trainable, always a miss), the timing offset between
labels and candidates, and loud unlabelled candidates outside a labelled hit's ring (possible
missing labels, which would be trained as "no hit" and counted as false positives).
"""

import argparse
import json
import math
from pathlib import Path
import numpy as np
from common import (
    ROOT,
    RATE,
    QUEST_MUTE,
    QuestMuteFilter,
    extract,
    load_kit,
    read_audio,
    read_labels,
)

ASSIGN_S = 0.020  # train_model.py assigns a label to its nearest candidate within 20 ms
RING_S = 0.25  # unlabelled candidates this soon after a labelled hit are treated as its ring
LOUD_DB = -12.0  # unlabelled candidate peak within this of the session's median hit peak


def peak_db(audio, t, window_s=0.03):
    start = max(0, int(round(t * RATE)))
    segment = audio[start : start + int(window_s * RATE)]
    if not len(segment):
        raise ValueError(f"No audio after {t:.3f} s")
    return 20 * math.log10(float(np.max(np.abs(segment))) + 1e-12)


def audit_session(folder, kit, mute=QUEST_MUTE):
    audio = read_audio(folder / "audio.wav")
    truth = read_labels(folder / "labels.csv", kit, len(audio) / RATE)
    hit_times = np.array(sorted({t for t, _ in truth}))
    candidates = extract(audio, mute=QuestMuteFilter(**mute) if mute else None)
    times = np.array([c.time_s for c in candidates])
    offsets, unmatched = [], []
    for t in hit_times:
        if not len(times):
            unmatched.append(round(float(t), 6))
            continue
        k = int(np.argmin(np.abs(times - t)))
        if abs(times[k] - t) <= ASSIGN_S:
            offsets.append((times[k] - t) * 1000)
        else:
            unmatched.append(round(float(t), 6))
    hit_peaks = [peak_db(audio, t) for t in hit_times]
    reference = float(np.median(hit_peaks)) if hit_peaks else None
    loud = []
    for t in times:
        if len(hit_times) and np.min(np.abs(hit_times - t)) <= ASSIGN_S:
            continue
        before = hit_times[hit_times < t]
        if len(before) and t - before[-1] <= RING_S:
            continue
        level = peak_db(audio, t)
        if reference is not None and level >= reference + LOUD_DB:
            loud.append(
                {"time_s": round(float(t), 6), "peak_vs_median_hit_db": round(level - reference, 1)}
            )
    gaps = np.diff(hit_times) * 1000 if len(hit_times) > 1 else np.array([])
    return {
        "session": folder.name,
        "labels": len(truth),
        "hit_times": len(hit_times),
        "candidates": len(candidates),
        "labels_without_candidate_20ms": unmatched,
        "label_to_candidate_offset_ms": {
            "median": float(np.median(offsets)) if offsets else None,
            "p95_absolute": float(np.percentile(np.abs(offsets), 95)) if offsets else None,
        },
        "min_gap_between_hits_ms": float(gaps.min()) if len(gaps) else None,
        "loud_unlabelled_candidates": loud,
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--recordings", default=ROOT / "data" / "recordings", type=Path)
    p.add_argument("--kit", required=True, type=Path)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    kit = load_kit(a.kit)
    result = {"assign_s": ASSIGN_S, "ring_s": RING_S, "loud_db": LOUD_DB, "splits": {}}
    for split in ("train", "validation", "test"):
        names = kit["splits"][split]
        result["splits"][split] = [audit_session(a.recordings / n, kit) for n in names]
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(result, indent=2) + "\n")
    for split, sessions in result["splits"].items():
        for s in sessions:
            print(
                f"{split:10s} {s['session']:20s} hits={s['hit_times']:3d} "
                f"cand={s['candidates']:4d} no_cand={len(s['labels_without_candidate_20ms']):2d} "
                f"offset_med={s['label_to_candidate_offset_ms']['median']:+.1f}ms "
                f"p95={s['label_to_candidate_offset_ms']['p95_absolute']:.1f}ms "
                f"loud_unlabelled={len(s['loud_unlabelled_candidates']):2d}"
            )


if __name__ == "__main__":
    main()
