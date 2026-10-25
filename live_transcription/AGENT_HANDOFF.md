# Live transcription handoff for another agent

Updated 2026-10-24. Read [README.md](README.md) for the authoritative technical and data
workflow, [quest_recorder/README.md](quest_recorder/README.md) for headset recording, and
[`../rushingOrDragging/STRUCTURE.md`](../rushingOrDragging/STRUCTURE.md) for the teammate's score
and comparison code. This file preserves the decisions and outstanding work from the Codex
conversation; it is not a claim that the hardware path or model accuracy has been validated.

## Objective and ownership

NotQuiteMyTempo is an educational Quest 2 VR drumming game. This user's part detects **which drums
were struck and when** from the Quest 2 microphone. Their teammate owns the Songsterr score
conversion and rushing/dragging feedback in `rushingOrDragging/`. The backing track is played
from stored audio and is not meant to enter the microphone capture bus. The transcription
component must eventually send timestamped family/MIDI events to the game/comparer. The user
wants the full workflow to run on Quest 2; a desktop WAV replay is only an intermediate test.

The Quest 2 is connected to a **different macOS computer** with Developer Mode enabled. The user
had no recorder before this work. No Quest WAV/JSON pair, reviewed labels, trained kit model, or
headset performance measurements have been provided or verified yet. The existing standalone
recorder has not yet been tested on the headset.

## Present implementation

- `quest_recorder/` is a standalone Godot 4.7.2 **2D** Android app. It requests microphone access,
  routes `AudioStreamMicrophone` through a muted `QuestMic` bus with `AudioEffectCapture`, writes
  stereo float32 PCM to `source.wav` at the actual Godot mix rate, and writes
  `source.capture.json`. It exports both through Android's native file picker. It is a data
  collection tool, not the VR game or a live classifier.
- A signed arm64 debug APK was built and checked locally at `build/quest_recorder.apk` (about
  27 MB). `build/` is Git-ignored, so that binary is **not transferred by cloning/pulling** the
  repo. Copy it directly to the headset-connected Mac, or rebuild from
  `quest_recorder/project.godot` with matching Godot 4.7.2 Android export templates. The APK
  contains `RECORD_AUDIO`. Godot parsing, WAV encoding, Android export/signature, and a synthetic
  import passed local checks. Quest mic input and file-picker export still need a smoke test.
- `tools/import_quest_take.py` validates an exported `TAKE_ID.wav` and matching
  `TAKE_ID.capture.json`, preserves both, creates `audio.wav` at 48 kHz mono, and writes
  `suggestions.csv` with onset proposals. It rejects invalid capture metadata, dropped frames,
  mismatched WAV properties, and nearly silent recordings.
- `src/`, `include/drum_transcriber.h`, and the Python tools implement a streaming C++ onset
  detector, six independent logistic classifiers, desktop training/evaluation, and WAV replay.
  This C++ core is **not yet bound to the Godot VR game**. No trained Quest kit model ships.
- Current `.drums.txt` output is the teammate's `time_s  midi  name` format; see
  [`../rushingOrDragging/file_format.md`](../rushingOrDragging/file_format.md). For live feedback,
  pipe events or deliver them directly. `live_compare.py` does not tail a regular output file.

## Detection model and limitations

The runtime input contract is mono float32 PCM at 48,000 Hz plus an absolute first-sample index
for each block. It computes a 1,024-sample FFT every 240 samples (5 ms), identifies spectral-flux
peaks as onset candidates, and extracts 256 features over approximately -10 through +140 ms.
Classification waits about 160 ms for context. Six L2-regularized logistic outputs have
independent thresholds, so different families can be emitted at the same onset. This small
trainer runs on a normal CPU; no GPU cluster is needed. The 200 BPM upper tempo does **not**
justify a low audio sample rate, since transients and timbre need waveform resolution.

The six family/MIDI representatives are kick 36, snare 38, hi-hat 42, tom 47, crash 49, ride 51.
Physical pieces such as two rack toms and two crashes retain distinct `piece_id` names in the
human labels, but map to families in `config/kit.local.json`. This version cannot distinguish
open/closed hi-hat, tom tuning/position, or count two simultaneous physical pieces within one
family. It can represent simultaneous different-family hits. Speech, room sounds, motion, and
cymbal decay may create false onset proposals; collect realistic negatives and evaluate them.
The classifier cannot recover a true hit the onset detector never proposes.

## Immediate headset smoke check

1. Copy `build/quest_recorder.apk` from the original Mac to the headset-connected Mac. Install
   via `adb install -r quest_recorder.apk` or Meta Quest Developer Hub. If that APK is unavailable,
   follow `quest_recorder/README.md` to build it there.
2. Open **NQMT Quest Recorder**, allow mic permission, and record 30-60 seconds: quiet, a few
   soft/normal/hard hits across all six families, then a spoken sentence. Stop with the button.
3. Export **both** the WAV and matching `.capture.json` into Downloads and transfer both to a Mac.
   Confirm the WAV has audible drums and speech, no intended backing track, and no clipping or
   missing beginning/end. Confirm JSON `valid_for_training: true`, `discarded_frames: 0`, and
   sensible duration/peak. Do not collect the full dataset until this route works.
4. From `live_transcription/`, import the smoke pair with `tools/import_quest_take.py` (command
   below) and verify the prepared WAV and `suggestions.csv` appear.

The Quest app's saved `user://takes/` directory is app-private. The export step matters. If its
Android picker fails, capture the exact on-screen error and fix export before data collection.

## Full recording and annotation recipe

Make at least **three separately started, freshly played takes**, assigned to train, validation,
and test. Do not split or duplicate one WAV across those sets. In **each take**, include all six
families and every physical kit piece: 10 soft, 10 normal, and 10 hard isolated hits per piece;
around 10 examples of each common combination (at least kick+hi-hat, snare+hi-hat,
kick+crash); three distinct 30-60 second passages (steady repeating groove, varied dynamics,
and a faster groove with fills); about one minute of quiet and one minute of ordinary background
sound including speech/counting and movement. A groove is a repeating beat; a fill is a brief
variation that leads back into the beat. Wear the headset in normal playing position. Record the
actual mic conditions expected in the game. Keep original WAVs untrimmed and unprocessed;
extremely cleaned-up studio samples would hide real Quest noise conditions.

Import each exported take from `live_transcription/`:

```sh
uv run python tools/import_quest_take.py /path/to/TAKE_ID.wav \
  /path/to/TAKE_ID.capture.json --session quest_train_01
```

Repeat with unique names such as `quest_validation_01` and `quest_test_01`. The importer creates
`data/recordings/<session>/source.wav`, `source.capture.json`, `audio.wav`,
`audio.capture.json`, and `suggestions.csv`. `data/` is Git-ignored. Humans then create one
`labels.csv` **in each session folder**, with exactly `time_s,piece_id` as header. Times are
seconds from the **first sample of that session's prepared `audio.wav`**, including leading
silence, using the beginning of the attack and aiming for about 10 ms precision. Example:

```csv
time_s,piece_id
1.250000,kick
1.250000,hihat
1.750000,snare
```

One row is required per real hit. Simultaneous different-family hits can have identical times;
their CSV row order is irrelevant because the trainer sorts labels. If attacks differ by a few
milliseconds, retain their actual separate times. Use physical piece IDs from the kit config.
Listen through the **whole** WAV: `suggestions.csv` can miss hits or propose speech/cymbal tails.
Replace its `REVIEW_REQUIRED` entries, add misses, remove false suggestions, and leave non-drum
sounds unlabeled. The Quest `.capture.json` is generated by the app, not written by hand.

macOS can open WAVs natively in QuickTime Player for listening. For labels, use a waveform editor
such as Audacity: open `audio.wav`, zoom in on the beginning of an attack, read the cursor time,
and write that time in the CSV. Avoid a video editor limited by video-frame snapping.

## Desktop setup, training, and outputs

From `live_transcription/`:

```sh
uv sync --locked --group dev
uv run cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
uv run cmake --build build --parallel
uv run ctest --test-dir build --output-on-failure
uv run pytest -q
```

Copy `config/kit.example.json` to `config/kit.local.json`; map every physical `piece_id` to one
of the six families and list the actual session folder names in `train`, `validation`, and
`test`. Every split must contain all families. Train with:

```sh
uv run python tools/train_model.py --kit config/kit.local.json
```

This produces `models/quest2_v1.bin`, model metadata JSON, `reports/quest2_v1.json`, and
per-session truth/prediction `.drums.txt` files. Check held-out misses, false positives,
family confusion, and onset error; collect more data if needed. Synthetic software tests do
not establish recognition accuracy. A desktop replay command is:

```sh
uv run python tools/transcribe_wav.py data/recordings/quest_test_01/audio.wav \
  --model models/quest2_v1.bin \
  --output ../rushingOrDragging/out/played_demo.drums.txt
```

Normalize a **copy** of the sheet music to the same six families before score comparison; the
original score may distinguish articulations the current detector does not. The default saved
performance file is under `rushingOrDragging/out/played_<UTC-session>.drums.txt` and is for
inspection/replay. Realtime-paced WAV replay exists but does not capture a Quest mic live.

## Remaining game integration work

Find the actual Godot VR game project (it was not in this repo when the recorder was built), then
capture mic-only PCM on a muted bus while the backing track stays separate. Downmix/resample to
48 kHz with a stable capture clock. Build the C++ core for Android arm64, bind its C API through
a Godot GDExtension/Android library, load the trained model, and process small blocks on one
worker thread. Track discarded frames/gaps. Align mic event timestamps with backing-track
playback by measuring Quest input/output latency and drift; never snap a drummer's hits to score
times. Deliver events to the in-game comparison component. The teammate's Python comparer also
needs an in-game port or adapter. Profile accuracy, capture-to-feedback latency, CPU, and memory
on the actual Quest. Intended targets were roughly 150-250 ms feedback and +/-10 ms onset timing;
these are goals, not validated device results.

## External projects discussed

- [ADTOF](https://github.com/MZehren/ADTOF), with paper
  [arXiv:2111.11737](https://arxiv.org/abs/2111.11737), is already an optional **offline**
  benchmark through `tools/setup_adtof.sh` and `tools/benchmark_adtof.py`. The pinned checkpoint
  has five labels, with crash and ride merged; it has future context and is not assumed to meet
  Quest live latency. Its stated license is CC BY-NC-SA 4.0; the user said the project is
  noncommercial. Do not train on its predictions as ground truth.
- [drumtranscription_pd](https://github.com/nkundiushuti/drumtranscription_pd) is a Pure Data
  streaming transcription program/demo for kick, snare, and hi-hat. It can inspire or benchmark
  onset methods, but is not Quest-ready and uses Pd-extended. Its README says patches and some C
  externals are GPL. It links to a **separate** overlapping-sounds dataset.
- [ADTLib](https://github.com/CarlSouthall/ADTLib) is an offline WAV transcription program
  with pretrained kick/snare/hi-hat models, a BSD 2-Clause license, and older TensorFlow
  `contrib`/bidirectional RNN code. It is a possible three-family benchmark, not a live Quest
  replacement.

These programs are **not reference datasets**. Human-reviewed Quest `labels.csv` files are the
reference. Run competing systems on the **same** labeled Quest recordings when comparing them,
and account for their narrower label sets. An earlier linked ISMIR few-shot paper led to a
996-page proceedings PDF; verify direct paper links before citing that work.

## Prior side topics and current next action

`.ruff_cache` is a generated linter cache, not audio or training data. The user also asked for a
Codex session identifier during the conversation, but its exact value is not available in this
handoff. The next concrete action is the **Quest smoke take and WAV/JSON transfer**. An agent
should inspect those real files and the user's headset results before claiming capture or the
training workflow works end to end.
