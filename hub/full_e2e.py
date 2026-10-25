"""The whole system as separate processes, the way the demo runs it, no API calls:

    hub.server (records to a temp dir)  <-  python -m fletcher --dry-run
        ^                               <-  Godot --headless --path quest_app (Adam's app, Terence + voice)
        |
    kit audio of a drifting take -> Kevin's transcribe_wav.py --onsets-only --realtime | hub.bridge

The take is Chris's 4-bar fixture looped to 16 bars, played with heavy drift so Fletcher cuts off.
Checks, from the hub recording and the app's `--fletcher-log` lines (laptop clock):
playing -> judgments -> barks and their cued moves -> stop + Stop bark with the fist on the word ->
tirade with gesture moves -> count-off finger -> restart(t0) -> bridge count_in(t0) -> count-off
words on the beats; no script errors in the app; every process exits 0. Prints a timeline, PASS/FAIL.

    python -m hub.full_e2e [--godot PATH] [--seed 5 --drift-sd 0.07] [--keep]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import numpy as np

from fletcher.config import Config
from fletcher.cues import COUNTOFF, COUNTOFF_LEAD_BEATS, FIST, gesture_move
from fletcher.dev.mini_hub import free_port

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
LT = ROOT / "live_transcription"
FIXTURE = ROOT / "datacollection" / "tests" / "fixtures" / "back_in_black_4bars.drums.txt"
COUNTOFF_MANIFEST = ROOT / "fletcher" / "countoff" / "manifest.json"
BPM = 94.0
BARS = 16
LEAD_S = 1.0  # audio starts this long before song time 0
GODOT_DEFAULTS = ["/Applications/Godot.app/Contents/MacOS/Godot"]
# The desktop preview asks for OpenXR and falls back to normal mode: expected, not a failure.
EXPECTED_ERRORS = re.compile(r"OpenXR|openxr")
LOG = re.compile(r"^\[fletcher-log\] (\S+) (\S+) ?(.*)$")
_CFG = Config()
LEAD_CAP = _CFG.bark_lead_max_s
LEAD_CAP_FIST = getattr(_CFG, "cutoff_lead_max_s", LEAD_CAP)
FIST_TOL_S = 0.15
FINGER_TOL_S = 0.15
WORD_TOL_S = 0.1


# ------------------------------------------------------------------ the take ---

def make_take(d: Path, seed: int, drift_sd: float) -> tuple[Path, Path]:
    """The 4-bar fixture looped to BARS bars (the sheet) and the kit audio of a drifting take of it."""
    import soundfile as sf
    sys.path[:0] = [str(ROOT / "datacollection"), str(ROOT / "rushingOrDragging" / "tests")]
    import sim_session as ss
    import test_drift as td
    from hub.bridge import cp

    bar = 4 * 60 / BPM
    notes = cp.read_notes(FIXTURE)
    loops = BARS // 4
    sheet = d / "back_in_black_16bars.drums.txt"
    sheet.write_text("\n".join(["# time_s  midi  name"] + [f"{h.time + k * 4 * bar:.6f}  {h.midi}  {h.name}"
                                                          for k in range(loops) for h in notes]) + "\n")
    truth = td.make_drifting(sheet, d / "perf.drums.txt", seed=seed, drift_sd=drift_sd)
    events = [(t, m, 4) for t, m in sorted(truth)]
    n = int((max(t for t, _ in truth) + LEAD_S + 2.0) * 48000)
    x = ss.render(events, 48000, n, lambda t, m: t + LEAD_S)
    x = 0.8 * x / np.abs(x).max() + 10 ** (-70 / 20) * np.random.default_rng(1).standard_normal(n)
    wav = d / "take.wav"
    sf.write(wav, x.astype(np.float32), 48000, subtype="FLOAT")
    return sheet, wav


# ------------------------------------------------------------------ processes ---

class Proc:
    """A child process whose output is pumped (stamped with arrival time) into a list and a log file."""

    def __init__(self, name: str, cmd: list[str], logdir: Path, cwd: Path = ROOT, stdin=None,
                 pipe_out: bool = False):
        """pipe_out: stdout is left unread for the next process; only stderr is logged."""
        self.name, self.lines = name, []  # [(arrival monotonic, line)]
        self.log = (logdir / f"{name}.log").open("w", buffering=1)
        self.log.write("$ " + " ".join(cmd) + "\n")
        env = {**os.environ, "PYTHONUNBUFFERED": "1"}
        self.p = subprocess.Popen(cmd, cwd=cwd, stdin=stdin, text=True, env=env, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE if pipe_out else subprocess.STDOUT)
        streams = [self.p.stderr] if pipe_out else [self.p.stdout]
        self.threads = [threading.Thread(target=self._pump, args=(s,), daemon=True) for s in streams if s]
        for t in self.threads:
            t.start()

    def _pump(self, stream) -> None:
        for line in stream:
            line = line.rstrip("\n")
            self.lines.append((time.monotonic(), line))
            self.log.write(line + "\n")

    def wait_for(self, pattern: str, timeout: float) -> bool:
        rx, end = re.compile(pattern), time.monotonic() + timeout
        while time.monotonic() < end:
            if any(rx.search(l) for _, l in list(self.lines)):
                return True
            if self.p.poll() is not None:
                return False
            time.sleep(0.05)
        return False

    def stop(self, timeout: float = 10.0) -> int | None:
        """SIGINT (Ctrl-C), then wait; None = had to be killed."""
        if self.p.poll() is None:
            self.p.send_signal(signal.SIGINT)
        return self.join(timeout)

    def join(self, timeout: float) -> int | None:
        try:
            rc = self.p.wait(timeout)
        except subprocess.TimeoutExpired:
            self.p.kill()
            self.p.wait()
            rc = None
        for t in self.threads:
            t.join(2)
        self.log.close()
        return rc

    def text(self) -> str:
        return "\n".join(l for _, l in self.lines)


def find_godot(arg: str | None) -> str:
    for c in [arg, os.environ.get("GODOT"), shutil.which("godot"), shutil.which("Godot"), *GODOT_DEFAULTS]:
        if c and Path(c).exists():
            return c
    raise SystemExit("no Godot 4.5 binary: pass --godot PATH or set GODOT")


# ------------------------------------------------------------------ analysis ---

def read_recording(record_dir: Path) -> list[dict]:
    files = sorted(record_dir.glob("*.jsonl"))
    out = []
    for f in files:
        for line in f.read_text().splitlines():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return sorted(out, key=lambda m: m.get("t", 0.0))


def read_godot(lines: list[str]) -> list[tuple[float, str, str]]:
    out = []
    for l in lines:
        m = LOG.match(l)
        if m and re.fullmatch(r"-?\d+(\.\d+)?", m.group(1)):
            out.append((float(m.group(1)), m.group(2), m.group(3)))
    return out


class Check:
    def __init__(self):
        self.problems: list[str] = []
        self.notes: list[str] = []

    def __call__(self, ok: bool, msg: str) -> bool:
        if not ok:
            self.problems.append(msg)
        return ok


def analyse(rec: list[dict], glog: list[tuple[float, str, str]], godot_out: str, rcs: dict,
            ck: Check) -> list[tuple[float, str]]:
    tl: list[tuple[float, str]] = []  # (laptop t, text) for the timeline
    d = lambda m: m.get("data") or {}  # noqa: E731
    of = lambda typ, src=None, **kv: [m for m in rec if m.get("type") == typ  # noqa: E731
                                     and (src is None or m.get("src") == src)
                                     and all(d(m).get(k) == v for k, v in kv.items())]
    gl = lambda ev, arg=None: [(t, a) for t, e, a in glog if e == ev and (arg is None or a == arg)]  # noqa: E731

    playing = of("session.state", "chris", state="playing")
    notes = of("judgment.note", "chris")
    stops = of("session.command", "aaron", action="stop")
    restarts = of("session.command", "aaron", action="restart")
    count_ins = of("session.state", "chris", state="count_in")
    speech = of("fletcher.speech", "aaron")
    barks = [m for m in speech if d(m).get("clip_id")]
    lines = [m for m in speech if d(m).get("stream")]
    cues = [m for m in of("fletcher.state", "aaron") if "gesture" in d(m)]
    started = gl("move_started")

    if not ck(bool(playing), "bridge never sent session.state playing"):
        return tl
    t_play = playing[0]["t"]
    tl.append((t_play, f"bridge: session playing (bar {d(playing[0]).get('bar')})"))
    ck(len(notes) >= 20, f"only {len(notes)} judgment.note from the bridge")
    if not ck(bool(stops), "Fletcher never cut off (no session.command stop)"):
        return tl
    stop = stops[0]
    t_stop = stop["t"]
    before = [n for n in notes if n["t"] < t_stop]
    errs = [abs(d(n)["error_ms"]) for n in before if d(n).get("error_ms") is not None]
    grades = {g: sum(d(n).get("grade") == g for n in before) for g in ("perfect", "good", "off", "miss", "extra")}
    tl.append((before[0]["t"] if before else t_play,
               f"judgments: {len(before)} before the cutoff, mean |err| {np.mean(errs) if errs else 0:.0f} ms, {grades}"))
    ck(len(before) >= 20, f"only {len(before)} judgments before the cutoff")

    # moves Terence was in at time t (for "already playing counts as done")
    def current_move(t: float) -> str | None:
        cur = None
        for ts, a in started:
            if ts > t:
                break
            cur = a
        return cur

    def speaking(uid: str) -> float | None:
        s = gl("speaking", uid)
        return s[0][0] if s else None

    def finished(uid: str) -> float | None:
        s = gl("finished", uid)
        return s[0][0] if s else None

    claimed: set[int] = set()

    def find_start(move: str, after: float, before_t: float) -> float | None:
        for i, (ts, a) in enumerate(started):
            if a == move and after <= ts <= before_t and i not in claimed:
                claimed.add(i)
                return ts
        return None

    # barks: each one sounded on the headset; their cued moves played
    for b in barks:
        uid, clip = d(b)["utterance_id"], d(b)["clip_id"]
        sp = speaking(uid)
        pre = "pre-cutoff " if b["t"] < t_stop else ""
        tl.append((b["t"], f"Fletcher: {pre}bark {uid} {clip} \"{d(b).get('text', '')}\""))
        nxt = next((o for o in barks if o["t"] >= b["t"] and o is not b), None)
        if sp is None and nxt is not None and nxt["t"] - b["t"] < 0.35 and finished(uid) is not None:
            ck.notes.append(f"bark {uid} ({clip}) was cut off by {d(nxt)['utterance_id']} "
                            f"{(nxt['t'] - b['t']) * 1000:.0f} ms later, before it sounded")
            continue
        if ck(sp is not None, f"bark {uid} ({clip}) never sounded in the app"):
            tl.append((sp, f"app: {uid} speaking (+{(sp - b['t']) * 1000:.0f} ms after the hub frame)"))
    ck(any(d(b)["clip_id"] for b in barks), "no bark (fletcher.speech clip_id)")

    stop_bark = next((b for b in barks if b["t"] >= t_stop), None)
    tl.append((t_stop, f"Fletcher: session.command stop from bar {d(stop).get('from_bar')}"))
    stopped = [m for m in of("session.state", "chris", state="stopped") if m["t"] >= t_stop]
    if ck(bool(stopped), "bridge never answered stop with session.state stopped"):
        tl.append((stopped[0]["t"], "bridge: session stopped"))

    finger = next((c for c in cues if d(c).get("gesture") == COUNTOFF), None)
    line = next((l for l in lines if l["t"] >= t_stop), None)
    t_line_uid = d(line)["utterance_id"] if line else None
    missing, dropped, moot, ok = [], [], [], 0
    t_end = glog[-1][0] if glog else 0.0
    line_moves: list[str] = []
    drops = [(t, a.split()[0], float(a.split()[1])) for t, a in gl("move_dropped")]
    for c in cues:
        cd = d(c)
        g, uid, move = cd["gesture"], cd.get("utterance_id"), gesture_move(cd["gesture"])
        sp, fin = speaking(uid), finished(uid)
        if "t0" in cd:
            bpm = float(cd.get("bpm") or BPM)
            want = cd["t0"] - COUNTOFF_LEAD_BEATS * 60.0 / bpm
            if want > t_end - 0.5:
                ck.notes.append(f"count-off finger due after the app stopped logging; not checked")
                continue
            got = find_start(move, want - 1.0, want + 2.0)
            if ck(got is not None, f"count-off finger never started (due {want - t_play:.2f})"):
                tl.append((got, f"app: {move} started ({(got - want) * 1000:+.0f} ms vs t0 - "
                                f"{COUNTOFF_LEAD_BEATS} beats)"))
                ck(abs(got - want) <= FINGER_TOL_S, f"finger {(got - want) * 1000:+.0f} ms off t0 - 5 beats")
            continue
        if "at_s" in cd and sp is not None:
            # the headset holds a bark back at most bark_lead_max_s (the fist: cutoff_lead_max_s, if set)
            cap = LEAD_CAP_FIST if move == FIST and int(cd.get("priority", 1)) >= 2 else LEAD_CAP
            want = sp + max(float(cd["at_s"]), -cap)
        elif "after_s" in cd and fin is not None:
            want = fin + float(cd["after_s"])
        else:
            want = sp if sp is not None else c["t"]
        what = f"{move} ({'fist' if move == FIST else uid})"
        if want > t_end - 0.5:
            ck.notes.append(f"{what} due after the app stopped logging; not checked")
            continue
        if "at_s" in cd and fin is not None and (sp is None or fin < want - 0.05):
            moot.append(what)  # its bark was cut off by the next one before the move was due
            continue
        lo = min(want, c["t"]) - 0.35
        hi = (fin if fin is not None else want) + 6.0
        got = find_start(move, lo, hi)
        if got is not None:
            ok += 1
            if move == FIST:
                tl.append((got, f"app: {FIST} started {(got - sp) * 1000 if sp else float('nan'):+.0f} ms "
                                f"from the Stop word (cue at_s {cd.get('at_s')}, expected "
                                f"{(want - sp) * 1000 if sp else float('nan'):+.0f} ms)"))
                ck(abs(got - want) <= FIST_TOL_S, f"fist started {(got - want) * 1000:+.0f} ms off its cue")
            elif uid == t_line_uid:
                line_moves.append(move)
                tl.append((got, f"app: tirade gesture {move} ({'in the line' if 'at_index' in cd else 'after it'})"))
        elif current_move(want) == move:
            ok += 1
            if uid == t_line_uid:
                line_moves.append(move)
        elif any(m == move and abs((t - late) - want) < 0.5 for t, m, late in drops):
            dropped.append(what)
        else:
            missing.append(what)
            ck(int(cd.get("priority", 1)) < 2, f"priority-2 move {what} never started")
    ck(not missing, f"cued moves that never played: {missing}")
    if moot:
        ck.notes.append(f"cues of barks cut off before they were due (dropped by design): {moot}")
    if dropped:
        ck.notes.append(f"cues dropped as stale by the link (allowed): {dropped}")
    ck(stop_bark is not None, "no Stop bark after the cutoff")
    ck(any(gesture_move(d(c)["gesture"]) == FIST for c in cues), "no fist cue")

    # the tirade
    if ck(line is not None, "no tirade line (fletcher.speech stream) after the cutoff"):
        uid = d(line)["utterance_id"]
        final = next((m for m in of("fletcher.audio", "aaron", utterance_id=uid) if d(m).get("final")), None)
        text = (final and d(final).get("text")) or d(line).get("text", "")
        tl.append((line["t"], f"Fletcher: tirade {uid}: \"{text}\""))
        sp, fin = speaking(uid), finished(uid)
        if ck(sp is not None and fin is not None, f"tirade {uid} did not play out in the app"):
            tl.append((sp, f"app: tirade speaking ({fin - sp:.1f} s)"))
        ck(any(t for t, a in gl("subtitle") if a.strip() == d(line).get("text", "").strip())
           or not d(line).get("text"), "tirade subtitle not shown")
        ck(bool(line_moves), "Terence played no gesture move for the tirade")
        n_in = sum(1 for c in cues if d(c).get("utterance_id") == uid and "at_index" in d(c))
        ck.notes.append(f"tirade {uid}: {n_in} in-line gesture cue(s), moves played {line_moves}")

    # restart and the count-off
    if not ck(bool(restarts), "no session.command restart"):
        return tl
    r = restarts[0]
    rt0 = d(r).get("t0")
    tl.append((r["t"], f"Fletcher: session.command restart from bar {d(r).get('from_bar')}, "
                       f"t0 {rt0 - t_play if rt0 else float('nan'):.3f}"))
    ck(isinstance(rt0, (int, float)), "restart has no t0")
    if finger:
        ck(rt0 is not None and abs(d(finger)["t0"] - rt0) < 1e-3, "restart t0 != count-off cue t0")
    ci = next((m for m in count_ins if m["t"] >= r["t"]), None)
    if ck(ci is not None, "bridge never answered restart with count_in"):
        tl.append((ci["t"], f"bridge: count_in t0 {d(ci).get('t0', float('nan')) - t_play:.3f}"))
        ck(rt0 is not None and abs(float(d(ci).get("t0", -1)) - rt0) < 1e-3,
           f"count_in t0 {d(ci).get('t0')} != restart t0 {rt0}")
    if finger and rt0:
        manifest = json.loads(COUNTOFF_MANIFEST.read_text())
        clips, bpm = d(finger).get("clips") or [], float(d(finger).get("bpm") or BPM)
        words = [(t, a) for t, a in gl("countoff_word") if t >= rt0 - 8 * 60 / bpm]
        ck(len(words) >= len(clips) and len(clips) > 0,
           f"app played {len(words)} count-off words, cue had {len(clips)}")
        for k, (clip, (t, a)) in enumerate(zip(clips, words)):
            onset = t + float(manifest.get(a, {}).get("onset_s", 0.0))
            want = rt0 - (len(clips) - k) * 60.0 / bpm
            tl.append((t, f"app: count-off {a}: word onset {(onset - want) * 1000:+.0f} ms vs beat {k - len(clips)}"))
            ck(a == clip, f"count-off word {k} is {a}, cue said {clip}")
            ck(abs(onset - want) <= WORD_TOL_S, f"count-off {a} {(onset - want) * 1000:+.0f} ms off its beat")
        tl.append((rt0, "t0: restart bar downbeat"))
    after = [n for n in notes if rt0 and n["t"] > rt0]
    ck.notes.append(f"{len(after)} judgments after the restart; {len(stops)} cutoff(s), {len(restarts)} restart(s)")

    # the app's own output
    bad = [l for l in godot_out.splitlines()
           if ("SCRIPT ERROR" in l or l.startswith("ERROR") or l.startswith("Error ")) and not EXPECTED_ERRORS.search(l)]
    ck(not bad, f"app errors: {bad[:5]}")
    warns = [l for l in godot_out.splitlines() if l.startswith("WARNING") and not EXPECTED_ERRORS.search(l)]
    if warns:
        ck.notes.append(f"app warnings: {sorted(set(warns))[:5]}")
    for name, rc in rcs.items():
        ck(rc == 0, f"{name} exited {rc if rc is not None else 'only when killed'}")
    return tl


# ------------------------------------------------------------------ main ---

def run(a) -> int:
    t_start = time.monotonic()
    godot = find_godot(a.godot)
    tmp = Path(tempfile.mkdtemp(prefix="full_e2e_"))
    port = a.port or free_port()
    url = f"ws://127.0.0.1:{port}"
    print(f"workdir {tmp}  hub {url}", flush=True)
    sheet, wav = make_take(tmp, a.seed, a.drift_sd)
    procs: dict[str, Proc] = {}
    rcs: dict[str, int | None] = {}
    ck = Check()
    try:
        procs["hub"] = Proc("hub", [PY, "-m", "hub.server", "--port", str(port), "--record-dir",
                                    str(tmp / "recordings")], tmp)
        ck(procs["hub"].wait_for("hub listening", 10), "hub did not start")
        procs["fletcher"] = Proc("fletcher", [PY, "-m", "fletcher", "--dry-run", "--hub", url], tmp)
        ck(procs["hub"].wait_for(r"\(1 connected\)", 15), "Fletcher did not connect")
        procs["godot"] = Proc("godot", [godot, "--headless", "--path", str(ROOT / "quest_app"), "--",
                                        "--fletcher", url, "--no-intro", "--fletcher-log",
                                        "--fletcher-quit-on-close"], tmp)
        ck(procs["godot"].wait_for(r"\[FletcherHub\] connected", 60), "the app did not connect to the hub")
        if ck.problems:
            raise RuntimeError("startup failed")
        time.sleep(1.0)
        # Kevin's transcriber in his own uv env, piped straight into the bridge
        uv = shutil.which("uv")
        tcmd = ([uv, "run", "python"] if uv else [PY]) + [
            "tools/transcribe_wav.py", str(wav), "--onsets-only", "--song-origin", str(-LEAD_S),
            "--realtime", "--output", str(tmp / "onsets.drums.txt")]
        procs["transcriber"] = Proc("transcriber", tcmd, tmp, cwd=LT, pipe_out=True)
        procs["bridge"] = Proc("bridge", [PY, "-m", "hub.bridge", str(sheet), "--bpm", str(BPM), "--hub", url],
                               tmp, stdin=procs["transcriber"].p.stdout)
        procs["transcriber"].p.stdout.close()  # the bridge owns the read end now
        rcs["transcriber"] = procs["transcriber"].join(BARS * 4 * 60 / BPM + 30)
        rcs["bridge"] = procs["bridge"].join(15)
        time.sleep(a.settle)  # a count-off or a line in flight at the end of the take plays out
    except RuntimeError:
        pass
    finally:
        for name in ("transcriber", "bridge"):
            if name in procs and name not in rcs:
                rcs[name] = procs[name].stop()
        for name in ("fletcher", "hub"):  # the app quits when the hub closes its socket
            if name in procs:
                rcs[name] = procs[name].stop()
        if "godot" in procs:
            rcs["godot"] = procs["godot"].join(15)

    rec = read_recording(tmp / "recordings")
    gout = procs["godot"].text() if "godot" in procs else ""
    glog = read_godot(gout.splitlines())
    tl = analyse(rec, glog, gout, rcs, ck) if not ck.problems else []

    print("\n=== full e2e timeline (s from the first 'playing') ===")
    t_play = next((m["t"] for m in rec if m.get("type") == "session.state"
                   and (m.get("data") or {}).get("state") == "playing"), rec[0]["t"] if rec else 0.0)
    for t, txt in sorted(tl):
        print(f"{t - t_play:8.3f}  {txt}")
    print(f"\nhub frames recorded: {len(rec)}; app log lines: {len(glog)}; exit codes: {rcs}")
    for n in ck.notes:
        print("note:", n)
    print(f"wall time {time.monotonic() - t_start:.1f} s; logs and recording in {tmp}")
    print("PASS" if not ck.problems else "FAIL:\n  " + "\n  ".join(ck.problems))
    if not ck.problems and not a.keep:
        shutil.rmtree(tmp, ignore_errors=True)
    return 0 if not ck.problems else 1


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--godot", help="Godot 4.5 binary (default: $GODOT, godot on PATH, /Applications)")
    p.add_argument("--port", type=int, default=0, help="hub port (default: a free one)")
    p.add_argument("--seed", type=int, default=5)
    p.add_argument("--drift-sd", type=float, default=0.07, help="Chris's drift simulator sd (s); heavy = cutoff")
    p.add_argument("--settle", type=float, default=2.0, help="s to wait after the take before shutting down")
    p.add_argument("--keep", action="store_true", help="keep the temp dir (logs, recording) on PASS too")
    sys.exit(run(p.parse_args()))


if __name__ == "__main__":
    main()
