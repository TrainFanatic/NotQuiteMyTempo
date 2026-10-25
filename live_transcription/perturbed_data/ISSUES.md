# Perturbed data experiment: issues and findings

## Generation issue: positive EQ clipped a recording

The first full generation attempt failed when a +3 dB shelf produced a peak of 1.1403,
outside the permitted PCM range [-1, 1]. The generator removed its staging directory,
so that failed attempt left no partial dataset. The generator now applies a fixed -6 dB
input reduction before either positive shelf boost. The manifest records this as
`headroom_db: -6`; every generated WAV is checked for clipping. The final largest
perturbed peak is 0.85198. This changes loudness as well as tone in those boosted variants.

## Held-out result

The new model uses the same six independent logistic classifiers and training procedure as
the original model. It was fit with 64 training sessions (8 original takes and 56 derived
takes). All 8 validation and 8 test takes are the same original WAV and label files for both
models, verified by SHA-256. At 10 ms tolerance:

| Split | Model | Micro precision | Micro recall | Micro F1 | TP / FP / FN |
| --- | --- | ---: | ---: | ---: | ---: |
| Validation | Original | 0.581 | 0.728 | 0.646 | 126 / 91 / 47 |
| Validation | Augmented | 0.500 | 0.769 | 0.606 | 133 / 133 / 40 |
| Test | Original | 0.457 | 0.665 | 0.541 | 105 / 125 / 53 |
| Test | Augmented | 0.430 | 0.677 | 0.526 | 107 / 142 / 51 |

The augmented model recognizes slightly more labeled events but creates more false hits.
On the test set, crash F1 improves from 0.270 to 0.400, while snare drops from 0.431 to
0.345 and ride from 0.778 to 0.744. Closed hi-hat F1 changes from 0.755 to 0.746.
At 50 ms tolerance, test micro F1 changes from 0.603 to 0.585. See `comparison.json`
for all six classes and both tolerances. The original model remains the better overall
choice on these held-out takes.

## Remaining data limitations

- Seven transformations of each training take are correlated copies of the same eight performances.
  They improve acoustic variation but provide no new hit timing, playing style, or independent
  Quest microphone behavior.
- The existing labels have not yet had a full audio audit. Missed or misplaced labels can
  distort both training and measured false-positive counts.
- The held-out sets contain only eight takes each. Per-class changes, especially for classes
  with few hits, are too small a sample to treat as general improvements.
- The transformations do not reproduce the Quest's intermittent suppression or ghost-tap
  behavior. New real headset captures are needed to learn those effects.
