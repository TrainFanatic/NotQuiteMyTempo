# fletcher/godot: Fletcher's voice and body cues in Adam's Godot app

Three scripts. Copy them into `quest_app/` (e.g. `scripts/fletcher/`); nothing else from this folder.

| file | role |
|---|---|
| `fletcher_hub.gd` | `FletcherHub`: WebSocket client for `hub/server.py`. Reconnects every 1 s, emits `message_received(type, data, envelope)`, `send()`, and `to_local_time(t)` (laptop clock -> this device) |
| `fletcher_voice.gd` | `FletcherVoice` (Node3D, put it on Terence's head): barks by `clip_id`, streamed lines from `fletcher.audio` (16-bit mono PCM, 24 kHz, 0.15 s prebuffer), `subtitle`, `speaking`, `finished` (lines and barks), `level` for the jaw. Holds each gesture cue until its anchor (`at_index` chunk plays, `at_s` after the first sound, `after_s` after the end), then emits `cue_due` (and the older `gesture_cue`) |
| `fletcher_link.gd` | `FletcherLink`: hub -> voice, and Fletcher's actions, gesture cues and `session.state` -> `SceneDirector.handle_message()` through a one-move-at-a-time queue (`ACTION_MOVES`, `GESTURE_MOVES` tables: tune freely) |

## Wiring (Adam)

```gdscript
var hub := FletcherHub.new(); hub.url = "ws://<laptop LAN IP>:8765"; add_child(hub)
var voice := FletcherVoice.new(); terence_head.add_child(voice)   # a BoneAttachment3D on the head
var link := FletcherLink.new(); link.hub = hub; link.voice = voice; link.director = director; add_child(link)
```

- Copy `fletcher/barks/*.wav` to `res://fletcher_barks/` (file name = `clip_id`), and
  `fletcher/countoff/*.wav` + `manifest.json` to `res://fletcher_countoff/` (FletcherVoice `countoff_dir`).
- Android export: add the INTERNET permission (and RECORD_AUDIO if the mic moves into the app).
- What the link does: plays a move only when Fletcher's `action` changes (he repeats it as anger moves);
  `lean_in` is a stare (no walking); on `fist_cutoff` it freezes the band on the fist's `cutoff` frame
  (read from the moves json: 14 in v004, tempo-scaled), not on arrival, keeps it frozen through the
  fist's hold and lowers it at `release` (frame 50). A `session.command stop` / `session.state stopped`
  that arrives while that fist is coming (playing, queued, or waiting for the Stop bark) leaves the band
  to the fist instead of lowering it at once (after 1 s with no fist it lowers anyway).
- Holds (terence_v004): moves with `hold_start`/`release` (t1_fist_cutoff, t1_fist_slam, t1_flick,
  t1_wave_off, t1_clap_stop) give way to queued cues at their `release` frame, and a cue that arrives
  before the release counts as due then, so it is not dropped as stale behind a long hold. That is
  where the tirade's first gesture plays: the line starts during the fist's held glare (0.8..2.1 s at
  120 BPM) and its opener follows the release.
- No walking by default: `GESTURE_MOVES` maps every tirade gesture to a move he plays where he stands
  (`in_face` -> point, `beat_time`/`slow_clap` -> `t0_clap_off`). `closeups = true` restores the walking
  t3_*/t5_rhythm_slaps versions. Raw move ids from Fletcher play as sent (Fletcher sends close-ups only
  with its own `closeups` on). The count-off finger never makes him walk: away from home it waits.
- Lead: a bark cue with a negative `at_s` starts its move at once and FletcherVoice holds the clip back
  by `-min(at_s)`, capped at `bark_lead_max_s` (0.3 s, same as Fletcher's), so key frames land on words. The cutoff fist (`t1_fist_cutoff` at priority 2) may hold "Stop!" up to `cutoff_lead_max_s` (0.5 s, = Fletcher's).
- Ducking: while he speaks FletcherVoice turns the `duck_bus` (quest_app's `SFX`) down from the peak meter of
  its own players' bus (`Voice`): threshold -42 dB, 5:1, at most 18 dB, attack 10 ms, release 450 ms. Inert
  without both buses; `duck_max_db = 0` turns it off.
- Count-off on one clock: Fletcher's `t0_countoff_finger` cue carries `t0` (laptop clock, the restart
  bar's downbeat), `bpm` and `clips`. The link maps t0 with `hub.to_local_time`, starts the finger 5 beats
  before it and calls `voice.play_countoff()`, which places each word's onset (manifest `onset_s`) on
  beats -4..-1. Fletcher sends `session.command restart` at t0 - 4 beats. A `session.state count_in`
  (with or without `t0`) adds no second finger.
- While the band plays, a finished one-shot falls back to `t0_conduct`.
- Move queue: a due cue starts now if Terence is idle, in a looping move, or in a lower-priority
  one-shot (priority 0 ambient, 1 normal, 2 must play); otherwise it waits for the one-shot's
  `move_finished` and is dropped if by then it is more than `max_late_s` (1.5 s) past its anchor,
  unless priority 2. A cue for the move already playing or queued counts as done. Gestures resolve
  `GESTURE_MOVES`, else a raw Terence move id, else `ACTION_MOVES[action]`. With his Terence
  (`director.terence`) start/finish are his signals, so a close-up move counts from arrival, not from
  the request; without him one-shot lengths come from `moves_json`
  (`res://assets/terence/terence_v004_moves.json`), scaled by bpm/120. No key frame is hard-coded:
  fist `cutoff`/`release` and the count-off lead (4 beats + the first `finger_up`) come from that json. When a line ends with nothing
  left, he settles into the action's loop (glare -> judging stare), or conducts while the band plays. Timed events run on the wall clock, not SceneTree
  timers (those fire early after a long frame).
- Gesture moves with no exact animation (fallbacks to tune): `grab_cymbal` -> `t5_cymbal_throw`,
  `turn_away` -> `t1_wave_off`, `spin_back` -> `t2_point_player`, `throw_hands_up` -> `t4_shout`,
  `slow_clap` -> `t0_clap_off` (`t3_clap_in_face` with close-ups).

## Tests (Godot 4.5, headless)

```
Godot --headless --path fletcher/godot --script tests/run_tests.gd        # 43 checks, exits 1 on failure
fletcher/godot/tests/run_integration.sh [sweep]   # copies the repo's quest_app to a scratch dir, drives his real Terence
python -m hub.e2e --port 8798 &  Godot --headless --path fletcher/godot --script tests/live_client.gd -- ws://localhost:8798 55
```

The live client connects to the real hub during the full chain (bridge -> Fletcher dry-run) and prints
the barks, streamed line, gesture cues and director moves it saw. Not yet run on a Quest.
`tests/fixtures/terence_v004_moves.json` is a copy of Adam's moves file for the unit tests; refresh it
when his changes. The integration test (`tests/integration_terence.gd`) asserts on Terence's own
`move_started` timestamps; `sweep` also plays every move once and compares its length with the json.
