# Progress — Not Quite My Tempo overnight run

Tasks come from OVERNIGHT.md. Check each one off when it's finished, and mark a task `BLOCKED` (with a pointer to PROBLEMS.md) after 3 failed attempts.

## Already done
- [x] Room: `build_room.py` -> `room/room.glb`, `room/room_v003.blend`, `room/room_v003_sheet.png` (film layout, 10 x 7.6 x 3.0 m, seat_/spot_ markers)
- [x] Terence model: `prep_terence_textures.py` + `build_terence.py` -> `terence/terence.glb`, `terence/terence_v001.blend`, `terence/terence_v001_sheet.png` (1,840 tris, 22-bone humanoid rig, no finger bones, no face shape keys)

## Phase A — Remaining assets
- [x] A1. Band: 3 rigged base bodies varied into 17 members (band_alto_sax_1-2, band_tenor_sax_1-2, band_baritone_sax_1, band_trumpet_1-4, band_trombone_1-4, band_piano_1, band_guitar_1, band_bass_1). Done: `build_band.py` -> `band/*.glb` (16 members; the ranges add up to 16, see DECISIONS.md)
- [x] A2. Instruments: 2 alto saxes, 2 tenor saxes, 1 baritone sax, 4 trumpets, 4 trombones (separate slide), guitar, upright bass, music stand with folder; grip_l/grip_r empties on handheld ones. Done: `build_instruments.py` -> `instruments/*.glb` (16 files, 98 to 336 tris)
- [x] A3. Props: chair, cowbell with stick, spare drum, cymbal, loose sheet music pages, knock-over music stand. Done: `build_props.py` -> `props/*.glb` (6 files, 74 to 236 tris)
- [x] A4. review_scene.blend with room, Terence, every band member + instrument at their seat, props; one contact sheet. Done: `build_review_scene.py` -> `review_scene.blend`, `review_scene_sheet.png`

## Phase B — Animations
- [x] B1. Band per instrument type: rest, raise, play_loop, lower; reactions freeze, startle, look_down, look_at_drummer, flip_page, arms_rest, spit_valve (trombones). Done: `band_anims.py` via `build_band.py`; actions are inside each `band/*.glb` (10 per member, 11 for trombones)
- [x] B2. Terence move actions. Done: `build_terence_v002.py` -> `terence/terence_v002.glb` (45 actions, finger rig, PropR/PropL bones), `terence_v002_moves.json`
  - [x] Tier 0: t0_countoff_finger, t0_countoff_verbal, t0_clap_off, t0_conduct, t0_pace, t0_lean_listen, t0_cue_section, t0_watch_other, t0_stare_drummer, t0_check_watch, t0_chair_rest
  - [x] Tier 1: t1_flick, t1_wave_off, t1_fist_cutoff, t1_clap_stop, t1_fist_slam
  - [x] Tier 2: t2_judging_stare, t2_score_point, t2_point_player, t2_hunt_sections, t2_forced_calm, t2_switch_drummer, t2_impatient_watch
  - [x] Tier 3: t3_loom, t3_whisper, t3_crouch, t3_circle_kit, t3_clap_in_face, t3_fake_warmth
  - [x] Tier 4: t4_shout, t4_charge, t4_sweep_stand, t4_throw_music, t4_door_point, t4_storm_out
  - [x] Tier 5: t5_chair_throw, t5_rhythm_slaps, t5_cowbell, t5_drum_throw, t5_cymbal_throw
  - [x] Approval: ap_nod, ap_grin, ap_quiet_wave, ap_beckon, ap_awe
- [x] B3. ANIMATIONS.md table (length, loops, key timings). Done: `gen_animations_md.py` -> `ANIMATIONS.md` (45 Terence + 11 band actions)
- [x] B4. Verify: re-import every .glb in a fresh Blender session, check action counts, one contact sheet (6 Terence poses, 3 band poses). Done: `verify_assets.py` -> `VERIFY.md` (41/41 pass), `verify_sheet.png`

## Phase C — Godot (skip and log if Godot 4 isn't in /Applications)
- [x] C1. Done in `~/not-quite-my-tempo` (Godot 4.5.1 from ~/Downloads). Import validator: `scripts/import_validator.gd`; headless tests pass (18/18). Import validator for the .glb files, with headless tests
- [x] C2. Done in `~/not-quite-my-tempo` (Godot 4.5.1 from ~/Downloads). MIDI parser + phrases: `scripts/midi/`, fixture tests; headless tests pass (18/18). Standard MIDI file parser + phrase extraction, with fixture tests
- [x] C3. Done in `~/not-quite-my-tempo` (Godot 4.5.1 from ~/Downloads). State machines: `scripts/band_member.gd`, `scripts/terence.gd`; headless tests pass (18/18). Terence AnimationTree state machine + band states + reaction mapping
- [x] C4. Done in `~/not-quite-my-tempo` (Godot 4.5.1 from ~/Downloads). Director + fake injector: `scripts/scene_director.gd`, `scripts/fake_injector.gd`; headless tests pass (18/18). Scene-director API with fake message injector and tests

## Polish loop (repeat until stopped)
- [ ] Polish passes, logged in MORNING_REPORT.md (Terence's look, his moves, room detail, band phrase motion, band members)
