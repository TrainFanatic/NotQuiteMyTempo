# Not Quite My Tempo (Godot 4.5)

Mixed-reality drum practice for Meta Quest 2: you play a real kit (seen through passthrough) while a virtual big band and their bandleader, Terence, rehearse around you. The art assets come from `~/not-quite-my-tempo-assets` (Blender scripts, PS2 style).

## Run it

- **Desktop preview:** open the project and press Play, or run `Godot --path .` from this folder. You see the room from the drummer's seat, and `scenes/demo_timeline.json` plays a short rehearsal: count-off, band in, conducting, fist cutoff, the band looks at you, a point and a shout.
- **Quest:** see "XR / Quest" below.
- **Tests (headless):**
  ```
  Godot --headless --path . --script tests/run_tests.gd
  ```
  There are 23 tests (plus `tests/test_fletcher.gd`, below). They cover the import validator, the MIDI parser and phrases, the band and Terence animation switching (crossfades, auto-return to idle), instrument and prop placement, Terence's walking route around the band, thrown props and the hidden room stand, scene assembly, and the director API via the fake injector. The runner exits 1 on any failure. Lines starting with `ERROR:` in the output come from tests that check error handling on purpose.
- **New assets:** after rebuilding anything in the asset repo, run `tools/sync_assets.sh`, then open the editor or run `Godot --headless --path . --import`.

## Layout

| Path | What |
|---|---|
| `assets/` | copied glbs: `room/room_v005.glb`, `band/` (16), `instruments/` (16), `props/` (7), `terence/terence_v004.glb` + move metadata |
| `tools/post_import_character.gd` | post-import for band and Terence: restores the `play_loop` name (Godot strips `_loop`) and sets loop modes from the move metadata |
| `scenes/rehearsal.tscn` + `scripts/rehearsal.gd` | main scene, built at runtime from the room markers |
| `scripts/intro.gd` | Terence's entrance at game start (`Rehearsal.intro_enabled`, on by default): `door_main` swings open, he walks in to `spot_terence`, the band startles, the door shuts with a quiet `door_slam`, he checks his watch and stares. The showcase or demo starts on `intro_finished`. About 9 s at 120 BPM, scaled by tempo |
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

## Fletcher integration

Fletcher (the bandleader's mind and voice, `fletcher/` in the repo, running on the laptop) can drive Terence live through the team hub (`hub/server.py`, WebSocket port 8765). Off by default, so nothing above changes unless you turn it on.

- **What was added.** `scripts/fletcher/fletcher_setup.gd` builds it; `rehearsal.gd` gained `fletcher_enabled`, `fletcher_hub_url` and a few marked `# --- Fletcher` lines. It makes `FletcherHub` (reconnects every second, so no laptop is fine), `FletcherVoice` on a `BoneAttachment3D` on Terence's `Head` bone, and `FletcherLink`, which turns Fletcher's actions and timed body cues into `SceneDirector.handle_message()` calls, one move at a time. With Fletcher on there is no showcase or demo. During the intro Fletcher can talk but does not move Terence; at `intro_finished` the link starts and catches up on the latest tempo, band and mood.
- **Audio.** Fletcher's players are on a new `Voice` bus (`default_bus_layout.tres`), separate from the SFX pool, so neither steals the other's voices. While he speaks, `FletcherVoice` ducks the `SFX` bus (its `duck_*` exports: from the `Voice` bus peak meter, threshold -42 dB, 5:1, at most 18 dB, attack 10 ms, release 450 ms) so impacts and crashes don't mask his words; with no speech the bus stays at its own volume, so your mix and `SFX is dry` are unchanged. Measured in Movie Maker renders in `fletcher/preview/SYNC_REVIEW.md`. Set `duck_max_db = 0` on the voice to turn it off.
- **Enable.** Tick `fletcher_enabled` and set `fletcher_hub_url` to `ws://<laptop LAN IP>:8765`, or launch with `Godot --path . -- --fletcher ws://<ip>:8765`. The Quest preset now has the INTERNET permission.
- **Files.** `scripts/fletcher/fletcher_{hub,voice,link}.gd`, `fletcher_barks/` (barks by `clip_id`) and `fletcher_countoff/` (count-off words, `manifest.json`, `cues.json`) are generated copies: edit `fletcher/godot/` and re-run `fletcher/godot/sync_to_quest_app.sh`, then `Godot --headless --path . --import`. `tests/test_fletcher.gd` covers it (no server needed).
- **Test switches** (for `hub/full_e2e.py`, the whole-system test): `-- --no-intro` turns `intro_enabled` off (the only line added to `rehearsal.gd` for it); in `fletcher_setup.gd`, `--fletcher-log` prints Terence's `move_started`/`move_finished` and the voice's `speaking`/`finished`/`subtitle`/count-off words as `[fletcher-log] <laptop s> <event> <arg>`, and `--fletcher-quit-on-close` quits when the hub closes the socket (Godot has no SIGINT handler, so that is its clean shutdown). All off unless given.

## Backing track and audio buses

- **Buses** (`default_bus_layout.tres`): `Master` <- `SFX` (yours, unchanged), `Voice` (Fletcher), and a new `Band` (-6 dB) for the song's backing track. `FletcherVoice` still ducks `SFX` under his voice; `BackingPlayer` ducks `Band` the same way, at most 6 dB (`duck_max_db`).
- **`scripts/game/backing_player.gd`** (`BackingPlayer`): plays `songs/<id>/backing.ogg` (every non-drum part, sample 0 = bar 1's downbeat) so a given song time is heard at a given moment of the device clock (`play_at`, `seek`, `stop`, `song_time()` = the playback clock the band's beat lock follows). Tests: `tests/test_backing_player.gd` (fixtures from `tests/fixtures/make_backing_fixtures.py`); for real timing numbers run `Godot --headless --audio-driver CoreAudio --path . --script tests/measure_backing.gd` (muted).
- **`AUDIO.md`**: the whole audio chain (laptop and headset), every clock, and the latencies measured so far and still unknown.
- **Quest mic to the laptop** (`scripts/game/mic_streamer.gd`, opt-in: the game adds it only when the mic source is QUEST). Captures like Kevin's recorder (`AudioStreamMicrophone` on a muted bus with an `AudioEffectCapture`; the `QuestMicStream` bus is made at runtime, so `default_bus_layout.tres` is unchanged) and sends 50 ms 16-bit mono `mic.pcm` frames through `FletcherHub.send` for `live_transcription/tools/transcribe_wav.py --input hub:...`. The Quest preset now has RECORD_AUDIO. It also needs `audio/driver/enable_input=true` in `project.godot` (not set yet; without it the streamer warns and sends nothing). `tests/test_mic_streamer.gd` covers the framing.

## Game mode (menu, songs, results)

The playable game on top of the rehearsal, off by default like Fletcher: tick `game_mode` on the Rehearsal node or launch with `-- --game` (it turns Fletcher on, since it needs his hub). After the intro a menu stands on a black music stand in front of the throne, a sheet of manuscript paper just under eye level. It needs the laptop running `python -m nqmt` (hub, library, session controller); see `CONTRACT.md`.

- **What was added.** Everything lives in `scripts/game/`: `game_session.gd` (the flow), `game_menu.gd` + `game_panel.gd` (the menu, a SubViewport drawn on a quad, nearest filtered), `game_input.gd` (pointing), `game_hud.gd`, `song_cache.gd` (downloads). `backing_player.gd` (the backing track) and `mic_streamer.gd` (opt-in Quest mic) come from the audio and mic work. `rehearsal.gd` gained `game_mode`, `start_demo_scene()`/`stop_demo_scene()` and a few marked `# --- Game` lines; with game mode off nothing changes. `project.godot` now has `audio/driver/enable_input=true` (for the Quest mic).
- **Pages.** Songs (from `library.songs`), PLAY, DEMO SCENES (your showcase or the demo timeline, exactly as before, with a small BACK TO MENU card to leave), SETTINGS (hub IP on a keypad, mic laptop/Quest, audio latency in ms for the backing and Fletcher's voice; kept in `user://game_settings.cfg`), a waiting page and the results (`session.result`).
- **Input.** No controllers: gaze at an item and a red ring fills, 1.5 s picks it; an OpenXR hand pinch picks the gazed item at once; on the desktop the mouse hovers (same dwell) and clicks. After a pick the gaze must move about 2 degrees before it can dwell again, so the next page's button under a resting gaze is never picked by accident. Everything can also be done from the laptop console: the menu only follows `session.state`.
- **The flow.** PLAY sends `session.command start`. On `count_in` the menu goes, the song's `backing.ogg`, `arrangement.mid` and `band_tracks.json` come from the library over HTTP (cached in `user://songs/<id>/`, refreshed when `imported_at` changes), `load_song` gets the arrangement, the backing is scheduled so `song_time_at_t0` is heard at `t0`, and the sections with a part raise 0.6 s (at 120 BPM) before the downbeat. From t0 `set_song_position` follows the backing's clock every frame. `stopped` stops the backing, a restart's `count_in` seeks it, `idle` brings back the menu. While an arrangement is loaded, Fletcher's link reaches the director through a small proxy in `game_session.gd`: its whole-band raise is held back (the phrases decide who plays) and its whole-band freeze goes only to players in `play_loop`.
- **HUD.** Two small world-space labels: the song and bar with a RUSHING/DRAGGING hint from `judgment.note` (up left), and Fletcher's last line (low in front).
- **Quest export** (`export_presets.cfg`, custom feature `nqmt_game`): the APK boots straight into game mode, and with no saved settings uses hub `ws://127.0.0.1:8765` (the laptop over USB via `adb reverse`) and the Quest mic. Desktop runs are unchanged (`-- --game` as before). Two marked lines in `rehearsal.gd`, one in `game_session.gd`. Headset test steps: `nqmt/HEADSET_TEST.md`.
- **Latency calibration** (`scripts/game/calibration_player.gd`, made in `GameSession.setup`): on `calibrate.start` from `python -m nqmt.calibrate` it plays short claps on the Band bus through the same `BackingPlayer.play_at` scheduling as the song, so the laptop can measure the round trip a drummer sees (CONTRACT.md, `calibrate.*`). Test: `tests/test_calibration_player.gd`.
- **Tests.** `tests/test_game.gd`. `-- --game-log` prints each step as `[game] <s> <event>`. A rendered preview of the flow: `fletcher/preview/tools/render_menu.sh`.

## Not done yet / known gaps

- **Untested on a headset.** The XR path hasn't run on a Quest yet.
- **Lighting is real-time.** It uses ambient light, one warm directional and omni lights at the sconces. The Quest will want baked lightmaps (LightmapGI) for the room.
- **Real kit space.** The room's geometry still covers the area where the real kit is. A passthrough cut-out around `spot_drummer` is still to do.
- **Close-up targeting.** Terence's aimed moves use the authored default player head. Live re-aiming (look-at IK toward the real headset) isn't implemented.
- **Thrown props** don't collide: they stay at the pose of the landing frame.
