"""The song library on disk: `songs/<song_id>/` (see library/README.md for the files)."""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SONGS_DIR = Path(os.environ.get("NQMT_SONGS_DIR", ROOT / "songs"))
CHRIS = ROOT / "rushingOrDragging"

SHEET = "drums.drums.txt"
TEMPO = "drums.tempo.json"
ARRANGEMENT = "arrangement.mid"
BAND_TRACKS = "band_tracks.json"
BACKING = "backing.ogg"
CLICK = "click.ogg"
SONG_JSON = "song.json"
# Everything the HTTP server may hand out from a song folder.
SERVED = (SONG_JSON, SHEET, TEMPO, ARRANGEMENT, BAND_TRACKS, BACKING, CLICK)

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,79}$")


def chris_on_path() -> None:
    """Chris's modules import each other flat (`from songsterr import ...`), like hub/bridge.py."""
    if str(CHRIS) not in sys.path:
        sys.path.insert(0, str(CHRIS))


def slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:80]
    return s or "song"


def valid_id(song_id: str) -> bool:
    return bool(_ID_RE.match(song_id or "")) and song_id not in ("inbox",)


def song_dir(song_id: str, songs_dir: Path | None = None) -> Path:
    if not valid_id(song_id):
        raise ValueError(f"bad song id {song_id!r}")
    return Path(songs_dir or SONGS_DIR) / song_id


def inbox(songs_dir: Path | None = None) -> Path:
    return Path(songs_dir or SONGS_DIR) / "inbox"


def load_song(song_id: str, songs_dir: Path | None = None) -> dict | None:
    p = song_dir(song_id, songs_dir) / SONG_JSON
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def list_songs(songs_dir: Path | None = None) -> list[dict]:
    d = Path(songs_dir or SONGS_DIR)
    out = []
    if d.is_dir():
        for sub in sorted(d.iterdir()):
            if sub.is_dir() and valid_id(sub.name):
                s = load_song(sub.name, d)
                if s is not None:
                    out.append(s)
    out.sort(key=lambda s: (s.get("title") or s["id"]).lower())
    return out


def paths(song_id: str, songs_dir: Path | None = None) -> dict[str, Path]:
    """The files of one song, for other code (session controller, bridge)."""
    d = song_dir(song_id, songs_dir)
    return {"dir": d, "sheet": d / SHEET, "tempo": d / TEMPO, "arrangement": d / ARRANGEMENT,
            "band_tracks": d / BAND_TRACKS, "backing": d / BACKING, "click": d / CLICK}


class Staging:
    """Build a song in a temp folder next to the library, then swap it in atomically-ish."""

    def __init__(self, song_id: str, songs_dir: Path | None = None):
        self.final = song_dir(song_id, songs_dir)
        self.final.parent.mkdir(parents=True, exist_ok=True)
        self.dir = Path(tempfile.mkdtemp(prefix=f".tmp-{song_id}-", dir=self.final.parent))

    def commit(self) -> Path:
        old = None
        if self.final.exists():
            old = self.final.with_name(f".old-{self.final.name}-{os.getpid()}")
            os.replace(self.final, old)
        os.replace(self.dir, self.final)
        if old:
            shutil.rmtree(old, ignore_errors=True)
        return self.final

    def abort(self) -> None:
        shutil.rmtree(self.dir, ignore_errors=True)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, *_):
        if exc_type is not None:
            self.abort()


def write_json(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
