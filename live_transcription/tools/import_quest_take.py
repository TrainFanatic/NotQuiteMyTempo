"""Validate and import one exported Quest recorder take without changing the original."""

import argparse
import csv
import json
import math
import re
import shutil
from pathlib import Path

import soundfile as sf

from common import ROOT, extract, read_audio
from prepare_recording import prepare


def import_take(wav_path, metadata_path, session, recordings=None):
    wav_path = Path(wav_path)
    metadata_path = Path(metadata_path)
    recordings = Path(recordings or ROOT / "data" / "recordings")
    if metadata_path.name != f"{wav_path.stem}.capture.json":
        raise ValueError("WAV and metadata filenames must have the same take ID")
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", session):
        raise ValueError("Session must contain only letters, numbers, _ or -")
    meta = json.loads(metadata_path.read_text(encoding="utf-8"))
    if meta.get("valid_for_training") is not True:
        raise ValueError(
            "Quest marked this take invalid; check dropped frames/write errors and re-record"
        )
    if meta.get("discarded_frames") != 0 or meta.get("write_failed") is not False:
        raise ValueError("Dropped frames or write failure in Quest capture; re-record")
    if meta.get("mic_only") is not True:
        raise ValueError("Recording metadata does not confirm mic-only capture")
    info = sf.info(wav_path)
    if info.format != "WAV" or info.subtype != "FLOAT":
        raise ValueError("Expected uncompressed float32 Quest recorder WAV")
    if (
        info.frames != meta.get("frames")
        or info.samplerate != meta.get("sample_rate")
        or info.channels != meta.get("channels")
        or info.frames <= 0
    ):
        raise ValueError("WAV duration, rate or channel count disagrees with metadata")
    if not isinstance(meta.get("peak"), (float, int)) or not math.isfinite(
        meta["peak"]
    ):
        raise ValueError("Missing or invalid capture peak")
    if meta["peak"] < 0.005:
        raise ValueError(
            "Recording is nearly silent; check Quest microphone permission and capture"
        )
    target = recordings / session
    if target.exists():
        raise FileExistsError(f"Session already exists: {target}")
    target.mkdir(parents=True)
    shutil.copy2(wav_path, target / "source.wav")
    shutil.copy2(metadata_path, target / "source.capture.json")
    notes = "; ".join(
        str(meta.get(k, ""))
        for k in ("app", "kit_id", "room_and_settings", "player_id")
    )
    report = prepare(target / "source.wav", target / "audio.wav", notes)
    candidates = extract(read_audio(target / "audio.wav"))
    with (target / "suggestions.csv").open("x", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["time_s", "piece_id"])
        writer.writerows((f"{c.time_s:.6f}", "REVIEW_REQUIRED") for c in candidates)
    return target, report, len(candidates)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wav", type=Path, help="exported Quest WAV")
    parser.add_argument("metadata", type=Path, help="matching capture JSON")
    parser.add_argument(
        "--session", required=True, help="local session folder, e.g. quest_train_01"
    )
    parser.add_argument("--recordings", type=Path, default=ROOT / "data" / "recordings")
    args = parser.parse_args()
    target, report, count = import_take(
        args.wav, args.metadata, args.session, args.recordings
    )
    print(f"Imported: {target}")
    print(
        f"Duration: {report['duration_s']:.2f}s; peak: {report['peak']:.3f}; suggestions: {count}"
    )
    if report["near_clip_fraction"] > 0:
        print(
            f"Check clipping: {report['near_clip_fraction']:.3%} of source samples near full scale"
        )
    print(
        "Next: review the whole WAV and save human-checked labels.csv in this folder."
    )


if __name__ == "__main__":
    main()
