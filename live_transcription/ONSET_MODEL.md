# Hit/no-hit model: `quest2_onset_v1`

Updated 2026-10-25. Desktop replay of the 24 `single_*` Quest takes, not a headset measurement.

## What changed

- `models/quest2_onset_v1.bin` is one logistic classifier on the existing 256 native features.
  A candidate is a hit when any drum was labelled there. Output is MIDI 0 `onset`. The native
  runtime is unchanged, since the model format already allows one class.
- Training matches `quest2_v1` except for the merged target. It uses the same 8/8/8 session split
  in `config/kit.local.json`, the Quest mute filter (240 ms, -75 dB, 35 dB drop), nearest
  candidate within 20 ms, and the same C grid and thresholds selected on validation only.
  Selected C 0.1, threshold 0.65 (validation candidate F1 0.88).
- The six-class model is untouched. It is copied byte for byte to
  `models/backup/quest2_v1_six_class.bin` (SHA-256 `9b6f3f5f...ba894`). Before comparing, it was
  replayed on these recordings and reproduced the handoff exactly (validation 126/91/47, test
  105/125/53 TP/FP/FN at 10 ms).

## Results on the held-out takes

Hit/no-hit, micro totals over the same original validation and test takes. A six-class event of
any family counts as a hit. "Onsets only" is today's model-free `--onsets-only` path.

| Split | Tolerance | Model | Precision | Recall | F1 | TP / FP / FN |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| Validation | 10 ms | Six-class `quest2_v1` | 0.780 | 0.757 | 0.768 | 131 / 37 / 42 |
| Validation | 10 ms | Onset `quest2_onset_v1` | 0.798 | 0.798 | 0.798 | 138 / 35 / 35 |
| Validation | 10 ms | Onsets only | 0.401 | 0.850 | 0.544 | 147 / 220 / 26 |
| Validation | 50 ms | Six-class | 0.839 | 0.815 | 0.827 | 141 / 27 / 32 |
| Validation | 50 ms | Onset | 0.867 | 0.867 | 0.867 | 150 / 23 / 23 |
| Validation | 50 ms | Onsets only | 0.436 | 0.925 | 0.593 | 160 / 207 / 13 |
| Test | 10 ms | Six-class | 0.701 | 0.728 | 0.714 | 115 / 49 / 43 |
| Test | 10 ms | Onset | 0.752 | 0.747 | 0.749 | 118 / 39 / 40 |
| Test | 10 ms | Onsets only | 0.399 | 0.797 | 0.532 | 126 / 190 / 32 |
| Test | 50 ms | Six-class | 0.774 | 0.804 | 0.789 | 127 / 37 / 31 |
| Test | 50 ms | Onset | 0.841 | 0.835 | 0.838 | 132 / 25 / 26 |
| Test | 50 ms | Onsets only | 0.446 | 0.892 | 0.595 | 141 / 175 / 17 |

The onset model is better than the six-class model at deciding whether a hit happened. On test
it removes 10 false hits and finds 3 more real ones at 10 ms. It ties or wins on 12 of 16
held-out takes. Its losses are one or two hits each (`single_kick_02`, `single_high_tom_03`,
`single_ride_03`). These numbers are not comparable with the six-class micro F1 of 0.541, which
also requires the right family.

Onsets only has the highest recall, because both models filter its candidates and cannot add
hits it never proposed. Its precision is about 0.40, so most of its output is false hits. The
onset model keeps most of that recall at about twice the precision.

Per-session results are in `reports/hit_detection_comparison.json`. The model's own report is
`reports/quest2_onset_v1.json`.

## Label audit

`tools/audit_labels.py` (output `reports/label_audit.json`) compared every label with the
mute-filtered candidates. It found problems that affect both models equally and limit what
these numbers can show. Nothing in `labels.csv` was edited, because fixing labels needs
someone to listen to the audio.

| Split | Hit times | Labels with no candidate within 20 ms | Loud unlabelled candidates |
| --- | ---: | ---: | ---: |
| Train | 272 | 26 | 14 |
| Validation | 173 | 21 | 58 |
| Test | 158 | 26 | 49 |

What the flagged items look like:

1. **Labels on the Quest re-attack instead of the hit.** 15 labels (9 validation, 6 test) sit 138
   to 236 ms after a loud unlabelled candidate, most of them 180 to 236 ms. The mute filter drops
   the labelled candidate as a phantom. This matches the Quest mute pattern, so the real attack is
   probably the earlier, unlabelled one. Examples are `single_hihat_02` at 2.602 s, 3.995 s and 6.782 s, and
   `single_kick_03` at 23.218 s and 34.553 s. Each one counts as a miss plus a false hit.
2. **Labels about 20 to 45 ms early or late.** Some loud labels have a candidate 21 to 45 ms away,
   for example `single_kick_03` at 6.569 s and 9.963 s, and `single_mid_tom_02` at 14.953 s. They
   are misses at 10 ms and hits at 50 ms. This is most of the 10 to 50 ms gap on `single_kick_03`.
3. **Quiet labelled hits the detector never proposes.** Labels 25 to 47 dB below the take's
   typical hit with no candidate nearby, for example seven soft kicks in `single_kick_01` from
   10.8 to 16.2 s. No classifier can recover these. A lower detector threshold would be needed.
4. **Loud unlabelled sounds.** Candidates at least as loud as labelled hits with no label, often
   in clusters 25 to 50 ms apart, for example `single_kick_02` at 5.299 s, 9.903 s and 26.825 s,
   and many in `single_floor_tom_03` and `single_mid_tom_03`. These are either missed labels or
   real non-drum sounds. The existing `labels_review.csv` files record that quiet sounds and
   sounds 230 to 470 ms before a hit were deliberately left out. Listening is the only way to
   tell.

The two weakest held-out takes for both models, `single_kick_03` and `single_mid_tom_03`, are the
ones with the most flagged labels. Correcting items 1 and 2 would likely raise both models'
scores at 10 ms. It would not change which one is better on hit/no-hit.

## Limitations

- It counts any drum as a hit. It cannot tell a snare from a kick, so on its own it cannot say
  whether the player hit the expected drum. `hub/bridge.py` assigns the expected drum to
  `onset` lines, the same as it does for `--onsets-only`.
- Every take has one drum only. There are no grooves, simultaneous hits or backing-track bleed,
  so the result may not hold for real playing.
- Eight test takes and 158 hits. A difference of a few hits per take is within what a different
  set of takes could change.
- The labels have the problems above, and the model learned from them too.
- These are desktop replays, not timing or latency measured on the Quest.

## Files

| File | In Git | Purpose |
| --- | --- | --- |
| `tools/train_onset_model.py` | yes | Trainer |
| `tools/compare_hit_detection.py` | yes | Hit/no-hit comparison of any models |
| `tools/audit_labels.py` | yes | Label audit |
| `tests/test_onset_model.py` | yes | Training, export, comparison and replay tests |
| `models/quest2_onset_v1.bin`, `.json` | force-added | Onset model and metadata |
| `models/backup/quest2_v1_six_class.bin` | force-added | Backup of the six-class model |
| `reports/quest2_onset_v1.json` | force-added | Onset model report |
| `reports/hit_detection_comparison.json` | force-added | Comparison above, per session |
| `reports/label_audit.json` | force-added | Audit above, per label |
| `data/recordings/`, `config/kit.local.json` | no | Local only (mic audio); needed to retrain |
