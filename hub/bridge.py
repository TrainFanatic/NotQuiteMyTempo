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

It obeys session.command: stop mutes judging; restart(from_bar) re-aligns the
sheet so bar from_bar starts one count-in after the restart command. That
alignment is PROVISIONAL: it maps hub time to Kevin's stream time from hit
arrival times minus --latency-ms. It becomes exact once the count-off
publishes the shared song start (session.state count_in with t0).
"""

from __future__ import annotations

import argparse
import asyncio
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


class Referee:
    """Pure core (no I/O): feed lines and commands, get contract messages back
    as (type, data) pairs. Times: `s` = Kevin's stream time, `rx` = hub clock."""

    def __init__(self, sheet: list, clock: BarClock, latency_s: float = 0.17, count_in_beats: int = 4,
                 song_id: str = ""):
        self.sheet, self.clock = sheet, clock
        self.latency_s, self.count_in_beats, self.song_id = latency_s, count_in_beats, song_id
        self.offset = 0.0  # stream time = sheet time + offset
        self.matcher = lc.LiveMatcher(self._copy(sheet), self.offset)
        self.phase = "waiting"  # waiting -> playing -> stopped -> (restart) count_in -> playing
        self.bar = 0
        self.clock_offsets: deque[float] = deque(maxlen=64)  # rx - s - latency, per hit
        self.resume_at_s: float | None = None

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
            s, midi = float(fields[0]), int(fields[1])
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
            # The drummer starts bar `bar` one count-in after now. Map that hub
            # time onto the stream clock (provisional until the count-off sends t0).
            start_rx = rx + self.count_in_beats * self.clock.beat_s
            if self.clock_offsets:
                self.resume_at_s = start_rx - statistics.median(self.clock_offsets)
            else:
                self.resume_at_s = None
            first = self.clock.bar_start(bar)
            self.offset = (self.resume_at_s - first) if self.resume_at_s is not None else 0.0
            rest = [n for n in self.sheet if n.time >= first - 1e-6]
            self.matcher = lc.LiveMatcher(self._copy(rest), self.offset)
            self.phase, self.bar = "count_in", bar
            log.info("restart from bar %d; expecting it at stream time %s", bar,
                     f"{self.resume_at_s:.3f} s" if self.resume_at_s is not None else "unknown")
            return [self._state("count_in")]
        return []


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
    ref = Referee(sheet, clock, args.latency_ms / 1000.0, args.count_in_beats, Path(args.sheet).stem)
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
        line, rx = await lines.get()
        if line is None:
            break
        msgs = ref.feed_line(line, rx)
        n += sum(1 for t, _ in msgs if t == JUDGMENT_NOTE)
        await publish(msgs)
    log.info("input ended after %d judgments", n)
    await publish([ref._state("stopped")])
    await asyncio.sleep(args.linger)
    for t in tasks:
        t.cancel()


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("sheet", help="reference .drums.txt (Chris's converter output)")
    p.add_argument("--bpm", type=float, help="song tempo; not needed with a tempo map")
    p.add_argument("--tempo", help="tempo map json (default: <sheet>.tempo.json if it exists)")
    p.add_argument("--beats-per-bar", type=int, default=4)
    p.add_argument("--input", help="read hits from this file/FIFO instead of stdin")
    p.add_argument("--hub", default="ws://localhost:8765")
    p.add_argument("--src", default="chris")
    p.add_argument("--latency-ms", type=float, default=170.0,
                   help="hit -> line delay of the transcriber (Kevin's core: ~170 ms); used to re-align on restart")
    p.add_argument("--count-in-beats", type=int, default=4, help="beats counted in before a restart")
    p.add_argument("--linger", type=float, default=1.0, help="seconds to keep obeying commands after input ends")
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s", datefmt="%H:%M:%S")
    try:
        asyncio.run(run(a))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
