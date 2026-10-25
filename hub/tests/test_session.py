"""python -m pytest hub/tests/test_session.py -q

The session controller's state machine (hub/session.py) and the bridge's runtime song
switching + laptop-clock mode (hub/bridge.py SessionBridge), on the fixture library
hub/tests/fixtures/songs/ (Chris's Back in Black fixture x4, his converter + tempo map)."""

import sys
from pathlib import Path

import pytest

from hub.bridge import SessionBridge, cp
from hub.session import SESSION_RESULT, Session, load_song

ROOT = Path(__file__).resolve().parents[2]
SONGS = Path(__file__).resolve().parent / "fixtures" / "songs"
SONG = "back-in-black-16"
sys.path.insert(0, str(ROOT / "rushingOrDragging" / "tests"))
import test_drift as td  # noqa: E402

BPM = 94.0
BEAT = 60 / BPM
BAR = 4 * BEAT


def states(msgs, state=None):
    return [d for t, d in msgs if t == "session.state" and (state is None or d["state"] == state)]


def results(msgs):
    return [d for t, d in msgs if t == SESSION_RESULT]


def notes_of(msgs):
    return [d for t, d in msgs if t == "judgment.note"]


def test_fixture_song_loads():
    s = load_song(SONGS, SONG)
    assert s.bars == 16 and s.bpm_at(1) == BPM and s.beats_per_bar == 4
    assert s.bar_start(5) == pytest.approx(4 * BAR) and s.end_s == pytest.approx(16 * BAR)
    with pytest.raises(FileNotFoundError):
        load_song(SONGS, "../songs")


def test_start_publishes_count_in_with_t0_and_song_time():
    ses = Session(SONGS, lead_s=1.0)
    (typ, d), = ses.on_command({"action": "start", "song_id": SONG}, "console", 100.0)
    assert typ == "session.state" and d["state"] == "count_in" and d["reason"] == "start"
    assert d["song_id"] == SONG and d["from_bar"] == 1 and d["bar"] == 1 and d["bpm"] == BPM
    assert d["song_time_at_t0"] == 0.0
    assert d["t0"] == pytest.approx(100.0 + 5 * BEAT + 1.5 + 1.0, abs=1e-4)  # lead >= 5 beats + 1.5 s
    (_, d5), = ses.on_command({"action": "start", "song_id": SONG, "from_bar": 5}, "quest_app", 200.0)
    assert d5["from_bar"] == 5 and d5["song_time_at_t0"] == pytest.approx(4 * BAR, abs=1e-6)


def test_unknown_song_is_refused_and_state_kept():
    ses = Session(SONGS)
    (_, d), = ses.on_command({"action": "start", "song_id": "nope"}, "console", 1.0)
    assert d["state"] == "idle" and "error" in d and ses.state == "idle"


def test_cutoff_restart_seeks_and_finish_publishes_result():
    ses = Session(SONGS, lead_s=0.0, ring_s=1.5, result_delay_s=0.5)
    (_, ci), = ses.on_command({"action": "start", "song_id": SONG}, "console", 0.0)
    t0 = ci["t0"]
    assert ses.tick(t0 - 0.01) == [] and ses.state == "count_in"
    ses.tick(t0 + 0.01)
    assert ses.state == "playing"
    for bar in (1, 2, 3):  # bar 3 drags by 25 ms on average
        for k, err in enumerate((15.0, 35.0) if bar == 3 else (5.0, -4.0)):
            ses.on_note({"grade": "perfect" if abs(err) <= 15 else "good", "error_ms": err, "bar": bar,
                         "beat": 1 + 2 * k}, 0)
    ses.on_note({"grade": "miss", "error_ms": None, "bar": 3, "beat": 4.0}, 0)
    ses.on_note({"grade": "extra", "error_ms": None, "bar": 3, "beat": 4.5}, 0)
    t_cut = t0 + 3 * BAR
    assert ses.on_command({"action": "stop", "from_bar": 1}, "aaron", t_cut) == []
    assert ses.state == "stopped"
    assert ses.tick(t0 + 100.0) == []  # the song clock is paused: no finish while stopped
    # Fletcher's restart: his t0, the controller adds the seek
    (_, rs), = ses.on_command({"action": "restart", "from_bar": 5, "t0": 500.0}, "aaron", 497.0)
    assert rs["state"] == "count_in" and rs["reason"] == "restart" and rs["t0"] == 500.0
    assert rs["from_bar"] == 5 and rs["song_time_at_t0"] == pytest.approx(4 * BAR, abs=1e-6)
    ses.on_state({"state": "playing", "bar": 5}, "chris", 500.1)
    assert ses.state == "playing"
    end = 500.0 + 12 * BAR  # bars 5..16
    assert ses.tick(end + 1.4) == []
    msgs = ses.tick(end + 1.6)
    assert states(msgs, "finished") and ses.state == "finished"
    assert results(ses.tick(end + 1.7)) == []  # waits for the bridge's last misses
    ses.on_note({"grade": "miss", "error_ms": None, "bar": 16, "beat": 4.0}, end + 1.8)
    r, = results(ses.tick(end + 2.2))
    assert r["song_id"] == SONG and r["completed"] is True and r["cutoffs"] == 1
    assert (r["perfect"], r["good"], r["off"], r["miss"], r["extra"]) == (5, 1, 0, 2, 1)
    assert r["notes"] == 8 and r["bars_played"] == 3 + 12
    assert r["dragging_bars"] == [3] and r["rushing_bars"] == []
    assert r["mean_error_ms"] == pytest.approx((2 * (5 - 4) + 15 + 35) / 6, abs=0.05)
    assert results(ses.tick(end + 5)) == []  # once
    out = ses.on_command({"action": "quit"}, "quest_app", end + 6)
    assert results(out) == [] and states(out, "idle") and ses.state == "idle"


def test_quit_mid_song_sends_partial_result_then_idle():
    ses = Session(SONGS)
    ses.on_command({"action": "start", "song_id": SONG}, "console", 0.0)
    ses.on_note({"grade": "perfect", "error_ms": 1.0, "bar": 2, "beat": 1.0}, 9.0)
    out = ses.on_command({"action": "quit"}, "console", 10.0)
    assert [t for t, _ in out] == [SESSION_RESULT, "session.state"]
    r = results(out)[0]
    assert r["completed"] is False and r["perfect"] == 1 and r["bars_played"] == 2
    assert states(out)[0] == {"state": "idle"}
    assert ses.on_command({"action": "restart", "from_bar": 1}, "aaron", 11.0) == []  # nothing to restart


def test_late_joiner_gets_the_current_state():
    ses = Session(SONGS)
    _, ci = ses.on_command({"action": "start", "song_id": SONG}, "console", 0.0)[0]
    assert ses.on_request() == [("session.state", ci)]


# ------------------------------------------------------------------ the bridge ---

def perfect_lines(t0, first_bar=1, latency=0.0, onsets=True):
    """Laptop-clock lines for the fixture played perfectly from bar `first_bar` at t0."""
    song = load_song(SONGS, SONG)
    first = song.bar_start(first_bar)
    return [f"{t0 + n.time - first + latency:.6f}  {0 if onsets else n.midi}  {'onset' if onsets else n.name}"
            for n in cp.read_notes(song.sheet) if n.time >= first - 1e-6]


def bridge_session(**kw):
    return SessionBridge(SONGS, "laptop", **kw), Session(SONGS, lead_s=0.0, ring_s=1.5)


def drive(sb, ses, msgs, t):
    """Pass controller output to the bridge (and bridge output back to the controller)."""
    out = []
    for typ, d in msgs:
        if typ == "session.state":
            out += sb.on_state(d, "session", t)
    for typ, d in out:
        if typ == "judgment.note":
            ses.on_note(d, t)
    return out


@pytest.mark.parametrize("onsets", [True, False])
def test_laptop_clock_perfect_take_is_perfect(onsets):
    sb, ses = bridge_session(input_latency_s=0.05)
    ci = ses.on_command({"action": "start", "song_id": SONG}, "console", 10.0)
    t0 = ci[0][1]["t0"]
    drive(sb, ses, ci, 10.0)
    out = []
    for line in perfect_lines(t0, latency=0.05, onsets=onsets):  # stamped 50 ms late: the mic's latency
        out += sb.feed_line(line, float(line.split()[0]) + 0.2)  # arrival time doesn't matter here
    notes = notes_of(out)
    assert len(notes) == 128 and all(n["grade"] == "perfect" for n in notes)
    assert max(abs(n["error_ms"]) for n in notes) < 0.01
    assert states(out, "playing")[0]["song_id"] == SONG
    assert {n["bar"] for n in notes} == set(range(1, 17))


def test_restart_seeks_the_sheet_and_finish_flushes_misses():
    sb, ses = bridge_session()
    msgs = ses.on_command({"action": "start", "song_id": SONG}, "console", 0.0)
    t0 = msgs[0][1]["t0"]
    drive(sb, ses, msgs, 0.0)
    out = []
    lines = perfect_lines(t0)
    for line in [l for l in lines if float(l.split()[0]) < t0 + 3 * BAR - 0.1]:  # bars 1-3
        out += sb.feed_line(line, float(line.split()[0]))
    for n in notes_of(out):
        ses.on_note(n, t0)
    t_cut = t0 + 3 * BAR
    out += sb.on_command({"action": "stop", "from_bar": 1}, t_cut)
    ses.on_command({"action": "stop", "from_bar": 1}, "aaron", t_cut)
    assert states(out, "stopped")
    assert sb.feed_line(lines[30], t_cut + 1) == []  # muted
    t1 = t_cut + 10.0
    rs = ses.on_command({"action": "restart", "from_bar": 5, "t0": t1}, "aaron", t1 - 2.5)
    drive(sb, ses, rs, t1 - 2.5)
    early = sb.feed_line(f"{t1 - 1.0:.6f}  0  onset", t1 - 1.0)
    assert early == []  # counting in: not judged
    bar16 = t1 + 11 * BAR
    played = perfect_lines(t1, first_bar=5, latency=0.012)  # 12 ms late, bar 16 skipped
    late = [l for l in played if float(l.split()[0]) < bar16]
    n16 = len(played) - len(late)
    res = []
    for line in late:
        res += sb.feed_line(line, float(line.split()[0]))
    ns = notes_of(res)
    assert ns[0]["bar"] == 5 and all(n["grade"] == "perfect" and n["error_ms"] == pytest.approx(12, abs=0.1)
                                     for n in ns if n["grade"] != "miss")
    end = t1 + 12 * BAR
    fin = ses.tick(end + 1.6)
    assert states(fin, "finished")
    flushed = drive(sb, ses, fin, end + 1.6)
    assert len(notes_of(flushed)) == n16 and not any(n["grade"] == "miss" for n in ns)
    assert all(n["grade"] == "miss" and n["bar"] == 16 for n in notes_of(flushed))
    for n in ns:
        ses.on_note(n, end)
    r, = results(ses.tick(end + 3))
    first_run = notes_of(out)
    assert {n["bar"] for n in first_run} == {1, 2, 3} and all(n["grade"] == "perfect" for n in first_run)
    assert r["cutoffs"] == 1 and r["miss"] == n16 and r["perfect"] == len(first_run) + len(ns)
    assert r["notes"] == r["perfect"] + n16 and r["bars_played"] == 3 + 12
    drive(sb, ses, ses.on_command({"action": "quit"}, "console", end + 4), end + 4)
    assert sb.ref is None and sb.feed_line(lines[0], end + 5) == []


def test_new_song_start_reloads_and_ignores_other_sources():
    sb, ses = bridge_session()
    assert sb.on_state({"state": "count_in", "song_id": SONG, "t0": 5.0}, "chris", 0.0) == []
    assert sb.ref is None  # only the controller's count_in counts
    drive(sb, ses, ses.on_command({"action": "start", "song_id": SONG}, "console", 0.0), 0.0)
    first = sb.ref
    drive(sb, ses, ses.on_command({"action": "start", "song_id": SONG}, "console", 50.0), 50.0)
    assert sb.ref is not first and sb.ref.phase == "count_in"  # a start is a fresh run


def test_drifting_take_through_controller_and_bridge(tmp_path):
    """Chris's drift simulator, laptop clock, end to end through both pure cores."""
    song = load_song(SONGS, SONG)
    truth = td.make_drifting(song.sheet, tmp_path / "perf.txt", seed=3, drift_sd=0.03)
    sb, ses = bridge_session()
    msgs = ses.on_command({"action": "start", "song_id": SONG}, "console", 0.0)
    t0 = msgs[0][1]["t0"]
    drive(sb, ses, msgs, 0.0)
    for t, m in sorted(truth):
        for typ, d in sb.feed_line(f"{t0 + t:.6f}  {m}  x", t0 + t):
            if typ == "judgment.note":
                ses.on_note(d, t0 + t)
    end = t0 + song.end_s
    for typ, d in drive(sb, ses, ses.tick(end + 1.6), end + 1.6):
        pass
    r, = results(ses.tick(end + 3))
    assert r["notes"] == 128 and r["perfect"] + r["good"] + r["off"] + r["miss"] == 128
    assert r["completed"] and r["bars_played"] == 16 and r["mean_abs_error_ms"] > 5


def test_unquantized_chords_are_one_group():
    """MIDI imports put chord notes 1-2 ms apart; one onset must take them all (not drop the rest
    as a tail and report misses)."""
    from hub.bridge import ScoreGate
    sheet = [cp.Hit(1.000, 36), cp.Hit(1.002, 49), cp.Hit(1.0015, 42), cp.Hit(1.140, 38)]
    g = ScoreGate(sheet)
    assert g.assign(1.001) == [36, 42, 49] and g.assign(1.141) == [38]


LIBRARY = ROOT / "songs"


@pytest.mark.skipif(not (LIBRARY / "hank_levy-whiplash" / "drums.drums.txt").exists(),
                    reason="agent A's songs/ library not present")
def test_whiplash_7_4_at_214_bpm():
    """Odd meter, fast: the count-in counts beats (t0 - 5 beats for the finger, 4 words on beats
    -4..-1), bars come from the tempo map, and a perfect onsets-only take at 214 bpm (eighths
    ~140 ms apart) is judged perfect by the score gate, note for note."""
    sid = "hank_levy-whiplash"
    song = load_song(LIBRARY, sid)
    assert song.beats_per_bar == 7 and song.bpm_at(1) == 214.0
    beat = 60 / 214.0
    assert song.bar_start(2) == pytest.approx(7 * beat, abs=1e-6)
    sb, ses = SessionBridge(LIBRARY, "laptop"), Session(LIBRARY, lead_s=1.0)
    (_, ci), = ses.on_command({"action": "start", "song_id": sid}, "console", 0.0)
    assert ci["t0"] == pytest.approx(5 * beat + 2.5, abs=1e-3) and ci["beats_per_bar"] == 7
    sb.on_state(ci, "session", 0.0)
    out, last = [], -1.0
    for n in cp.read_notes(song.sheet):
        if n.time - last < 0.03:
            continue  # one detector onset per stroke cluster
        last = n.time
        out += sb.feed_line(f"{ci['t0'] + n.time:.6f}  0  onset", ci["t0"] + n.time)
    out += sb.ref.finish()
    notes = notes_of(out)
    assert len(notes) == len(cp.read_notes(song.sheet)) and all(n["grade"] == "perfect" for n in notes)
    from songsterr import tempo_map as tm
    last = max(n.time for n in cp.read_notes(song.sheet))
    assert max(n["bar"] for n in notes) == tm.bar_at(song.tempo_map, last)[0] > 50  # 7/4 bars, not 4/4
