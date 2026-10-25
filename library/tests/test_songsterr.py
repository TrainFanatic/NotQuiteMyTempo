"""Songsterr path, offline: Chris's real 4-bar Back In Black drum fixture + synthetic pitched tracks."""

import copy
import json
from fractions import Fraction
from pathlib import Path

import numpy as np
import pytest

import compare_performance as cp
from songsterr import convert as conv
from songsterr import fetch
from songsterr import tempo_map as tm

from library import smf, songsterr_band
from library.tests.conftest import ROOT, needs_fluidsynth

FIXTURE = ROOT / "rushingOrDragging" / "tests" / "fixtures" / "back_in_black_drums_4bars.json"


def drums():
    return json.loads(FIXTURE.read_text())


def bass_track(name="Bass", tempo_change=True):
    """4 bars of 4/4: quarter notes E1+fret on string 3, a tie across bar 2->3, a repeat of bar 4."""
    q = lambda fret, **kw: {"notes": [{"fret": fret, "string": 3, **kw}], "duration": [1, 4], "type": 4}
    measures = [
        {"signature": [4, 4], "voices": [{"beats": [q(0), q(2), q(3), q(5)]}]},
        {"voices": [{"beats": [q(0), q(0), q(0), q(7)]}]},
        {"voices": [{"beats": [q(7, tie=True), {"rest": True, "notes": [{"rest": True}], "duration": [3, 4]}]}]},
        {"voices": [{"beats": [{"notes": [{"fret": 12, "string": 3}], "duration": [1, 1]}]}]},
    ]
    return {"name": name, "instrumentId": 33, "instrument": "Electric Bass (finger)",
            "tuning": [43, 38, 33, 28], "measures": measures}


def test_pitched_notes_positions_pitch_and_ties():
    notes = songsterr_band.pitched_notes(bass_track())
    assert [(float(a), float(b), p) for a, b, p, _ in notes[:4]] == [
        (0, .25, 28), (.25, .5, 30), (.5, .75, 31), (.75, 1, 33)]
    tied = [n for n in notes if n[2] == 35]
    assert len(tied) == 1 and tied[0][0] == Fraction(7, 4) and tied[0][1] == Fraction(9, 4)
    assert notes[-1][:3] == (Fraction(3), Fraction(4), 40)


def test_arrangement_lines_up_with_chris_sheet():
    d = drums()
    mf, band = songsterr_band.build_arrangement(d, [(0, bass_track()), (1, bass_track("Trumpet 1"))])
    mf = smf.parse(mf.to_bytes())
    assert [t.key for t in mf.tracks] == ["Songsterr", "Drums", "Bass", "Trumpet 1"]
    assert band == {"Bass": ["bass"], "Trumpet 1": ["trumpet"]}
    sec = mf.tick_seconds()
    mine = sorted(float(sec(n.start_tick)) for n in mf.tracks[1].notes())
    chris = sorted(n.time_s for n in conv.convert(d))
    assert len(mine) == len(chris) and max(abs(a - b) for a, b in zip(mine, chris)) < 5e-4
    bars = tm.extract_tempo_map(d)["bars"]
    ns = mf.tracks[2].notes()
    starts = [next(n for n in ns if n.note == 28), next(n for n in ns if n.note == 40)]
    for n, b in zip(starts, (bars[0], bars[3])):
        assert float(sec(n.start_tick)) == pytest.approx(b["start_s"], abs=5e-4)


def test_mismatched_repeat_structure_is_skipped():
    other = bass_track()
    other["measures"][1]["repeatStart"] = True
    other["measures"][2]["repeat"] = 2
    mf, band = songsterr_band.build_arrangement(drums(), [(0, other)])
    assert [t.key for t in mf.tracks] == ["Songsterr", "Drums"] and band == {}


@pytest.fixture
def offline(monkeypatch):
    d = drums()
    meta = {"songId": 1024, "revisionId": 9880696, "title": "Back In Black", "artist": "AC/DC", "image": "x",
            "tracks": [{"name": "Bass", "instrumentId": 33}, {"name": "Drums", "instrumentId": 1024}]}
    monkeypatch.setattr(fetch, "fetch_drums", lambda song, track=None, refresh=False: (meta, 1, copy.deepcopy(d)))
    monkeypatch.setattr(fetch, "get_track", lambda *a, **k: bass_track())
    return d


def test_import_songsterr_sheet_is_chris_output(offline, tmp_path):
    from library.songsterr_import import import_songsterr
    info = import_songsterr("1024", songs_dir=tmp_path, band=False, log=lambda *_: None)
    assert info["id"] == "ac_dc-back_in_black" and info["source"] == "songsterr"
    assert not info["backing"] and not info["arrangement"]
    d = tmp_path / info["id"]
    header = ["song: Back In Black - AC/DC", "songId=1024 revisionId=9880696 track=1 (%s)" % offline.get("name", "?")]
    assert (d / "drums.drums.txt").read_text() == conv.format_notes(conv.convert(offline), header)
    assert tm.load_tempo_map(d / "drums.tempo.json")["bars"] == tm.extract_tempo_map(offline)["bars"]
    assert len(cp.read_notes(d / "drums.drums.txt")) > 0
    assert info["bpm"] == 94 and info["bars"] == 4


@needs_fluidsynth
def test_import_songsterr_with_band_renders_backing(offline, tmp_path):
    import soundfile as sf
    from library.songsterr_import import import_songsterr
    info = import_songsterr("1024", songs_dir=tmp_path, log=lambda *_: None)
    d = tmp_path / info["id"]
    assert info["backing"] and info["arrangement"]
    assert json.loads((d / "band_tracks.json").read_text()) == {"Bass": ["bass"]}
    a, sr = sf.read(str(d / "backing.ogg"), always_2d=True)
    assert sr == 48000 and len(a) / sr >= info["duration_s"]
    # first bass note on the bar-1 downbeat: sound starts within a few ms of sample 0
    x = np.abs(a[:, 0])
    first = np.flatnonzero(x > 0.05 * x.max())[0] / sr
    assert first < 0.02
