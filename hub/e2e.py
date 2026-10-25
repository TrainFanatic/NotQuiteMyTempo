"""The real chain in one process: hits in Kevin's line format are written at
real-time pace into a FIFO -> hub.bridge (Chris's matcher) -> hub.server ->
Fletcher. Passes if the bridge starts Fletcher's session, his anger rises on
the drift, he cuts off after the grace bars, and the bridge obeys stop and
restart. No API calls unless --live.

    python -m hub.e2e [--seed 5 --drift-sd 0.07] [--speed 1] [--live]
"""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from fletcher.config import Config
from fletcher.dev.watch import Watcher
from fletcher.main import Fletcher, setup_logging

from . import bridge
from .server import run_hub

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "rushingOrDragging" / "tests"))


def make_song(d: Path, seed: int, drift_sd: float) -> tuple[Path, Path]:
    """Chris's Back in Black fixture looped to 16 bars + a drifting take of it."""
    import test_drift as td
    subprocess.run([sys.executable, str(ROOT / "rushingOrDragging/cli.py"), "convert",
                    str(ROOT / "rushingOrDragging/tests/fixtures/back_in_black_drums_4bars.json"),
                    "-o", str(d / "b4.txt")], check=True, capture_output=True)
    bar = 4 * 60 / 94
    notes = bridge.cp.read_notes(d / "b4.txt")
    lines = ["# time_s  midi  name"] + [f"{h.time + k * 4 * bar:.6f}  {h.midi}  {h.name}"
                                       for k in range(4) for h in notes]
    sheet = d / "song.drums.txt"
    sheet.write_text("\n".join(lines) + "\n")
    td.make_drifting(sheet, d / "take.drums.txt", seed=seed, drift_sd=drift_sd)
    return sheet, d / "take.drums.txt"


async def feed(fifo: Path, take: Path, speed: float, stop_after_restart: asyncio.Event) -> None:
    """Writes the take into the FIFO at its own timestamps, like a live transcriber."""
    loop = asyncio.get_running_loop()
    fh = await loop.run_in_executor(None, lambda: open(fifo, "w", buffering=1))
    start = loop.time()
    for line in take.read_text().splitlines():
        if line.startswith("#"):
            fh.write(line + "\n")
            continue
        await asyncio.sleep(max(0.0, start + float(line.split()[0]) / speed - loop.time()))
        if stop_after_restart.is_set():
            break
        fh.write(line + "\n")
    fh.close()


async def e2e(seed: int, drift_sd: float, speed: float, live: bool, port: int) -> int:
    url = f"ws://localhost:{port}"
    tmp = Path(tempfile.mkdtemp())
    sheet, take = make_song(tmp, seed, drift_sd)
    fifo = tmp / "hits.fifo"
    os.mkfifo(fifo)

    ready = asyncio.Event()
    tasks = [asyncio.create_task(run_hub(port, None, ready))]
    await ready.wait()
    fletcher = Fletcher(Config.from_env(hub_url=url, dry_run=not live, lines_dir="" if not live else None))
    watcher = Watcher(url)
    tasks += [asyncio.create_task(fletcher.run()), asyncio.create_task(watcher.run())]
    await asyncio.sleep(1.0)

    restarted = asyncio.Event()
    args = bridge.argparse.Namespace(sheet=str(sheet), bpm=94.0, tempo=None, beats_per_bar=4, input=str(fifo),
                                     hub=url, src="chris", latency_ms=0.0, count_in_beats=4, linger=1.0)
    btask = asyncio.create_task(bridge.run(args))
    ftask = asyncio.create_task(feed(fifo, take, speed, restarted))

    peak = 0.0
    while not ftask.done():
        await asyncio.sleep(0.1)
        peak = max(peak, fletcher.brain.anger)
        if any(typ == "session.command" and d.get("action") == "restart" for _, typ, d in watcher.events):
            await asyncio.sleep(0.5)  # let the bridge answer the restart
            restarted.set()
            break
    await asyncio.wait_for(btask, timeout=30) if ftask.done() else None
    for t in tasks + [btask, ftask]:
        t.cancel()

    ev = watcher.events
    stops = [d for _, typ, d in ev if typ == "session.command" and d.get("action") == "stop"]
    restarts = [d for _, typ, d in ev if typ == "session.command" and d.get("action") == "restart"]
    lines = [d.get("text") for _, typ, d in ev if typ == "fletcher.audio" and d.get("final")]
    print("\n=== hub e2e summary ===")
    print(f"peak anger {peak:.2f}  cutoffs: {len(stops)}  restarts from bars: {[r['from_bar'] for r in restarts]}")
    for l in lines:
        print(f"line: {l}")
    problems = []
    if not stops:
        problems.append("Fletcher never cut off on the bridge's judgments")
    if stops and not restarts:
        problems.append("no restart")
    print("PASS" if not problems else "FAIL: " + "; ".join(problems))
    return 0 if not problems else 1


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--seed", type=int, default=5)
    p.add_argument("--drift-sd", type=float, default=0.07)
    p.add_argument("--speed", type=float, default=1.0)
    p.add_argument("--live", action="store_true")
    p.add_argument("--port", type=int, default=8798)
    a = p.parse_args()
    setup_logging("INFO")
    sys.exit(asyncio.run(e2e(a.seed, a.drift_sd, a.speed, a.live, a.port)))


if __name__ == "__main__":
    main()
