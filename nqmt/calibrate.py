"""Round-trip latency calibration: speaker out + mic in, measured through the real chain.

    python -m nqmt.calibrate [--mic quest|laptop] [--hub ws://127.0.0.1:8765] [--n 8]

With `python -m nqmt` (and the headset in game mode) running, this asks the headset to play N
short claps on its Band bus at chosen laptop-clock times (`calibrate.start`, CONTRACT.md),
scheduled exactly like the backing track. Its own copy of Kevin's transcriber listens on the
same input the session uses (`--input hub` for the Quest mic, or the laptop mic;
`--clock monotonic`, the Quest mute filter on for the Quest mic), and each clap's first onset
is matched to its scheduled time. The median delay is how late a drummer playing exactly with
what they hear will appear to the bridge, so it is saved to calibration.json (repo root, one
entry per mic source) and published as `calibrate.result`: the running bridge applies it from
the next song on, and `python -m nqmt` passes the saved one to the bridge at startup.

Run it in a quiet room with nobody playing: any other onset near a clap spoils that clap.
"""

from __future__ import annotations

import argparse
import array
import base64
import asyncio
import json
import logging
import math
import signal
import statistics
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from fletcher.contract import now

from .launcher import LT, ROOT, mic_command

log = logging.getLogger("nqmt.calibrate")
CALIBRATION_FILE = ROOT / "calibration.json"
CALIBRATE_START, CALIBRATE_ACK, CALIBRATE_RESULT = "calibrate.start", "calibrate.ack", "calibrate.result"


def match(scheduled: list[float], onsets: list[float], early_s: float = 0.1, late_s: float = 0.8,
          tol_s: float = 0.012) -> list:
    """Per scheduled time, the delay of its clap's onset, else None. The delay is the one most
    claps agree on (within tol_s), so a stray onset (a cough, a door) doesn't move it; of
    equally supported delays the smallest wins, since reflections and ringing come after the
    direct sound. Each clap then takes its onset nearest that delay."""
    pairs = [[o - t for o in onsets if t - early_s <= o <= t + late_s] for t in scheduled]
    cands = sorted(d for ds in pairs for d in ds)
    if not cands:
        return [None] * len(scheduled)
    support = lambda c: sum(1 for ds in pairs if any(abs(d - c) <= tol_s for d in ds))
    best = max(cands, key=lambda c: (support(c), -c))
    out = []
    for ds in pairs:
        near = [d for d in ds if abs(d - best) <= 2 * tol_s]
        out.append(min(near, key=lambda d: abs(d - best)) if near else None)
    return out


def summarize(delays: list, n: int) -> dict:
    got = [d for d in delays if d is not None]
    res = {"n": n, "matched": len(got), "delays_ms": [None if d is None else round(d * 1000, 1) for d in delays]}
    if not got:
        return {**res, "ok": False, "error": "no clap was heard"}
    med = statistics.median(got)
    mad = statistics.median(abs(d - med) for d in got)
    res.update(latency_ms=round(med * 1000, 1), spread_ms=round((max(got) - min(got)) * 1000, 1),
               mad_ms=round(mad * 1000, 1), stdev_ms=round(statistics.pstdev(got) * 1000, 1))
    need = math.ceil(0.75 * n)
    if len(got) < need:
        res.update(ok=False, error=f"only {len(got)}/{n} claps heard (need {need}): louder, or closer to the mic?")
    elif mad > 0.015:
        res.update(ok=False, error="claps disagree by more than 15 ms (MAD): noisy room or echo?")
    else:
        res["ok"] = True
    return res


def load(path: Path = CALIBRATION_FILE) -> dict:
    try:
        d = json.loads(Path(path).read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save(mic: str, res: dict, path: Path = CALIBRATION_FILE) -> None:
    d = load(path)
    d[mic] = {k: res[k] for k in ("latency_ms", "spread_ms", "mad_ms", "matched", "n") if k in res}
    d[mic]["measured_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    Path(path).write_text(json.dumps(d, indent=2) + "\n")


def latency_ms(mic: str, path: Path = CALIBRATION_FILE) -> float | None:
    """The saved round trip for this mic source, or None."""
    v = (load(path).get(mic) or {}).get("latency_ms")
    return float(v) if isinstance(v, (int, float)) and math.isfinite(v) else None


class Transcriber:
    """Kevin's transcriber on the live input (same command line as the launcher's mic)."""

    def __init__(self, cmd: list[str]):
        self.onsets: list[float] = []
        self.ready = threading.Event()
        self.p = subprocess.Popen(cmd, cwd=LT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  start_new_session=True)
        self.err: list[str] = []
        threading.Thread(target=self._out, daemon=True).start()
        threading.Thread(target=self._errs, daemon=True).start()

    def _out(self) -> None:
        for line in self.p.stdout:
            if line.startswith("#"):
                self.ready.set()
                continue
            try:
                self.onsets.append(float(line.split()[0]))
            except (IndexError, ValueError):
                pass

    def _errs(self) -> None:
        for line in self.p.stderr:
            self.err.append(line.rstrip("\n"))

    def stop(self) -> dict:
        if self.p.poll() is None:
            self.p.send_signal(signal.SIGINT)
        try:
            self.p.wait(10)
        except subprocess.TimeoutExpired:
            self.p.kill()
            self.p.wait()
        for line in reversed(self.err):  # its JSON summary is the last stderr line
            try:
                return json.loads(line)
            except ValueError:
                continue
        return {}


async def calibrate(a) -> dict:
    from fletcher.hub_client import HubClient

    hub = HubClient(a.hub, "calibrate")
    hub_task = asyncio.create_task(hub.run())
    acks: list[dict] = []
    pcm = {"frames": 0, "first": None, "peak": 0}

    async def listen() -> None:
        async for msg in hub.messages():
            if msg.get("type") == "mic.pcm":
                pcm["frames"] += 1
                pcm["first"] = pcm["first"] or msg["_rx"]
                if pcm["frames"] % 5 == 1:  # the Quest's stuck mic sends exact zeros
                    try:
                        b = base64.b64decode((msg.get("data") or {}).get("pcm_b64", ""))
                        pcm["peak"] = max(pcm["peak"], max((abs(v) for v in array.array("h", b)), default=0))
                    except (ValueError, TypeError):
                        pass
            elif msg.get("type") == CALIBRATE_ACK:
                acks.append(msg.get("data") or {})

    listen_task = asyncio.create_task(listen())
    logdir = Path(a.record_dir) / time.strftime("calibrate-%Y%m%d-%H%M%S")
    logdir.mkdir(parents=True, exist_ok=True)
    cmd = mic_command(a.mic, a.hub, logdir, a.mic_latency_ms)
    if a.mic == "quest" and a.no_mute_filter:
        cmd += ["--mute-window-ms", "0"]
    tr = None
    try:
        try:
            await asyncio.wait_for(hub.connected.wait(), 5)
        except asyncio.TimeoutError:
            raise SystemExit(f"no hub at {a.hub}: start `python -m nqmt` first")
        tr = Transcriber(cmd)
        if not await asyncio.to_thread(tr.ready.wait, 20):
            raise SystemExit("transcriber did not start: " + " | ".join(tr.err[-5:]))
        if a.mic == "quest":  # the headset's mic must be streaming before the claps are scheduled
            end = time.monotonic() + a.wait_mic_s
            while time.monotonic() < end and not (pcm["first"] and now() - pcm["first"] > 1.0):
                await asyncio.sleep(0.1)
            if not pcm["first"]:
                raise SystemExit("no mic.pcm from the headset: is it in game mode with Settings > mic QUEST?")
            if pcm["peak"] == 0:
                log.warning("the headset's mic.pcm is all zeros so far: mic stuck (reboot the Quest) or permission missing")
        await asyncio.sleep(a.settle_s)  # the transcriber's first blocks, and a noise baseline
        quiet_from = now()
        await asyncio.sleep(1.0)
        noise = sum(1 for o in tr.onsets if quiet_from <= o)
        t_first = now() + a.lead_s
        times = [round(t_first + k * a.interval_s, 4) for k in range(a.n)]
        cid = uuid.uuid4().hex[:8]
        await hub.send(CALIBRATE_START, {"id": cid, "times": times, "gain_db": a.gain_db,
                                         "interval_s": a.interval_s})
        log.info("calibrate %s: %d claps on the %s mic from t=%.3f, every %.2f s", cid, a.n, a.mic,
                 times[0], a.interval_s)
        await asyncio.sleep(1.5)
        ack = next((x for x in acks if x.get("id") == cid), None)
        if ack is None:
            log.warning("no calibrate.ack from the headset yet (old build? not in game mode?)")
        elif ack.get("error"):
            raise SystemExit(f"headset refused: {ack['error']}")
        else:
            log.info("headset ack: lead %.3f s, output latency %.1f ms", ack.get("lead_s", 0),
                     1000 * (ack.get("output_latency_s") or 0))
        await asyncio.sleep(max(0.0, times[-1] + a.tail_s - now()))
        stats = tr.stop()
        delays = match(times, tr.onsets)
        res = summarize(delays, a.n)
        if a.mic == "quest":
            res["mic_pcm_peak"] = pcm["peak"]
            if pcm["peak"] == 0:
                res.update(ok=False, error="the Quest mic sent only zeros (stuck mic: reboot the headset; or RECORD_AUDIO not granted)")
        res.update(id=cid, mic=a.mic, noise_onsets_per_s=noise, onsets=len(tr.onsets),
                   mute_filter=a.mic == "quest" and not a.no_mute_filter,
                   transcriber={k: stats.get(k) for k in ("notes", "quest_mute_dropped", "hit_to_line_ms_p50")})
        if ack:
            res["headset_output_latency_ms"] = round(1000 * (ack.get("output_latency_s") or 0), 1)
        (logdir / "result.json").write_text(json.dumps({**res, "scheduled": times, "all_onsets": tr.onsets}, indent=1))
        if res["ok"] and not a.no_save:
            save(a.mic, res, Path(a.out))
            res["saved"] = str(a.out)
        await hub.send(CALIBRATE_RESULT, {k: v for k, v in res.items() if k != "transcriber"})
        await asyncio.sleep(0.2)
        return res
    finally:
        if tr is not None and tr.p.poll() is None:
            tr.stop()
        listen_task.cancel()
        hub_task.cancel()
        await asyncio.gather(listen_task, hub_task, return_exceptions=True)


def parse_args(argv=None):
    p = argparse.ArgumentParser(prog="python -m nqmt.calibrate", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--mic", choices=("quest", "laptop"), default="quest")
    p.add_argument("--hub", default="ws://127.0.0.1:8765")
    p.add_argument("--n", type=int, default=8, help="claps")
    p.add_argument("--interval-s", type=float, default=1.0, help="between claps")
    p.add_argument("--lead-s", type=float, default=1.5, help="first clap this long after the request")
    p.add_argument("--tail-s", type=float, default=1.2, help="keep listening after the last clap")
    p.add_argument("--settle-s", type=float, default=1.0)
    p.add_argument("--wait-mic-s", type=float, default=8.0, help="quest: wait this long for mic.pcm")
    p.add_argument("--gain-db", type=float, default=0.0, help="clap level on the Band bus")
    p.add_argument("--mic-latency-ms", type=float, default=None, help="laptop mic: as python -m nqmt's")
    p.add_argument("--no-mute-filter", action="store_true", help="quest: turn Kevin's mute filter off")
    p.add_argument("--out", default=str(CALIBRATION_FILE))
    p.add_argument("--no-save", action="store_true", help="measure and publish only")
    p.add_argument("--record-dir", default=str(ROOT / "recordings"), help="calibrate-<time>/ logs go here")
    return p.parse_args(argv)


def main(argv=None) -> None:
    a = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s", datefmt="%H:%M:%S")
    res = asyncio.run(calibrate(a))
    print(json.dumps(res, indent=1))
    if res["ok"]:
        print(f"\nround trip ({a.mic} mic): {res['latency_ms']:.1f} ms median, spread {res['spread_ms']:.1f} ms, "
              f"{res['matched']}/{res['n']} claps" + (f"; saved to {res['saved']}" if res.get("saved") else ""))
    else:
        print(f"\ncalibration FAILED: {res.get('error')}", file=sys.stderr)
    sys.exit(0 if res["ok"] else 1)


if __name__ == "__main__":
    main()
