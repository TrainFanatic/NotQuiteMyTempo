import json
from collections import Counter
from fractions import Fraction as F
from pathlib import Path

import pytest

from songsterr.convert import convert, extract_events, format_notes, playback_order
from songsterr.tempo_map import (
    bar_at, bar_start, exact_tempo_changes, extract_tempo_map, load_tempo_map, sidecar_path,
    write_tempo_map, written_signatures,
)

FIXTURE = Path(__file__).parent / "fixtures" / "back_in_black_drums_4bars.json"


def bib():
    return json.loads(FIXTURE.read_text())


def beat(dur, *frets, **extra):
    return {"duration": list(dur), "notes": [{"fret": f, "string": 0} for f in frets], **extra}


def written_hits(track):
    """Ground truth straight from the score: Counter of (midi, playback bar, beat) for straight,
    non-grace, non-tied notes, beat in 1/denominator units."""
    ms, sigs = track["measures"], written_signatures(track["measures"])
    out = Counter()
    for bar, idx in enumerate(playback_order(ms), start=1):
        for v in ms[idx].get("voices", []):
            off = F(0)
            for b in v.get("beats", []):
                if not b.get("rest"):
                    for n in b.get("notes", []):
                        if not (n.get("rest") or n.get("tie")):
                            out[(int(n["fret"]), bar, off * sigs[idx][1])] += 1
                off += F(*b["duration"])
    return out


def judged_hits(tm, notes, digits=None):
    out = Counter()
    for n in notes:
        t = n.time_s if digits is None else round(n.time_s, digits)
        bar, b = bar_at(tm, t)
        out[(n.midi, bar, F(b).limit_denominator(96))] += 1
        assert abs(b - float(F(b).limit_denominator(96))) < 1e-6
    return out


# ---------- Back In Black fixture ----------

def test_bib_header_and_grid():
    tm = extract_tempo_map(bib())
    assert tm["format"] == "tempo-map/1"
    assert tm["source"]["songId"] == 1024 and tm["source"]["revisionId"] == 9880696
    assert [b["bar"] for b in tm["bars"]] == [1, 2, 3, 4]
    assert [b["source_measure"] for b in tm["bars"]] == [0, 1, 2, 3]
    for b in tm["bars"]:
        assert (b["numerator"], b["denominator"], b["bpm"]) == (4, 4, 94.0)
        assert b["beat_s"] == float(F(60, 94)) and round(b["beat_s"], 6) == 0.638298
    assert [b["start_s"] for b in tm["bars"]] == [float(F(240, 94) * k) for k in range(4)]
    assert tm["end_s"] == float(F(240, 94) * 4)
    assert tm["tempo_changes"] == [{"time_s": 0.0, "pos_whole": 0.0, "bpm": 94.0, "written_bpm": 94,
                                    "written_unit": 4, "linear": False}]


@pytest.mark.parametrize("digits", [None, 6])  # exact floats, and as read back from .drums.txt
def test_bib_every_note_lands_on_its_written_beat(digits):
    t = bib()
    tm = extract_tempo_map(t)
    notes = convert(t)
    assert notes
    assert judged_hits(tm, notes, digits) == written_hits(t)


def test_bib_downbeat_notes_equal_bar_starts_exactly_and_quarter_spacing_is_beat_s():
    t = bib()
    tm = extract_tempo_map(t)
    notes = convert(t)
    times = sorted({n.time_s for n in notes})
    for b in tm["bars"]:
        if any(bar_at(tm, x) == (b["bar"], 0.0) for x in times):
            assert b["start_s"] in times  # bit-identical float
    # bars 1-2 are written in quarters: consecutive onsets are exactly beat_s apart
    q = [x for x in times if x < tm["bars"][2]["start_s"]]
    gaps = {round(b - a, 9) for a, b in zip(q, q[1:])}
    assert gaps <= {round(tm["bars"][0]["beat_s"] * k, 9) for k in (1, 2, 3, 4)}


def test_roundtrip_bar_start_of_bar_at():
    for t in (bib(), synthetic()):
        tm = extract_tempo_map(t)
        for n in convert(t):
            for x in (n.time_s, round(n.time_s, 6)):
                bar, b = bar_at(tm, x)
                if b == 0.0:
                    assert abs(bar_start(tm, bar) - x) < 1e-6
                assert bar_start(tm, bar) - 1e-6 <= x < bar_start(tm, bar + 1)


def test_exact_tempo_changes_agree_with_extract_events():
    for t in (bib(), synthetic(), {"measures": [{"voices": []}]}):
        _, tempos = extract_events(t)
        assert [(p, s) for p, s, _ in exact_tempo_changes(t)] == tempos


# ---------- synthetic: repeat, 3/4, tempo change at a bar and mid-bar ----------

def synthetic():
    q = lambda *f: beat((1, 4), *f)
    return {
        "songId": 7, "measures": [
            {"signature": [4, 4], "repeatStart": True, "voices": [{"beats": [q(36), q(42), q(38), q(42)]}]},
            {"signature": [3, 4], "repeat": 2, "voices": [{"beats": [q(36), q(42), q(42)]}]},
            {"signature": [4, 4], "voices": [{"beats": [q(36), q(42), q(38), q(42)]}]},
            {"voices": [{"beats": [q(36), q(42), q(38), q(42)]}]},
        ],
        "automations": {"tempo": [
            {"measure": 0, "position": 0, "bpm": 120, "type": 4},
            {"measure": 2, "position": 0, "bpm": 90, "type": 4},
            {"measure": 3, "position": 2, "bpm": 120, "type": 8, "linear": True},  # = 60 quarter bpm
        ]},
    }


def test_synthetic_bar_starts_by_hand():
    tm = extract_tempo_map(synthetic())
    q120, q90, q60 = F(1, 2), F(2, 3), F(1)
    s1 = F(0)
    s2 = s1 + 4 * q120          # 2
    s3 = s2 + 3 * q120          # 7/2   (repeat: back to measure 0)
    s4 = s3 + 4 * q120          # 11/2
    s5 = s4 + 3 * q120          # 7
    s6 = s5 + 4 * q90           # 29/3
    end = s6 + 2 * q90 + 2 * q60  # 13
    assert [b["source_measure"] for b in tm["bars"]] == [0, 1, 0, 1, 2, 3]
    assert [b["start_s"] for b in tm["bars"]] == [float(x) for x in (s1, s2, s3, s4, s5, s6)]
    assert [(b["numerator"], b["denominator"]) for b in tm["bars"]] == [(4, 4), (3, 4)] * 2 + [(4, 4)] * 2
    assert [b["bpm"] for b in tm["bars"]] == [120.0] * 4 + [90.0, 90.0]
    assert tm["bars"][5]["bar_s"] == float(end - s6)
    assert tm["end_s"] == float(end) == 13.0
    assert [(c["time_s"], c["bpm"], c["linear"]) for c in tm["tempo_changes"]] == [
        # measure 0's automation is re-applied on the repeat pass, exactly as extract_events does
        (0.0, 120.0, False), (3.5, 120.0, False), (7.0, 90.0, False), (float(s6 + 2 * q90), 60.0, True)]
    assert [bar_start(tm, k) for k in range(1, 8)] == [float(x) for x in (s1, s2, s3, s4, s5, s6, end)]
    with pytest.raises(IndexError):
        bar_start(tm, 8)


def test_synthetic_notes_after_mid_bar_tempo_change():
    t = synthetic()
    tm = extract_tempo_map(t)
    assert judged_hits(tm, convert(t)) == written_hits(t)
    assert judged_hits(tm, convert(t), 6) == written_hits(t)
    assert bar_at(tm, 12.0) == (6, 3.0)        # last quarter of bar 6, played at 60 bpm
    assert bar_at(tm, 12.5) == (6, 3.5)
    assert bar_at(tm, 2.0 - 4e-7) == (2, 0.0)  # microsecond rounding below a downbeat


def test_eighth_note_meter_beat_s():
    t = {"measures": [{"signature": [6, 8], "voices": [{"beats": [beat((1, 8), 42)] * 6}]}],
         "automations": {"tempo": [{"measure": 0, "position": 0, "bpm": 100, "type": 4}]}}
    tm = extract_tempo_map(t)
    assert tm["bars"][0]["beat_s"] == 0.3 and tm["bars"][0]["bpm"] == 100.0
    assert [bar_at(tm, n.time_s) for n in convert(t)] == [(1, pytest.approx(k)) for k in range(6)]


def test_write_and_load(tmp_path):
    drums = tmp_path / "ac_dc-back_in_black.drums.txt"
    t = bib()
    drums.write_text(format_notes(convert(t)))
    p = write_tempo_map(t, drums)
    assert p == sidecar_path(drums) == tmp_path / "ac_dc-back_in_black.tempo.json"
    tm = load_tempo_map(p)
    assert tm == extract_tempo_map(t)
    assert bar_at(tm, 5.106383) == (3, 0.0)   # the crash downbeat from file_format.md
    bad = tmp_path / "x.json"
    bad.write_text("{}")
    with pytest.raises(ValueError):
        load_tempo_map(bad)


def test_plain_dict_also_accepted():
    tm = json.loads(json.dumps(extract_tempo_map(synthetic())))
    assert bar_at(tm, 7.0) == (5, 0.0)
