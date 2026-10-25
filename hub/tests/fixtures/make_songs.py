"""Regenerate the fixture song library (hub/tests/fixtures/songs/) used by hub tests and
hub/app_e2e.py, in the e2e spec's songs/<song_id>/ layout, from Chris's 4-bar Back in Black
fixture with its measures repeated 4x, through Chris's own converter and tempo-map code.

    python hub/tests/fixtures/make_songs.py
"""

import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "rushingOrDragging"))
import cli  # noqa: E402
from songsterr import tempo_map as tm  # noqa: E402

FIXTURE = ROOT / "rushingOrDragging" / "tests" / "fixtures" / "back_in_black_drums_4bars.json"
OUT = Path(__file__).resolve().parent / "songs"
SONG_ID, LOOPS = "back-in-black-16", 4


def main() -> None:
    track = json.loads(FIXTURE.read_text())
    track = {**track, "measures": [copy.deepcopy(m) for _ in range(LOOPS) for m in track["measures"]]}
    d = OUT / SONG_ID
    d.mkdir(parents=True, exist_ok=True)
    src = d / "track.json"
    src.write_text(json.dumps(track))
    cli.main(["convert", str(src), "-o", str(d / "drums.drums.txt")])
    sheet = d / "drums.drums.txt"
    lines = sheet.read_text().splitlines()
    lines[0] = f"# source: {FIXTURE.relative_to(ROOT)} x{LOOPS}  track={track['name']}"
    sheet.write_text("\n".join(lines) + "\n")
    tmap = tm.write_tempo_map(track, d / "drums.tempo.json")
    src.unlink()
    m = tm.load_tempo_map(tmap)
    song = {"id": SONG_ID, "title": "Back in Black (fixture, 16 bars)", "artist": "AC/DC",
            "bpm": m["bars"][0]["bpm"], "beats_per_bar": m["bars"][0]["numerator"], "bars": len(m["bars"]),
            "duration_s": round(m["end_s"], 6), "source": "songsterr", "drum_midi_track": track["name"],
            "backing": False, "arrangement": False, "imported_at": "2026-10-24T00:00:00Z"}
    (d / "song.json").write_text(json.dumps(song, indent=1) + "\n")
    print(d)


if __name__ == "__main__":
    main()
