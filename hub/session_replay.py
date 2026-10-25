"""Closed-loop replay of a real headset session, offline: the drummer's recorded onsets through
the session controller + session bridge + Fletcher's brain, on a virtual laptop clock.

    python -m hub.session_replay recordings/20261025-002801.jsonl \\
        recordings/nqmt-20261025-002801/mic_01_quest.drums.txt [--from-run 8] [--runs 3]

The drummer is replayed relative to each count-in: run k of the replay gets the onsets recorded
around the k-th count-in of the session (from 6 s before its t0 to 6 s before the next one),
shifted onto the replay's own t0, and each line arrives `line_delay_s` after its hit (the Quest
mic's measured ~240 ms). The cutoff sequence is main.py's, timed like the real one (restart t0 =
cutoff + `cut_to_t0_s`, the restart command 4 beats before). It can't know what he would have
played after the session diverges, so onsets past the recording are silence.
Pure Python, no hub, no network: `replay()` returns the event timeline.
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

from fletcher import brain as B
from fletcher.adapt import Adapter
from fletcher.config import Config
from hub.bridge import SessionBridge
from hub.session import Session

ROOT = Path(__file__).resolve().parent.parent


def load_recording(jsonl: str | Path) -> tuple[str, list[float]]:
    """(song_id, the controller's count_in t0s) from a hub recording (mic.pcm lines skipped)."""
    song, t0s = "", []
    with open(jsonl) as fh:
        for line in fh:
            if '"mic.pcm"' in line:
                continue
            m = json.loads(line)
            d = m.get("data") or {}
            if m.get("type") == "session.state" and m.get("src") == "session" and d.get("state") == "count_in":
                song = song or str(d.get("song_id") or "")
                t0s.append(float(d["t0"]))
    return song, t0s


def load_onsets(path: str | Path) -> list[float]:
    return [float(x.split()[0]) for x in Path(path).read_text().splitlines()
            if x.strip() and not x.startswith("#")]


def runs_from(onsets: list[float], t0s: list[float], pre_s: float = 6.0) -> list[list[float]]:
    """Each recorded run's onsets relative to its t0."""
    out = []
    for k, t0 in enumerate(t0s):
        end = t0s[k + 1] - pre_s if k + 1 < len(t0s) else float("inf")
        out.append([o - t0 for o in onsets if t0 - pre_s <= o < end])
    return out


def replay(song_id: str, runs: list[list[float]], songs: str | Path = ROOT / "songs", cfg: Config | None = None,
           line_delay_s: float = 0.24, cut_to_t0_s: float = 9.0, dt: float = 0.05, max_s: float = 900.0,
           seed: int = 0) -> list[tuple[float, str, str]]:
    """[(t, who, what)] from song start (t = 0 at the start command)."""
    cfg = cfg or Config(dry_run=True, lines_dir="")
    ses = Session(songs)
    sb = SessionBridge(songs, "laptop", 0.17, 0.0)
    brain = B.Brain(cfg, random.Random(seed))
    adapter = Adapter(cfg.error_sign, cfg.beats_per_bar)
    log: list[tuple[float, str, str]] = []
    lines: list[tuple[float, str]] = []  # (arrival, text)
    timers: list[tuple[float, str, dict]] = []  # Fletcher's scheduled commands
    state = {"run": -1, "t": 0.0, "notes": {}}

    def say(who: str, what: str) -> None:
        log.append((round(state["t"], 2), who, what))

    def to_brain(typ: str, data: dict, src: str) -> None:
        msg = adapter.normalize({"type": typ, "src": src, "data": data})
        if msg is None:
            return
        fn = {"session.state": brain.on_session_state, "judgment.note": brain.on_note,
              "session.result": brain.on_result}.get(msg["type"])
        if fn:
            effects(fn(msg["data"], state["t"]))

    def publish(msgs, src: str) -> None:
        for typ, data in msgs:
            route(typ, data, src)

    def route(typ: str, data: dict, src: str) -> None:
        t = state["t"]
        if typ == "session.state":
            st = data.get("state")
            if src == "session":
                if st == "count_in":
                    if data.get("reason") == "start":
                        brain.reset()
                    state["run"] += 1
                    k = state["run"]
                    t0 = float(data["t0"])
                    say("session", f"count_in ({data.get('reason')}) bar {data.get('from_bar')}, t0 = +{t0:.2f}")
                    if k < len(runs):
                        for o in runs[k]:
                            lines.append((t0 + o + line_delay_s, f"{t0 + o:.6f}  0  onset"))
                        lines.sort()
                elif st in ("idle", "finished"):
                    say("session", st)
                publish(sb.on_state(data, src, t), "chris")
            else:
                publish(ses.on_state(data, src, t), "session")
            to_brain(typ, data, src)
        elif typ == "judgment.note":
            ses.on_note(data, t)
            key = (state["run"], data.get("bar"))
            state["notes"].setdefault(key, {}).setdefault(data.get("grade"), 0)
            state["notes"][key][data.get("grade")] += 1
            to_brain(typ, data, src)
        elif typ == "session.command":
            publish(ses.on_command(data, src, t), "session")
            publish(sb.on_command(data, t), "chris")
        elif typ == "session.result":
            say("session", "result: " + ", ".join(f"{k} {data.get(k)}" for k in
                                                  ("completed", "cutoffs", "bars_played", "perfect", "good", "off",
                                                   "miss", "extra", "accuracy", "ended_by") if k in data))
            to_brain(typ, data, src)

    def bark(category: str) -> None:
        say("fletcher", f"bark {category}")
        sb.on_speech({"clip_id": {"stop": "stop_1", "waiting": "watch_1"}.get(category, "no_1")}, "aaron",
                     state["t"])

    def effects(effs: list) -> None:
        t = state["t"]
        for e in effs:
            if isinstance(e, B.Bark):
                bark(e.category)
            elif isinstance(e, B.Cutoff):
                f = e.facts
                say("fletcher", f"CUTOFF #{f.cutoff_number} at bar {f.current_bar}: {f.kind} since bar {f.since_bar}"
                                f"{' (LAST: the song ends)' if e.final else f', restart from bar {f.from_bar}'}"
                                f" | {f.detail}")
                bark("stop")
                route("session.command", {"action": "stop", "from_bar": f.from_bar}, "aaron")
                if e.final:
                    timers.append((t + 8.0, "quit", {"action": "quit", "reason": "cutoffs"}))
                else:
                    t0 = t + cut_to_t0_s
                    beat = 60.0 / (brain.bpm or 120.0)
                    timers.append((t0 - 4 * beat, "restart", {"action": "restart", "from_bar": f.from_bar,
                                                              "t0": round(t0, 4)}))
            elif isinstance(e, B.EndSong):
                say("fletcher", f"END SONG at bar {e.bar}: {e.reason}")
                effects(brain.on_ended())
                route("session.command", {"action": "quit", "reason": e.reason}, "aaron")

    route("session.command", {"action": "start", "song_id": song_id, "from_bar": 1}, "quest")
    while state["t"] < max_s:
        state["t"] += dt
        t = state["t"]
        for item in [x for x in timers if x[0] <= t]:
            timers.remove(item)
            _, what, data = item
            route("session.command", data, "aaron")
            if what == "restart":
                effects(brain.on_restarted(int(data["from_bar"])))
        while lines and lines[0][0] <= t:
            rx, text = lines.pop(0)
            publish(sb.feed_line(text, rx), "chris")
        publish(sb.tick(t), "chris")
        publish(ses.tick(t), "session")
        effects(brain.tick(t))
        if ses.state == "idle" and ses.song is None and state["run"] >= 0 and not timers:
            break
    state["log_notes"] = state["notes"]
    log.append((round(state["t"], 2), "replay", f"bars judged per run: {_bars_summary(state['notes'])}"))
    return log


def _bars_summary(notes: dict) -> str:
    runs: dict[int, list] = {}
    for (run, bar), g in sorted(notes.items(), key=lambda kv: (kv[0][0], kv[0][1] or 0)):
        hits = sum(v for k, v in g.items() if k in ("perfect", "good", "off"))
        runs.setdefault(run + 1, []).append(f"{bar}:{hits}h/{g.get('miss', 0)}m")
    return "; ".join(f"run {r} [{' '.join(v[:12])}{' ...' if len(v) > 12 else ''}]" for r, v in runs.items())


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("recording", help="hub recording (recordings/<time>.jsonl)")
    p.add_argument("onsets", help="the transcriber's lines (recordings/nqmt-<time>/mic_*.drums.txt, laptop clock)")
    p.add_argument("--from-run", type=int, default=1, help="start at this recorded run (1 = the song start)")
    p.add_argument("--runs", type=int, default=0, help="replay only this many recorded runs (0 = all)")
    p.add_argument("--songs", default=str(ROOT / "songs"))
    a = p.parse_args(argv)
    song, t0s = load_recording(a.recording)
    runs = runs_from(load_onsets(a.onsets), t0s)[a.from_run - 1:]
    if a.runs:
        runs = runs[:a.runs]
    for t, who, what in replay(song, runs, a.songs):
        print(f"{t:8.2f}  {who:8s} {what}")


if __name__ == "__main__":
    main()
