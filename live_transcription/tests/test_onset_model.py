"""train_onset_model.py: one hit/no-hit class (MIDI 0 onset) on the same candidates as the
six-class trainer; compare_hit_detection.py scores models as hit/no-hit; transcribe_wav.py
writes onset lines for such a model."""

import json
import subprocess
import sys
import numpy as np
import pytest
import soundfile as sf
from common import ONSET_MIDI, ROOT, extract, events, read_notes
from compare_hit_detection import compare
from evaluate import evaluate_hits
from train_model import train as train_six
from train_onset_model import train
from test_workflow import make_sessions as make_six_class_sessions


def make_sessions(tmp_path):
    """make_six_class_sessions plus unlabelled noise bursts that the onset detector proposes:
    a hit/no-hit model needs candidates with no drum at all (its fixture transient is too
    quiet to become one)."""
    kit, spec = make_six_class_sessions(tmp_path)
    for seed, name in enumerate(spec["splits"]["train"] + spec["splits"]["validation"]
                                + spec["splits"]["test"]):
        path = tmp_path / name / "audio.wav"
        audio = sf.read(path, dtype="float32")[0]
        rng = np.random.default_rng(100 + seed)
        for t in (7.45, 7.8):
            start, n = round(t * 48000), 2400
            audio[start : start + n] += (
                rng.normal(size=n) * np.exp(-np.arange(n) / 200) * 0.3
            ).astype(np.float32)
        sf.write(path, audio, 48000, subtype="FLOAT")
    return kit, spec


def test_evaluate_hits_merges_simultaneous_families():
    reference = [(1.0, 36), (1.0, 42), (2.0, 38)]
    result = evaluate_hits(reference, [1.004, 2.03, 3.0])
    assert result["10ms"]["tp"] == 1 and result["10ms"]["fn"] == 1
    assert result["50ms"]["tp"] == 2 and result["50ms"]["fp"] == 1


def test_onset_model_trains_exports_one_class_and_reports(tmp_path):
    kit, _ = make_sessions(tmp_path)
    model = tmp_path / "onset.bin"
    report = train(tmp_path, kit, model, tmp_path / "report.json", c_grid=(0.01, 1.0))
    assert report["selection"]["C"] in (0.01, 1.0)
    assert max(
        s["native_python_max_probability_error"]
        for split in report["splits"].values()
        for s in split
    ) < 2e-5
    metadata = json.loads((tmp_path / "onset.json").read_text())
    assert metadata["midis"] == [ONSET_MIDI] and metadata["families"] == ["onset"]
    assert metadata["training_options"]["quest_mute_filter"]["window_s"] == 0.24
    assert not metadata["device_validated"]
    test = report["splits"]["test"][0]
    assert test["model"]["50ms"]["f1"] > 0.9
    assert test["model"]["50ms"]["fp"] <= test["onsets_only"]["50ms"]["fp"]
    audio = sf.read(tmp_path / "three" / "audio.wav", dtype="float32")[0]
    assert {m for _, m in events(extract(audio, model.read_bytes()))} == {ONSET_MIDI}
    played = tmp_path / "report" / "test" / "three.played.drums.txt"
    assert {m for _, m in read_notes(played)} == {ONSET_MIDI}


def test_onset_model_refuses_to_overwrite(tmp_path):
    kit, _ = make_sessions(tmp_path)
    with pytest.raises(ValueError, match="sidecar"):
        train(tmp_path, kit, tmp_path / "onset.bin", tmp_path / "onset.json")
    (tmp_path / "onset.bin").write_bytes(b"previous")
    with pytest.raises(FileExistsError):
        train(tmp_path, kit, tmp_path / "onset.bin", tmp_path / "report.json")
    assert (tmp_path / "onset.bin").read_bytes() == b"previous"


def test_compare_scores_six_class_and_onset_models_on_same_sessions(tmp_path):
    kit, _ = make_sessions(tmp_path)
    six, onset = tmp_path / "six.bin", tmp_path / "onset.bin"
    train_six(tmp_path, kit, six, tmp_path / "six_report.json", c_grid=(1.0,))
    train(tmp_path, kit, onset, tmp_path / "onset_report.json", c_grid=(1.0,))
    result = compare(tmp_path, kit, [six, onset])
    assert set(result["splits"]) == {"validation", "test"}
    totals = result["splits"]["test"]["totals"]
    assert set(totals) == {"six", "onset", "onsets_only"}
    hits = 18  # make_sessions: 3 repeats of 6 families
    for name in totals:
        m = totals[name]["50ms"]
        assert m["tp"] + m["fn"] == hits


def test_compare_requires_explicit_mute_without_sidecar(tmp_path):
    kit, _ = make_sessions(tmp_path)
    six = tmp_path / "six.bin"
    train_six(tmp_path, kit, six, tmp_path / "six_report.json", c_grid=(1.0,))
    six.with_suffix(".json").unlink()
    with pytest.raises(FileNotFoundError, match="--default-mute six"):
        compare(tmp_path, kit, [six])
    result = compare(tmp_path, kit, [six], default_mute=["six"])
    assert result["models"]["six"]["mute_filter"]["window_s"] == 0.24


def test_transcribe_wav_writes_onset_lines_for_onset_model(tmp_path):
    kit, _ = make_sessions(tmp_path)
    model = tmp_path / "onset.bin"
    train(tmp_path, kit, model, tmp_path / "onset_report.json", c_grid=(1.0,))
    out = tmp_path / "played.drums.txt"
    result = subprocess.run(
        [sys.executable, str(ROOT / "tools/transcribe_wav.py"),
         str(tmp_path / "three" / "audio.wav"), "--model", str(model), "--output", str(out)],
        capture_output=True, text=True, check=True,
    )
    notes = read_notes(out)
    assert notes and {m for _, m in notes} == {ONSET_MIDI}
    assert out.read_text().startswith("# live transcription: onsets only, drum unknown (midi 0)")
    assert "  0  onset" in result.stdout
