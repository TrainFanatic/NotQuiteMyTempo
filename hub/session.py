"""Session controller: owns one song session and its state machine.

    idle -> count_in -> playing <-> stopped -> count_in (Fletcher's restart) -> ... -> finished -> idle

Driven by `session.command` (src quest / console: `start {song_id, from_bar}`, `quit`;
src Fletcher: `stop`, `restart {from_bar, t0}`) and by the clock. It is the ONE publisher of
`session.state count_in`: at a song start it picks t0 (now + lead, laptop clock) itself; on a
restart it uses Fletcher's t0. Every count_in carries `song_id`, `t0`, `from_bar` and
`song_time_at_t0` (the tempo-map start of from_bar), so the bridge aligns its sheet and the
headset seeks its backing track from the same numbers. The bridge still publishes `playing` /
`stopped`. The song ends by the clock (song time past the last bar + `ring_s`, while not
stopped): `finished`, then `session.result` (aggregated from `judgment.note`) once the bridge
has flushed its last misses. `quit` publishes the result if none went out yet, then `idle`.

    python -m hub.session [--hub ws://localhost:8765] [--songs songs]

Songs: `songs/<song_id>/song.json` + `drums.drums.txt` + `drums.tempo.json` (e2e spec).
`Session` is pure (no I/O, no clock of its own): feed it messages and times, get back
(type, data) pairs to publish.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "rushingOrDragging"))  # Chris's modules import each other flat

from fletcher.contract import JUDGMENT_NOTE, SESSION_COMMAND, SESSION_STATE, now  # noqa: E402

log = logging.getLogger("session")

SRC = "session"
SESSION_RESULT = "session.result"
SESSION_REQUEST = "session.request"
SONGS_DIR = ROOT / "songs"
SHEET, TEMPO, META = "drums.drums.txt", "drums.tempo.json", "song.json"
HIT_GRADES = ("perfect", "good", "off")


# ------------------------------------------------------------------ songs ---

@dataclass
class Song:
    """One library song: meta (song.json) + Chris's tempo map. Song time 0 = bar 1 downbeat."""
    id: str
    dir: Path
    meta: dict
    tempo_map: dict | None

    @property
    def sheet(self) -> Path:
        return self.dir / SHEET

    @property
    def title(self) -> str:
        return str(self.meta.get("title") or self.id)

    @property
    def beats_per_bar(self) -> int:
        return int(self.meta.get("beats_per_bar") or 4)

    @property
    def bars(self) -> int:
        if self.tempo_map:
            return len(self.tempo_map["bars"])
        return int(self.meta.get("bars") or 1)

    def bpm_at(self, bar: int) -> float:
        if self.tempo_map:
            return float(self.tempo_map["bars"][min(max(bar, 1), self.bars) - 1]["bpm"])
        return float(self.meta.get("bpm") or 120.0)

    def bar_start(self, bar: int) -> float:
        """Song time (s) of bar `bar`'s downbeat; bars + 1 = the end of the last bar."""
        bar = min(max(bar, 1), self.bars + 1)
        if self.tempo_map:
            return float(self.tempo_map["end_s"] if bar == self.bars + 1
                         else self.tempo_map["bars"][bar - 1]["start_s"])
        return (bar - 1) * self.beats_per_bar * 60.0 / self.bpm_at(1)

    @property
    def end_s(self) -> float:
        if self.tempo_map:
            return float(self.tempo_map["end_s"])
        return float(self.meta.get("duration_s") or self.bar_start(self.bars + 1))


def load_song(songs_dir: Path | str, song_id: str) -> Song:
    """songs/<song_id>/ -> Song. Raises FileNotFoundError / ValueError on a bad folder."""
    d = Path(songs_dir) / song_id
    if not song_id or "/" in song_id or song_id.startswith(".") or not (d / SHEET).exists():
        raise FileNotFoundError(f"no song {song_id!r} in {songs_dir} (needs {SHEET})")
    meta = json.loads((d / META).read_text()) if (d / META).exists() else {"id": song_id}
    tmap = None
    if (d / TEMPO).exists():
        from songsterr import tempo_map as tm
        tmap = tm.load_tempo_map(d / TEMPO)
    return Song(song_id, d, meta, tmap)


# ------------------------------------------------------------------ the machine ---

class Session:
    """The session state machine. Methods take the laptop time `t` and return messages."""

    def __init__(self, songs_dir: Path | str = SONGS_DIR, lead_s: float = 1.0, ring_s: float = 1.5,
                 count_in_beats: int = 4, result_delay_s: float = 0.6, trend_ms: float = 20.0):
        self.songs_dir = Path(songs_dir)
        self.lead_s = lead_s            # on top of 5 beats + 1.5 s (the Quest prepares the song)
        self.ring_s = ring_s            # the last bar rings this long before `finished`
        self.count_in_beats = count_in_beats  # a restart without t0 starts this many beats later
        self.result_delay_s = result_delay_s  # bridge flushes its misses on `finished`
        self.trend_ms = trend_ms        # |mean error| of a bar above this = rushing / dragging
        self.state = "idle"
        self.song: Song | None = None
        self.last_state: dict = {"state": "idle"}
        self._reset_stats()

    def _reset_stats(self) -> None:
        self.t0 = 0.0
        self.song_time_at_t0 = 0.0
        self.from_bar = 1
        self.bar = 0
        self.count_ins = 0
        self.cutoffs = 0
        self.runs: list[list[int]] = []  # [from_bar, last bar heard] per run
        self.grades = {g: 0 for g in (*HIT_GRADES, "miss", "extra")}
        self.errors: list[float] = []
        self.bar_errors: dict[tuple[int, int], list[float]] = {}  # (run, bar) -> signed errors
        self.result_due: float | None = None
        self.result_sent = False
        self.completed = False

    # -- helpers -----------------------------------------------------------
    def _publish(self, state: str, **extra) -> tuple[str, dict]:
        data = {"state": state}
        if self.song:
            data.update(song_id=self.song.id, title=self.song.title, bars=self.song.bars,
                        beats_per_bar=self.song.beats_per_bar)
        data.update(extra)
        self.last_state = data
        return SESSION_STATE, data

    def song_time(self, t: float) -> float:
        return t - self.t0 + self.song_time_at_t0

    def _count_in(self, from_bar: int, t0: float, reason: str) -> tuple[str, dict]:
        song = self.song
        self.from_bar = min(max(1, from_bar), song.bars)
        self.t0, self.song_time_at_t0 = t0, song.bar_start(self.from_bar)
        self.bar, self.count_ins = self.from_bar, self.count_ins + 1
        self.runs.append([self.from_bar, self.from_bar - 1])
        self.state = "count_in"
        log.info("count_in (%s) %s from bar %d, t0 %.3f, song time %.3f", reason, song.id, self.from_bar,
                 t0, self.song_time_at_t0)
        return self._publish("count_in", bpm=round(song.bpm_at(self.from_bar), 3), bar=self.from_bar, beat=1,
                             from_bar=self.from_bar, t0=round(t0, 4),
                             song_time_at_t0=round(self.song_time_at_t0, 6), reason=reason)

    # -- inputs ------------------------------------------------------------
    def on_command(self, d: dict, src: str, t: float) -> list[tuple[str, dict]]:
        action = d.get("action")
        if action == "start":
            return self.start(str(d.get("song_id") or ""), int(d.get("from_bar") or 1), t)
        if action == "quit":  # also Fletcher's (src aaron): reason no_playing / cutoffs
            return self.quit(t, str(d.get("reason") or ""))
        if action == "stop" and self.state in ("count_in", "playing"):
            self.state = "stopped"
            self.cutoffs += 1
            log.info("stopped by %s at bar %d (cutoff %d)", src, self.bar, self.cutoffs)
            return []
        if action == "restart" and self.song and self.state in ("count_in", "playing", "stopped"):
            raw = d.get("t0")
            exact = isinstance(raw, (int, float)) and not isinstance(raw, bool)
            bar = int(d.get("from_bar") or self.from_bar)
            t0 = float(raw) if exact else t + self.count_in_beats * 60.0 / self.song.bpm_at(bar)
            return [self._count_in(bar, t0, "restart")]
        return []

    def start(self, song_id: str, from_bar: int, t: float) -> list[tuple[str, dict]]:
        try:
            song = load_song(self.songs_dir, song_id)
        except (OSError, ValueError) as e:
            log.warning("start refused: %s", e)
            return [(SESSION_STATE, {"state": self.state, "error": f"unknown song {song_id!r}",
                                     **({"song_id": self.song.id} if self.song else {})})]
        self.song = song
        self._reset_stats()
        bar = min(max(1, from_bar), song.bars)
        lead = 5 * 60.0 / song.bpm_at(bar) + 1.5 + self.lead_s
        return [self._count_in(bar, t + lead, "start")]

    def quit(self, t: float, reason: str = "") -> list[tuple[str, dict]]:
        out = []
        if self.song and not self.result_sent and self.count_ins:
            if reason:
                log.info("song ended by Fletcher: %s", reason)
            out.append(self._result(reason))  # runs[-1] already ends at the last bar heard
        self.state, self.song = "idle", None
        out.append(self._publish("idle"))
        return out

    def on_state(self, d: dict, src: str, t: float) -> list[tuple[str, dict]]:
        """The bridge's playing/stopped (bar numbers)."""
        if self.state in ("count_in", "playing") and d.get("state") == "playing":
            if self.state == "count_in":
                self.state = "playing"
            self._heard(int(d.get("bar") or 0))
        return []

    def on_note(self, d: dict, t: float) -> list[tuple[str, dict]]:
        if self.song is None or self.result_sent or not self.count_ins:
            return []
        grade = d.get("grade")
        if grade in self.grades:
            self.grades[grade] += 1
        err = d.get("error_ms")
        bar = int(d.get("bar") or 0)
        if grade in HIT_GRADES and isinstance(err, (int, float)):
            self.errors.append(float(err))
            self.bar_errors.setdefault((len(self.runs), bar), []).append(float(err))
        if grade != "extra" and self.state in ("count_in", "playing"):
            self._heard(bar)
        return []

    def _heard(self, bar: int) -> None:
        if self.runs and bar > self.runs[-1][1]:
            self.runs[-1][1] = min(bar, self.song.bars if self.song else bar)
        self.bar = max(self.bar, bar)

    def tick(self, t: float) -> list[tuple[str, dict]]:
        out: list[tuple[str, dict]] = []
        if self.song and self.state in ("count_in", "playing"):
            if self.state == "count_in" and t >= self.t0:
                self.state = "playing"
            if self.song_time(t) >= self.song.end_s + self.ring_s:
                self.state, self.completed = "finished", True
                self.runs[-1][1] = self.song.bars
                self.result_due = t + self.result_delay_s
                log.info("finished %s", self.song.id)
                out.append(self._publish("finished", bar=self.song.bars, beat=1))
        if self.result_due is not None and t >= self.result_due and not self.result_sent:
            out.append(self._result())
        return out

    def on_request(self) -> list[tuple[str, dict]]:
        """A client joined late (session.request): repeat the current state."""
        return [(SESSION_STATE, dict(self.last_state))]

    # -- the result --------------------------------------------------------
    def _result(self, reason: str = "") -> tuple[str, dict]:
        self.result_sent = True
        self.result_due = None
        g = self.grades
        notes = g["perfect"] + g["good"] + g["off"] + g["miss"]
        rushing, dragging = set(), set()
        for (_, bar), errs in self.bar_errors.items():
            if len(errs) < 2:
                continue
            mean = sum(errs) / len(errs)
            if mean <= -self.trend_ms:
                rushing.add(bar)
            elif mean >= self.trend_ms:
                dragging.add(bar)
        errs = self.errors
        data = {"song_id": self.song.id, "title": self.song.title, "completed": self.completed,
                "bars_played": sum(max(0, last - first + 1) for first, last in self.runs),
                "cutoffs": self.cutoffs, "notes": notes, **g,
                "accuracy": round((g["perfect"] + g["good"]) / notes, 3) if notes else None,
                "mean_error_ms": round(sum(errs) / len(errs), 1) if errs else None,
                "mean_abs_error_ms": round(sum(abs(e) for e in errs) / len(errs), 1) if errs else None,
                "rushing_bars": sorted(rushing), "dragging_bars": sorted(dragging)}
        if reason:
            data["ended_by"] = reason  # Fletcher ended it: no_playing (silence) / cutoffs (gave up)
        log.info("result: %s", data)
        return SESSION_RESULT, data


# ------------------------------------------------------------------ I/O ---

async def run(args) -> None:
    from fletcher.hub_client import HubClient

    ses = Session(args.songs, args.lead_s, args.ring_s, args.count_in_beats)
    hub = HubClient(args.hub, args.src)

    async def publish(msgs) -> None:
        for typ, data in msgs:
            await hub.send(typ, data)

    async def consume() -> None:
        async for msg in hub.messages():
            typ, d, src, rx = msg.get("type"), msg.get("data") or {}, str(msg.get("src", "")), msg.get("_rx", now())
            try:
                if typ == SESSION_COMMAND:
                    await publish(ses.on_command(d, src, rx))
                elif typ == SESSION_STATE:
                    await publish(ses.on_state(d, src, rx))
                elif typ == JUDGMENT_NOTE:
                    ses.on_note(d, rx)
                elif typ == SESSION_REQUEST:
                    await publish(ses.on_request())
            except Exception:
                log.exception("failed handling %s", typ)

    async def ticker() -> None:
        await hub.connected.wait()
        await publish([ses._publish("idle")])
        while True:
            await asyncio.sleep(0.05)
            await publish(ses.tick(now()))

    log.info("session controller: songs in %s, hub %s", ses.songs_dir, args.hub)
    await asyncio.gather(hub.run(), consume(), ticker())


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--hub", default="ws://localhost:8765")
    p.add_argument("--src", default=SRC)
    p.add_argument("--songs", default=str(SONGS_DIR), help="song library folder (songs/<song_id>/)")
    p.add_argument("--lead-s", type=float, default=1.0,
                   help="start: t0 = now + 5 beats + 1.5 s + this (the headset loads the song meanwhile)")
    p.add_argument("--ring-s", type=float, default=1.5, help="the last bar rings this long before 'finished'")
    p.add_argument("--count-in-beats", type=int, default=4, help="a restart without t0 starts this many beats later")
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s", datefmt="%H:%M:%S")
    try:
        asyncio.run(run(a))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
