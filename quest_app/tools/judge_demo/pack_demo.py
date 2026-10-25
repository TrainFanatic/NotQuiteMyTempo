#!/usr/bin/env python3
"""Bundle a recorded session (record_demo.py's output) into the app as the judge demo.

    python3 tools/judge_demo/pack_demo.py RECORD_DIR

writes res://demo/caravan/:
  session.jsonl    the hub frames the headset received, in order: the headset's own frames
                   (src quest) are dropped, and the one streamed line (a dry-run tone, since the
                   recording had no voice keys) becomes two of Fletcher's real barks
  take0.wav ...    what the drummer played (16-bit mono 48 kHz), one per count_in (the file mic's takes)
  demo.json        song id, the takes' origin (song time of sample 0)
  song/            the song's library files, copied into the song cache before the replay
Fails on anything unexpected rather than packing a demo that would replay wrong.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
APP = HERE.parent.parent
OUT = APP / "demo" / "caravan"
SONG = HERE / "songs" / "caravan-head"
# the streamed line after the cutoff -> real barks (clip ids from fletcher/barks), in order,
# each this many seconds after the line started
LINE_BARKS = [("rushing_2", "You're rushing.", 0.0), ("bar_01", "From bar one.", 1.45)]


def fail(msg: str) -> None:
    print(f"pack_demo FAILED: {msg}", file=sys.stderr)
    sys.exit(1)


def main() -> None:
    if len(sys.argv) != 2:
        fail("usage: pack_demo.py RECORD_DIR")
    rec = Path(sys.argv[1])
    frames = [json.loads(l) for l in (rec / "session.jsonl").read_text().splitlines() if l.strip()]
    if not frames:
        fail("empty recording")
    streamed = [f for f in frames if f["type"] == "fletcher.speech" and f["data"].get("stream")]
    if len(streamed) != 1:
        fail(f"expected one streamed line, found {len(streamed)}")
    line = streamed[0]
    uid = line["data"]["utterance_id"]
    out = []
    for f in frames:
        if f.get("src") == "quest":
            continue
        if f["type"] == "fletcher.audio" and f["data"].get("utterance_id") == uid:
            continue
        if f is line:
            for k, (clip, text, dt) in enumerate(LINE_BARKS):
                g = json.loads(json.dumps(f))
                g["t"] = f["t"] + dt
                g["data"] = {"utterance_id": uid if k == 0 else f"{uid}-{k}", "text": text, "clip_id": clip,
                             "sample_rate": f["data"]["sample_rate"]}
                out.append(g)
            continue
        out.append(f)
    out.sort(key=lambda f: f["t"])
    count_ins = [f for f in out if f["type"] == "session.state" and f.get("src") == "session"
                 and f["data"].get("state") == "count_in"]
    takes = sorted(rec.glob("take*.wav"))
    if len(count_ins) != len(takes):
        fail(f"{len(count_ins)} count_ins but {len(takes)} takes")
    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "song").mkdir(parents=True)
    (OUT / "session.jsonl").write_text("".join(json.dumps(f) + "\n" for f in out))
    for k, w in enumerate(takes):
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(w), "-ac", "1", "-ar", "48000",
                        "-c:a", "pcm_s16le", str(OUT / f"take{k}.wav")], check=True)
    for f in ("song.json", "drums.drums.txt", "drums.tempo.json"):
        shutil.copy(SONG / f, OUT / "song" / f)
    origin = float((rec / "take_origin.txt").read_text())
    song = json.loads((SONG / "song.json").read_text())
    (OUT / "demo.json").write_text(json.dumps({"song_id": song["id"], "take_origin_s": origin,
                                               "takes": len(takes)}, indent=1) + "\n")
    span = out[-1]["t"] - out[0]["t"]
    print(f"packed {len(out)} frames ({span:.1f} s), {len(takes)} takes, song {song['id']} into {OUT}")


if __name__ == "__main__":
    main()
