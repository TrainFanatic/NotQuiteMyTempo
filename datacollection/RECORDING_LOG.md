# Recording log (Quest 2 single-drum takes)

Kit (live_transcription/config/kit.local.json): kick, snare, hihat (closed), high_tom, mid_tom,
floor_tom, crash, ride. Recorder: Kevin's quest_recorder (Godot, OpenSL "MIC" source with the
Quest's echo canceller + noise suppressor). Takes pulled with `datacollection/quest_takes.py`,
labelled with `label.py single`. Audio and labels live in live_transcription/data/recordings/
(git-ignored).

| round | sessions | hits | notes |
|---|---|---|---|
| smoke | quest_smoke_01, quest_test_02 | - | capture checks, not labelled |
| 1 | single_<piece>_01 | 272 (44/35/52/30/37/38/14/22) | clean. Labels checked against the waveform. |
| 2 | single_<piece>_02 | 173 (21/25/24/33/16/25/12/17) | lots of talking in the room; labelled with `--floor-db -20`, so sounds more than 20 dB under the take's loud hits (voices, ghost notes) are left out |
| 3 | single_<piece>_03 | 158 (22/30/27/18/17/18/9/17) | quiet, clean; labelled with the precursor rule (below) |
| - | discard_r2_false_start | - | 8 s false start, ignore |

Splits in kit.local.json (`quest_takes.py splits`): round 1 = train, 2 = validation, 3 = test.

First classifier run (Kevin's `train_model.py`, 2026-10-24, `reports/quest2_v1.json`), held-out
round 3, 10 ms match: hi-hat P 0.84 / R 0.78, ride 0.38 / 0.88, tom 0.33 / 0.70, kick
0.19 / 0.23, snare 0.11 / 0.23, crash 0.11 / 0.56. Not usable yet: few hits per family, single-
drum takes only, and the training candidates still include the Quest's phantom re-attacks.
The demo uses drum identity from the score instead (classifier shelved).

What we learned on the device (2026-10-24):
- The Quest mutes the mic ~0.1 s after a loud attack and un-mutes ~0.05 s later; the ring then
  looks like a second hit ~0.18 s after the real one. label.py and the live filter
  (live_transcription QuestMuteFilter) drop these. Drummer confirmed all strokes were single.
- After the laptop died and USB debugging was re-accepted, the Quest's audio input got stuck and
  handed the recorder pure zeros (12 silent takes, discarded) until the headset was rebooted.
  The recorder doesn't notice: check the peak after every take (quest_takes.py flags SILENT).
- Opening menus / Quick Settings / system prompts silences the recorder's mic while they are open.
- The headset clock is wrong (2025-06-06) and resets on reboot; take IDs' timestamps are not
  real time, only ordering within a boot.
- Talking during takes gets labelled as hits; record in silence.
- A quiet sound 0.3-0.4 s before a hit (~40 dB under it) is the stroke getting ready (beater,
  stick, pedal), not a stroke; label.py drops it ("precursor") and keeps quiet sounds spaced like
  strokes (real soft hits).
