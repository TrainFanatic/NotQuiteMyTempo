"""python -m pytest fletcher/tests -q"""

import asyncio
import random

from fletcher import brain as B
from fletcher.adapt import Adapter, parse_live_compare_line
from fletcher.config import Config
from fletcher.contract import now, parse
from fletcher.text import SentenceSplitter, mentions_bar, num_words, strip_tags
from fletcher.tirade import Draft, TiradeWriter, canned_line

# The three examples from the plan's "Message contracts" section.
EXAMPLES = [
    '{"type":"judgment.alert","src":"chris","seq":88,"t":18240.112,'
    '"data":{"kind":"rushing","severity":0.72,"since_bar":9,'
    '"detail":"tempo_ratio 1.06 over bars 9-11; snare on 2 and 4 early by ~35 ms"}}',
    '{"type":"fletcher.state","src":"aaron","seq":31,"t":18240.120,'
    '"data":{"mood":"furious","anger":0.86,"action":"fist_cutoff","look_at":"drummer"}}',
    '{"type":"fletcher.speech","src":"aaron","seq":32,"t":18241.400,'
    '"data":{"utterance_id":"u-0007","text":"Were you rushing or were you dragging?",'
    '"stream":true,"sample_rate":24000}}',
]


def cfg(**kw) -> Config:
    c = Config(dry_run=True)
    for k, v in kw.items():
        setattr(c, k, v)
    return c


def test_contract_examples_parse():
    msgs = [parse(e) for e in EXAMPLES]
    assert [m["type"] for m in msgs] == ["judgment.alert", "fletcher.state", "fletcher.speech"]
    assert msgs[0]["data"]["since_bar"] == 9
    assert parse("not json") is None
    assert parse('{"no_type": 1}') is None


def test_adapter_aliases_and_sign():
    a = Adapter(error_sign=-1)
    m = a.normalize({"type": "note", "t": 1.0, "data": {"instrument": "snare", "offset_ms": 30, "measure": 3}})
    assert m["type"] == "judgment.note"
    assert m["data"] == {"drum": "snare", "error_ms": -30.0, "bar": 3, "grade": "good"}


def test_adapter_fills_bar_from_t0():
    a = Adapter()
    a.normalize({"type": "session.state", "data": {"state": "playing", "bpm": 100, "t0": 10.0}})
    m = a.normalize({"type": "judgment.note", "t": 10.0 + 2.4 * 4 + 0.1, "data": {"drum": "kick", "error_ms": 5}})
    assert m["data"]["bar"] == 5


# Real output of Chris's referee: `python live_compare.py bib.drums.txt --input
# bib.drift.drums.txt --plain`, sheet = rushingOrDragging/tests/fixtures/
# back_in_black_drums_4bars.json (94 bpm), performance = tests/test_drift.py clone
# (seed 3, drift sd 0.04 s). Header/summary lines are not judgments.
LIVE_COMPARE_OUTPUT = """\
     0.673s  DRAGGING!  (median +35.1 ms over 1 hit(s))
     2.542s  RUSHING!   (median -11.0 ms over 1 hit(s))
     6.381s  PERFECT!   (median -1.6 ms over 2 hit(s))
     8.605s  RUSHING!   (median -12.2 ms over 3 hit(s))

Live verdicts per hit: perfect 2  rushing 0  dragging 0
  mean error:       +0.4 ms  (+ = late)"""


def test_adapter_reads_live_compare_output():
    a = Adapter()
    a.normalize({"type": "session.state", "data": {"bpm": 94}})
    msgs = [parse_live_compare_line(line) for line in LIVE_COMPARE_OUTPUT.splitlines()]
    notes = [a.normalize(m)["data"] for m in msgs if m]
    assert len(notes) == 4
    assert [n["error_ms"] for n in notes] == [35.1, -11.0, -1.6, -12.2]  # + = late, as in the contract
    assert [n["bar"] for n in notes] == [1, 1, 3, 4]                     # 4 * 60 / 94 = 2.55 s per bar
    assert [n["grade"] for n in notes] == ["off", "perfect", "perfect", "perfect"]
    # his LiveMatcher.feed() errors are seconds; his verdict words are labels
    assert a.normalize({"type": "judgment.note", "data": {"err": 0.0351}})["data"]["error_ms"] == 35.1
    assert a.normalize({"type": "judgment.note", "data": {"error_ms": "perfect"}})["data"]["error_ms"] == 0.0


def test_draft_keeps_an_order_split_across_sentences():
    async def go():
        class W:
            async def sentences(self, facts, level):
                for x in ["[shouting] You dragged!", "[furious] Bar nine.", "NOW!"]:
                    yield x
        f = B.Facts(kind="dragging", severity=0.8, since_bar=9, current_bar=11, from_bar=9)
        d = Draft(W(), f, 2).start()
        await d.done.wait()
        assert not d.order_added and d.text.count("Bar nine") == 1
    asyncio.run(go())


def test_gesture_cues_are_stripped_and_timed_to_sentences():
    from fletcher.main import Fletcher
    from fletcher.text import split_gestures
    from fletcher.voice import TURN
    assert split_gestures("{slam_stand} [screaming] WHAT? {point}") == ("[screaming] WHAT?", ["slam_stand", "point"])
    assert strip_tags("{in_face} [cold] Bar nine.") == "Bar nine."

    class V4:  # yields audio, then a TURN marker, per sentence (like DialogueVoice)
        name, marks_turns = "v4", True
        def __init__(self): self.heard = []
        async def speak(self, sentences):
            async for s in sentences:
                self.heard.append(s)
                yield b"\x00\x00" * 10
                yield b"\x00\x00" * 10
                yield TURN

    class W:
        async def sentences(self, facts, level):
            for x in ["{slam_stand} [screaming] WHAT was THAT, you absolute disaster?",
                      "{turn_away} [cold] Forty milliseconds of pure garbage.",
                      "{spin_back} {point} [screaming] Bar nine. NOW!"]:
                yield x

    async def go():
        f = Fletcher(cfg(lines_dir=""))
        f.voices = [V4()]
        sent = []
        async def send(type_, data, t=None):
            sent.append((type_, data))
            return True
        f.hub.send = send
        facts = B.Facts(kind="rushing", severity=0.8, since_bar=9, current_bar=11, from_bar=9)
        d = Draft(W(), facts, 2).start()
        assert await f.speak(d, now(), now(), facts, 2)
        assert all("{" not in s for s in f.voices[0].heard)  # the voice never sees cues
        cues = [(m["gesture"], m["at_index"], m["action"]) for t, m in sent if t == "fletcher.state" and "gesture" in m]
        assert cues == [("t1_fist_slam", 0, "lean_in"), ("t1_wave_off", 2, "glare"),
                        ("t2_point_player", 4, "point")]  # spin_back + point: one point, not two
        speech = [m for t, m in sent if t == "fletcher.speech"][0]
        assert "{" not in speech["text"] and "[" not in speech["text"]
    asyncio.run(go())


def test_auto_gesture_follows_the_voice():
    from fletcher.tirade import AUTO_GESTURES, auto_gesture
    rng = random.Random(1)
    assert auto_gesture("[mocking] [whiny] \"I felt it.\"", None, rng) in AUTO_GESTURES["mock"]
    assert auto_gesture("[cold] Count it off.", None, rng) in AUTO_GESTURES["quiet"]
    assert auto_gesture("[screaming] NINETY-FIVE?", None, rng) in AUTO_GESTURES["loud"]
    assert all(auto_gesture("[shouting] NO!", "point", rng) != "point" for _ in range(50))


def test_text_helpers():
    assert num_words(9) == "nine" and num_words(42) == "forty-two" and num_words(105) == "one hundred and five"
    assert strip_tags("[sharp] Bar nine. [pause] Now.") == "Bar nine. Now."
    assert mentions_bar("From bar nine. Now.", 9) and mentions_bar("from bar 9", 9)
    assert not mentions_bar("From bar nineteen.", 9)


def test_splitter_keeps_ellipsis_and_tags():
    s = SentenceSplitter()
    out = []
    for piece in ["[quietly] Bar nine", "... you rushed. [shouting] Wh", "y? From bar nine", ". Now."]:
        out += s.feed(piece)
    out += s.flush()
    assert out == ["[quietly] Bar nine... you rushed.", "[shouting] Why?", "From bar nine.", "Now."]


def play(br: B.Brain, t: float = 0.0):
    return br.on_session_state({"state": "playing", "bpm": 100, "bar": 1, "t0": 0.0}, t)


def test_sustained_alert_cuts_off_on_phrase_boundary():
    br = B.Brain(cfg(), random.Random(0))
    play(br)
    t = 0.0
    effects = []
    for bar in range(1, 13):
        t += 2.4
        br.on_session_state({"state": "playing", "bar": bar}, t)
        if bar >= 10:
            effects += br.on_alert({"kind": "rushing", "severity": 0.75, "since_bar": 10,
                                    "detail": "snare early ~35 ms"}, t)
        if br.phase == "cutoff":
            break
    kinds = [type(e).__name__ for e in effects]
    assert "Speculate" in kinds
    cut = next(e for e in effects if isinstance(e, B.Cutoff))
    assert cut.facts.kind == "rushing" and cut.facts.since_bar == 10
    assert cut.facts.from_bar == 9  # phrase start
    assert cut.level == 1
    assert "bar 9 (nine)" in cut.facts.sheet()


def test_grace_bars_block_early_cutoff():
    br = B.Brain(cfg(), random.Random(0))
    play(br)
    eff = br.on_alert({"kind": "dragging", "severity": 1.0, "since_bar": 1}, 0.1)
    eff += br.on_alert({"kind": "dragging", "severity": 1.0, "since_bar": 1}, 0.2)
    assert not any(isinstance(e, B.Cutoff) for e in eff)


def test_notes_only_stream_triggers_cutoff():
    """If the referee only sends early/late per note, the brain finds the drift itself."""
    br = B.Brain(cfg(), random.Random(0))
    play(br)
    t, cut = 0.0, None
    for bar in range(1, 17):
        for beat in range(8):
            t += 0.3
            err = -6.0 * max(0, bar - 4)  # rushing more every bar from bar 5
            for e in br.on_note({"drum": "snare", "bar": bar, "beat": 1 + beat / 2, "error_ms": err,
                                 "grade": "good"}, t):
                if isinstance(e, B.Cutoff):
                    cut = e
        if cut:
            break
    assert cut is not None and cut.facts.kind == "rushing"
    assert cut.facts.per_drum["snare"]["mean_ms"] < 0


def test_patience_shrinks_and_level_escalates():
    br = B.Brain(cfg(), random.Random(0))
    assert br.cut_threshold() == 0.8
    br.cutoffs = 2
    assert abs(br.cut_threshold() - 0.64) < 1e-9
    br.cutoffs = 10
    assert br.cut_threshold() == 0.55


def test_recovery_cancels_speculation():
    br = B.Brain(cfg(), random.Random(0))
    play(br)
    br.bar = 6
    eff = br.on_alert({"kind": "rushing", "severity": 0.6, "since_bar": 5}, 1.0)
    assert any(isinstance(e, B.Speculate) for e in eff)
    br.anger = 0.1
    later = []
    for i in range(60):  # alert expires after 4 s without updates
        later += br.tick(1.0 + i * 0.1)
    assert any(isinstance(e, B.CancelSpeculation) for e in later)


def test_canned_lines_name_the_restart_bar():
    f = B.Facts(kind="dragging", severity=0.8, since_bar=11, current_bar=12, from_bar=9,
                mean_error_ms=42, bpm=100, tempo_ratio=0.96)
    for level in (1, 2, 3):
        for seed in range(5):
            line = " ".join(canned_line(f, level, random.Random(seed)))
            assert mentions_bar(line, 9), line


def test_draft_appends_order_when_missing():
    async def go():
        w = TiradeWriter(cfg())
        f = B.Facts(kind="rushing", severity=0.8, since_bar=10, current_bar=11, from_bar=9)

        async def fake(_facts, _level):
            yield "[sharp] You rushed bar ten."
        w.sentences = fake
        d = Draft(w, f, 2).start()
        assert await d.wait_first(1.0)
        await d.done.wait()
        got = [s async for s in d.iter_sentences()]
        assert got[-1] == "[shouting] Bar nine. NOW!" and d.order_added
    asyncio.run(go())
