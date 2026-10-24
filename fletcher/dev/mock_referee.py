"""Fake Chris + fake drummer, for testing Fletcher with nobody at the kit.

Plays a 16-bar 4/4 groove at 100 bpm and publishes session.state,
judgment.note, judgment.bar and judgment.alert like the contract says.
It obeys session.command stop/restart, so the whole cutoff loop runs.

Run 1 the drummer starts rushing in bar 6, run 2 drags two bars after the
restart, run 3 plays clean (Fletcher should nod).

    python -m fletcher.dev.mock_referee                # full contract stream
    python -m fletcher.dev.mock_referee --notes-only   # only notes + state
    python -m fletcher.dev.mock_referee --speed 3      # 3x faster
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import random

from websockets.asyncio.client import connect

from ..contract import envelope, now, parse

log = logging.getLogger("mock_referee")

GROOVE = ([(b + 0.5 * i, "hihat_closed") for b in range(1, 5) for i in range(2)]
          + [(1.0, "kick"), (3.0, "kick"), (2.0, "snare"), (4.0, "snare")])
GROOVE = sorted({(round(b, 2), d) for b, d in GROOVE})


class MockReferee:
    def __init__(self, url: str, bpm: float = 100, bars: int = 16, speed: float = 1.0,
                 notes_only: bool = False, runs: int = 3, seed: int = 7):
        self.url, self.bpm, self.bars, self.speed = url, bpm, bars, speed
        self.notes_only, self.max_runs = notes_only, runs
        self.rng = random.Random(seed)
        self.seq = 0
        self.ws = None
        self.restart_count = 0
        self.stop_flag = asyncio.Event()
        self.restart_from: int | None = None
        self.got_restart = asyncio.Event()
        self.finished = asyncio.Event()

    async def send(self, type_: str, data: dict, t: float | None = None) -> None:
        self.seq += 1
        await self.ws.send(json.dumps(envelope(type_, "chris", self.seq, data, t)))

    def beat_s(self) -> float:
        return 60.0 / self.bpm / self.speed

    def drummer_error(self, run: int, start_bar: int, bar: int) -> float:
        jitter = self.rng.gauss(0, 6)
        if run == 0 and bar >= 6:
            return jitter - 9 * (bar - 5)  # rushing: earlier every bar
        if run == 1 and bar >= start_bar + 2:
            return jitter + 9 * (bar - start_bar - 1)  # dragging
        return jitter

    async def listen(self) -> None:
        async for raw in self.ws:
            msg = parse(raw)
            if not msg or msg["type"] != "session.command":
                continue
            d = msg["data"]
            log.info("<- session.command %s", d)
            if d.get("action") == "stop":
                self.stop_flag.set()
            elif d.get("action") == "restart":
                self.restart_from = int(d.get("from_bar") or 1)
                self.got_restart.set()

    async def state(self, state: str, bar: int, t0: float) -> None:
        await self.send("session.state", {"state": state, "song_id": "groove_100", "bpm": self.bpm, "t0": t0,
                                          "bar": bar, "beat": 1, "restart_count": self.restart_count})

    async def play_run(self, run: int, start_bar: int) -> bool:
        """Returns True if stopped by Fletcher."""
        bs = self.beat_s()
        t_count = now()
        await self.state("count_in", start_bar, t_count + 4 * bs)
        await asyncio.sleep(4 * bs)
        t0 = now() - (start_bar - 1) * 4 * bs  # t0 = virtual time of bar 1 beat 1
        self.stop_flag.clear()
        bad_since: int | None = None
        for bar in range(start_bar, self.bars + 1):
            bar_t = t0 + (bar - 1) * 4 * bs
            await self.state("playing", bar, t0)
            errs = []
            for beat, drum in GROOVE:
                expected = bar_t + (beat - 1) * bs
                err = self.drummer_error(run, start_bar, bar)
                actual = expected + err / 1000.0 / self.speed
                await asyncio.sleep(max(0.0, actual - now()))
                if self.stop_flag.is_set():
                    await self.state("stopped", bar, t0)
                    return True
                miss = self.rng.random() < 0.01
                a = abs(err)
                grade = "miss" if miss else "perfect" if a <= 15 else "good" if a <= 35 else "off" if a <= 80 else "miss"
                data = {"drum": drum, "bar": bar, "beat": beat, "error_ms": None if miss else round(err, 1), "grade": grade}
                await self.send("judgment.note", data, t=actual)
                if not miss:
                    errs.append(err)
            await asyncio.sleep(max(0.0, bar_t + 4 * bs - now()))
            if self.stop_flag.is_set():
                await self.state("stopped", bar, t0)
                return True
            if not self.notes_only and errs:
                mean = sum(errs) / len(errs)
                trend = "rushing" if mean < -20 else "dragging" if mean > 20 else "steady"
                ratio = 1.0 - mean / 1000.0  # rough; the brain also reads mean_error_ms
                acc = sum(1 for e in errs if abs(e) <= 35) / len(GROOVE)
                await self.send("judgment.bar", {"bar": bar, "accuracy": round(acc, 2), "mean_error_ms": round(mean, 1),
                                                 "tempo_ratio": round(ratio, 3), "trend": trend,
                                                 "misses": len(GROOVE) - len(errs), "extras": 0})
                if trend == "steady":
                    bad_since = None
                else:
                    bad_since = bad_since or bar
                    sev = min(1.0, 0.3 + (abs(mean) - 20) / 30)
                    await self.send("judgment.alert", {"kind": trend, "severity": round(sev, 2), "since_bar": bad_since,
                                                       "detail": f"bar {bar} mean {abs(mean):.0f} ms {'early' if mean < 0 else 'late'}"})
        return False

    async def run(self) -> None:
        async with connect(self.url) as ws:
            self.ws = ws
            listener = asyncio.create_task(self.listen())
            await self.state("idle", 1, 0.0)
            await asyncio.sleep(0.5)
            run, start = 0, 1
            while run < self.max_runs:
                log.info("run %d from bar %d", run + 1, start)
                stopped = await self.play_run(run, start)
                if not stopped:
                    log.info("song finished cleanly")
                    break
                self.got_restart.clear()
                try:
                    await asyncio.wait_for(self.got_restart.wait(), timeout=20)
                except asyncio.TimeoutError:
                    log.warning("no restart command within 20 s")
                    break
                self.restart_count += 1
                run, start = run + 1, self.restart_from or 1
            await self.state("idle", 1, 0.0)
            await asyncio.sleep(0.3)
            listener.cancel()
            self.finished.set()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--hub", default="ws://localhost:8765")
    p.add_argument("--speed", type=float, default=1.0)
    p.add_argument("--notes-only", action="store_true")
    a = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s", datefmt="%H:%M:%S")
    asyncio.run(MockReferee(a.hub, speed=a.speed, notes_only=a.notes_only).run())


if __name__ == "__main__":
    main()
