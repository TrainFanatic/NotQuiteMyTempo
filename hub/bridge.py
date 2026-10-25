"""Referee bridge: Kevin's drum hits -> Chris's live matcher -> the hub.

Reads Kevin's transcriber output (`time_s  midi  name` lines, rushingOrDragging/
file_format.md) from stdin or a FIFO, pairs every hit with the sheet using
Chris's own LiveMatcher (imported, unmodified), and publishes the contract
messages Fletcher's mood runs on:

  session.state  playing / stopped (+ bpm, bar)   so Fletcher starts listening
  judgment.note  one per hit: error_ms (+ = late), bar, beat, drum, grade;
                 grade "miss" for sheet notes the drummer skipped, "extra" for
                 hits with no sheet note

    python live_transcription/tools/transcribe_wav.py ... --realtime \\
        | python -m hub.bridge out/song.drums.txt --bpm 94

Model-free live path: Kevin's `--onsets-only` lines carry midi 0 ("onset",
drum unknown). Those go through ScoreGate, which gives each onset the note
group the sheet expects there (judged against that group's time) and drops
cymbal-tail re-triggers; lines with a real drum skip the gate exactly as before.

It obeys session.command: stop mutes judging; restart(from_bar, t0?) re-aligns
the sheet so bar from_bar starts at t0 (laptop clock, Fletcher's count-off
downbeat), or one count-in after the command arrives when t0 is absent. Hub
time maps to Kevin's stream time from hit arrival times minus --latency-ms.
The count_in session.state it answers with carries the t0 it used.

Session mode (`--session`, what `python -m nqmt` runs): no sheet argument; it follows the
session controller (hub/session.py), loading songs/<song_id>/ at runtime on each count_in,
and with `--clock laptop` its input lines carry laptop monotonic times (SessionBridge).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import statistics
import sys
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "rushingOrDragging"))  # Chris's modules import each other flat

import compare_performance as cp  # noqa: E402
import live_compare as lc  # noqa: E402

from fletcher.adapt import _grade  # noqa: E402  (same grade bands Fletcher uses)
from fletcher.contract import JUDGMENT_NOTE, SESSION_COMMAND, SESSION_STATE, now  # noqa: E402

log = logging.getLogger("bridge")


class BarClock:
    """Song time (s, 0 = first downbeat) <-> bar. Uses Chris's tempo map when
    one is given (tempo changes, odd meters), else a fixed bpm in N/4."""

    def __init__(self, bpm: float, beats_per_bar: int = 4, tempo_map: dict | None = None):
        self.bpm, self.beats_per_bar, self.map = bpm, beats_per_bar, tempo_map
        if tempo_map:
            from songsterr import tempo_map as tm
            self._tm = tm
            self.bpm = tempo_map["bars"][0]["bpm"]

    @property
    def beat_s(self) -> float:
        return 60.0 / self.bpm

    def bar_at(self, t: float) -> tuple[int, float]:
        """(bar, beat) with beat 1.0 = the downbeat."""
        if self.map:
            bar, beat = self._tm.bar_at(self.map, t)  # beat is 0-based there
            return int(bar), float(beat) + 1.0
        # .drums.txt rounds to 1 us, which can put a downbeat a hair before its bar line
        beats = (max(0.0, t) + 1e-6) / self.beat_s
        return int(beats // self.beats_per_bar) + 1, beats % self.beats_per_bar + 1.0

    def bar_start(self, bar: int) -> float:
        if self.map:
            return float(self._tm.bar_start(self.map, min(bar, len(self.map["bars"]))))
        return (bar - 1) * self.beats_per_bar * self.beat_s


ONSET_MIDI = 0  # Kevin's --onsets-only marker: an onset whose drum is unknown
TICK_S = 0.05  # the loops settle the gate's held onset (Referee.tick) when no line came this long


class ScoreGate:
    """Names unknown-drum onsets from the sheet, causally (datacollection/score_flux.py's
    gating, live). Sheet notes are grouped by time (a chord = one group; notes within `tail` of
    a group's first note join it). An onset takes the
    nearest unconsumed group within +-window of (group time + drift) and gets all its notes;
    drift is an EMA of the matched errors. A group within +-window of its plain sheet time is
    also in reach, so a drummer who swings back faster than the EMA follows doesn't leave the
    gate locked out. An onset within `tail` of the last one is a cymbal tail / double trigger
    and is dropped (in a run faster than that, `tail` shrinks to 0.6 x the gap to the next
    group: a 70 ms tom fill gives onsets ~60 ms apart). One with no group in reach is dropped
    too while it is within `ring` of the last matched onset (a kick or crash re-triggers 50-90
    ms into its own decay, and the Quest mic's mute/un-mute makes a ringing drum re-attack
    150-200 ms after the hit; measured on the real single-drum takes); otherwise it is an extra.
    Two more drops keep a stray onset from stealing the NEXT group (found on a MIDI import's
    ride/splash triplets and 32nd-note pairs): a re-trigger within `ring` whose error is more
    than `jump` earlier than the last matched onset's (a crash re-attacks ~70 ms into its decay,
    a triplet ahead of the next note; a drummer doesn't rush 50+ ms from one stroke to the
    next), and the later stroke of a group that spans two times (a 35 ms pair the detector does
    split), expected at the matched onset + that spacing, +-tail/2. Neither counts as a stroke
    for the tail window, so the real hit just after still gets its group.
    Times are stream times (sheet time + offset)."""

    def __init__(self, sheet: list, offset: float = 0.0, window: float = 0.1, tail: float = 0.06,
                 ring: float = 0.2, alpha: float = 0.3, jump: float = 0.05, mask: float = 0.1):
        self.window, self.tail, self.ring, self.alpha, self.jump = window, tail, ring, alpha, jump
        self.mask = mask
        groups: dict[float, list[int]] = {}  # stream time -> indices into `sheet`
        for i, n in enumerate(sheet):
            groups.setdefault(round(n.time + offset, 6), []).append(i)
        # Notes closer than `tail` can't get onsets of their own (the second one would be dropped
        # as a tail), so they form one group: unquantized MIDI chords (1-2 ms apart), flams.
        self.times, self.notes, self.strokes = [], [], []  # per group: first note's stream time,
        for t in sorted(groups):                           # sheet indices, later times - first
            if self.times and t - self.times[-1] < tail:
                self.notes[-1] += groups[t]
                self.strokes[-1].append(t - self.times[-1])
            else:
                self.times.append(t)
                self.notes.append(list(groups[t]))
                self.strokes.append([])
        self.midis = [sorted({sheet[i].midi for i in g}) for g in self.notes]
        self.next = 0               # first group not yet consumed or passed
        self.drift = 0.0
        self.last: float | None = None  # stream time of the last onset not dropped
        self.matched: float | None = None  # ... and of the last one that took a group
        self.matched_err = 0.0  # that one's error
        self.pending: list[float] = []  # where the matched group's later strokes are expected
        self.dropped = 0
        self.held = None  # push(): (s, group, state before it, deadline) of an onset on hold
        self.taken: set[int] = set()  # groups an onset took

    def assign(self, s: float) -> list[int] | None:
        """MIDI notes for an onset at stream time s: [] = tail (drop it), None = extra."""
        j = self.assign_group(s)
        return j if j is None or j == [] else self.midis[j]

    def _tail(self) -> float:
        n = self.next
        if 0 < n < len(self.times):
            return min(self.tail, 0.6 * (self.times[n] - self.times[n - 1]))
        return self.tail

    def assign_group(self, s: float):
        """Like assign, but the index of the group the onset took (its notes: self.notes[j])."""
        if self.last is not None and 0 <= s - self.last < self._tail():
            self.dropped += 1
            return []
        for p in self.pending:
            if abs(s - p) <= self.tail / 2:
                self.pending.remove(p)
                self.dropped += 1  # the group's own later stroke
                return []
        target = s - self.drift
        lo, hi = min(target, s) - self.window, max(target, s) + self.window
        while self.next < len(self.times) and self.times[self.next] < lo:
            self.next += 1  # passed: the drummer skipped it (the Referee reports the miss)
        best = None
        for j in range(self.next, len(self.times)):
            t = self.times[j]
            if t > hi:
                break
            if min(abs(t - target), abs(t - s)) > self.window:
                continue
            if best is None or abs(t - target) < abs(self.times[best] - target):
                best = j
        if best is None:
            self.last = s
            if self.matched is not None and s - self.matched < self.ring:
                self.dropped += 1
                return []
            return None
        err = s - self.times[best]
        if self.matched is not None and s - self.matched < self.ring and err - self.matched_err < -self.jump:
            self.dropped += 1  # a re-trigger reaching ahead
            return []
        self.last, self.next, self.matched, self.matched_err = s, best + 1, s, err
        self.pending = [s + d for d in self.strokes[best]]
        self.drift += self.alpha * (err - self.drift)
        self.taken.add(best)
        return best

    def masked(self, j: int) -> bool:
        """Group j (not taken) was likely played but not heard: only cymbals/hats, within `mask`
        after a group an onset took. A quiet ride or splash 70-90 ms after a snare stroke is
        buried in its decay and the detector gives it no onset of its own (measured on a MIDI
        import rendered clean: 9 of 26 notes in one bar), so it isn't reported as a miss."""
        return (j > 0 and (j - 1) in self.taken and self.times[j] - self.times[j - 1] <= self.mask
                and all(cp.family(m) in ("hihat", "ride", "crash") for m in self.midis[j]))

    # One onset of look-ahead, for the onsets assign() can't tell apart: a re-trigger 50-90 ms
    # into a kick's decay (or a stray onset) lands where an early stroke would. An onset that
    # takes its group early (error < -hold) is held until the next onset: if that one is nearer
    # the group, it takes it and the held one was a re-trigger (dropped, within `ring` of the
    # last matched onset) or an extra; else the held one keeps it. flush() settles a hold once no nearer onset can still come.
    # Decisions are (s, group index | [] | None, groups settled before it), in onset order.
    _STATE = ("next", "drift", "last", "matched", "matched_err", "pending", "dropped", "taken")

    @staticmethod
    def _copy(state: dict) -> dict:
        return {k: (v.copy() if isinstance(v, (list, set)) else v) for k, v in state.items()}

    def _save(self) -> dict:
        return self._copy({k: getattr(self, k) for k in self._STATE})

    def push(self, s: float, hold: float = 0.03) -> list[tuple]:
        out = []
        if self.held is not None:
            hs, hj, state, deadline = self.held
            self.held = None
            # s just after the held one is its own tail, unless the held one was itself a
            # re-trigger (within `ring` of the onset before it): then s may be the real stroke
            ring = state["matched"] is not None and hs - state["matched"] < self.ring
            if s <= deadline and (ring or s - hs >= self._tail()):
                now = self._save()
                for k, v in self._copy(state).items():
                    setattr(self, k, v)  # replay s in the held one's place
                target = self.times[hj] + self.drift
                j = self.assign_group(s)
                if j == hj and abs(s - target) < abs(hs - target):
                    if ring:
                        self.dropped += 1
                        return [(hs, [], hj), (s, j, j)]
                    return [(hs, None, state["next"]), (s, j, j)]
                for k, v in now.items():
                    setattr(self, k, v)
            out.append((hs, hj, hj))
        state = self._save()
        j = self.assign_group(s)
        if isinstance(j, int) and s - self.times[j] < -hold:
            target = self.times[j] + state["drift"]
            self.held = (s, j, state, 2 * target - s)  # a nearer onset must come before this
            return out
        return out + [(s, j, self.next if j is None else j if j != [] else self.next)]

    def flush(self, s_now: float | None = None) -> list[tuple]:
        """Settle a held onset once onsets up to its deadline have all arrived (None: now)."""
        if self.held is None or (s_now is not None and s_now <= self.held[3]):
            return []
        hs, hj, _, _ = self.held
        self.held = None
        return [(hs, hj, hj)]


class Referee:
    """Pure core (no I/O): feed lines and commands, get contract messages back
    as (type, data) pairs. Times: `s` = Kevin's stream time, `rx` = hub clock."""

    def __init__(self, sheet: list, clock: BarClock, latency_s: float = 0.17, count_in_beats: int = 4,
                 song_id: str = "", gate: dict | None = None, line_shift_s: float = 0.0):
        self.sheet, self.clock = sheet, clock
        self.gate_args = gate or {}  # ScoreGate window/tail/alpha
        self.line_shift_s = line_shift_s  # subtracted from every line time (clock mode: input latency)

        self.latency_s, self.count_in_beats, self.song_id = latency_s, count_in_beats, song_id
        self.offset = 0.0  # stream time = sheet time + offset
        self.matcher = lc.LiveMatcher(self._copy(sheet), self.offset)
        self.gate = ScoreGate(sheet, self.offset, **self.gate_args)
        self._gate_ptr = 0  # first gate group whose misses aren't reported yet
        self._gate_pre = 0  # gate notes before the matcher's sheet (decoys after a restart)
        self.unheard = 0  # sheet notes ScoreGate.masked() excused
        self.phase = "waiting"  # waiting -> playing -> stopped -> (restart) count_in -> playing
        self.bar = 0
        self.clock_offsets: deque[float] = deque(maxlen=64)  # rx - s - latency, per hit
        self.resume_at_s: float | None = None
        self.expire_margin_s = 0.15  # expire(): line-delay jitter on top of the gate's reach
        # tick() before any line arrived (silence from the downbeat on): stream time of a line
        # arriving now = rx - this. None = unknown (stream mode: no misses until the first line).
        self.idle_lag_s: float | None = None
        self.mute_windows: list[tuple[float, float]] = []  # laptop-clock spans Fletcher is audible
        self.bleed = 0  # extras dropped inside a mute window
        self.midi_lines = False  # a line with a real drum came (not --onsets-only)

    @staticmethod
    def _copy(notes):
        return [cp.Hit(n.time, n.midi) for n in notes]

    def _state(self, state: str) -> tuple[str, dict]:
        return SESSION_STATE, {"state": state, "song_id": self.song_id, "bpm": round(self.clock.bpm, 3),
                               "bar": max(1, self.bar), "beat": 1}

    def feed_line(self, text: str, rx: float) -> list[tuple[str, dict]]:
        text = text.strip()
        if not text or text.startswith("#"):
            return []
        try:
            fields = text.split()
            s, midi = float(fields[0]) - self.line_shift_s, int(fields[1])
        except (IndexError, ValueError):
            log.warning("ignoring malformed line: %r", text)
            return []
        self.clock_offsets.append(rx - s - self.latency_s)
        if self.phase == "stopped":
            return []
        out: list[tuple[str, dict]] = []
        if self.phase == "count_in":
            if self.resume_at_s is not None and s < self.resume_at_s - self.clock.beat_s / 2:
                return []  # still counting in: don't judge stick clicks or noodling
            self.phase = "playing"
            out.append(self._state("playing"))
        if midi != ONSET_MIDI:
            self.midi_lines = True
            return out + self._judge(s, midi)
        for d in self.gate.push(s):
            out += self._judge_gated(*d)
        return out

    def tick(self, rx: float) -> list[tuple[str, dict]]:
        """Settle the gate's held onset once every line up to its deadline must have arrived
        (stream time of a line arriving now, from the arrival offsets), then report the misses
        the clock has passed (expire)."""
        if self.phase == "stopped":
            return []
        if self.clock_offsets:
            s_now = rx - self.latency_s - statistics.median(self.clock_offsets)
        elif self.idle_lag_s is not None:
            s_now = rx - self.idle_lag_s
        else:
            return []
        out = self.settle(s_now - 0.02) if self.gate.held is not None else []
        return out + self.expire(s_now)

    def settle(self, s_now: float | None = None) -> list[tuple[str, dict]]:
        """Judge the gate's held onset (by stream time s_now; None = now, the input ended)."""
        out: list[tuple[str, dict]] = []
        for d in self.gate.flush(s_now):
            out += self._judge_gated(*d)
        return out

    def _judge(self, s: float, midi: int) -> list[tuple[str, dict]]:
        out: list[tuple[str, dict]] = []
        ptr = self.matcher._missed_ptr
        hit, err = self.matcher.feed(s, midi)
        for note in self.matcher.sheet[ptr:self.matcher._missed_ptr]:
            if note.match is None:
                out += self._note(note.time, note.name, None, "miss")
        song_t = s - self.offset
        if self.phase == "waiting":
            self.phase = "playing"
            self.bar = self.clock.bar_at(song_t)[0]
            out.insert(0, self._state("playing"))
        if err is None:
            out += self._note(song_t, hit.name, None, "extra")
        else:
            out += self._note(hit.match.time, hit.name, err * 1000.0, None, song_t)
        return out

    def _judge_gated(self, s: float, group, done: int) -> list[tuple[str, dict]]:
        """An unknown-drum onset the gate placed: judged against the group the gate gave it
        (error = onset - that group's sheet time), not re-searched by LiveMatcher, whose own
        drift estimate a busy part (ride/splash triplets, extra onsets) can pull ~150 ms off so
        clean hits grade as misses. The matcher's sheet still holds the bookkeeping (matches,
        misses, extras) so finish() and its counters stay right. Groups the gate passed without
        an onset are the misses, so the two can't disagree. group: index / [] tail / None extra;
        done: the groups before it are settled (ScoreGate.push)."""
        if group == []:
            return []
        m, g = self.matcher, self.gate
        out = self._report_passed(done)
        pre = self._gate_pre
        hit = cp.Hit(s, ONSET_MIDI)
        m.played.append(hit)
        song_t = s - self.offset
        if self.phase == "waiting":
            self.phase = "playing"
            self.bar = self.clock.bar_at(song_t)[0]
            out.insert(0, self._state("playing"))
        if group is None:
            self._settle()
            if self.muted(s):  # most likely Fletcher's own bark through the headset mic
                self.bleed += 1
                return out
            m.extra += 1
            return out + self._note(song_t, hit.name, None, "extra")
        err_ms = (s - g.times[group]) * 1000.0
        for i in g.notes[group]:
            if i < pre:
                continue  # a decoy: the pickup before the bar was played, nothing to report
            note = m.sheet[i - pre]
            note.match = cp.Hit(s, note.midi, note)
            out += self._note(note.time, note.name, err_ms, None, song_t)
        self._gate_ptr = group + 1
        self._settle()
        return out

    def _report_passed(self, done: int) -> list[tuple[str, dict]]:
        """The gate groups before `done` that no onset took: misses (or excused, ScoreGate.masked)."""
        m, g, pre = self.matcher, self.gate, self._gate_pre
        out: list[tuple[str, dict]] = []
        for j in range(self._gate_ptr, done):
            for i in g.notes[j]:
                note = m.sheet[i - pre] if i >= pre else None
                if note is not None and note.match is None and g.masked(j):
                    note.match = note  # heard as part of the stroke before it: not judged
                    self.unheard += 1
                elif note is not None and note.match is None:
                    m.missed += 1
                    out += self._note(note.time, note.name, None, "miss")
        self._gate_ptr = max(self._gate_ptr, done)
        return out

    def expire(self, s_now: float) -> list[tuple[str, dict]]:
        """Report the misses the clock has made certain: every gate group no onset can reach any
        more (sheet time + drift + window + expire_margin_s before s_now, the stream time of a line
        arriving now). Without this the misses only came with the NEXT onset, so a drummer who sat
        still for 30 s got 30 s of misses in one burst, all at once, whenever a stray onset finally
        came (headset session 2026-10-25), and Fletcher cut off "in bar 3" at bar 129. Once the
        downbeat has passed the song is playing even if nobody hits anything: `playing` goes out."""
        g = self.gate
        if (self.phase not in ("playing", "count_in") or self.resume_at_s is None or g.held is not None
                or self.midi_lines):  # a drum model's lines: LiveMatcher reports its own misses
            return []
        if self.phase == "count_in" and s_now < self.resume_at_s:
            return []  # still counting in (feed_line ignores hits until just before the downbeat)
        reach = g.window + max(0.0, g.drift) + self.expire_margin_s
        j = self._gate_ptr
        while j < len(g.times) and g.times[j] + reach < s_now:
            j += 1
        if j == self._gate_ptr:
            return []
        out: list[tuple[str, dict]] = []
        if self.phase == "count_in":
            self.phase = "playing"
            out.append(self._state("playing"))
        out += self._report_passed(j)
        g.next = max(g.next, j)  # a late onset can't take a group already reported as missed
        self._settle()
        return out

    def muted(self, s: float) -> bool:
        """s (stream time) falls while Fletcher's own voice is audible (mute_windows)."""
        t = s + self.line_shift_s  # back on the laptop clock in clock mode
        return any(a <= t <= b for a, b in self.mute_windows)

    def mute(self, start: float, end: float) -> None:
        """Fletcher speaks from `start` to `end` (laptop clock): extras then are self-bleed."""
        self.mute_windows.append((start, end))
        del self.mute_windows[:-8]

    def _settle(self) -> None:
        """LiveMatcher's miss pointer past every note of the settled groups (a group's notes are
        consecutive in the sheet), so finish() flushes only the rest."""
        g, m = self.gate, self.matcher
        first = min(g.notes[self._gate_ptr]) - self._gate_pre if self._gate_ptr < len(g.notes) else len(m.sheet)
        m._missed_ptr = max(m._missed_ptr, first, 0)

    def _note(self, sheet_t: float, drum: str, err_ms: float | None, grade: str | None,
              song_t: float | None = None) -> list[tuple[str, dict]]:
        bar, beat = self.clock.bar_at(sheet_t)
        out = []
        if bar > self.bar:
            self.bar = bar
            out.append(self._state("playing"))
        data = {"drum": drum, "bar": bar, "beat": round(beat, 3),
                "error_ms": None if err_ms is None else round(err_ms, 1),
                "grade": grade or _grade(err_ms), "song_time": round(sheet_t if song_t is None else song_t, 4)}
        out.append((JUDGMENT_NOTE, data))
        return out

    def on_command(self, data: dict, rx: float) -> list[tuple[str, dict]]:
        action = data.get("action")
        if action == "stop" and self.phase != "stopped":
            self.phase = "stopped"
            log.info("stop at bar %d", self.bar)
            return [self._state("stopped")]
        if action == "restart":
            bar = max(1, int(data.get("from_bar") or 1))
            # Bar `bar` starts at Fletcher's count-off downbeat t0 (hub clock) when he
            # sends it, so a late or early command doesn't shift it; else one count-in
            # after the command arrives. Then map that hub time onto the stream clock.
            t0 = data.get("t0")
            exact = isinstance(t0, (int, float)) and not isinstance(t0, bool)
            start_rx = float(t0) if exact else rx + self.count_in_beats * self.clock.beat_s
            resume = start_rx - statistics.median(self.clock_offsets) if self.clock_offsets else None
            self.align(bar, resume)
            log.info("restart from bar %d at %s; expecting it at stream time %s", bar,
                     "t0" if exact else "rx + count-in",
                     f"{self.resume_at_s:.3f} s" if self.resume_at_s is not None else "unknown")
            typ, state = self._state("count_in")
            return [(typ, {**state, "t0": round(start_rx, 4)})]
        return []

    def align(self, bar: int, start_s: float | None, first: float | None = None) -> None:
        """Count in to bar `bar`: its downbeat (song time `first`, default the tempo map's) is
        expected at stream time `start_s` (None = unknown: judge from the first hit on)."""
        first = self.clock.bar_start(bar) if first is None else first
        self.resume_at_s = start_s
        self.offset = (start_s - first) if start_s is not None else 0.0
        rest = [n for n in self.sheet if n.time >= first - 1e-6]
        # The gate also gets the beat before the bar, as decoys that are never reported: a
        # drummer (or a take's pre-roll) playing into the bar must not have that pickup take the
        # downbeat's group.
        pre = [n for n in self.sheet if first - self.clock.beat_s - 1e-6 <= n.time < first - 1e-6]
        self.matcher = lc.LiveMatcher(self._copy(rest), self.offset)
        self.gate = ScoreGate(pre + rest, self.offset, **self.gate_args)
        self._gate_ptr, self._gate_pre = 0, len(pre)
        self.phase, self.bar = "count_in", bar

    def finish(self) -> list[tuple[str, dict]]:
        """The song is over: every sheet note not played yet is a miss. Judging stops."""
        out: list[tuple[str, dict]] = []
        if self.phase in ("playing", "count_in"):
            out += self.settle()
            m = self.matcher
            for note in m.sheet[m._missed_ptr:]:
                if note.match is None:
                    m.missed += 1
                    out += self._note(note.time, note.name, None, "miss")
            m._missed_ptr = len(m.sheet)
        self.phase = "stopped"
        return out


class SessionBridge:
    """Runtime song switching: follows the session controller's session.state instead of one
    sheet per process. On count_in it loads songs/<song_id>/ (when the song changed) and counts
    the Referee in so bar `from_bar` (song time song_time_at_t0) starts at t0. Clock mode
    (`laptop`): input lines carry laptop monotonic times, so stream time = laptop time - input
    latency and song time = t - t0 + song_time_at_t0 - latency, exactly. Stream mode: line
    times map to the hub clock through hit arrival times, as in the one-sheet bridge.
    `finished` flushes the unplayed notes as misses; `idle` drops the song. Fletcher's
    restart is left to the controller (its count_in follows); his stop mutes judging.
    Calibration (nqmt/calibrate.py): `mic_latency` maps a mic source to its measured round
    trip (s); when the current `mic` has one it replaces input_latency_s, and a
    `calibrate.result` or (with follow_mic) the headset's quest.settings mic switch updates it."""

    def __init__(self, songs_dir: Path | str, clock: str = "laptop", latency_s: float = 0.17,
                 input_latency_s: float = 0.0, count_in_beats: int = 4, gate: dict | None = None,
                 session_src: str = "session", mic: str = "", mic_latency: dict | None = None,
                 follow_mic: bool = False):
        self.songs_dir, self.clock_mode = Path(songs_dir), clock
        self.latency_s, self.input_latency_s, self.count_in_beats = latency_s, input_latency_s, count_in_beats
        self.gate, self.session_src = gate or {}, session_src
        self.ref: Referee | None = None
        self.song_id = ""
        self.base_input_latency_s = input_latency_s
        self.mic, self.mic_latency, self.follow_mic = mic, dict(mic_latency or {}), follow_mic
        self._apply_latency()

    def _apply_latency(self) -> None:
        self.input_latency_s = self.mic_latency.get(self.mic, self.base_input_latency_s)
        if self.ref is not None and self.clock_mode == "laptop":
            self.ref.line_shift_s = self.input_latency_s
            self.ref.idle_lag_s = self._idle_lag()
        log.info("input latency %.1f ms (mic %s%s)", self.input_latency_s * 1000, self.mic or "?",
                 ", calibrated" if self.mic in self.mic_latency else "")

    def _idle_lag(self) -> float:
        """Clock mode, no line yet: a line arriving now is about this old in stream time (the
        transcriber's hit -> line delay, ~240 ms on the Quest mic, plus the line shift)."""
        return self.latency_s + 0.1 + self.input_latency_s

    BARK_TAIL_S = 0.35  # headset buffering + the bark's held lead (bark_lead_max_s) + decay

    def on_speech(self, data: dict, src: str, rx: float) -> None:
        """fletcher.speech bark (clip_id): while it plays, onsets with no sheet note are taken for
        its bleed through the headset mic and not reported as extras (measured 2026-10-25: 7 of 32
        barks had an onset 50-200 ms after they went out, ~2 expected by chance)."""
        if self.ref is None or src == self.session_src or not data.get("clip_id") or self.clock_mode != "laptop":
            return
        from fletcher.barks import BarkPicker
        if not hasattr(self, "_barks"):
            self._barks = BarkPicker()
        self.ref.mute(rx, rx + self._barks.duration(str(data["clip_id"])) + self.BARK_TAIL_S)

    def on_calibration(self, data: dict) -> None:
        """calibrate.result: a good measurement for a mic source becomes its latency."""
        mic, ms = data.get("mic"), data.get("latency_ms")
        if data.get("ok") and isinstance(mic, str) and isinstance(ms, (int, float)):
            self.mic_latency[mic] = float(ms) / 1000.0
            if mic == self.mic or not self.mic:
                self.mic = mic
                self._apply_latency()

    def on_settings(self, data: dict) -> None:
        mic = data.get("mic_source")
        if self.follow_mic and mic in ("laptop", "quest") and mic != self.mic:
            self.mic = mic
            self._apply_latency()

    def _load(self, song_id: str) -> Referee:
        from hub.session import load_song
        song = load_song(self.songs_dir, song_id)
        sheet = cp.read_notes(song.sheet)
        clock = BarClock(song.bpm_at(1), song.beats_per_bar, song.tempo_map)
        log.info("loaded %s: %d notes, %d bars", song_id, len(sheet), song.bars)
        ref = Referee(sheet, clock, self.latency_s, self.count_in_beats, song_id, self.gate,
                      self.input_latency_s if self.clock_mode == "laptop" else 0.0)
        if self.clock_mode == "laptop":
            ref.idle_lag_s = self._idle_lag()
        return ref

    def on_state(self, data: dict, src: str, rx: float) -> list[tuple[str, dict]]:
        if src != self.session_src:
            return []
        state = data.get("state")
        if state == "count_in":
            song_id = str(data.get("song_id") or "")
            if self.ref is None or song_id != self.song_id or data.get("reason") == "start":
                try:
                    self.ref, self.song_id = self._load(song_id), song_id
                except (OSError, ValueError) as e:
                    log.warning("can't load song %r: %s", song_id, e)
                    self.ref, self.song_id = None, ""
                    return []
            ref = self.ref
            bar = max(1, int(data.get("from_bar") or data.get("bar") or 1))
            t0 = float(data.get("t0") or rx + self.count_in_beats * ref.clock.beat_s)
            first = data.get("song_time_at_t0")
            if self.clock_mode == "laptop":
                start = t0  # stream time = laptop time - input latency (line_shift_s)
            else:
                start = t0 - statistics.median(ref.clock_offsets) if ref.clock_offsets else None
            ref.align(bar, start, None if first is None else float(first))
            log.info("count_in %s bar %d at t0 %.3f (%s)", song_id, bar, t0, data.get("reason", "?"))
            return []
        if self.ref is None:
            return []
        if state == "finished":
            return self.ref.finish()
        if state == "idle":
            self.ref.phase = "stopped"
            self.ref, self.song_id = None, ""
        return []

    def on_command(self, data: dict, rx: float) -> list[tuple[str, dict]]:
        if self.ref is None or data.get("action") != "stop":
            return []
        return self.ref.on_command(data, rx)

    def tick(self, rx: float) -> list[tuple[str, dict]]:
        return self.ref.tick(rx) if self.ref is not None else []

    def feed_line(self, text: str, rx: float) -> list[tuple[str, dict]]:
        if self.ref is None or self.ref.phase in ("waiting", "stopped"):
            if self.ref is not None and text.strip() and not text.lstrip().startswith("#"):
                try:  # keep the stream -> hub mapping fresh between runs (stream mode)
                    self.ref.clock_offsets.append(rx - float(text.split()[0]) - self.ref.latency_s)
                except (IndexError, ValueError):
                    pass
            return []
        return self.ref.feed_line(text, rx)


async def run_session(args) -> None:
    import threading
    from fletcher.hub_client import HubClient

    gate = {k: getattr(args, f"gate_{k}_ms") / 1000.0 for k in ("window", "tail", "ring", "jump")}
    gate["alpha"] = args.gate_drift_alpha
    mic_latency = {}
    if args.calibration:
        try:
            cal = json.loads(Path(args.calibration).read_text())
            mic_latency = {k: v["latency_ms"] / 1000.0 for k, v in cal.items()
                           if isinstance(v, dict) and isinstance(v.get("latency_ms"), (int, float))}
        except (OSError, ValueError) as e:
            log.info("no calibration (%s): %s", args.calibration, e.__class__.__name__)
    sb = SessionBridge(args.songs, args.clock, args.latency_ms / 1000.0, args.input_latency_ms / 1000.0,
                       args.count_in_beats, gate, args.session_src, args.mic_source, mic_latency,
                       args.follow_mic)
    hub = HubClient(args.hub, args.src)
    loop = asyncio.get_running_loop()
    lines: asyncio.Queue = asyncio.Queue()

    def reader() -> None:  # a daemon thread: an open stdin must not block shutdown
        f = open(args.input) if args.input else sys.stdin
        for line in f:
            loop.call_soon_threadsafe(lines.put_nowait, (line, now()))
        loop.call_soon_threadsafe(lines.put_nowait, (None, now()))

    async def publish(msgs) -> None:
        for typ, data in msgs:
            await hub.send(typ, data)

    async def messages() -> None:
        async for msg in hub.messages():
            typ, d, rx = msg.get("type"), msg.get("data") or {}, msg.get("_rx", now())
            try:
                if typ == SESSION_STATE:
                    await publish(sb.on_state(d, str(msg.get("src", "")), rx))
                elif typ == SESSION_COMMAND:
                    await publish(sb.on_command(d, rx))
                elif typ == "fletcher.speech":
                    sb.on_speech(d, str(msg.get("src", "")), rx)
                elif typ == "calibrate.result":
                    sb.on_calibration(d)
                elif typ == "quest.settings":
                    sb.on_settings(d)
            except Exception:
                log.exception("failed handling %s", typ)

    tasks = [asyncio.create_task(hub.run()), asyncio.create_task(messages())]
    threading.Thread(target=reader, daemon=True).start()
    log.info("session bridge: songs in %s, %s clock, hub %s", sb.songs_dir, sb.clock_mode, args.hub)
    while True:
        try:
            line, rx = await asyncio.wait_for(lines.get(), TICK_S)
        except asyncio.TimeoutError:
            await publish(sb.tick(now()))
            continue
        if line is None:
            log.info("input ended")
            if args.exit_on_eof:
                break
            continue
        await publish(sb.feed_line(line, rx))
    await asyncio.sleep(args.linger)
    for t in tasks:
        t.cancel()


async def run(args) -> None:
    from fletcher.hub_client import HubClient

    sheet = cp.read_notes(args.sheet)
    tmap = None
    tempo = Path(args.tempo) if args.tempo else Path(args.sheet).with_suffix("").with_suffix(".tempo.json")
    if tempo.exists():
        from songsterr import tempo_map as tm
        tmap = tm.load_tempo_map(tempo)
        log.info("tempo map %s", tempo)
    elif args.bpm is None:
        raise SystemExit("need --bpm (or a <sheet>.tempo.json next to the sheet)")
    clock = BarClock(args.bpm or 120.0, args.beats_per_bar, tmap)
    gate = {k: getattr(args, f"gate_{k}_ms") / 1000.0 for k in ("window", "tail", "ring", "jump")
            if getattr(args, f"gate_{k}_ms", None) is not None}  # hub.e2e builds args by hand
    if getattr(args, "gate_drift_alpha", None) is not None:
        gate["alpha"] = args.gate_drift_alpha
    ref = Referee(sheet, clock, args.latency_ms / 1000.0, args.count_in_beats, Path(args.sheet).stem, gate)
    hub = HubClient(args.hub, args.src)
    loop = asyncio.get_running_loop()
    lines: asyncio.Queue = asyncio.Queue()

    def reader() -> None:
        f = open(args.input) if args.input else sys.stdin
        for line in f:
            loop.call_soon_threadsafe(lines.put_nowait, (line, now()))
        loop.call_soon_threadsafe(lines.put_nowait, (None, now()))

    async def publish(msgs) -> None:
        for typ, data in msgs:
            await hub.send(typ, data)

    async def commands() -> None:
        async for msg in hub.messages():
            if msg.get("type") == SESSION_COMMAND:
                await publish(ref.on_command(msg.get("data") or {}, msg.get("_rx", now())))

    tasks = [asyncio.create_task(hub.run()), asyncio.create_task(commands())]
    try:  # judge even if the hub is down; sends are dropped until it's back
        await asyncio.wait_for(hub.connected.wait(), timeout=3)
    except asyncio.TimeoutError:
        log.warning("hub %s not reachable yet; judging anyway", args.hub)
    loop.run_in_executor(None, reader)
    n = 0
    while True:
        try:
            line, rx = await asyncio.wait_for(lines.get(), TICK_S)
        except asyncio.TimeoutError:
            msgs = ref.tick(now())
            n += sum(1 for t, _ in msgs if t == JUDGMENT_NOTE)
            await publish(msgs)
            continue
        if line is None:
            break
        msgs = ref.feed_line(line, rx)
        n += sum(1 for t, _ in msgs if t == JUDGMENT_NOTE)
        await publish(msgs)
    msgs = ref.settle() if ref.phase == "playing" else []
    n += sum(1 for t, _ in msgs if t == JUDGMENT_NOTE)
    log.info("input ended after %d judgments", n)
    await publish(msgs + [ref._state("stopped")])
    await asyncio.sleep(args.linger)
    for t in tasks:
        t.cancel()


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("sheet", nargs="?", help="reference .drums.txt (Chris's converter output); omit with --session")
    p.add_argument("--session", action="store_true",
                   help="follow the session controller (hub.session): load songs/<song_id>/ on each count_in")
    p.add_argument("--songs", default=str(ROOT / "songs"), help="--session: the song library folder")
    p.add_argument("--clock", choices=("laptop", "stream"), default="laptop",
                   help="--session: input line times are laptop monotonic (contract.now(); default) or "
                        "transcriber stream times (mapped through arrival times)")
    p.add_argument("--input-latency-ms", type=float, default=0.0,
                   help="--session --clock laptop: line time - the hit's real time (mic input latency)")
    p.add_argument("--session-src", default="session", help="--session: src of the controller's session.state")
    p.add_argument("--calibration", help="--session: calibration.json (nqmt/calibrate.py); a mic's measured "
                   "round trip replaces --input-latency-ms")
    p.add_argument("--mic-source", default="", help="--session: laptop|quest, the calibration entry to use")
    p.add_argument("--follow-mic", action="store_true",
                   help="--session: the headset's quest.settings mic_source switches the calibration entry")
    p.add_argument("--exit-on-eof", action="store_true", help="--session: exit when the input ends")
    p.add_argument("--bpm", type=float, help="song tempo; not needed with a tempo map")
    p.add_argument("--tempo", help="tempo map json (default: <sheet>.tempo.json if it exists)")
    p.add_argument("--beats-per-bar", type=int, default=4)
    p.add_argument("--input", help="read hits from this file/FIFO instead of stdin")
    p.add_argument("--hub", default="ws://localhost:8765")
    p.add_argument("--src", default="chris")
    p.add_argument("--latency-ms", type=float, default=170.0,
                   help="hit -> line delay of the transcriber (Kevin's core: ~170 ms); used to re-align on restart")
    p.add_argument("--count-in-beats", type=int, default=4, help="beats counted in before a restart")
    p.add_argument("--gate-window-ms", type=float, default=100.0,
                   help="unknown-drum onsets: search +- this around sheet time + drift")
    p.add_argument("--gate-tail-ms", type=float, default=60.0,
                   help="unknown-drum onsets this soon after an accepted one are tails, dropped")
    p.add_argument("--gate-ring-ms", type=float, default=200.0,
                   help="unknown-drum onsets matching no note this soon after a matched one are tails too")
    p.add_argument("--gate-drift-alpha", type=float, default=0.3,
                   help="EMA weight of each matched error in the gate's drift estimate")
    p.add_argument("--gate-jump-ms", type=float, default=50.0,
                   help="unknown-drum onsets within the ring this much earlier than the last matched one are tails")
    p.add_argument("--linger", type=float, default=1.0, help="seconds to keep obeying commands after input ends")
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s", datefmt="%H:%M:%S")
    if not a.session and not a.sheet:
        p.error("give a sheet, or --session")
    try:
        asyncio.run(run_session(a) if a.session else run(a))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
