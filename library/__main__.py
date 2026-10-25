"""python -m library [serve|import-midi|import-songsterr|inbox|list|soundfont] ..."""

import argparse
import sys


def main(argv=None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    cmd = argv[0] if argv and not argv[0].startswith("-") else "serve"
    rest = argv[1:] if argv and argv[0] == cmd else argv
    if cmd == "serve":
        from .server import main as serve
        return serve(rest)
    if cmd == "soundfont":
        from .soundfont import fetch
        return print(fetch())
    if cmd == "list":
        from .catalog import list_songs
        for s in list_songs():
            print(f"{s['id']:<40} {s['source']:<9} {s['bpm']:>7} bpm  {s['bars']:>4} bars"
                  f"  backing={'yes' if s.get('backing') else 'no'}  {s.get('title', '')}")
        return
    if cmd == "inbox":
        from .midi_import import import_inbox
        import_inbox()
        return
    if cmd == "import-midi":
        p = argparse.ArgumentParser(prog="python -m library import-midi")
        p.add_argument("midi", nargs="+")
        p.add_argument("--id", help="song id (default: slug of the file name)")
        p.add_argument("--title")
        p.add_argument("--artist", default="")
        p.add_argument("--drum-track", action="append", default=[], help="read this track as drums")
        p.add_argument("--no-backing", action="store_true")
        g = p.add_mutually_exclusive_group()
        g.add_argument("--pickup", dest="pickup", action="store_true", default=None,
                       help="first bar is a pickup (default: auto-detect)")
        g.add_argument("--no-pickup", dest="pickup", action="store_false")
        a = p.parse_args(rest)
        from .midi_import import SongImportError, import_midi
        for m in a.midi:
            try:
                import_midi(m, song_id=a.id if len(a.midi) == 1 else None, title=a.title, artist=a.artist,
                            drum_tracks=tuple(a.drum_track), backing=not a.no_backing, pickup=a.pickup)
            except SongImportError as e:
                raise SystemExit(str(e))
        return
    if cmd == "import-songsterr":
        p = argparse.ArgumentParser(prog="python -m library import-songsterr")
        p.add_argument("song", help="Songsterr URL or numeric song id")
        p.add_argument("--id")
        p.add_argument("--track", type=int)
        p.add_argument("--refresh", action="store_true")
        p.add_argument("--no-band", action="store_true", help="drums only: no arrangement/backing")
        a = p.parse_args(rest)
        from .songsterr_import import import_songsterr
        import_songsterr(a.song, song_id=a.id, track=a.track, refresh=a.refresh, band=not a.no_band)
        return
    raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
