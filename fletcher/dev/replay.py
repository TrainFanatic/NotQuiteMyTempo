"""Replay a recorded session (recordings/*.jsonl) into the hub at real speed,
so Fletcher can be tuned on real drumming with nobody at the kit.

    python -m fletcher.dev.replay recordings/20261024-1530.jsonl [--speed 2]

By default only the referee's side is replayed (session.state + judgment.*);
Fletcher's own old messages are skipped so the live Fletcher answers instead.
Note: a replay can't obey Fletcher's stop/restart; it just keeps playing.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging

from websockets.asyncio.client import connect

from ..contract import now, parse

log = logging.getLogger("replay")
DEFAULT_TYPES = ("session.state", "judgment.note", "judgment.bar", "judgment.alert",
                 # aliases a drifted referee might use; adapt.py maps them
                 "note", "bar", "alert", "state")


async def replay(path: str, url: str, speed: float, types: tuple[str, ...]) -> None:
    msgs = []
    with open(path) as fh:
        for line in fh:
            m = parse(line)
            if m and m["type"] in types:
                msgs.append(m)
    if not msgs:
        raise SystemExit(f"no {types} messages in {path}")
    ts = [m.get("t") for m in msgs]
    use_t = all(isinstance(t, (int, float)) for t in ts)
    log.info("%d messages, %.1f s of session", len(msgs), (ts[-1] - ts[0]) if use_t else 0)
    async with connect(url, max_size=None) as ws:
        start, base = now(), (ts[0] if use_t else 0.0)
        for i, m in enumerate(msgs):
            if use_t:
                await asyncio.sleep(max(0.0, start + (m["t"] - base) / speed - now()))
            else:
                await asyncio.sleep(0.05 / speed)
            await ws.send(json.dumps(m))
            if m["type"] in ("judgment.alert", "alert"):
                log.info("alert %s", m["data"])
    log.info("replay finished")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("file")
    p.add_argument("--hub", default="ws://localhost:8765")
    p.add_argument("--speed", type=float, default=1.0)
    p.add_argument("--types", help="comma-separated message types to replay")
    a = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s", datefmt="%H:%M:%S")
    types = tuple(a.types.split(",")) if a.types else DEFAULT_TYPES
    asyncio.run(replay(a.file, a.hub, a.speed, types))


if __name__ == "__main__":
    main()
