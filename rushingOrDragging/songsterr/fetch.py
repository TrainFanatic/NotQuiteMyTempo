"""Part 1: download Songsterr song metadata and track JSON.

These are the unofficial endpoints the Songsterr web player uses (verified 2026-10). If they
change, open a tab in the browser and check DevTools -> Network, then update the constants.
Songsterr's ToS restrict scraping: keep this to personal use, rely on the cache, don't redistribute.
"""

import json
import re
import time
from pathlib import Path

import requests

API_BASE = "https://www.songsterr.com/api"
TRACK_CDN = "https://dqsljvtekg760.cloudfront.net"
DRUMS_INSTRUMENT_ID = 1024

USER_AGENT = "Mozilla/5.0 (personal tab importer)"
TIMEOUT_S = 20
MIN_REQUEST_INTERVAL_S = 1.0

DEFAULT_CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"

_last_request = 0.0


def _get_json(url, params=None):
    global _last_request
    wait = MIN_REQUEST_INTERVAL_S - (time.monotonic() - _last_request)
    if wait > 0:
        time.sleep(wait)
    _last_request = time.monotonic()
    resp = requests.get(url, params=params, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT_S)
    resp.raise_for_status()
    return resp.json()  # requests transparently handles the CDN's gzip encoding


def parse_song_id(url_or_id):
    """Accepts a bare id ("1024") or a tab URL ending in "-s<id>" (optionally followed by "t<track>")."""
    s = str(url_or_id).strip()
    if s.isdigit():
        return int(s)
    m = re.search(r"-s(\d+)(?:t\d+)?/?(?:[?#].*)?$", s)
    if not m:
        raise ValueError(f"Can't find a Songsterr song id in {url_or_id!r}")
    return int(m.group(1))


def search(query, size=10):
    return _get_json(f"{API_BASE}/songs", params={"pattern": query, "size": size})


def get_meta(song_id):
    """Latest revision metadata: revisionId, image (CDN path segment), tracks[], title, artist."""
    return _get_json(f"{API_BASE}/meta/{song_id}")


def pick_drum_track(meta, override=None):
    tracks = meta.get("tracks", [])
    if override is not None:
        if not 0 <= override < len(tracks):
            raise ValueError(f"Track {override} out of range (song has {len(tracks)} tracks)")
        return override
    drums = [i for i, t in enumerate(tracks) if t.get("instrumentId") == DRUMS_INSTRUMENT_ID]
    if not drums:
        names = ", ".join(f"{i}: {t.get('name')}" for i, t in enumerate(tracks))
        raise LookupError(f"No drum track in this song. Tracks: {names}")
    popular = meta.get("popularTrackDrum")
    return popular if popular in drums else drums[0]


def get_track(song_id, revision_id, image, track_idx, cache_dir=DEFAULT_CACHE_DIR):
    path = Path(cache_dir) / str(song_id) / str(revision_id) / f"{track_idx}.json"
    if path.exists():
        return json.loads(path.read_text())
    data = _get_json(f"{TRACK_CDN}/{song_id}/{revision_id}/{image}/{track_idx}.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))
    return data


def load_cached_meta(song_id, cache_dir=DEFAULT_CACHE_DIR):
    path = Path(cache_dir) / str(song_id) / "meta.json"
    return json.loads(path.read_text()) if path.exists() else None


def fetch_drums(url_or_id, track=None, cache_dir=DEFAULT_CACHE_DIR, refresh=False):
    """Returns (meta, track_idx, track_json).

    Cached songs are served without network access; refresh=True re-fetches metadata to pick up
    a newer revision (track JSON is cached per revision, so old downloads stay valid).
    """
    song_id = parse_song_id(url_or_id)
    meta = None if refresh else load_cached_meta(song_id, cache_dir)
    if meta is None:
        meta = get_meta(song_id)
        meta_path = Path(cache_dir) / str(song_id) / "meta.json"
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        meta_path.write_text(json.dumps(meta))
    idx = pick_drum_track(meta, track)
    data = get_track(song_id, meta["revisionId"], meta["image"], idx, cache_dir)
    return meta, idx, data
