"""One command for every laptop service:

    python -m nqmt [--mic laptop|quest|file:<wav>[,<wav>...]] [--live] [--no-fletcher-voice] [--usb]

starts, each as its own process with its log in <record dir>/nqmt-<time>/:
  hub        hub.server, ws :8765 (records every frame to recordings/)
  library    python -m library serve, http :8766 (song files, console page, MIDI upload); skipped
             if the library package isn't there
  session    hub.session, the song session state machine (start/quit, count_in t0, result)
  bridge     hub.bridge --session --clock laptop: loads the song on each count_in, judges hits
  mic        laptop: Kevin's transcriber on the laptop mic (`--input mic --clock monotonic`)
             quest:  the same on the Quest's mic.pcm frames (`--input hub:<hub url>`)
             (laptop/quest follow the headset's Settings live: quest.settings mic_source)
             file:   a recorded take played in sync with the session (nqmt/file_mic.py)
  fletcher   python -m fletcher (--dry-run unless --live; --barks-only with --no-fletcher-voice)
and prints the LAN address for the Quest (`--usb`: 127.0.0.1, for a headset on USB with
`adb reverse tcp:8765 tcp:8765` and `adb reverse tcp:8766 tcp:8766`, no Wi-Fi needed). The bridge
uses the round trip `python -m nqmt.calibrate` measured (calibration.json) for the mic. Ctrl-C stops everything (Fletcher and the mic first,
the hub last); a crashed hub takes the rest down with it.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import logging
import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
LT = ROOT / "live_transcription"
log = logging.getLogger("nqmt")


def lan_ip() -> str:
    """This laptop's LAN address (no packet is sent), else 127.0.0.1."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


class Child:
    """A service process: output to <logdir>/<name>.log and (unless quiet) the console."""

    def __init__(self, name: str, cmd: list[str], logdir: Path, quiet: bool, cwd: Path = ROOT, stdin=None,
                 stdout_to_next: bool = False):
        self.name, self.cmd, self.quiet = name, cmd, quiet
        self.lines: list[str] = []
        self.log = (logdir / f"{name}.log").open("w", buffering=1)
        self.log.write("$ " + " ".join(cmd) + "\n")
        env = {**os.environ, "PYTHONUNBUFFERED": "1"}
        self.p = subprocess.Popen(cmd, cwd=cwd, stdin=stdin, text=True, env=env,
                                  stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE if stdout_to_next else subprocess.STDOUT,
                                  start_new_session=True)  # Ctrl-C reaches us only; we stop them in order
        stream = self.p.stderr if stdout_to_next else self.p.stdout
        self.thread = threading.Thread(target=self._pump, args=(stream,), daemon=True)
        self.thread.start()

    def _pump(self, stream) -> None:
        for line in stream:
            line = line.rstrip("\n")
            self.lines.append(line)
            self.log.write(line + "\n")
            if not self.quiet:
                print(f"[{self.name:8}] {line}", flush=True)

    def wait_for(self, pattern: str, timeout: float) -> bool:
        rx, end = re.compile(pattern), time.monotonic() + timeout
        seen = 0
        while time.monotonic() < end:
            lines = self.lines[seen:]
            if any(rx.search(l) for l in lines):
                return True
            seen += len(lines)
            if self.p.poll() is not None:
                return False
            time.sleep(0.05)
        return False

    def alive(self) -> bool:
        return self.p.poll() is None

    def stop(self, timeout: float = 8.0) -> int | None:
        if self.p.poll() is None:
            self.p.send_signal(signal.SIGINT)
        try:
            rc = self.p.wait(timeout)
        except subprocess.TimeoutExpired:
            self.p.kill()
            self.p.wait()
            rc = None
        self.thread.join(2)
        self.log.close()
        return rc


def mic_command(mode: str, hub_url: str, logdir: Path, latency_ms: float | None, n: int = 1) -> list[str]:
    """Kevin's transcriber on a live input, laptop-clock lines (live_transcription/tools/live_input.py).
    The repo .venv python: sounddevice and websockets live there, not in Kevin's uv lock."""
    src = "mic" if mode == "laptop" else f"hub:{hub_url}"
    cmd = [PY, 
        "tools/transcribe_wav.py", "--input", src, "--clock", "monotonic", "--onsets-only",
        "--output", str(logdir / f"mic_{n:02d}_{mode}.drums.txt")]
    if mode == "laptop":  # the Quest mute filter is for the headset's mic only (it drops real hits elsewhere)
        cmd += ["--mute-window-ms", "0"]
    if latency_ms is not None and mode == "laptop":
        cmd += ["--mic-latency-ms", str(latency_ms)]
    return cmd


def parse_args(argv=None):
    p = argparse.ArgumentParser(prog="python -m nqmt", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--mic", default="laptop",
                   help="laptop (default) | quest (the headset's mic.pcm) | file:<wav>[,<wav>...]")
    p.add_argument("--file-origin", type=float, default=0.0,
                   help="file mic: song time (s) of the take's first sample; negative = lead-in")
    p.add_argument("--file-quest-take", action="store_true",
                   help="file mic: the takes were recorded on the Quest (keep Kevin's Quest mute filter on)")
    p.add_argument("--live", action="store_true", help="Fletcher with Claude + ElevenLabs (default --dry-run)")
    p.add_argument("--no-fletcher-voice", action="store_true", help="Fletcher barks only (no voice API)")
    p.add_argument("--no-fletcher", action="store_true", help="don't start Fletcher")
    p.add_argument("--no-library", action="store_true", help="don't start the library/console server")
    p.add_argument("--port", type=int, default=8765, help="hub port")
    p.add_argument("--http-port", type=int, default=8766, help="library + console port")
    p.add_argument("--songs", default=str(ROOT / "songs"), help="song library folder")
    p.add_argument("--record-dir", default=str(ROOT / "recordings"), help="hub recordings and service logs")
    p.add_argument("--no-record", action="store_true", help="hub doesn't record frames")
    p.add_argument("--record-mic-pcm", action="store_true", help="hub records mic.pcm frames too (~130 KB/s)")
    p.add_argument("--mic-latency-ms", type=float, default=None,
                   help="laptop mic: input latency override (default: what PortAudio reports)")
    p.add_argument("--mic-fixed", action="store_true",
                   help="laptop/quest mic: ignore the headset's mic setting (quest.settings); default: follow it")
    p.add_argument("--quiet", action="store_true", help="services log to files only")
    p.add_argument("--usb", action="store_true",
                   help="headset on USB via adb reverse: advertise 127.0.0.1 (library base_url, printed URLs)")
    p.add_argument("--calibration", default=str(ROOT / "calibration.json"),
                   help="round-trip latencies per mic (python -m nqmt.calibrate); the bridge applies them")
    return p.parse_args(argv)


class Launcher:
    def __init__(self, a):
        self.a = a
        self.url = f"ws://127.0.0.1:{a.port}"
        self.logdir = Path(a.record_dir) / time.strftime("nqmt-%Y%m%d-%H%M%S")
        self.logdir.mkdir(parents=True, exist_ok=True)
        self.children: dict[str, Child] = {}
        self.drummer = None
        self.stopping = asyncio.Event()

    def start(self, name: str, cmd: list[str], **kw) -> Child:
        c = Child(name, cmd, self.logdir, self.a.quiet, **kw)
        self.children[name] = c
        return c

    async def run(self) -> int:
        a = self.a
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, self.stopping.set)
        ip = "127.0.0.1" if a.usb else lan_ip()
        mic = a.mic
        takes = []
        if mic.startswith("file:"):
            takes = [Path(t) for t in mic[5:].split(",") if t]
            missing = [str(t) for t in takes if not t.exists()]
            if not takes or missing:
                raise SystemExit(f"--mic file: no such take(s): {missing or mic}")
        elif mic not in ("laptop", "quest"):
            raise SystemExit("--mic must be laptop, quest or file:<wav>[,<wav>...]")
        try:
            hub_cmd = [PY, "-m", "hub.server", "--port", str(a.port)]
            hub_cmd += ["--no-record"] if a.no_record else ["--record-dir", str(a.record_dir)]
            if a.record_mic_pcm:
                hub_cmd.append("--record-mic-pcm")
            hub = self.start("hub", hub_cmd)
            if not await asyncio.to_thread(hub.wait_for, "hub listening", 15):
                raise RuntimeError(f"hub did not start (port {a.port} in use?); see {self.logdir}/hub.log")
            clients = 0
            if not a.no_library and importlib.util.find_spec("library") and (ROOT / "library" / "server.py").exists():
                self.start("library", [PY, "-m", "library", "serve", "--port", str(a.http_port), "--hub", self.url,
                                       "--songs", a.songs, "--public-host", ip])
                clients += 1
            else:
                log.warning("no library server (library/ missing or --no-library): the Quest can't download songs")
            self.start("session", [PY, "-m", "hub.session", "--hub", self.url, "--songs", a.songs])
            clients += 1
            bridge_cmd = [PY, "-m", "hub.bridge", "--session", "--clock", "laptop", "--songs", a.songs,
                          "--hub", self.url]
            if not takes:  # a live mic: its calibrated round trip (calibration.json), switched with the mic
                bridge_cmd += ["--calibration", a.calibration, "--mic-source", mic]
                bridge_cmd += [] if a.mic_fixed else ["--follow-mic"]
            clients += 1  # the bridge
            if takes:
                bridge = self.start("bridge", bridge_cmd, stdin=subprocess.PIPE)
                from .file_mic import FileDrummer, run as run_drummer
                self.drummer = FileDrummer(takes, a.file_origin, bridge.p.stdin, self.logdir,
                                           quest_take=a.file_quest_take)
                self.drummer_task = asyncio.create_task(run_drummer(self.drummer, self.url))
                clients += 1
            else:
                self.bridge = self.start("bridge", bridge_cmd, stdin=subprocess.PIPE)
                self.mic_lock, self.mic_n = threading.Lock(), 0
                self.start_mic(mic)
                clients += 1 if mic == "quest" else 0
                if not a.mic_fixed:  # the headset's Settings page switches the source live
                    self.settings_task = asyncio.create_task(self.follow_settings())
                    clients += 1
            if not a.no_fletcher:
                cmd = [PY, "-m", "fletcher", "--hub", self.url]
                if not a.live:
                    cmd.append("--dry-run")
                if a.no_fletcher_voice:
                    cmd.append("--barks-only")
                self.start("fletcher", cmd)
                clients += 1
            ok = await asyncio.to_thread(hub.wait_for, rf"\({clients} connected\)", 30)
            print("\n" + "=" * 64, flush=True)
            print(f"nqmt {'ready' if ok else 'started (not every service connected yet)'}: "
                  f"hub ws://{ip}:{a.port}   mic {mic}   Fletcher "
                  f"{'off' if a.no_fletcher else ('live' if a.live else 'dry-run')}", flush=True)
            if "library" in self.children:
                print(f"  console:  http://{ip}:{a.http_port}/   (songs from {a.songs})", flush=True)
            print(f"  Quest:    hub URL ws://{ip}:{a.port}  (Settings, or `-- --fletcher ws://{ip}:{a.port}`)",
                  flush=True)
            if a.usb:
                print(f"  USB:      adb reverse tcp:{a.port} tcp:{a.port} && adb reverse tcp:{a.http_port} "
                      f"tcp:{a.http_port}", flush=True)
            print(f"  calibrate: python -m nqmt.calibrate --mic {mic if mic in ('laptop', 'quest') else 'quest'}"
                  f"{'' if a.port == 8765 else f' --hub {self.url}'}", flush=True)
            print(f"  logs:     {self.logdir}", flush=True)
            print("=" * 64 + "\n", flush=True)
            while not self.stopping.is_set():
                try:
                    await asyncio.wait_for(self.stopping.wait(), 0.5)
                except asyncio.TimeoutError:
                    pass
                for name, c in self.children.items():
                    if not c.alive() and not getattr(c, "reported", False):
                        c.reported = True
                        log.warning("%s exited (%s); see %s", name, c.p.returncode, self.logdir / f"{name}.log")
                        if name == "hub":
                            self.stopping.set()
        except RuntimeError as e:
            log.error("%s", e)
            return await self.shutdown(1)
        return await self.shutdown(0)

    # -- live mic: laptop or Quest, switchable from the headset (quest.settings) ------
    def start_mic(self, mode: str) -> None:
        """Kevin's transcriber on `mode`; its lines are copied into the bridge's stdin."""
        self.mic_n += 1
        m = Child(f"mic{self.mic_n}", mic_command(mode, self.url, self.logdir, self.a.mic_latency_ms, self.mic_n),
                  self.logdir, self.a.quiet, cwd=LT, stdout_to_next=True)
        self.children["mic"], self.mic_mode = m, mode

        def copy(src=m.p.stdout) -> None:
            for line in src:
                try:
                    with self.mic_lock:
                        self.bridge.p.stdin.write(line)
                        self.bridge.p.stdin.flush()
                except (BrokenPipeError, ValueError, OSError):
                    return
        threading.Thread(target=copy, daemon=True).start()
        log.info("mic: %s (transcriber %d)", mode, self.mic_n)

    async def follow_settings(self) -> None:
        from fletcher.hub_client import HubClient
        hub = HubClient(self.url, "nqmt")
        task = asyncio.create_task(hub.run())
        try:
            async for msg in hub.messages():
                if msg.get("type") != "quest.settings":
                    continue
                want = (msg.get("data") or {}).get("mic_source")
                if want in ("laptop", "quest") and want != self.mic_mode:
                    log.info("headset settings: mic %s -> %s", self.mic_mode, want)
                    old = self.children["mic"]
                    await asyncio.to_thread(old.stop)
                    self.start_mic(want)
        finally:
            if hub.ws is not None:
                await hub.ws.close()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def shutdown(self, rc: int) -> int:
        print("nqmt: shutting down", flush=True)
        if self.drummer:
            self.drummer.stop("shutdown")
            self.drummer_task.cancel()  # its hub socket closes now, so the hub isn't left waiting on it
            await asyncio.gather(self.drummer_task, return_exceptions=True)
        if getattr(self, "settings_task", None):
            self.settings_task.cancel()
            await asyncio.gather(self.settings_task, return_exceptions=True)
        rcs = {}
        for name in ("fletcher", "mic", "bridge", "session", "library", "hub"):
            if name in self.children:
                rcs[name] = await asyncio.to_thread(self.children[name].stop)
        bad = {k: v for k, v in rcs.items() if v not in (0, -2, 130)}  # SIGINT exits are clean too
        print(f"nqmt: stopped {rcs}", flush=True)
        return rc or (1 if bad else 0)


def main(argv=None) -> None:
    a = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s", datefmt="%H:%M:%S")
    sys.exit(asyncio.run(Launcher(a).run()))
