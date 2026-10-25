"""The General MIDI soundfont used to render backing tracks.

    python -m library.soundfont          # download it (once, ~31 MB) into songs/_soundfonts/

GeneralUser GS v2.0.3 by S. Christian Collins. Its license allows unrestricted use in music
creation and software, but asks not to be hot-linked from websites; we fetch it from the author's
GitHub repository at a pinned commit, check its SHA-256, and keep it out of git
(songs/_soundfonts/ is ignored). Override with NQMT_SOUNDFONT=<path to any GM .sf2>.
"""

from __future__ import annotations

import hashlib
import os
import sys
import urllib.request
from pathlib import Path

from .catalog import SONGS_DIR

NAME = "GeneralUser-GS.sf2"
COMMIT = "684543d5e5efaef08d02be50dcda8d552478fa60"
URL = f"https://raw.githubusercontent.com/mrbumpy409/GeneralUser-GS/{COMMIT}/{NAME}"
LICENSE_URL = f"https://raw.githubusercontent.com/mrbumpy409/GeneralUser-GS/{COMMIT}/documentation/LICENSE.txt"
SHA256 = "9575028c7a1f589f5770fccc8cff2734566af40cd26ed836944e9a5152688cfe"


def default_path() -> Path:
    return SONGS_DIR / "_soundfonts" / NAME


def path() -> Path | None:
    p = Path(os.environ["NQMT_SOUNDFONT"]) if os.environ.get("NQMT_SOUNDFONT") else default_path()
    return p if p.is_file() else None


def fetch(dest: Path | None = None) -> Path:
    dest = Path(dest or default_path())
    if dest.is_file() and _sha(dest) == SHA256:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part")
    print(f"downloading {URL} ...", file=sys.stderr)
    urllib.request.urlretrieve(URL, tmp)
    got = _sha(tmp)
    if got != SHA256:
        tmp.unlink(missing_ok=True)
        raise SystemExit(f"soundfont checksum mismatch: {got}")
    os.replace(tmp, dest)
    try:
        urllib.request.urlretrieve(LICENSE_URL, dest.parent / "GeneralUser-GS.LICENSE.txt")
    except OSError:
        pass
    return dest


def _sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


if __name__ == "__main__":
    print(fetch())
