# Project structure

This project turns drum sheet music from Songsterr into a simple timestamped note list. It then
uses that list to listen to the part, and to grade a real drummer's performance against it.

```
Songsterr API ──► data/raw/…json ──► songsterr/convert.py ──► out/<song>.drums.txt
   (cli.py, songsterr/fetch.py)                                  │
                                                                 ├─► test_file_with_audio.py ──► .wav  (listen)
                                                                 ├─► test_file_with_midi.py  ──► .mid  (open in a DAW)
                                                                 ├─► compare_performance.py  ◄── played .drums.txt
                                                                 │       (early/late report, after the fact)
                                                                 └─► live_compare.py ◄── stream_notes.py played.drums.txt
                                                                         (live RUSHING! / DRAGGING! / PERFECT!)
```

The central data format is `.drums.txt`: one drum hit per line, written as `time_s  midi  name`.
It's fully specified in [`file_format.md`](file_format.md).

## Folder tree

The code lives in `rushingOrDragging/` inside the [`NotQuiteMyTempo`](https://github.com/TrainFanatic/NotQuiteMyTempo) repository. Run every command in
this guide from `rushingOrDragging/`. The scripts find `data/`, `out/` and `samples/` relative
to their own location, so those folders are always created inside `rushingOrDragging/`.

```
NotQuiteMyTempo/                   # repository root (a local clone may have another folder name)
├── README.md                      # repo title only
├── .gitignore                     # ignores data/, out/, samples/ and Python caches at any depth
├── .Rhistory                      # unrelated (R session history), untracked
├── data/, out/                    # stale copies from before the code moved into rushingOrDragging/;
│                                  #   nothing reads or writes them any more (git-ignored)
│
└── rushingOrDragging/
    ├── cli.py                     # entry point: import a song from Songsterr → out/*.drums.txt
    ├── test_file_with_audio.py    # .drums.txt → .wav using recorded acoustic drum samples
    ├── test_file_with_midi.py     # .drums.txt → .mid (General MIDI drum channel)
    ├── compare_performance.py     # played .drums.txt vs sheet .drums.txt → early/late report
    ├── stream_notes.py            # replays a .drums.txt as a live stream (each line at its time)
    ├── live_compare.py            # reads a stream, shows RUSHING! / DRAGGING! / PERFECT! live
    │
    ├── songsterr/                 # library code used by cli.py
    │   ├── __init__.py
    │   ├── fetch.py               # Part 1: download + cache song metadata and track JSON
    │   ├── convert.py             # Part 2: track JSON → timestamped notes
    │   └── drum_map.py            # MIDI number → drum name table
    │
    ├── tests/                     # pytest suite (run: python3 -m pytest -q)
    │   ├── fixtures/
    │   │   └── back_in_black_drums_4bars.json
    │   ├── test_convert.py
    │   ├── test_audio.py
    │   ├── test_compare.py
    │   ├── test_uniform_jitter.py # also a script: makes a ±50 ms jittered clone
    │   ├── test_drift.py          # also a script: makes a rushing/dragging clone
    │   └── test_streaming.py      # stream_notes.py + live_compare.py, alone and piped together
    │
    ├── data/raw/                  # (generated on first import, git-ignored) Songsterr download cache
    │   └── <songId>/
    │       ├── meta.json
    │       └── <revisionId>/<trackIndex>.json
    │
    ├── out/                       # (generated on first run, git-ignored) everything the scripts produce
    │   ├── <artist>-<title>.drums.txt
    │   ├── <artist>-<title>.drums.wav
    │   ├── <artist>-<title>.jitter50_seed<N>.drums.txt   + .report.txt
    │   ├── <artist>-<title>.drift<X>ms_seed<N>.drums.txt + .report.txt
    │   └── mop_60-75.wav          # example excerpt render
    │
    ├── samples/                   # (downloaded, git-ignored) acoustic drum kit
    │   ├── ATTRIBUTION.md
    │   └── AVLDrumkits-BlackPearl-2023/
    │       ├── <num>-<Maker>-<size>-<Piece>-<1..5>.wav   (127 samples)
    │       ├── drumkit.xml
    │       └── blackpearl-h2.png
    │
    ├── file_format.md             # specification of the .drums.txt format
    ├── TODO.md                    # deferred work (tonal instruments, etc.)
    ├── STRUCTURE.md               # this file
    └── requirements.txt
```

---

## Entry-point scripts (project root)

### `cli.py`: import sheet music
Downloads a Songsterr song's drum track and converts it to a `.drums.txt` file.

```
python cli.py https://www.songsterr.com/a/wsa/metallica-master-of-puppets-drum-tab-s455118
python cli.py 455118 -o out/mop.drums.txt      # bare song id works too
python cli.py search master of puppets          # find song ids; flags results that have drums
python cli.py convert data/raw/1024/9880696/6.json   # convert an already-downloaded track JSON
```
- **Input:** a Songsterr URL or numeric song id.
- **Output:** `out/<artist>-<title>.drums.txt` by default, or the path given with `-o`.
- **Options:**
  - `--track N`: pick a track other than the auto-detected drum track.
  - `--refresh`: re-fetch metadata to pick up a newer tab revision. Without it, a song already in `data/raw/` converts with no network access at all.

### `test_file_with_audio.py`: listen to a note file
Renders a `.drums.txt` file straight to a mono 44.1 kHz WAV by placing a recorded drum sample at
each timestamp. The hit's transient lands on exactly sample `round(time_s × 44100)`: each
sample's leading silence is trimmed, and its small lead-in is compensated.

```
python test_file_with_audio.py out/metallica-master_of_puppets.drums.txt
python test_file_with_audio.py out/metallica-master_of_puppets.drums.txt --start 60 --end 75 -o out/mop_60-75.wav
```
- **Output:** a `.wav` next to the input, or the path given with `-o`.
- **Samples:** taken from `samples/AVLDrumkits-BlackPearl-2023/`, which is downloaded automatically (~16 MB) on first run if missing.
- **`KIT_MAP`** in the script maps every note number to a kit sample, because the kit numbers its own files and has only two toms. Its substitutions:
  - The four missing toms are the two real toms pitch-shifted.
  - Choked crash 2 and china are the full cymbal faded out early (the crash 1 choke has its own recording).
  - Half-open hi-hat is the semi-open hi-hat, damped sooner.
  - Ride edge is the ride struck with the stick shank.
- **Unmapped numbers:** anything not in `KIT_MAP` uses a stand-in sound and prints a warning.
- **Options:**
  - `--layer 1–5`: how hard every hit sounds (the format has no velocities; default 4).
  - `--start` / `--end`: render only part of the song.
  - `--kit DIR`: use a different sample folder.

### `test_file_with_midi.py`: export a note file as MIDI
Writes a Standard MIDI File (one track, GM drum channel 10, no dependencies) to open in
GarageBand, MuseScore or a DAW. Guitar Pro's extended numbers 91–98 are remapped to the closest
GM sound unless `--no-remap` is given. `--velocity` sets the loudness of all hits.
*Status:* written during an interrupted step. It runs, but its output wasn't independently
verified the way the WAV renderer was.

### `compare_performance.py`: grade a performance
Compares a `.drums.txt` reconstructed from someone actually playing against the sheet-music
`.drums.txt`. For every played hit it prints the paired sheet note and how early or late the hit was.

```
python compare_performance.py played.drums.txt out/metallica-master_of_puppets.drums.txt
python compare_performance.py played.drums.txt sheet.drums.txt --summary-only
```
- **Matching:**
  1. Same drum first, preserving playing order. Among non-crossing pairings, it picks the one with the least total error.
  2. Leftover hits then go to the same *kind* of drum (kick, snare, hi-hat, crash-like, ride, toms). These are flagged `(expected <name>)`.
- **Drift tracking:** players rush and drag. After a first pass, the local timing (rolling median of errors over ±2 s) becomes the expected position of each note, and matching is repeated. Reported errors are still measured against the sheet. `--no-drift-tracking` disables this.
- **Start offset:** a constant shift between the recording and the song start is auto-detected. It stays 0 unless another value pairs clearly more hits. `--offset` sets it explicitly.
- **Output:** one line per played hit with `on time` / `early` / `late`, plus `EXTRA` and `MISSED` lines. Then a summary:
  - counts
  - mean, median and spread of the error
  - per-drum figures
  - a *timing over time* bar chart showing where the player rushed (`<`) or dragged (`>`)
- **Options:**
  - `--tolerance` (default 10 ms): the "on time" band.
  - `--window` (default 100 ms): the maximum distance for a match.
  - `--segment` (default 10 s): the chart's row size.
  - `--hide-missed`: leave out the `MISSED` lines.

### `stream_notes.py`: replay a note file as a live stream
Writes each data line of a `.drums.txt` to stdout at its timestamp, in real time. Lines are
flushed one by one in canonical form, keeping the song timestamps. It stands in for a live
drum-transcription feed, and works through pipes, FIFOs and redirects.

```
python stream_notes.py played.drums.txt | python live_compare.py out/<song>.drums.txt
python stream_notes.py played.drums.txt --start 60 --speed 2
```
- **Timing:** it sleeps until about 2 ms before each deadline, then busy-waits, so lines arrive within about 1 ms of their target.
- **Options:**
  - `--start S`: song time that maps to the moment of launch.
  - `--speed X`: playback rate. Timestamps stay unchanged.
  - `--header`: emit the column header first.
- **Ending early:** if the reader goes away, it exits quietly.

### `live_compare.py`: live timing coach
Reads a stream of note lines, from stdin or `--input FILE_OR_FIFO`, and judges each hit as it
arrives. The screen switches between big coloured **RUSHING!** (early), **DRAGGING!** (late) and
**PERFECT!** (within `--tolerance`, default ±10 ms).
- **Matching:** online, using only hits that have already arrived. Each hit gets the nearest unmatched note on the same drum, after the last note matched on that drum, around the sheet time shifted by the player's recent drift (median of the last `--drift-window` seconds, default 2). Failing that, it tries the same kind of drum; otherwise the hit is extra. Notes the stream has passed count as missed.
- **Displayed state:** the median error of the last `--smooth` seconds (default 0.5), with newer hits weighing more. `--hysteresis` (default 2 ms) stops flicker at the threshold edge.
- **Clock:**
  - `--clock line` (default): uses each line's timestamp, so pipe delays don't count against the player.
  - `--clock arrival`: times hits by wall-clock arrival since launch (plus `--start`), for sources with untrustworthy timestamps.
- **Output:**
  - full-screen view on a terminal
  - one line per state change with `--plain`, or when output isn't a terminal
  - the `compare_performance.py` summary at the end
- **Accuracy:** with heavy drift (peaks about ±130 ms) it pairs about 99.5–99.8% of hits correctly, against 100% for the batch comparer, which can use later hits. With realistic drift it's 100%.

---

## `songsterr/`: library package

| File | Role |
|---|---|
| `__init__.py` | Empty; makes `songsterr` an importable package. |
| `fetch.py` | **Part 1, scraping.** Holds the unofficial Songsterr endpoints (search API, metadata API, CloudFront track CDN) as constants. `parse_song_id` reads a song id from a URL. `pick_drum_track` chooses a track with `instrumentId == 1024`. Downloads are cached under `data/raw/`, with requests rate-limited to one per second. |
| `convert.py` | **Part 2, translation.** Walks the track JSON and produces timestamps: it expands repeats (`playback_order`), carries time signatures forward, applies swing (`tripletFeel`) and builds the tempo map, then integrates to seconds. It uses exact fractions throughout, so long songs don't drift. Grace notes take no time, and ties and rests produce no notes. `format_notes` writes the `.drums.txt` text. |
| `drum_map.py` | `GM_DRUMS`: General MIDI percussion numbers 27–87 plus Guitar Pro extensions 91–98 (rim shot, half-open hi-hat, ride edge, chokes) → snake_case names. `drum_name()` returns `unknown` for anything else. Used by every other script. |

---

## `tests/`: automated checks

Run everything with `python3 -m pytest -q` (about 25 s; the streaming tests run in real time). Tests that need a downloaded song
(`out/metallica-master_of_puppets.drums.txt`) or the sample kit skip themselves if it's missing.

| File | What it verifies |
|---|---|
| `fixtures/back_in_black_drums_4bars.json` | Real data: the first 4 bars of Songsterr's Back In Black drum track, used as a known-good input. |
| `test_convert.py` | Timestamps for tempo changes, dotted notes and triplets, time-signature changes, two voices, grace notes, swing, repeats and alternate endings, the output format, URL parsing and drum-track selection. |
| `test_audio.py` | The WAV renderer, using a fake kit (exact hit placement, start/end windows, layers, pitch/choke, no clipping, CLI) and, if present, the real kit: every mapped drum's hit lands on its exact sample. |
| `test_compare.py` | Matching unit cases (nearest note, windows, duplicates, drum-family fallback, simultaneous hits), offset detection (including "consistently late isn't hidden"), report text, and a simulated performance of a real song. |
| `test_uniform_jitter.py` | Clones the sheet file, shifts every note uniformly within ±50 ms, runs the comparer, and checks that its printed output matches the planted shifts. The only allowed mistakes are unavoidable order swaps. As a script (`python tests/test_uniform_jitter.py [--seed N]`) it writes the clone to `out/`. |
| `test_streaming.py` | The streamer emits canonical lines on time, honours `--start`, and exits quietly when the reader closes. The live matcher is accurate on drifting players and handles order, extra and missed hits, and drum-family fallback. Covers hysteresis and the recency-weighted median. Pipe tests check each state appears, both clock modes, and that the end-of-stream summary agrees with the batch comparer. Also covers regular-file stdin (it used to hang) and the full-screen view under a pseudo-terminal. |
| `test_drift.py` | Simulates a drummer who rushes and drags: slow drift that keeps returning toward the beat, plus jitter whose range itself varies. It covers realistic, heavy and offset scenarios, and checks pairing, reported errors and the timing-over-time chart. It also shows the matcher fails without drift tracking. As a script (`python tests/test_drift.py [--seed N] [--drift-sd MS] [--offset S]`) it writes the clone to `out/`. |

---

## Data and generated folders

### `data/raw/`: Songsterr download cache
- **`<songId>/meta.json`:** song metadata. It holds the title, artist, current `revisionId`, the `image` segment used in CDN URLs, and the list of tracks with instrument ids.
- **`<songId>/<revisionId>/<trackIndex>.json`:** the raw track data, structured as measures → voices → beats → notes. For drums, a note's `fret` is its MIDI drum number.

These are cached so re-runs work offline. They're keyed by revision, so editing a tab on Songsterr never silently changes old downloads. They're generated and git-ignored. Songsterr's terms restrict scraping, so keep them for personal use and don't redistribute.

### `out/`: generated outputs (git-ignored; safe to delete and regenerate)
| Pattern | Produced by | Meaning |
|---|---|---|
| `<artist>-<title>.drums.txt` | `cli.py` | The sheet-music reference: every drum hit with its expected time. |
| `<artist>-<title>.drums.wav` | `test_file_with_audio.py` | Audio render of that file, for checking by ear against the recording. |
| `mop_60-75.wav` | `test_file_with_audio.py --start/--end` | Example of rendering an excerpt (Master of Puppets, 60–75 s). |
| `*.mid` | `test_file_with_midi.py` | MIDI export, if you made one. |
| `*.jitter50_seed<N>.drums.txt` | `tests/test_uniform_jitter.py` | Simulated performance: the sheet with uniform ±50 ms errors. |
| `*.drift<X>ms_seed<N>.drums.txt` | `tests/test_drift.py` | Simulated performance with rushing and dragging (drift spread X ms). |
| `*.report.txt` | `compare_performance.py … > file` | Saved comparison report for the matching simulated performance. |

### `samples/`: drum samples
- **`AVLDrumkits-BlackPearl-2023/`:** "AVL Drumkits – Black Pearl" by Glen MacArthur, licensed **CC BY-SA**. It's a Pearl kit with Sabian, Zildjian, Paiste and Wuhan cymbals, from the Hydrogen sound library. Downloaded automatically by `test_file_with_audio.py`.
  - **`<num>-<Maker>-<size>-<Piece>-<1..5>.wav`:** 16-bit mono 44.1 kHz one-shots. `<num>` is the kit's own instrument number, which only partly follows GM; `KIT_MAP` translates. `1..5` are velocity layers from soft to hard. Chokes (`50-…CrashChoke.wav`, `52-…RideChoke.wav`) have a single layer.
  - **`drumkit.xml`:** Hydrogen's kit definition (instrument names, velocity ranges, license). It's informational; the renderer doesn't read it.
  - **`blackpearl-h2.png`:** the kit's preview image.
- **`ATTRIBUTION.md`:** credit and license note. If you share renders, the license requires crediting the kit.

---

## Documentation and configuration

| File | Purpose |
|---|---|
| `file_format.md` | The authoritative spec of `.drums.txt`: encoding, header lines, the exact data-line format and regex, ordering rules, and the full MIDI → name table. Anything that reads or writes note files should follow it. |
| `TODO.md` | Deferred work. The main item is tonal instruments (guitar/bass, using string + fret + tuning to get pitch). Also durations, velocities, D.S./coda handling, and verifying how alternate endings are labelled in Songsterr's data. |
| `STRUCTURE.md` | This guide. |
| `requirements.txt` | Lists `requests` and `pytest`. **`numpy` is also required** (audio rendering, comparison and tests) but isn't listed yet. Install with `pip install requests pytest numpy`. |
| `../.gitignore` (repo root) | Excludes generated or downloaded material (`data/`, `out/`, `samples/`) and Python caches (`__pycache__/`, `.pytest_cache/`). |

## Not part of the project
- **`../.Rhistory`** (repo root): an R session history file, unrelated to this code.
- **`../data/`, `../out/`** (repo root): leftovers from when the code sat at the repo root. The current code ignores them; move their contents into `rushingOrDragging/` to reuse the cached downloads and outputs, or delete them.
- **`.DS_Store`:** macOS Finder metadata. `rushingOrDragging/.DS_Store` is committed by accident.
- **`__pycache__/`, `.pytest_cache/`:** Python bytecode and pytest caches, created automatically and safe to delete.

---

## Common workflows

```bash
cd rushingOrDragging                                  # from the repo root

# 1. Import a song's drum part
python cli.py search master of puppets
python cli.py 455118                                  # → out/metallica-master_of_puppets.drums.txt

# 2. Listen to it
python test_file_with_audio.py out/metallica-master_of_puppets.drums.txt

# 3. Grade a real performance (played.drums.txt follows file_format.md)
python compare_performance.py played.drums.txt out/metallica-master_of_puppets.drums.txt

# 4. Make a simulated performance to try the comparer on
python tests/test_drift.py --drift-sd 35 --seed 5
python compare_performance.py out/metallica-master_of_puppets.drift35ms_seed5.drums.txt \
                              out/metallica-master_of_puppets.drums.txt

# 5. Live: stream a performance into the coach (Ctrl-C to stop)
python stream_notes.py out/metallica-master_of_puppets.drift35ms_seed5.drums.txt \
  | python live_compare.py out/metallica-master_of_puppets.drums.txt

# 6. Run the test suite
python3 -m pytest -q
```
