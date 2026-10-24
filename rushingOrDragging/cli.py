"""Import a Songsterr drum tab as a timestamped note list.

  python cli.py https://www.songsterr.com/a/wsa/ac-dc-back-in-black-drum-tab-s1024
  python cli.py 1024 -o out/back_in_black.drums.txt
  python cli.py search "back in black"
  python cli.py convert data/raw/1024/9880696/6.json    # convert a local track JSON
"""

import argparse
import json
import re
import sys
from pathlib import Path

from songsterr import convert as conv
from songsterr import fetch

OUT_DIR = Path(__file__).resolve().parent / "out"


def slug(text):
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def cmd_search(args):
    for s in fetch.search(args.query):
        has_drums = any(t.get("instrumentId") == fetch.DRUMS_INSTRUMENT_ID for t in s.get("tracks", []))
        print(f"{s['songId']:>8}  {'drums' if has_drums else '     '}  {s['artist']} - {s['title']}")


def cmd_convert(args):
    track = json.loads(Path(args.path).read_text())
    header = [f"source: {args.path}  track={track.get('name', '?')}"]
    write(conv.convert(track), header, args.output or OUT_DIR / f"{Path(args.path).stem}.drums.txt")


def cmd_import(args):
    meta, idx, track = fetch.fetch_drums(args.song, track=args.track, refresh=args.refresh)
    header = [
        f"song: {meta['title']} - {meta['artist']}",
        f"songId={meta['songId']} revisionId={meta['revisionId']} track={idx} ({track.get('name', '?')})",
    ]
    default = OUT_DIR / f"{slug(meta['artist'])}-{slug(meta['title'])}.drums.txt"
    write(conv.convert(track), header, args.output or default)


def write(notes, header, out_path):
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(conv.format_notes(notes, header))
    end = notes[-1].time_s if notes else 0
    print(f"Wrote {len(notes)} notes ({end:.1f}s) to {out_path}")


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "search":
        p = argparse.ArgumentParser(prog="cli.py search")
        p.add_argument("query", nargs="+")
        args = p.parse_args(argv[1:])
        args.query = " ".join(args.query)
        return cmd_search(args)
    if argv and argv[0] == "convert":
        p = argparse.ArgumentParser(prog="cli.py convert")
        p.add_argument("path", help="Songsterr track JSON file")
        p.add_argument("-o", "--output")
        return cmd_convert(p.parse_args(argv[1:]))

    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("song", help="Songsterr tab URL or numeric song id")
    p.add_argument("-o", "--output", help="output file (default: out/<artist>-<title>.drums.txt)")
    p.add_argument("--track", type=int, help="track index override (default: the drum track)")
    p.add_argument("--refresh", action="store_true", help="re-fetch metadata for the latest revision")
    return cmd_import(p.parse_args(argv))


if __name__ == "__main__":
    main()
