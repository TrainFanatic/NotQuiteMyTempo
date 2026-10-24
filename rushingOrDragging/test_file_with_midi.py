"""Turn a timestamped drum note file (`time_s  midi  name` per line) into a MIDI file to listen to.

  python test_file_with_midi.py out/metallica-master_of_puppets.drums.txt
  python test_file_with_midi.py notes.txt -o check.mid --velocity 110

Open the .mid in GarageBand, MuseScore, or any DAW and play it alongside the recording.
Notes go on the GM drum channel (10). Guitar Pro's extended numbers (91-98) aren't part of
General MIDI, so they're remapped to their closest GM sound unless --no-remap is given.
No dependencies: the Standard MIDI File is written by hand.
"""

import argparse
import struct
import sys
from pathlib import Path

PPQ = 960                    # ticks per quarter note
TEMPO_US = 500_000           # 120 bpm -> 1 tick = 1/1920 s (~0.5 ms resolution)
TICKS_PER_SECOND = PPQ * 1_000_000 // TEMPO_US
DRUM_CHANNEL = 9             # MIDI channel 10, zero-based
NOTE_LENGTH_S = 0.1          # drums ignore note length; this just keeps note-offs tidy

GP_TO_GM = {
    91: 38,  # snare rim shot -> snare
    92: 46,  # half-open hi-hat -> open hi-hat
    93: 51,  # ride edge -> ride
    94: 51,  # ride choke -> ride
    95: 55,  # splash choke -> splash
    96: 52,  # china choke -> china
    97: 49,  # crash choke -> crash
    98: 57,  # crash 2 choke -> crash 2
}


def read_notes(path):
    """Returns [(time_s, midi)], skipping `#` comments and blank lines."""
    notes = []
    for lineno, line in enumerate(Path(path).read_text().splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split()
        try:
            notes.append((float(fields[0]), int(fields[1])))
        except (IndexError, ValueError):
            raise SystemExit(f"{path}:{lineno}: expected `time_s midi [name]`, got {line!r}")
    return notes


def varlen(n):
    """MIDI variable-length quantity."""
    out = [n & 0x7F]
    n >>= 7
    while n:
        out.append((n & 0x7F) | 0x80)
        n >>= 7
    return bytes(reversed(out))


def build_midi(notes, velocity=100, remap=True):
    """Returns (midi_bytes, shift_s). Negative times (e.g. a leading grace note) shift the song later."""
    shift = max(0.0, -min((t for t, _ in notes), default=0.0))
    events = []  # (tick, order, status, data1, data2); note-offs sort before note-ons on the same tick
    for t, midi in notes:
        midi = GP_TO_GM.get(midi, midi) if remap else midi
        if not 0 <= midi <= 127:
            print(f"warning: skipping out-of-range note {midi} at {t:.3f}s", file=sys.stderr)
            continue
        on = round((t + shift) * TICKS_PER_SECOND)
        off = on + round(NOTE_LENGTH_S * TICKS_PER_SECOND)
        events.append((on, 1, 0x90 | DRUM_CHANNEL, midi, velocity))
        events.append((off, 0, 0x80 | DRUM_CHANNEL, midi, 0))
    events.sort()

    track = bytearray()
    track += b"\x00\xff\x51\x03" + TEMPO_US.to_bytes(3, "big")   # set tempo
    track += b"\x00\xff\x58\x04\x04\x02\x18\x08"                  # 4/4 (cosmetic)
    track += b"\x00\xff\x03" + varlen(5) + b"Drums"               # track name
    last = 0
    for tick, _, status, d1, d2 in events:
        track += varlen(tick - last) + bytes([status, d1, d2])
        last = tick
    track += b"\x00\xff\x2f\x00"                                   # end of track

    header = b"MThd" + struct.pack(">IHHH", 6, 0, 1, PPQ)
    return header + b"MTrk" + struct.pack(">I", len(track)) + bytes(track), shift


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("notes_file", help="file with `time_s midi name` lines")
    p.add_argument("-o", "--output", help="output .mid (default: next to the input)")
    p.add_argument("--velocity", type=int, default=100, choices=range(1, 128), metavar="1-127")
    p.add_argument("--no-remap", action="store_true", help="keep Guitar Pro numbers 91-98 as-is")
    args = p.parse_args(argv)

    notes = read_notes(args.notes_file)
    if not notes:
        raise SystemExit(f"No notes found in {args.notes_file}")
    data, shift = build_midi(notes, args.velocity, remap=not args.no_remap)
    out = Path(args.output) if args.output else Path(args.notes_file).with_suffix(".mid")
    out.write_bytes(data)
    end = max(t for t, _ in notes) + shift
    msg = f"Wrote {len(notes)} notes ({end:.1f}s) to {out}"
    if shift:
        msg += f"  (shifted {shift * 1000:.1f} ms later so the first note isn't before 0s)"
    print(msg)


if __name__ == "__main__":
    main()
