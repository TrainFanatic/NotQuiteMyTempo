# Live drum transcription

This module turns a mono microphone PCM stream into timestamped drum-family events for
`rushingOrDragging`. The native C++ core can run on a worker inside the future Godot game.
The tools here prepare recordings, help annotate them, train a small classifier, replay WAVs
through that same core, and measure its errors.

**Status:** the desktop pipeline is implemented. There is no trained Quest drum model bundled
with it. Collect and annotate your kit recordings before training. Synthetic tests check
software behavior; they do not establish recognition accuracy. Quest microphone capture,
Godot bindings, Android packaging, and device timing calibration are deliberately not part of
this first milestone. The ADTOF checkpoint is an optional offline comparison, not the live engine.

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

## What you need to record

Start with a **30–60 second smoke recording** from the Quest microphone. Listen for clipping,
silence, suppressed low frequencies and changing gain before recording everything. Document the
recording app, microphone options, kit, room and headset position. Use the intended game capture
path when it exists; a different app may process microphone audio differently.

Keep original recordings. Do not denoise, trim away initial silence, or enhance the audio unless
the live path will do exactly that. Natural room sound and the actual headset microphone matter
more than a studio-clean recording. The preparation tool only averages channels and resamples;
it records peak, near-clipping fraction, RMS, sample rate, duration and source hashes. Those
numbers do not replace listening or a device frequency-response check.

As an initial collection, record:

- Every physical kit piece, including every tom and cymbal: roughly 30–50 isolated hits each,
  covering soft, normal and hard playing.
- Common simultaneous combinations: kick + hi-hat, snare + hi-hat, kick + crash, and others you use.
- Several 30–60 second grooves, including fast passages and realistic head movement.
- Silence, cymbal decay, speech and incidental noises, with no drum annotations for non-drum sounds.
- At least three genuinely separate sessions for training, validation and testing. Every family
  must occur in each split. More recordings may be necessary after seeing the errors.

A session folder identifies a recording taken under one capture setup, for example
`quest_room_a_2026_10_24_take1`. Each folder currently contains one WAV and its annotations;
concatenate related takes before annotation or use separate session folders. Do not split a
single take's adjacent windows between training and testing, and do not duplicate audio under
new names. The trainer rejects duplicate prepared PCM and overlapping session names.

```text
data/recordings/
  session_train/
    audio.wav
    audio.capture.json
    labels.csv
  session_validation/
    audio.wav
    audio.capture.json
    labels.csv
  session_test/
    audio.wav
    audio.capture.json
    labels.csv
```

Prepare each working WAV (48 kHz mono float WAV):

```sh
uv run python tools/prepare_recording.py /path/to/original.wav \
  data/recordings/session_train/audio.wav \
  --capture-notes 'Quest microphone; recording app/settings; kit; room; headset position'
uv run python tools/propose_onsets.py data/recordings/session_train/audio.wav \
  data/recordings/session_train/suggestions.csv
```

`audio.wav` contains sound. `labels.csv` is your human answer key, identifying what happened in
that sound. Review the suggestions in an audio editor: assign piece IDs, add missed hits, remove
false candidates, and correct timestamps. Save the reviewed file as `labels.csv`:

```csv
time_s,piece_id
0.500000,kick
0.500000,hihat
0.750000,snare
1.000000,rack_tom_1
```

Times are seconds from **WAV start**, including initial silence. Simultaneous pieces get
separate rows. Use your actual piece IDs from the kit config. Annotation must be accurate enough
to assess the requested timing tolerance; imprecise labels cannot demonstrate ±10 ms accuracy.
Suggestions and ADTOF outputs are never automatically treated as ground truth.

Copy `config/kit.example.json` to `config/kit.local.json`, edit the physical pieces and assign
session directory names to `train`, `validation`, and `test`. All three split lists are required.
The physical piece IDs retain information for a future finer-grained model even though v1
maps them to broad families.

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
