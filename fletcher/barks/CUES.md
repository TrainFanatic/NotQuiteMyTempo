# Bark body cues

`cues.json` (keyed by clip_id) gives every bark 1-2 Terence moves, timed so the move's key event
frame lands on the clip's emphasized syllable. It is data only; `manifest.json` stays as
`generate_barks.py` writes it. Each cue: `gesture` (a Terence move id from Adam's
`quest_app/assets/terence/terence_v004_moves.json`), `at_s` (seconds after the bark starts playing, i.e. FletcherVoice's
`speaking` signal), `priority` (2 = must play), plus two informational fields: `key_frame` (the
move's event frame at 24 fps / 120 BPM) and `emphasis_s` (measured syllable peak in the clip).

## How it was measured
Clips at 24 kHz. First sound / end = `countoff.audible_span` (-40 dB). Syllable peaks = local
maxima of the <1 kHz, 10 ms RMS envelope (`countoff.envelope`) above 45 % of the clip max, merged
within 150 ms. Emphasis was chosen per text (the stressed content word, not always the loudest
peak); for "From bar N" it is the first peak >= 0.2 s after "bar" (~0.35 s), i.e. the number.
Script: `~/.claude/jobs/7b7e33c1/tmp/agent_barks/{analyze,build}.py` (v003), re-timed for v004 by
`build_v004.py <repo>`: same clips, gestures and emphasis, key frames read from the moves json.

## v004 (Adam's moves of 2026-10)
Only the t1_* accents changed: they got a wind-up and a held pose (`windup`, `hold_start`..`release`).
Key frames that moved, and so `at_s`: `t1_fist_cutoff` cutoff 10 -> **14** (stop_1/2), `t1_fist_slam`
slam 12 -> **14** (dragging_3), `t1_flick` flick 10 -> **11** (no_1). Also longer, not used here:
`t1_clap_stop` clap 12, `t1_wave_off` wave 16 (unchanged). The count-off finger is unchanged (finger_up
12/24/36/48, still 5 beats of lead). The headset lets the next move start at a hold's `release`
frame (fist cutoff 50 of 72, slam 26/38, flick 20/28, wave off 28/38, clap stop 23/32), and
`cues.busy_s` spaces two-cue barks by that, not by the full length.

## Tempo
`at_s` is for 120 BPM. Bark audio does not scale with tempo, but Terence's moves do (event at
`f/24 * 120/bpm` s), so the exact value at song tempo is

    at_s(bpm) = emphasis_s - key_frame/24 * 120/bpm      (may be negative)

which is why `key_frame` and `emphasis_s` are in the file (the file's `at_s` is that value at 120 BPM).
A negative `at_s` means the move must start before the clip: Fletcher sends it unclamped and the
headset (FletcherVoice) starts the move at once and holds the clip back by `-at_s`, at most
`bark_lead_max_s` = 0.3 s. That hold is extra latency for the bark: e.g. the Stop fist (`stop_1`) holds
the clip 255 ms at 120 BPM, the full 300 ms below ~111 BPM (at 94 the fist lands 117 ms after the
word), nothing at >= 213 BPM (v004's cutoff frame 14; it was 10 in v003). The table's last note is the
slowest tempo at which the key frame still lands on the word with the 0.3 s cap.
The cutoff "Stop!" (main.py's cutoff bark, fist at priority 2) has its own cap, `cutoff_lead_max_s` =
0.5 s (Fletcher config, and the same export on FletcherVoice): its fist lands on the word down to
~84 BPM; at 94 BPM "Stop!" is heard 424 ms after the stop command instead of 300 ms (+124 ms).

Two-cue barks: `cues.BarkCues` drops a cue that would start while the previous one still plays at the
song tempo (it would wait and land late). `rushing_3` now has a single cue (the point on "sit BACK").

`stop_1`/`stop_2` are both picked as the cutoff bark, which main.py sends right after
`action: fist_cutoff`; the bark's fist cue then duplicates the running move. Godot should treat a
cue for the move already playing as satisfied rather than restarting it.

## Sound clash check
No move used here has vocal sfx: Adam's `sfx_manifest.json` has no breath/grunt/voice sounds today
(all foley: whooshes, stomps, claps, taps, impacts). Loud foley on the accent, by design:
`t1_fist_slam` (fist_slam_stand +1 dB, foot_stomp) on "PUSH", `t4_shout` (foot_stomp -2 dB at f10).
`t0_clap_off` / `t3_clap_in_face` were avoided during play: 0 dB claps off the beat grid would
throw the drummer. **Re-check when Adam's breathing sfx land**: a breath on t4_shout/t1_fist_slam
would sit under Fletcher's voice.

## Table (at 120 BPM)

| clip | text | emphasis s | move | key frame | at_s | why |
|---|---|---|---|---|---|---|
| rushing_1 | Rushing. | 0.212 | t2_point_player | point=8 | -0.121 | accusing jab on RUSH-; on time >= 78 BPM |
| rushing_2 | You're rushing. | 0.426 | t2_point_player | point=8 | 0.093 | finger lands on RUSH-; on time >= 55 BPM |
| rushing_3 | Ahead of me. Sit back. | 1.258 | t2_point_player | point=8 | 0.925 | point on 'sit BACK' (the only cue: a flick before it ran into it below ~97 BPM); on time >= 26 BPM |
| dragging_1 | Dragging. | 0.212 | t2_point_player | point=8 | -0.121 | jab on DRAG-; on time >= 78 BPM |
| dragging_2 | You're dragging. | 0.463 | t2_impatient_watch | taps[0]=14 | -0.12 | taps his watch on DRAG- (tap 2 after the word); on time >= 92 BPM |
| dragging_3 | Behind. Push it. | 0.989 | t1_fist_slam | slam=14 (v003: 12) | 0.406 | stand slam (fist_slam_stand + stomp) on PUSH, then holds the fist on the stand; on time >= 54 BPM |
| no_1 | No. | 0.217 | t1_flick | flick=11 (v003: 10) | -0.241 | flicks him off on 'No.'; on time >= 106 BPM |
| no_2 | No, no, no. | 1.463 | t4_shout | accents[1]=36 | -0.037 | accents 12/36 fall on the 1st and loudest 'no'; stomp f10; on time >= 102 BPM |
| watch_1 | Watch my hand. | 0.54 | t0_cue_section | cue=10 | 0.123 | raises the cueing hand on HAND (was t0_conduct: invisible while he already conducts); on time >= 60 BPM |
| watch_2 | Eyes on me. | 0.394 | t2_point_player | point=8 | 0.061 | points at the drummer on 'ON me'; on time >= 58 BPM |
| stop_1 | Stop! | 0.328 | t1_fist_cutoff | cutoff=14 (v003: 10) | -0.255 | fist lands on STOP (cutoff bark), then the held glare; on time >= 111 BPM |
| stop_2 | Stop. | 0.321 | t1_fist_cutoff | cutoff=14 (v003: 10) | -0.262 | fist lands on STOP (cutoff bark; both stop clips are picked at cutoff); on time >= 113 BPM |
| again_1 | Again. | 0.333 | t2_point_player | point=8 | 0.0 | point lands on -GAIN; on time >= 63 BPM |
| again_2 | Again. | 0.725 | t0_cue_section | cue=10 | 0.308 | quiet 'go' cue on -GAIN; on time >= 49 BPM |
| top_1 | From the top. | 0.456 | t0_cue_section | cue=10 | 0.039 | cue on TOP; on time >= 66 BPM |
| praise_1 | Good. | 0.526 | ap_nod | nods[0]=6 | 0.276 | nod on GOOD (second nod after); on time >= 36 BPM |
| praise_2 | ...Better. | 0.192 | ap_nod | nods[0]=6 | -0.058 | grudging nod on BET-; on time >= 61 BPM |
| count_1 | Five, six, seven, eight. | 0.186 | t0_countoff_verbal | beats[0]=2 | 0.103 | beats 2/14/26/38 on five/six/seven/eight (clip spoken at ~104 BPM); on time >= 21 BPM |
| bar_01 | From bar one. | 0.737 | t2_score_point | taps[0]=16 | 0.07 | taps the bar in his score on the number; tap 2 +0.33 s; on time >= 77 BPM |
| bar_02 | From bar two. | 0.742 | t2_score_point | taps[0]=16 | 0.075 | taps the bar in his score on the number; tap 2 +0.33 s; on time >= 77 BPM |
| bar_03 | From bar three. | 0.771 | t2_score_point | taps[0]=16 | 0.104 | taps the bar in his score on the number; tap 2 +0.33 s; on time >= 75 BPM |
| bar_04 | From bar four. | 0.735 | t2_score_point | taps[0]=16 | 0.068 | taps the bar in his score on the number; tap 2 +0.33 s; on time >= 77 BPM |
| bar_05 | From bar five. | 0.676 | t2_score_point | taps[0]=16 | 0.009 | taps the bar in his score on the number; tap 2 +0.33 s; on time >= 82 BPM |
| bar_06 | From bar six. | 0.718 | t2_score_point | taps[0]=16 | 0.051 | taps the bar in his score on the number; tap 2 +0.33 s; on time >= 79 BPM |
| bar_07 | From bar seven. | 0.719 | t2_score_point | taps[0]=16 | 0.052 | taps the bar in his score on the number; tap 2 +0.33 s; on time >= 79 BPM |
| bar_08 | From bar eight. | 0.507 | t2_score_point | taps[0]=16 | -0.16 | taps the bar in his score on the number; tap 2 +0.33 s; on time >= 99 BPM |
| bar_09 | From bar nine. | 0.615 | t2_score_point | taps[0]=16 | -0.052 | taps the bar in his score on the number; tap 2 +0.33 s; on time >= 87 BPM |
| bar_10 | From bar ten. | 0.687 | t2_score_point | taps[0]=16 | 0.02 | taps the bar in his score on the number; tap 2 +0.33 s; on time >= 81 BPM |
| bar_11 | From bar eleven. | 0.584 | t2_score_point | taps[0]=16 | -0.083 | taps the bar in his score on the number; tap 2 +0.33 s; on time >= 90 BPM |
| bar_12 | From bar twelve. | 0.725 | t2_score_point | taps[0]=16 | 0.058 | taps the bar in his score on the number; tap 2 +0.33 s; on time >= 78 BPM |
| bar_13 | From bar thirteen. | 0.647 | t2_score_point | taps[0]=16 | -0.02 | taps the bar in his score on the number; tap 2 +0.33 s; on time >= 84 BPM |
| bar_14 | From bar fourteen. | 0.654 | t2_score_point | taps[0]=16 | -0.013 | taps the bar in his score on the number; tap 2 +0.33 s; on time >= 84 BPM |
| bar_15 | From bar fifteen. | 0.894 | t2_score_point | taps[0]=16 | 0.227 | taps the bar in his score on the number; tap 2 +0.33 s; on time >= 67 BPM |
| bar_16 | From bar sixteen. | 0.699 | t2_score_point | taps[0]=16 | 0.032 | taps the bar in his score on the number; tap 2 +0.33 s; on time >= 80 BPM |
| bar_17 | From bar seventeen. | 0.649 | t2_score_point | taps[0]=16 | -0.018 | taps the bar in his score on the number; tap 2 +0.33 s; on time >= 84 BPM |
| bar_18 | From bar eighteen. | 0.872 | t2_score_point | taps[0]=16 | 0.205 | taps the bar in his score on the number; tap 2 +0.33 s; on time >= 68 BPM |
| bar_19 | From bar nineteen. | 0.551 | t2_score_point | taps[0]=16 | -0.116 | taps the bar in his score on the number; tap 2 +0.33 s; on time >= 94 BPM |
| bar_20 | From bar twenty. | 0.74 | t2_score_point | taps[0]=16 | 0.073 | taps the bar in his score on the number; tap 2 +0.33 s; on time >= 77 BPM |
| bar_21 | From bar twenty-one. | 0.762 | t2_score_point | taps[0]=16 | 0.095 | taps the bar in his score on the number; tap 2 +0.33 s; on time >= 75 BPM |
| bar_22 | From bar twenty-two. | 0.679 | t2_score_point | taps[0]=16 | 0.012 | taps the bar in his score on the number; tap 2 +0.33 s; on time >= 82 BPM |
| bar_23 | From bar twenty-three. | 0.672 | t2_score_point | taps[0]=16 | 0.005 | taps the bar in his score on the number; tap 2 +0.33 s; on time >= 82 BPM |
| bar_24 | From bar twenty-four. | 0.675 | t2_score_point | taps[0]=16 | 0.008 | taps the bar in his score on the number; tap 2 +0.33 s; on time >= 82 BPM |
| bar_25 | From bar twenty-five. | 0.677 | t2_score_point | taps[0]=16 | 0.01 | taps the bar in his score on the number; tap 2 +0.33 s; on time >= 82 BPM |
| bar_26 | From bar twenty-six. | 0.685 | t2_score_point | taps[0]=16 | 0.018 | taps the bar in his score on the number; tap 2 +0.33 s; on time >= 81 BPM |
| bar_27 | From bar twenty-seven. | 0.679 | t2_score_point | taps[0]=16 | 0.012 | taps the bar in his score on the number; tap 2 +0.33 s; on time >= 82 BPM |
| bar_28 | From bar twenty-eight. | 0.762 | t2_score_point | taps[0]=16 | 0.095 | taps the bar in his score on the number; tap 2 +0.33 s; on time >= 75 BPM |
| bar_29 | From bar twenty-nine. | 0.759 | t2_score_point | taps[0]=16 | 0.092 | taps the bar in his score on the number; tap 2 +0.33 s; on time >= 76 BPM |
| bar_30 | From bar thirty. | 0.791 | t2_score_point | taps[0]=16 | 0.124 | taps the bar in his score on the number; tap 2 +0.33 s; on time >= 73 BPM |
| bar_31 | From bar thirty-one. | 0.702 | t2_score_point | taps[0]=16 | 0.035 | taps the bar in his score on the number; tap 2 +0.33 s; on time >= 80 BPM |
| bar_32 | From bar thirty-two. | 0.703 | t2_score_point | taps[0]=16 | 0.036 | taps the bar in his score on the number; tap 2 +0.33 s; on time >= 80 BPM |

## Count-off (`fletcher/countoff/cues.json`)
The count-off is four clips scheduled on beats, so its cue is anchored to t0 in beats, not to a
clip: **`t0_countoff_finger` at t0 - 5 beats, priority 2** (finger k up on frame 12k = beat k, on
five/six/seven/eight; exact at every tempo since the move scales 12 frames/beat; same as
`COUNTOFF_LEAD_BEATS`, now derived from the json's first `finger_up` frame on both sides). `t0_countoff_verbal` (accents f2/14/26/38, start t0 - 4 beats - 2 frames)
is the alternative if the drummer should see him "speak" the count instead of the fingers.
The bark `count_1` is a fixed recording at ~104 BPM (peaks 0.19/0.77/1.35/1.91 s), so it uses
verbal at 0.103 s: its beats drift up to ~0.2 s from the words at 120 BPM. Prefer the scheduled
count-off whenever tempo matters.

## Moves Adam might want to add
- **Head shake / "no" wag** (key frame <= 4) for `no_1`/`no_2`; today flick lands 200 ms late.
- **Short-lead jab** (point key frame ~4) for one-word barks peaking at ~0.2 s (rushing_1,
  dragging_1, praise_2): every current accent move needs >= 0.25 s of lead.
- **Fist cutoff with key frame <= 7**, so "Stop!" (peak 0.33 s) lands on the vowel at any tempo
  above ~60 BPM. v004 moved it the other way (frame 14, after a wind-up): on time only >= 111 BPM.
- **Palm-down "sit back" / settle gesture** for rushing_3 and **"push" forward beckon/shove**
  (stationary, unlike t4_charge) for dragging_3.
- **Point at own eyes** for watch_2 "Eyes on me".
- **Score tap with one tap** (taps[0] earlier than f16) for "From bar N"; current t2_score_point
  needs 0.67 s lead, so bar_08/11/19 tap 80-160 ms late (bar_09 52 ms).
