"""Live timing coach: reads a stream of `time_s  midi  name` lines (stdin by default), compares
each hit with the sheet music as it arrives, and shows RUSHING! / DRAGGING! / PERFECT!.

  python stream_notes.py played.drums.txt | python live_compare.py out/<song>.drums.txt
  python live_compare.py sheet.drums.txt --input /tmp/drums.fifo        # any FIFO or file
  python live_compare.py sheet.drums.txt --plain                        # one line per change

How a hit is judged (online, using only hits that have already arrived):
  - It's paired with the nearest unmatched sheet note on the same drum, after the last note
    already matched on that drum (players play one drum's notes in order), around the sheet time
    shifted by the player's recent drift (median error over the last --drift-window s). Next it
    tries the same kind of drum (any crash, any tom, ...). If neither is within --window it's
    an extra hit.
  - Its error = hit time - (sheet time + --offset). The screen shows the verdict for the median
    error of the hits in the last --smooth seconds (newer hits weigh more): within ±--tolerance is PERFECT!, earlier is
    RUSHING!, later is DRAGGING!, with --hysteresis so it doesn't flicker at the edge. (A single
    hit is a 16th note's worth of evidence at 212 bpm, which would flicker unreadably, so the
    state is smoothed; --smooth 0 judges every hit on its own.)

Clock: by default a hit's time is the timestamp in its line (`--clock line`), so delays in the
pipe don't count against the player. `--clock arrival` instead times hits by when they arrive
(seconds since this program started, plus --start), for sources whose timestamps can't be trusted.
Start both ends of the pipe together.

On end of input (or Ctrl-C) the full compare_performance summary is printed.
"""

import argparse
import os
import selectors
import shutil
import stat
import statistics
import sys
import time
from collections import deque

import numpy as np

import compare_performance as cp

IDLE_S = 2.0          # no input for this long (wall clock) -> show "waiting"
MAX_FPS = 60

STATES = {  # state -> (big text, ANSI colour)
    "perfect": ("PERFECT!", "\x1b[1;32m"),
    "rushing": ("RUSHING!", "\x1b[1;33m"),
    "dragging": ("DRAGGING!", "\x1b[1;31m"),
}


# ---- online matching -----------------------------------------------------------------------

class LiveMatcher:
    def __init__(self, sheet, offset=0.0, window=0.1, drift_window=2.0, track_drift=True):
        self.sheet, self.offset, self.window = sheet, offset, window
        self.drift_window, self.track_drift = drift_window, track_drift
        for h in sheet:
            h.match = None
        self.by_midi, self.by_family = {}, {}
        for h in sheet:
            self.by_midi.setdefault(h.midi, []).append(h)
            self.by_family.setdefault(cp.family(h.midi), []).append(h)
        self.times = {("m", m): np.array([n.time for n in v]) for m, v in self.by_midi.items()}
        self.times.update({("f", f): np.array([n.time for n in v]) for f, v in self.by_family.items()})
        self.next_idx = {}          # midi -> index after the last matched note on that drum
        self.recent = deque()       # (hit_time, error) for the drift estimate
        self.played = []
        self.extra = 0
        self._missed_ptr = 0
        self.missed = 0

    def drift(self, now):
        while self.recent and self.recent[0][0] < now - self.drift_window:
            self.recent.popleft()
        if not self.track_drift or not self.recent:
            return 0.0
        return statistics.median(e for _, e in self.recent)

    def _nearest(self, key, notes, t, expected_offset, start=0):
        """Index of the closest unmatched note (index >= start) within the window, or None."""
        times = self.times[key]
        target = t - expected_offset
        lo = max(start, int(np.searchsorted(times, target - self.window, "left")))
        hi = int(np.searchsorted(times, target + self.window, "right"))
        free = [j for j in range(lo, hi) if notes[j].match is None]
        return min(free, key=lambda j: abs(times[j] - target)) if free else None

    def feed(self, t, midi):
        """Pairs one incoming hit; returns (hit, error_s or None)."""
        hit = cp.Hit(t, midi)
        self.played.append(hit)
        expected = self.offset + self.drift(t)
        note = None
        notes = self.by_midi.get(midi)
        if notes:
            j = self._nearest(("m", midi), notes, t, expected, self.next_idx.get(midi, 0))
            if j is not None:
                note, self.next_idx[midi] = notes[j], j + 1
        if note is None and cp.family(midi) in self.by_family:
            fam = self.by_family[cp.family(midi)]
            j = self._nearest(("f", cp.family(midi)), fam, t, expected)
            if j is not None:
                note = fam[j]
        self._advance_missed(t, expected)
        if note is None:
            self.extra += 1
            return hit, None
        hit.match, note.match = note, hit
        err = t - (note.time + self.offset)
        self.recent.append((t, err))
        return hit, err

    def _advance_missed(self, now, expected):
        while (self._missed_ptr < len(self.sheet)
               and self.sheet[self._missed_ptr].time + expected < now - self.window):
            if self.sheet[self._missed_ptr].match is None:
                self.missed += 1
            self._missed_ptr += 1

    def span_sheet(self):
        """Sheet notes inside the played time span: what 'missed' should be counted against."""
        if not self.played:
            return []
        lo = self.played[0].time - self.offset - self.window
        hi = self.played[-1].time - self.offset + self.window
        return [s for s in self.sheet if lo <= s.time <= hi or s.match is not None]


def weighted_median(values, weights):
    order = np.argsort(values)
    v, w = np.asarray(values)[order], np.asarray(weights)[order]
    cum = np.cumsum(w)
    return float(v[np.searchsorted(cum, cum[-1] / 2)])


def recent_median(recent, now, smooth):
    """Median of recent errors, weighting newer hits more (half-life smooth / 2), so an even
    split between old and new evidence follows the new rather than averaging to 'perfect'."""
    errs = [e for _, e in recent]
    if smooth <= 0 or len(errs) == 1:
        return errs[-1]
    ages = np.array([now - t for t, _ in recent])
    return weighted_median(errs, 0.5 ** (ages / (smooth / 2)))


def verdict(err, tolerance):
    if abs(err) <= tolerance:
        return "perfect"
    return "dragging" if err > 0 else "rushing"


def smoothed_state(previous, median, tolerance, hysteresis):
    """Verdict for the on-screen state, with hysteresis so a median hovering at the threshold
    doesn't flicker: leaving PERFECT needs |median| > tolerance + hysteresis, and returning to it
    needs |median| < tolerance - hysteresis. Rushing <-> dragging switches immediately."""
    new = verdict(median, tolerance)
    if previous == "perfect" and abs(median) <= tolerance + hysteresis:
        return "perfect"
    still_same_side = (median > 0) == (previous == "dragging")
    if (previous in ("rushing", "dragging") and new == "perfect" and still_same_side
            and abs(median) >= tolerance - hysteresis):
        return previous
    return new


# ---- displays ------------------------------------------------------------------------------

FONT = {
    "R": ["████ ", "█   █", "████ ", "█  █ ", "█   █"],
    "U": ["█   █", "█   █", "█   █", "█   █", " ███ "],
    "S": [" ████", "█    ", " ███ ", "    █", "████ "],
    "H": ["█   █", "█   █", "█████", "█   █", "█   █"],
    "I": ["███", " █ ", " █ ", " █ ", "███"],
    "N": ["█   █", "██  █", "█ █ █", "█  ██", "█   █"],
    "G": [" ████", "█    ", "█  ██", "█   █", " ████"],
    "D": ["████ ", "█   █", "█   █", "█   █", "████ "],
    "A": [" ███ ", "█   █", "█████", "█   █", "█   █"],
    "P": ["████ ", "█   █", "████ ", "█    ", "█    "],
    "E": ["█████", "█    ", "████ ", "█    ", "█████"],
    "F": ["█████", "█    ", "████ ", "█    ", "█    "],
    "C": [" ████", "█    ", "█    ", "█    ", " ████"],
    "T": ["█████", "  █  ", "  █  ", "  █  ", "  █  "],
    "!": ["█", "█", "█", " ", "█"],
}


def big_text(word):
    return [" ".join(FONT[c][row] for c in word) for row in range(5)]


class PlainDisplay:
    """One line per state change; used when stdout isn't a terminal."""

    def __init__(self, out=sys.stdout):
        self.out, self.state = out, None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def update(self, status):
        if status["state"] != self.state and status["state"] in STATES:
            self.state = status["state"]
            print(f"{status['song_time']:10.3f}s  {STATES[self.state][0]:<10} "
                  f"(median {cp.fmt_ms(status['median'])} over {status['n_recent']} hit(s))",
                  file=self.out, flush=True)

    def idle(self, status):
        pass


class TerminalDisplay:
    """Full-screen view on the alternate screen; restored on exit."""

    def __init__(self, title, song_length, out=sys.stdout):
        self.title, self.song_length, self.out = title, song_length, out
        self.last_draw, self.last_state = 0.0, None

    def __enter__(self):
        self.out.write("\x1b[?1049h\x1b[?25l")  # alternate screen, hide cursor
        self.draw({"state": "waiting", "song_time": 0.0})
        return self

    def __exit__(self, *exc):
        self.out.write("\x1b[?25h\x1b[?1049l")
        self.out.flush()
        return False

    def update(self, status):
        now = time.perf_counter()
        if status["state"] != self.last_state or now - self.last_draw >= 1 / MAX_FPS:
            self.draw(status)

    def idle(self, status):
        self.draw({**status, "state": "waiting"})

    def draw(self, s):
        self.last_draw, self.last_state = time.perf_counter(), s["state"]
        cols = shutil.get_terminal_size((80, 24)).columns
        reset, dim = "\x1b[0m", "\x1b[2m"

        def fmt_t(x):
            return f"{int(x // 60)}:{x % 60:04.1f}"

        lines = [f"{self.title}   {fmt_t(s['song_time'])} / {fmt_t(self.song_length)}", ""]
        if s["state"] in STATES:
            word, colour = STATES[s["state"]]
            art = big_text(word)
            if len(art[0]) > cols:
                art = [word]
            lines += [colour + row.center(cols).rstrip() + reset for row in art]
        else:
            lines += ["", "", (dim + "waiting for notes…".center(cols).rstrip() + reset), "", ""]
        lines.append("")
        if "median" in s:
            lines.append(f"  recent median {cp.fmt_ms(s['median']):>10}  over {s['n_recent']} hit(s)   "
                         f"drift {cp.fmt_ms(s['drift']):>10}")
            lines.append("  " + cp._bar(s["median"]) + "   < rushing | dragging >")
            if s.get("last"):
                lines.append(f"  last hit: {s['last']}")
            c = s["counts"]
            lines.append(f"  hits: perfect {c['perfect']}  rushing {c['rushing']}  dragging {c['dragging']}"
                         f"   extra {s['extra']}  missed {s['missed']}")
        lines += ["", dim + "  Ctrl-C to stop" + reset]
        self.out.write("\x1b[H" + "\n".join(line + "\x1b[K" for line in lines) + "\x1b[J")
        self.out.flush()


# ---- input loop ----------------------------------------------------------------------------

def read_stream(fd, on_line, on_idle, idle_s=IDLE_S):
    """Calls on_line(text) per complete line; on_idle() after idle_s without input. Uses raw
    reads + select so the screen can update while waiting (buffered readline would block)."""
    # A regular file is always readable and kqueue (macOS's default selector) never reports its
    # EOF, so only wait on pipes/FIFOs/sockets; read regular files straight through.
    sel = None
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        sel = selectors.DefaultSelector()
        sel.register(fd, selectors.EVENT_READ)
    buf = b""
    last_input = time.perf_counter()
    idle_shown = False
    while True:
        if sel is None or sel.select(timeout=0.1):
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            buf += chunk
            *lines, buf = buf.split(b"\n")
            for line in lines:
                on_line(line.decode("utf-8", "replace"))
            last_input, idle_shown = time.perf_counter(), False
        elif not idle_shown and time.perf_counter() - last_input > idle_s:
            on_idle()
            idle_shown = True
    if buf.strip():
        on_line(buf.decode("utf-8", "replace"))


def run(sheet, fd, display, matcher, tolerance, smooth, clock="line", start=0.0, hysteresis=0.002):
    recent = deque()  # (song_time, error) within the smoothing window
    counts = {"perfect": 0, "rushing": 0, "dragging": 0}
    t_launch = time.perf_counter()
    status = {"state": "waiting", "song_time": 0.0}

    def on_line(text):
        text = text.strip()
        if not text or text.startswith("#"):
            return
        fields = text.split()
        try:
            t, midi = float(fields[0]), int(fields[1])
        except (IndexError, ValueError):
            print(f"ignoring malformed line: {text!r}", file=sys.stderr)
            return
        if clock == "arrival":
            t = time.perf_counter() - t_launch + start
        hit, err = matcher.feed(t, midi)
        if err is not None:
            counts[verdict(err, tolerance)] += 1
            recent.append((t, err))
        while recent and recent[0][0] < t - smooth:
            recent.popleft()
        if smooth <= 0 and err is not None:
            recent.clear()
            recent.append((t, err))
        status.update(song_time=t, extra=matcher.extra, missed=matcher.missed, counts=counts,
                      drift=matcher.drift(t))
        if recent:
            med = recent_median(recent, t, smooth)
            state = smoothed_state(status["state"], med, tolerance, hysteresis)
            status.update(state=state, median=med, n_recent=len(recent))
        if err is None:
            status["last"] = f"{hit.name:<14} EXTRA (no candidate in sheet)"
        else:
            other = f"  (expected {hit.match.name})" if hit.match.midi != midi else ""
            status["last"] = f"{hit.name:<14} {cp.fmt_ms(err):>10}  {verdict(err, tolerance)}{other}"
        display.update(status)

    try:
        read_stream(fd, on_line, lambda: display.idle(status))
    except KeyboardInterrupt:
        pass
    return counts


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("sheet", help="reference .drums.txt generated from the sheet music")
    p.add_argument("--input", help="read the stream from this file/FIFO instead of stdin")
    p.add_argument("--tolerance", type=float, default=0.010, help="± seconds counted as perfect (default 0.010)")
    p.add_argument("--smooth", type=float, default=0.5,
                   help="judge the median of hits in the last SMOOTH seconds (default 0.5; 0 = per hit)")
    p.add_argument("--hysteresis", type=float, default=0.002,
                   help="seconds of margin before the on-screen state changes (default 0.002)")
    p.add_argument("--window", type=float, default=0.1, help="max distance for a match, seconds (default 0.1)")
    p.add_argument("--offset", type=float, default=0.0, help="stream time = sheet time + OFFSET (default 0)")
    p.add_argument("--drift-window", type=float, default=2.0,
                   help="seconds of recent hits used to follow rushing/dragging when pairing (default 2)")
    p.add_argument("--no-drift-tracking", action="store_true", help="pair against the sheet as-is")
    p.add_argument("--clock", choices=("line", "arrival"), default="line",
                   help="time hits by their line timestamp (default) or by arrival time")
    p.add_argument("--start", type=float, default=0.0, help="with --clock arrival: song time at launch")
    p.add_argument("--plain", action="store_true", help="print state changes instead of the full-screen view")
    p.add_argument("--no-summary", action="store_true", help="don't print the summary at the end")
    args = p.parse_args(argv)

    sheet = cp.read_notes(args.sheet)
    if not sheet:
        raise SystemExit(f"No notes in {args.sheet}")
    matcher = LiveMatcher(sheet, args.offset, args.window, args.drift_window, not args.no_drift_tracking)
    if args.plain or not sys.stdout.isatty():
        display = PlainDisplay()
    else:
        display = TerminalDisplay(os.path.basename(args.sheet), sheet[-1].time)

    fd = os.open(args.input, os.O_RDONLY) if args.input else sys.stdin.fileno()
    with display:
        counts = run(sheet, fd, display, matcher, args.tolerance, args.smooth, args.clock, args.start,
                     args.hysteresis)

    if not args.no_summary and matcher.played:
        print()
        print(f"Live verdicts per hit: perfect {counts['perfect']}  rushing {counts['rushing']}  "
              f"dragging {counts['dragging']}")
        cp.report(matcher.played, matcher.span_sheet(), args.offset, args.tolerance, summary_only=True)


if __name__ == "__main__":
    main()
