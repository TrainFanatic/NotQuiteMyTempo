"""One-command smoke test of the whole loop, in one process, no API calls:
mini hub + mock referee + Fletcher (dry run) + watcher. Passes if Fletcher
cuts off, speaks a line that names the restart bar, and restarts.

    python -m fletcher.dev.e2e                 # contract stream
    python -m fletcher.dev.e2e --notes-only    # referee sends only notes
    python -m fletcher.dev.e2e --live          # real Claude + ElevenLabs (keys in .env)
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from ..config import Config
from ..main import Fletcher, setup_logging
from ..text import mentions_bar
from .mini_hub import run_hub
from .mock_referee import MockReferee
from .watch import Watcher


async def e2e(notes_only: bool, speed: float, live: bool, port: int) -> int:
    url = f"ws://localhost:{port}"
    ready = asyncio.Event()
    hub = asyncio.create_task(run_hub(port, None, ready))
    await ready.wait()
    cfg = Config.from_env(hub_url=url, dry_run=not live)
    fletcher = Fletcher(cfg)
    ftask = asyncio.create_task(fletcher.run())
    watcher = Watcher(url)
    wtask = asyncio.create_task(watcher.run())
    await asyncio.sleep(1.5)  # let everyone connect
    ref = MockReferee(url, speed=speed, notes_only=notes_only)
    try:
        await asyncio.wait_for(ref.run(), timeout=180)
    except asyncio.TimeoutError:
        print("FAIL: referee never finished")
    await asyncio.sleep(0.5)
    for t in (ftask, wtask, hub):
        t.cancel()

    ev = watcher.events
    stops = [d for _, typ, d in ev if typ == "session.command" and d.get("action") == "stop"]
    restarts = [d for _, typ, d in ev if typ == "session.command" and d.get("action") == "restart"]
    streams = [d for _, typ, d in ev if typ == "fletcher.speech" and d.get("stream")]
    finals = [d for _, typ, d in ev if typ == "fletcher.audio" and d.get("final")]
    barks = [d["clip_id"] for _, typ, d in ev if typ == "fletcher.speech" and d.get("clip_id")]
    moods = [d["mood"] for _, typ, d in ev if typ == "fletcher.state"]

    print("\n=== e2e summary ===")
    print(f"cutoffs: {len(stops)}  restarts from bars: {[r['from_bar'] for r in restarts]}")
    print(f"barks: {barks}")
    print(f"moods seen: {sorted(set(moods))}")
    for s, f in zip(streams, finals):
        print(f"line: {f.get('text') or s.get('text')}")
    lat = watcher.latencies_ms
    if lat:
        print(f"stop -> first audio: {', '.join(f'{x:.0f} ms' for x in lat)}")
    print(f"speculative draft used: {fletcher.stats['speculative_hits']}")

    problems = []
    if not stops:
        problems.append("no cutoff")
    if len(restarts) != len(stops):
        problems.append("a cutoff without a restart")
    if len(streams) < len(stops):
        problems.append("a cutoff without a spoken line")
    for r, f in zip(restarts, finals):
        if not mentions_bar(f.get("text", ""), r["from_bar"]):
            problems.append(f"line does not name bar {r['from_bar']}")
    if lat and max(lat) > 1500:
        problems.append(f"first audio too slow ({max(lat):.0f} ms > 1500)")
    print("PASS" if not problems else "FAIL: " + "; ".join(problems))
    return 0 if not problems else 1


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--notes-only", action="store_true")
    p.add_argument("--speed", type=float, default=2.0)
    p.add_argument("--live", action="store_true")
    p.add_argument("--port", type=int, default=8799)
    a = p.parse_args()
    setup_logging("INFO")
    sys.exit(asyncio.run(e2e(a.notes_only, a.speed, a.live, a.port)))


if __name__ == "__main__":
    main()
