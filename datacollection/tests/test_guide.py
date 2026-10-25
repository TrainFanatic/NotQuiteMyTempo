"""make_guide.py and playalong.py.  Run: .venv/bin/python -m pytest -q datacollection/tests"""

import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

HERE = Path(__file__).resolve().parent
DC = HERE.parent
REPO = DC.parent
ROD = REPO / "rushingOrDragging"
sys.path[:0] = [str(DC), str(REPO)]

import make_guide as mg  # noqa: E402
import playalong as pa  # noqa: E402
from fletcher import countoff  # noqa: E402


@pytest.fixture(scope="module")
def sheet(tmp_path_factory):
    out = tmp_path_factory.mktemp("sheet") / "bib.drums.txt"
    subprocess.run([sys.executable, "cli.py", "convert", "tests/fixtures/back_in_black_drums_4bars.json",
                    "-o", str(out)], cwd=ROD, check=True, capture_output=True)
    return out


def detect_clicks(x, start, thr=0.05, quiet_s=0.004, sr=mg.SR):
    """Blind onset detection: first sample above thr after >= quiet_s of near silence."""
    q = int(quiet_s * sr)
    loud = np.abs(x) > thr
    c = np.concatenate([[0], np.cumsum(np.abs(x) > 1e-5)])
    out, i = [], start
    for i in np.nonzero(loud)[0]:
        if i < start:
            continue
        if i >= q and c[i] - c[i - q] == 0:
            out.append(int(i))
    return out


@pytest.mark.parametrize("bpm", [94, 140])
def test_guide_clicks_countoff_and_json(sheet, tmp_path, bpm):
    wav = tmp_path / f"g{bpm}.wav"
    subprocess.run([sys.executable, str(DC / "make_guide.py"), str(sheet), "--bpm", str(bpm), "-o", str(wav)],
                   check=True, capture_output=True, cwd=tmp_path)
    x, sr = sf.read(str(wav), dtype="float64")
    g = json.loads(wav.with_suffix(".json").read_text())
    assert sr == g["sample_rate"] == 48000 and x.ndim == 1
    assert len(x) == g["frames"] and abs(g["duration_s"] - len(x) / sr) < 1e-9
    assert g["tempo_map"] and g["sheet_bpm"] == 94 and g["bpm"] == bpm  # found via the sheet header
    assert g["beats_per_bar"] == 4 and g["sheet_bars"] == 4 and g["sheet_loops"] == 1

    t0, beat = g["t0_sample"], 60.0 / bpm
    assert abs(g["t0_s"] - t0 / sr) < 1e-12
    assert np.all(np.abs(x[:int(round(g["lead_s"] * sr))]) == 0)  # leading silence

    # clicks: detected back from the audio, one per beat, on the beat to < 0.1 ms
    det = detect_clicks(x, t0)
    assert len(det) == 16 == len(g["click_times_s"])
    err = np.array(det) / sr - (t0 / sr + np.arange(16) * beat)
    assert np.abs(err).max() < 1e-4, err
    assert np.allclose(g["click_times_s"], t0 / sr + np.arange(16) * beat, atol=1.1e-5)
    assert g["click_kinds"] == (["accent"] + ["beat"] * 3) * 4
    assert np.all(np.abs(x[np.array(det)[::4]]) > 0.85)  # accent louder than the other beats
    assert np.all(np.abs(x[np.array(det)[1::4]]) < 0.65)
    assert np.all(np.abs(x[:t0]) < 0.9)  # nothing click-like before t0

    # count-off words land on beats -4..-1 relative to t0
    sched = countoff.schedule(bpm, 4, 1, "sharp")
    assert [c for _, c in sched] == g["countoff_clips"]
    assert np.allclose(g["countoff_times_s"], t0 / sr + np.array([-4, -3, -2, -1]) * beat, atol=1e-9)
    lead = int(round(g["lead_s"] * sr))
    t_start = sched[0][0] - beat
    assert abs((t0 - lead) / sr + t_start) < 1e-4  # count-off buffer ends exactly at t0
    errs = countoff.measure_onsets(x[lead:t0], t_start, sched, sr)
    assert max(abs(e) for e in errs) <= 0.002, errs

    # end of the sheet: 4 bars at bpm, then the tail
    assert abs(g["loop_len_s"] - 16 * beat) < 1e-9
    assert len(x) / sr >= t0 / sr + 16 * beat


def test_loops_half_and_tempo_map(sheet, tmp_path):
    sys.path.insert(0, str(ROD))
    from songsterr.tempo_map import write_tempo_map
    tm = write_tempo_map(json.loads((ROD / "tests/fixtures/back_in_black_drums_4bars.json").read_text()),
                         tmp_path / "bib.tempo.json")
    audio, g = mg.build_guide(sheet, 120, tempo=tm, bars=2, loops=3, click_every="half",
                              countoff_clicks=True)
    beat = 0.5
    ts = np.array(g["click_times_s"])
    t0 = g["t0_s"]
    sheet_clicks = ts[ts >= t0 - 1e-9]
    assert len(sheet_clicks) == 3 * 2 * 8 and g["loop_len_s"] == pytest.approx(8 * beat)
    assert np.allclose(sheet_clicks - t0, np.arange(48) * beat / 2, atol=1.1e-5)
    pre = ts[ts < t0 - 1e-9]
    assert np.allclose(pre, g["countoff_times_s"], atol=1.1e-5)
    det = detect_clicks(audio.astype(np.float64), g["t0_sample"], thr=0.2)
    assert len(det) == 48


def test_drums_optional(sheet, tmp_path, capsys):
    audio, g = mg.build_guide(sheet, 94, drums=True, kit=tmp_path / "nokit")
    assert g["drums_rendered"] is False
    assert "skipping --drums" in capsys.readouterr().err


# --------------------------------------------------------------------------- playalong (fake duplex)

class FakeTime:
    def __init__(self, t):
        self.currentTime = t
        self.inputBufferAdcTime = t - 0.005
        self.outputBufferDacTime = t + 0.010


class FakeStream:
    """Duplex loopback: the mic hears the speaker DELAY frames later, plus faint noise."""
    DELAY = 1234

    def __init__(self, samplerate, blocksize, device, channels, dtype, callback, finished_callback):
        self.sr, self.bs, self.cb, self.fin = samplerate, blocksize, callback, finished_callback
        self.in_ch, self.out_ch = channels
        self.latency = (0.005, 0.010)
        self.played = []
        self.aborted = False

    def _run(self):
        rng = np.random.default_rng(0)
        hist = np.zeros(self.DELAY)
        n = 0
        while True:
            out = np.zeros((self.bs, self.out_ch), dtype=np.float32)
            y = np.concatenate([hist, np.zeros(self.bs)])
            ind = (y[:self.bs] + 1e-4 * rng.standard_normal(self.bs))[:, None].repeat(self.in_ch, 1)
            try:
                self.cb(ind.astype(np.float32), out, self.bs, FakeTime(n / self.sr), 0)
                stop = False
            except pa.sd.CallbackStop:
                stop = True
            self.played.append(out[:, 0].copy())
            hist = np.concatenate([hist[self.bs:], out[:, 0]])
            n += self.bs
            if stop or self.aborted:
                break
            time.sleep(self.bs / self.sr / 50)  # ~50x real time, so the main loop sees progress
        self.fin()

    def abort(self):
        self.aborted = True

    def __enter__(self):
        self.t = threading.Thread(target=self._run)
        self.t.start()
        return self

    def __exit__(self, *a):
        self.t.join()


@pytest.fixture
def fake_sd(monkeypatch):
    devs = {"input": {"name": "Fake Mic", "index": 0, "max_input_channels": 1, "max_output_channels": 0,
                      "default_samplerate": 48000.0},
            "output": {"name": "Fake Speaker", "index": 1, "max_input_channels": 0, "max_output_channels": 2,
                       "default_samplerate": 48000.0}}
    monkeypatch.setattr(pa.sd, "query_devices", lambda dev=None, kind=None: devs[kind])
    monkeypatch.setattr(pa.sd, "check_input_settings", lambda **k: None)
    monkeypatch.setattr(pa.sd, "check_output_settings", lambda **k: None)
    monkeypatch.setattr(pa.sd, "Stream", FakeStream)
    monkeypatch.setattr(pa, "DEFAULT_OUT", Path("/nonexistent-calibration-dir"))


def test_playalong_file_layout(sheet, tmp_path, fake_sd):
    audio, g = mg.build_guide(sheet, 140, bars=1)
    js = mg.write_guide(tmp_path / "guide.wav", audio, g)
    take = pa.record_take(tmp_path / "guide.wav", "s1", tmp_path / "takes", countdown_s=0)
    assert take == tmp_path / "takes" / "s1"
    assert sorted(p.name for p in take.iterdir()) == ["ref.wav", "take.json"]
    t = json.loads((take / "take.json").read_text())
    rec, sr = sf.read(str(take / "ref.wav"), dtype="float32", always_2d=True)
    info = sf.info(str(take / "ref.wav"))
    assert info.subtype == "FLOAT" and sr == t["sample_rate"] == 48000 and rec.shape[1] == 1
    assert len(rec) == t["frames"] == t["scheduled_frames"] == t["guide_offset_frames"] + len(audio)
    assert t["guide"] == json.loads(js.read_text())
    assert t["input_device"]["name"] == "Fake Mic" and t["output_device"]["name"] == "Fake Speaker"
    assert t["latency_s"] == {"input": 0.005, "output": 0.010}
    assert t["status_flags_count"] == 0 and t["interrupted"] is False and t["start_unix"] > 0
    assert len(t["slate_times_s"]) == 3 and np.allclose(np.diff(t["slate_times_s"]), 0.5)
    off = t["guide_offset_s"]
    assert t["t0_ref_s"] == pytest.approx(off + g["t0_s"])
    assert np.allclose(t["click_times_ref_s"], off + np.array(g["click_times_s"]))
    # loopback: each guide click appears in ref.wav exactly DELAY frames after its scheduled time
    for c in t["click_times_ref_s"]:
        f = int(round(c * sr)) + FakeStream.DELAY
        assert abs(rec[f, 0]) > 0.5 and np.abs(rec[f - 40:f - 1, 0]).max() < 0.01
    for s in t["slate_frames"]:
        assert abs(rec[s + FakeStream.DELAY, 0]) > 0.9
    with pytest.raises(SystemExit):  # never overwrite a take
        pa.record_take(tmp_path / "guide.wav", "s1", tmp_path / "takes", countdown_s=0)


def test_calibrate_measures_loopback(tmp_path, fake_sd):
    res = pa.calibrate(out=tmp_path / "calibration.json")
    assert res["detected"] == pa.CAL_CLICKS
    assert res["median_s"] == pytest.approx(FakeStream.DELAY / 48000, abs=1.5 / 48000)
    assert res["spread_s"]["max"] - res["spread_s"]["min"] <= 2 / 48000
    assert json.loads((tmp_path / "calibration.json").read_text())["median_s"] == res["median_s"]


def test_ctrl_c_saves_partial(sheet, tmp_path, fake_sd, monkeypatch):
    audio, g = mg.build_guide(sheet, 140, bars=1)
    mg.write_guide(tmp_path / "guide.wav", audio, g)

    def printer(*a):
        def cb(pos):
            if pos > 48000:
                raise KeyboardInterrupt
        return cb
    monkeypatch.setattr(pa, "phase_printer", printer)
    take = pa.record_take(tmp_path / "guide.wav", "cc", tmp_path / "takes", countdown_s=0)
    t = json.loads((take / "take.json").read_text())
    assert t["interrupted"] is True
    assert 48000 < sf.info(str(take / "ref.wav")).frames == t["frames"] < t["scheduled_frames"]
