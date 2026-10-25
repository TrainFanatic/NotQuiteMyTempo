# Live drum transcription

This module turns a mono microphone PCM stream into timestamped drum-family events for
`rushingOrDragging`. The native C++ core can run on a worker inside the future Godot game.
The tools here prepare recordings, help annotate them, train a small classifier, replay WAVs
through that same core, and measure its errors.

**Status:** the desktop pipeline and a standalone Quest microphone recorder are implemented.
The recorder has not yet passed a Quest 2 hardware smoke test. There is no trained Quest drum
model bundled with the repo; collect and annotate your kit recordings before training. Synthetic
tests check software behavior, not recognition accuracy. Godot game bindings, the in-game live
adapter, and device timing calibration remain to be implemented. The ADTOF checkpoint is an
optional offline comparison, not the live engine.

## Layout and responsibilities

| Location | Purpose |
| --- | --- |
| `include/drum_transcriber.h` | Public, C-compatible runtime interface and error codes |
| `src/audio_features.cpp` | Shared native FFT and logarithmic band features |
| `src/onset_detector.cpp` | Onset candidates, waveform refinement, time-ordered context |
| `src/classifier.cpp` | Independent sigmoid probabilities and thresholds |
| `src/model_io.cpp` | Strict, versioned model loading from memory |
| `src/transcriber.cpp` | Stream state, gaps, resets, finish, candidate queue |
| `tools/common.py` | Python ctypes bridge, recording/annotation validation, note IO |
| `tools/prepare_recording.py` | Preserve original; downmix/resample a working copy |
| `quest_recorder/` | Standalone Godot Quest microphone recorder and export guide |
| `tools/import_quest_take.py` | Validate and import an exported Quest WAV/metadata pair |
| `tools/propose_onsets.py` | Generate annotation suggestions for human review |
| `tools/train_model.py` | Session splits, L2 training, validation tuning, native export/evaluation |
| `tools/transcribe_wav.py` | Bounded-block replay; live stdout plus saved performance |
| `tools/normalize_reference.py` | Convert a copy of the score into the same six families |
| `tools/evaluate.py` | One-to-one note matching, errors, precision/recall/F1 |
| `tools/benchmark_adtof.py`, `tools/setup_adtof.sh` | Isolated, pinned upstream benchmark |
| `config/kit.example.json` | Physical piece IDs, family mapping, train/validation/test sessions |
| `tests/` | Native behavior, data workflow, training/export, real comparer pipe regression |
| `third_party/kissfft/` | Vendored KissFFT 131.1.0, BSD-3-Clause license and pinned commit |
| `data/recordings/<session>/` | Your `audio.wav`, `labels.csv`, capture metadata; ignored by Git |
| `models/`, `reports/`, `build/` | Generated model, metrics, binaries/dependencies; ignored by Git |

The root README links here. This README is the full setup, data collection, workflow, output,
and integration guide. Generated models carry their own metadata and reports.

## Build and test

Prerequisites: `uv`, a C/C++17 compiler, Git (optional ADTOF only). Commands below assume this
module is the working directory:

```sh
cd '/Users/theyeetsman1/Downloads/UofT/Year 2/CalHacks 13.0 /NotQuiteMyTempo/live_transcription'
uv sync --locked --group dev
uv run cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
uv run cmake --build build --parallel
uv run ctest --test-dir build --output-on-failure
uv run pytest -q
```

The checked-in `uv.lock` resolves the Python tools and CMake. Python 3.11 is suitable; `uv`
creates an isolated environment. No GPU is needed. Native builds have no Python, TensorFlow,
network, or filesystem dependency. CMake can also be used independently of `uv`.

The Python bridge finds `build/libdrum_transcriber.dylib` (macOS), `.so` (Linux), or
`drum_transcriber.dll` (Windows, including `build/Release`). Override with
`DRUM_TRANSCRIBER_LIB` for another build. Only macOS desktop has been exercised here.

## What the detector actually does

1. **Receive PCM.** Input is 48,000 Hz, mono, normalized float32 samples, plus the absolute
   index of the first sample in each block. The native core does not create WAVs or access
   the microphone. The adapter owns capture, buffering, and resampling.
2. **Find likely attacks.** A 1,024-sample Hann-window FFT runs every 240 samples (5 ms).
   Positive changes across 32 logarithmically spaced frequency bands produce spectral flux.
   A local peak above an adaptive threshold proposes an onset. The first strong amplitude
   rise inside that window refines its sample index. Candidates have a 20 ms refractory gap.
3. **Describe the sound around that onset.** The exact same native FFT features are sampled
   at -10, 0, 10, 20, 40, 70, 100, and 140 ms relative to the candidate. These 256 features
   retain their order. The core waits 160 ms for context. A 75 ms later hit may appear in the
   context, but training labels refer only to the candidate's own onset.
4. **Classify independently.** Six L2-regularized logistic classifiers each output a
   probability. Independent thresholds allow kick + hi-hat, for example, at the same time.
   An all-below-threshold candidate emits no note. It remains available in diagnostic output;
   a rejected real hit is a miss, not automatically forgiven by the comparer.
5. **Emit the onset time.** The timestamp is based on the attack sample, not completion of
   classification or pipe arrival. Streaming output stays in time/MIDI order. The runtime
   never shifts your hits toward the sheet music to hide timing errors.

This is a baseline to measure on your own kit, not an already-proven drum recognizer. The
fixed onset thresholds can miss quiet hits or trigger on room noises; classification cannot
recover hits never proposed. Cymbal tails, head motion, overlaps and microphone processing
need held-out testing. A 200 BPM tempo does not allow low-rate audio: frequency content still
matters for distinguishing instruments.

| Family | Representative MIDI | File name |
| --- | ---: | --- |
| kick | 36 | `kick` |
| snare | 38 | `snare` |
| hi-hat | 42 | `hihat_closed` |
| tom | 47 | `tom_low_mid` |
| crash | 49 | `crash` |
| ride | 51 | `ride` |

These are family representatives. A hi-hat prediction does not prove it was closed, and a tom
prediction does not identify its tuning or physical position. Two simultaneous physical pieces
within one family become one family event. The native model format allows up to 16 independent
classes, but the current trainer and text tools intentionally support these six; extending
articulations requires new data, mappings, training, and evaluation.

## Collecting training data on Quest 2

### Quest recorder

The standalone Godot recorder is in [quest_recorder/](quest_recorder/README.md). It requests
`RECORD_AUDIO`, captures the **Quest microphone only** on a muted bus, writes an unprocessed WAV
and capture JSON, and offers export through Android's native file picker. Its Android build and
microphone path still need a **Quest 2 hardware smoke test**. Follow the recorder's README for
installation, the first 30–60 second take, and transfer to the computer. The eventual game must
integrate and verify the same capture path before using this data for its live detector.

The Quest's system screen recorder is designed to save video, and may combine microphone and app
sound into its video. That is not the recommended training source: we need the isolated mic input,
not the backing music, and the game's capture path should match the sound used during play. If you
use a system recording for a quick smoke check, confirm the mic is included and treat the extracted
audio as a temporary check rather than the training master.

Godot documents mic capture using [`AudioStreamMicrophone`](https://docs.godotengine.org/en/stable/classes/class_audiostreammicrophone.html)
and [`AudioEffectCapture`](https://docs.godotengine.org/en/stable/classes/class_audioeffectcapture.html).
The capture effect returns PCM frames that the app can process or save. The training tools do not
need MP3: they prepare an uncompressed WAV working copy at 48 kHz mono. Keep the exported Quest
WAV as the untouched original.

### One-time Quest smoke check

Before the full collection:

1. Install the [Quest recorder](quest_recorder/README.md) and connect the headset to the computer
   using a data-capable USB cable. Put the headset on and accept the microphone permission prompt.
2. Start a new session, record 30–60 seconds: room silence, a few soft/normal/loud hits on kick,
   snare, hi-hat, a tom, crash and ride, then a few seconds of speech. Stop and export the take.
3. On the computer, open the WAV and listen with headphones. Confirm that it contains the kit and
   background sounds, not the in-game backing track; check that hits are not clipped, the signal
   is not mostly silent, and the beginning and end are intact. The capture metadata should match
   the recording. If the file is silent or distorted, fix capture/permission/gain before continuing.

### Full collection recipe

Use the same headset, app capture path, mic settings and physical kit for all three data splits.
A session is one fresh recording take, not a folder label for a split of one long file. Make at
least three separately started sessions and assign one or more whole sessions to each of `train`,
`validation` and `test`. Do not cut one WAV into three splits or copy a take under another name.
Each split needs examples from **all six families**. If the app is intended for different drummers,
kits or rooms, collect additional sessions from those conditions too; three takes of one setup only
test that setup.

For each of the three sessions, use this checklist:

1. **Isolated hits:** for every physical kit piece, record 10 soft, 10 normal and 10 hard hits
   (30 hits per piece per session). Leave enough space to hear each attack and decay; wait for a
   cymbal to stop ringing before the next isolated cymbal hit. Include every tom and cymbal, even
   when several map to one broad family.
2. **Common combinations:** record 10 clean simultaneous examples per session of each combination
   the drummer uses, at minimum kick + hi-hat, snare + hi-hat and kick + crash. Include other
   familiar pairs and some close, fast sequences. These examples teach the classifier that two
   different families can sound together. The v1 output still merges two simultaneous hits from
   the same family into one event.
3. **Grooves and fills:** record three different 30–60 second passages per session: a steady
   ordinary groove, a groove with soft/loud variation and quieter notes, and a fast passage with
   fills near the intended upper tempo (about 200 BPM). Play naturally, including the head motion
   expected in the app. Label every drum hit in these passages, not only the obvious accents.
4. **Non-drum sound:** record at least one minute of room silence and one minute of ordinary
   background sound per session. Include the drummer speaking/counting, breathing or moving as
   naturally happens while playing. Do not add labels for speech or other non-drum sounds; those
   unlabeled onset suggestions become negative examples during training.
5. **Vary the take:** stop and start a new recording for each session. Use fresh performances of
   the exercises. Keep notes about anything that changed (player, room, kit, mic position, app
   build or settings); do not change the capture setup just to make one split easier.

These counts are a practical **starting collection**, not a claim that the model will be accurate
or that this amount is sufficient. Inspect held-out results and add examples for the families,
playing styles and noises it confuses. This recipe creates substantial annotation work: every hit
in the grooves and combinations must be labeled too.

### Files to export and prepare

For each Quest session, export an untouched WAV and the capture metadata. On the computer, keep the
original and make the prepared training WAV alongside it:

```text
data/recordings/
  quest_train_01/
    source.wav                 # original Quest capture, unchanged
    source.capture.json        # exported capture/session metadata
    audio.wav                  # prepared 48 kHz mono float WAV
    audio.capture.json         # generated by prepare_recording.py
    suggestions.csv            # onset proposals; review, never treat as truth
    labels.csv                # completed human-reviewed labels
  quest_validation_01/
    ...
  quest_test_01/
    ...
```

From this module's directory, import the exported take. The importer checks the WAV against its
metadata, copies both originals into a new session folder, prepares the working WAV and writes
onset suggestions:

```sh
uv run python tools/import_quest_take.py /path/to/<take-id>.wav \
  /path/to/<take-id>.capture.json --session quest_train_01
```

Preparation averages channels and resamples a copy; it does not trim or denoise. Keep `source.wav`
unchanged. `audio.capture.json` records the source hash and preparation measurements. Repeat for
every session. The Quest recorder's `source.capture.json` is session/device provenance; these
two metadata files serve different purposes.

### Review and write `labels.csv`

The suggestion tool proposes attack times and writes `REVIEW_REQUIRED` in the piece column. They
are only hints: the detector can miss quiet hits and can suggest speech or cymbal decay. Review the
**entire take**, including sections where it proposed nothing.

1. Open the prepared `audio.wav` in an audio waveform editor and listen while following the
   suggestions in a spreadsheet or text editor. Zoom in around each hit. Use the start of the sharp
   attack—the first clear rapid waveform rise—as the onset, not necessarily the tallest point of
   the waveform.
2. Replace each `REVIEW_REQUIRED` with the physical `piece_id` from your kit configuration. If a
   suggestion is false (for example, speech or cymbal ringing), delete its row. If a real hit is
   missing, add a row at its onset. Check quiet notes and fast fills carefully.
3. Save as a plain CSV named `labels.csv`, with exactly the header `time_s,piece_id`. Times are
   decimal seconds from the **first sample of `audio.wav`**; retain initial silence and do not
   reset time at the first hit or at a musical bar. Use enough decimal places to locate the attack
   within about 10 ms (six decimal places is convenient).
4. For simultaneous hits, put one row per physical piece at the same time. Use the piece IDs from
   `kit.local.json`, such as `kick`, `hihat`, `rack_tom_1`, `crash_2` or `ride`. Do not create rows
   for silence, speech or other sounds with no drum hit.
5. Listen to the WAV once more while checking the finished labels. Confirm every real drum attack
   has a row, every row points to a real hit, IDs are spelled exactly as in the kit file, and all
   times fall inside the recording.

A video editor whose playhead snaps to frames is a poor tool for this job: at 30 frames/second,
frames are about 33 ms apart, coarser than the project's 10 ms timing goal. Use an audio editor
that lets you zoom/read the waveform time at millisecond or finer resolution and turn off coarse
grid snapping. The sound waveform is the reference; a video frame or the score grid is not.

Example `labels.csv`:

```csv
time_s,piece_id
0.500000,kick
0.500000,hihat
0.750000,snare
1.000000,rack_tom_1
```

Times are relative to WAV start, including any leading silence. The current trainer maps piece IDs
to six families and deduplicates same-family notes at the same timestamp. Keep the physical piece
IDs in the labels for clarity and future model improvements, but understand that v1 cannot learn to
count two same-family pieces struck simultaneously.

### Configure the three splits

After preparing and labeling every session, copy `config/kit.example.json` to
`config/kit.local.json`. Keep your kit's physical piece names and map each one to `kick`, `snare`,
`hihat`, `tom`, `crash` or `ride`. Replace the example split names with your complete session
folder names. Put all takes from a split in that split's list; each of train, validation and test
must include the six families. For example:

```json
"splits": {
  "train": ["quest_train_01"],
  "validation": ["quest_validation_01"],
  "test": ["quest_test_01"]
}
```

Then follow [Training and results](#training-and-results) below. The trainer checks session
separation, audio format, label IDs, family coverage and the presence of positive and negative
onset candidates. It stops with an error if a required family is missing.

## Assumptions recorded for this workflow

- The intended training signal is the Quest 2 mic captured through Godot, isolated from the
  backing-track bus. The standalone recorder implements this path, but the headset capture and
  export must pass the smoke check before full collection.
- The recorder saves an untrimmed WAV and capture metadata, requests microphone permission, and
  exports from app-private storage. The game project is not present in this repository; its live
  capture integration remains a separate task. Check that game's mic processing against the
  recorder before treating the collected data as representative.
- Original Quest files are preserved. Desktop preparation creates a 48 kHz mono float WAV for
  training, with no trimming or denoising. Timestamps in labels are relative to that prepared WAV;
  resampling preserves its start time.
- Three independent sessions are the minimum starting split. Entire sessions—not adjacent clips
  from the same take—are assigned to training, validation and test. All six families occur in each.
- Human-reviewed labels are ground truth; onset proposals are fallible aids. Every real hit in
  every recording section, including grooves, is labeled. Non-drum sounds remain unlabeled.
- Initial collection counts (30 isolated hits per piece per session, combination examples, three
  grooves and background recordings) are starting targets only. Held-out measurements determine
  whether more examples are needed.
- V1 predicts six broad families and one event per family at a time. It does not identify exact
  physical drums/articulations or count two simultaneous pieces within one family.

## Training and results

```sh
uv run python tools/train_model.py --kit config/kit.local.json
```

The trainer extracts native onset candidates, associates each human label with its nearest
candidate within 20 ms, and trains only on the training sessions. Noise candidates have all-zero
targets. Each family must have both positive and negative candidates in training and validation;
otherwise training fails with an explanation. Missed candidates remain misses in event-level
metrics. Means/scales come only from training. Validation selects `C` from 0.1, 1, 10 and each
threshold from 0.1 to 0.9 in steps of 0.05. Test data never selects parameters.

Outputs:

- `models/quest2_v1.bin`: portable weights, scaling, thresholds and MIDI classes; about 8 KiB
  for six classes. No pickle or Python runtime required.
- `models/quest2_v1.json`: configuration, data hashes/splits, software versions, selected
  parameters, model hash and explicit `device_validated: false`.
- `reports/quest2_v1.json`: per-session event precision/recall/F1 at ±10 and ±50 ms, matched
  onset error, separate onset-detection and candidate-classification metrics, desktop processing
  time, whole desktop-process peak RSS, and Python/native probability parity.
- `reports/quest2_v1/<split>/<session>.{truth,played}.drums.txt`: human reference and native
  predictions for inspection or the existing comparer.

**Training options (added for the Quest 2 takes).** Defaults were picked on the validation round
(event macro-F1 at ±25 ms), never on test; old behaviour is `--mute-window-ms 0 --c-grid 0.1,1,10`.

- `--mute-window-ms/--mute-db/--mute-drop-db` (240/-75/35, on): `QuestMuteFilter` runs on the
  candidate stream before labels are assigned, in training **and** in the native-replay reports.
  The values go into the model's `.json` (`training_options`), and `transcribe_wav.py --model`
  reads them from there, so live drops the same candidates (verified: identical notes on all 24
  takes). Models without that key (older ones) get no filter live, as before.
- `--c-grid` (default 0.001,0.003,0.01,0.03,0.1,0.3,1,10).
- `--ring-ms` (0, off): unlabelled candidates this soon after a labelled hit are left out of that
  hit's family's loss (still negatives for the others). 50-250 ms was never better on validation:
  the model then fires on the rings, which are false notes live.
- `--pool-windows 1|2|4|8` (8, off): average the 8 time offsets per band (32 or 64 features). The
  pooled model is folded back into the 256-feature v1 format (each offset gets the pooled mean,
  scale and 1/size of the weight), so no runtime change. Worse on validation; the cross-round
  weight agreement improves a little.
- `--class-weight balanced` (off): worse on validation.

Tests: `tests/test_training_options.py`. `models/quest2_v2.bin` / `reports/quest2_v2.json` are the
default run on the `single_*` takes; held-out macro-F1 at ±10 ms 0.39 (v1) -> 0.52.

Choose new `--model` and `--report` paths for another run; previous results are not silently
replaced. Synthetic fixtures exercise training/export but are never saved as your production
model. A usable kit model requires the team's recordings and satisfactory held-out results.

The goals are roughly **150–250 ms feedback delay** and **±10 ms onset timing**. The core's
160 ms lookahead is known; capture, resampling, polling and frame scheduling add delay.
Neither target is a guarantee on Quest. Reports must distinguish missed onsets, wrong families,
wrong onset times, and late event delivery. If the baseline is insufficient, retain the failed
examples and report the limitation before choosing a larger model.

## Score normalization, live feed and saved output

Normalize a *copy* of the exact score before comparing:

```sh
uv run python tools/normalize_reference.py ../rushingOrDragging/out/song.drums.txt \
  ../rushingOrDragging/out/song.families.drums.txt
```

The current comparer tries exact MIDI before family fallback. Without normalization, a broad
tom prediction can match the wrong nearby tom note. Normalization applies the same mapping to
the score, collapses simultaneous notes within one family, and leaves the original score alone.
Unknown percussion fails by default. `--drop-unsupported` explicitly omits it and reports the
MIDI numbers to stderr.

Offline transcription (fast as the machine allows):

```sh
uv run python tools/transcribe_wav.py data/recordings/session_test/audio.wav \
  --model models/quest2_v1.bin --output ../rushingOrDragging/out/played_demo.drums.txt \
  --diagnostics reports/played_demo.jsonl
```

Realtime-paced replay into the existing comparison process:

```sh
uv run python tools/transcribe_wav.py data/recordings/session_test/audio.wav \
  --model models/quest2_v1.bin --realtime \
  --output ../rushingOrDragging/out/played_live.drums.txt \
  | uv run python ../rushingOrDragging/live_compare.py \
      ../rushingOrDragging/out/song.families.drums.txt --clock line --plain
```

Use a recording of that song for a meaningful comparison. `--song-origin` is the song time at
capture sample zero. For a recording starting two seconds before the song downbeat, use
`--song-origin -2`. Pre-song events are excluded from the note stream. Labels for training
still use WAV-relative times. Do not align the first drum hit to zero if the song starts with rests.

The default performance path is `../rushingOrDragging/out/played_<UTC-session>.drums.txt`.
Stdout contains only the canonical header and note lines; diagnostics go to stderr or the
optional JSONL file. Each classified batch is flushed to stdout immediately. The saved file is
flushed at least every approximately 250 ms while replay progresses and on completion; this
makes data visible to readers but is not an `fsync` power-loss guarantee. Flush timing depends
on worker scheduling. The file contains note events, not audio. Replay holds only a small block
of WAV audio at a time. Replay diagnostics distinguish sample-based algorithmic delay from
wall-clock delivery delay (available with `--realtime`); neither includes real microphone capture
latency. Peak RSS measures the entire desktop process, not isolated core or Quest memory.

**The comparer does not tail a regular file.** Use the pipe or native event delivery for live
feedback. Keep `--clock line` so inference delay does not count as the drummer playing late.
The saved file is for replay and inspection. No confidence or extra columns are added to the
[authoritative format](../rushingOrDragging/file_format.md):

```text
# time_s  midi  name
1.250000  36  kick
1.250000  42  hihat_closed
```

**Onsets only (no model), added for the team demo.** `--onsets-only` replaces `--model`: the
same native core, pacing and `--song-origin`, but with no classifier every onset is printed as
`time_s  0  onset` (drum unknown, [file_format.md §10](../rushingOrDragging/file_format.md)).
`hub/bridge.py` names each onset from the sheet (its `ScoreGate`); `live_compare.py` would count
these lines as extras, so pipe them to the bridge instead:

```sh
uv run python tools/transcribe_wav.py take.wav --onsets-only --song-origin -2 --realtime \
  | ../.venv/bin/python -m hub.bridge ../rushingOrDragging/out/song.drums.txt --bpm 94
```

**Quest mute filter (onsets only; with `--model` when the model was trained with it, see Training options).** On real Quest 2 takes the OS mic processing mutes the input to
the noise floor (about -90 dB) roughly 0.1 s after a loud attack and un-mutes it ~0.05 s later, so a
still-ringing drum looks like a new attack 0.15-0.2 s after the real one. `QuestMuteFilter`
(`tools/common.py`) drops an onset when the last kept onset was under `--mute-window-ms` (240)
before it **and** the 2 ms level dipped in between below the higher of `--mute-db` (-75 dBFS) and
that onset's peak minus `--mute-drop-db` (35 dB); a real double stroke dips only ~16 dB and is
kept. This is the rule `datacollection/label.py` uses to label these takes. It only reads audio
already delivered, so it adds no latency; `--mute-window-ms 0` turns it off.

Measured on the eight `single_*_01` takes (272 labelled strokes, match within 30 ms): extra onsets
356 -> 229, matched strokes 247 -> 247, timing vs labels unchanged (median +0.2 ms, p95 6.6 ms).
The window is 240 ms, not 300, because a closed hi-hat is really played 0.28 s apart: at 300 ms
the hi-hat take lost 3 strokes. Crash/ride re-attacks beyond 240 ms therefore get through. What
remains is mostly the core re-triggering 20-120 ms into a ring with no mute in between (floor tom
and kick); the bridge's score gate drops those.

**Live input (added for the team demo).** `--input` replaces the WAV path; the same native core,
mute filter and `--model`/`--onsets-only` choice, until Ctrl-C/SIGTERM (summary on stderr, input
stats every 10 s). Code: `tools/live_input.py`; tests: `tests/test_live_input.py`.

```sh
# laptop microphone (sounddevice, 48 kHz mono float32, 10 ms blocks); mic:<name|index> picks one
../.venv/bin/python tools/transcribe_wav.py --input mic --onsets-only --clock monotonic
# the Quest's mic, streamed as mic.pcm hub frames by quest_app/scripts/game/mic_streamer.gd
../.venv/bin/python tools/transcribe_wav.py --input hub:ws://localhost:8765 --onsets-only --clock monotonic
```

`sounddevice` and `websockets` are imported only for these modes; they are in the repo's
`.venv`, not in this project's lock (`uv run --with sounddevice --with websockets ...` also works).
`--clock monotonic` prints `time_s` on the laptop clock (`time.monotonic()`, the hub's clock,
`fletcher.contract.now()`) so the bridge can convert with the session's `t0`; the default `song`
clock is `--song-origin + sample/48000` as before. A WAV with `--realtime` can use it too (sample 0
= replay start).

- **Laptop mic dating:** PortAudio's `inputBufferAdcTime` (or arrival minus the block) minus the
  stream's reported input latency (`--mic-latency-ms` overrides). On macOS the ADC time leaves the
  device latency out (41.5 ms on a MacBook Pro mic). Speaker->mic loopback against PortAudio's DAC
  time: 8/8 clicks found, dated +2.4 to +2.9 ms late; **hit -> line on stdout 212 ms** (41.5 input
  latency + the core's ~170 ms context wait).
- **Quest stream:** 16-bit mono frames of ~50 ms at the headset's mix rate (44.1 kHz on Quest 2),
  resampled to 48 kHz by a streaming windowed sinc (matches `prepare_recording.py`'s polyphase
  resample to -52 dB). Frames are put back in `first_sample` order; a missing `seq` waits
  `--reorder-ms` (60) and is then a gap, a `first_sample` jump with the next `seq` (the headset's
  capture discarded frames) is a gap at once; the core restarts its acoustic state across a gap
  (`DT_GAP`). Device `local_t` -> laptop clock with running-minimum offsets, symmetric (NTP style)
  when the frame carries the headset's `clock_offset`. Test: the real 44.1 kHz `single_snare_01`
  take streamed through `hub/server.py` with jitter, a reordered pair, a lost frame and a capture
  gap lands on the labels as well as the WAV replay does (13 labels, systematic +0.4 ms, the one
  label the replay gets 16 ms wrong the stream gets right); **hit -> line 214 ms p50**, 289 max.
  The real Godot `MicStreamer` through `FletcherHub` and the hub on this Mac: 7/7 synthetic
  clicks within +0.3 ms, 209 ms. Not yet included: the Quest mic's own input latency (hardware
  -> Godot mix), which nobody has measured; set `MicStreamer.input_latency_s` once it is.

For direct scoring of exported notes:

```sh
uv run python tools/evaluate.py reports/quest2_v1/test/session_test.truth.drums.txt \
  reports/quest2_v1/test/session_test.played.drums.txt
```

## Optional ADTOF comparison

[ADTOF](https://github.com/MZehren/ADTOF) and its
[paper](https://arxiv.org/abs/2111.11737) provide a useful pretrained baseline. The selected
`Frame_RNN_adtofAll_0` checkpoint is pinned to commit
`b3968fb332f69b65ee07c089fc62f436503755db`. Its labels are bass drum, snare, tom, hi-hat,
and **combined crash/ride**; a six-label mapping elsewhere in that repo does not make this a
six-output checkpoint. Its bidirectional recurrent layers use future context. We do not claim
it meets the live Quest delay or compute budget.

Run the benchmark in a separate Python 3.10 / TensorFlow 2.15 environment. Setup downloads
upstream source, the checkpoint and scientific dependencies (several hundred MB), retaining
upstream LICENSE and attribution. It does not install TensorFlow into the native tools env.

```sh
bash tools/setup_adtof.sh
.venv-adtof/bin/python tools/benchmark_adtof.py data/recordings/session_test/audio.wav \
  --reference reports/quest2_v1/test/session_test.truth.drums.txt \
  --output reports/adtof_session_test.drums.txt --report reports/adtof_session_test.json
```

Upstream preprocessing remains intact: 44.1 kHz audio, 2,048-point FFT, 100 frames/sec,
12 logarithmic bands/octave and the original peak picking. The benchmark loads the actual
checkpoint and fails if it is missing; it never silently evaluates random weights. It compares
five families by merging human crash/ride labels. `tools/evaluate.py --merge-cymbals` also permits
an apples-to-apples five-family evaluation of our native output. Keep the six-family report
separately so crash-versus-ride errors are visible.

Direct optional dependency versions are in `config/adtof-requirements.txt`; the verified macOS
arm64 environment is pinned in `config/adtof-constraints.txt`. Setup records all resolved packages in `build/adtof-resolved.txt`. Upstream ADTOF and madmom commits are pinned.
ADTOF's README/LICENSE specify **CC BY-NC-SA 4.0**. Preserve its notices and attribution when
sharing applicable artifacts; the agreed use here is noncommercial. Our six-family model is
trained on your annotations, not on ADTOF's generated predictions.

## Native contract and future Godot adapter

`dt_create(model_bytes, length)` validates and copies the model. Pass `NULL, 0` for annotation
features without a classifier. `dt_reset(handle, song_origin_s)` starts a fresh session.
`dt_process(handle, mono, count, first_sample)` accepts 1–4,800 samples; poll all candidates
after every block. `dt_finish` zero-pads missing final context without inventing post-EOF hits;
final classifications may be less reliable. Destroy the handle after use. All calls for a
handle must stay on one worker thread.

A candidate contains the capture sample, song time, readiness sample, probabilities, MIDI array,
threshold mask, and the features used for training. For every set mask bit, emit one event.
All-zero masks are diagnostics only. Consumers should sort same-time MIDI outputs (the shipped
model already uses ascending MIDI). Negative song-time candidates belong to the count-in.

The ABI accepts only finite PCM in [-1,1] and rejects backwards/overlapping input. A forward gap
returns `DT_GAP`, increments statistics and drops pending incomplete contexts, while preserving
the absolute timeline. Handle that as lost capture data, not player errors. `DT_OVERFLOW` also
signals an undrained queue or allocation failure: drain/reset appropriately; after an allocation
failure, reset or recreate the session. Reset clears all prior audio, candidates and statistics.
The rolling audio buffer is capped at 24,000 samples (0.5 s); normal operation additionally holds
FFT workspaces, model and bounded per-block candidates. It allocates on the worker; it is **not**
a hard-realtime audio-callback API. Profile actual memory/CPU on Quest before deployment.

The binary model is little-endian: magic `DTRN0001`; five uint32 values (schema=1, rate=48000,
features=256, class count, lookahead=7680); float32 means[256] and positive scales[256]; then each
class's uint32 MIDI, float32 bias and threshold, and float32 weights[256]. Size, finite values,
class limit and unique MIDI codes are validated. Feature schema 1 includes the current onset and
feature constants; changing those requires a schema/version change and retraining.

The later adapter should:

- Capture from `AudioStreamMicrophone` into an `AudioEffectCapture` bus without audible monitoring.
  Enable audio input and Android microphone permission. Godot supplies stereo float32 PCM;
  downmix and, if necessary, continuously resample to 48 kHz while preserving the capture clock.
- Transfer small blocks to one worker. Account for `get_discarded_frames()` and signal gaps.
  Load model bytes from the packaged game and call this ABI through a GDExtension/Android library.
  No subprocess, Python, or TensorFlow is needed for this detector on the headset.
- Establish song origin from the actual backing-track playback and capture clocks, compensating
  for measured capture/output latency. Sample counting alone cannot reveal microphone latency.
  The capture API does not expose hardware timestamps; calibrate the actual route and track drift.
  Do not calibrate by shifting the player's performance onto expected notes.
- Send classified events directly to the game/comparison component. Write optional performance
  logs beneath `user://performances/`, which is Android app-private storage, not the desktop repo.
- Package an Android arm64 build using the game's actual Godot/NDK versions. The teammate's current
  Python comparer also needs an independent in-game port or adapter; this module does not solve that.

Here “background” means a worker while the game is running, not an Android background service.
Relevant Godot contracts:
[AudioEffectCapture](https://docs.godotengine.org/en/stable/classes/class_audioeffectcapture.html),
[AudioStreamMicrophone](https://docs.godotengine.org/en/stable/classes/class_audiostreammicrophone.html),
[audio synchronization](https://docs.godotengine.org/en/stable/tutorials/audio/sync_with_audio.html),
[data paths](https://docs.godotengine.org/en/stable/tutorials/io/data_paths.html).

## Validation boundaries

Tests cover chunk-size invariance, fast attacks, simultaneous family output, negative song origin,
rejected candidates, silence/ringing, finish/reset/gaps, malformed model/input, float inference
parity, session leakage, preparation, metrics, the tom-matching regression, training/export, and
a real pipe to the existing comparer. They do not validate physical instrument recognition.

Before calling this ready for the game, collect the Quest data, inspect held-out family errors
and onset timing, compare ADTOF on the same human truth, and measure capture-to-feedback latency,
CPU and memory on-device. No headset performance or accuracy claim follows from desktop tests.

### Checks run during implementation

- Release build and 21 Python workflow/native tests passed.
- Native CTest passed under AddressSanitizer and UndefinedBehaviorSanitizer.
- Existing comparer/streaming suite: 29 passed, five skipped (downloaded song fixture absent).
- The pinned ADTOF checkpoint loaded and processed a six-second synthetic WAV through its
  original frontend and peak picker. This validates the benchmark command, not drum accuracy.
- Upstream TensorFlow may warn about unused optimizer checkpoint state, and madmom may warn
  about a non-audio WAV metadata chunk. Inference weights are checked separately.
