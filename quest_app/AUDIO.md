# The audio chain, end to end

Written for the whole team (2026-10-24). It covers what makes sound, where it plays, which clock each
part runs on, and how one number, **t0**, keeps them in step. It also lists the latencies we have
measured and the ones still unknown. Message formats are in `CONTRACT.md` at the repo root; this file
explains how the audio uses them.

## The picture

```
 LAPTOP  (one clock: time.monotonic(), "laptop seconds", contract.now())
 ┌─────────────────────────────────────────────────────────────────────────────────────┐
 │  session controller (hub/session.py) ── picks t0 = downbeat of the first bar        │
 │        │ session.state count_in {song_id, t0, from_bar, song_time_at_t0}            │
 │        ▼                                                                            │
 │  Fletcher (fletcher/) ── barks: clip_id ─────────────┐                              │
 │     │ lines: Claude text → ElevenLabs → PCM 24 kHz ──┤  fletcher.speech / .audio    │
 │     │ count-off: cue with t0 + clip ids ─────────────┤  fletcher.state (cues)       │
 │     ▲                                                ▼                              │
 │     │ judgment.note                         hub/server.py  ws :8765 ◄──────────┐    │
 │  bridge (hub/bridge.py): score gate + Chris's matcher                          │    │
 │     ▲  "time_s 0 onset" lines (laptop seconds)                                 │    │
 │  Kevin's transcriber --onsets-only + Quest mute filter                         │    │
 │     ▲ 48 kHz blocks with laptop-clock dates                                    │    │
 │     ├── laptop mic (sounddevice)                                               │    │
 │     └── mic.pcm frames from the Quest ◄────────────────────────────────────────┤    │
 │  library server :8766 ── backing.ogg, arrangement.mid over HTTP ──────────┐    │    │
 └───────────────────────────────────────────────────────────────────────────┼────┼────┘
                                                                  Wi-Fi     │    │
 QUEST  (its own clock: Time.get_ticks_usec(); laptop times mapped with FletcherHub.to_local_time)
 ┌───────────────────────────────────────────────────────────────────────────┼────┼────┐
 │  SongCache → user://songs/<id>/ ◄─────────────────────────────────────────┘    │    │
 │  FletcherHub (ws client) ◄─────────────────────────────────────────────────────┘    │
 │     │                                                                               │
 │     ├─► FletcherVoice (on Terence's head bone) ──► Voice bus ─┐                     │
 │     │     barks, streamed lines, count-off words              │ ducks SFX (≤18 dB)  │
 │     │                                                         │ ducks Band (≤6 dB)  │
 │     ├─► FletcherLink → SceneDirector → Terence's moves        ▼                     │
 │     │        └─► SfxCuePlayer (move frames) ──► SFX bus ──► Master ──► speakers     │
 │     └─► GameSession → BackingPlayer (backing.ogg) ──► Band bus ─┘   (+ limiter)     │
 │                         song_time() ──► SceneDirector.set_song_position (band)      │
 │  MicStreamer (opt-in): AudioStreamMicrophone → muted bus → mic.pcm → hub ───────────┤
 └─────────────────────────────────────────────────────────────────────────────────────┘
```

## 1. On the laptop: what Fletcher sends

- **Barks** are short clips generated ahead of time (`fletcher/barks/*.wav`, copied to
  `quest_app/fletcher_barks/`). Fletcher sends only the name: `fletcher.speech {clip_id}`. Nothing
  travels over the network but the name, so a bark costs about 0 ms of network time.
- **Lines** (the tirade after a cutoff): Claude writes the text and ElevenLabs turns it into speech.
  ElevenLabs returns 16-bit mono PCM at 24 kHz (`pcm_24000`). Fletcher forwards it in chunks as
  `fletcher.audio {utterance_id, index, pcm_b64}` and ends with `final: true`. Our budget is cutoff to
  first sound at most 1.5 s. The "Stop!" bark covers the wait, and the line starts when the bark ends.
- **Count-off** ("five, six, seven, eight"): a `fletcher.state` cue with `t0`, `bpm` and the clip ids
  of the words (`fletcher/countoff/`, copied to `quest_app/fletcher_countoff/`). At a song start the
  controller publishes `count_in` and Fletcher sends this cue. After a cutoff Fletcher picks t0
  himself, sends the cue, then sends `session.command restart` at t0 − 4 beats.

## 2. On the Quest: what makes sound

There are three buses into `Master`. `Master` has a hard limiter at −0.5 dB.

| bus | what | who plays it | level |
|---|---|---|---|
| `Voice` | Fletcher: barks, lines, count-off words | `FletcherVoice` (3D, on Terence's Head bone, `unit_size` 5 m) | 0 dB |
| `SFX` | Adam's sound effects (stomps, slams, throws) | `SfxCuePlayer` per character → shared `SfxVoicePool` (3D) | −3 dB, dry |
| `Band` | the song's backing track (+ optional click) | `BackingPlayer` (not 3D) | −6 dB |

- **Barks.** A bark plays at once, unless one of its moves has to start before the word (negative
  `at_s`). Then the clip is held back by up to 0.3 s (`bark_lead_max_s`) so the move lands on the
  word. The cutoff fist on "Stop!" may hold up to 0.5 s (`cutoff_lead_max_s`).
- **Lines.** FletcherVoice collects 0.15 s of audio (`prebuffer_s`) before it starts a line, to ride
  out Wi-Fi jitter. Then it plays the line through an `AudioStreamGenerator`. Body cues fire when
  their chunk (`at_index`) is actually heard.
- **Count-off.** FletcherVoice schedules each word so that its *onset* (manifest `onset_s`, the
  moment the word begins inside the clip) lands on beats −4..−1 before t0. FletcherLink starts the
  finger move 5 beats before t0.
- **SFX.** Adam's `SfxCuePlayer` fires each cue when Terence's (or a band member's) animation
  passes the cue's frame, so the sounds follow the moves and need no clock of their own.
- **Ducking.** Two buses are ducked from the `Voice` bus's peak meter, like a sidechain compressor:
  threshold −42 dB, 5:1. `FletcherVoice` turns `SFX` down by up to 18 dB (attack 10 ms, release
  450 ms; measured in `fletcher/preview/SYNC_REVIEW.md`: masked speech 3.7 % → 0.3 % in the cutoff
  scene). `BackingPlayer` turns `Band` down by up to **6 dB** (attack 20 ms, release 0.6 s). The
  values are its `duck_*` exports, and `duck_max_db = 0` turns it off. With no speech both buses stay
  at their own volume. The ducking is done in script, not with a bus effect, because Adam's test
  requires the SFX bus to stay dry.
- **The backing track.** `songs/<id>/backing.ogg` holds every non-drum part (Ogg Vorbis, 48 kHz
  stereo). Its sample 0 is song time 0, the downbeat of bar 1. The library renders it with
  FluidSynth (`library/render.py`, at most 1.33 ms late). `SongCache` downloads it to
  `user://songs/<id>/`. `GameSession` then calls the BackingPlayer:

  ```gdscript
  backing.load("user://songs/<id>")                          # backing.ogg (+ click.ogg)
  backing.play_at(hub.to_local_time(t0), song_time_at_t0)    # on count_in
  director.set_song_position(backing.song_time())            # every frame
  backing.stop(0.15)                                         # on stopped (the fist)
  backing.seek(song_time_at_t0, hub.to_local_time(t0))       # the restart's count_in
  ```

  `play_at(local_t, song_t)` makes song time `song_t` come out of the speaker at `local_t`. It uses
  Godot's own model: `play()` takes effect at the next mix (`AudioServer.get_time_to_next_mix()`).
  Godot then delays every playback by 68 frames (its 64-frame lookahead plus the resampler, measured
  below). The result is heard `output_latency()` after that. The player starts the stream at the
  frame that lands closest to the target, at the position that puts `song_t` exactly on `local_t`:
  - **Early:** it plays up to `preroll_s` (50 ms) of what comes before.
  - **Late:** if `local_t` has already passed, it starts at once and skips ahead.

  `song_time()` is the clock the band's beat lock follows. It is anchored to the wall clock at the
  start, so it moves smoothly and has none of the 10 ms steps the raw playback position has. It is
  pulled toward the stream's real position with a 1 s time constant, so it follows the audio
  clock's drift. Before the start it counts up to the target, so it is negative during a count-in
  from bar 1. After `stop()` it holds still.

## Levels (measured 2026-10-24)

The game preview measured the backing at about −35 dB mean, and Fletcher's voice was under the
band: the backing was peak-normalised (−3 dBFS), which left songs 5 dB apart (−21 to −16 LUFS),
and the voice's 3D attenuation (`unit_size` 2.0) took 7.9 dB off at Terence's conducting spot,
4.96 m from the seated eye. Now:

| stage | setting | measured |
|---|---|---|
| `backing.ogg` (`library/render.py`) | normalised to **−18 LUFS** integrated (BS.1770), sample peak ≤ −1 dBFS | bond_demo −18.9 (peak-capped), whiplash / caravan / jump −18.1 |
| Band bus | −6 dB, ducked ≤ 6 dB under Voice | backing at Master: rms −27 to −29 dBFS alone, −32 to −35 under speech |
| Voice | 0 dB bus; `FletcherVoice.unit_size` **5.0** (0 dB at his spot, ≤ +3 dB when he walks up: `max_db`) | barks rms −24 dBFS, peak −6 dBFS at Master |
| voice vs backing | | **+2.5 to +4.4 dB** over the band alone, **+7.6 to +10.6 dB** over the ducked band |
| SFX (Adam's, unchanged) | −3 dB bus, per-cue `gain_db`, `unit_size` 2.5, ducked ≤ 18 dB under Voice | clips −22 LUFS median |

Clips: barks −18 LUFS median (peak ≤ −0.8 dBFS), count-off words −19, SFX −22, `click.ogg` −25.
ElevenLabs lines are generated at the same level as the barks (same voice and engine).

Why these numbers: speech needs ~6–10 dB over music to stay intelligible, and the duck gives that
while he speaks; between lines the band sits only ~3 dB under his voice, so it's clearly heard.
The band is kept well under full scale on purpose: the drummer plays an acoustic kit, which on
its own is louder in the room than the headset can be, so the backing is a guide to play with,
not something to compete with the drums; and a louder band leaks more into the mic (the Quest's
own speakers sit next to its mic: see "Unknown"). Everything is relative: the drummer sets the
overall level with the headset volume. To change the balance: `BackingPlayer.gain_db` (per
player), the Band bus in `default_bus_layout.tres`, or `duck_max_db`. Re-measure with

    Godot --headless --path quest_app --script tests/measure_mix.gd -- song=$PWD/songs/bond_demo

(the real scene: seated camera, Terence's head bone, the song's backing, five barks; exits 1 if
the voice is not ≥ 6 dB over the ducked band). Songs imported before 2026-10-24 were re-levelled
in place with `render.normalise` (re-encoded, sample alignment unchanged: checked with impulses).

## 3. The mic path back

1. **Mic.** Either the laptop mic (`python -m nqmt --mic laptop`) or the Quest mic. The Quest mic is
   `MicStreamer` (opt-in): `AudioStreamMicrophone` goes to a muted bus with an `AudioEffectCapture`
   and is sent as `mic.pcm` frames (~50 ms each, 44.1 kHz on Quest 2). Each frame carries `local_t`,
   the Quest-clock time of its first sample, and the Quest's `clock_offset`.
2. **Dates.** `live_transcription/tools/live_input.py` dates every 48 kHz block on the laptop clock:
   - **Laptop mic:** from PortAudio's ADC time, minus the input latency.
   - **Quest mic:** `local_t` mapped back with the same clock offset the Quest uses for t0. The
     network delay therefore cancels.
3. **Onsets.** Kevin's transcriber `--onsets-only` finds onsets (5 ms hop; about 170 ms from hit to
   line, because it waits for context). The **Quest mute filter** (`QuestMuteFilter`) drops the false
   onsets caused by the Quest OS: it mutes the mic to about −90 dB roughly 0.1 s after a loud hit and
   un-mutes ~50 ms later, so the ringing drum looks like a new attack.
4. **Bridge.** The bridge (`hub/bridge.py`, clock mode) turns a line time into song time:
   `t − t0 + song_time_at_t0 − input latency`. The score gate gives each onset to the notes the
   sheet expects within ±100 ms (plus tracked drift) and drops re-triggers. Chris's matcher grades
   each hit, and `judgment.note` goes to Fletcher. Fletcher's mood decides barks and cutoffs.

## 4. The clocks, and how t0 ties them

| clock | where | used for |
|---|---|---|
| laptop `time.monotonic()` | hub envelopes `t`, `t0`, bridge, Fletcher, mic dates | **the** clock (CONTRACT.md) |
| Quest `Time.get_ticks_usec()/1e6` | FletcherVoice, FletcherLink, BackingPlayer, MicStreamer | everything scheduled on the headset |
| Quest audio clock | the mixer's sample count | what is actually heard; drifts slightly against the tick clock |
| song time | seconds from bar 1's downbeat (tempo map `drums.tempo.json`) | sheet, backing.ogg, MIDI phrases |

- **Laptop to Quest.** `FletcherHub.clock_offset = min(local − envelope t)` over every frame
  received. Network delay only ever makes a sample later, so the minimum is the best estimate.
  `to_local_time(t) = t + clock_offset`. It is off by the *smallest* one-way Wi-Fi delay, which is
  unknown (probably 1–3 ms).
- **t0** is the laptop-clock moment at which song time `song_time_at_t0` is heard (the downbeat of
  `from_bar`):
  - The count-off words land on beats −4..−1 before it.
  - The finger starts 5 beats before it.
  - The backing plays `song_time_at_t0` at it.
  - The bridge judges hits against `t0 − input latency`.

  Every part works out its own schedule from that one number. Nothing is chained to anything else.
- **Audio clock vs tick clock.** `BackingPlayer.song_time()` follows the audio clock, so the band's
  animation follows what is heard. The count-off words are scheduled on the tick clock, which is
  fine: they only last 4 beats.

## 5. Latencies

### Measured

| what | number | where from |
|---|---|---|
| BackingPlayer `play_at` / `seek` / late start / click vs scheduled (Godot's model, CoreAudio 48 kHz) | **0.00–0.06 ms** | `tests/measure_backing.gd` (below) |
| `play_at` from song 0 (nothing to pre-roll) | 0 to −3 ms (early by up to half a frame) | same |
| `song_time()` step jitter / vs heard | 0.00–0.02 ms / ±0.03 ms (raw `get_playback_position()+time_since_last_mix`: 10.7 ms steps) | same |
| Godot's playback delay (lookahead + resampler) | 68 frames = 1.42 ms at 48 kHz, for 48 and 44.1 kHz streams | same; `BackingPlayer.MIX_DELAY_FRAMES` |
| Godot mix period | 512 frames = 10.67 ms (CoreAudio); Dummy driver 4096 frames ≈ 93 ms, runs ~4 % slow | probe |
| `AudioServer.get_output_latency()` on this Mac (CoreAudio) | **0.0** (driver reports nothing) | probe |
| count-off word onsets vs beats | +1 to +9 ms | `hub/full_e2e.py`, 2026-10-24 |
| count-off finger vs t0 − 5 beats | +9 / +11 ms | same |
| bark: hub frame → speaking (no lead) | +9 ms | same |
| "Stop!" bark held for the fist | 417–434 ms after the stop command (by design: `at_s −0.417`) | same |
| fist key frame vs "Stop!" (Movie Maker) | −14 ms; count-off fingers vs words −1..−9 ms | `fletcher/preview/SYNC_REVIEW.md` |
| restart command vs t0 − 4 beats | 0 frames | same |
| transcriber hit → line | ~170 ms (bridge default `latency_s 0.17`) | `hub/README.md` |
| laptop mic input latency (MacBook Pro) | 41.5 ms; corrected dates 2.5 ms late in a speaker→mic loopback | `live_input.py` (agent C) |
| PortAudio *reported* MacBook latency | in 127.5 ms, out 32.6 ms (reported, not measured) | `datacollection/takes/calibration.failed.json` |
| Movie Maker renders | every sound ~40 ms (one mix block) after its frame: a renderer constant | SYNC_REVIEW |

How the BackingPlayer numbers were measured (`tests/test_backing_player.gd`): an
`AudioEffectCapture` is put on the Band bus. Every frame the test notes when the last mix began
(`now − get_time_since_last_mix()`) and how many frames the capture had received by then, which
dates every captured frame. The fixture `backing.ogg` has tone bursts at 1.0, 2.0 and 3.0 s. Each
burst's first captured frame above −20 dBFS, plus the output latency, is compared with the time
the player scheduled it for. This checks everything up to the DAC: Godot's mixing, Ogg seeking, the
start logic and the clock. It does **not** check the speaker or the room. The headless suite runs
the same tests with coarse tolerances, because the Dummy driver isn't real time. For the precise
run (muted, no sound), from `quest_app/`:

    Godot --headless --audio-driver CoreAudio --path . --script tests/measure_backing.gd

### Unknown (to measure on the Quest)

- **Quest output latency.** `project.godot` has no audio latency settings, so Godot's defaults
  apply. Godot's Android audio has a long history of high output latency (Godot issue tracker), and what it reports is not to be trusted. On this Mac
  `get_output_latency()` returns 0, and it may well return 0 on the Quest too. Every Godot-scheduled
  sound (count-off words, barks, backing) would then be heard late by the same unknown amount. They
  stay in step *with each other*, but are late against t0 and against Terence's moves. First check:
  print `AudioServer.get_output_latency()` and `get_mix_rate()` on the headset.
- **Quest mic input latency.** `MicStreamer.input_latency_s` is 0 for now.
- **Whether the Quest's own speakers reach its mic.** The OS mutes the mic after loud attacks, which
  suggests voice processing, possibly echo cancellation. If it doesn't cancel the speakers, the
  backing's bass and guitar notes become onsets: the score gate will drop some, and the rest count
  as extras.
- **Wi-Fi.** The minimum one-way delay (the bias in `clock_offset`) and how much it jitters on the
  demo network.
- **Drift** between the Quest's audio clock and its tick clock. `song_time()` follows it, but
  nothing else does.

## 6. Proposed one-time calibration (loopback clap)

**Implemented (2026-10-24):** `python -m nqmt.calibrate --mic quest|laptop` +
`scripts/game/calibration_player.gd` (messages: CONTRACT.md `calibrate.*`). Differences from the
proposal below: the laptop sends the schedule (laptop times) and the headset maps it with
`to_local_time`, exactly as it maps `t0` for the backing, so any bias in that mapping is in the
song too and is calibrated with it; the claps are a generated train started once by
`BackingPlayer.play_at`. RT goes to `calibration.json` and the bridge's `input_latency` for that
mic. Desktop loopback (MacBook speaker -> its mic, laptop mic path): 18.3 ms, spread 0.2 ms.

For judging, only the **round trip** matters: what the drummer hears versus when the mic reports
his hit. The drummer plays to the band he hears. If the Quest output is late by `Lo` and the mic
path is late by `Li`, a perfect hit is reported `Lo + Li` after its scheduled time. So subtract the
measured round trip `RT = Lo + Li` (plus the air path) in one place and the timing is right,
without knowing either latency separately.

1. **Click.** In a "Calibrate" menu entry (or at the end of Terence's count-off), the Quest plays 8
   sharp claps through `BackingPlayer` at known Quest times `s_k`, one per beat. A fixture
   `click.ogg` with the clap at song times k works: `play_at(local_t, 0)`.
2. **Hear.** The mic in use (Quest via `MicStreamer`, or the laptop mic) records them. Agent C's
   path dates every onset on the laptop clock. The Quest sends its schedule as laptop times,
   `s_k − clock_offset`, using the same offset that `mic.pcm` uses, so the network bias cancels.
3. **Measure.** The transcriber's onset times `o_k` give `RT = median(o_k − s_k)`. Use the spread to
   check the result: it should be under 2 ms. Write RT once as the bridge's input latency (clock
   mode `input_latency_s`; for the laptop mic, in place of `--mic-latency-ms`).
4. **Visuals (optional).** To make Terence's moves and the band's animation match what is heard,
   the output part `Lo` alone is needed. Get it with the laptop mic, whose `Li` is known (41.5 ms
   measured): `Lo ≈ RT_laptop − 41.5 ms − air (≈3 ms/m)`. Then set `BackingPlayer.extra_latency_s =
   Lo`, and give FletcherVoice's scheduling the same value (one shared setting; see "Open issues").
5. **Fallbacks.**
   - If echo cancellation removes the Quest's own clicks, use the laptop mic for step 2.
   - If no mic works, use a **tap test**: the drummer plays 8 rim clicks with the clicks. The human
     bias, usually a little early, goes into RT, which is arguably what we want anyway.

Agent C's `mic.pcm`/`live_input.py` already carries everything needed: absolute sample indices, the
Quest `local_t` and the `clock_offset`. What's missing is a calibrate mode that schedules the claps,
collects onsets for ~10 s and prints/stores RT. It would need a small `session.command calibrate`,
or a console button.

## Open issues

- **Two latency settings.** FletcherVoice (generated from `fletcher/godot/`) schedules the count-off
  with `AudioServer.get_output_latency()` alone. BackingPlayer adds `extra_latency_s`. After a
  calibration both need the same number. Proposal: one shared value, e.g. a project setting
  `nqmt/audio/extra_output_latency_s` that both read. That would be a change in `fletcher/godot/`.
  FletcherVoice also leaves out Godot's 68-frame playback delay (1.4 ms), which is negligible.
- **A start from bar 1 can be ~half a frame early.** There is no audio before song time 0 to
  pre-roll, so the start can't be placed between mixes. That is ≤7 ms at 72 fps, and 0–3 ms
  measured on desktop.
- **Backing in the mic.** See "Unknown". Worth a `pa_bleed`-style take with the backing playing.
