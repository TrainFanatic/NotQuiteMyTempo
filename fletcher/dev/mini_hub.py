"""Stand-in hub for testing alone: broadcast every frame to every OTHER client
and log it to recordings/<time>.jsonl. Use the team's real hub when it runs.

    python -m fletcher.dev.mini_hub [--port 8765] [--record-dir recordings | --no-record]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import socket
import time
from pathlib import Path

from websockets.asyncio.server import broadcast, serve

log = logging.getLogger("mini_hub")


def free_port() -> int:
    """A TCP port nothing listens on right now (the OS picks it), for tests that start a hub."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# Not recorded unless asked: the Quest's mic.pcm audio is ~130 KB/s of base64.
UNRECORDED = ('"mic.pcm"',)


def _recorded(raw: str, skip: tuple[str, ...]) -> bool:
    head = raw[:96]  # the type comes first in every sender's envelope; no JSON parse per frame
    return not any(f'"type":{t}' in head.replace(" ", "") for t in skip)


async def run_hub(port: int = 8765, record_dir: str | None = "recordings", ready: asyncio.Event | None = None,
                  record_mic_pcm: bool = False) -> None:
    skip = () if record_mic_pcm else UNRECORDED
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
                    text = raw if isinstance(raw, str) else raw.decode()
                    if _recorded(text, skip):
                        fh.write(text + "\n")
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
    p.add_argument("--record-dir", default="recordings", help="where <time>.jsonl goes")
    p.add_argument("--record-mic-pcm", action="store_true", help="also record mic.pcm frames (~130 KB/s)")
    a = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s", datefmt="%H:%M:%S")
    try:
        asyncio.run(run_hub(a.port, None if a.no_record else a.record_dir, record_mic_pcm=a.record_mic_pcm))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
