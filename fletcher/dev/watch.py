"""Stand-in for Adam's headset: prints what Fletcher does and measures the
cutoff -> first-audio latency. --play also plays him on this laptop.

    python -m fletcher.dev.watch [--play]
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import logging
from collections import defaultdict

from websockets.asyncio.client import connect

from ..barks import BarkPicker
from ..contract import now, parse

log = logging.getLogger("watch")


class Watcher:
    def __init__(self, url: str, play: bool = False, sample_rate: int = 24000):
        self.url = url
        self.events: list[tuple[float, str, dict]] = []
        self.audio_bytes: dict[str, int] = defaultdict(int)
        self.last_stop: float | None = None
        self.latencies_ms: list[float] = []  # stop -> first sound (bark clip or streamed line)
        self.line_latencies_ms: list[float] = []  # stop -> first audio of the generated line
        self.heard_since_stop = True
        self.player = None
        self.barks = BarkPicker()
        if play:
            from ..local_audio import LocalPlayer
            self.player = LocalPlayer(sample_rate)

    async def run(self) -> None:
        async with connect(self.url, max_size=None) as ws:
            log.info("watching %s", self.url)
            async for raw in ws:
                msg = parse(raw)
                if not msg:
                    continue
                typ, d = msg["type"], msg["data"]
                if not (typ.startswith("fletcher.") or typ == "session.command"):
                    continue
                self.events.append((now(), typ, d))
                if typ == "session.command":
                    log.info("COMMAND %s from bar %s", d.get("action"), d.get("from_bar"))
                    if d.get("action") == "stop":
                        self.last_stop = now()
                        self.heard_since_stop = False
                elif typ == "fletcher.state":
                    log.info("state  %-9s anger %.2f  %s", d.get("mood"), d.get("anger", 0), d.get("action"))
                elif typ == "fletcher.speech":
                    kind = f"clip {d['clip_id']}" if d.get("clip_id") else "stream"
                    log.info("SPEECH %s %s: %s", d.get("utterance_id"), kind, d.get("text"))
                    if d.get("clip_id"):
                        self._first_sound("bark")
                    if self.player and d.get("clip_id"):
                        self.player.play_wav(self.barks.path(d["clip_id"]))
                elif typ == "fletcher.audio":
                    uid = d.get("utterance_id")
                    pcm = base64.b64decode(d.get("pcm_b64") or "")
                    if self.audio_bytes[uid] == 0 and pcm and self.last_stop is not None:
                        ms = (now() - self.last_stop) * 1000
                        self.line_latencies_ms.append(ms)
                        log.info("line audio %.0f ms after the stop command", ms)
                        self._first_sound("line")
                    self.audio_bytes[uid] += len(pcm)
                    if self.player:
                        self.player.play_pcm(pcm)
                    if d.get("final"):
                        log.info("audio %s done: %.1f s", uid, self.audio_bytes[uid] / 2 / 24000)

    def _first_sound(self, what: str) -> None:
        if self.heard_since_stop or self.last_stop is None:
            return
        self.heard_since_stop = True
        ms = (now() - self.last_stop) * 1000
        self.latencies_ms.append(ms)
        log.info("first audio (%s) %.0f ms after the stop command", what, ms)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--hub", default="ws://localhost:8765")
    p.add_argument("--play", action="store_true")
    a = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s", datefmt="%H:%M:%S")
    try:
        asyncio.run(Watcher(a.hub, a.play).run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
