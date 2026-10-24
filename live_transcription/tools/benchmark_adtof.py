"""Offline five-family ADTOF benchmark. Run in the dedicated legacy environment."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ADTOF_COMMIT = "b3968fb332f69b65ee07c089fc62f436503755db"


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("audio", type=Path)
    p.add_argument(
        "--reference", required=True, type=Path, help="human six-family .drums.txt"
    )
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--report", required=True, type=Path)
    a = p.parse_args()
    # Import after parsing so --help works outside the optional environment.
    os.environ.setdefault("CUDA_VISIBLE_DEVICES", "-1")
    os.environ["PATH"] = (
        str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", "")
    )
    import adtof
    from adtof.model.model import Model
    from adtof.model.track import Track
    from adtof.model.peakPicking import PeakPicking
    from common import NORMALIZE, read_notes, write_notes, sha256
    from evaluate import evaluate

    checkout = Path(adtof.__file__).resolve().parents[1]
    commit = subprocess.check_output(
        ["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True
    ).strip()
    if commit != ADTOF_COMMIT:
        raise RuntimeError(f"Expected ADTOF {ADTOF_COMMIT}, got {commit}")
    if a.output.exists() or a.report.exists():
        raise FileExistsError("Choose fresh output/report paths")
    start = time.perf_counter()
    model, hparams = Model.modelFactory(
        modelName="Frame_RNN", scenario="adtofAll", fold=0
    )
    if not model.weightLoadedFlag:
        raise RuntimeError(
            "Pretrained checkpoint missing; refusing untrained predictions"
        )
    # Require all inference objects to match; optimizer state is irrelevant here.
    restore = model.model.load_weights(model.path)
    restore.assert_existing_objects_matched()
    restore.expect_partial()
    if hparams["labels"] != [35, 38, 47, 42, 49]:
        raise RuntimeError("Unexpected checkpoint label order")
    # Track invokes upstream mir.preProcess: 44.1 kHz, 2048-point FFT,
    # 100 fps logarithmic filterbank. Do not substitute the native features.
    track = Track(str(a.audio.resolve()), **hparams)
    dense = model.predict(track, **hparams)
    peaks = PeakPicking()
    peaks.setParameters(**hparams)
    sparse = peaks.predict([dense], **hparams)[0]
    notes = sorted(
        (float(t), NORMALIZE[int(m)])
        for m, times in sparse.items()
        for t in times
        if t >= 0
    )
    write_notes(a.output, notes, "ADTOF offline; crash and ride merged into MIDI 49")
    report = evaluate(read_notes(a.reference), notes, merge_cymbals=True)
    checkpoint = (
        checkout / "adtof" / "models" / "Frame_RNN_adtofAll_0.data-00000-of-00001"
    )
    report.update(
        {
            "commit": commit,
            "checkpoint_sha256": sha256(checkpoint),
            "audio_sha256": sha256(a.audio),
            "elapsed_s": time.perf_counter() - start,
            "mode": "offline bidirectional, five families",
            "license": "CC-BY-NC-SA-4.0",
            "source": "https://github.com/MZehren/ADTOF",
        }
    )
    a.report.parent.mkdir(parents=True, exist_ok=True)
    a.report.write_text(json.dumps(report, indent=2) + "\n")
    print(f"ADTOF predictions: {a.output}; report: {a.report}")


if __name__ == "__main__":
    main()
