"""The whole app, the way a session runs it, no API calls and no headset:

    python -m nqmt --mic file:<sloppy take>,<clean take>   (hub, library, session, bridge, Fletcher --dry-run)
    Godot --headless --path quest_app -- --game --fletcher ws://... --no-intro --fletcher-log
    a scripted console client (src "console") on the hub

The song is the fixture library's back-in-black-16 (hub/tests/fixtures/songs/, Chris's
4-bar Back in Black x4); the takes are Chris's drift simulator rendered with the sample kit: a
heavily drifting one (cut off), then a clean one that the file mic plays after the restart.
The console sends `session.command start {song_id}`; then, from the hub recording and the app's
`[fletcher-log]` lines (laptop clock), it checks:
  count_in (reason start, t0 >= 5 beats + 1.5 s ahead, song_time_at_t0 0) -> Fletcher's count-off
  cue on that t0 -> Terence's finger on t0 - 5 beats and the four words on their beats ->
  bridge playing + judgments -> stop (cutoff) -> restart(t0) -> the controller's count_in
  (reason restart, same t0, song_time_at_t0 = the bar's start) -> count-off on the beats ->
  judgments from the restart bar, clean -> finished -> session.result (cutoffs, notes) ->
  quit -> idle; no script errors in the app; every process exits 0. Prints a timeline, PASS/FAIL.

    python -m hub.app_e2e [--godot PATH] [--runs 1] [--keep] [--no-godot]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

import numpy as np

from fletcher.cues import COUNTOFF, COUNTOFF_LEAD_BEATS
from fletcher.dev.mini_hub import free_port
from hub.full_e2e import EXPECTED_ERRORS, Check, Proc, find_godot, read_godot, read_recording
from hub.session import load_song

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
SONGS = ROOT / "hub" / "tests" / "fixtures" / "songs"
SONG_ID = "back-in-black-16"
COUNTOFF_MANIFEST = ROOT / "fletcher" / "countoff" / "manifest.json"
LEAD_S = 1.0  # the takes start this long before bar 1 (--file-origin -LEAD_S)
FINGER_TOL_S, WORD_TOL_S = 0.15, 0.1


# The test kit (datacollection/tests/fixtures/kit) has kick 36, snare 38, closed hat 42, crash 57:
# other drums (MIDI imports: ride, toms, pedal hat) are rendered with their family's sample.
KIT_SAMPLE = {"kick": 36, "snare": 38, "hihat": 42, "crash": 57, "ride": 42, "tom": 38}


def make_takes(d: Path, sheet: Path, takes: list[tuple[int, float]]) -> list[Path]:
    """Kit audio of drifting clones of the sheet (Chris's simulator), bar 1 at LEAD_S."""
    import soundfile as sf
    sys.path[:0] = [str(ROOT / "datacollection"), str(ROOT / "rushingOrDragging" / "tests")]
    import sim_session as ss
    import test_drift as td
    from hub.bridge import cp

    out = []
    for k, (seed, sd) in enumerate(takes):
        truth = td.make_drifting(sheet, d / f"take{k}.drums.txt", seed=seed, drift_sd=sd)
        events = [(t, KIT_SAMPLE.get(cp.family(m), 38), 4) for t, m in sorted(truth)]
        n = int((max(t for t, _ in truth) + LEAD_S + 2.0) * 48000)
        x = ss.render(events, 48000, n, lambda t, m: t + LEAD_S)
        x = 0.8 * x / np.abs(x).max() + 10 ** (-70 / 20) * np.random.default_rng(1).standard_normal(n)
        wav = d / f"take{k}.wav"
        sf.write(wav, x.astype(np.float32), 48000, subtype="FLOAT")
        out.append(wav)
    return out


class Console:
    """The laptop console as a script: a hub client (src "console") in its own thread."""

    def __init__(self, url: str):
        self.url, self.got = url, []
        self.loop = asyncio.new_event_loop()
        self.hub = None
        threading.Thread(target=self.loop.run_forever, daemon=True).start()
        asyncio.run_coroutine_threadsafe(self._run(), self.loop)

    async def _run(self) -> None:
        from fletcher.hub_client import HubClient
        self.hub = HubClient(self.url, "console")
        asyncio.create_task(self.hub.run())
        async for msg in self.hub.messages():
            self.got.append(msg)

    def send(self, typ: str, data: dict) -> bool:
        while self.hub is None:
            time.sleep(0.05)
        asyncio.run_coroutine_threadsafe(self.hub.connected.wait(), self.loop).result(10)
        return asyncio.run_coroutine_threadsafe(self.hub.send(typ, data), self.loop).result(5)

    def wait(self, typ: str, timeout: float, **kv) -> dict | None:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            for m in list(self.got):
                d = m.get("data") or {}
                if m.get("type") == typ and all(d.get(k) == v for k, v in kv.items()):
                    return m
            time.sleep(0.05)
        return None

    def close(self) -> None:
        async def stop():
            if self.hub and self.hub.ws is not None:
                await self.hub.ws.close()
            for t in asyncio.all_tasks():
                if t is not asyncio.current_task():
                    t.cancel()
        try:
            asyncio.run_coroutine_threadsafe(stop(), self.loop).result(5)
        except Exception:
            pass
        self.loop.call_soon_threadsafe(self.loop.stop)


def game_log(out: str) -> list[tuple[float, str, str]]:
    """D's `-- --game-log` lines: `[game] <headset s> <event> <args>` (headset clock)."""
    rows = []
    for line in out.splitlines():
        m = re.match(r"^\[game\] (-?\d+\.\d+) (\S+) ?(.*)$", line)
        if m:
            rows.append((float(m.group(1)), m.group(2), m.group(3)))
    return rows


def check_game(rows, count_ins: list[dict], song, ck: Check) -> None:
    """The headset's game flow: the library arrived, every count_in was prepared and handed to the
    backing with its song_time_at_t0 ahead of t0, the song finished and the results came in."""
    ev = [e for _, e, _ in rows]
    ck("library" in ev, "game: no library.songs on the headset")
    plays = [(t, a.split()) for t, e, a in rows if e == "play_at"]
    ck(len(plays) == len(count_ins), f"game: {len(plays)} play_at for {len(count_ins)} count_in")
    for (t, args), ci in zip(plays, count_ins):
        t0_local, st = float(args[0]), float(args[1])
        ck(abs(st - ci["data"]["song_time_at_t0"]) < 1e-3, f"game: play_at song time {st} != {ci['data']}")
        ck(t0_local - t > 0.5, f"game: backing scheduled only {t0_local - t:.2f} s before t0")
    states = [a for _, e, a in rows if e == "state"]
    for want in ("count_in", "finished", "idle"):
        ck(want in states, f"game: never in state {want} (saw {states})")
    res = [a for _, e, a in rows if e == "result"]
    if ck(bool(res), "game: no results on the headset"):
        ck(json.loads(res[0]).get("song_id") == song.id, "game: results for another song")


def analyse(rec: list[dict], glog, godot_out: str, rcs: dict, ck: Check, song, t_cmd: float,
            have_app: bool) -> list[tuple[float, str]]:
    tl: list[tuple[float, str]] = []
    d = lambda m: m.get("data") or {}  # noqa: E731
    of = lambda typ, src=None, **kv: [m for m in rec if m.get("type") == typ  # noqa: E731
                                     and (src is None or m.get("src") == src)
                                     and all(d(m).get(k) == v for k, v in kv.items())]
    gl = lambda ev: [(t, a) for t, e, a in glog if e == ev]  # noqa: E731
    manifest = json.loads(COUNTOFF_MANIFEST.read_text())

    def countoff_on_app(t0: float, bpm: float, clips: list[str], what: str) -> None:
        if not have_app:
            return
        want = t0 - COUNTOFF_LEAD_BEATS * 60.0 / bpm
        fingers = [t for t, a in gl("move_started") if a == COUNTOFF and want - 1.0 <= t <= want + 2.0]
        if ck(bool(fingers), f"{what}: the count-off finger never started (due {want - t_cmd:.2f})"):
            tl.append((fingers[0], f"app: {what} finger {(fingers[0] - want) * 1000:+.0f} ms vs t0 - "
                                   f"{COUNTOFF_LEAD_BEATS:g} beats"))
            ck(abs(fingers[0] - want) <= FINGER_TOL_S, f"{what}: finger {(fingers[0] - want) * 1000:+.0f} ms off")
        words = [(t, a) for t, a in gl("countoff_word") if t0 - 8 * 60 / bpm <= t <= t0]
        ck(len(words) == len(clips) > 0, f"{what}: app played {len(words)} count-off words, cue had {len(clips)}")
        for k, (clip, (t, a)) in enumerate(zip(clips, words)):
            onset = t + float(manifest.get(a, {}).get("onset_s", 0.0))
            beat = t0 - (len(clips) - k) * 60.0 / bpm
            tl.append((t, f"app: {what} count-off {a} onset {(onset - beat) * 1000:+.0f} ms vs beat {k - len(clips)}"))
            ck(a == clip, f"{what}: word {k} is {a}, cue said {clip}")
            ck(abs(onset - beat) <= WORD_TOL_S, f"{what}: {a} {(onset - beat) * 1000:+.0f} ms off its beat")

    count_ins = of("session.state", "session", state="count_in")
    starts = [m for m in count_ins if d(m).get("reason") == "start"]
    if not ck(len(starts) == 1, f"{len(starts)} count_in(start) from the controller"):
        return tl
    st = starts[0]
    t0, bpm = d(st)["t0"], d(st)["bpm"]
    tl.append((st["t"], f"session: count_in start {d(st)['song_id']} bar {d(st)['from_bar']}, t0 +{t0 - st['t']:.2f} s"))
    ck(d(st)["song_id"] == song.id and d(st)["song_time_at_t0"] == 0.0 and d(st)["from_bar"] == 1,
       f"count_in start fields: {d(st)}")
    ck(t0 - st["t"] >= 5 * 60 / bpm + 1.5, f"count_in lead only {t0 - st['t']:.2f} s")
    cues = [m for m in of("fletcher.state", "aaron") if d(m).get("gesture") == COUNTOFF]
    co = next((c for c in cues if abs(d(c).get("t0", 0) - t0) < 1e-3), None)
    if ck(co is not None, "Fletcher sent no count-off cue on the song's t0"):
        tl.append((co["t"], f"Fletcher: count-off cue t0 (+{(co['t'] - st['t']) * 1000:.0f} ms after count_in), "
                            f"clips {d(co)['clips']}"))
        countoff_on_app(t0, float(d(co)["bpm"]), d(co)["clips"], "start")
    tl.append((t0, "t0: bar 1 downbeat"))

    notes = of("judgment.note", "chris")
    playing = [m for m in of("session.state", "chris", state="playing") if m["t"] >= t0 - 1]
    if ck(bool(playing), "bridge never sent playing"):
        tl.append((playing[0]["t"], f"bridge: playing bar {d(playing[0]).get('bar')} "
                                    f"({(playing[0]['t'] - t0) * 1000:+.0f} ms vs t0)"))
    stops = of("session.command", "aaron", action="stop")
    restarts = of("session.command", "aaron", action="restart")
    if not ck(bool(stops), "Fletcher never cut off"):
        return tl
    t_stop = stops[0]["t"]
    before = [n for n in notes if n["t"] < t_stop]
    errs = [abs(d(n)["error_ms"]) for n in before if d(n).get("error_ms") is not None]
    tl.append((before[0]["t"] if before else t0, f"judgments: {len(before)} before the cutoff, mean |err| "
                                                  f"{np.mean(errs) if errs else 0:.0f} ms"))
    ck(len(before) >= 20, f"only {len(before)} judgments before the cutoff")
    tl.append((t_stop, f"Fletcher: stop at bar {d(stops[0]).get('from_bar')}"))
    if not ck(bool(restarts), "no restart after the cutoff"):
        return tl
    r = restarts[0]
    rt0, rbar = d(r)["t0"], d(r)["from_bar"]
    tl.append((r["t"], f"Fletcher: restart from bar {rbar}, t0 in {rt0 - r['t']:.2f} s"))
    rci = next((m for m in count_ins if m["t"] >= r["t"] and d(m).get("reason") == "restart"), None)
    if ck(rci is not None, "controller never answered the restart with count_in"):
        tl.append((rci["t"], f"session: count_in restart bar {d(rci)['from_bar']} song time "
                             f"{d(rci)['song_time_at_t0']:.3f} (+{(rci['t'] - r['t']) * 1000:.0f} ms)"))
        ck(abs(d(rci)["t0"] - rt0) < 1e-3 and d(rci)["from_bar"] == rbar, f"restart count_in {d(rci)} vs {d(r)}")
        ck(abs(d(rci)["song_time_at_t0"] - song.bar_start(rbar)) < 1e-4, "song_time_at_t0 != the bar's start")
    rco = next((c for c in cues if abs(d(c).get("t0", 0) - rt0) < 1e-3), None)
    if ck(rco is not None, "no count-off cue for the restart"):
        countoff_on_app(rt0, float(d(rco)["bpm"]), d(rco)["clips"], "restart")
    ck(sum(1 for c in cues if abs(d(c).get("t0", 0) - rt0) < 1e-3) == 1, "restart counted off twice")
    tl.append((rt0, f"t0: bar {rbar} downbeat"))
    t_end = stops[1]["t"] if len(stops) > 1 else float("inf")
    after = [n for n in notes if rt0 - 0.5 <= n["t"] < t_end]
    hits = [d(n) for n in after if d(n).get("error_ms") is not None]
    ck(len(after) >= 20, f"only {len(after)} judgments after the restart")
    ck(bool(after) and min(d(n)["bar"] for n in after) >= rbar, "judgments after the restart before its bar")
    if hits:
        mae = float(np.mean([abs(h["error_ms"]) for h in hits]))
        tl.append((after[0]["t"], f"judgments: {len(after)} after the restart from bar {min(h['bar'] for h in hits)}, "
                                  f"mean |err| {mae:.0f} ms"))
        ck(mae < 30, f"clean take judged at mean |err| {mae:.0f} ms")
    fin = of("session.state", "session", state="finished")
    if ck(bool(fin), "no finished"):
        end = t0 if not restarts else rt0
        want = end + song.end_s - song.bar_start(rbar) + 1.5
        tl.append((fin[0]["t"], f"session: finished ({(fin[0]['t'] - want) * 1000:+.0f} ms vs last bar + ring)"))
        ck(abs(fin[0]["t"] - want) < 0.3 or len(stops) > 1, "finished at the wrong time")
    res = of("session.result", "session")
    if ck(len(res) == 1, f"{len(res)} session.result"):
        rd = d(res[0])
        tl.append((res[0]["t"], "session: result " + json.dumps(rd)))
        ck(rd["song_id"] == song.id and rd["completed"] is True, "result: song/completed")
        ck(rd["cutoffs"] == len(stops), f"result cutoffs {rd['cutoffs']} != {len(stops)} stops")
        judged = [n for n in notes if n["t"] <= res[0]["t"]]
        ck(rd["notes"] + rd["extra"] == len(judged), f"result counts {rd['notes']}+{rd['extra']} != {len(judged)} notes")
        ck(rd["bars_played"] >= song.bars - rbar + 1, f"bars_played {rd['bars_played']}")
    idle = [m for m in of("session.state", "session", state="idle") if res and m["t"] > res[0]["t"]]
    if ck(bool(idle), "quit didn't return the session to idle"):
        tl.append((idle[0]["t"], "session: idle (quit)"))
    if have_app:
        rows = game_log(godot_out)
        check_game(rows, count_ins, song, ck)
        for t, e, a in rows:
            if e in ("play_at", "result", "prepared"):
                ck.notes.append(f"game: {e} {a[:160]}")
        bad = [l for l in godot_out.splitlines()
               if ("SCRIPT ERROR" in l or l.startswith("ERROR") or l.startswith("Error ")) and not EXPECTED_ERRORS.search(l)]
        ck(not bad, f"app errors: {bad[:5]}")
        warns = sorted({l for l in godot_out.splitlines() if l.startswith("WARNING") and not EXPECTED_ERRORS.search(l)})
        if warns:
            ck.notes.append(f"app warnings: {warns[:5]}")
    for name, rc in rcs.items():
        ck(rc == 0, f"{name} exited {rc if rc is not None else 'only when killed'}")
    return tl


def run_once(a) -> int:
    t_start = time.monotonic()
    godot = None if a.no_godot else find_godot(a.godot)
    tmp = Path(tempfile.mkdtemp(prefix="app_e2e_"))
    port, http = free_port(), free_port()
    url = f"ws://127.0.0.1:{port}"
    print(f"workdir {tmp}  hub {url}", flush=True)
    songs = Path(a.songs)
    song = load_song(songs, a.song)
    takes = make_takes(tmp, song.sheet, [(a.seed, a.drift_sd), (1, 0.003)])
    procs: dict[str, Proc] = {}
    rcs: dict[str, int | None] = {}
    ck = Check()
    console = None
    t_cmd = time.monotonic()
    try:
        procs["nqmt"] = Proc("nqmt", [PY, "-m", "nqmt", "--mic", "file:" + ",".join(map(str, takes)),
                                      "--file-origin", str(-LEAD_S), "--port", str(port), "--http-port", str(http),
                                      "--songs", str(songs), "--record-dir", str(tmp / "rec")], tmp)
        if not ck(procs["nqmt"].wait_for(r"nqmt ready", 40), "nqmt did not come up"):
            raise RuntimeError
        if godot:
            procs["godot"] = Proc("godot", [godot, "--headless", "--path", str(ROOT / "quest_app"), "--", "--game",
                                            "--fletcher", url, "--no-intro", "--fletcher-log", "--game-log",
                                            "--fletcher-quit-on-close"], tmp)
            if not ck(procs["godot"].wait_for(r"\[FletcherHub\] connected", 60), "the app did not connect"):
                raise RuntimeError
            time.sleep(2.0)
        console = Console(url)
        t_cmd = time.monotonic()
        console.send("session.command", {"action": "start", "song_id": a.song, "from_bar": 1})
        res = console.wait("session.result", 180)
        ck(res is not None, "no session.result within 180 s")
        time.sleep(1.5)  # the results page; Fletcher's end-of-song bark
        console.send("session.command", {"action": "quit"})
        console.wait("session.state", 5, state="idle")  # checked from the recording
        time.sleep(1.0)
    except RuntimeError:
        pass
    finally:
        if console:
            console.close()
        if "nqmt" in procs:
            rcs["nqmt"] = procs["nqmt"].stop(30)
        if "godot" in procs:  # quits when the hub closes its socket
            rcs["godot"] = procs["godot"].join(15)

    recs = list((tmp / "rec").glob("*.jsonl"))
    rec = read_recording(tmp / "rec") if recs else []
    gout = procs["godot"].text() if "godot" in procs else ""
    glog = read_godot(gout.splitlines())
    tl = analyse(rec, glog, gout, rcs, ck, song, t_cmd, godot is not None) if rec else []
    if not rec:
        ck(False, "no hub recording")
    t_ref = next((m["t"] for m in rec if m.get("type") == "session.command"
                  and (m.get("data") or {}).get("action") == "start"), rec[0]["t"] if rec else 0.0)
    print("\n=== app e2e timeline (s from the console's start) ===")
    for t, txt in sorted(tl):
        print(f"{t - t_ref:8.3f}  {txt}")
    print(f"\nhub frames recorded: {len(rec)}; app log lines: {len(glog)}; exit codes: {rcs}")
    for n in ck.notes:
        print("note:", n)
    print(f"wall time {time.monotonic() - t_start:.1f} s; logs and recording in {tmp}")
    print("PASS" if not ck.problems else "FAIL:\n  " + "\n  ".join(ck.problems), flush=True)
    if not ck.problems and not a.keep:
        shutil.rmtree(tmp, ignore_errors=True)
    return 0 if not ck.problems else 1


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--godot", help="Godot 4.5 binary (default: $GODOT, godot on PATH, /Applications)")
    p.add_argument("--no-godot", action="store_true", help="laptop side only (no headset app)")
    p.add_argument("--songs", default=str(SONGS), help="song library (default: the fixture library)")
    p.add_argument("--song", default=SONG_ID)
    p.add_argument("--seed", type=int, default=5, help="the sloppy take: Chris's drift simulator seed")
    p.add_argument("--drift-sd", type=float, default=0.07)
    p.add_argument("--runs", type=int, default=1)
    p.add_argument("--keep", action="store_true", help="keep the temp dir (logs, recording) on PASS too")
    a = p.parse_args()
    fails = sum(run_once(a) for _ in range(a.runs))
    if a.runs > 1:
        print(f"\n{a.runs - fails}/{a.runs} runs passed")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
