# Overnight work order — Not Quite My Tempo

Context: a mixed-reality VR drum-practice app inspired by the film Whiplash, for a Meta Quest 2 in Godot 4. The player sits at a real drumset seen through passthrough; a virtual big band and the bandleader, Terence, surround them. Art style: PS2-era low-poly with small textures. The room (room/) and Terence's model already exist. Blender: /Applications/Blender.app/Contents/MacOS/Blender --background --python <script>

## Rules (read every session)
- You run unattended until morning. Nobody is here: never ask questions and never wait for input.
- When something is undecided, choose the simplest reasonable option, make it a variable that's easy to change, log it in DECISIONS.md, and continue.
- Never stop because something is hard. If a task fails 3 times, log it in PROBLEMS.md, mark it blocked in PROGRESS.md, and move to the next task.
- Work only inside this folder. Never delete anything: put replaced files in archive/. Don't overwrite the room or Terence files; save new versions (_v002, _v003).
- After every finished task: check it off in PROGRESS.md, update MORNING_REPORT.md (done, decisions, problems, files to review), and git commit with a clear message.
- Save usage: put shared code in a library script (lib_ps2.py: textures, boxes, rigs, export, contact sheets) and reuse it. At most one 1024x512 contact-sheet render per pass and at most two fix passes per asset. Don't re-read large files you don't need.
- Build only what's listed. No drum kit (the real kit is used). Don't touch drum input, voice, AI, or scoring; teammates own those.
- When every task is done, move to the Polish loop at the end. Never idle.

## PS2 style
- Starting triangle targets: band member 2,000; instrument 500; prop 300.
- Textures 128–512 px, base color only, crisp pixelated filtering. Principled BSDF with image textures only.
- .glb, meters, transforms applied, characters face -Y on the floor at the origin, lowercase snake_case names.
- Rigs map to Godot's SkeletonProfileHumanoid, T-pose, finger bones, at most 4 bone influences per vertex. Hair is mesh or texture, never particles.

## Phase A — Remaining assets
A1. Band: 3 rigged base bodies, varied (heads, hair, clothing colors) into 17 members: band_alto_sax_1-2, band_tenor_sax_1-2, band_baritone_sax_1, band_trumpet_1-4, band_trombone_1-4, band_piano_1, band_guitar_1, band_bass_1. Seated poses come from animation, not modeling.
A2. Instruments, one .glb each: 2 alto saxes, 2 tenor saxes, 1 baritone sax, 4 trumpets, 4 trombones (slide as a separate object moving along one axis), guitar, upright bass, and a music stand with folder. Handheld instruments get grip_l and grip_r empties.
A3. Props: chair, cowbell with stick, spare drum, cymbal, loose sheet music pages, a music stand that can be knocked over.
A4. review_scene.blend: room + Terence at spot_terence + every band member at their seat_ marker with instrument + props. One contact sheet.

## Phase B — Animations (keyframed by script, simple PS2-style motion, exported inside each character's .glb)
B1. Band, per instrument type: rest (instrument down), raise, play_loop (2–4 s at 120 BPM so Godot can scale the speed; trombone slide moves), lower. Reactions: freeze, startle, look_down, look_at_drummer, flip_page, arms_rest, and spit_valve for trombones.
B2. Terence: one action per move ID below, named exactly by ID. Order: tier 0 and 1 first, then 2, 3, 4, 5, approval. Aim the slap and chair throw at a default player head at spot_drummer, 1.15 m high (Godot re-aims them live). If he has facial shape keys, use them; if not, skip faces and log it.
  t0_countoff_finger, t0_countoff_verbal, t0_clap_off, t0_conduct, t0_pace, t0_lean_listen, t0_cue_section, t0_watch_other, t0_stare_drummer, t0_check_watch, t0_chair_rest,
  t1_flick, t1_wave_off, t1_fist_cutoff, t1_clap_stop, t1_fist_slam,
  t2_judging_stare, t2_score_point, t2_point_player, t2_hunt_sections, t2_forced_calm, t2_switch_drummer, t2_impatient_watch,
  t3_loom, t3_whisper, t3_crouch, t3_circle_kit, t3_clap_in_face, t3_fake_warmth,
  t4_shout, t4_charge, t4_sweep_stand, t4_throw_music, t4_door_point, t4_storm_out,
  t5_chair_throw, t5_rhythm_slaps, t5_cowbell, t5_drum_throw, t5_cymbal_throw,
  ap_nod, ap_grin, ap_quiet_wave, ap_beckon, ap_awe
B3. ANIMATIONS.md: a table of every action with length, whether it loops, and key timings (slap impact frame, chair release frame).
B4. Verify: re-import each .glb in a fresh Blender session, check its action count, and render one contact sheet of 6 key poses for Terence and 3 for one band member.

## Phase C — Godot (only if Godot 4 is installed: check /Applications/Godot*.app; otherwise skip and log)
In a godot/ folder, build only what needs no headset, each with headless tests that pass:
C1. Import validator for the .glb files (scale, humanoid bones, triangle limits, known animation names).
C2. Standard MIDI file parser (types 0/1, tempo changes, running status, note-on velocity 0 as note-off, track names) and phrase extraction (play spans per track, raise lead time, lower after rests), with fixture tests.
C3. Terence AnimationTree state machine with one state per move ID, plus a band state per player (resting, raising, playing, lowering) and the reaction mapping.
C4. A scene-director API (play move, set song position, set band state) with a fake message injector and tests.

## Polish loop (after everything above, repeat until stopped)
Improve the weakest work in this priority order: Terence's look, variety and quality of Terence's moves, room detail, band phrase motion, band members. One improvement per commit; log each in MORNING_REPORT.md.
