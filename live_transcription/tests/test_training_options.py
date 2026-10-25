"""train_model.py options: Quest mute filter on the candidates (shared with live replay), ring
exclusion from the loss, time-offset pooling folded into the 256 native weights."""

import json
import subprocess
import sys
import numpy as np
import pytest
import soundfile as sf
from common import (
    FEATURES,
    MIDIS,
    ROOT,
    QuestMuteFilter,
    events,
    extract,
    read_notes,
)
from train_model import pool, ring_mask, train, unpool
from test_workflow import make_sessions


@pytest.mark.parametrize("windows", [1, 2, 4, 8])
def test_pooled_model_folds_into_native_256_weights(windows):
    rng = np.random.default_rng(windows)
    x = rng.normal(2, 1, (50, FEATURES))
    z = pool(x, windows)
    assert z.shape == (50, FEATURES // 8 * windows)
    mean, scale = z.mean(axis=0), z.std(axis=0) + 0.1
    w = rng.normal(size=(6, z.shape[1]))
    pooled_logit = ((z - mean) / scale) @ w.T
    m, s, w256 = unpool(mean, scale, w, windows)
    native_logit = ((x - m) / s) @ w256.T  # what classifier.cpp computes
    assert np.allclose(native_logit, pooled_logit)
    if windows == 1:  # features are offset-major (index = offset * 32 + band)
        assert np.allclose(z[:, 3], x[:, 3::32].mean(axis=1))


def test_ring_mask_excludes_only_the_hit_family_and_unlabelled_candidates():
    times = np.array([1.0, 1.1, 1.2, 1.3, 2.0])
    y = np.zeros((5, 6), dtype=int)
    snare, kick = MIDIS.index(38), MIDIS.index(36)
    y[0, snare] = 1
    y[2, kick] = 1  # a labelled hit inside the window keeps its loss
    w = ring_mask(times, y, [(1.0, 38), (1.2, 36)], 0.25)
    assert w[1].tolist() == [1, 0, 1, 1, 1, 1]  # ring of the snare: not a snare negative
    assert w[3].tolist() == [0, 1, 1, 1, 1, 1]  # 0.1 s after the kick; the snare is 0.3 s back
    assert w[[0, 2, 4]].min() == 1
    assert ring_mask(times, y, [(1.0, 38)], 0).min() == 1


def test_mute_filter_in_replay_drops_phantoms_like_live(tmp_path, audio, all_classes_model):
    # The fixture's hits 75 ms apart decay to digital silence in between: a "mute" dip.
    all_events = events(extract(audio, all_classes_model))
    kept = events(extract(audio, all_classes_model, mute=QuestMuteFilter()))
    assert len(all_events) == 36 and len(kept) == 12
    assert set(kept) <= set(all_events)
    wav, model = tmp_path / "audio.wav", tmp_path / "m.bin"
    sf.write(wav, audio, 48000, subtype="FLOAT")
    model.write_bytes(all_classes_model)

    def live(name):
        out = tmp_path / name
        subprocess.run(
            [sys.executable, str(ROOT / "tools/transcribe_wav.py"), str(wav),
             "--model", str(model), "--output", str(out), "--chunk", "127"],
            capture_output=True, text=True, check=True,
        )
        return read_notes(out)

    # No sidecar (older model): every candidate, as before.
    assert len(live("old.drums.txt")) == 36
    # A model trained with the filter: live applies the same one, and matches the replay.
    filt = {"window_s": 0.24, "mute_db": -75.0, "drop_db": 35.0}
    model.with_suffix(".json").write_text(
        json.dumps({"training_options": {"quest_mute_filter": filt}})
    )
    assert live("new.drums.txt") == [(round(t, 6), m) for t, m in kept]


def test_training_with_all_options_exports_matching_native_model(tmp_path):
    kit, _ = make_sessions(tmp_path)
    report = train(
        tmp_path, kit, tmp_path / "model.bin", tmp_path / "report.json",
        ring_s=0.25, pool_windows=2, class_weight="balanced", c_grid=(0.01, 1.0),
    )
    parity = max(
        s["native_python_max_probability_error"]
        for split in report["splits"].values()
        for s in split
    )
    assert parity < 2e-5
    options = json.loads((tmp_path / "model.json").read_text())["training_options"]
    assert options["pool_windows"] == 2 and options["quest_mute_filter"]["window_s"] == 0.24
    assert all(s["C"] in (0.01, 1.0) for s in report["selection"])
    assert report["splits"]["test"][0]["50ms"]["micro_f1"] > 0.5
