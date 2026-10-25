"""Train one L2 logistic hit/no-hit classifier on the native onset candidates.

Same sessions, candidates, label assignment (nearest candidate within 20 ms), Quest mute filter
and validation search as train_model.py, but the six family targets are merged into one: a
candidate is positive when any drum was labelled there. The exported model has a single class,
MIDI 0 "onset" (rushingOrDragging/file_format.md section 10), so it says when a hit happened,
not which drum. Evaluation is hit/no-hit; see compare_hit_detection.py for the comparison with
the six-class model.
"""

import argparse
import hashlib
import json
import platform
import time
from pathlib import Path
import numpy as np
import sklearn
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import f1_score
from sklearn.preprocessing import StandardScaler
from common import (
    ROOT,
    RATE,
    FEATURES,
    ONSET_MIDI,
    ONSET_NAME,
    QuestMuteFilter,
    extract,
    events,
    export_model,
    load_kit,
    onset_line,
    sha256,
    peak_rss_bytes,
)
from evaluate import evaluate_hits
from train_model import C_GRID, MUTE, dataset, stack

THRESHOLDS = np.linspace(0.1, 0.9, 17)


def write_onsets(path, times, description):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    text = f"# {description}\n# time_s  midi  name\n"
    text += "".join(onset_line(t) for t in sorted(set(times)))
    p.write_text(text, encoding="utf-8", newline="\n")


def train(recordings, kit_path, model_path, report_path, mute=MUTE, c_grid=C_GRID):
    model_path, report_path = Path(model_path), Path(report_path)
    if report_path.resolve() == model_path.with_suffix(".json").resolve():
        raise ValueError("The report path would overwrite the model's sidecar .json")
    if model_path.exists() or model_path.with_suffix(".json").exists() or report_path.exists():
        raise FileExistsError("Choose new model/report paths to preserve prior results")
    options = {"quest_mute_filter": mute, "c_grid": list(c_grid), "target": "any_drum"}
    kit = load_kit(kit_path)
    data, manifest = dataset(recordings, kit, mute)
    tx, ty = stack(data["train"], "x"), stack(data["train"], "y").max(axis=1)
    vx, vy = stack(data["validation"], "x"), stack(data["validation"], "y").max(axis=1)
    for split, y in [("train", ty), ("validation", vy)]:
        if not len(y) or len(np.unique(y)) != 2:
            raise ValueError(
                f"{split}: needs both labelled hits on candidates and unlabelled candidates"
            )
    scaler = StandardScaler().fit(tx)
    xt, xv = scaler.transform(tx), scaler.transform(vx)
    best = None
    for regularization in c_grid:
        clf = LogisticRegression(
            C=regularization, solver="liblinear", max_iter=2000, random_state=0
        )
        clf.fit(xt, ty)
        prob = clf.predict_proba(xv)[:, 1]
        for threshold in THRESHOLDS:
            score = f1_score(vy, prob >= threshold, zero_division=0)
            # Deterministic ties prefer a higher threshold and smaller C, as train_model.py.
            key = (score, float(threshold), -regularization)
            if best is None or key > best[0]:
                best = (key, clf, float(threshold), regularization)
    (score, _, _), clf, threshold, regularization = best
    selection = {"C": regularization, "threshold": threshold, "validation_candidate_f1": score}
    blob = export_model(
        scaler.mean_, scaler.scale_, clf.coef_, clf.intercept_, [threshold], midis=(ONSET_MIDI,)
    )
    replay_mute = lambda: QuestMuteFilter(**mute) if mute else None
    report = {
        "schema_version": 1,
        "target": "hit/no-hit (any drum); one class, MIDI 0 onset",
        "sessions": manifest,
        "selection": selection,
        "training_options": options,
        "splits": {},
        "limitations": [
            "Counts any drum as a hit; cannot judge whether the expected drum was played",
            "Cannot recover hits the onset detector never proposed (see onsets_only recall)",
            "Desktop measurements, not Quest profiling",
            "Human annotation quality limits timing claims",
        ],
        "software": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "sklearn": sklearn.__version__,
        },
        "model_sha256": hashlib.sha256(blob).hexdigest(),
    }
    played = {}
    for split, sessions in data.items():
        reports = []
        for s in sessions:
            before = time.perf_counter()
            classified = extract(s["audio"], blob, mute=replay_mute())
            elapsed = time.perf_counter() - before
            expected = (
                clf.predict_proba(scaler.transform(s["x"]))[:, 1] if len(s["x"]) else np.empty(0)
            )
            actual = np.array([c.probability[0] for c in classified])
            if len(actual) != len(expected):
                raise RuntimeError("Native replay produced a different candidate count")
            parity = float(np.max(np.abs(expected - actual))) if len(actual) else 0.0
            if parity > 2e-5:
                raise RuntimeError(f"Python/native inference mismatch {parity}")
            times = [t for t, _ in events(classified)]
            played[(split, s["name"])] = times
            reports.append(
                {
                    "session": s["name"],
                    "model": evaluate_hits(s["truth"], times),
                    "onsets_only": evaluate_hits(
                        s["truth"], [c.time_s for c in s["candidates"] if c.time_s >= 0]
                    ),
                    "native_python_max_probability_error": parity,
                    "desktop_processing_s": elapsed,
                    "audio_duration_s": len(s["audio"]) / RATE,
                }
            )
        report["splits"][split] = reports
    report["desktop_process_peak_rss_bytes"] = peak_rss_bytes()
    model_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    model_path.write_bytes(blob)
    metadata = {
        "schema_version": 1,
        "sample_rate": RATE,
        "feature_schema": 1,
        "features": FEATURES,
        "lookahead_samples": 7680,
        "families": [ONSET_NAME],
        "midis": [ONSET_MIDI],
        "model_sha256": report["model_sha256"],
        "kit": kit,
        "kit_sha256": sha256(kit_path),
        "sessions": manifest,
        "selection": selection,
        "training_options": options,
        "software": report["software"],
        "report": str(report_path),
        "device_validated": False,
    }
    model_path.with_suffix(".json").write_text(json.dumps(metadata, indent=2) + "\n")
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    for split, sessions in data.items():
        for s in sessions:
            dest = report_path.parent / report_path.stem / split
            write_onsets(
                dest / (s["name"] + ".truth.drums.txt"),
                [t for t, _ in s["truth"]],
                "human annotations, any drum",
            )
            write_onsets(
                dest / (s["name"] + ".played.drums.txt"),
                played[(split, s["name"])],
                "hit/no-hit model, drum unknown (midi 0)",
            )
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--recordings", default=ROOT / "data" / "recordings", type=Path)
    p.add_argument("--kit", required=True, type=Path)
    p.add_argument("--model", default=ROOT / "models" / "quest2_onset_v1.bin", type=Path)
    p.add_argument("--report", default=ROOT / "reports" / "quest2_onset_v1.json", type=Path)
    p.add_argument(
        "--mute-window-ms",
        type=float,
        default=MUTE["window_s"] * 1000,
        help="QuestMuteFilter on the candidates, as train_model.py (0 = off)",
    )
    p.add_argument("--mute-db", type=float, default=MUTE["mute_db"])
    p.add_argument("--mute-drop-db", type=float, default=MUTE["drop_db"])
    p.add_argument(
        "--c-grid",
        default=",".join(f"{c:g}" for c in C_GRID),
        help="comma-separated C values searched on validation",
    )
    a = p.parse_args()
    mute = (
        {
            "window_s": a.mute_window_ms / 1000,
            "mute_db": a.mute_db,
            "drop_db": a.mute_drop_db if a.mute_drop_db >= 0 else None,
        }
        if a.mute_window_ms > 0
        else None
    )
    train(
        a.recordings,
        a.kit,
        a.model,
        a.report,
        mute=mute,
        c_grid=tuple(float(c) for c in a.c_grid.split(",")),
    )
    print(f"Model: {a.model}\nHeld-out report: {a.report}")


if __name__ == "__main__":
    main()
