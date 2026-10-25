from library import smf
from library.midi_import import split_format0
from library.tests.fixtures import DIV, pickup_song


def test_roundtrip_is_stable():
    raw = pickup_song()
    mf = smf.parse(raw)
    assert mf.format == 1 and mf.division == DIV and len(mf.tracks) == 5
    assert smf.parse(mf.to_bytes()).to_bytes() == mf.to_bytes()
    assert [t.key for t in mf.tracks] == ["Pickup test", "Drums", "Piano", "Bass", "Trumpet"]


def test_running_status_and_velocity_zero_note_off():
    # one track: note-on C4, running-status note-on vel 0 (= off), then E4 on/off
    body = bytes([0x00, 0x90, 60, 100, 0x60, 60, 0, 0x00, 64, 90, 0x60, 0x80, 64, 0, 0x00, 0xFF, 0x2F, 0x00])
    raw = b"MThd" + (6).to_bytes(4, "big") + bytes([0, 0, 0, 1, 0, 96]) + b"MTrk" + len(body).to_bytes(4, "big") + body
    notes = smf.parse(raw).tracks[0].notes()
    assert [(n.note, n.start_tick, n.end_tick) for n in notes] == [(60, 0, 96), (64, 96, 192)]


def test_tempo_map_seconds_exact():
    mf = smf.parse(pickup_song(bpm1=120, bpm2=90))
    sec = mf.tick_seconds()
    assert sec(DIV) == 0.5                      # pickup quarter at 120
    assert float(sec(DIV + 2 * 4 * DIV)) == 4.5  # tempo change at song bar 3
    assert abs(float(sec(DIV + 3 * 4 * DIV)) - (4.5 + 4 * 0.666667)) < 1e-5


def test_format0_split_by_channel():
    mf = split_format0(smf.parse(pickup_song(fmt=0)))
    names = [t.key for t in mf.tracks]
    assert names[1:] == ["piano_ch1", "bass_ch2", "brass_ch3", "Drums"]
    assert sum(len(t.notes()) for t in mf.tracks) == sum(len(t.notes()) for t in smf.parse(pickup_song()).tracks)
