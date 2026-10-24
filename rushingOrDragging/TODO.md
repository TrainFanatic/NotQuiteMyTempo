# TODO

## Tonal instruments (guitar, bass, keys, vocals)
- Pick tracks by `instrumentId` (not 1024) instead of only the drum track; let the CLI choose a track by index or name.
- Convert string+fret to pitch: `midi = tuning[string] + fret` (+ capo if present). Songsterr's track JSON has `tuning`. Check whether `string` is 0-based from the high string.
- Output format for pitched notes: `time_s  midi  pitch_name  string  fret  duration_s`
- Ties: a tied note extends the previous note's duration instead of starting a new note (drums currently just skip ties).
- Bends, slides, hammer-ons, vibrato, let-ring, palm mute: drop these, or keep them as an optional extra column.

## Timing / notation
- Note duration/sustain column (also useful for drums: open hi-hat, crash ring).
- Velocity: beat `velocity` (f, mf, p…), `gradualVelocity`, note `accentuated`, `ghost`.
- Alternate endings: `alternateEnding` key name is a guess. Verify against a real tab with voltas.
- D.S. / D.C. / coda / fine navigation markers.
- Tempo automation `position` other than 0 (currently read as a beat index in voice 0). Also linear tempo ramps (`linear: true`).
- Triplet feels other than `8th` and `16th` (e.g. dotted / scottish variants).
- Grace notes: currently zero-length (`beforeBeat` placed just before the beat, `onBeat` on the beat).

## Other
- Export all tracks at once; MIDI export (e.g. with `mido`) to check by ear against the song.
- Other sources: Guitar Pro files via `PyGuitarPro`, MusicXML via `music21`.
