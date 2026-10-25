# Quest 2 training recorder

This is a small **2D Godot app for collecting microphone-only WAV takes** on a Quest 2. It is a
separate development tool, not the VR game or a trained drum detector. It uses the same Godot
`AudioStreamMicrophone` → `AudioEffectCapture` path planned for the game. The microphone bus is
muted so playing does not echo through the headset, and no backing track is routed to that bus.
The game may eventually choose different microphone settings; compare a sample from its final
capture path before relying on this training set.

**Hardware status:** the project opens and runs in Godot 4.7.2 on macOS. Its WAV encoder has
been checked by reading a generated WAV with `soundfile`. It has **not yet been run on your
Quest 2**; the first 30–60 second smoke take below is the device check.

## Install on the Mac connected to the headset

The current Mac has a debug APK at `live_transcription/build/quest_recorder.apk` (the `build/`
directory is ignored by Git). Copy that APK to the Mac connected to the Quest, for example with
AirDrop or a USB drive. If it is unavailable on that Mac, build it from this folder:

1. Install [Godot 4.7.2 standard](https://godotengine.org/download/archive/4.7.2-stable/)
   and the **matching 4.7.2 export templates**. Open `project.godot` in the editor.
2. Configure Android export on that Mac using [Godot's Android instructions](https://docs.godotengine.org/en/4.7/tutorials/export/exporting_for_android.html):
   OpenJDK 17 and the Android SDK/platform tools. This project includes an Android export preset
   for arm64, a launcher app, and `RECORD_AUDIO`. In the editor, verify those three options.
3. Export **debug APK** from Project → Export → Android to `quest_recorder.apk`. The project has
   no signing secrets; Godot's debug signing is sufficient for local collection.
4. If using the prebuilt APK, skip the build steps above. Enable Developer Mode in the Meta Horizon
   phone app, connect the Quest with a data-capable USB
   cable, put it on, and accept the USB debugging prompt. Then install the APK with `adb install -r
   quest_recorder.apk` or Meta Quest Developer Hub. Find the sideloaded app under Library →
   Unknown Sources. [Meta's device setup guide](https://developers.meta.com/vr/documentation/android-apps/enable-developer-mode/)
   covers those headset steps.

This standalone recorder opens as a 2D panel in the headset. A normal Android app can run on
Quest in a panel; an immersive VR scene is not required for these WAV takes.

## First smoke take

1. Put on the headset in playing position and open **NQMT Quest Recorder**. Allow its microphone
   permission. If the status still asks for access, tap **Request mic permission** and grant it.
2. Name the take `quest_smoke_01`, fill in player ID, kit ID and room/setting notes, and tap
   **Start new take**. Record 5 seconds of quiet, a few hits on each family at soft/normal/hard
   strength, then a short spoken sentence. Aim for 30–60 seconds total.
3. Tap **Stop and save**. The app shows duration and peak level. If it reports zero frames or
   dropped frames, retake it. If it stopped because the app paused, retake it; a full data take
   should finish with the Stop button. Do not remove the headset while recording.
4. Select the saved take. Tap **Export WAV** and save it to the headset's **Downloads** folder with
   the suggested filename. Tap **Export metadata** and save its matching `.capture.json` there.
   You must export both files. Export does not delete the original files in the app.
5. Transfer both files to the Mac. In Meta Quest Developer Hub, use File Manager, or use ADB:

   ```sh
   adb devices
   adb shell ls /sdcard/Download
   adb pull /sdcard/Download/<take-id>.wav .
   adb pull /sdcard/Download/<take-id>.capture.json .
   ```

   Replace `<take-id>` with the exact filenames shown by the `ls` command. If the picker saved to
   another folder, browse for it in MQDH. Connect with a data cable and allow data access on the
   headset if file transfer is blocked.
6. Listen to the WAV on the Mac. The kick, snare, hi-hat, tom, crash, ride and speech should be
   audible; any game music should be absent. Open the JSON: `valid_for_training` must be `true`,
   `discarded_frames` must be `0`, and `peak` must be well above zero. A near-clipping fraction
   above zero means inspect the loud hits. This checks the route before you spend time on a full
   dataset.

The app saves stereo 32-bit float PCM at Godot's actual mix rate. It writes small blocks to disk
as they arrive, then closes the WAV header when you stop. If recording is interrupted, the app
closes the take and marks it unsuitable for training. Godot's capture effect reports frames lost
from a full ring buffer; the app records that count in the JSON. Keep the original WAV unchanged.

## Import into the training workflow

First build the existing native library and install the Python tools (from `live_transcription/`):

```sh
uv sync --locked --group dev
uv run cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
uv run cmake --build build --parallel
```

Import each exported WAV/JSON pair with a unique local session name:

```sh
uv run python tools/import_quest_take.py /path/to/<take-id>.wav \
  /path/to/<take-id>.capture.json --session quest_smoke_01
```

The importer checks that the audio matches its metadata and has no dropped frames. It copies
both originals into `data/recordings/quest_smoke_01/`, creates `audio.wav` at 48 kHz mono, and
writes `suggestions.csv` with proposed hit times. Review **every** actual drum hit in an audio
waveform editor, replace `REVIEW_REQUIRED` with the physical piece ID from your kit config, add
missed hits, remove non-drum suggestions, and save the result as `labels.csv`. The wider
[collection and annotation guide](../README.md#collecting-training-data-on-quest-2) explains
exactly what to play and how to split three sessions for training, validation and testing.

For the smoke take, you only need to confirm capture, transfer and preparation. Then use this
same workflow for `quest_train_01`, `quest_validation_01`, and `quest_test_01` with the complete
collection recipe. The Quest hardware recording and export path still needs testing before its
files can be treated as valid training data.

## Source and export details

- `default_bus_layout.tres` isolates microphone audio and keeps monitoring muted.
- `recorder.gd` records stereo float32 WAV and session metadata under `user://takes/`, then
  exports via Android's native file picker. The app stores all previous takes until uninstalled
  or its app data is cleared; select any saved take to re-export it.
- `export_presets.cfg` selects arm64, Android launcher mode and microphone permission. The
  package ID is `org.notquitemytempo.questrecorder`.
- `tests/wav_format_smoke.gd` checks the actual WAV header and samples on desktop. It does not
  test Quest microphone permission, on-device timing or native file-picker export.

The app uses Android's native Storage Access Framework in Godot 4.7 to export to a user-selected
location. [Godot 4.6 introduced this file picker](https://godotengine.org/article/godot-4-6-all-about-your-flow/).
If the Quest's file picker does not work in this build, preserve the take in `user://takes/`,
report the exact on-screen error, and fix export before recording the full dataset.
