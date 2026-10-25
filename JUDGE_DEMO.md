# Judge demo runbook

Two ways to show it. **Start every judge on Mode A** (zero risk, zero input). Offer Mode B to anyone
who wants to try it with sticks.

## Mode A: hands-off (self-playing, no laptop, no USB, no input)

App on the headset: **NQMT Judge Demo** (`com.notquitemytempo.demo`). It is a real session replayed:
Terence walks in through the door, the menu shows Caravan, he counts off "5, 6, 7, 8", the band plays,
a virtual drummer plays from the kit, the drum chart scrolls on the music stand, he cuts the rushing
drummer off with the fist, barks, counts back in, the clean run finishes, the results show, and it loops.

- Put the headset on the judge, sitting on the drum throne. Nothing to press.
- Launch: Library → Unknown Sources → **NQMT Judge Demo** (or from the Mac:
  `adb shell monkey -p com.notquitemytempo.demo -c android.intent.category.LAUNCHER 1`).
- Install once (Mac, USB): `adb install -r quest_app/build/nqmt_demo.apk`
  (rebuild: `GODOT=<Godot 4.5> quest_app/tools/build_quest.sh demo`).
- Say while they watch: "Terence is our AI bandleader. He listens through the Quest's mic, compares
  every hit against the sheet music, and when you rush or drag he stops the band and lets you have it."

## Mode B: play it yourself with just sticks (laptop + USB)

No drum kit needed. Fletcher only cares about *when* you hit, the drum comes from the score, so any
surface works: a table, a chair seat, a book, a practice pad, or clicking the sticks together.

1. Mac, Terminal 1 (laptop services, real voice):
   `.venv/bin/python -m nqmt --usb --mic quest --live` → wait for `nqmt ready`.
2. Mac, Terminal 2 (headset over USB):
   ```
   adb devices                         # must say "device"
   adb reverse tcp:8765 tcp:8765 && adb reverse tcp:8766 tcp:8766
   adb shell am broadcast -a com.oculus.vrpowermanager.prox_close   # keep it awake off-head
   adb shell monkey -p com.notquitemytempo.game -c android.intent.category.LAUNCHER 1
   scrcpy --no-audio --crop=1832:1920:0:0 --no-control --window-title "Quest view"   # mirror for the crowd
   ```
3. Browser on the Mac: **http://127.0.0.1:8766** (the console).
   - Judge sits facing their "kit" (the table), press **Recenter headset**.
   - Pick **bond_demo** (143 bpm, steady, easiest) and press **Start**. Or look at a song on the music
     stand for 1.5 s, then at PLAY.
4. Terence counts "5, 6, 7, 8". Tap a steady beat with the band. The drum chart on the stand shows
   where you are. To make him explode: **speed up** (rush) or **slow down** (drag) for a few bars.
   He stops the band, gives a fresh Claude-written line in his ElevenLabs voice, counts back in.
   Stop playing entirely and he barks "Watch my hand." and ends the song.
5. The console shows his mood/anger live, his last line, and the results.

Optional before the first judge: `python -m nqmt.calibrate --mic quest` (quiet room, 8 claps) so the
timing correction includes the Quest's audio latency.

## If something goes wrong

| symptom | fix |
|---|---|
| Mac can't see the headset (`adb devices` empty) | wake it, replug the cable (no hub), `prox_close` broadcast again |
| Game loads but no song list | `adb reverse` again (it resets when USB drops) |
| Fletcher only beeps | Terminal 1 was started without `--live` |
| Every take silent / no hits register | Quest mic stuck after a USB prompt: `adb reboot`, relaunch |
| "Controllers required" on launch | hand tracking must be on in Quest Settings → Movement tracking |
| Room is behind you | console → **Recenter headset** while facing the kit |
| Noisy hall triggers false hits | play louder/closer to the headset; Mode A is unaffected |
| Anything else | switch to Mode A, it needs nothing |
