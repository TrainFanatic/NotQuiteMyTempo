"""The first real headset session (2026-10-25, Quest 2 mic, onsets only, Caravan at 140 bpm):
python -m pytest hub/tests/test_headset_session.py -q

Fixture (fixtures/headset_20261025/): the transcriber's 175 onsets on the laptop clock, the
controller's 16 count-in downbeats and Fletcher's 15 stops, plus the first 40 bars of the sheet.
What went wrong with the old code: the bridge only reported a miss when the NEXT onset came, so
in the runs he sat out (8-15) nothing happened until one stray onset 15-220 s in, which dumped
every miss since bar 1 at once; the brain read that burst as "dropped notes in bar 2" and cut
off "at bar 3" (really bar 10-129), again and again, from bar 1 each time."""

import json
from pathlib import Path

import pytest

from hub.bridge import SessionBridge
from hub.session import Session
from hub.session_replay import replay, runs_from

FIX = Path(__file__).resolve().parent / "fixtures" / "headset_20261025"
SONGS = FIX / "songs"
BEAT = 60 / 140
BAR = 4 * BEAT


@pytest.fixture(scope="module")
def rec():
    d = json.loads((FIX / "session.json").read_text())
    d["runs"] = runs_from(d["onsets"], d["t0s"])
    return d


def run_bridge(rec, k, seconds=40.0, delay=0.24):
    """Recorded run k (0-based) through a fresh session bridge: [(t rel. t0, type, data)]."""
    sb = SessionBridge(SONGS, "laptop", 0.17, 0.0)
    t0 = rec["t0s"][k]
    sb.on_state({"state": "count_in", "song_id": "caravan-head", "from_bar": 1, "t0": t0,
                 "song_time_at_t0": 0.0, "reason": "start"}, "session", t0 - 5)
    lines = sorted((t0 + o + delay, f"{t0 + o:.6f}  0  onset") for o in rec["runs"][k] if o > -5)
    out, t = [], t0 - 5
    while t < t0 + seconds:
        t += 0.05
        while lines and lines[0][0] <= t:
            rx, text = lines.pop(0)
            out += [(t - t0, *m) for m in sb.feed_line(text, rx)]
        out += [(t - t0, *m) for m in sb.tick(t)]
    return out


def test_the_old_bug_is_in_the_fixture(rec):
    """Runs 8-15: at most 3 onsets in the first 15 s after the downbeat, yet each was cut off."""
    stops = [s - t0 for s, t0 in zip(rec["stops"], rec["t0s"])]
    for k in range(7, 15):
        assert sum(1 for o in rec["runs"][k] if 0 <= o < 15) <= 1, k
        assert stops[k] > 15  # the cutoff came only with the first stray onset


@pytest.mark.parametrize("k", [7, 13])  # run 8 (stray onset at +28 s), run 14 (+220 s)
def test_silent_run_misses_arrive_with_the_clock_not_in_a_burst(rec, k):
    msgs = run_bridge(rec, k)
    notes = [(t, d) for t, typ, d in msgs if typ == "judgment.note"]
    assert notes, "a run he sits out still produces its misses"
    for t, d in notes:
        if d["grade"] == "miss":  # each one within ~0.6 s of the note's own time
            assert d["song_time"] <= t <= d["song_time"] + 0.6, (t, d)
    playing = [t for t, typ, d in msgs if typ == "session.state" and d["state"] == "playing"]
    assert playing and 0 <= playing[0] < 0.6  # the song plays from the downbeat on, hits or not


def test_silence_is_a_waiting_bark_then_the_menu_not_a_tirade(rec):
    for start in [6] + list(range(7, 15)):  # runs 7-15 (0-based 6..14) as the song start
        log = replay("caravan-head", rec["runs"][start:start + 1], SONGS)
        what = [w for _, who, w in log if who == "fletcher"]
        assert not any(w.startswith("CUTOFF") for w in what), (start, what)
        assert what[0] == "bark waiting" and what[-1].startswith("END SONG"), (start, what)
        end = next(t for t, who, w in log if w.startswith("END SONG"))
        if start >= 7:  # runs 8-15 (run 7 has a few hits in bar 4: its silence starts later)
            assert end < 4.64 + 7 * BAR  # within ~7 bars of the downbeat
        result = next(w for _, who, w in log if w.startswith("result:"))
        assert "ended_by no_playing" in result and "cutoffs 0" in result


def test_whole_session_replay_never_loops(rec):
    log = replay("caravan-head", rec["runs"], SONGS)
    cuts = [w for _, who, w in log if who == "fletcher" and w.startswith("CUTOFF")]
    assert len(cuts) <= 1  # the old code: 15
    assert any(w.startswith("END SONG") for _, _, w in log)
    assert log[-2][2] == "idle"


def test_first_run_gets_its_grace(rec):
    """Run 1: cut off 4.0 s after the downbeat with 4 hits heard. Now no cutoff inside the grace
    (4 bars from his first hit) whatever the misses say."""
    log = replay("caravan-head", rec["runs"][:1], SONGS)
    t0 = next(float(w.split("+")[1]) for _, who, w in log if w.startswith("count_in"))
    first_hit = min(o for o in rec["runs"][0] if o > 0)
    for t, who, w in log:
        if w.startswith("CUTOFF"):
            assert t - t0 >= first_hit + 3 * BAR


def test_bark_bleed_is_not_an_extra():
    """An onset with no sheet note while his bark plays is the bark through the mic."""
    sb = SessionBridge(SONGS, "laptop", 0.17, 0.0)
    t0 = 1000.0
    sb.on_state({"state": "count_in", "song_id": "caravan-head", "from_bar": 1, "t0": t0,
                 "song_time_at_t0": 0.0, "reason": "start"}, "session", t0 - 5)
    times = sorted(sb.ref.gate.times)  # the widest gap between sheet notes, 0.2 s in: no note near
    gap = max(((b - a), a) for a, b in zip(times, times[1:]) if a > t0 + 2)[1] + 0.2
    out = sb.feed_line(f"{t0 + 1.0:.6f}  0  onset", t0 + 1.24)  # start judging
    sb.on_speech({"clip_id": "no_1", "utterance_id": "u-1"}, "aaron", gap - 0.1)
    out = sb.feed_line(f"{gap:.6f}  0  onset", gap + 0.24) + sb.ref.settle()
    assert not [d for t, d in out if t == "judgment.note" and d["grade"] == "extra"]
    assert sb.ref.bleed == 1


def test_fletcher_quit_reason_reaches_the_result():
    ses = Session(SONGS)
    ses.on_command({"action": "start", "song_id": "caravan-head"}, "quest", 0.0)
    out = ses.on_command({"action": "quit", "reason": "no_playing"}, "aaron", 10.0)
    res = [d for t, d in out if t == "session.result"]
    assert res and res[0]["ended_by"] == "no_playing" and res[0]["completed"] is False
    assert out[-1][1]["state"] == "idle"
