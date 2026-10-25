"""`--mic file:<wav>[,<wav>...]`: a recorded take stands in for the drummer, in sync with the session.

On every `session.state count_in` from the controller, the take is played from the count_in's
bar: Kevin's transcriber (`transcribe_wav.py --onsets-only --realtime`, his uv env) runs on the
take from just before `song_time_at_t0`, padded with silence so it reaches that song time at
`t0`, and every line it prints is re-stamped onto the laptop clock with the session's numbers:
laptop t = t0 + (song time - song_time_at_t0). So the bridge (`--clock laptop`) judges exactly
the timing that was recorded, however long the transcriber takes to start. A cutoff (`stop`),
`finished` or `idle` stops the take, as a drummer stops when Terence's fist comes down.

Several takes: the n-th count_in of a session plays the n-th file (the last one repeats), e.g. a
sloppy take that gets cut off, then a clean one for after the tirade. The WAVs are prepared
48 kHz mono (Kevin's prepare_recording.py), and `--file-origin` is the song time of sample 0
(negative = the take starts before bar 1, like his `--song-origin`).
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import numpy as np

from fletcher.contract import SESSION_COMMAND, SESSION_STATE, now

log = logging.getLogger("nqmt.file_mic")
ROOT = Path(__file__).resolve().parent.parent
LT = ROOT / "live_transcription"
RATE = 48000
PRE_ROLL_S = 0.25  # of the take before the count_in's bar (a pickup note just before the downbeat)


class FileDrummer:
    def __init__(self, takes: list[Path], origin: float, out, logdir: Path, session_src: str = "session",
                 startup_s: float = 0.3, quest_take: bool = False):
        self.takes = [Path(t).resolve() for t in takes]
        self.origin, self.out, self.logdir = origin, out, Path(logdir)
        self.session_src = session_src
        # Kevin's Quest mute filter only for takes the Quest recorded: on a clean take it drops real
        # hits (a closed hat decays 35 dB in < 200 ms, which the filter reads as the mic muting).
        self.quest_take = quest_take
        self.startup_s = startup_s  # transcriber launch -> replay start, learned per launch
        self.lock = threading.Lock()
        self.proc: subprocess.Popen | None = None
        self.gen = 0          # bumps on every new take / stop: old output is ignored
        self.count_ins = 0
        self.launches = 0
        self._audio: dict[Path, np.ndarray] = {}
        self.write("# nqmt file mic: laptop clock (time.monotonic() seconds)\n# time_s  midi  name\n")

    # -- hub ---------------------------------------------------------------
    def on_message(self, msg: dict) -> None:
        typ, d = msg.get("type"), msg.get("data") or {}
        if typ == SESSION_STATE and msg.get("src") == self.session_src:
            st = d.get("state")
            if st == "count_in" and isinstance(d.get("t0"), (int, float)):
                self.count_ins = 0 if d.get("reason") == "start" else self.count_ins + 1
                take = self.takes[min(self.count_ins, len(self.takes) - 1)]
                self.play(take, float(d["t0"]), float(d.get("song_time_at_t0") or 0.0))
            elif st in ("finished", "idle"):
                self.stop(st)
        elif typ == SESSION_COMMAND and d.get("action") == "stop":
            self.stop("cutoff")

    # -- takes -------------------------------------------------------------
    def audio(self, take: Path) -> np.ndarray:
        if take not in self._audio:
            import soundfile as sf
            x, rate = sf.read(str(take), dtype="float32", always_2d=True)
            if rate != RATE:
                raise ValueError(f"{take}: {rate} Hz; prepare it at 48 kHz mono first")
            self._audio[take] = x.mean(axis=1).astype(np.float32)
        return self._audio[take]

    def play(self, take: Path, t0: float, song_t0: float) -> None:
        import soundfile as sf
        self.stop("new take")
        x = self.audio(take)
        start_song = max(self.origin, song_t0 - PRE_ROLL_S)
        i0 = int(round((start_song - self.origin) * RATE))
        if i0 >= len(x):
            log.warning("take %s ends before song time %.2f; nothing to play", take.name, song_t0)
            return
        # Replay starts ~startup_s after launch; pad so song time start_song plays at its laptop time.
        want = t0 - (song_t0 - start_song)
        pad_s = max(0.0, want - (now() + self.startup_s))
        seg = np.concatenate([np.zeros(int(round(pad_s * RATE)), np.float32), x[i0:]])
        self.launches += 1
        n = self.launches
        wav = self.logdir / f"file_mic_{n:02d}.wav"
        sf.write(str(wav), seg, RATE, subtype="FLOAT")
        uv = shutil.which("uv")
        cmd = ([uv, "run", "python"] if uv else [sys.executable]) + [
            "tools/transcribe_wav.py", str(wav), "--onsets-only", "--realtime",
            "--song-origin", f"{start_song - pad_s:.6f}", "--output", str(self.logdir / f"file_mic_{n:02d}.drums.txt")]
        if not self.quest_take:
            cmd += ["--mute-window-ms", "0"]
        errlog = (self.logdir / f"file_mic_{n:02d}.log").open("w")
        launched = now()
        proc = subprocess.Popen(cmd, cwd=LT, stdout=subprocess.PIPE, stderr=errlog, text=True,
                                env={**os.environ, "PYTHONUNBUFFERED": "1"})
        with self.lock:
            self.gen += 1
            self.proc, gen = proc, self.gen
        log.info("take %d: %s from song time %.2f (bar at %.2f s), t0 in %.2f s, %.2f s of silence first",
                 n, take.name, start_song, song_t0, t0 - now(), pad_s)
        threading.Thread(target=self._pump, args=(proc, gen, t0, song_t0, launched, pad_s, want, errlog),
                         daemon=True).start()

    def _pump(self, proc, gen, t0, song_t0, launched, pad_s, want, errlog) -> None:
        started = None
        for line in proc.stdout:
            s = line.strip()
            if not s or s.startswith("#"):
                if started is None and "time_s" in s:  # header printed: the replay clock starts now
                    started = now()
                    took = started - launched
                    self.startup_s = took
                    # lines are stamped by the plan, so this only shifts when they arrive
                    log.info("transcriber replay started %.2f s after launch (audio %+.0f ms vs plan)",
                             took, (started + pad_s - want) * 1000)
                continue
            try:
                fields = s.split()
                song_t, rest = float(fields[0]), fields[1:]
            except (IndexError, ValueError):
                continue
            with self.lock:
                if gen != self.gen:
                    break
            self.write(f"{t0 + song_t - song_t0:.6f}  {'  '.join(rest)}\n")
        proc.wait()
        errlog.close()

    def stop(self, why: str) -> None:
        with self.lock:
            proc, self.proc = self.proc, None
            self.gen += 1
        if proc and proc.poll() is None:
            log.info("take stopped (%s)", why)
            proc.terminate()
            try:
                proc.wait(3)
            except subprocess.TimeoutExpired:
                proc.kill()

    def write(self, text: str) -> None:
        try:
            with self.lock:
                self.out.write(text)
                self.out.flush()
        except (BrokenPipeError, ValueError, OSError):
            pass


async def run(drummer: FileDrummer, hub_url: str, src: str = "file_mic") -> None:
    from fletcher.hub_client import HubClient
    import asyncio

    hub = HubClient(hub_url, src)
    task = asyncio.create_task(hub.run())
    try:
        async for msg in hub.messages():
            try:
                drummer.on_message(msg)
            except Exception:
                log.exception("file mic failed on %s", msg.get("type"))
    finally:
        drummer.stop("shutdown")
        if hub.ws is not None:
            await hub.ws.close()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
