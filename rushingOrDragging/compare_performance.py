"""Compare a played drum performance with the sheet-music reference and report timing.

  python compare_performance.py played.drums.txt out/metallica-master_of_puppets.drums.txt
  python compare_performance.py played.txt sheet.txt --offset 2.5 --tolerance 0.015
  python compare_performance.py played.txt sheet.txt --summary-only

Both files use the `.drums.txt` format (see file_format.md). Each played hit is paired with a
candidate sheet note and reported as early/late by some number of milliseconds:

  1. Same drum first (same MIDI number), order-preserving: a drummer plays one drum's notes in
     sequence, so pairings never cross. Among those, the one with the least total error wins
     (leaving a hit or note unpaired costs half of --window). A hit pushed closer to the next
     note than to its own therefore still lands on its own note when the next note has its
     own hit.
  2. Leftovers then go to the same kind of drum (kick, snare, hi-hat, crash-like cymbals, ride,
     toms), so a transcription that mixes up crash/crash_2 or two toms still lines up. Such
     pairs are flagged "expected <name>". Here a sheet note only accepts hits within half the
     gap to its neighbouring notes of that kind (capped at --window).

Rushing and dragging: real players drift ahead of and behind the beat. After a first pass, the
local timing (rolling median of errors over ±2 s) is used as the expected position of each sheet
note and matching is redone, so a player who has dragged 60 ms behind is still paired with the
right notes. --window applies around that local timing. Reported errors are always measured
against the sheet, and the summary's "timing over time" section shows the drift.
--no-drift-tracking turns this off.

Hits with no candidate are "extra"; sheet notes nobody played are "missed".

Time origin: a recording rarely starts exactly on the song's downbeat, so a constant offset
(performance time = sheet time + offset) is detected automatically. 0 is kept unless another offset
is clearly better, so a player who is consistently late isn't "corrected" away. Use --offset to
set it explicitly.
"""

import argparse
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from songsterr.drum_map import drum_name

# Drums a transcription could plausibly confuse with each other (second matching pass).
FAMILIES = {
    "kick": (35, 36),
    "snare": (37, 38, 40, 91),
    "hihat": (42, 44, 46, 92),
    "crash": (49, 52, 55, 57, 95, 96, 97, 98),
    "ride": (51, 53, 59, 93, 94),
    "tom": (41, 43, 45, 47, 48, 50),
}
FAMILY_OF = {m: fam for fam, ms in FAMILIES.items() for m in ms}

OFFSET_SEARCH_S = 30.0   # largest offset the auto-alignment considers
OFFSET_BIN_S = 0.005
OFFSET_MIN_GAIN = 1.05   # a non-zero offset must match this many times more hits than offset 0
DRIFT_HALF_WIDTH_S = 2.0  # rolling-median half width used to follow rushing/dragging
DRIFT_ITERATIONS = 4


@dataclass
class Hit:
    time: float
    midi: int
    match: "Hit" = None

    @property
    def name(self):
        return drum_name(self.midi)


def family(midi):
    return FAMILY_OF.get(midi, f"midi{midi}")


def read_notes(path):
    """Tolerant reader (file_format.md §8); exact duplicate (time, midi) lines are dropped."""
    seen, hits = set(), []
    for lineno, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split()
        try:
            key = (float(fields[0]), int(fields[1]))
        except (IndexError, ValueError):
            raise SystemExit(f"{path}:{lineno}: expected `time_s  midi  name`, got {line!r}")
        if key not in seen:
            seen.add(key)
            hits.append(Hit(*key))
    hits.sort(key=lambda h: (h.time, h.midi))
    return hits


# ---- matching ------------------------------------------------------------------------------

def _windows(times, max_window):
    """Per-note acceptance window: half the gap to the nearest *distinct-time* neighbour."""
    t = np.asarray(times)
    uniq = np.unique(t)
    gap = np.full(len(uniq), np.inf)
    if len(uniq) > 1:
        d = np.diff(uniq)
        gap[:-1] = d
        gap[1:] = np.minimum(gap[1:], d)
    win = np.minimum(gap / 2, max_window)
    return win[np.searchsorted(uniq, t)]


def _expected(sheet, offset):
    """Expected performance time of each sheet note. `offset` is a constant or {id(note): offset}
    (the tracked drift). Kept non-decreasing so the sorted-search logic stays valid."""
    if isinstance(offset, dict):
        st = np.array([s.time + offset[id(s)] for s in sheet])
        return np.maximum.accumulate(st) if len(st) else st
    return np.array([s.time + offset for s in sheet])


def _match_group(played, sheet, win, offset):
    """One-to-one matching of played hits to sheet notes (with per-note windows); closest pairs win."""
    if not played or not sheet:
        return
    st = _expected(sheet, offset)
    candidates = []
    for p in played:
        i = int(np.searchsorted(st, p.time))
        for j in (i - 1, i):  # nearest note on either side; ties at equal time expand below
            if 0 <= j < len(st):
                # include every sheet note sharing that timestamp (e.g. crash + crash_2 together)
                lo = int(np.searchsorted(st, st[j], "left"))
                hi = int(np.searchsorted(st, st[j], "right"))
                for k in range(lo, hi):
                    d = p.time - st[k]
                    if abs(d) <= win[k]:
                        candidates.append((abs(d), p.midi != sheet[k].midi, id(p), k, p))
    taken = set()
    for _, _, _, k, p in sorted(candidates, key=lambda c: c[:2]):
        if p.match is None and k not in taken:
            taken.add(k)
            p.match, sheet[k].match = sheet[k], p


def _match_in_order(played, sheet, offset, max_window):
    """Order-preserving matching for one drum: among pairings that never cross (a drummer plays
    one drum's notes in sequence), pick the one with the least total error, where leaving a hit
    or note unpaired costs max_window / 2. Equivalently, maximize the sum of (max_window - |error|)
    over non-crossing pairs: a weighted longest-chain problem, solved with a Fenwick tree in
    O(E log n) over the E candidate pairs within the window."""
    if not played or not sheet:
        return
    st = _expected(sheet, offset)
    edges = []  # (played index, sheet index, gain); played and sheet times are strictly increasing
    for i, p in enumerate(played):
        lo = int(np.searchsorted(st, p.time - max_window, "left"))
        hi = int(np.searchsorted(st, p.time + max_window, "right"))
        edges += [(i, j, max_window - abs(p.time - st[j])) for j in range(lo, hi)]
    if not edges:
        return

    n = len(sheet)
    tree_val = [0.0] * (n + 1)  # Fenwick tree: prefix max of best chain value ending at sheet j
    tree_arg = [-1] * (n + 1)

    def query(j):  # best chain over sheet indices < j
        best, arg = 0.0, -1
        while j > 0:
            if tree_val[j] > best:
                best, arg = tree_val[j], tree_arg[j]
            j -= j & -j
        return best, arg

    def update(j, val, arg):
        j += 1
        while j <= n:
            if val > tree_val[j]:
                tree_val[j], tree_arg[j] = val, arg
            j += j & -j

    value, prev = [0.0] * len(edges), [-1] * len(edges)
    k = 0
    while k < len(edges):
        group_end = k
        while group_end < len(edges) and edges[group_end][0] == edges[k][0]:
            group_end += 1
        for e in range(k, group_end):  # query before updating: one played hit, one sheet note
            best, arg = query(edges[e][1])
            value[e], prev[e] = best + edges[e][2], arg
        for e in range(k, group_end):
            update(edges[e][1], value[e], e)
        k = group_end

    e = max(range(len(edges)), key=value.__getitem__)
    while e != -1:
        i, j, _ = edges[e]
        played[i].match, sheet[j].match = sheet[j], played[i]
        e = prev[e]


def match(played, sheet, offset=0.0, max_window=0.1, track_drift=True):
    """Pairs played hits with sheet notes (sets .match on both).

    With track_drift, players who rush or drag are followed: after a first pass, the local timing
    (rolling median of errors within ±DRIFT_HALF_WIDTH_S) becomes the expected offset for each
    sheet note, and matching is redone, DRIFT_ITERATIONS times. Only the pairing uses this;
    reported errors are still measured against the sheet, so the drift stays visible.
    """
    _match_once(played, sheet, offset, max_window)
    if not track_drift:
        return
    for _ in range(DRIFT_ITERATIONS):
        local = _local_offsets(played, sheet, offset)
        if local is None:
            return
        _match_once(played, sheet, local, max_window)


def _local_offsets(played, sheet, fallback):
    pairs = sorted((p.match.time, p.time - p.match.time) for p in played if p.match)
    if not pairs:
        return None
    xs = np.array([x for x, _ in pairs])
    ys = np.array([y for _, y in pairs])
    at = np.array([n.time for n in sheet])
    lo = np.searchsorted(xs, at - DRIFT_HALF_WIDTH_S)
    hi = np.searchsorted(xs, at + DRIFT_HALF_WIDTH_S)
    return {id(n): float(np.median(ys[a:b])) if b > a else fallback for n, a, b in zip(sheet, lo, hi)}


def _match_once(played, sheet, offset, max_window):
    for h in played + sheet:
        h.match = None
    by_midi = {}
    for h in played:
        by_midi.setdefault(h.midi, ([], []))[0].append(h)
    for h in sheet:
        if h.midi in by_midi:
            by_midi[h.midi][1].append(h)
    for p, s in by_midi.values():
        _match_in_order(p, s, offset, max_window)

    # Fallback for leftovers: same kind of drum. Windows come from *all* sheet notes of that kind,
    # so leftovers keep tight windows instead of seeing artificially large gaps.
    window, everything = {}, {}
    for h in sheet:
        everything.setdefault(family(h.midi), []).append(h)
    for notes in everything.values():
        window.update(zip(map(id, notes), _windows([n.time for n in notes], max_window)))
    groups = {}
    for h in played:
        if h.match is None:
            groups.setdefault(family(h.midi), ([], []))[0].append(h)
    for h in sheet:
        if h.match is None and family(h.midi) in groups:
            groups[family(h.midi)][1].append(h)
    for p, s in groups.values():
        _match_group(p, s, [window[id(n)] for n in s], offset)


def estimate_offset(played, sheet, max_window=0.1):
    """Most common (played - sheet) time difference between same-drum hits, preferring 0."""
    diffs = []
    by_midi = {}
    for s in sheet:
        by_midi.setdefault(s.midi, []).append(s.time)
    for p in played:
        st = by_midi.get(p.midi)
        if not st:
            continue
        lo = np.searchsorted(st, p.time - OFFSET_SEARCH_S)
        hi = np.searchsorted(st, p.time + OFFSET_SEARCH_S)
        diffs.append(p.time - np.asarray(st[lo:hi]))
    if not diffs:
        return 0.0
    d = np.concatenate(diffs)
    bins = np.round(d / OFFSET_BIN_S).astype(int)
    vals = np.unique(bins)
    # score each candidate offset by how many diffs fall within ±2 bins (tolerates jitter)
    smooth = np.convolve(np.bincount(bins - vals[0]), np.ones(5), "same")
    best = vals[0] + int(np.argmax(smooth))
    candidate = float(np.median(d[np.abs(d - best * OFFSET_BIN_S) <= 2 * OFFSET_BIN_S]))

    def matched(off):
        match(played, sheet, off, max_window)
        return sum(p.match is not None for p in played)

    at_zero, at_best = matched(0.0), matched(candidate)
    return candidate if at_best > at_zero * OFFSET_MIN_GAIN else 0.0


# ---- reporting -----------------------------------------------------------------------------

def fmt_ms(seconds):
    return f"{seconds * 1000:+.1f} ms"


def verdict(err, tolerance):
    if abs(err) <= tolerance:
        return "on time"
    return "late" if err > 0 else "early"


def timeline(played, offset, segment_s):
    """[(segment_start_s, n, median_error_s)] over sheet time, for spotting rushing/dragging."""
    seg = {}
    for p in played:
        if p.match:
            seg.setdefault(int(p.match.time // segment_s), []).append(p.time - (p.match.time + offset))
    return [(k * segment_s, len(es), statistics.median(es)) for k, es in sorted(seg.items())]


def _bar(err, ms_per_char=2, width=20):
    n = min(width, round(abs(err) * 1000 / ms_per_char))
    left = ("<" * n if err < 0 else "").rjust(width)
    right = (">" * n if err > 0 else "").ljust(width)
    return f"{left}|{right}"


def report(played, sheet, offset, tolerance, summary_only=False, show_missed=True, out=None,
           segment_s=10.0):
    def w(s=""):
        print(s, file=out or sys.stdout)

    if not summary_only:
        rows = [(p.time, 0, p) for p in played]
        if show_missed:
            rows += [(s.time + offset, 1, s) for s in sheet if s.match is None]
        w(f"{'played_s':>11}  {'drum':<15} {'sheet_s':>11}  {'error':>10}  verdict")
        for _, kind, h in sorted(rows, key=lambda r: (r[0], r[1], r[2].midi)):
            if kind == 1:
                w(f"{'-':>11}  {h.name:<15} {h.time:11.6f}  {'':>10}  MISSED")
            elif h.match is None:
                w(f"{h.time:11.6f}  {h.name:<15} {'-':>11}  {'':>10}  EXTRA (no candidate in sheet)")
            else:
                err = h.time - (h.match.time + offset)
                note = f"  (expected {h.match.name})" if h.match.midi != h.midi else ""
                w(f"{h.time:11.6f}  {h.name:<15} {h.match.time:11.6f}  {fmt_ms(err):>10}  "
                  f"{verdict(err, tolerance)}{note}")
        w()

    errs = [p.time - (p.match.time + offset) for p in played if p.match]
    w("Summary")
    w(f"  offset applied:   {offset:+.6f} s (performance = sheet + offset)")
    w(f"  played hits:      {len(played)}   sheet notes: {len(sheet)}")
    w(f"  matched:          {len(errs)}   "
      f"(drum differs: {sum(1 for p in played if p.match and p.match.midi != p.midi)})")
    w(f"  extra:            {sum(1 for p in played if p.match is None)}")
    w(f"  missed:           {sum(1 for s in sheet if s.match is None)}")
    if errs:
        early = sum(e < -tolerance for e in errs)
        late = sum(e > tolerance for e in errs)
        w(f"  on time (±{tolerance * 1000:.0f} ms): {len(errs) - early - late}   early: {early}   late: {late}")
        w(f"  mean error:       {fmt_ms(statistics.fmean(errs))}  (+ = late)")
        w(f"  median error:     {fmt_ms(statistics.median(errs))}")
        w(f"  mean |error|:     {statistics.fmean(abs(e) for e in errs) * 1000:.1f} ms")
        if len(errs) > 1:
            w(f"  std dev:          {statistics.stdev(errs) * 1000:.1f} ms")
        w("  per drum:")
        by = {}
        for p in played:
            if p.match:
                by.setdefault(p.match.name, []).append(p.time - (p.match.time + offset))
        for name, es in sorted(by.items(), key=lambda kv: -len(kv[1])):
            w(f"    {name:<15} n={len(es):<5} mean {fmt_ms(statistics.fmean(es)):>10}   "
              f"mean |err| {statistics.fmean(abs(e) for e in es) * 1000:5.1f} ms")
        w(f"  timing over time (median error per {segment_s:g} s of sheet time; "
          f"< rushing / early, > dragging / late, 1 char = 2 ms):")
        for start, n, med in timeline(played, offset, segment_s):
            w(f"    {start:7.1f}s  n={n:<4} {fmt_ms(med):>10}  {_bar(med)}")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("played", help="performance .drums.txt (reconstructed from real playing)")
    p.add_argument("sheet", help="reference .drums.txt generated from the sheet music")
    p.add_argument("--offset", type=float, help="performance = sheet + OFFSET seconds (default: auto)")
    p.add_argument("--window", type=float, default=0.1, help="max distance from the expected (drift-tracked) time for a match, seconds (default 0.1)")
    p.add_argument("--tolerance", type=float, default=0.010,
                   help="|error| up to this counts as on time, seconds (default 0.010)")
    p.add_argument("--summary-only", action="store_true", help="skip the per-hit listing")
    p.add_argument("--hide-missed", action="store_true", help="don't list unplayed sheet notes")
    p.add_argument("--segment", type=float, default=10.0,
                   help="seconds per row in the timing-over-time summary (default 10)")
    p.add_argument("--no-drift-tracking", action="store_true",
                   help="match against the sheet as-is instead of following rushing/dragging")
    args = p.parse_args(argv)

    played, sheet = read_notes(args.played), read_notes(args.sheet)
    if not played or not sheet:
        raise SystemExit("Both files need at least one note")
    offset = args.offset if args.offset is not None else estimate_offset(played, sheet, args.window)
    match(played, sheet, offset, args.window, track_drift=not args.no_drift_tracking)
    report(played, sheet, offset, args.tolerance, args.summary_only, not args.hide_missed,
           segment_s=args.segment)


if __name__ == "__main__":
    main()
