"""Song library: songs/<song_id>/ on disk, MIDI + Songsterr importers, backing renderer, and the
laptop console server (:8766). See library/README.md."""

from .catalog import SONGS_DIR, list_songs, load_song, paths  # noqa: F401
