"""Train six independent L2 logistic classifiers using session-separated data."""

import argparse
import hashlib
import json
import platform
import time
from pathlib import Path
import numpy as np
import sklearn
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import f1_score, precision_recall_fscore_support
from sklearn.preprocessing import StandardScaler
from common import (
    ROOT,
    RATE,
    FEATURES,
    FAMILIES,
    MIDIS,
    extract,
    events,
    export_model,
    read_audio,
    read_labels,
    load_kit,
    sha256,
    peak_rss_bytes,
    write_notes,
)
from evaluate import evaluate


def dataset(recordings, kit):
    splits = kit.get("splits", {})
    seen = set()
    hashes = set()
    result = {}
    manifest = []
    for split in ("train", "validation", "test"):
        names = splits.get(split, [])
        if not names or not isinstance(names, list):
            raise ValueError(f"Provide sessions for {split}")
        sessions = []
        for name in names:
            if (
                not isinstance(name, str)
                or Path(name).name != name
                or name in (".", "..")
            ):
                raise ValueError("Session names must be directory names, without paths")
            if name in seen:
                raise ValueError(f"Session {name} appears in multiple splits")
            seen.add(name)
            folder = Path(recordings) / name
            audio = read_audio(folder / "audio.wav")
            digest = hashlib.sha256(audio.tobytes()).hexdigest()
            if digest in hashes:
                raise ValueError(
                    "Duplicate audio across sessions; split by independent recording"
                )
            hashes.add(digest)
            truth = read_labels(folder / "labels.csv", kit, len(audio) / RATE)
            before = time.perf_counter()
            candidates = extract(audio)
            elapsed = time.perf_counter() - before
            x = np.array(
                [list(c.features) for c in candidates], dtype=np.float32
            ).reshape(-1, FEATURES)
            y = np.zeros((len(candidates), len(MIDIS)), dtype=np.int32)
            times = np.array([c.time_s for c in candidates])
            # Assign each human-labelled onset only to its nearest candidate, never
            # to every candidate in the 160 ms context window. Overlaps are multi-label.
            for t, m in truth:
                if len(times):
                    index = int(np.argmin(np.abs(times - t)))
                    if abs(times[index] - t) <= 0.020:
                        y[index, MIDIS.index(m)] = 1
            sessions.append(
                {
                    "name": name,
                    "audio": audio,
                    "truth": truth,
                    "candidates": candidates,
                    "x": x,
                    "y": y,
                    "extraction_s": elapsed,
                }
            )
            manifest.append(
                {
                    "session": name,
                    "split": split,
                    "audio_sha256": sha256(folder / "audio.wav"),
                    "labels_sha256": sha256(folder / "labels.csv"),
                    "duration_s": len(audio) / RATE,
                    "onsets": len(candidates),
                    "human_family_hits": len(truth),
                    "assigned_labels": int(y.sum()),
                }
            )
        result[split] = sessions
    return result, manifest


def stack(sessions, key):
    return np.concatenate([s[key] for s in sessions], axis=0)


def train(recordings, kit_path, model_path, report_path):
    kit = load_kit(kit_path)
    data, manifest = dataset(recordings, kit)
    tx, ty = stack(data["train"], "x"), stack(data["train"], "y")
    vx, vy = stack(data["validation"], "x"), stack(data["validation"], "y")
    for split, y in [("train", ty), ("validation", vy)]:
        for k, family in enumerate(FAMILIES):
            if not len(y) or len(np.unique(y[:, k])) != 2:
                raise ValueError(
                    f"{split}: {family} needs both detected positive examples and negative candidates; inspect annotations/onsets"
                )
    for family, midi in zip(FAMILIES, MIDIS):
        if not any(m == midi for s in data["test"] for _, m in s["truth"]):
            raise ValueError(f"test: missing human-labelled {family} hits")
    scaler = StandardScaler().fit(tx)
    xt = scaler.transform(tx)
    xv = scaler.transform(vx)
    classifiers = []
    thresholds = []
    selections = []
    for k, family in enumerate(FAMILIES):
        best = None
        for regularization in (0.1, 1.0, 10.0):
            clf = LogisticRegression(
                C=regularization, solver="liblinear", max_iter=2000, random_state=0
            )
            clf.fit(xt, ty[:, k])
            prob = clf.predict_proba(xv)[:, 1]
            for threshold in np.linspace(0.1, 0.9, 17):
                score = f1_score(vy[:, k], prob >= threshold, zero_division=0)
                # Deterministic ties prefer a higher threshold and smaller C.
                key = (score, float(threshold), -regularization)
                if best is None or key > best[0]:
                    best = (key, clf, float(threshold), regularization)
        _, clf, threshold, regularization = best
        classifiers.append(clf)
        thresholds.append(threshold)
        selections.append(
            {
                "family": family,
                "C": regularization,
                "threshold": threshold,
                "validation_candidate_f1": best[0][0],
            }
        )
    weights = np.concatenate([c.coef_ for c in classifiers])
    bias = np.array([c.intercept_[0] for c in classifiers])
    blob = export_model(scaler.mean_, scaler.scale_, weights, bias, thresholds)
    # Native replay, not Python surrogate predictions, produces the final reports.
    report = {
        "schema_version": 1,
        "sessions": manifest,
        "selection": selections,
        "splits": {},
        "limitations": [
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
    for split, sessions in data.items():
        reports = []
        for s in sessions:
            before = time.perf_counter()
            classified = extract(s["audio"], blob)
            elapsed = time.perf_counter() - before
            predictions = events(classified)
            metrics = evaluate(s["truth"], predictions, [c.time_s for c in classified])
            expected = (
                np.column_stack(
                    [
                        c.predict_proba(scaler.transform(s["x"]))[:, 1]
                        for c in classifiers
                    ]
                )
                if len(s["x"])
                else np.empty((0, 6))
            )
            actual = np.array([list(c.probability)[:6] for c in classified]).reshape(
                -1, 6
            )
            parity = float(np.max(np.abs(expected - actual))) if len(actual) else 0.0
            if parity > 2e-5:
                raise RuntimeError(f"Python/native inference mismatch {parity}")
            classified_targets = (actual >= thresholds).astype(int)
            pr, rc, f1, _ = (
                precision_recall_fscore_support(
                    s["y"], classified_targets, average="micro", zero_division=0
                )
                if len(actual)
                else (0, 0, 0, None)
            )
            metrics["classification_at_candidates"] = {
                "precision": float(pr),
                "recall": float(rc),
                "f1": float(f1),
            }
            metrics["session"] = s["name"]
            metrics["native_python_max_probability_error"] = parity
            metrics["desktop_processing_s"] = elapsed
            metrics["audio_duration_s"] = len(s["audio"]) / RATE
            metrics["real_time_factor"] = elapsed / (len(s["audio"]) / RATE)
            delays = [(c.available_sample - c.sample) / RATE * 1000 for c in classified]
            metrics["core_latency_ms_p95"] = (
                float(np.percentile(delays, 95)) if delays else None
            )
            reports.append(metrics)
        report["splits"][split] = reports
    report["desktop_process_peak_rss_bytes"] = peak_rss_bytes()
    output = Path(model_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    report_path = Path(report_path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() or output.with_suffix(".json").exists() or report_path.exists():
        raise FileExistsError("Choose new model/report paths to preserve prior results")
    output.write_bytes(blob)
    metadata = {
        "schema_version": 1,
        "sample_rate": RATE,
        "feature_schema": 1,
        "features": FEATURES,
        "lookahead_samples": 7680,
        "families": list(FAMILIES),
        "midis": list(MIDIS),
        "model_sha256": report["model_sha256"],
        "kit": kit,
        "kit_sha256": sha256(kit_path),
        "sessions": manifest,
        "selection": selections,
        "software": report["software"],
        "report": str(report_path),
        "device_validated": False,
    }
    output.with_suffix(".json").write_text(json.dumps(metadata, indent=2) + "\n")
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    # Human truth and native output are also available for the existing comparer.
    for split, sessions in data.items():
        for s in sessions:
            dest = report_path.parent / report_path.stem / split
            write_notes(
                dest / (s["name"] + ".truth.drums.txt"),
                s["truth"],
                "human annotations, broad families",
            )
            write_notes(
                dest / (s["name"] + ".played.drums.txt"),
                events(extract(s["audio"], blob)),
            )
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--recordings", default=ROOT / "data" / "recordings", type=Path)
    p.add_argument("--kit", required=True, type=Path)
    p.add_argument("--model", default=ROOT / "models" / "quest2_v1.bin", type=Path)
    p.add_argument("--report", default=ROOT / "reports" / "quest2_v1.json", type=Path)
    a = p.parse_args()
    train(a.recordings, a.kit, a.model, a.report)
    print(f"Model: {a.model}\nHeld-out report: {a.report}")


if __name__ == "__main__":
    main()
