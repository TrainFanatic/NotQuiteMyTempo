# Guide track + reference recording (make_guide.py, playalong.py)

The Quest records the kit through the headset mic. While it does, the laptop plays a guide track
(count-off + click) and records a reference mic **in the same duplex stream**. The reference is
therefore sample-locked to the guide and to the score. Align the Quest take to it afterwards.

Python: `.venv/bin/python` (needs numpy, scipy, soundfile, sounddevice). Tests:
`.venv/bin/python -m pytest -q datacollection/tests`.

## 1. Make a guide: `make_guide.py`

```sh
.venv/bin/python datacollection/make_guide.py out/bib.drums.txt --bpm 94 -o datacollection/guides/bib94.wav
#   [--tempo SHEET.tempo.json] [--bars N] [--loops K] [--click-every beat|half]
#   [--style sharp|menacing|shouting|build] [--countoff-clicks] [--drums [--drums-gain 0.25]]
```

- **Layout** (48 kHz mono float32): 2 s silence, then Fletcher's count-off
  (`fletcher/countoff.py`). In 4/4 that is "five six seven eight" on beats −4..−1, and its buffer
  ends exactly at **t0** = the downbeat of bar 1. After that comes a click on every beat (or every half
  beat): 15 ms, 4 kHz accent on beat 1, 2 kHz otherwise. Each click begins at full amplitude on
  sample `t0_sample + round(t·sr)`.
- **Tempo map.** The script looks in this order: `--tempo`, then `<stem>.tempo.json` next to the
  sheet, then a map derived from the track JSON named in the sheet's header (`# source:` or
  `songId/revisionId/track` → `rushingOrDragging/data/raw`). With a map, the click follows the
  sheet's bars and meters. The whole map is scaled by `--bpm / bpm of bar 1`, so `--bpm` defaults to the
  sheet's own tempo. Without a map, the script assumes `--sheet-bpm` (default `--bpm`) and
  `--beats-per-bar` 4.
- `--drums` mixes the sheet in quietly with Chris's sample renderer. It needs his kit in
  `rushingOrDragging/samples/`, which you get by running `test_file_with_audio.py` once. If the kit
  is missing, the drums are skipped with a warning; the script never downloads it.
- **`guide.json`** (same stem as the WAV) holds `t0_sample`, `t0_s`, `sample_rate`, `bpm`, `sheet_bpm`,
  `time_scale`, `beats_per_bar`, `sheet`, `tempo_map`, `sheet_bars`, `sheet_loops`, `loop_len_s`,
  `click_times_s`, `click_samples`, `click_kinds`, `click_bar_beat` (`[loop, bar, beat]`),
  `countoff_times_s` and `countoff_clips`, `lead_s`, `drums_rendered` and `duration_s`. Every
  `*_s` is in guide-file seconds.

## 2. Record: `playalong.py`

```sh
.venv/bin/python datacollection/playalong.py --list-devices
.venv/bin/python datacollection/playalong.py --calibrate            # once per room/setup
.venv/bin/python datacollection/playalong.py datacollection/guides/bib94.wav --session alice_bib94_01 \
    [--input-device ID] [--output-device ID] [--channels N] [--out datacollection/takes]
```

- **What plays:** 1 s of silence, then **3 slate clicks** 0.5 s apart (loud noise bursts, for
  cross-correlation), then a 3 s window where the drummer **taps 3 rim clicks on the snare**, then
  the guide. The script prints instructions, a big 3-2-1 countdown, and the current phase and bar.
  Start the Quest recorder before the countdown ends.
- **What it writes:** `takes/<session>/ref.wav` (float32, at the stream rate, all input channels) and
  `take.json`. The JSON holds the full `guide` dict and `guide_offset_s/frames` (where guide sample 0
  sits in ref.wav). It also has `t0_ref_s`, `click_times_ref_s`, `countoff_times_ref_s`,
  `slate_times_s/frames` and `tap_window_s`, all *scheduled* times on ref.wav's clock. Then it records
  the device names and channels, PortAudio's `latency_s`, the first callback's ADC/DAC times,
  `start_unix`, `frames`, `status_flags_count` + `status_flags` (xruns) and `interrupted`, plus a copy
  of `takes/calibration.json` if it exists.
- **Ctrl-C** aborts the stream and saves what was recorded, with `interrupted: true`. An existing
  session directory is never overwritten.
- **Clock:** ref.wav frame n was captured in the callback that sent output frame n, so a scheduled
  sound appears at about `n + calibration.median_s·sr`. The MacBook mic and speakers share a clock.
  For a USB mic on a different output device, use an Aggregate Device (drift correction on).
- **`--calibrate`** plays 10 slates and detects each one in the recording: a high-passed envelope
  crossing 30 % of its peak, which must be at least 6× the pre-roll noise. It reports the onset
  minus the scheduled time as a median, std and range, and writes `takes/calibration.json`. If
  fewer than 5 clicks are detected it writes `calibration.failed.json` instead, so a bad run never
  replaces a good calibration.
