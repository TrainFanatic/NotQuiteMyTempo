import csv
import json
import re
import subprocess
import sys
import numpy as np
import pytest
import soundfile as sf
from common import ROOT, read_notes, write_notes, read_labels, FAMILIES
from normalize_reference import normalize
from prepare_recording import prepare
from import_quest_take import import_take
from evaluate import evaluate, match
from train_model import train, dataset

COMPARE = ROOT.parent / "rushingOrDragging"
sys.path.insert(0, str(COMPARE))
from live_compare import LiveMatcher
from compare_performance import Hit


def test_reference_normalization_regression():
    sheet = [Hit(1.0, 48), Hit(1.075, 47)]
    hit, error = LiveMatcher(sheet).feed(1.0, 47)
    assert error == pytest.approx(-0.075)
    notes, _ = normalize([(1.0, 48), (1.075, 47), (1.0, 50)])
    assert notes == [(1.0, 47), (1.075, 47)]
    hit, error = LiveMatcher([Hit(t, m) for t, m in notes]).feed(1.0, 47)
    assert error == 0
    with pytest.raises(ValueError):
        normalize([(1.0, 56)])
    assert normalize([(1.0, 56)], True) == ([], [56])


def test_preparation_and_label_validation(tmp_path):
    original = tmp_path / "original.wav"
    prepared = tmp_path / "session" / "audio.wav"
    x = np.zeros((44100, 2), dtype=np.float32)
    x[22050:22100] = 0.3
    sf.write(original, x, 44100, subtype="FLOAT")
    report = prepare(original, prepared, "test capture")
    y, rate = sf.read(prepared)
    assert rate == 48000 and len(y) == 48000
    assert abs(np.argmax(y) / rate - 0.5) < 0.003
    assert report["source_channels"] == 2 and report["near_clip_fraction"] == 0
    with pytest.raises(FileExistsError):
        prepare(original, prepared)
    kit = {"pieces": {"bd": "kick"}}
    labels = tmp_path / "labels.csv"
    labels.write_text("time_s,piece_id\n0.5,bd\n")
    assert read_labels(labels, kit, 1) == [(0.5, 36)]
    labels.write_text("time_s,piece_id\n1.5,bd\n")
    with pytest.raises(ValueError):
        read_labels(labels, kit, 1)


def test_quest_take_import_checks_capture_and_preserves_source(tmp_path):
    wav = tmp_path / "take_01.wav"
    metadata = tmp_path / "take_01.capture.json"
    x = np.zeros((48000, 2), dtype=np.float32)
    x[12000:12100] = 0.3
    sf.write(wav, x, 48000, subtype="FLOAT")
    info = {
        "valid_for_training": True,
        "discarded_frames": 0,
        "write_failed": False,
        "mic_only": True,
        "frames": len(x),
        "sample_rate": 48000,
        "channels": 2,
        "peak": 0.3,
    }
    metadata.write_text(json.dumps(info))
    info["discarded_frames"] = 1
    metadata.write_text(json.dumps(info))
    with pytest.raises(ValueError, match="Dropped frames"):
        import_take(wav, metadata, "session", tmp_path / "recordings")
    assert not (tmp_path / "recordings" / "session").exists()
    info["discarded_frames"] = 0
    metadata.write_text(json.dumps(info))
    target, report, count = import_take(wav, metadata, "session", tmp_path / "recordings")
    assert (target / "source.wav").read_bytes() == wav.read_bytes()
    assert sf.info(target / "audio.wav").channels == 1
    assert report["source_channels"] == 2
    assert count >= 1
    assert "REVIEW_REQUIRED" in (target / "suggestions.csv").read_text()


def test_evaluation_missing_extra_and_cymbal_merge():
    r = evaluate([(1.0, 36), (2.0, 49)], [(1.02, 36), (2.0, 51), (3.0, 38)])
    assert r["10ms"]["micro_f1"] == 0
    assert r["50ms"]["families"]["kick"]["tp"] == 1
    merged = evaluate([(2.0, 49)], [(2.0, 51)], merge_cymbals=True)
    assert merged["10ms"]["micro_f1"] == 1
    assert match([0.0, 0.075], [0.001, 0.076], 0.01)["tp"] == 2


def test_replay_canonical_output_and_live_pipe(tmp_path, audio, all_classes_model):
    wav = tmp_path / "audio.wav"
    model = tmp_path / "fixture.bin"
    output = tmp_path / "played.drums.txt"
    sf.write(wav, audio, 48000, subtype="FLOAT")
    model.write_bytes(all_classes_model)
    command = [
        sys.executable,
        str(ROOT / "tools/transcribe_wav.py"),
        str(wav),
        "--model",
        str(model),
        "--output",
        str(output),
        "--chunk",
        "127",
    ]
    producer = subprocess.run(command, capture_output=True, text=True, check=True)
    assert output.read_text() == producer.stdout
    lines = producer.stdout.splitlines()
    assert lines[1] == "# time_s  midi  name"
    assert len(lines) == 38
    assert all(re.fullmatch(r"\d+\.\d{6}  \d+  [a-z0-9_]+", s) for s in lines[2:])
    sheet = tmp_path / "sheet.drums.txt"
    write_notes(sheet, read_notes(output))
    output2 = tmp_path / "pipe.drums.txt"
    command[command.index(str(output))] = str(output2)
    with subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    ) as proc:
        consumer = subprocess.run(
            [
                sys.executable,
                str(COMPARE / "live_compare.py"),
                str(sheet),
                "--plain",
                "--clock",
                "line",
                "--smooth",
                "0",
            ],
            stdin=proc.stdout,
            capture_output=True,
            text=True,
            check=True,
            timeout=15,
        )
        proc.stdout.close()
        stderr = proc.stderr.read()
        assert proc.wait(timeout=15) == 0, stderr
    assert "PERFECT" in consumer.stdout


def make_sessions(tmp_path):
    kit = {
        "pieces": dict(zip(FAMILIES, FAMILIES)),
        "splits": {"train": ["one"], "validation": ["two"], "test": ["three"]},
    }
    for seed, name in enumerate(("one", "two", "three")):
        rng = np.random.default_rng(seed)
        a = np.zeros(48000 * 8, dtype=np.float32)
        labels = []
        for repeat in range(3):
            for k, freq in enumerate((70, 180, 4000, 400, 8000, 2000)):
                t = 0.2 + (repeat * 6 + k) * 0.4
                start = round(t * 48000)
                s = np.arange(4800) / 48000
                wave = np.sin(2 * np.pi * freq * s + 0.1 * seed) * np.exp(-s * 60)
                wave += rng.normal(0, 0.02, len(s)) * np.exp(-s * 100)
                a[start : start + len(s)] += (0.35 * wave).astype(np.float32)
                labels.append((t, FAMILIES[k]))
        # A non-drum transient provides a genuine all-negative candidate.
        a[370000:370100] = rng.uniform(-0.1, 0.1, 100)
        folder = tmp_path / name
        folder.mkdir()
        sf.write(folder / "audio.wav", a, 48000, subtype="FLOAT")
        with (folder / "labels.csv").open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["time_s", "piece_id"])
            w.writerows(labels)
    path = tmp_path / "kit.json"
    path.write_text(json.dumps(kit))
    return path, kit


def test_training_native_export_and_held_out_report(tmp_path):
    kit, _ = make_sessions(tmp_path)
    report = train(tmp_path, kit, tmp_path / "model.bin", tmp_path / "report.json")
    assert set(report["splits"]) == {"train", "validation", "test"}
    assert len(report["selection"]) == 6
    assert (
        max(
            s["native_python_max_probability_error"]
            for split in report["splits"].values()
            for s in split
        )
        < 2e-5
    )
    assert (tmp_path / "model.json").exists()
    assert not json.loads((tmp_path / "model.json").read_text())["device_validated"]
    assert report["splits"]["test"][0]["50ms"]["micro_f1"] > 0.5


def test_split_leakage_rejected(tmp_path):
    _, kit = make_sessions(tmp_path)
    kit["splits"]["test"] = ["one"]
    with pytest.raises(ValueError, match="multiple splits"):
        dataset(tmp_path, kit)
