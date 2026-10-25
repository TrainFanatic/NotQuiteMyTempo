import json

import pytest

import compare_performance as cp
from songsterr import tempo_map as tm

from library import catalog
from library.midi_import import SongImportError, import_inbox, import_midi
from library.tests.fixtures import expected_bar_start, pickup_song


@pytest.fixture
def song(tmp_path):
    mid = tmp_path / "Pickup Song.mid"
    mid.write_bytes(pickup_song())
    info = import_midi(mid, songs_dir=tmp_path / "songs", backing=False, log=lambda *_: None)
    return info, tmp_path / "songs" / info["id"]


def test_song_json(song):
    info, d = song
    assert info["id"] == "pickup_song" and catalog.valid_id(info["id"])
    assert json.loads((d / "song.json").read_text()) == info
    assert info["source"] == "midi" and info["arrangement"] and info["drum_midi_track"] == "Drums"
    assert info["bpm"] == 120 and info["beats_per_bar"] == 4 and info["bars"] == 6
    assert info["pickup_s"] == 0.5 and info["midi_t0_s"] == 0.5
    assert info["duration_s"] == pytest.approx(expected_bar_start(7), abs=1e-3)
    for k in ("id", "title", "artist", "bpm", "beats_per_bar", "bars", "duration_s", "source",
              "drum_midi_track", "backing", "arrangement", "imported_at"):
        assert k in info


def test_sheet_parses_with_chris_reader_and_is_canonical(song):
    _, d = song
    text = (d / "drums.drums.txt").read_text()
    lines = text.splitlines()
    assert lines[1] == "# time_s  midi  name" and text.endswith("\n") and not text.endswith("\n\n")
    data = [l for l in lines if not l.startswith("#")]
    assert all(cp_line(l) for l in data)
    hits = cp.read_notes(d / "drums.drums.txt")
    assert [(h.time, h.midi) for h in hits] == sorted((h.time, h.midi) for h in hits)
    assert min(h.time for h in hits) == 0.0              # pickup hi-hat is before 0: dropped
    assert {h.midi for h in hits} == {36, 38, 49}        # tambourine (54) is band percussion
    kicks = [h.time for h in hits if h.midi == 36]
    assert len(kicks) == 24
    for b in range(1, 7):                                # a kick on every downbeat, tempo change included
        assert any(abs(k - expected_bar_start(b)) < 2e-6 for k in kicks)


def cp_line(line):
    import re
    return re.match(r"^\d+\.\d{6}  \d+  [a-z0-9_]+$", line)


def test_tempo_map_loads_with_chris_loader(song):
    _, d = song
    m = tm.load_tempo_map(d / "drums.tempo.json")
    assert m["bars"][0]["start_s"] == 0.0 and len(m["bars"]) == 6
    for b in range(1, 8):
        assert tm.bar_start(m, b) == pytest.approx(expected_bar_start(b), abs=2e-6)
    assert m["bars"][2]["bpm"] == pytest.approx(90, abs=1e-3) and m["bars"][1]["bpm"] == 120
    assert [c["time_s"] for c in m["tempo_changes"]] == [0.0, 4.0]
    for h in cp.read_notes(d / "drums.drums.txt"):       # every note lands on a whole/half beat
        bar, beat = tm.bar_at(m, h.time)
        assert beat == pytest.approx(round(beat), abs=1e-4)
    assert tm.sidecar_path(d / "drums.drums.txt") == d / "drums.tempo.json"


def test_bridge_barclock_reads_it(song):
    _, d = song
    from hub.bridge import BarClock
    clock = BarClock(0, tempo_map=tm.load_tempo_map(d / "drums.tempo.json"))
    assert clock.bar_at(expected_bar_start(4) + 0.001)[0] == 4
    assert clock.bar_start(3) == pytest.approx(4.0)


def test_arrangement_and_band_tracks(song, tmp_path):
    _, d = song
    assert (d / "arrangement.mid").read_bytes() == (tmp_path / "Pickup Song.mid").read_bytes()
    assert json.loads((d / "band_tracks.json").read_text()) == {
        "Piano": ["piano"], "Bass": ["bass"], "Trumpet": ["trumpet"]}


def test_no_pickup_file(tmp_path):
    (tmp_path / "plain.mid").write_bytes(pickup_song(pickup_beats=0))
    info = import_midi(tmp_path / "plain.mid", songs_dir=tmp_path, backing=False, log=lambda *_: None)
    assert info["pickup_s"] == 0.0 and info["bars"] == 6
    hits = cp.read_notes(tmp_path / "plain" / "drums.drums.txt")
    assert {h.midi for h in hits} == {36, 38, 49}


def test_pickup_can_be_forced_off(tmp_path):
    (tmp_path / "p.mid").write_bytes(pickup_song())
    info = import_midi(tmp_path / "p.mid", songs_dir=tmp_path, backing=False, pickup=False, log=lambda *_: None)
    m = tm.load_tempo_map(tmp_path / "p" / "drums.tempo.json")
    assert info["pickup_s"] == 0.0 and m["bars"][0]["numerator"] == 1 and info["bars"] == 7
    assert 42 in {h.midi for h in cp.read_notes(tmp_path / "p" / "drums.drums.txt")}


def test_format0_file(tmp_path):
    (tmp_path / "zero.mid").write_bytes(pickup_song(fmt=0))
    info = import_midi(tmp_path / "zero.mid", songs_dir=tmp_path, backing=False, log=lambda *_: None)
    assert info["format0_split"] and info["drum_midi_track"] == "Drums"
    band = json.loads((tmp_path / "zero" / "band_tracks.json").read_text())
    assert band == {"piano_ch1": ["piano"], "bass_ch2": ["bass"], "brass_ch3": ["trumpet"]}
    a = cp.read_notes(tmp_path / "zero" / "drums.drums.txt")
    (tmp_path / "one.mid").write_bytes(pickup_song())
    import_midi(tmp_path / "one.mid", songs_dir=tmp_path, backing=False, log=lambda *_: None)
    assert a == cp.read_notes(tmp_path / "one" / "drums.drums.txt")


def test_no_drums_is_an_error(tmp_path):
    from library import smf
    mf = smf.parse(pickup_song())
    mf.tracks = [t for t in mf.tracks if t.key != "Drums"]
    (tmp_path / "nodrums.mid").write_bytes(mf.to_bytes())
    with pytest.raises(SongImportError, match="no drum-kit notes"):
        import_midi(tmp_path / "nodrums.mid", songs_dir=tmp_path, backing=False, log=lambda *_: None)
    assert not (tmp_path / "nodrums").exists()
    assert not [p for p in tmp_path.iterdir() if p.name.startswith(".tmp-")]


def test_drum_track_override(tmp_path):
    from library import smf
    mf = smf.parse(pickup_song())
    mf.tracks = [t for t in mf.tracks if t.key != "Drums"]
    (tmp_path / "x.mid").write_bytes(mf.to_bytes())
    info = import_midi(tmp_path / "x.mid", songs_dir=tmp_path, backing=False, drum_tracks=("Bass",), log=lambda *_: None)
    assert info["drum_midi_track"] == "Bass" and info["notes"] == 6


def test_reimport_replaces(tmp_path, song):
    info, d = song
    (tmp_path / "Pickup Song.mid").write_bytes(pickup_song(bars=8))
    info2 = import_midi(tmp_path / "Pickup Song.mid", songs_dir=tmp_path / "songs", backing=False, log=lambda *_: None)
    assert info2["id"] == info["id"] and info2["bars"] == 8
    assert [s["bars"] for s in catalog.list_songs(tmp_path / "songs")] == [8]


def test_inbox(tmp_path):
    box = catalog.inbox(tmp_path)
    box.mkdir(parents=True)
    (box / "good.mid").write_bytes(pickup_song())
    (box / "bad.mid").write_bytes(b"not midi")
    (box / "notes.txt").write_text("ignored")
    got = import_inbox(tmp_path, log=lambda *_: None)
    assert [s["id"] for s in got] == ["good"]
    assert (box / "imported" / "good.mid").exists() and (box / "failed" / "bad.mid").exists()
    assert "MThd" in (box / "failed" / "bad.mid.error.txt").read_text()
    assert (box / "notes.txt").exists()
    assert [s["id"] for s in catalog.list_songs(tmp_path)] == ["good"]
