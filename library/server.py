"""Library + console HTTP server (:8766) and its hub link.

    python -m library serve [--port 8766] [--hub ws://localhost:8765] [--public-host <lan ip>]

HTTP:
  GET  /                          the laptop console (library/console/)
  GET  /songs/                    {"songs": [song.json...], "base_url"}   (also /api/songs)
  GET  /songs/<id>/<file>         song files (song.json, drums.drums.txt, drums.tempo.json,
                                  arrangement.mid, band_tracks.json, backing.ogg, click.ogg); Range ok
  POST /api/upload                multipart `file` (.mid/.midi), optional `title`, `artist`, `id`
  POST /api/songsterr             {"song": url or id, "track"?: int, "id"?: str}
  POST /api/command               {"action": "start"|"stop"|"quit", ...} -> session.command (src console)
  DELETE /api/songs/<id>          remove a song
  GET  /api/config                {"hub_url", "base_url"}

Hub (src "library"): publishes `library.songs` on every (re)connect, whenever the library changes
(imports, uploads, deletes, files dropped into songs/inbox/ or edited by the CLI), and in reply to
`library.request`.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import shutil
import socket
import sys
from pathlib import Path
from urllib.parse import unquote

from aiohttp import web

from . import catalog
from .catalog import SERVED

ROOT = catalog.ROOT
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from fletcher.contract import envelope  # noqa: E402
from fletcher.hub_client import HubClient  # noqa: E402

log = logging.getLogger("library")
CONSOLE = Path(__file__).resolve().parent / "console"
LIBRARY_SONGS = "library.songs"
LIBRARY_REQUEST = "library.request"
COMMAND_ACTIONS = ("start", "stop", "quit", "restart")
MAX_UPLOAD = 20 * 1024 * 1024


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


class Library:
    def __init__(self, songs_dir: Path | None = None, hub_url: str | None = "ws://localhost:8765",
                 port: int = 8766, public_host: str | None = None, watch_s: float = 1.0):
        self.songs_dir = Path(songs_dir or catalog.SONGS_DIR)
        self.hub_url, self.port, self.watch_s = hub_url, port, watch_s
        self.public_host = public_host or lan_ip()
        self.hub = HubClient(hub_url, "library") if hub_url else None
        self.console_seq = 0
        self.lock = asyncio.Lock()   # one import at a time
        self._sig = None
        self.published = 0           # library.songs frames sent (tests)
        self.events: list[str] = []  # import log lines, newest last (console shows them)

    # ---- catalog ----
    @property
    def base_url(self) -> str:
        return f"http://{self.public_host}:{self.port}/songs/"

    def songs_payload(self) -> dict:
        return {"songs": catalog.list_songs(self.songs_dir), "base_url": self.base_url}

    def _signature(self):
        out = []
        if self.songs_dir.is_dir():
            for d in self.songs_dir.iterdir():
                f = d / catalog.SONG_JSON
                if d.is_dir() and catalog.valid_id(d.name) and f.is_file():
                    out.append((d.name, f.stat().st_mtime_ns))
        return sorted(out)

    def note(self, line: str) -> None:
        log.info(line)
        self.events = (self.events + [line])[-50:]

    # ---- hub ----
    async def publish(self) -> bool:
        if self.hub is None:
            return False
        ok = await self.hub.send(LIBRARY_SONGS, self.songs_payload())
        self.published += ok
        return ok

    async def changed(self) -> None:
        self._sig = self._signature()
        await self.publish()

    async def send_command(self, data: dict) -> bool:
        ws = self.hub.ws if self.hub else None
        if ws is None:
            return False
        self.console_seq += 1
        msg = envelope("session.command", "console", self.console_seq, data)
        try:
            await ws.send(json.dumps(msg, separators=(",", ":")))
            return True
        except Exception:  # noqa: BLE001
            return False

    async def _hub_loop(self) -> None:
        async def on_connect():
            while True:
                await self.hub.connected.wait()
                await self.publish()
                while self.hub.connected.is_set():
                    await asyncio.sleep(0.2)

        async def on_messages():
            async for msg in self.hub.messages():
                if msg.get("type") == LIBRARY_REQUEST:
                    await self.publish()

        await asyncio.gather(self.hub.run(), on_connect(), on_messages())

    async def _watch_loop(self) -> None:
        from .midi_import import import_inbox
        loop = asyncio.get_running_loop()
        self._sig = self._signature()
        box = catalog.inbox(self.songs_dir)
        while True:
            await asyncio.sleep(self.watch_s)
            try:
                box.mkdir(parents=True, exist_ok=True)
                pending = [f for f in box.iterdir() if f.is_file() and not f.name.startswith(".")
                           and f.suffix.lower() in (".mid", ".midi")]
                if pending:
                    async with self.lock:
                        await loop.run_in_executor(None, lambda: import_inbox(self.songs_dir, log=self.note))
                sig = self._signature()
                if sig != self._sig:
                    await self.changed()
            except Exception as e:  # noqa: BLE001 - the watcher must survive anything
                self.note(f"watcher error: {e!r}")

    # ---- HTTP ----
    def app(self) -> web.Application:
        app = web.Application(client_max_size=MAX_UPLOAD, middlewares=[_cors])
        r = app.router
        r.add_get("/", self.h_index)
        r.add_get("/api/songs", self.h_songs)
        r.add_get("/songs/", self.h_songs)
        r.add_get("/songs", self.h_songs)
        r.add_get("/api/config", self.h_config)
        r.add_get("/api/events", self.h_events)
        r.add_get("/songs/{song_id}/{name}", self.h_file)
        r.add_post("/api/upload", self.h_upload)
        r.add_post("/api/songsterr", self.h_songsterr)
        r.add_post("/api/command", self.h_command)
        r.add_delete("/api/songs/{song_id}", self.h_delete)
        r.add_static("/console/", CONSOLE)
        return app

    async def h_index(self, _req):
        return web.FileResponse(CONSOLE / "index.html", headers={"Cache-Control": "no-cache"})

    async def h_songs(self, _req):
        return web.json_response(self.songs_payload())

    async def h_events(self, _req):
        return web.json_response({"events": self.events})

    async def h_config(self, req):
        hub = self.hub_url or ""
        host = req.url.host or self.public_host
        if hub:  # the browser reaches the hub at the host it reached us at
            hub = hub.replace("localhost", host).replace("127.0.0.1", host)
        return web.json_response({"hub_url": hub, "base_url": self.base_url,
                                  "hub_connected": bool(self.hub and self.hub.connected.is_set())})

    async def h_file(self, req):
        song_id, name = req.match_info["song_id"], req.match_info["name"]
        if not catalog.valid_id(song_id) or name not in SERVED:
            raise web.HTTPNotFound()
        p = self.songs_dir / song_id / name
        if not p.is_file():
            raise web.HTTPNotFound()
        ctype = {".ogg": "audio/ogg", ".mid": "audio/midi", ".json": "application/json",
                 ".txt": "text/plain; charset=utf-8"}[p.suffix]
        return web.FileResponse(p, headers={"Content-Type": ctype})

    async def h_upload(self, req):
        from .midi_import import import_midi
        fields, data, fname = {}, None, None
        reader = await req.multipart()
        async for part in reader:
            if part.name == "file":
                fname = Path(unquote(part.filename or "upload.mid")).name
                data = await part.read(decode=False)
            elif part.name:
                fields[part.name] = (await part.text()).strip()
        if data is None:
            return web.json_response({"ok": False, "error": "no file"}, status=400)
        if Path(fname).suffix.lower() not in (".mid", ".midi"):
            return web.json_response({"ok": False, "error": "not a .mid file"}, status=400)
        box = catalog.inbox(self.songs_dir)
        (box / "imported").mkdir(parents=True, exist_ok=True)
        tmp = box / f".upload-{fname}"
        tmp.write_bytes(data)
        song_id = fields.get("id") or catalog.slug(Path(fname).stem)
        if not catalog.valid_id(song_id):
            return web.json_response({"ok": False, "error": f"bad id {song_id!r}"}, status=400)
        loop = asyncio.get_running_loop()
        try:
            async with self.lock:
                song = await loop.run_in_executor(None, lambda: import_midi(
                    tmp, song_id=song_id, title=fields.get("title") or Path(fname).stem.replace("_", " "),
                    artist=fields.get("artist", ""), songs_dir=self.songs_dir, log=self.note))
            shutil.move(str(tmp), box / "imported" / fname)
        except Exception as e:  # noqa: BLE001
            tmp.unlink(missing_ok=True)
            self.note(f"upload {fname} failed: {e}")
            return web.json_response({"ok": False, "error": str(e)}, status=422)
        await self.changed()
        return web.json_response({"ok": True, "song": song})

    async def h_songsterr(self, req):
        from .songsterr_import import import_songsterr
        body = await req.json()
        loop = asyncio.get_running_loop()
        try:
            async with self.lock:
                song = await loop.run_in_executor(None, lambda: import_songsterr(
                    body["song"], song_id=body.get("id") or None, track=body.get("track"),
                    songs_dir=self.songs_dir, log=self.note))
        except Exception as e:  # noqa: BLE001
            self.note(f"songsterr import failed: {e}")
            return web.json_response({"ok": False, "error": str(e)}, status=422)
        await self.changed()
        return web.json_response({"ok": True, "song": song})

    async def h_command(self, req):
        body = await req.json()
        action = body.get("action")
        if action not in COMMAND_ACTIONS:
            return web.json_response({"ok": False, "error": f"unknown action {action!r}"}, status=400)
        data = {"action": action}
        if action == "start":
            if not catalog.valid_id(body.get("song_id", "")) or catalog.load_song(body["song_id"], self.songs_dir) is None:
                return web.json_response({"ok": False, "error": "unknown song"}, status=404)
            data["song_id"] = body["song_id"]
        if body.get("from_bar") is not None:
            data["from_bar"] = max(1, int(body["from_bar"]))
        elif action == "start":
            data["from_bar"] = 1
        ok = await self.send_command(data)
        return web.json_response({"ok": ok, "sent": data, **({} if ok else {"error": "hub not connected"})},
                                 status=200 if ok else 503)

    async def h_delete(self, req):
        song_id = req.match_info["song_id"]
        if not catalog.valid_id(song_id) or not (self.songs_dir / song_id).is_dir():
            raise web.HTTPNotFound()
        async with self.lock:
            shutil.rmtree(self.songs_dir / song_id)
        self.note(f"deleted {song_id}")
        await self.changed()
        return web.json_response({"ok": True})

    # ---- run ----
    async def start(self, host: str = "0.0.0.0") -> web.AppRunner:
        runner = web.AppRunner(self.app(), access_log=None)
        await runner.setup()
        await web.TCPSite(runner, host, self.port).start()
        self._tasks = [asyncio.create_task(self._watch_loop())]
        if self.hub:
            self._tasks.append(asyncio.create_task(self._hub_loop()))
        log.info("library on http://%s:%d/ (songs: %s, hub %s)", self.public_host, self.port, self.songs_dir, self.hub_url)
        return runner

    async def stop(self, runner: web.AppRunner) -> None:
        for t in getattr(self, "_tasks", []):
            t.cancel()
        await asyncio.gather(*getattr(self, "_tasks", []), return_exceptions=True)
        await runner.cleanup()


@web.middleware
async def _cors(req, handler):
    resp = await handler(req)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


async def serve(port: int = 8766, hub_url: str | None = "ws://localhost:8765", songs_dir=None,
                public_host: str | None = None, host: str = "0.0.0.0") -> None:
    lib = Library(songs_dir, hub_url, port, public_host)
    runner = await lib.start(host)
    print(f"console: http://{lib.public_host}:{port}/   songs: {lib.base_url}", flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        await lib.stop(runner)


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="python -m library serve", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--port", type=int, default=8766)
    p.add_argument("--host", default="0.0.0.0", help="bind address")
    p.add_argument("--hub", default="ws://localhost:8765", help="hub URL ('' = no hub)")
    p.add_argument("--public-host", help="address the Quest uses for base_url (default: LAN IP)")
    p.add_argument("--songs", help="songs folder (default: <repo>/songs)")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s", datefmt="%H:%M:%S")
    try:
        asyncio.run(serve(args.port, args.hub or None, args.songs, args.public_host, args.host))
    except KeyboardInterrupt:
        pass
