# Quest 2 headset test (USB, no controllers, Quest mic)

Everything runs from the repo root, `/Users/aaronwang/NotQuiteMyTempo`. Here `python` means
`.venv/bin/python` and `adb` means `/opt/homebrew/bin/adb`. Run adb outside the sandbox.
**Don't run adb more than you need to.** Every time the Quest asks for USB debugging again, its
mic can get stuck sending zeros until you reboot it. Calibration catches this ("the Quest mic sent
only zeros").

APK: `quest_app/build/not_quite_my_tempo.apk` (debug, signed with the Godot debug key, package
`com.notquitemytempo.app`, arm64, OpenXR + Meta loader, passthrough required). It boots straight
into game mode, uses hub `ws://127.0.0.1:8765` and the QUEST mic (export feature `nqmt_game`).
To rebuild it:
`JAVA_HOME=/opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home <Godot 4.5> --headless --path quest_app --export-debug Quest build/not_quite_my_tempo.apk`

## 0. Install and start (one adb session)

```sh
adb devices                                   # one device, "device" (not "unauthorized")
adb install -r quest_app/build/not_quite_my_tempo.apk
adb shell pm grant com.notquitemytempo.app android.permission.RECORD_AUDIO   # no Allow dialog needed (no controllers)
adb reverse tcp:8765 tcp:8765                 # hub
adb reverse tcp:8766 tcp:8766                 # library: backing.ogg / arrangement.mid downloads
adb reverse --list                            # both listed
```

Terminal A (laptop services: hub, library/console, session, bridge, Quest-mic transcriber, Fletcher):

```sh
.venv/bin/python -m nqmt --usb --mic quest --record-mic-pcm          # add --live for Claude+ElevenLabs Fletcher
```

Wait for `nqmt ready`. It prints the console (`http://127.0.0.1:8766/`) and the log folder
`recordings/nqmt-<time>/` (hub.log, bridge.log, mic1.log, mic_01_quest.drums.txt, ...).

Terminal B (headset log):

```sh
adb shell am start -n com.notquitemytempo.app/com.godot.game.GodotApp
adb logcat -c; adb logcat -s godot:V | grep -v -E "^\s*$"
```

Put the headset on. Passthrough comes on, Terence walks in, then the menu appears on the music
stand. Check: `hub.log` shows a client joining, and the console page shows the songs. In the
headset, Settings should show `127.0.0.1` and mic QUEST. If `PlayerRig` errors about passthrough
in logcat, the app can't run.

## 1. Calibrate (headset on its stand or worn, room quiet, nobody playing)

```sh
.venv/bin/python -m nqmt.calibrate --mic quest
```

You should hear 8 claps, 1 s apart. The tool prints `round trip (quest mic): X ms median, spread Y`,
saves it to `calibration.json` (entry `quest`), and publishes `calibrate.result`. `bridge.log` then
says `input latency X ms (mic quest, calibrated)`. That is what the bridge subtracts from every hit
from now on, and from startup next time.
- Every clap's delay is in `recordings/calibrate-<time>/result.json` (`delays_ms`, `all_onsets`).
- Failures: "no mic.pcm" means the app isn't in game mode or the mic is LAPTOP. "only zeros" means
  the mic is stuck: reboot the Quest. "only k/8 claps heard" means try `--gain-db 6`. "disagree"
  means a noisy room.
- Optional: the laptop mic's round trip too (the drummer still hears the Quest):
  `.venv/bin/python -m nqmt.calibrate --mic laptop`.
- If you change Settings > latency in the headset, calibrate again.

## 2. Play a song from the console

Open `http://127.0.0.1:8766/`, pick a song (e.g. `bond_demo`), and press Play. Or:
`curl -s -XPOST localhost:8766/api/command -d '{"action":"start","song_id":"bond_demo"}'`.
Expected: count_in, then Terence's 5-6-7-8, then the backing from the downbeat with the band
raising. `quit`: `-d '{"action":"quit"}'`.

What to watch:
- `recordings/nqmt-*/session.log`: count_in t0, finished, result.
- `bridge.log`: `loaded <song>`, `count_in ... at t0`, playing per bar.
- `mic1.log`: transcriber lines (`time 0 onset`). At exit its JSON summary (`live`:
  `laptop_minus_device_s`, jitter, gaps; `quest_mute_dropped`).
- logcat: `[game]`/BackingPlayer/MicStreamer warnings, `discarded_samples`.
- Hub recording `recordings/<time>.jsonl`. Grade counts and the last result:
  ```sh
  .venv/bin/python -c "import json,glob,collections as c;f=sorted(glob.glob('recordings/*.jsonl'))[-1];m=[json.loads(l) for l in open(f)];print(f,c.Counter(x['data'].get('grade') for x in m if x.get('type')=='judgment.note'));print([x['data'] for x in m if x.get('type')=='session.result'][-1:])"
  ```

## 3. Checks

**(a) Does the Quest mic hear its own backing?** Nobody drums.
1. Play `bond_demo` at normal volume to the end (or quit after ~30 bars). Note `session.result`
   (`extra`, `perfect/good/off` > 0 means bleed is being judged as hits). Also count the onsets:
   `.venv/bin/python -m nqmt.mic_check count recordings/nqmt-*/mic_0*_quest.drums.txt`. Use only
   the latest folder; `--from/--to` laptop times come from session.log's t0.
2. Same again with the headset's volume all the way down (physical button on the strap; this
   mutes Fletcher too). Same counts. This is the floor (room noise, Fletcher off).
3. Optional: the same at half volume. If (1) is much more than (2), the bleed needs a level gate or
   echo handling, and the fallback is `--mic laptop` (Settings > mic LAPTOP or restart nqmt with `--mic laptop`).

**(b) Does the mute filter drop real fast hi-hat notes?** The drummer plays steady closed hi-hat
16ths, ~8 bars each, at 80, 100 and 120 BPM. 120 BPM 16ths are 125 ms apart, inside the filter's
240 ms window. Use a phone metronome, no song playing, and pause a few seconds between tempos.
nqmt is already recording mic.pcm (`--record-mic-pcm`). Afterwards:
```sh
J=$(ls -t recordings/*.jsonl | head -1)
.venv/bin/python -m nqmt.mic_check wav $J /tmp/hats.wav
.venv/bin/python -m nqmt.mic_check compare /tmp/hats.wav --bpm 120 --sub 4
```
This prints the onset counts with the filter on and off, what the filter dropped, and the IOI
median and p10/p90. With the filter on, an IOI median at 2x the grid, or many drops, means it eats
real notes. Live, the launcher's transcriber has the filter on; `quest_mute_dropped` in mic1.log's
summary is the live count. To run a second live transcriber with the filter off, side by side:
`cd live_transcription && ../.venv/bin/python tools/transcribe_wav.py --input hub:ws://127.0.0.1:8765 --clock monotonic --onsets-only --mute-window-ms 0 --output /tmp/nofilter.drums.txt`

**(c) Gaze menu.** No controllers: look at an item, the ring fills, and 1.5 s selects it. Check:
song list → PLAY works; Settings → mic LAPTOP/QUEST toggles (the launcher logs `headset settings:
mic ...` and restarts the transcriber); DEMO SCENES + BACK TO MENU; the results page after a song.
Note any item hard to reach or picked by accident.

## Stop

Ctrl-C in terminal A, then `adb shell am force-stop com.notquitemytempo.app`.
