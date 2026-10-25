# datacollection/: what to record, how, and how it becomes labels

Owner: Aaron. Feeds Kevin's `live_transcription/` (labels in his format) and decides which mic the demo
uses. Kevin's tools stay the authority for importing and training; this folder only adds a **reference
recording** so labels come for free instead of from 8–10 hours of hand-labeling.

## The idea in one paragraph

Every take is recorded twice at once: the **Quest mic** (Kevin's recorder app, on the drummer's head)
and a **reference mic** on the laptop, close to the kit. The laptop also plays the **guide**: Fletcher's
5-6-7-8 count-off and a click, generated from the song's `.drums.txt`. The laptop records and plays on
one audio clock, so the reference recording is locked to the score. We detect the hits on the clean
reference (no model needed: the score says which drum and roughly when), align the Quest take to the
reference by cross-correlation, and carry the hit times over. Result per take: a `labels.csv` for the
Quest audio, plus the numbers that say whether the Quest mic is good enough or the demo should use the
reference mic.

## What to record (about 75 minutes with setup)

| # | Take name (same on Quest and laptop) | Length | What the drummer does | Used for |
|---|---|---|---|---|
| 0 | `quest_smoke_01` | 1 min | Kevin's smoke take: quiet, a few soft/normal/hard hits per drum, a spoken sentence. Quest only | Gate: capture + export work. Stop here if not |
| 1 | `cal_01` | 2 min | Laptop alone: `playalong.py --calibrate` (speaker → mic offset). Then with the Quest on: 8 rim clicks on the snare, 1 per second | Clock + latency calibration |
| 2 | `pa_tight_01..03` | 3 × 60 s | Play the demo song to the guide, as tightly as possible | Timing truth, detector accuracy |
| 3 | `pa_rush_01`, `pa_drag_01` | 2 × 60 s | Same song, deliberately creep ahead / fall behind (make it obvious by bar 4) | Does Fletcher's verdict match? |
| 4 | `pa_sloppy_01` | 60 s | Skip a few notes, add a few extra hits, one fill | Misses / extras |
| 5 | `pa_fast_01` | 60 s | Same song at a faster guide (e.g. 120 bpm) | Tempo robustness |
| 6 | `pa_bleed_01` | 60 s | Like #2, while Fletcher's barks play from a speaker near the headset (simulates game audio leaking into the Quest mic) | False triggers from his voice |
| 7 | `single_<piece>_01` | 9 × 45 s | One drum per take (kick, snare, hihat, each tom, each cymbal), mixed soft/normal/hard, no click needed | Free identity labels, only if Kevin trains the classifier |
| 8 | `noise_01` | 2 min | 30 s silence, then talking, counting out loud, moving, adjusting the headset, no drums | Negatives |

Rows 2–6 are what the demo needs. Row 7 is only for the stretch goal (catching *wrong-drum* hits).

## Setup

- **Reference mic**: laptop built-in, or better a USB mic, ~1 m from the kit at snare height, pointed at
  the snare, not right under a cymbal. Check levels with a hard snare hit and a crash: no clipping.
- **Guide speaker**: the laptop speaker (or a small speaker) where the drummer can hear the click.
  The click leaking into both mics is fine (it is distinctive, its times are known, and it helps alignment).
- **Quest**: worn in playing position, normal strap, Kevin's recorder open (Library → Unknown Sources).
  Same headset settings for every take. Write player, kit and room notes in the app's fields.
- **Song**: the demo song's `.drums.txt` from Chris's converter (with its `.tempo.json` if it has tempo
  changes). Make the guide once per tempo: see commands below.

## Per take (two people: drummer + operator at the laptop)

1. Operator names the take on the Quest (e.g. `pa_tight_01`) and the drummer taps **Start new take**.
2. Operator runs `playalong.py guide.wav --session pa_tight_01`. Three loud slate clicks play, the
   drummer answers with **3 rim clicks** on the snare, then Fletcher counts "5, 6, 7, 8" and the click runs.
3. Drummer plays. After the last bar, 3 seconds of silence.
4. The laptop take stops by itself; then the drummer taps **Stop and save** on the Quest.
5. After the session: export each take's WAV **and** `.capture.json` from the Quest (Kevin's README),
   `adb pull` both to the laptop, import with Kevin's `tools/import_quest_take.py ... --session <name>`.

## From recordings to labels and numbers

```sh
python datacollection/make_guide.py song.drums.txt --bpm 94 -o datacollection/guides/song_94.wav
python datacollection/playalong.py --calibrate
python datacollection/playalong.py datacollection/guides/song_94.wav --session pa_tight_01
# after importing the Quest take into live_transcription/data/recordings/pa_tight_01/:
python datacollection/label.py score --session pa_tight_01 --take datacollection/takes/pa_tight_01
python datacollection/label.py single --session single_snare_01 --piece snare
python datacollection/evaluate.py --session pa_tight_01 --take datacollection/takes/pa_tight_01
```

`label.py` writes `labels.csv` (Kevin's `time_s,piece_id` format) into the session folder plus a short
`labels_review.csv` of the few hits it was unsure about: those are the only ones a human checks.
`evaluate.py` reports, for both mics, recall / precision / timing error per drum and whether the
rushing/dragging verdicts through Chris's matcher agree with the truth.

## Decision this data makes

- Quest-mic timing error p95 within ~15 ms and the verdicts agree: the game uses the Quest mic.
- Otherwise: the demo uses the reference (USB) mic on the laptop, which feeds the hub directly, and the
  Quest stays the display.
