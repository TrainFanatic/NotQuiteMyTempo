"""Songsterr band tracks -> an arrangement MIDI (so a Songsterr song gets a backing track too).

Chris's converter reads only the drum track (pitched tracks are on his TODO list). This module
reads the other tracks of the same song with his own timing rules (playback_order for repeats,
written-order signatures, tripletFeel swing, beat offsets) and writes one Standard MIDI File:
every pitched track on its own channel with its Songsterr instrument as the GM program
(Songsterr's instrumentId is the GM program number for pitched instruments), plus the drums on
channel 10. Pitch = tuning[string] + fret; tied notes extend the note before them.

The tempo map is the DRUM track's (the same one the sheet and drums.tempo.json use), so the
backing lines up with the sheet; MIDI tempos are whole microseconds per quarter, which costs
well under a millisecond over a whole song (checked in tests/test_songsterr_band.py).
"""

from __future__ import annotations

from fractions import Fraction

from . import catalog, smf

catalog.chris_on_path()
from songsterr import convert as conv  # noqa: E402
from songsterr import tempo_map as chris_tm  # noqa: E402

DIV = 960
WHOLE = 4 * DIV
GRACE = Fraction(1, 32)
GP_TO_GM = {91: 38, 92: 46, 93: 51, 94: 51, 95: 55, 96: 52, 97: 49, 98: 57}  # as test_file_with_midi.py


def _signatures_and_feels(measures):
    sigs, feels, sig, feel = [], [], [4, 4], None
    for m in measures:
        sig = m.get("signature", sig)
        if "tripletFeel" in m:
            feel = m["tripletFeel"]
        sigs.append(Fraction(int(sig[0]), int(sig[1])))
        feels.append(conv.SWING_UNITS.get(feel))
    return sigs, feels


def pitched_notes(track) -> list[tuple[Fraction, Fraction, int, int]]:
    """[(start, end, pitch, velocity)], positions in whole notes from bar 1, repeats expanded."""
    measures, tuning = track["measures"], track.get("tuning") or []
    sigs, feels = _signatures_and_feels(measures)
    out: list[list] = []
    open_by_pitch: dict[int, list] = {}
    pos = Fraction(0)
    for idx in conv.playback_order(measures):
        m, unit = measures[idx], feels[idx]
        for voice in m.get("voices", []):
            for off, beat in conv._beat_offsets(voice.get("beats", [])):
                if beat.get("rest"):
                    continue
                grace = beat.get("graceNote")
                dur = GRACE if grace else Fraction(*beat["duration"])
                a, b = off, off + dur
                if unit is not None and not grace:
                    a, b = conv._swing(a, unit), conv._swing(b, unit)
                for n in beat.get("notes", []):
                    if n.get("rest") or n.get("dead") or "fret" not in n:
                        continue
                    s = n.get("string", 0)
                    if not 0 <= s < len(tuning):
                        continue
                    pitch = tuning[s] + int(n["fret"])
                    if not 0 <= pitch <= 127:
                        continue
                    start, end = pos + a, pos + b
                    if n.get("tie") and pitch in open_by_pitch:
                        open_by_pitch[pitch][1] = max(open_by_pitch[pitch][1], end)
                        continue
                    vel = 70 if n.get("ghost") else 96
                    note = [start, end, pitch, vel]
                    out.append(note)
                    open_by_pitch[pitch] = note
        pos += sigs[idx]
    return sorted((Fraction(a), Fraction(b), p, v) for a, b, p, v in out)


def build_arrangement(drum_track, others: list[tuple[int, dict]]) -> tuple[smf.MidiFile, dict]:
    """(MidiFile, band_tracks) from the drum track JSON and [(track index, track JSON)]."""
    from .midi_import import section_for

    def tick(pos):
        return round(pos * WHOLE)

    conductor = [smf.track_name("Songsterr")]
    for pos, spw, _ in chris_tm.exact_tempo_changes(drum_track):
        conductor.append(smf.meta(tick(pos), 0x51, round(spw / 4 * 1_000_000).to_bytes(3, "big")))
    sigs, _ = _signatures_and_feels(drum_track["measures"])
    pos, last = Fraction(0), None
    for idx in conv.playback_order(drum_track["measures"]):
        s = sigs[idx]
        if s != last:
            num, den = _sig_of(drum_track, idx)
            conductor.append(smf.time_sig(tick(pos), int(num), int(den)))
            last = s
        pos += s
    length = pos

    tracks = [smf.Track(0, smf.sort_events(conductor))]
    drum_evs = [smf.track_name("Drums")]
    events, _ = conv.extract_events(drum_track)
    for p, midi in events:
        t = max(0, tick(p))
        drum_evs += smf.note_events(smf.DRUM_CHANNEL, GP_TO_GM.get(midi, midi), t, t + DIV // 8, 100)
    tracks.append(smf.Track(1, smf.sort_events(drum_evs)))

    channels = [c for c in range(16) if c != smf.DRUM_CHANNEL]
    by_program: dict[int, int] = {}
    band, used = {}, set()
    want_bars = len(conv.playback_order(drum_track["measures"]))
    for ti, tr in others:
        if len(conv.playback_order(tr["measures"])) != want_bars:
            continue  # a different repeat structure would not line up with the sheet
        notes = pitched_notes(tr)
        if not notes:
            continue
        prog = int(tr.get("instrumentId", 0)) & 0x7F
        if prog in by_program:
            ch = by_program[prog]
        elif channels:
            ch = by_program[prog] = channels.pop(0)
        else:
            ch = by_program[prog] = next(iter(by_program.values()))
        name = (tr.get("name") or tr.get("instrument") or f"Track {ti}").strip()
        base, k = name, 2
        while name in used or name == "Drums":
            name, k = f"{base} {k}", k + 1
        used.add(name)
        evs = [smf.track_name(name), smf.program(0, ch, prog)]
        for a, b, p, v in notes:
            if a >= length:
                continue
            ta, tb = max(0, tick(a)), tick(min(b, length))
            evs += smf.note_events(ch, p, ta, max(ta + 1, tb - 1), v)
        t = smf.Track(len(tracks), smf.sort_events(evs))
        tracks.append(t)
        sec = section_for(smf.Track(0, [smf.track_name(f"{name} {tr.get('instrument', '')}"), smf.program(0, ch, prog)]))
        if sec:
            band[name] = [sec]
    return smf.MidiFile(1, DIV, tracks), band


def _sig_of(track, idx):
    sig = [4, 4]
    for m in track["measures"][:idx + 1]:
        sig = m.get("signature", sig)
    return sig
