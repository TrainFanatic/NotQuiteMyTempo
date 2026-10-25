"""Build a reproducible training set from original takes and mild audio perturbations.

The original train/validation/test split is kept by source recording. Only train takes
are perturbed. Validation and test are exact copies of the original WAVs and labels.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import lfilter

from common import RATE, load_kit, read_audio, read_labels

ROOT = Path(__file__).resolve().parents[1]
VARIANTS = {
    "gain_m3db": ("gain", -3.0),
    "gain_m6db": ("gain", -6.0),
    "gain_m12db": ("gain", -12.0),
    "bass_m3db": ("low_shelf", -3.0),
    "bass_p3db": ("low_shelf", 3.0),
    "treble_m3db": ("high_shelf", -3.0),
    "treble_p3db": ("high_shelf", 3.0),
}


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def shelf_coefficients(kind: str, gain_db: float, rate: int = RATE):
    """RBJ shelf, slope 1; W3C Audio EQ Cookbook, 2021, sections low/highShelf."""
    frequency = 200.0 if kind == "low_shelf" else 4000.0
    a = 10 ** (gain_db / 40.0)
    w = 2 * math.pi * frequency / rate
    cosine, sine = math.cos(w), math.sin(w)
    alpha = sine / math.sqrt(2.0)
    q = 2 * math.sqrt(a) * alpha
    if kind == "low_shelf":
        b = [a * ((a + 1) - (a - 1) * cosine + q),
             2 * a * ((a - 1) - (a + 1) * cosine),
             a * ((a + 1) - (a - 1) * cosine - q)]
        d = [(a + 1) + (a - 1) * cosine + q,
             -2 * ((a - 1) + (a + 1) * cosine),
             (a + 1) + (a - 1) * cosine - q]
    elif kind == "high_shelf":
        b = [a * ((a + 1) + (a - 1) * cosine + q),
             -2 * a * ((a - 1) + (a + 1) * cosine),
             a * ((a + 1) + (a - 1) * cosine - q)]
        d = [(a + 1) - (a - 1) * cosine + q,
             2 * ((a - 1) - (a + 1) * cosine),
             (a + 1) - (a - 1) * cosine - q]
    else:
        raise ValueError(kind)
    return np.asarray(b) / d[0], np.asarray(d) / d[0]


def perturb(audio: np.ndarray, kind: str, gain_db: float) -> np.ndarray:
    if kind == "gain":
        changed = audio.astype(np.float64) * 10 ** (gain_db / 20.0)
    else:
        b, a = shelf_coefficients(kind, gain_db)
        # Real Quest transients exceed full scale under +3 dB shelving. A fixed
        # 6 dB headroom reduction preserves the shelf contrast without clipping.
        headroom = 10 ** (-6 / 20.0) if gain_db > 0 else 1.0
        changed = lfilter(b, a, audio.astype(np.float64) * headroom)
    peak = float(np.max(np.abs(changed)))
    if not math.isfinite(peak) or peak >= 1.0:
        raise ValueError(f"perturbation {kind} {gain_db:+g} dB exceeds float audio range: {peak}")
    return changed.astype(np.float32)


def build(recordings: Path, kit_path: Path, output: Path) -> dict:
    recordings, kit_path, output = map(Path, (recordings, kit_path, output))
    if output.exists():
        raise FileExistsError(f"{output} already exists; choose a new output directory")
    kit = load_kit(kit_path)
    splits = kit["splits"]
    names = [name for split in ("train", "validation", "test") for name in splits[split]]
    if len(names) != len(set(names)):
        raise ValueError("A source session occurs in more than one split")
    staging = output.with_name(output.name + ".building")
    if staging.exists():
        raise FileExistsError(f"staging folder exists: {staging}")
    staging.mkdir(parents=True)
    manifest = []
    derived_splits = {"train": [], "validation": [], "test": []}
    try:
        for split in ("train", "validation", "test"):
            for name in splits[split]:
                source = recordings / name
                audio_path, labels_path = source / "audio.wav", source / "labels.csv"
                audio = read_audio(audio_path)
                read_labels(labels_path, kit, len(audio) / RATE)
                source_hash, labels_hash = digest(audio_path), digest(labels_path)
                original = staging / "recordings" / name
                original.mkdir(parents=True)
                shutil.copy2(audio_path, original / "audio.wav")
                shutil.copy2(labels_path, original / "labels.csv")
                derived_splits[split].append(name)
                manifest.append(dict(split=split, session=name, parent=name, variant="original",
                                     source_audio_sha256=source_hash, audio_sha256=source_hash,
                                     labels_sha256=labels_hash, frames=len(audio), sample_rate=RATE,
                                     peak=float(np.max(np.abs(audio)))))
                if split != "train":
                    continue
                for variant, (kind, gain_db) in VARIANTS.items():
                    changed = perturb(audio, kind, gain_db)
                    derived_name = f"{name}__{variant}"
                    target = staging / "recordings" / derived_name
                    target.mkdir(parents=True)
                    sf.write(target / "audio.wav", changed, RATE, subtype="FLOAT")
                    shutil.copy2(labels_path, target / "labels.csv")
                    derived_splits[split].append(derived_name)
                    manifest.append(dict(split=split, session=derived_name, parent=name,
                                         variant=variant, kind=kind, gain_db=gain_db,
                                         headroom_db=-6.0 if kind != "gain" and gain_db > 0 else 0.0,
                                         source_audio_sha256=source_hash,
                                         audio_sha256=digest(target / "audio.wav"),
                                         labels_sha256=labels_hash, frames=len(audio),
                                         sample_rate=RATE, peak=float(np.max(np.abs(changed)))))
        augmented_kit = dict(kit, splits=derived_splits)
        (staging / "kit.json").write_text(json.dumps(augmented_kit, indent=2) + "\n")
        report = dict(schema_version=1, source_kit_sha256=digest(kit_path),
                      variants=VARIANTS, entries=manifest,
                      note="Only train has derivatives; validation and test audio and labels are byte-identical to originals.")
        (staging / "manifest.json").write_text(json.dumps(report, indent=2) + "\n")
        staging.rename(output)
        return report
    except BaseException:
        shutil.rmtree(staging)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--recordings", type=Path, default=ROOT / "data" / "recordings")
    parser.add_argument("--kit", type=Path, default=ROOT / "config" / "kit.local.json")
    parser.add_argument("--output", type=Path, default=ROOT / "perturbed_data")
    args = parser.parse_args()
    report = build(args.recordings, args.kit, args.output)
    print(f"Created {args.output}: {len(report['entries'])} sessions ")


if __name__ == "__main__":
    main()
