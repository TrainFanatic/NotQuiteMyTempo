"""Stand-in hub for testing alone: broadcast every frame to every OTHER client
and log it to recordings/<time>.jsonl. Use the team's real hub when it runs.

    python -m fletcher.dev.mini_hub [--port 8765]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import time
from pathlib import Path

from websockets.asyncio.server import broadcast, serve

log = logging.getLogger("mini_hub")


async def run_hub(port: int = 8765, record_dir: str | None = "recordings", ready: asyncio.Event | None = None) -> None:
    clients = set()
    fh = None
    if record_dir:
        Path(record_dir).mkdir(exist_ok=True)
        path = Path(record_dir) / time.strftime("%Y%m%d-%H%M%S.jsonl")
        fh = path.open("a", buffering=1)
        log.info("recording to %s", path)

    async def handler(ws):
        clients.add(ws)
        log.info("client joined (%d connected)", len(clients))
        try:
            async for raw in ws:
                if fh:
                    fh.write((raw if isinstance(raw, str) else raw.decode()) + "\n")
                broadcast([c for c in clients if c is not ws], raw)
        finally:
            clients.discard(ws)
            log.info("client left (%d connected)", len(clients))

    async with serve(handler, "0.0.0.0", port, max_size=None):
        log.info("hub listening on ws://localhost:%d", port)
        if ready:
            ready.set()
        await asyncio.Future()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--no-record", action="store_true")
    a = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s", datefmt="%H:%M:%S")
    try:
        asyncio.run(run_hub(a.port, None if a.no_record else "recordings"))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
