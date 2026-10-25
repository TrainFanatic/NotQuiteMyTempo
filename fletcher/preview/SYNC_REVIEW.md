# Sync review: Fletcher's voice vs Terence's moves (Godot Movie Maker renders)

## Round 3: merged quest_app, Terence v004, Adam's real SFX (2026-10-24 night)

These renders use the repo's `quest_app` and its real Fletcher wiring: `fletcher_setup.gd`, the Voice bus,
`res://fletcher_barks` and `res://fletcher_countoff`. They were made in a scratch copy because of the
frame-clock patch; nothing in the repo is patched.
- **Settings**: intro off, cinematics off, Adam's recorded SFX on, 94 BPM, 1280x720, 30 fps.
- **Streams**: the cutoff stream is again recorded from `hub.e2e` (dry run) with the cached real line
  `lines/20261024-171410_c3_rushing_L3.wav`.
- **Stress scene**: `sfx_stress_*` is new. A 23.7 s real tirade plays while Terence throws a chair, a
  cymbal and a drum, slams a fist, kicks the stand and does a big clap.
- **Stems**: every scene was rendered three times (mix, voice-only, SFX-only). The SFX-only render keeps the
  Voice bus running into a muted sink, so the ducker still hears it.
- **Window**: renders use `--always-on-top`. With the Godot window covered, Movie Maker wrote stale frames:
  the HUD frame number lagged the video frame. Audio and the event log are per-frame either way, so the
  measurements were not affected.

| video | what |
|---|---|
| `bark_gallery_{drummer,side}.mp4` | 9 barks during play (45 s) |
| `cutoff_sequence_{drummer,side}.mp4` | the full cutoff, with ducking and the 0.5 s cutoff lead (37 s) |
| `sfx_stress_{drummer,side}.mp4` | the SFX stress scene, with ducking (26 s) |
| `before/v4_*` | the same cutoff and stress scenes with no ducking and a 0.3 s cutoff lead |

### Sync (key frame minus the word; v004 key frames)
| moment | before (lead cap 0.3) | after |
|---|---|---|
| cutoff "Stop!" fist (frame 14) | +82 ms (clip held 300 ms) | **-14 ms** (held 424 ms) |
| gallery barks | -10 to +21 ms; no_1 flick +59 (wants 368 ms, cap 300); Stop during play +111 | unchanged (play barks keep the 0.3 s cap by design) |
| count-off fingers vs words | +17 to +22 ms | -1 to -9 ms |
| restart command vs t0 - 4 beats | 0 frames | 0 frames |
| tirade opener (`t4_shout`) | starts at the fist's hold release (frame 50 = 2.66 s at 94 BPM), +1.1 s after its sentence | same, as cues.py intends |

- **Cutoff lead.** `cutoff_lead_max_s = 0.5` in `config.py` and as a FletcherVoice export. It applies only
  to the priority-2 fist on the cutoff "Stop!".
  - At 94 BPM "Stop!" is now heard **424 ms after the stop command instead of 300 ms (+124 ms)**.
  - At 120 BPM it is unchanged (255 ms). The fist lands on the word down to about 84 BPM.

### Voice vs SFX
- **How it was measured**: from the stems, in the speech band (300 to 3400 Hz) over 20 ms frames. A voiced
  frame counts as masked when the SFX are within 3 dB of the voice.

| scene | masked speech, before | after |
|---|---|---|
| cutoff sequence | 3.7 % (fist stomp on "Stop!" -5 dB; throw_music -13; sweep_stand -25) | **0.3 %** |
| bark gallery | 3.2 % (fist_slam on "PUSH" -10 dB) | **0.6 %** |
| SFX stress tirade | 20.7 % (chair_impact -28 dB, cymbal -27, drum -30, stand_crash -25, clap_big -15) | **10.8 %** |

- **Fix: ducking in FletcherVoice, not a bus effect.**
  - A sidechain compressor on the SFX bus measured well (6.6 %), but it breaks Adam's
    `test_sfx_bus_layout` ("SFX is dry"), so it was reverted.
  - Instead FletcherVoice turns the `SFX` bus down from the Voice bus peak meter: threshold -42 dB, 5:1, at
    most 18 dB, attack 10 ms, release 450 ms. These are `duck_*` exports; `duck_max_db = 0` turns it off.
  - Under speech, impacts drop 6 to 19 dB (chair -11, cymbal -8, stand_crash -14, drum -17, clap_big -12,
    fist_slam -18).
  - With no speech the mix is unchanged: in a render with SFX and no voice, total RMS was within 0.15 dB,
    which is the pitch jitter.
- **Remaining masks.**
  - The leftover masked frames are at word onsets: the meter is read once per frame (33 ms in these renders,
    14 ms on the Quest).
  - Quiet words below the threshold are not protected either: chair_grab under a whispered "[quietly] Oh...".
- **Doubling.** No SFX doubles the voice: there are no vocal or breath sounds, and no bark word names an SFX.
  - Some accents are synced to words on purpose: the fist cutoff's `foot_stomp` sits on "Stop!", and
    fist_slam's on "PUSH".
  - Those used to mask their word. They are now ducked under it, so they are much quieter while he speaks.

### Changed files (this round)
- `fletcher/config.py`: `cutoff_lead_max_s`.
- `fletcher/main.py`: the cutoff bark uses `cutoff_lead_max_s`.
- `fletcher/godot/fletcher_voice.gd`: `cutoff_lead_max_s` and ducking. Synced to `quest_app/scripts/fletcher/`.
- `quest_app/README.md`: the Audio note.
- Tests:
  - `fletcher/tests/test_cues.py`: the cutoff gets the longer lead.
  - `fletcher/godot/tests/run_tests.gd`: the cutoff cap, and ducking with recovery.

## Round 2 (Terence v003, before the merge): history

Rendered 2026-10-24 from a copy of Adam's `quest_app` (checkout `fbc1cfa`, sfx snapshot of 18:02) with
`fletcher/godot/*.gd`, at 1280x720 and 30 fps, 94 BPM. Cinematics were off, sfx were on, and the band has no
music track.

- The current videos (after the fixes below) are in this folder. The first round is in `before/`.
- Contact sheets are in `frames/` (`before_*` and `after_*`). Scripts to re-render are in `tools/`.

| video | what | length |
|---|---|---|
| `bark_gallery_drummer.mp4` / `_side.mp4` | 9 barks during play, one every 4.6 s | 45 s |
| `cutoff_sequence_drummer.mp4` / `_side.mp4` | conduct, "Rushing", "Stop!" + fist, 20.6 s tirade, stare, count-off with words, band back in | 37 s |

- **drummer**: Adam's desktop `PlayerRig` camera at the drummer's seat. Terence is small and about 6 m away.
- **side**: a fixed camera that turns to follow Terence (fov 30). Its audio is quieter because the listener is
  the camera.
- **HUD**: movie time, frame, Terence's current move, the voice line or count-off word, and the last sfx.

## How it was made
- **Cutoff stream**: recorded, not hand-written. `hub.e2e` (bridge, mock drift, Fletcher dry run, no API
  calls) ran with every envelope logged. The draft and voice were swapped for a cached real line
  (`lines/20261024-171410_c3_rushing_L3.wav`), streamed per sentence with TURN markers, so `main.py`
  made every cue.
- **Gallery**: made by `Fletcher._send_clip` (the real `BarkCues` path) with `phase=playing` and `bpm=94`.
- **Clock**: in the copy, the scripts read `Time.get_ticks_usec()/1e6` as frames/fps (Movie Maker runs on
  simulated time). The harness sets `hub.clock_offset` so laptop `t0` maps to movie time. Nothing else was
  changed.
- **Count-off audio**: in the "after" videos FletcherVoice plays it itself. In "before" the harness played
  it.
- **How offsets were measured**: each source clip is cross-correlated against the movie's audio track.
  Key frame = `move_started` + frame/24 x 120/94.
  - Movie Maker puts every sound about 40 ms (one mix block) after the frame that started it. This shows as
    "audio vs event +37 to 46 ms" and as "word vs beat +40 ms". It is a renderer constant, not sync drift.
  - Holds and starts are quantized to 33 ms frames at 30 fps (14 ms on the Quest at 72 fps).

## Before / after (key frame minus the word it should hit; + = the move is late)

| moment | before | after | what changed |
|---|---|---|---|
| "Stop!" fist close (cutoff) | +194 ms | **-7 ms** | fist `at_s -0.204` (unclamped); the clip is held 204 ms |
| "Stop!" fist (gallery) | +197 | **-5** | same |
| rushing_1/2 point | +210 | **+9 / -4** | held 214 ms (rushing_2 needs none) |
| rushing_3 point | +127 (waited behind the flick) | **-7** | single cue |
| dragging_2 impatient watch | +277 | **+12** | held 282 ms |
| bar_09 score point | +223 | **-10** | held 236 ms |
| no_1 flick | +305 | **+6** | held 300 ms (cap; it wanted 315) |
| again_1 point | +82 | **+20** | held 93 ms |
| dragging_3 slam / praise_1 nod | +9 / +14 | +9 / +14 | already aligned |
| watch_1 "Watch my hand." | nothing (already conducting) | **+21** | `t0_cue_section` on HAND |
| after a mid-song bark | idle stare until the next cue | **conducts again within 1 frame** | conduct fallback |
| tirade opener (`t4_shout`) | +292 ms start | +91 ms start | waits for the p2 fist |
| tirade sentence 3 | in_face: a 4.3 s walk to the drummer, never played | `t0_clap_off` at home, start -141 | stationary moves |
| tirade sentence 4 | kick_stand: never played | `t4_sweep_stand` at home, start -135 | stationary moves |
| count-off fingers vs words | **+3797 ms** each (walked home first) | **+15 / +21 / +15 / +15** | one clock (t0) |
| count-off words vs beats | (played by the harness) | +39 to +46 (the 40 ms Movie Maker offset) | FletcherVoice plays the clips on t0 |
| restart command vs "five" | n/a | -10 ms (31.433 vs t0 - 4 beats = 31.443) | restart sent at t0 - 4 beats |
| band back in vs t0 | before the fingers | at t0 (34.00 vs 33.96, one frame), then conduct | — |

- The line's `at_index` cues place the move's *start* at the sentence's first chunk. The chunk can begin
  in the pause before the word, hence -140 ms. The key events (claps, the stand hit) come about 0.5 s
  into the sentence.

## Fixes (in fletcher/ only)
1. **No walking by default.**
   - `cues.gesture_move` resolves tirade gesture names to Terence move ids. Without `closeups`, close-ups
     map to moves he plays at home (`cues.STATIONARY`: t3_loom -> point, t3_clap_in_face /
     t5_rhythm_slaps / t5_cowbell -> t0_clap_off, t3_whisper / t3_fake_warmth -> t2_forced_calm,
     t3_crouch -> t0_lean_listen, t3_circle_kit -> t2_hunt_sections).
   - In Godot, `GESTURE_MOVES` and `ACTION_MOVES.lean_in` are stationary too.
   - With `closeups` on, Fletcher sends a `t3_loom` walk-in at the Stop bark, and the count-off waits an
     extra `closeup_return_s` (4 s at 120 BPM, scaled).
   - The link never lets the finger trigger a walk: away from home it waits until he is back.
   - A move repeated back to back within a line is sent once. A second kick_stand made the thrown stand
     pop back up.
2. **Lead.**
   - `at_s` is sent unclamped (emphasis minus key-frame time at the song tempo).
   - FletcherVoice starts moves with negative `at_s` at once and holds the clip by `-min(at_s)`, capped at
     `bark_lead_max_s` = 0.3 s (config plus a matching export). It counts as speaking during the hold.
   - Fletcher adds the hold to the bark's busy time and to the cutoff's `bark_end`.
   - **Added latency**: Stop bark first sound +89 ms at 120 BPM, +204 ms at 94, +300 ms below about
     78 BPM, 0 ms from 153 BPM. Other barks hold 0 to 300 ms. The fist still goes out with the stop command.
3. **Conduct fallback.** While the band plays, a finished one-shot falls back to `t0_conduct`.
4. **cues.json.**
   - `watch_1` -> `t0_cue_section` (cue frame on HAND, 0.54 s).
   - `rushing_3` has a single cue (the point).
   - `BarkCues` drops a later cue that would start while an earlier one plays.
   - File `at_s` values are now the unclamped 120 BPM values.
5. **Count-off on one clock.**
   - Python computes `t0 = estimated line end + post_stare_s + 5 beats`, never before the line ends.
   - It sends the finger cue with `t0`, `bpm` and `clips` (from `countoff.schedule`, `countoff_style`),
     and sends `session.command restart` at `t0 - count_in_beats` beats. `restart_gap_s` is gone.
   - Godot maps t0 with `hub.to_local_time`, starts the finger at t0 - 5 beats, and plays the words with
     each manifest onset on beats -4..-1 (`res://fletcher_countoff`, like the barks).
   - Fletcher does not send `session.state count_in`; the bridge owns it. The bridge's `count_in` still has
     no `t0`, but its bar start (command + 4 beats) now equals Fletcher's t0. Adding `t0` there would be
     a one-line `hub/bridge.py` change.

## Still worth a look
- **Fist delays the tirade opener.** The opener (`t4_shout`) waits for the p2 fist, which is 1.6 s long at
  94 BPM, so it starts 91 ms after its sentence.
- **Post-line stare usually drops.** The `after_s 0` stare is dropped when the last tirade move is still
  playing. Harmless: he idles in a stare and then the finger plays on time.
- **Stand stays on the floor.** A knocked stand lies there through the count-off (Adam's 8 s prop
  lifetime).
- **Claps off the beat.** `t0_clap_off` (the stationary stand-in for clap-in-face / rhythm slaps) has
  0 dB clap sfx. That is fine in a tirade, and barks during play never use it.
- **No vocal sfx in Adam's snapshot,** so nothing doubles the voice. Re-check when his breathing sfx land.
