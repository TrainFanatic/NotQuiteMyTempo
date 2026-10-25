"""Bark gallery stream: the band plays at 94 bpm while Fletcher (main.Fletcher._send_clip, the
real cue path through cues.BarkCues) barks one clip every GAP seconds. Writes the same
{"rx", "msg"} rows as record_e2e.py. No network, no API."""
import asyncio, json, sys
sys.path.insert(0, "/Users/aaronwang/NotQuiteMyTempo")
from fletcher.config import Config
from fletcher.main import Fletcher
from fletcher.barks import BarkPicker

OUT = sys.argv[1]
BPM, GAP, T0 = 94.0, 4.6, 2.0
CLIPS = [("rushing_3", "rushing"), ("dragging_2", "dragging"), ("bar_09", "bar"),
         ("no_1", "no"), ("dragging_3", "dragging"), ("watch_1", "watch"), ("praise_1", "praise"), ("again_1", "again"), ("stop_1", "stop")]
rows, clock = [], [0.0]

async def main():
    f = Fletcher(Config.from_env(dry_run=True, lines_dir=""))
    async def send(typ, data):
        rows.append({"rx": clock[0], "msg": {"type": typ, "src": "aaron", "t": clock[0], "data": data}})
    f.hub.send = send
    f.brain.phase, f.brain.bpm = "playing", BPM
    f.brain.last_sent = ("calm", "conduct", "drummer")  # what the brain sent last while the band plays
    def ref(t, typ, d):
        rows.append({"rx": t, "msg": {"type": typ, "src": "chris", "t": t, "data": d}})
    ref(0.5, "session.state", {"state": "playing", "bpm": BPM, "bar": 1, "beat": 1})
    clock[0] = 0.5; await f.send_state("calm", 0.1, "conduct")
    texts = {c: d["text"] for c, d in BarkPicker().manifest.items()}
    for i, (clip, cat) in enumerate(CLIPS):
        clock[0] = T0 + i * GAP
        await f._send_clip(clip, texts.get(clip, clip), category=cat)
    end = T0 + len(CLIPS) * GAP
    ref(end, "session.state", {"state": "stopped", "bpm": BPM})
    rows.sort(key=lambda r: r["rx"])
    with open(OUT, "w") as fh:
        for r in rows: fh.write(json.dumps(r) + "\n")
    for r in rows: print(round(r["rx"], 2), r["msg"]["type"], {k: v for k, v in r["msg"]["data"].items() if k != "sample_rate"})

asyncio.run(main())
