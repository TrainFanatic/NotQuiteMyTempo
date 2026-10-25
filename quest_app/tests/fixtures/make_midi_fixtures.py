"""Write the MIDI fixtures for tests/test_midi.gd (stdlib only):  python3 make_midi_fixtures.py

type0_running_status.mid  one track; running status; note-on velocity 0 as note-off;
                          tempo 120 -> 150 BPM at beat 4; a 4-beat rest splits two phrases
type1_tracks.mid          tempo track (100 BPM) + "Trumpets" (note-offs as 0x80) +
                          "Trombones" (two phrases), sysex and a controller event to skip
bad_header.mid            not a MIDI file
Expected values are in expected.json, computed here independently of the GDScript code.
"""

import json
import struct
from pathlib import Path

HERE = Path(__file__).resolve().parent
DIV = 480


def vlq(n):
    out = [n & 0x7F]
    n >>= 7
    while n:
        out.insert(0, (n & 0x7F) | 0x80)
        n >>= 7
    return bytes(out)


def track(events):
    """events: list of (delta_ticks, raw bytes)."""
    body = b"".join(vlq(d) + e for d, e in events) + vlq(0) + b"\xff\x2f\x00"
    return b"MTrk" + struct.pack(">I", len(body)) + body


def tempo(bpm):
    us = round(60_000_000 / bpm)
    return b"\xff\x51\x03" + us.to_bytes(3, "big")


def name(text):
    return b"\xff\x03" + vlq(len(text)) + text.encode()


def smf(fmt, tracks):
    return b"MThd" + struct.pack(">IHHH", 6, fmt, len(tracks), DIV) + b"".join(tracks)


def seconds(tick, tempo_map):
    """tempo_map: [(tick, bpm)] sorted, first at 0."""
    s = 0.0
    for i, (t0, bpm) in enumerate(tempo_map):
        t1 = tempo_map[i + 1][0] if i + 1 < len(tempo_map) else tick
        if tick <= t0:
            break
        s += (min(tick, t1) - t0) * (60.0 / bpm) / DIV
    return s


def main():
    # --- type 0: running status + velocity-0 note-offs + tempo change -------------
    ev = [(0, tempo(120)),
          (0, b"\x90\x3c\x64"),          # C4 on
          (480, b"\x3c\x00"),            # running status: C4 velocity 0 = off at beat 1
          (0, b"\x3e\x64"),              # D4 on
          (480, b"\x3e\x00"),            # off at beat 2
          (0, b"\x40\x64"),              # E4 on
          (960, b"\x40\x00"),            # off at beat 4
          (0, tempo(150)),               # tempo change at beat 4 (tick 1920)
          (1920, b"\x90\x43\x50"),       # G4 on at tick 3840 after a 4-beat rest
          (960, b"\x43\x00")]            # off at tick 4800
    (HERE / "type0_running_status.mid").write_bytes(smf(0, [track(ev)]))
    tm0 = [(0, 120.0), (1920, 150.0)]
    notes0 = [(0, 480), (480, 960), (960, 1920), (3840, 4800)]

    # --- type 1: tempo track + two named parts -------------------------------------
    t0 = track([(0, name("Conductor")), (0, tempo(100))])
    t1 = track([(0, name("Trumpets")), (0, b"\xf0\x03\x7e\x7f\xf7"), (0, b"\xb0\x07\x64"),
                (0, b"\x91\x48\x70"), (960, b"\x81\x48\x40"),     # note-offs as 0x8n
                (0, b"\x91\x4a\x70"), (960, b"\x81\x4a\x40")])
    t2 = track([(0, name("Trombones")),
                (480, b"\x92\x30\x60"), (480, b"\x82\x30\x00"),   # beat 1 to 2
                (2880, b"\x92\x32\x60"), (960, b"\x82\x32\x00")]) # beat 8 to 10
    (HERE / "type1_tracks.mid").write_bytes(smf(1, [t0, t1, t2]))
    tm1 = [(0, 100.0)]

    (HERE / "bad_header.mid").write_bytes(b"RIFF\x00\x00\x00\x00not midi")

    rest_beats, lead, delay = 2.0, 0.6, 0.4

    def phrases(notes, tm):
        spans = []
        for s, e in notes:
            if spans and s - spans[-1][1] < rest_beats * DIV:
                spans[-1][1] = max(spans[-1][1], e)
            else:
                spans.append([s, e])
        out = []
        for s, e in spans:
            st, en = seconds(s, tm), seconds(e, tm)
            ph = dict(start=st, end=en, raise_at=max(0.0, st - lead), lower_at=en + delay)
            if out and ph["raise_at"] <= out[-1]["lower_at"]:
                out[-1].update(end=ph["end"], lower_at=ph["lower_at"])
            else:
                out.append(ph)
        return out

    expected = {
        "type0": {"format": 0, "tracks": 1, "notes": len(notes0),
                  "note_times": [[seconds(s, tm0), seconds(e, tm0)] for s, e in notes0],
                  "tempo_map": [[0, 500000], [1920, 400000]],
                  "phrases": phrases(notes0, tm0)},
        "type1": {"format": 1, "tracks": 3, "names": ["Conductor", "Trumpets", "Trombones"],
                  "notes": {"Trumpets": 2, "Trombones": 2},
                  "phrases": {"Trumpets": phrases([(0, 960), (960, 1920)], tm1),
                              "Trombones": phrases([(480, 960), (3840, 4800)], tm1)},
                  "bpm": 100.0},
        "params": {"rest_beats": rest_beats, "raise_lead": lead, "lower_delay": delay},
    }
    (HERE / "expected.json").write_text(json.dumps(expected, indent=2))
    print("wrote fixtures:", sorted(p.name for p in HERE.glob("*.mid")))


if __name__ == "__main__":
    main()
