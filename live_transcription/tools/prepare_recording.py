"""Convert without trimming or denoising; record capture provenance and smoke metrics."""

import argparse
import json
import math
from pathlib import Path
import numpy as np
import soundfile as sf
from scipy.signal import resample_poly
from common import RATE, sha256


def prepare(source, output, capture_notes=""):
    source, output = Path(source), Path(output)
    if source.resolve() == output.resolve():
        raise ValueError("Keep the original; choose a different output path")
    if output.exists():
        raise FileExistsError(output)
    a, rate = sf.read(source, dtype="float32", always_2d=True)
    if not a.size or not np.all(np.isfinite(a)) or np.max(np.abs(a)) > 1:
        raise ValueError("Invalid source PCM")
    mono = a.mean(axis=1)
    if rate != RATE:
        gcd = math.gcd(rate, RATE)
        mono = resample_poly(mono, RATE // gcd, rate // gcd)
    if np.max(np.abs(mono)) > 1:
        raise ValueError(
            "Resampling overshot full scale; provide a recording with more headroom"
        )
    # Float WAV preserves the samples without another integer quantization step.
    output.parent.mkdir(parents=True, exist_ok=True)
    sf.write(output, mono, RATE, subtype="FLOAT")
    report = {
        "source": str(source.resolve()),
        "source_sha256": sha256(source),
        "prepared_sha256": sha256(output),
        "source_sample_rate": rate,
        "source_channels": a.shape[1],
        "frames": len(mono),
        "sample_rate": RATE,
        "duration_s": len(mono) / RATE,
        "peak": float(np.max(np.abs(a))),
        "near_clip_fraction": float(np.mean(np.abs(a) >= 0.999)),
        "rms": float(np.sqrt(np.mean(a * a))),
        "capture_notes": capture_notes,
        "processing": "mono mean, polyphase resample only; no trim or denoise",
    }
    output.with_suffix(".capture.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("source")
    p.add_argument("output")
    p.add_argument(
        "--capture-notes",
        required=True,
        help="Quest app, mic settings, kit, room, headset position",
    )
    a = p.parse_args()
    print(json.dumps(prepare(a.source, a.output, a.capture_notes), indent=2))


if __name__ == "__main__":
    main()
