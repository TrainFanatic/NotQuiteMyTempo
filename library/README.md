# library/: song library, importers, backing renderer, laptop console

Owner: Aaron (agent A, 2026-10-24). Songs live in `songs/<song_id>/`; this package fills that
folder and serves it (and the console page) over HTTP on :8766.

## Setup (once)

    brew install fluid-synth ffmpeg          # ffmpeg is only used by a test
    .venv/bin/pip install aiohttp            # listed in fletcher/requirements.txt
    python -m library soundfont              # GeneralUser GS v2.0.3 -> songs/_soundfonts/ (git-ignored)

The soundfont is fetched from the author's GitHub repo at a pinned commit and its SHA-256 is
checked. Its license allows unrestricted use; `NQMT_SOUNDFONT=<any GM .sf2>` overrides it.
Without FluidSynth or the soundfont, imports still work, just with `"backing": false`.

## Use

    python -m library serve [--port 8766] [--hub ws://localhost:8765]   # console: http://<laptop>:8766/
    python -m library import-midi song.mid [--title T --artist A --id ID --drum-track NAME --no-pickup]
    python -m library import-songsterr <url|id> [--track N] [--no-band]  # wraps Chris's fetch/convert
    python -m library inbox                  # import songs/inbox/*.mid (the server does this every second)
    python -m library list

## A song folder

| file | contents |
|---|---|
| `song.json` | id, title, artist, bpm (bar 1), beats_per_bar, bars, duration_s, source (`midi`/`songsterr`), drum_midi_track, backing, arrangement, imported_at, plus `notes`, `click`, `pickup_s`, `midi_t0_s` |
| `drums.drums.txt` | Chris's format (`rushingOrDragging/file_format.md`), t = 0 at the bar-1 downbeat |
| `drums.tempo.json` | Chris's `tempo-map/1`; loads with `songsterr.tempo_map.load_tempo_map`; the bridge finds it next to the sheet |
| `arrangement.mid` | the source MIDI (format 0 is split into one track per channel), for Adam's `load_song` |
| `band_tracks.json` | `{MIDI track name: [band target]}` for `scene_director.load_song`, by track name, else GM program |
| `backing.ogg` | Ogg Vorbis, 48 kHz stereo, every non-drum part, sample 0 = song time 0, padded to `duration_s`; normalised to −18 LUFS, peak ≤ −1 dBFS (`render.normalise`; levels in quest_app/AUDIO.md) |
| `click.ogg` | a click on every beat (accented downbeats), same origin |

**Drums from MIDI:** channel-10 notes in Chris's drum families (`compare_performance.FAMILIES`:
kick, snare, hi-hat, crash-like, ride, toms) go to the sheet with their GM numbers unchanged.
Other channel-10 sounds (tambourine, cowbell, claps) count as band percussion and stay in the backing.

**Pickups:** if the first bar is shorter than the second and has its own time signature (how
notation programs export an anacrusis), bar 1 is the first FULL bar. The pickup is then at
negative song time and is left out of the sheet and the backing. `pickup_s` gives its length, and
`midi_t0_s` gives song time 0 in arrangement.mid seconds. **The headset must add `midi_t0_s` to
song time before calling `set_song_position`**, because Adam's phrase times count from MIDI tick 0.
For Songsterr songs and for MIDI files without a pickup, `midi_t0_s` is 0. `--no-pickup` keeps
the short bar as bar 1.

**Songsterr band:** `songsterr_band.py` reads the song's pitched tracks with Chris's timing rules
(repeats, swing, beat offsets): pitch = tuning[string] + fret, and ties extend the note. The
instrumentId is used as the GM program, and the tempo map comes from the drum track. The result
is arrangement.mid, and its render is backing.ogg. Drum times in that MIDI match Chris's sheet
to within 0.5 ms. A track whose repeat structure differs from the drum track's is skipped.

**Alignment:** FluidSynth renders in 64-sample blocks. Each event sounds 64 to 127 samples late,
and the fixed 64 is removed, so notes are 0 to 1.33 ms late. Vorbis pre-echo can lead a transient
by about 2 ms. Measured in `tests/test_render.py` across a pickup and a tempo change.

## Hub messages (src `library`)

`library.songs` `{"songs": [song.json...], "base_url": "http://<lan-ip>:8766/songs/"}` is sent on
every (re)connect, on any change, and in reply to `library.request` `{}`. A file is then at
`base_url + <id>/<file>`.

The console's buttons POST `/api/command`, and the server sends `session.command` from src
`console` (`start {song_id, from_bar}`, `stop {from_bar?}`, `quit`). The page also opens its own
WebSocket to the hub, but **only reads from it**: a browser's clock is not the laptop's
`contract.now()`, and the headset estimates its clock offset from every frame's `t`.

## Tests

    .venv/bin/python -m pytest library/tests -q

These run offline. The render tests skip without FluidSynth and the soundfont.
