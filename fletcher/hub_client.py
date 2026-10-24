"""WebSocket client for the team hub. Reconnects every second forever, so
Fletcher keeps running when the hub restarts (a contract guardrail)."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncIterator

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from .contract import envelope, now, parse

log = logging.getLogger("fletcher.hub")


class HubClient:
    def __init__(self, url: str, src: str):
        self.url = url
        self.src = src
        self.seq = 0
        self.ws = None
        self.inbox: asyncio.Queue[dict] = asyncio.Queue(maxsize=5000)
        self.connected = asyncio.Event()
        self._warned = False

    async def run(self) -> None:
        while True:
            try:
                async with connect(self.url, max_size=None, open_timeout=3) as ws:
                    self.ws = ws
                    self.connected.set()
                    self._warned = False
                    log.info("connected to hub %s", self.url)
                    async for raw in ws:
                        msg = parse(raw)
                        if msg is None or msg.get("src") == self.src:
                            continue  # ignore junk and our own echoes
                        msg["_rx"] = now()
                        if self.inbox.full():
                            self.inbox.get_nowait()  # drop oldest rather than block
                        self.inbox.put_nowait(msg)
            except (OSError, ConnectionClosed, asyncio.TimeoutError, TimeoutError) as e:
                if not self._warned:
                    log.warning("hub unavailable (%s); retrying every 1 s", e.__class__.__name__)
                    self._warned = True
            except Exception as e:  # never die because of the hub
                log.warning("hub error: %r; retrying", e)
            finally:
                self.ws = None
                self.connected.clear()
            await asyncio.sleep(1.0)

    async def send(self, type_: str, data: dict[str, Any], t: float | None = None) -> bool:
        self.seq += 1
        msg = envelope(type_, self.src, self.seq, data, t)
        ws = self.ws
        if ws is None:
            log.debug("hub down, dropped %s", type_)
            return False
        try:
            await ws.send(json.dumps(msg, separators=(",", ":")))
            return True
        except ConnectionClosed:
            return False

    async def messages(self) -> AsyncIterator[dict]:
        while True:
            yield await self.inbox.get()
