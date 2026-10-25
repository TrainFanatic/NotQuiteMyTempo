"""Library server + hub link, against the real hub (fletcher.dev.mini_hub = hub.server)."""

import asyncio
import json
import socket

import aiohttp
import pytest
from websockets.asyncio.client import connect

from fletcher.contract import now
from fletcher.dev.mini_hub import run_hub
from library.server import Library
from library.tests.fixtures import pickup_song


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


async def recv_type(ws, type_, timeout=5.0):
    async def go():
        while True:
            m = json.loads(await ws.recv())
            if m.get("type") == type_:
                return m
    return await asyncio.wait_for(go(), timeout)


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def ports():
    return free_port(), free_port()


def test_library_end_to_end(tmp_path, ports):
    hub_port, http_port = ports

    async def main():
        ready = asyncio.Event()
        hub = asyncio.create_task(run_hub(hub_port, None, ready))
        await ready.wait()
        async with connect(f"ws://127.0.0.1:{hub_port}") as obs:
            lib = Library(tmp_path, f"ws://127.0.0.1:{hub_port}", http_port, public_host="10.0.0.5", watch_s=0.2)
            runner = await lib.start("127.0.0.1")
            base = f"http://127.0.0.1:{http_port}"
            try:
                m = await recv_type(obs, "library.songs")             # on connect
                assert m["src"] == "library" and m["data"] == {"songs": [], "base_url": "http://10.0.0.5:%d/songs/" % http_port}
                assert abs(m["t"] - now()) < 5

                await obs.send(json.dumps({"type": "library.request", "src": "quest_app", "seq": 1, "t": now(), "data": {}}))
                assert (await recv_type(obs, "library.songs"))["data"]["songs"] == []

                async with aiohttp.ClientSession() as http:
                    fd = aiohttp.FormData()
                    fd.add_field("file", pickup_song(), filename="My Tune.mid", content_type="audio/midi")
                    fd.add_field("title", "My Tune")
                    fd.add_field("artist", "Band")
                    r = await http.post(f"{base}/api/upload", data=fd)
                    j = await r.json()
                    assert r.status == 200 and j["ok"], j
                    assert j["song"]["id"] == "my_tune" and j["song"]["artist"] == "Band"
                    m = await recv_type(obs, "library.songs")         # on change
                    assert [s["id"] for s in m["data"]["songs"]] == ["my_tune"]

                    r = await http.get(f"{base}/songs/my_tune/drums.drums.txt")
                    assert r.status == 200 and "# time_s  midi  name" in await r.text()
                    r = await http.get(f"{base}/songs/my_tune/arrangement.mid")
                    assert r.headers["Content-Type"] == "audio/midi" and (await r.read()) == pickup_song()
                    if j["song"]["backing"]:
                        r = await http.get(f"{base}/songs/my_tune/backing.ogg", headers={"Range": "bytes=0-3"})
                        assert r.status == 206 and await r.read() == b"OggS"
                    for bad in ("/songs/my_tune/../../etc/passwd", "/songs/my_tune/secret.txt", "/songs/_soundfonts/x.sf2"):
                        assert (await http.get(base + bad)).status == 404
                    r = await http.get(f"{base}/songs/")
                    assert [s["id"] for s in (await r.json())["songs"]] == ["my_tune"]
                    assert "NQMT Console" in await (await http.get(base + "/")).text()
                    assert (await http.get(base + "/console/console.js")).status == 200
                    cfg = await (await http.get(base + "/api/config")).json()
                    assert cfg["hub_url"] == f"ws://127.0.0.1:{hub_port}" and cfg["hub_connected"]

                    # console buttons -> session.command from src "console" on the laptop clock
                    r = await http.post(f"{base}/api/command", json={"action": "start", "song_id": "my_tune", "from_bar": 3})
                    assert (await r.json())["ok"]
                    m = await recv_type(obs, "session.command")
                    assert m["src"] == "console" and m["data"] == {"action": "start", "song_id": "my_tune", "from_bar": 3}
                    assert abs(m["t"] - now()) < 5
                    for body in ({"action": "stop"}, {"action": "quit"}):
                        await http.post(f"{base}/api/command", json=body)
                        assert (await recv_type(obs, "session.command"))["data"] == body
                    r = await http.post(f"{base}/api/command", json={"action": "start", "song_id": "nope"})
                    assert r.status == 404
                    r = await http.post(f"{base}/api/command", json={"action": "explode"})
                    assert r.status == 400

                    bad = aiohttp.FormData()
                    bad.add_field("file", b"garbage", filename="bad.mid")
                    r = await http.post(f"{base}/api/upload", data=bad)
                    assert r.status == 422 and not (await r.json())["ok"]

                # a MIDI dropped into songs/inbox/ is picked up by the watcher
                (tmp_path / "inbox").mkdir(exist_ok=True)
                (tmp_path / "inbox" / "dropped.mid").write_bytes(pickup_song(bars=4))
                m = await recv_type(obs, "library.songs", timeout=30)
                assert sorted(s["id"] for s in m["data"]["songs"]) == ["dropped", "my_tune"]
                assert (tmp_path / "inbox" / "imported" / "dropped.mid").exists()

                async with aiohttp.ClientSession() as http:
                    assert (await http.delete(f"{base}/api/songs/dropped")).status == 200
                m = await recv_type(obs, "library.songs")
                assert [s["id"] for s in m["data"]["songs"]] == ["my_tune"]
            finally:
                await lib.stop(runner)
                hub.cancel()

    run(main())


def test_reconnect_republishes(tmp_path, ports):
    """The hub restarts: the library sends library.songs again on reconnect."""
    hub_port, http_port = ports

    async def main():
        lib = Library(tmp_path, f"ws://127.0.0.1:{hub_port}", http_port, watch_s=0.2)
        runner = await lib.start("127.0.0.1")
        try:
            for cycle in range(2):
                before = lib.published
                ready = asyncio.Event()
                hub = asyncio.create_task(run_hub(hub_port, None, ready))
                await ready.wait()
                async with connect(f"ws://127.0.0.1:{hub_port}") as obs:
                    for _ in range(100):                    # library reconnects within ~1 s
                        if lib.published > before:
                            break
                        await asyncio.sleep(0.05)
                    assert lib.published == before + 1, cycle
                    await obs.send(json.dumps({"type": "library.request", "src": "x", "seq": 1, "t": 0, "data": {}}))
                    await recv_type(obs, "library.songs", timeout=5)
                    for _ in range(20):
                        if lib.published == before + 2:
                            break
                        await asyncio.sleep(0.05)
                    assert lib.published == before + 2
                hub.cancel()
                await asyncio.gather(hub, return_exceptions=True)
                await asyncio.sleep(0.3)
        finally:
            await lib.stop(runner)

    run(main())
