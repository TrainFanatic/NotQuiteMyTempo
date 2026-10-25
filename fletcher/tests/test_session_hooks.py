"""Fletcher and the session controller (hub/session.py): python -m pytest fletcher/tests -q

A count_in with reason "start" (a new song) resets his mood and makes Terence count the
drummer in on the controller's t0 with the same count-off cue as after a tirade; the
count_in answering his own restart doesn't; idle / a new song abandon a cutoff in progress;
session.result at the end of a song gets one bark."""

import asyncio

from fletcher import brain as B
from fletcher.config import Config


def make(**kw):
    from fletcher.main import Fletcher
    c = Config(dry_run=True, lines_dir="", **kw)
    f = Fletcher(c)
    sent = []

    async def send(type_, data, t=None):
        sent.append((type_, data))
        return True
    f.hub.send = send
    return f, sent


async def feed(f, typ, data):
    f.hub.inbox.put_nowait({"type": typ, "src": "session", "data": data, "_rx": 0.0})
    task = asyncio.create_task(f._consume())
    for _ in range(20):
        await asyncio.sleep(0)
    task.cancel()


def countoffs(sent):
    return [d for t, d in sent if t == "fletcher.state" and d.get("gesture") == "t0_countoff_finger"]


def start_msg(t0, **kw):
    return {"state": "count_in", "song_id": "s", "title": "Whiplash", "bpm": 94.0, "bar": 1, "beat": 1,
            "from_bar": 1, "t0": t0, "song_time_at_t0": 0.0, "reason": "start", **kw}


def test_song_start_counts_in_on_t0_and_resets_the_mood():
    from fletcher.contract import now

    async def go():
        f, sent = make()
        f.brain.anger, f.brain.cutoffs = 0.7, 3
        t0 = now() + 6.0
        await feed(f, "session.state", start_msg(t0))
        co, = countoffs(sent)
        assert co["t0"] == round(t0, 4) and co["bpm"] == 94.0 and co["priority"] == 2
        assert co["clips"] == ["co_five_sharp", "co_six_sharp", "co_seven_sharp", "co_eight_sharp"]
        assert f.brain.anger == 0.0 and f.brain.cutoffs == 0 and f.brain.phase == "ready"
        assert f.brain.song == "Whiplash"
        barks = [d["clip_id"] for t, d in sent if t == "fletcher.speech"]
        assert barks and barks[0].startswith("top_")  # "From the top." before the finger
        assert f.busy_until >= t0 - 1e-3
    asyncio.run(go())


def test_count_in_of_his_own_restart_is_not_counted_twice():
    async def go():
        f, sent = make(start_bark=False)
        f._restart_t0 = 1234.5
        await feed(f, "session.state", {**start_msg(1234.5, reason="restart"), "from_bar": 5})
        await feed(f, "session.state", {"state": "count_in", "song_id": "s", "bar": 5, "t0": 1234.5,
                                        "song_time_at_t0": 10.2})  # no reason: matched by t0
        assert countoffs(sent) == []
        await feed(f, "session.state", {"state": "count_in", "bar": 9, "t0": 99.0})  # old bridge: no
        assert countoffs(sent) == []
    asyncio.run(go())


def test_quit_abandons_a_cutoff_in_progress():
    async def go():
        f, sent = make()
        f.cut_task = asyncio.create_task(asyncio.sleep(30))
        f.brain.phase = "cutoff"
        await feed(f, "session.state", {"state": "idle"})
        await asyncio.sleep(0)
        assert f.cut_task.cancelled() and f.brain.phase == "idle"
    asyncio.run(go())


def test_finished_stops_judging_and_result_gets_a_bark():
    cfg = Config(dry_run=True)
    b = B.Brain(cfg)
    b.on_session_state({"state": "playing", "bar": 3, "bpm": 94}, 0.0)
    b.on_session_state({"state": "finished", "bar": 16}, 1.0)
    assert b.phase == "stopped"
    good = {"completed": True, "notes": 100, "accuracy": 0.9, "cutoffs": 0}
    assert [e.category for e in b.on_result(good, 2.0)] == ["praise"]
    assert [e.category for e in b.on_result({**good, "accuracy": 0.4}, 2.0)] == ["again"]
    assert b.on_result({**good, "cutoffs": 1}, 2.0) == []
    assert b.on_result({**good, "completed": False}, 2.0) == []  # a quit: nothing to say


def test_song_title_reaches_the_fact_sheet():
    f = B.Facts(kind="rushing", severity=0.8, since_bar=9, current_bar=11, from_bar=9, song="Caravan")
    assert f.sheet().splitlines()[0] == "song: Caravan"
    assert "song:" not in B.Facts(kind="rushing", severity=0.8, since_bar=9, current_bar=11, from_bar=9).sheet()


def test_misses_from_the_last_run_dont_count_after_a_restart():
    """bond_demo regression: drums start in bar 3; run 1's misses in bar 3 made every later run
    (played clean) get cut off as soon as it reached bar 3 again."""
    cfg = Config(dry_run=True)
    b = B.Brain(cfg)
    b.on_session_state({"state": "playing", "bar": 3, "bpm": 143}, 0.0)
    for k in range(5):
        b.on_note({"bar": 3, "beat": 1 + k * 0.5, "grade": "miss", "error_ms": None, "drum": "snare"}, 1.0)
    b.phase = "cutoff"
    b.on_restarted(1)
    b.on_session_state({"state": "count_in", "bar": 1, "bpm": 143}, 2.0)
    b.on_session_state({"state": "playing", "bar": 3, "bpm": 143}, 5.0)
    effects = []
    for k in range(8):
        effects += b.on_note({"bar": 3, "beat": 1 + k * 0.5, "grade": "perfect", "error_ms": 2.0, "drum": "snare"}, 5.0)
    assert not [e for e in effects if isinstance(e, B.Cutoff)] and b.phase == "playing"
    assert "missed_notes" not in b.alerts
