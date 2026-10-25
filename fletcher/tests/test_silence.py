"""Silence, grace and the restart loop guard (headset session 2026-10-25):
python -m pytest fletcher/tests/test_silence.py -q

Notes are shaped like the session bridge's: 8 sheet notes a bar, a miss for every one he
doesn't play, in time order."""

import asyncio
import random

from fletcher import brain as B
from fletcher.config import Config


def cfg(**kw) -> Config:
    c = Config(dry_run=True, lines_dir="")
    for k, v in kw.items():
        setattr(c, k, v)
    return c


def start(br, t=0.0):
    br.on_session_state({"state": "count_in", "bpm": 140, "bar": 1, "from_bar": 1, "t0": t}, t)
    return br.on_session_state({"state": "playing", "bpm": 140, "bar": 1}, t)


def bar_notes(bar, played=(), err=0.0):
    """8 notes in `bar`: indexes in `played` are hits `err` ms off, the rest misses."""
    out = []
    for i in range(8):
        hit = i in played
        out.append({"drum": "snare", "bar": bar, "beat": 1 + i / 2, "error_ms": err if hit else None,
                    "grade": ("perfect" if abs(err) <= 15 else "good" if abs(err) <= 35 else "off") if hit else "miss",
                    "song_time": (bar - 1) * 1.714 + i * 0.214})
    return out


def run(br, bars, t=0.0, first=1):
    """[(bar, effect)] from feeding bars = [(played, err), ...] from bar `first`."""
    eff = []
    for k, (played, err) in enumerate(bars):
        for n in bar_notes(first + k, played, err):
            t += 0.214
            eff += [(first + k, e) for e in br.on_note(n, t)]
            eff += [(first + k, e) for e in br.tick(t)]
        if any(isinstance(e, (B.Cutoff, B.EndSong)) for _, e in eff):
            break
    return eff, t


def of(eff, kind):
    return [(bar, e) for bar, e in eff if isinstance(e, kind)]


def test_silence_is_not_a_mistake_it_is_waiting():
    br = B.Brain(cfg(), random.Random(0))
    start(br)
    eff, _ = run(br, [((), 0.0)] * 10)
    assert not of(eff, B.Cutoff) and not of(eff, B.Speculate)
    assert br.anger == 0.0  # 70 misses, no rage: he hasn't played
    assert not br.alerts
    (wb, w), = of(eff, B.Bark)
    assert w.category == "waiting" and wb == 1 + br.cfg.wait_bars  # once bar 2 has closed silent
    (eb, e), = of(eff, B.EndSong)
    assert e.reason == "no_playing" and eb == 1 + br.cfg.silent_stop_bars


def test_a_lone_onset_a_bar_is_still_silence():
    """One hit in a bar (a stick click, his own bark through the mic) isn't playing."""
    br = B.Brain(cfg(), random.Random(0))
    start(br)
    eff, _ = run(br, [((3,), 60.0)] * 10)
    assert not of(eff, B.Cutoff) and of(eff, B.EndSong)


def test_misses_alone_never_cut_off_without_enough_hits():
    br = B.Brain(cfg(), random.Random(0))
    start(br)
    eff, _ = run(br, [((0, 4), 0.0)] * 2 + [((0,), 0.0), ((), 0.0)] * 4)
    assert not of(eff, B.Cutoff)  # 5 hits < cutoff_min_hits, whatever he dropped


def test_grace_counts_from_his_first_hit():
    """He comes in at bar 4 (after the count-in, or after noodling) and drops half the notes:
    no cutoff before bar 4 + grace_bars."""
    br = B.Brain(cfg(), random.Random(0))
    start(br)
    eff, _ = run(br, [((), 0.0)] * 3 + [((0, 2, 4, 6), 0.0)] * 12)
    cuts = of(eff, B.Cutoff)
    assert cuts and cuts[0][0] >= 4 + br.cfg.grace_bars
    assert cuts[0][1].facts.kind == "missed_notes"


def test_timing_cutoffs_still_work_when_he_plays():
    br = B.Brain(cfg(), random.Random(0))
    start(br)
    eff, _ = run(br, [(range(8), 60.0)] * 12)
    cuts = of(eff, B.Cutoff)
    assert cuts and cuts[0][1].facts.kind == "dragging" and not cuts[0][1].final


def test_silence_after_playing_reraises_nothing():
    """He played late, then stopped: the misses of the bars he sits out must not keep
    re-deriving 'dragging' from his old hits."""
    br = B.Brain(cfg(grace_bars=99), random.Random(0))
    start(br)
    eff, _ = run(br, [(range(8), 30.0)] * 2 + [((), 0.0)] * 10)
    assert not of(eff, B.Cutoff) and of(eff, B.EndSong)


def _session(br, runs):
    """Cut off, restart, ... as main.py does; runs = list of per-run bar specs."""
    cuts, t = [], 0.0
    start(br)
    for spec in runs:
        eff, t = run(br, spec, t)
        c = of(eff, B.Cutoff)
        if not c:
            return cuts, eff
        cuts.append(c[0][1])
        if c[0][1].final:
            br.on_ended()
            return cuts, eff
        br.on_restarted(1)
        br.on_session_state({"state": "count_in", "bpm": 140, "bar": 1, "from_bar": 1, "t0": t}, t)
        br.on_session_state({"state": "playing", "bpm": 140, "bar": 1}, t)
    return cuts, []


def test_restart_loop_gives_up_without_improvement():
    br = B.Brain(cfg(), random.Random(0))
    cuts, _ = _session(br, [[(range(8), 60.0)] * 20] * 10)
    assert len(cuts) == 1 + br.cfg.give_up_after  # the first, then give_up_after no better
    assert [c.final for c in cuts] == [False] * br.cfg.give_up_after + [True]
    assert cuts[-1].facts.final and "NO restart" in cuts[-1].facts.sheet()
    assert br.closed
    assert br.on_note(bar_notes(5)[0], 99.0) == []  # notes in flight after the end: ignored


def test_restart_loop_cap_even_when_improving():
    br = B.Brain(cfg(give_up_after=0, max_cutoffs=3), random.Random(0))
    cuts, _ = _session(br, [[(range(8), 60.0)] * 20] * 10)
    assert [c.final for c in cuts] == [False, False, True]


def test_main_ends_the_song_with_a_quit_and_no_tirade():
    from fletcher.main import Fletcher

    async def go():
        f = Fletcher(cfg())
        sent = []

        async def send(type_, data, t=None):
            sent.append((type_, data))
            return True
        f.hub.send = send
        await f.apply([B.EndSong("no_playing", 7)])
        cmds = [d for t, d in sent if t == "session.command"]
        assert cmds == [{"action": "quit", "reason": "no_playing"}]
        assert not [d for t, d in sent if t == "fletcher.speech"]
        assert f.brain.closed
    asyncio.run(go())


def test_main_final_cutoff_quits_instead_of_restarting():
    from fletcher.main import Fletcher

    async def go():
        f = Fletcher(cfg(post_stare_s=0.0, stare_s=0.0))
        sent = []

        async def send(type_, data, t=None):
            sent.append((type_, data))
            return True

        async def speak(draft, t_cut, speak_at, facts, level):
            await draft.done.wait()
            from fletcher.contract import now
            f.speech_end = now()
            return True
        f.hub.send, f.speak = send, speak
        br = f.brain
        start(br)
        facts = B.Facts("dragging", 1.0, 1, 6, 1, final=True, cutoff_number=4)
        await f.cutoff(facts, 3)
        cmds = [d["action"] for t, d in sent if t == "session.command"]
        assert cmds == ["stop", "quit"]
        assert not [d for t, d in sent if t == "fletcher.state" and d.get("gesture") == "t0_countoff_finger"]
    asyncio.run(go())
