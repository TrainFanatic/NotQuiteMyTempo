"""Pull takes straight off the Quest (no export picker), import and label them.

The recorder is a debug APK, so `adb run-as` can read its private user://takes folder.
A take named `single_<piece>_<NN>` in the app becomes session `single_<piece>_<NN>`
(the app's `_<unix>_<usec>` suffix is dropped), is imported with Kevin's
import_quest_take.py, and labelled with `label.py single --piece <piece>`. Other names
(`noise_01`, `quest_smoke_01`, `pa_*`) are imported only.

    python datacollection/quest_takes.py list
    python datacollection/quest_takes.py pull                # every take not imported yet
    python datacollection/quest_takes.py pull --only single_snare_01
    python datacollection/quest_takes.py pull --names single_kick_01 single_snare_01 ...
        # takes recorded without renaming them in the app (typing in the headset is slow):
        # the new takes, oldest first, get these names in order
    python datacollection/quest_takes.py splits              # fill kit.local.json splits

adb's server needs the network, so run this outside any sandbox.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LT = ROOT / "live_transcription"
PKG = "org.notquitemytempo.questrecorder"
TAKES = "files/takes"  # user://takes, relative to the app's data dir for run-as
RECORDINGS = LT / "data" / "recordings"
PULLS = ROOT / "datacollection" / "quest_pulls"
KIT = LT / "config" / "kit.local.json"
SUFFIX = re.compile(r"^(?P<name>.+?)_\d{9,}_\d+$")
SINGLE = re.compile(r"^single_(?P<piece>.+)_(?P<round>\d+)$")
SILENT_PEAK = 0.005  # import_quest_take.py rejects these too; the Quest can hand the app pure zeros
SPLIT_OF_ROUND = {"01": "train", "02": "validation", "03": "test"}


def adb(*args: str, binary: bool = False):
    r = subprocess.run(["adb", *args], capture_output=True)
    if r.returncode:
        raise SystemExit(f"adb {' '.join(args)} failed: {r.stderr.decode().strip() or r.stdout.decode().strip()}")
    return r.stdout if binary else r.stdout.decode()


def check_device() -> None:
    lines = [l for l in adb("devices").splitlines()[1:] if l.strip()]
    if not lines:
        raise SystemExit("no Quest found: connect a USB-C data cable")
    if any("unauthorized" in l for l in lines):
        raise SystemExit("Quest is unauthorized: put it on and accept 'Allow USB debugging'")


def remote_takes() -> list[str]:
    out = adb("shell", "run-as", PKG, "ls", TAKES)
    return sorted(t for t in out.split() if t)


def session_of(take_id: str) -> str:
    m = SUFFIX.match(take_id)
    return m["name"] if m else take_id


def remote_meta(take_id: str) -> dict:
    raw = adb("exec-out", "run-as", PKG, "cat", f"{TAKES}/{take_id}/source.capture.json", binary=True)
    return json.loads(raw)


def pull(take_id: str) -> tuple[Path, Path]:
    PULLS.mkdir(parents=True, exist_ok=True)
    wav, meta = PULLS / f"{take_id}.wav", PULLS / f"{take_id}.capture.json"
    for name, dst in (("source.wav", wav), ("source.capture.json", meta)):
        if not dst.exists():
            dst.write_bytes(adb("exec-out", "run-as", PKG, "cat", f"{TAKES}/{take_id}/{name}", binary=True))
    return wav, meta


def run(cmd: list[str], cwd: Path) -> tuple[bool, str]:
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    return r.returncode == 0, (r.stdout + r.stderr).strip()


def pieces() -> set[str]:
    return set(json.loads(KIT.read_text())["pieces"]) if KIT.exists() else set()


def summarize(session: str) -> str:
    d = RECORDINGS / session
    rep = json.loads((d / "audio.capture.json").read_text()) if (d / "audio.capture.json").exists() else {}
    def rows(p):
        return sum(1 for _ in csv.reader(p.open())) - 1 if p.exists() else 0
    return (f"{rep.get('duration_s', 0):5.1f} s  peak {rep.get('peak', 0):.2f}  clip {rep.get('near_clip_fraction', 0):.4f}"
            f"  labels {rows(d / 'labels.csv'):3d}  review {rows(d / 'labels_review.csv'):2d}")


def cmd_list(_a) -> None:
    check_device()
    for t in remote_takes():
        s = session_of(t)
        try:
            m = remote_meta(t)
            info = f"{m.get('duration_s', 0):5.1f} s  peak {m.get('peak', 0):.2f}  valid {m.get('valid_for_training')}"
            if m.get("peak", 0) < SILENT_PEAK:
                info += "  SILENT (mic gave zeros: restart the app and re-record)"
        except (SystemExit, ValueError):
            info = "no metadata (interrupted take?)"
        print(f"{s:<26} {'imported' if (RECORDINGS / s).exists() else 'new':<9} {info}   [{t}]")


def cmd_pull(a) -> None:
    check_device()
    kit = pieces()
    if not kit:
        print(f"warning: {KIT} missing; single takes will not be labelled", file=sys.stderr)
    seen: dict[str, str] = {}
    takes = remote_takes()
    if a.names:
        imported = {p.name for p in PULLS.glob("*.wav")} if PULLS.exists() else set()
        new = sorted((t for t in takes if f"{t}.wav" not in imported), key=lambda t: int(t.rsplit("_", 2)[-2]))
        silent = [t for t in new if remote_meta(t).get("peak", 0) < SILENT_PEAK]
        if silent:
            print(f"! skipping {len(silent)} silent take(s) (mic delivered zeros): {' '.join(silent)}")
            new = [t for t in new if t not in silent]
        if len(new) != len(a.names):
            raise SystemExit(f"{len(new)} new take(s) on the headset but {len(a.names)} name(s) given:\n  "
                             + "\n  ".join(new))
        renamed = dict(zip(new, a.names))
        globals()["session_of"] = lambda t, _f=session_of: renamed.get(t, _f(t))
        takes = new
    for t in takes:
        seen.setdefault(session_of(t), t)
        if session_of(t) in seen and seen[session_of(t)] != t:
            print(f"! {session_of(t)}: two takes with this name ({seen[session_of(t)]}, {t}); keeping the first. "
                  "Rename the retake in the app (e.g. _01b) and pull again.")
    for s, t in seen.items():
        if a.only and s not in a.only:
            continue
        if (RECORDINGS / s).exists():
            print(f"= {s:<26} already imported   {summarize(s)}")
            continue
        wav, meta = pull(t)
        ok, out = run(["uv", "run", "python", "tools/import_quest_take.py", str(wav), str(meta), "--session", s], LT)
        if not ok:
            print(f"x {s:<26} import failed: {out.splitlines()[-1] if out else '?'}")
            continue
        m = SINGLE.match(s)
        if m and m["piece"] in kit:
            ok, out = run([sys.executable, "datacollection/label.py", "single", "--session", s,
                           "--piece", m["piece"], "--kit", str(KIT)], ROOT)
            if not ok:
                print(f"x {s:<26} labelling failed: {out.splitlines()[-1] if out else '?'}")
                continue
        elif m:
            print(f"! {s}: piece '{m['piece']}' is not in {KIT.name} (have: {', '.join(sorted(kit))}); imported only")
        print(f"+ {s:<26} {summarize(s)}")


def cmd_splits(_a) -> None:
    kit = json.loads(KIT.read_text())
    splits = {"train": [], "validation": [], "test": []}
    for d in sorted(p for p in RECORDINGS.iterdir() if (p / "labels.csv").exists() or p.name.startswith("noise")):
        m = SINGLE.match(d.name)
        if m and m["round"] in SPLIT_OF_ROUND:
            splits[SPLIT_OF_ROUND[m["round"]]].append(d.name)
        elif d.name.startswith("noise"):
            splits["train"].append(d.name)
    kit["splits"] = splits
    KIT.write_text(json.dumps(kit, indent=2) + "\n")
    for k, v in splits.items():
        print(f"{k:<10} {len(v):2d}  {' '.join(v)}")
    missing = {k: sorted(set(kit['pieces']) - {SINGLE.match(s)['piece'] for s in v if SINGLE.match(s)})
               for k, v in splits.items()}
    for k, v in missing.items():
        if v:
            print(f"! {k} has no take for: {', '.join(v)}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list").set_defaults(fn=cmd_list)
    pp = sub.add_parser("pull")
    pp.add_argument("--only", nargs="*", help="session names to pull")
    pp.add_argument("--names", nargs="*", help="names for the new takes, oldest first")
    pp.set_defaults(fn=cmd_pull)
    sub.add_parser("splits").set_defaults(fn=cmd_splits)
    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
