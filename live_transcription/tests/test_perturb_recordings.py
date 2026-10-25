import json

import numpy as np
import soundfile as sf
from scipy.signal import freqz

from perturb_recordings import VARIANTS, build, shelf_coefficients


def test_shelves_change_intended_band():
    for kind, low, high in (("low_shelf", 3.0, 0.0), ("high_shelf", 0.0, 3.0)):
        b, a = shelf_coefficients(kind, 3.0)
        _, response = freqz(b, a, worN=np.array([30.0, 18000.0]), fs=48000)
        actual = 20 * np.log10(np.abs(response))
        assert np.allclose(actual, [low, high], atol=0.15)


def test_derivatives_stay_with_train_source(tmp_path):
    kit = {"pieces": {"kick": "kick"},
           "splits": {s: [f"kick_{s}"] for s in ("train", "validation", "test")}}
    kit_path = tmp_path / "kit.json"
    kit_path.write_text(json.dumps(kit))
    recordings = tmp_path / "recordings"
    for split in kit["splits"]:
        folder = recordings / f"kick_{split}"
        folder.mkdir(parents=True)
        audio = np.zeros(48000, dtype=np.float32)
        audio[4800:4900] = 0.25
        sf.write(folder / "audio.wav", audio, 48000, subtype="FLOAT")
        (folder / "labels.csv").write_text("time_s,piece_id\n0.100000,kick\n")
    out = tmp_path / "perturbed_data"
    report = build(recordings, kit_path, out)
    derived = json.loads((out / "kit.json").read_text())["splits"]
    assert len(derived["train"]) == 1 + len(VARIANTS)
    assert derived["validation"] == ["kick_validation"]
    assert derived["test"] == ["kick_test"]
    assert all(x["parent"] == "kick_train" for x in report["entries"] if x["split"] == "train")
    for split in ("validation", "test"):
        name = f"kick_{split}"
        assert (out / "recordings" / name / "audio.wav").read_bytes() == (recordings / name / "audio.wav").read_bytes()
        assert (out / "recordings" / name / "labels.csv").read_bytes() == (recordings / name / "labels.csv").read_bytes()
    sample = sf.read(out / "recordings" / "kick_train__gain_m6db" / "audio.wav")[0]
    assert np.isclose(sample[4800], 0.25 * 10 ** (-6 / 20), atol=1e-7)
