"""A stand-in headset for testing calibration with no Quest: streams `mic.pcm` (CONTRACT.md) like
quest_app/scripts/game/mic_streamer.gd, from a device clock offset from the laptop's, and answers
`calibrate.start` like scripts/game/calibration_player.gd, except that each clap "arrives at the
mic" exactly --delay-ms after its scheduled laptop time. A calibration against it must report
--delay-ms (plus the transcriber's own onset bias).

    python -m nqmt.sim_headset [--hub ws://127.0.0.1:8765] [--delay-ms 180] [--jitter-ms 2]
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import logging
import random

import numpy as np

from fletcher.contract import now

RATE, FRAME = 48000, 2400  # 50 ms frames
log = logging.getLogger("nqmt.sim_headset")


def clap(rng: np.random.Generator, n: int = 2880) -> np.ndarray:
    """The headset's clap (calibration_player.gd): a noise burst with an 8 ms decay."""
    return rng.uniform(-1, 1, n) * np.exp(-np.arange(n) / (0.008 * RATE)) * 0.5


async def run(a) -> None:
    from fletcher.hub_client import HubClient

    hub = HubClient(a.hub, "quest")
    task = asyncio.create_task(hub.run())
    await hub.connected.wait()
    rng = np.random.default_rng(a.seed)
    offset = a.clock_offset  # device clock = laptop clock + offset
    start_local = now() + offset
    pending: list = []  # (device time a clap reaches the mic, its samples)

    async def listen() -> None:
        async for msg in hub.messages():
            if msg.get("type") == "calibrate.start":
                d = msg.get("data") or {}
                times = [float(t) for t in d.get("times", [])]
                for t in times:
                    pending.append((t + offset + (a.delay_ms + random.gauss(0, a.jitter_ms)) / 1000.0, clap(rng)))
                await hub.send("calibrate.ack", {"id": d.get("id", ""), "n": len(times),
                                                 "lead_s": round(times[0] - now(), 3) if times else 0,
                                                 "output_latency_s": 0.0, "clock_offset": offset},
                               t=now() + offset)
                log.info("calibrate.start: %d claps, arriving %.0f ms late", len(times), a.delay_ms)

    lt = asyncio.create_task(listen())
    seq, first = 0, 0
    try:
        while True:
            frame_end_local = start_local + (first + FRAME) / RATE
            await asyncio.sleep(max(0.0, frame_end_local - (now() + offset)))
            x = rng.normal(0, 10 ** (a.noise_db / 20), FRAME)
            for item in list(pending):
                p, c = item
                s0 = int(round((p - start_local) * RATE)) - first  # clap start, in this frame's samples
                lo, hi = max(0, s0), min(FRAME, s0 + len(c))
                if hi > lo:
                    x[lo:hi] += c[lo - s0:hi - s0]
                if s0 + len(c) <= FRAME:
                    pending.remove(item)
            pcm = (np.clip(x, -1, 1) * 32767).astype("<i2").tobytes()
            seq += 1
            await hub.send("mic.pcm", {"seq": seq, "rate": RATE, "channels": 1, "first_sample": first,
                                       "local_t": start_local + first / RATE, "clock_offset": offset,
                                       "pcm_b64": base64.b64encode(pcm).decode()}, t=now() + offset)
            first += FRAME
    finally:
        lt.cancel()
        task.cancel()


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="python -m nqmt.sim_headset", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--hub", default="ws://127.0.0.1:8765")
    p.add_argument("--delay-ms", type=float, default=180.0, help="speaker out + mic in, simulated")
    p.add_argument("--jitter-ms", type=float, default=2.0)
    p.add_argument("--clock-offset", type=float, default=1234.5, help="device clock - laptop clock (s)")
    p.add_argument("--noise-db", type=float, default=-60.0)
    p.add_argument("--seed", type=int, default=1)
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s", datefmt="%H:%M:%S")
    try:
        asyncio.run(run(a))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
