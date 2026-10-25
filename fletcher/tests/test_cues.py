"""Body-cue choreography: python -m pytest fletcher/tests -q"""

import asyncio
import json
import random

from fletcher import brain as B
from fletcher.config import Config
from fletcher import cues as C
from fletcher.cues import BarkCues, bark_lead, busy_s, countoff_t0, gesture_move, move_s, post_line
from fletcher.tirade import Draft
from fletcher.voice import TURN


def make(tmp_path, **kw):
    from fletcher.main import Fletcher
    c = Config(dry_run=True, lines_dir="")
    for k, v in kw.items():
        setattr(c, k, v)
    f = Fletcher(c)
    f.bark_cues = BarkCues(tmp_path / "none.json")  # the defaults, whatever barks/cues.json says
    sent = []

    async def send(type_, data, t=None):
        sent.append((type_, data))
        return True
    f.hub.send = send
    return f, sent


def cue_list(sent):
    out = []
    for t, m in sent:
        if t == "fletcher.state" and "gesture" in m:
            k = next(k for k in ("at_index", "at_s", "after_s", "t0") if k in m)
            out.append((m["gesture"], k, m[k], m.get("priority", 1), m["utterance_id"]))
    return out


def test_cutoff_line_restart_is_choreographed(tmp_path, monkeypatch):
    real_sleep = asyncio.sleep
    monkeypatch.setattr(asyncio, "sleep", lambda s, *a: real_sleep(0))

    class V4:
        name, marks_turns = "v4", True
        async def speak(self, sentences):
            async for _ in sentences:
                yield b"\x00\x00" * 2400  # 0.1 s
                yield b"\x00\x00" * 2400
                yield TURN

    class W:
        async def sentences(self, facts, level):
            for x in ["{slam_stand} [screaming] WHAT was THAT, you absolute disaster?",
                      "[cold] Forty milliseconds ahead of me, every single bar.",
                      "[cold] Do you think this is some kind of joke to me?",
                      "{point} [screaming] Bar nine, right now, from the top of it!"]:
                yield x

    async def go():
        f, sent = make(tmp_path, stare_s=0.0)
        f.voices = [V4()]
        f.writer.rng = random.Random(1)
        f.brain.phase, f.brain.bpm, f.brain.anger = "cutoff", 120.0, 0.9
        facts = B.Facts(kind="rushing", severity=0.8, since_bar=9, current_bar=11, from_bar=9)
        f.draft = Draft(W(), facts, 2).start()
        await f.cutoff(facts, 2)
        cues = cue_list(sent)
        assert cues[:-1] == [
            ("t1_fist_cutoff", "at_s", 0.0, 2, "u-0001"),         # rides on the "Stop!" bark
            ("t1_fist_slam", "at_index", 0, 1, "u-0002"),         # the line, gesture names -> move ids
            ("t1_wave_off", "at_index", 4, 1, "u-0002"),          # auto cue after two bare sentences
            ("t2_point_player", "at_index", 6, 1, "u-0002"),
            ("t2_judging_stare", "after_s", 0.0, 1, "u-0002"),    # after it: a beat of stare
        ]
        assert cues[-1][0] == "t0_countoff_finger" and cues[-1][1] == "t0" and cues[-1][3] == 2
        co = [m for t, m in sent if t == "fletcher.state" and "t0" in m][0]
        assert co["bpm"] == 120.0 and co["clips"] == ["co_five_sharp", "co_six_sharp", "co_seven_sharp",
                                                      "co_eight_sharp"]
        types = [(t, m.get("clip_id") or m.get("action")) for t, m in sent if t != "fletcher.audio"]
        assert types[:2] == [("fletcher.state", "fist_cutoff"), ("session.command", "stop")]
        assert types[2][0] == "fletcher.speech" and types[2][1].startswith("stop_")
        assert types[-1] == ("session.command", "restart")  # the brain's own state is held for the count-off
        plain = [m for t, m in sent if t == "fletcher.state" and "gesture" not in m]
        assert [(m["action"], m.get("priority")) for m in plain] == [("lean_in", 0)]
        assert f.held is not None and f._busy()
    asyncio.run(go())


def test_post_line_fits_moves_before_the_count_off():
    # a 0.4 s stare before the finger: no room for the point (1.5 s at 120 bpm)
    assert post_line(120, 0.4, "t2_point_player") == [("t2_judging_stare", "after_s", 0.0, 1)]
    assert post_line(120, 2.0, "t2_point_player") == [("t2_judging_stare", "after_s", 0.0, 1),
                                                      ("t2_point_player", "after_s", 0.4, 1)]
    # t0 = line end + stare + 5 beats: the finger move (5 beats long) starts right after the stare
    assert abs(countoff_t0(10.0, 120, 0.4) - (10.0 + 0.4 + 2.5)) < 1e-9
    assert abs(countoff_t0(10.0, 60, 0.4) - (10.0 + 0.4 + 5.0)) < 1e-9


def test_countoff_runs_on_one_clock(tmp_path, monkeypatch):
    """The count-off cue carries t0; the restart command goes out exactly count_in_beats before
    it (the bridge starts the bar then), and t0 is the line's end + post stare + 5 beats."""
    import fletcher.main as M
    clock = [1000.0]
    monkeypatch.setattr(M, "now", lambda: clock[0])

    async def fake_sleep(s, *a):
        clock[0] += max(0.0, s)
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    async def go():
        f, sent = make(tmp_path, stare_s=0.0, post_stare_s=0.4)
        stamped = []
        inner = f.hub.send

        async def send(t, d, *a):
            stamped.append((clock[0], t, d))
            return await inner(t, d)
        f.hub.send = send
        f.voices = []  # barks only: the bar clip is the "line"
        f.brain.phase, f.brain.bpm = "cutoff", 94.0
        facts = B.Facts(kind="rushing", severity=0.8, since_bar=9, current_bar=11, from_bar=9)
        f.draft = Draft(None, facts, 1, canned=True).start()
        await f.cutoff(facts, 1)
        co = [d for _, t, d in stamped if t == "fletcher.state" and "t0" in d][0]
        restart = [ts for ts, t, d in stamped if t == "session.command" and d["action"] == "restart"][0]
        beat = 60 / 94
        assert abs(restart - (co["t0"] - 4 * beat)) < 1e-3
        assert abs(co["t0"] - (f.speech_end + 0.4 + 5 * beat)) < 1e-3
        assert co["t0"] > f.speech_end  # never before the line ends
    asyncio.run(go())


def test_bark_lead_and_stop_fist_on_the_word(tmp_path):
    assert bark_lead([0.1, -0.12], 0.3) == 0.12
    assert bark_lead([-0.5], 0.3) == 0.3 and bark_lead([0.2], 0.3) == 0.0
    real = BarkCues()  # the shipped cues.json: stop_1's fist frame (v004: 14) on its word (0.328 s)
    cut = C.MOVES["t1_fist_cutoff"]["cutoff"]
    assert cut == 14
    assert real.fist_at_s("stop_1", 94) == round(0.328 - cut / 24 * 120 / 94, 3)  # -0.417: lead it (capped)
    p = tmp_path / "cues.json"
    p.write_text(json.dumps({"no_1": {"cues": [{"gesture": "t1_flick", "at_s": 0, "key_frame": 10,
                                                "emphasis_s": 0.217}]}}))

    async def go():
        f, sent = make(tmp_path, bark_lead_max_s=0.3)
        f.bark_cues = BarkCues(p)
        f.brain.phase, f.brain.bpm = "playing", 94.0
        dur = await f._send_clip("no_1", "No.", category="no")
        at = cue_list(sent)[0][2]
        assert at == round(0.217 - 10 / 24 * 120 / 94, 3) < 0  # sent unclamped
        assert abs(dur - (f.barks.duration("no_1") + min(0.3, -at))) < 1e-9  # the held clip ends later
    asyncio.run(go())


def test_cutoff_stop_gets_the_longer_lead(tmp_path, monkeypatch):
    """Only the cutoff "Stop!" may be held up to cutoff_lead_max_s; play barks keep bark_lead_max_s."""
    real_sleep = asyncio.sleep
    monkeypatch.setattr(asyncio, "sleep", lambda s, *a: real_sleep(0))

    async def go():
        f, sent = make(tmp_path, stare_s=0.0, bark_lead_max_s=0.3, cutoff_lead_max_s=0.5)
        f.bark_cues = BarkCues()  # the shipped cues.json (stop_1/stop_2 fist on the word)
        f.voices = []
        held = []
        inner = f._send_clip

        async def spy(clip_id, text, category="bar", lead_s=0.0):
            held.append((clip_id, lead_s))
            return await inner(clip_id, text, category, lead_s)
        f._send_clip = spy
        f.brain.phase, f.brain.bpm = "cutoff", 94.0
        facts = B.Facts(kind="rushing", severity=0.8, since_bar=9, current_bar=11, from_bar=9)
        f.draft = Draft(None, facts, 1, canned=True).start()
        await f.cutoff(facts, 1)
        stop, lead = held[0]
        fist = [m for t, m in sent if t == "fletcher.state" and m.get("gesture") == "t1_fist_cutoff"][0]
        assert stop.startswith("stop_") and lead == round(-fist["at_s"], 3) and 0.3 < lead <= 0.5  # ~0.42 at 94
    asyncio.run(go())


def test_gestures_stay_put_unless_closeups(tmp_path):
    assert [gesture_move(g) for g in ("in_face", "beat_time", "slow_clap", "slam_stand", "t3_loom")] == \
        ["t2_point_player", "t0_clap_off", "t0_clap_off", "t1_fist_slam", "t2_point_player"]
    assert gesture_move("in_face", closeups=True) == "t3_loom"

    async def go():
        for closeups, want in ((False, "t2_point_player"), (True, "t3_loom")):
            f, sent = make(tmp_path, closeups=closeups)
            await f.send_gestures(["in_face"], "u-0009", 3)
            assert cue_list(sent) == [(want, "at_index", 3, 1, "u-0009")]
    asyncio.run(go())


def test_barks_carry_cues_and_hold_the_brain(tmp_path):
    async def go():
        f, sent = make(tmp_path)
        f.brain.phase = "playing"
        await f.bark("rushing")
        speech = [m for t, m in sent if t == "fletcher.speech"][0]
        assert cue_list(sent) == [("t1_wave_off", "at_s", 0.0, 1, speech["utterance_id"])]
        assert sent[0][0] == "fletcher.state"  # the cue goes out before the clip
        n = len(sent)
        await f.apply([B.SetState("irritated", 0.5, "glare")])
        assert len(sent) == n and f.held.action == "glare"  # no move change over the bark
        f.busy_until = 0.0
        assert not f._busy()
    asyncio.run(go())


def test_bark_cue_file_and_no_close_ups_while_playing(tmp_path):
    p = tmp_path / "cues.json"
    p.write_text(json.dumps({"no_2": {"cues": [{"gesture": "t4_charge", "at_s": 0.2, "priority": 2},
                                               {"gesture": "t1_flick", "at_s": 1.5, "priority": 2}]}}))
    c = BarkCues(p)
    assert c.get("no_2", "no", playing=True) == [("t1_flick", 1.5, 1)]
    assert c.get("no_2", "no", playing=False) == [("t4_charge", 0.2, 2), ("t1_flick", 1.5, 2)]
    assert c.get("no_1", "no", playing=True) == [("t1_wave_off", 0.0, 1)]  # not in the file: default
    assert c.get("stop_1", "stop", playing=True) == [("t1_fist_cutoff", 0.0, 1)]
    p.write_text(json.dumps({"no_1": {"cues": [{"gesture": "t1_flick", "at_s": 0.1, "key_frame": 10,
                                                "emphasis_s": 0.517}]}}))
    c = BarkCues(p)  # key frame 10 = 0.417 s in at 120 bpm, 0.5 s at 100: start earlier, never before 0
    assert [c.get("no_1", "no", True, bpm)[0][1] for bpm in (None, 120, 100, 60)] == [0.1, 0.1, 0.017, -0.316]
    # two cues: the second is dropped when it would start while the first still plays
    p.write_text(json.dumps({"r": {"cues": [{"gesture": "t1_flick", "at_s": 0.0},
                                            {"gesture": "t2_point_player", "at_s": 0.83}]}}))
    c = BarkCues(p)
    assert [g for g, _, _ in c.get("r", "rushing", True, 120)] == ["t1_flick", "t2_point_player"]
    assert [g for g, _, _ in c.get("r", "rushing", True, 94)] == ["t1_flick"]  # flick holds to its release, 1.06 s


def test_closeups_walk_in_at_the_stop_bark_and_wait_to_count_off(tmp_path, monkeypatch):
    import fletcher.main as M
    clock = [50.0]
    monkeypatch.setattr(M, "now", lambda: clock[0])

    async def fake_sleep(s, *a):
        clock[0] += max(0.0, s)
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    async def go():
        f, sent = make(tmp_path, stare_s=0.0, post_stare_s=0.4, closeups=True)
        f.voices = []
        f.brain.phase, f.brain.bpm = "cutoff", 120.0
        facts = B.Facts(kind="rushing", severity=0.8, since_bar=9, current_bar=11, from_bar=9)
        f.draft = Draft(None, facts, 1, canned=True).start()
        await f.cutoff(facts, 1)
        cues = cue_list(sent)
        assert cues[1] == ("t3_loom", "after_s", 0.0, 1, "u-0001")  # walks in after the fist
        co = [m for t, m in sent if t == "fletcher.state" and "t0" in m][0]
        assert abs(co["t0"] - (f.speech_end + 0.4 + 4.0 + 2.5)) < 1e-3  # + closeup_return_s at 120 bpm
    asyncio.run(go())


def test_v004_moves_and_holds():
    """Lengths and key frames come from Adam's moves json; held accents give way at their release."""
    assert C.FRAMES["t1_fist_cutoff"] == 72 and C.RELEASE["t1_fist_cutoff"] == 50
    assert C.RELEASE == {"t1_fist_cutoff": 50, "t1_fist_slam": 26, "t1_flick": 20, "t1_wave_off": 28, "t1_clap_stop": 23}
    assert "t5_chair_throw" not in C.RELEASE  # its "release" lets go of the chair: not a hold
    assert abs(move_s("t1_fist_cutoff", 120) - 3.0) < 1e-9 and abs(busy_s("t1_fist_cutoff", 120) - 50 / 24) < 1e-9
    assert busy_s("t2_point_player", 94) == move_s("t2_point_player", 94)
    assert C.FIRST_FINGER == 12 and C.COUNTOFF_LEAD_BEATS == 5.0


def test_tirade_starts_during_the_fist_hold(tmp_path, monkeypatch):
    """The line doesn't wait for the 3 s fist: it starts once the Stop bark is over, inside the
    fist's hold (hold_start..release), and its first gesture is left to the headset's release."""
    import fletcher.main as M
    clock = [100.0]
    monkeypatch.setattr(M, "now", lambda: clock[0])

    async def fake_sleep(s, *a):
        clock[0] += max(0.0, s)
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    class V4:
        name, marks_turns = "v4", True
        async def speak(self, sentences):
            async for _ in sentences:
                yield b"\x00\x00" * 2400
                yield TURN

    class W:
        async def sentences(self, facts, level):
            yield "{slam_stand} [screaming] WHAT was THAT?"

    async def go():
        f, sent = make(tmp_path, stare_s=0.5, quest_latency_s=0.0)
        f.bark_cues = BarkCues()  # the shipped cues: the fist's cutoff frame on "Stop!"
        stamped = []
        inner = f.hub.send

        async def send(t, d, *a):
            stamped.append((clock[0], t, d))
            return await inner(t, d)
        f.hub.send = send
        f.voices = [V4()]
        f.brain.phase, f.brain.bpm = "cutoff", 120.0
        facts = B.Facts(kind="rushing", severity=0.8, since_bar=9, current_bar=11, from_bar=9)
        f.draft = Draft(W(), facts, 2).start()
        await f.cutoff(facts, 2)
        t_cut = stamped[0][0]
        fist = next(d for _, t, d in stamped if t == "fletcher.state" and d.get("gesture") == "t1_fist_cutoff")
        assert fist["at_s"] < 0  # so the headset starts the fist at once (t_cut) and holds the bark back
        line = next(ts for ts, t, d in stamped if t == "fletcher.speech" and d.get("stream")) - t_cut
        hold = (C.MOVES["t1_fist_cutoff"]["hold_start"] / 24, C.RELEASE["t1_fist_cutoff"] / 24)  # 0.79..2.08 s
        assert hold[0] <= line < hold[1], (line, hold)
    asyncio.run(go())
