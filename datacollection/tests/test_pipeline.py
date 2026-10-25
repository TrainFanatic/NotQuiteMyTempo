"""End-to-end check of datacollection/ on a simulated session (sim_session.py):
guide + playalong schedule -> ref.wav and a drifting, reverberant 44.1 kHz Quest take ->
Kevin's import_quest_take -> align -> label -> evaluate.

  .venv/bin/python -m pytest datacollection/tests -q
Skips when the kit subset in tests/fixtures/kit or Kevin's native library is missing.
"""

import csv
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

HERE = Path(__file__).resolve().parent
DC = HERE.parent
sys.path.insert(0, str(DC))

import dc_common as dc  # noqa: E402

KIT_DIR = HERE / "fixtures" / "kit" / "AVLDrumkits-BlackPearl-2023"
LIB = dc.LT / "build" / ("libdrum_transcriber.dylib" if sys.platform == "darwin" else "libdrum_transcriber.so")
pytestmark = pytest.mark.skipif(not any(KIT_DIR.glob("*.wav")) or not LIB.exists(),
                                reason="kit fixture or live_transcription/build native library missing")
KIT_JSON = dc.LT / "config" / "kit.example.json"


@pytest.fixture(scope="module")
def sim(tmp_path_factory):
    import json
    import align
    import label
    import sim_session

    out = sim_session.build(tmp_path_factory.mktemp("sim") / "s", quiet=True)
    truth = json.loads((out / "truth" / "truth.json").read_text())
    sdir = Path(truth["session_dir"])
    kit = dc.load_kit(KIT_JSON)
    labels, review, al = label.label_score(sdir, out / "takes" / "sim", kit, quiet=True)
    return dict(out=out, truth=truth, sdir=sdir, kit=kit, labels=labels, review=review, al=al,
                align=align, label=label)


def test_alignment_recovers_offset_and_clock(sim):
    t, al = sim["truth"], sim["al"]
    span = np.linspace(*t["ref_span_s"], 200)
    err = (al["a"] * span + al["b"]) - (t["quest_start_s"] + t["ratio"] * span)
    print(f"offset error over the take: max {np.abs(err).max() * 1000:.3f} ms, "
          f"ratio {al['ratio_ppm']:+.1f} ppm (true {(t['ratio'] - 1) * 1e6:+.1f})")
    assert np.abs(err).max() < 0.0005
    assert abs(al["ratio_ppm"] - (t["ratio"] - 1) * 1e6) < 15
    assert al["ok"] and al["rms_residual_ms"] < 1.0


def test_labels_match_truth_within_3ms(sim):
    kit = sim["kit"]
    lab = dc.read_labels(sim["sdir"] / "labels.csv", kit)
    tru = dc.read_labels(sim["out"] / "truth" / "truth_labels.csv", kit)
    # same physical piece, one-to-one
    pieces = sorted({p for *_, p in lab} | {p for *_, p in tru})
    pid = {p: i for i, p in enumerate(pieces)}
    L = [(t, pid[p]) for t, _, p in lab]
    T = [(t, pid[p]) for t, _, p in tru]
    pairs = dc.match_one_to_one(L, T, tol=0.003, key=lambda m: m)
    frac = len(pairs) / len(T)
    e = np.abs([d for d, _, _ in pairs]) * 1000
    print(f"{len(pairs)}/{len(T)} truth hits labeled within 3 ms ({frac:.3f}); |err| mean {e.mean():.2f} "
          f"ms; {len(L)} labels")
    assert frac >= 0.97
    assert len(L) <= len(T) + 3            # few spurious labels


def test_labels_csv_is_kevins_format(sim):
    rows = list(csv.reader(open(sim["sdir"] / "labels.csv")))
    assert rows[0] == ["time_s", "piece_id"]
    ltc = dc.kevin()
    kit = ltc.load_kit(KIT_JSON)
    import soundfile as sf
    dur = sf.info(sim["sdir"] / "audio.wav").duration
    assert len(ltc.read_labels(sim["sdir"] / "labels.csv", kit, dur)) > 100


def test_never_overwrites_without_force(sim):
    with pytest.raises(SystemExit):
        sim["label"].label_score(sim["sdir"], sim["out"] / "takes" / "sim", sim["kit"], quiet=True)


def test_evaluate_reports(sim):
    import evaluate
    o = evaluate.evaluate(sim["sdir"], sim["out"] / "takes" / "sim", sim["kit"], quiet=True)
    assert o["score_flux"]["ALL"]["recall"] > 0.8
    assert "verdict_agree" in o["score_flux"]["live_compare"]
    assert o["kevin_core_onsets"]["ALL"]["recall"] > 0.5
    lat = o["latency"]
    assert abs(lat["ref_round_trip_ms"] - sim["truth"]["rt_ref_s"] * 1000) < 0.1
    assert abs(lat["guide_at_headset_vs_drum_alignment_ms"] - sim["truth"]["speaker_extra_s"] * 1000) < 1.0


def test_score_flux_cli_with_sheet_offset(sim, tmp_path):
    ri = dc.load_ref(sim["out"] / "takes" / "sim")
    sheet = tmp_path / "sheet.drums.txt"
    dc.write_notes(sheet, [(t - ri["offset"], m) for t, m in ri["sheet"]], "expanded sheet")
    out = tmp_path / "det.drums.txt"
    subprocess.run([sys.executable, str(DC / "score_flux.py"), str(sim["out"] / "takes" / "sim" / "ref.wav"),
                    str(sheet), "-o", str(out), "--sheet-offset", str(ri["offset"]), "--no-extras"],
                   check=True, capture_output=True)
    det = dc.read_notes(out)
    tru = dc.read_notes(sim["out"] / "truth" / "truth_ref.drums.txt")
    assert len(dc.match_one_to_one(det, tru)) / len(tru) > 0.9


def test_single_mode(tmp_path):
    import json
    import label
    import sim_session
    sdir = sim_session.build_single(tmp_path)
    labels, review = label.label_single(sdir, "snare", dc.load_kit(KIT_JSON), quiet=True)
    truth = np.array([float(r["time_s"]) for r in csv.DictReader(open(tmp_path / "truth" / "single_truth.csv"))])
    lt = np.array([t for t, _ in labels])
    recall = np.mean([np.min(np.abs(lt - t)) < 0.015 for t in truth])
    false = [t for t in lt if np.min(np.abs(truth - t)) > 0.015]
    excluded_real = [r for r in review if np.min(np.abs(truth - r["time_s"])) < 0.015]
    talk = json.loads((tmp_path / "truth" / "single_speech.json").read_text())
    print(f"single: recall {recall:.3f}, {len(false)} false labels, {len(review)} excluded "
          f"({len(excluded_real)} real hits), {len(talk)} speech bursts")
    # Single takes are recorded without talking (README). The level detector does not try to
    # reject speech beyond the voicing check, so only false labels OUTSIDE speech count here.
    in_talk = lambda t: any(a <= t <= a + d + 0.5 for a, d in talk)  # noqa: E731
    assert recall >= 0.95 and sum(not in_talk(t) for t in false) <= 2 and not excluded_real
