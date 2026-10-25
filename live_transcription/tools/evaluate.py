"""One-to-one event matching; time error is measured without fitted alignment."""

import argparse
import json
import numpy as np
from common import MIDIS, NAMES, read_notes


def match(reference, predicted, tolerance):
    r, p = sorted(reference), sorted(predicted)
    i = j = 0
    errors = []
    while i < len(r) and j < len(p):
        delta = p[j] - r[i]
        if delta < -tolerance:
            j += 1
        elif delta > tolerance:
            i += 1
        else:
            errors.append(delta)
            i += 1
            j += 1
    tp = len(errors)
    fp = len(p) - tp
    fn = len(r) - tp
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": tp / (tp + fp) if tp + fp else 0.0,
        "recall": tp / (tp + fn) if tp + fn else 0.0,
        "f1": 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 0.0,
        "onset_error_ms": {
            "median": float(np.median(errors) * 1000) if errors else None,
            "p95_absolute": float(np.percentile(np.abs(errors), 95) * 1000)
            if errors
            else None,
        },
    }


def evaluate(reference, predicted, candidate_times=None, merge_cymbals=False):
    if merge_cymbals:
        reference = sorted({(t, 49 if m == 51 else m) for t, m in reference})
        predicted = sorted({(t, 49 if m == 51 else m) for t, m in predicted})
    midis = MIDIS[:-1] if merge_cymbals else MIDIS
    unsupported = {m for _, m in reference + predicted} - set(midis)
    if unsupported:
        raise ValueError(
            f"Normalize notes first; unsupported MIDI: {sorted(unsupported)}"
        )
    report = {}
    for tolerance in (0.010, 0.050):
        classes = {
            NAMES[m]: match(
                [t for t, k in reference if k == m],
                [t for t, k in predicted if k == m],
                tolerance,
            )
            for m in midis
        }
        tp = sum(c["tp"] for c in classes.values())
        fp = sum(c["fp"] for c in classes.values())
        fn = sum(c["fn"] for c in classes.values())
        report[f"{int(tolerance * 1000)}ms"] = {
            "families": classes,
            "micro_precision": tp / (tp + fp) if tp + fp else 0.0,
            "micro_recall": tp / (tp + fn) if tp + fn else 0.0,
            "micro_f1": 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 0.0,
        }
    if candidate_times is not None:
        report["onset_detection"] = {
            f"{ms}ms": match(
                sorted({t for t, _ in reference}), candidate_times, ms / 1000
            )
            for ms in (10, 50)
        }
    return report


def evaluate_hits(reference, predicted_times):
    """Hit/no-hit: any drum counts. Reference hits at the same time (two families struck
    together) are one hit, as are predictions at the same time."""
    hits = sorted({t for t, _ in reference})
    predicted = sorted(set(predicted_times))
    return {f"{ms}ms": match(hits, predicted, ms / 1000) for ms in (10, 50)}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("reference")
    p.add_argument("played")
    p.add_argument("--merge-cymbals", action="store_true")
    a = p.parse_args()
    print(
        json.dumps(
            evaluate(
                read_notes(a.reference),
                read_notes(a.played),
                merge_cymbals=a.merge_cymbals,
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
