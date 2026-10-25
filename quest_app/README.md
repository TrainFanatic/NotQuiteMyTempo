# Not Quite My Tempo (Godot 4.5)

Mixed-reality drum practice for Meta Quest 2: you play a real kit (seen through passthrough) while a virtual big band and their bandleader, Terence, rehearse around you. The art assets come from `~/not-quite-my-tempo-assets` (Blender scripts, PS2 style).

## Run it

- **Desktop preview:** open the project and press Play, or run `Godot --path .` from this folder. You see the room from the drummer's seat, and `scenes/demo_timeline.json` plays a short rehearsal: count-off, band in, conducting, fist cutoff, the band looks at you, a point and a shout.
- **Quest:** see "XR / Quest" below.
- **Tests (headless):**
  ```
  Godot --headless --path . --script tests/run_tests.gd
  ```
  There are 23 tests. They cover the import validator, the MIDI parser and phrases, the band and Terence animation switching (crossfades, auto-return to idle), instrument and prop placement, Terence's walking route around the band, thrown props and the hidden room stand, scene assembly, and the director API via the fake injector. The runner exits 1 on any failure. Lines starting with `ERROR:` in the output come from tests that check error handling on purpose.
- **New assets:** after rebuilding anything in the asset repo, run `tools/sync_assets.sh`, then open the editor or run `Godot --headless --path . --import`.

## Layout

| Path | What |
|---|---|
| `assets/` | copied glbs: `room/room_v005.glb`, `band/` (16), `instruments/` (16), `props/` (7), `terence/terence_v003.glb` + move metadata |
| `tools/post_import_character.gd` | post-import for band and Terence: restores the `play_loop` name (Godot strips `_loop`) and sets loop modes from the move metadata |
| `scenes/rehearsal.tscn` + `scripts/rehearsal.gd` | main scene, built at runtime from the room markers |
| `scripts/band_member.gd` | one player: character, instrument on the `Instrument` bone (trombone slide on `InstrumentSlide`), crossfaded states and reactions |
| `scripts/terence.gd` | Terence: one animation per move ID (hub `t0_stare_drummer`), crossfaded; props on `PropR`/`PropL` (the `PROPS` table at the top); walking to close-up moves |
| `scripts/anim_blender.gd` | crossfades an AnimationPlayer driven by hand (used by both characters); signals `started(anim)` and `advanced(anim, from_pos, to_pos, wrapped)` in authored time |
| `scripts/tempo.gd` | tempo rules shared by both characters: the policies, the 40 to 400 BPM range, crossfade scaling and the beat-lock correction |
| `scripts/walk_planner.gd` | floor routes around the seats and furniture of the room glb (A* over a visibility graph) |
| `scripts/scene_director.gd` | the API the rest of the game calls (below) |
| `scripts/midi/` | Standard MIDI File parser and phrase extraction |
| `scripts/fake_injector.gd` | plays timed director messages (the demo, and tests) |
| `scripts/import_validator.gd` | checks glbs: humanoid bones, triangle limits, animation names, nearest filtering, heights |
| `scripts/player_rig.gd` | OpenXR + passthrough on Quest; desktop camera elsewhere |

## Director API (for drum input, voice, AI, scoring)

Call methods on `SceneDirector`, or send dictionaries to `handle_message()`. Either way, a bad call reports `push_error` and returns `false`; nothing is guessed.

```gdscript
director.play_move(&"t1_fist_cutoff")          # any of the 45 move IDs (see the asset repo's ANIMATIONS.md)
director.stop_move()                           # back to the idle stare
director.set_band_state("trumpet", &"playing") # resting / raising / playing / lowering
director.react("all", &"look_at_drummer")      # freeze (only while playing), startle, look_down,
                                               # look_at_drummer, flip_page, arms_rest, spit_valve (trombones)
director.set_tempo(300.0)                      # everything is authored at 120 BPM and scaled; 40..400 BPM, else an error
director.load_song("res://songs/caravan.mid", {"Trumpets": ["trumpet"], "Saxes": ["saxes"]})
director.set_song_position(seconds)            # once a song is loaded, players raise and lower with their phrases
```

Targets can be `"all"`, a section (`alto_sax`, `tenor_sax`, `baritone_sax`, `trumpet`, `trombone`, `piano`, `guitar`, `bass`), `"saxes"`, or a member name (`band_trumpet_2`).

## Conventions that matter

- **Facing.** Characters face +Z, and room markers look down -Z, so a character goes at its marker rotated 180° (`Rehearsal.character_at`).
- **Attachments.** Instruments and props go under a `BoneAttachment3D` on `Instrument`/`InstrumentSlide`/`PropR`/`PropL` with `StateMachineBuilder.ATTACH_OFFSET`, a +90° X rotation. It is not identity: imported joints keep Blender's bone axes, while the attached glbs are converted to Y-up. The tests check that the sax hangs from the mouth and the slide travels along its axis.
- **Blending.** Every switch is a crossfade (0.25 s between moves, 0.3 s back to idle, at 120 BPM), including interrupting a move half way. One-shot moves and reactions fade back to idle on their own; the band's `freeze` holds until `set_band_state`.
- **Close-up moves.** The t3 moves, `t5_rhythm_slaps` and `t5_cowbell` need Terence 0.70 m in front of the player (`t3_circle_kit` 1.3 m). He walks there around the band (1.4 m/s, 1.9 m/s for t4/t5 moves), keeping at least 0.85 m from every seat marker, turns to face the player, then plays the move; when a one-shot is over he walks back. A move requested while he walks waits for him (the newest request wins). He plays `walk` while travelling once the glb has one; until then he glides in the idle stare and a warning says so.
- **Thrown props** (chair, drum, cymbal, music, knocked stand) stay in the room where they land for 8 s, or until the same move starts again. The room's `stand_terence` is hidden while the knockable stand or the thrown music exists, so it never doubles them.
- **Tempo.** Every animation (all of Terence's moves, his `walk`, every band action) plays at bpm / 120, so an event authored at frame f happens f / 24 × 120 / bpm seconds in. `TEMPO_POLICY` at the top of `terence.gd` and `band_member.gd` lists the exceptions: `Tempo.FREE` keeps an animation in real time, `Tempo.LOCKED` (band `play_loop`, Terence `t0_conduct`) also phase-locks the loop to a loaded song's beat; anything unlisted is `Tempo.SYNCED`. Tempos outside 40 to 400 BPM are rejected.
- **Walking with the tempo.** His travel speed is the walk cycle's ground speed (`speed_mps` on `walk` in the moves JSON) times its playback rate, so his feet never slide. The 1.4 and 1.9 m/s gaits are the speeds at 120 BPM and scale with the tempo, as does his turning.
- **Crossfades with the tempo.** Fades are authored in seconds at 120 BPM and scale like the moves they join, clamped to 0.12 to 0.45 real seconds (`Tempo.MIN_FADE`, `MAX_FADE`). Unclamped, a 0.25 s fade would be a 75 ms pop at 400 BPM and 0.75 s of mush at 40.
- **Beat lock.** After `load_song`, each `set_song_position` turns song time into a beat through the MIDI tempo map (`SMFParser.seconds_to_beats`), follows its tempo changes with `set_tempo`, and hands the beat to every character. A locked loop is one 4-beat bar: it joins the song at the song's bar position and is then nudged toward it (time constant 0.5 s, never more than 25% off tempo), so drift and seeks are corrected without jumps. Between calls the beat runs on at the current tempo; without a song, loops free-run at the scaled tempo. A manual `set_tempo` lasts until the next `set_song_position` while a song is loaded.

## XR / Quest

`project.godot` enables OpenXR only for Android exports (`xr/openxr/enabled.android=true`), so the desktop preview never needs a headset. On the Quest, `PlayerRig` stops with an error unless OpenXR initialises and passthrough (alpha blend) is supported. Without passthrough the real kit would be invisible. Not done here, because it needs your Android setup:

1. Android SDK/JDK, plus Godot's Android export templates (Editor > Manage Export Templates).
2. The **Godot OpenXR Vendors** plugin (Meta loader and passthrough) from the Asset Library. Enable it, and turn on Passthrough in the Meta section of the Android export preset.
3. An Android export preset with XR Mode = OpenXR.

## Not done yet / known gaps

- **Untested on a headset.** The XR path hasn't run on a Quest yet.
- **Lighting is real-time.** It uses ambient light, one warm directional and omni lights at the sconces. The Quest will want baked lightmaps (LightmapGI) for the room.
- **Real kit space.** The room's geometry still covers the area where the real kit is. A passthrough cut-out around `spot_drummer` is still to do.
- **Close-up targeting.** Terence's aimed moves use the authored default player head. Live re-aiming (look-at IK toward the real headset) isn't implemented.
- **Thrown props** don't collide: they stay at the pose of the landing frame.
