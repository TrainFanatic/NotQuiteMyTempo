import json
from pathlib import Path

import pytest

from songsterr.convert import convert, format_notes, playback_order
from songsterr.fetch import parse_song_id, pick_drum_track

FIXTURES = Path(__file__).parent / "fixtures"


def beat(dur, *frets, **extra):
    return {"duration": list(dur), "notes": [{"fret": f, "string": 0} for f in frets], **extra}


def rest(dur):
    return {"duration": list(dur), "rest": True, "notes": [{"rest": True}]}


def measure(*beats, voices=None, **extra):
    return {"voices": voices or [{"beats": list(beats)}], **extra}


def track(*measures, tempo=({"measure": 0, "position": 0, "bpm": 120, "type": 4},)):
    return {"measures": list(measures), "automations": {"tempo": list(tempo)}}


def times(notes, midi=None):
    return [round(n.time_s, 6) for n in notes if midi is None or n.midi == midi]


def test_eighth_hihats_at_120():
    t = track(measure(*[beat((1, 8), 42) for _ in range(8)], signature=[4, 4]))
    assert times(convert(t)) == [0, 0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75]


def test_default_tempo_when_missing():
    t = {"measures": [measure(beat((1, 4), 36), beat((1, 4), 36), signature=[2, 4])]}
    assert times(convert(t)) == [0, 0.5]


def test_tempo_change_between_measures():
    m = measure(*[beat((1, 4), 38) for _ in range(4)], signature=[4, 4])
    t = track(m, m, tempo=[{"measure": 0, "position": 0, "bpm": 120, "type": 4},
                          {"measure": 1, "position": 0, "bpm": 60, "type": 4}])
    assert times(convert(t)) == [0, 0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 5.0]


def test_tempo_change_mid_measure_by_beat_position():
    m = measure(*[beat((1, 4), 38) for _ in range(4)], signature=[4, 4])
    t = track(m, tempo=[{"measure": 0, "position": 0, "bpm": 120, "type": 4},
                        {"measure": 0, "position": 2, "bpm": 60, "type": 4}])
    assert times(convert(t)) == [0, 0.5, 1.0, 2.0]


def test_dotted_and_triplets_use_precomputed_duration():
    t = track(measure(
        beat((3, 8), 36, dots=1), beat((1, 8), 38),
        beat((1, 12), 42, tuplet=3), beat((1, 12), 42, tuplet=3), beat((1, 12), 42, tuplet=3),
        beat((1, 4), 38), signature=[4, 4]))
    assert times(convert(t)) == [0, 0.75, 1.0, round(1 + 1 / 6, 6), round(1 + 2 / 6, 6), 1.5]


def test_time_signature_change_carries_forward():
    t = track(
        measure(*[beat((1, 8), 42) for _ in range(9)], signature=[9, 8]),
        measure(beat((1, 4), 36), beat((1, 4), 36), beat((1, 4), 36), beat((1, 4), 36), signature=[4, 4]),
        measure(beat((1, 1), 49)),
    )
    notes = convert(t)
    assert times(notes, 36)[0] == 2.25
    assert times(notes, 49) == [4.25]


def test_rests_and_ties_skipped():
    t = track(measure(rest((1, 4)), beat((1, 4), 38),
                      {"duration": [1, 2], "notes": [{"fret": 49, "string": 0, "tie": True}]},
                      signature=[4, 4]))
    assert [(round(n.time_s, 6), n.midi) for n in convert(t)] == [(0.5, 38)]


def test_two_voices_merged_and_sorted():
    hands = {"beats": [beat((1, 8), 42) for _ in range(8)]}
    feet = {"beats": [beat((1, 4), 36), beat((1, 4), 38), beat((1, 4), 36), beat((1, 4), 38)]}
    notes = convert(track(measure(voices=[hands, feet], signature=[4, 4])))
    assert [(round(n.time_s, 2), n.midi) for n in notes[:4]] == [(0, 36), (0, 42), (0.25, 42), (0.5, 38)]


def test_short_voice_does_not_shift_next_measure():
    t = track(measure(beat((1, 4), 38), signature=[4, 4]), measure(beat((1, 4), 36)))
    assert times(convert(t)) == [0, 2.0]


def test_grace_notes_take_no_time():
    t = track(measure(
        beat((1, 32), 38, graceNote="beforeBeat"), beat((1, 4), 38),
        beat((1, 64), 38, graceNote="onBeat"), beat((3, 4), 36), signature=[4, 4]))
    assert times(convert(t)) == [-0.0625, 0, 0.5, 0.5]


def test_swing_eighths():
    t = track(measure(*[beat((1, 8), 42) for _ in range(4)], signature=[2, 4], tripletFeel="8th"),
              measure(beat((1, 8), 42), beat((1, 8), 42)))  # feel carries forward
    assert times(convert(t)) == [0, round(1 / 3, 6), 0.5, round(0.5 + 1 / 3, 6), 1.0, round(1 + 1 / 3, 6)]


def test_simple_repeat():
    ms = [measure(beat((1, 1), 36)), measure(beat((1, 1), 38), repeatStart=True),
          measure(beat((1, 1), 42), repeat=3), measure(beat((1, 1), 49))]
    assert playback_order(ms) == [0, 1, 2, 1, 2, 1, 2, 3]


def test_single_measure_repeat_then_new_section():  # as in Songsterr's "Take Five"
    ms = [measure(repeatStart=True, repeat=4), measure(repeatStart=True, repeat=3)]
    assert playback_order(ms) == [0, 0, 0, 0, 1, 1, 1]


def test_alternate_endings():
    ms = [measure(repeatStart=True), measure(alternateEnding=[1], repeat=2),
          measure(alternateEnding=[2]), measure()]
    assert playback_order(ms) == [0, 1, 0, 2, 3]


def test_implicit_repeat_start_after_closed_section():
    ms = [measure(repeatStart=True, repeat=2), measure(), measure(repeat=2)]
    assert playback_order(ms) == [0, 0, 1, 2, 1, 2]


def test_repeat_timestamps():
    ms = [measure(beat((1, 4), 36), beat((1, 4), 38), signature=[2, 4], repeatStart=True, repeat=2)]
    assert times(convert(track(*ms))) == [0, 0.5, 1.0, 1.5]


def test_real_back_in_black_fixture():
    t = json.loads((FIXTURES / "back_in_black_drums_4bars.json").read_text())
    notes = convert(t)
    q = 60 / 94
    assert times(notes)[:4] == [round(i * q, 6) for i in range(4)]
    bar3 = [n for n in notes if abs(n.time_s - 8 * q) < 1e-9]
    assert sorted(n.name for n in bar3) == ["crash_2", "kick"]
    assert all(n.midi in (36, 38, 42, 57) for n in notes)


def test_format():
    t = track(measure(beat((1, 4), 36, 42), signature=[1, 4]))
    out = format_notes(convert(t), ["song: X"])
    assert out == "# song: X\n# time_s  midi  name\n0.000000  36  kick\n0.000000  42  hihat_closed\n"


@pytest.mark.parametrize("given,expected", [
    ("1024", 1024),
    ("https://www.songsterr.com/a/wsa/ac-dc-back-in-black-drum-tab-s1024", 1024),
    ("https://www.songsterr.com/a/wsa/ac-dc-back-in-black-drum-tab-s1024t6", 1024),
    ("https://www.songsterr.com/a/wsa/red-hot-chili-peppers-scar-tissue-tab-s49?x=1", 49),
])
def test_parse_song_id(given, expected):
    assert parse_song_id(given) == expected


def test_pick_drum_track():
    meta = {"tracks": [{"instrumentId": 29}, {"instrumentId": 1024}, {"instrumentId": 1024}],
            "popularTrackDrum": 2}
    assert pick_drum_track(meta) == 2
    assert pick_drum_track({"tracks": meta["tracks"]}) == 1
    with pytest.raises(LookupError):
        pick_drum_track({"tracks": [{"instrumentId": 29, "name": "Gtr"}]})
