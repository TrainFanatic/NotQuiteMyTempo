"""Record one real session for the headset's judge demo: the whole laptop stack (hub, library,
session, bridge, file-mic drummer, Fletcher with barks) and headless Godot in game mode, as in
hub.app_e2e, on Caravan with a sloppy take that gets cut off and a clean take that finishes.

    .venv/bin/python quest_app/tools/judge_demo/record_demo.py OUT_DIR     (from the team repo root)

OUT_DIR gets session.jsonl (the hub recording), take0.wav / take1.wav (what the drummer played),
take_origin.txt (song time of sample 0) and the timeline check. Fails if the session didn't run
the way the demo needs: one cutoff, a restart, a finished song and a result.
"""

import argparse
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]   # the team repo: quest_app/tools/judge_demo/<this>
if not (ROOT / "hub").is_dir() or not (ROOT / "quest_app").is_dir():
    sys.exit(f"record_demo: run this from the team repo's quest_app/tools/judge_demo (no hub/ or quest_app/ at {ROOT})")
sys.path.insert(0, str(ROOT))

from fletcher.dev.mini_hub import free_port  # noqa: E402
from hub.app_e2e import LEAD_S, Console, analyse, make_takes  # noqa: E402
from hub.full_e2e import Check, Proc, find_godot, read_godot, read_recording  # noqa: E402
from hub.session import load_song  # noqa: E402

PY = str(ROOT / ".venv/bin/python")
MENU_HOLD_S = 6.0
RESULT_HOLD_S = 10.0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("out_dir")
    p.add_argument("--songs", default=str(Path(__file__).with_name("songs")))
    p.add_argument("--song", default="caravan-head")
    p.add_argument("--seed", type=int, default=5)
    p.add_argument("--drift-sd", type=float, default=0.07)
    p.add_argument("--godot", help="Godot 4.5 binary (default: $GODOT, godot on PATH, /Applications)")
    a = p.parse_args()
    godot = find_godot(a.godot)
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="record_demo_"))
    port, http = free_port(), free_port()
    url = f"ws://127.0.0.1:{port}"
    song = load_song(Path(a.songs), a.song)
    takes = make_takes(tmp, song.sheet, [(a.seed, a.drift_sd), (1, 0.003)])
    procs, rcs, ck = {}, {}, Check()
    console = None
    t_cmd = time.monotonic()
    try:
        procs["nqmt"] = Proc("nqmt", [PY, "-m", "nqmt", "--mic", "file:" + ",".join(map(str, takes)),
                                      "--no-fletcher-voice",
                                      "--file-origin", str(-LEAD_S), "--port", str(port), "--http-port", str(http),
                                      "--songs", a.songs, "--record-dir", str(tmp / "rec")], tmp)
        if not ck(procs["nqmt"].wait_for(r"nqmt ready", 60), "nqmt did not come up"):
            raise RuntimeError
        procs["godot"] = Proc("godot", [godot, "--headless", "--path", str(ROOT / "quest_app"),
                                        "--", "--game", "--fletcher", url, "--no-intro", "--fletcher-log",
                                        "--game-log", "--fletcher-quit-on-close"], tmp)
        if not ck(procs["godot"].wait_for(r"\[FletcherHub\] connected", 90), "the app did not connect"):
            raise RuntimeError
        time.sleep(MENU_HOLD_S)
        console = Console(url)
        t_cmd = time.monotonic()
        console.send("session.command", {"action": "start", "song_id": a.song, "from_bar": 1})
        res = console.wait("session.result", 300)
        ck(res is not None, "no session.result within 300 s")
        time.sleep(RESULT_HOLD_S)
        console.send("session.command", {"action": "quit"})
        time.sleep(1.5)
    except RuntimeError:
        pass
    finally:
        if console:
            console.close()
        if "nqmt" in procs:
            rcs["nqmt"] = procs["nqmt"].stop(30)
        if "godot" in procs:
            rcs["godot"] = procs["godot"].join(30)
    recs = sorted((tmp / "rec").glob("*.jsonl"))
    rec = read_recording(tmp / "rec") if recs else []
    gout = procs["godot"].text() if "godot" in procs else ""
    tl = analyse(rec, read_godot(gout.splitlines()), gout, rcs, ck, song, t_cmd, True) if rec else []
    stops = [m for m in rec if m.get("type") == "session.command" and (m.get("data") or {}).get("action") == "stop"]
    ck(len(stops) == 1, f"the demo needs exactly one cutoff, got {len(stops)}")
    if recs:
        shutil.copy(recs[0], out / "session.jsonl")
    for k, w in enumerate(takes):
        shutil.copy(w, out / f"take{k}.wav")
    (out / "take_origin.txt").write_text(f"{-LEAD_S}\n")
    t_ref = next((m["t"] for m in rec if m.get("type") == "session.command"
                  and (m.get("data") or {}).get("action") == "start"), rec[0]["t"] if rec else 0.0)
    lines = [f"{t - t_ref:8.3f}  {txt}" for t, txt in sorted(tl)]
    (out / "timeline.txt").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    print("PASS" if not ck.problems else "FAIL:\n  " + "\n  ".join(ck.problems), flush=True)
    return 0 if not ck.problems else 1


if __name__ == "__main__":
    sys.exit(main())
