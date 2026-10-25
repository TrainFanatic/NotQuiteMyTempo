"""Compare models as hit/no-hit detectors on the same held-out sessions.

Every model is replayed natively with the Quest mute filter its sidecar .json records. A model
without a sidecar must be named with --default-mute, which applies the standard QUEST_MUTE.
A six-class model counts as a hit wherever it emits any family. The model-free
`transcribe_wav.py --onsets-only` path (every mute-filtered candidate) is included as a
reference. Micro totals per split at 10 and 50 ms.
"""

import argparse
import json
from pathlib import Path
from common import (
    ROOT,
    RATE,
    QUEST_MUTE,
    QuestMuteFilter,
    extract,
    events,
    load_kit,
    read_audio,
    read_labels,
    sha256,
)
from evaluate import evaluate_hits

SPLITS = ("validation", "test")


def trained_mute(model, default_mute):
    sidecar = Path(model).with_suffix(".json")
    if not sidecar.exists():
        if Path(model).stem not in default_mute:
            raise FileNotFoundError(
                f"{sidecar} missing; pass --default-mute {Path(model).stem} to use QUEST_MUTE"
            )
        return QUEST_MUTE
    return json.loads(sidecar.read_text())["training_options"]["quest_mute_filter"]


def totals(sessions, key):
    result = {}
    for tol in ("10ms", "50ms"):
        tp = sum(s[key][tol]["tp"] for s in sessions)
        fp = sum(s[key][tol]["fp"] for s in sessions)
        fn = sum(s[key][tol]["fn"] for s in sessions)
        result[tol] = {
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "precision": tp / (tp + fp) if tp + fp else 0.0,
            "recall": tp / (tp + fn) if tp + fn else 0.0,
            "f1": 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 0.0,
        }
    return result


def compare(recordings, kit_path, models, default_mute=()):
    kit = load_kit(kit_path)
    names = [Path(m).stem for m in models] + ["onsets_only"]
    if len(set(names)) != len(names):
        raise ValueError("Model file names must be distinct")
    blobs = {Path(m).stem: (Path(m).read_bytes(), trained_mute(m, default_mute)) for m in models}
    result = {
        "models": {
            stem: {"path": str(m), "sha256": sha256(m), "mute_filter": blobs[stem][1]}
            for stem, m in zip(names, models)
        },
        "onsets_only_mute_filter": QUEST_MUTE,
        "splits": {},
    }
    for split in SPLITS:
        sessions = []
        for name in kit["splits"][split]:
            folder = Path(recordings) / name
            audio = read_audio(folder / "audio.wav")
            truth = read_labels(folder / "labels.csv", kit, len(audio) / RATE)
            row = {
                "session": name,
                "labels_sha256": sha256(folder / "labels.csv"),
                "onsets_only": evaluate_hits(
                    truth,
                    [
                        c.time_s
                        for c in extract(audio, mute=QuestMuteFilter(**QUEST_MUTE))
                        if c.time_s >= 0
                    ],
                ),
            }
            for stem, (blob, mute) in blobs.items():
                classified = extract(audio, blob, mute=QuestMuteFilter(**mute) if mute else None)
                row[stem] = evaluate_hits(truth, [t for t, _ in events(classified)])
            sessions.append(row)
        result["splits"][split] = {
            "sessions": sessions,
            "totals": {n: totals(sessions, n) for n in names},
        }
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--recordings", default=ROOT / "data" / "recordings", type=Path)
    p.add_argument("--kit", required=True, type=Path)
    p.add_argument("--model", action="append", required=True, type=Path)
    p.add_argument("--output", required=True, type=Path)
    p.add_argument(
        "--default-mute",
        action="append",
        default=[],
        help="model file stem without a sidecar .json to replay with QUEST_MUTE",
    )
    a = p.parse_args()
    if a.output.exists():
        raise FileExistsError(f"{a.output} exists; choose a new path")
    result = compare(a.recordings, a.kit, a.model, a.default_mute)
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(result, indent=2) + "\n")
    for split, block in result["splits"].items():
        for name, t in block["totals"].items():
            for tol, m in t.items():
                print(
                    f"{split:10s} {name:22s} {tol:>4s}  P={m['precision']:.3f} "
                    f"R={m['recall']:.3f} F1={m['f1']:.3f}  TP/FP/FN={m['tp']}/{m['fp']}/{m['fn']}"
                )


if __name__ == "__main__":
    main()
