"""python -m pytest hub/tests -q

Real inputs only: the sheet is Chris's converter output for his Back in Black
fixture (94 bpm), looped to 16 bars; performances come from his own drift
simulator (rushingOrDragging/tests/test_drift.py)."""

import subprocess
import sys
from pathlib import Path

import pytest

from hub.bridge import BarClock, Referee, cp, lc

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "rushingOrDragging" / "tests"))
import test_drift as td  # noqa: E402

from fletcher import brain as B  # noqa: E402
from fletcher.config import Config  # noqa: E402

BPM = 94
BAR_S = 4 * 60 / BPM


@pytest.fixture(scope="module")
def sheet_path(tmp_path_factory):
    d = tmp_path_factory.mktemp("sheet")
    subprocess.run([sys.executable, str(ROOT / "rushingOrDragging/cli.py"), "convert",
                    str(ROOT / "rushingOrDragging/tests/fixtures/back_in_black_drums_4bars.json"),
                    "-o", str(d / "b4.txt")], check=True, capture_output=True)
    notes = cp.read_notes(d / "b4.txt")
    lines = ["# time_s  midi  name"] + [f"{h.time + k * 4 * BAR_S:.6f}  {h.midi}  {h.name}"
                                       for k in range(4) for h in notes]
    (d / "sheet.drums.txt").write_text("\n".join(lines) + "\n")
    return d / "sheet.drums.txt"


def perf_lines(sheet_path, tmp_path, seed, sd, drop=()):
    td.make_drifting(sheet_path, tmp_path / "perf.txt", seed=seed, drift_sd=sd)
    lines = [l for l in (tmp_path / "perf.txt").read_text().splitlines() if not l.startswith("#")]
    return [l for i, l in enumerate(lines) if i not in drop]


def referee(sheet_path):
    return Referee(cp.read_notes(sheet_path), BarClock(BPM), song_id=sheet_path.stem)


def notes_of(msgs):
    return [d for t, d in msgs if t == "judgment.note"]


def test_errors_match_chriss_matcher_exactly(sheet_path, tmp_path):
    lines = perf_lines(sheet_path, tmp_path, seed=3, sd=0.04)
    ref, own = referee(sheet_path), lc.LiveMatcher(cp.read_notes(sheet_path))
    msgs = []
    for i, l in enumerate(lines):
        msgs += ref.feed_line(l, rx=100.0 + i)
    expected = []
    for l in lines:
        t, midi = float(l.split()[0]), int(l.split()[1])
        _, err = own.feed(t, midi)
        expected.append(None if err is None else round(err * 1000, 1))
    got = [n["error_ms"] for n in notes_of(msgs) if n["grade"] != "miss"]
    assert got == expected and any(e for e in got)
    assert msgs[0] == ("session.state", {"state": "playing", "song_id": "sheet.drums", "bpm": 94,
                                         "bar": 1, "beat": 1})
    bars = [n["bar"] for n in notes_of(msgs)]
    assert bars == sorted(bars) and bars[-1] == 16  # bars only move forward, 16 bars of song
    for n in notes_of(msgs):  # bar = the written bar of the sheet note (song_time - error)
        if n["error_ms"] is not None:
            assert n["bar"] == int((n["song_time"] - n["error_ms"] / 1000) / BAR_S + 1e-3) + 1  # 1 ms: song_time/error are rounded


def test_skipped_notes_are_reported_as_misses(sheet_path, tmp_path):
    lines = perf_lines(sheet_path, tmp_path, seed=3, sd=0.01, drop={40, 41, 42})
    ref = referee(sheet_path)
    msgs = [m for i, l in enumerate(lines) for m in ref.feed_line(l, rx=float(i))]
    misses = [n for n in notes_of(msgs) if n["grade"] == "miss"]
    assert len(misses) == 3 and all(n["error_ms"] is None for n in misses)


def mood(sheet_path, lines):
    """Bridge output straight into Fletcher's brain, at the song's own pace."""
    br, ref = B.Brain(Config(dry_run=True)), referee(sheet_path)
    for l in lines:
        s = float(l.split()[0])
        for typ, d in ref.feed_line(l, rx=s):
            eff = br.on_session_state(d, s) if typ == "session.state" else br.on_note(d, s) + br.tick(s)
            for e in eff:
                if isinstance(e, B.Cutoff):
                    return br.anger, e.facts
    return br.anger, None


def test_heavy_drift_makes_fletcher_cut_off(sheet_path, tmp_path):
    _, facts = mood(sheet_path, perf_lines(sheet_path, tmp_path, seed=5, sd=0.07))
    assert facts is not None and facts.current_bar >= 3  # never in the grace bars
    assert facts.kind in ("rushing", "dragging") and facts.from_bar <= facts.since_bar


def test_mild_wobble_keeps_him_calm(sheet_path, tmp_path):
    anger, facts = mood(sheet_path, perf_lines(sheet_path, tmp_path, seed=11, sd=0.015))
    assert facts is None and anger < 0.3


def test_stop_mutes_and_restart_realigns(sheet_path):
    ref = referee(sheet_path)
    latency, clock_gap = 0.17, 5000.0  # hub clock = stream clock + 5000 s; lines arrive 170 ms late
    sheet = cp.read_notes(sheet_path)
    for n in sheet[:30]:
        ref.feed_line(f"{n.time:.6f}  {n.midi}  x", rx=n.time + clock_gap + latency)
    assert ref.on_command({"action": "stop"}, rx=sheet[30].time + clock_gap) == [
        ("session.state", {"state": "stopped", "song_id": "sheet.drums", "bpm": 94, "bar": ref.bar, "beat": 1})]
    assert ref.feed_line(f"{sheet[31].time:.6f}  {sheet[31].midi}  x", rx=0) == []  # muted while stopped
    rx_restart = sheet[31].time + clock_gap + 3.0
    assert ref.on_command({"action": "restart", "from_bar": 5}, rx_restart)[0][1]["state"] == "count_in"
    # The drummer plays bar 5 perfectly, starting one count-in after the restart.
    start_s = rx_restart + 4 * 60 / BPM - clock_gap
    bar5 = [n for n in sheet if 4 * BAR_S - 1e-6 <= n.time < 6 * BAR_S - 1e-6]  # bars 5 and 6
    msgs = []
    for n in bar5:
        s = start_s + (n.time - 4 * BAR_S)
        msgs += ref.feed_line(f"{s:.6f}  {n.midi}  x", rx=s + clock_gap + latency)
    assert msgs[0][1]["state"] == "playing"
    played = notes_of(msgs)
    assert {n["bar"] for n in played} == {5, 6} and all(n["grade"] == "perfect" for n in played)
    assert max(abs(n["error_ms"]) for n in played) < 1.0


def test_tempo_map_gives_the_same_bars_as_fixed_bpm(tmp_path):
    """Chris's tempo map (songsterr/tempo_map.py) drives the bar clock for real songs."""
    import json
    sys.path.insert(0, str(ROOT / "rushingOrDragging"))
    from songsterr import tempo_map as tm
    src = ROOT / "rushingOrDragging/tests/fixtures/back_in_black_drums_4bars.json"
    subprocess.run([sys.executable, str(ROOT / "rushingOrDragging/cli.py"), "convert", str(src),
                    "-o", str(tmp_path / "b4.drums.txt")], check=True, capture_output=True)
    tm.write_tempo_map(json.loads(src.read_text()), tmp_path / "b4.drums.txt")
    tmap = tm.load_tempo_map(tmp_path / "b4.tempo.json")
    mapped, fixed = BarClock(0, tempo_map=tmap), BarClock(BPM)
    assert mapped.bpm == pytest.approx(BPM, abs=0.01)
    for n in cp.read_notes(tmp_path / "b4.drums.txt"):
        (b1, beat1), (b2, beat2) = mapped.bar_at(n.time), fixed.bar_at(n.time)
        assert b1 == b2 and beat1 == pytest.approx(beat2, abs=1e-4)
    assert [mapped.bar_start(b) for b in (1, 2, 3)] == pytest.approx([fixed.bar_start(b) for b in (1, 2, 3)])


@pytest.mark.parametrize("arrives", [-0.4, 0.0, 0.35, 1.2])  # s vs. the nominal t0 - 4 beats
def test_restart_with_t0_is_exact_however_late_the_command(sheet_path, arrives):
    """Fletcher's restart carries t0 (hub clock, the count-off downbeat): bar 5 starts at t0,
    not at arrival + count-in, so a late or early command doesn't shift the sheet."""
    ref = referee(sheet_path)
    latency, clock_gap = 0.17, 5000.0
    sheet = cp.read_notes(sheet_path)
    for n in sheet[:30]:
        ref.feed_line(f"{n.time:.6f}  {n.midi}  x", rx=n.time + clock_gap + latency)
    ref.on_command({"action": "stop"}, rx=sheet[30].time + clock_gap)
    t0 = sheet[31].time + clock_gap + 6.0
    rx = t0 - 4 * 60 / BPM + arrives
    out = ref.on_command({"action": "restart", "from_bar": 5, "t0": t0}, rx)
    assert out == [("session.state", {"state": "count_in", "song_id": "sheet.drums", "bpm": 94,
                                      "bar": 5, "beat": 1, "t0": round(t0, 4)})]
    start_s = t0 - clock_gap  # the drummer starts bar 5 on the downbeat
    msgs = []
    for n in [n for n in sheet if 4 * BAR_S - 1e-6 <= n.time < 6 * BAR_S - 1e-6]:
        s = start_s + (n.time - 4 * BAR_S)
        msgs += ref.feed_line(f"{s:.6f}  {n.midi}  x", rx=s + clock_gap + latency)
    played = notes_of(msgs)
    assert {n["bar"] for n in played} == {5, 6} and all(n["grade"] == "perfect" for n in played)
    assert max(abs(n["error_ms"]) for n in played) < 1.0


def test_restart_without_t0_keeps_the_count_in_path(sheet_path):
    """Old senders: bar starts one count-in after arrival; count_in reports that time as t0."""
    ref = referee(sheet_path)
    sheet = cp.read_notes(sheet_path)
    for n in sheet[:10]:
        ref.feed_line(f"{n.time:.6f}  {n.midi}  x", rx=n.time + 100.0)
    late = ref.on_command({"action": "restart", "from_bar": 2}, rx=200.0)
    assert late[0][1]["t0"] == pytest.approx(200.0 + 4 * 60 / BPM, abs=1e-4)
    assert ref.resume_at_s == pytest.approx(200.0 + 4 * 60 / BPM - (100.0 - 0.17))  # minus the clock offset
