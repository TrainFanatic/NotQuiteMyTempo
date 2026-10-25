"""Songsterr tab -> songs/<song_id>/, through Chris's importer (rushingOrDragging/songsterr).

    python -m library import-songsterr <songsterr url or id> [--id ID] [--track N]

The sheet and tempo map are exactly what `rushingOrDragging/cli.py` + `tempo_map.write_tempo_map`
produce. The song's other (pitched) tracks become arrangement.mid + backing.ogg through
library/songsterr_band.py (`--no-band` skips them: drums + click only). Fetching them costs one
request per track the first time (Chris's fetcher caches them and rate-limits to 1/s).
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from . import catalog, render
from .catalog import ARRANGEMENT, BACKING, BAND_TRACKS, CLICK, SHEET, SONG_JSON, TEMPO

catalog.chris_on_path()
from songsterr import convert as conv  # noqa: E402
from songsterr import fetch  # noqa: E402
from songsterr import tempo_map as chris_tm  # noqa: E402


def import_songsterr(song, song_id: str | None = None, track: int | None = None, refresh: bool = False,
                     songs_dir: Path | None = None, band: bool = True, log=print) -> dict:
    meta, idx, tr = fetch.fetch_drums(song, track=track, refresh=refresh)
    header = [
        f"song: {meta['title']} - {meta['artist']}",
        f"songId={meta['songId']} revisionId={meta['revisionId']} track={idx} ({tr.get('name', '?')})",
    ]
    notes = conv.convert(tr)
    source = {"songId": meta["songId"], "revisionId": meta["revisionId"], "track": idx, "name": tr.get("name", "")}
    tm = chris_tm.extract_tempo_map(tr, source=source)
    song_id = song_id or f"{catalog.slug(meta['artist'])}-{catalog.slug(meta['title'])}"
    arrangement = None
    if band:
        from . import songsterr_band
        from .midi_import import backing_midi
        others = []
        for i, t in enumerate(meta.get("tracks", [])):
            if i == idx or t.get("instrumentId") == fetch.DRUMS_INSTRUMENT_ID:
                continue
            try:
                others.append((i, fetch.get_track(meta["songId"], meta["revisionId"], meta["image"], i)))
            except Exception as e:  # noqa: BLE001 - a missing part only thins the band
                log(f"warning: track {i} ({t.get('name')}): {e}")
        if others:
            arrangement, band_tracks = songsterr_band.build_arrangement(tr, others)
            if len(arrangement.tracks) <= 2:
                arrangement = None
    with catalog.Staging(song_id, songs_dir) as st:
        (st.dir / SHEET).write_text(conv.format_notes(notes, header), encoding="utf-8")
        chris_tm.write_tempo_map(tm, st.dir / TEMPO)
        render.write_ogg(st.dir / CLICK, render.click_track(tm))
        rendered = False
        if arrangement is not None:
            (st.dir / ARRANGEMENT).write_bytes(arrangement.to_bytes())
            catalog.write_json(st.dir / BAND_TRACKS, band_tracks)
            why = render.available()
            if why is None:
                audio = render.render_midi(backing_midi(arrangement, {"Drums"}).to_bytes(), start_s=0.0)
                render.write_ogg(st.dir / BACKING, render.normalise(audio), min_s=tm["end_s"])
                rendered = True
            else:
                log(f"warning: no backing for {song_id}: {why}")
        first = tm["bars"][0]
        info = {
            "id": song_id, "title": meta["title"], "artist": meta["artist"],
            "bpm": round(first["bpm"], 3), "beats_per_bar": first["numerator"],
            "bars": len(tm["bars"]), "duration_s": round(tm["end_s"], 3),
            "source": "songsterr", "drum_midi_track": f"{idx}: {tr.get('name', '')}",
            "backing": rendered, "arrangement": arrangement is not None,
            "imported_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "click": True, "notes": len(notes), "pickup_s": 0.0, "midi_t0_s": 0.0,
            "songsterr": {"songId": meta["songId"], "revisionId": meta["revisionId"], "track": idx},
        }
        catalog.write_json(st.dir / SONG_JSON, info)
        st.commit()
    log(f"imported {song_id} from Songsterr: {len(notes)} drum notes, {info['bars']} bars, {info['duration_s']} s,"
        f" band tracks {len(arrangement.tracks) - 2 if arrangement else 0}, backing={'yes' if rendered else 'no'}")
    return info
