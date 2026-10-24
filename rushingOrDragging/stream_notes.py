"""Stream a .drums.txt file in real time: each data line is written to stdout at its timestamp.

  python stream_notes.py played.drums.txt | python live_compare.py out/<song>.drums.txt
  python stream_notes.py notes.txt --start 60          # begin 60 s into the song
  python stream_notes.py notes.txt --speed 2            # twice as fast (timestamps unchanged)

Lines are re-emitted in the canonical file_format.md form (`time_s  midi  name`), keeping the
song timestamps from the file, so a consumer can use either the timestamp or the arrival time.
Output is flushed per line, so it works through pipes, FIFOs (mkfifo) and redirects.
"""

import argparse
import os
import sys
import time

from songsterr.drum_map import drum_name

SPIN_S = 0.002  # sleep until this close to the deadline, then busy-wait for sub-ms accuracy


def read_lines(path):
    """[(time_s, midi)] in file order; comments and blank lines skipped (file_format.md §8)."""
    notes = []
    with open(path, encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            fields = line.split()
            try:
                notes.append((float(fields[0]), int(fields[1])))
            except (IndexError, ValueError):
                raise SystemExit(f"{path}:{lineno}: expected `time_s  midi  name`, got {line!r}")
    return sorted(notes)


def wait_until(deadline):
    while True:
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            return
        if remaining > SPIN_S:
            time.sleep(remaining - SPIN_S)


def stream(notes, out, start=0.0, speed=1.0, header=False):
    """Writes each note when the wall clock reaches (time_s - start) / speed after launch."""
    t0 = time.perf_counter()
    if header:
        out.write("# time_s  midi  name\n")
        out.flush()
    for t, midi in notes:
        if t < start:
            continue
        wait_until(t0 + (t - start) / speed)
        out.write(f"{t:.6f}  {midi}  {drum_name(midi)}\n")
        out.flush()


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("notes_file", help=".drums.txt file to stream")
    p.add_argument("--start", type=float, default=0.0, help="song time (s) that maps to launch time")
    p.add_argument("--speed", type=float, default=1.0, help="playback rate (default 1.0)")
    p.add_argument("--header", action="store_true", help="emit the `# time_s  midi  name` header first")
    args = p.parse_args(argv)
    if args.speed <= 0:
        p.error("--speed must be positive")

    try:
        stream(read_lines(args.notes_file), sys.stdout, args.start, args.speed, args.header)
    except BrokenPipeError:
        # the reader went away: silence the final flush at interpreter exit, then stop quietly
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
