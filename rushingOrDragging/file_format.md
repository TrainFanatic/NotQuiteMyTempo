# File format: `.drums.txt` (timestamped drum note list)

This document is a complete, self-contained specification of the `.drums.txt` format. It is
written so that a program or an LLM can **read**, **validate**, and **generate** these files
correctly without any other context.

## 1. Purpose

A `.drums.txt` file lists every drum hit in a song as a point in time. Each hit is
`(time in seconds, General MIDI percussion number, human-readable name)`. It contains no
durations, velocities, bars, beats, or tempo information. All of that has already been resolved
into absolute time. Simultaneous hits, such as kick plus crash, appear as separate lines with the
same timestamp.

Files are produced from Songsterr drum tabs. Repeats have been expanded, swing/triplet feel has
been applied, and tempo changes have been integrated, so the timestamps are the real playback
times in seconds from the start of the song.

## 2. Encoding and lines

- Plain text, UTF-8. In practice the content is ASCII, except that header comments may contain
  non-ASCII song or artist names.
- Line endings are `\n` (LF). The file ends with a single trailing `\n`.
- There are two kinds of lines: **comment lines** and **data lines**. Writers never emit blank
  lines. Readers should ignore them.

## 3. Comment / header lines

- A comment line starts with `#` as the first character, followed by a single space and then
  free text.
- All comments appear at the top of the file, before any data line.
- The **last** comment line is always exactly the column header:
  ```
  # time_s  midi  name
  ```
- The lines above the column header are informational metadata in one of two layouts:

  **(a) Imported from Songsterr** (the normal case):
  ```
  # song: <Title> - <Artist>
  # songId=<int> revisionId=<int> track=<int> (<track name, may be empty>)
  # time_s  midi  name
  ```
  `track` is the zero-based index of the track within the Songsterr song. The track name in
  parentheses is free text and may be empty, for example `()`.

  **(b) Converted from a local track JSON:**
  ```
  # source: <path to json>  track=<track name or ?>
  # time_s  midi  name
  ```
- Readers must not depend on the metadata lines for parsing. Treat them as optional free text.

## 4. Data lines

Each data line describes exactly one drum hit:

```
<time_s>␠␠<midi>␠␠<name>
```

The three fields are separated by **exactly two ASCII spaces** (`"  "`). Leading and trailing
whitespace is not allowed.

| Field    | Type    | Format                                                         | Meaning |
|----------|---------|----------------------------------------------------------------|---------|
| `time_s` | float   | Non-negative decimal with **exactly 6 digits after the point** (`%.6f`), e.g. `0.000000`, `13.563830` | Onset time in seconds from the start of the song (the first measure's downbeat is `0`). |
| `midi`   | integer | Decimal, no sign, no leading zeros                             | General MIDI percussion note number (channel 10), plus Guitar Pro extensions 91–98 (see §6). |
| `name`   | string  | `[a-z0-9_]+` (lowercase snake_case)                            | Name of the drum sound, derived solely from `midi` using the table in §6. `unknown` if the number is not in the table. |

Validation regex for a data line:

```
^\d+\.\d{6}  \d+  [a-z0-9_]+$
```

The `name` field is redundant: it is always the table lookup of `midi`. If the two ever disagree,
`midi` is authoritative.

## 5. Ordering and semantics

- Data lines are sorted ascending by `time_s`, then ascending by `midi` to break ties.
- Duplicate `time_s` values are normal and mean the hits are simultaneous.
- An identical `(time_s, midi)` pair can occur more than once if the source tab had the same
  drum in two voices at the same instant. Consumers may deduplicate.
- The first hit is not necessarily at `0.000000`. Songs that start with drum rests begin later
  (for example, `7.142857`).
- Notes have no duration. Each line is an instantaneous onset. Tied notes in the source are
  dropped (they are continuations, not new hits), and rests produce no lines.
- Grace notes are emitted as ordinary zero-length hits. A "before beat" grace note is placed
  slightly before the main beat it decorates.
- There is no velocity, accent, or ghost-note information. All hits are equal.
- The file contains only drum hits, with no tempo, time-signature, or bar markers. Tempo can only
  be inferred from the spacing of the hits.
- Timestamps are rounded to microseconds. Times are computed exactly from rational musical
  positions, so there is no cumulative drift, but the printed values are rounded.

## 6. MIDI number → name table (complete)

Standard General MIDI percussion (27–87):

| midi | name | midi | name | midi | name |
|---:|---|---:|---|---:|---|
| 27 | high_q | 48 | tom_high_mid | 69 | cabasa |
| 28 | slap | 49 | crash | 70 | maracas |
| 29 | scratch_push | 50 | tom_high | 71 | whistle_short |
| 30 | scratch_pull | 51 | ride | 72 | whistle_long |
| 31 | sticks | 52 | china | 73 | guiro_short |
| 32 | square_click | 53 | ride_bell | 74 | guiro_long |
| 33 | metronome_click | 54 | tambourine | 75 | claves |
| 34 | metronome_bell | 55 | splash | 76 | wood_block_high |
| 35 | kick_acoustic | 56 | cowbell | 77 | wood_block_low |
| 36 | kick | 57 | crash_2 | 78 | cuica_mute |
| 37 | side_stick | 58 | vibraslap | 79 | cuica_open |
| 38 | snare | 59 | ride_2 | 80 | triangle_mute |
| 39 | hand_clap | 60 | bongo_high | 81 | triangle_open |
| 40 | snare_electric | 61 | bongo_low | 82 | shaker |
| 41 | tom_floor_low | 62 | conga_high_mute | 83 | jingle_bell |
| 42 | hihat_closed | 63 | conga_high_open | 84 | bell_tree |
| 43 | tom_floor_high | 64 | conga_low | 85 | castanets |
| 44 | hihat_pedal | 65 | timbale_high | 86 | surdo_mute |
| 45 | tom_low | 66 | timbale_low | 87 | surdo_open |
| 46 | hihat_open | 67 | agogo_high | | |
| 47 | tom_low_mid | 68 | agogo_low | | |

Guitar Pro extended articulations (not part of General MIDI; used by Songsterr):

| midi | name | closest GM sound |
|---:|---|---|
| 91 | snare_rimshot | 38 snare |
| 92 | hihat_half | 46 hihat_open |
| 93 | ride_edge | 51 ride |
| 94 | ride_choke | 51 ride |
| 95 | splash_choke | 55 splash |
| 96 | china_choke | 52 china |
| 97 | crash_choke | 49 crash |
| 98 | crash_2_choke | 57 crash_2 |

"Choke" means the cymbal is hit and then grabbed, so it stops ringing quickly. Any other number
(for example 88–90, or anything below 27 or above 98) is written with the name `unknown`.

In real rock and metal files, the most common values are 36 kick, 38 snare, 42 hihat_closed,
46 hihat_open, 44 hihat_pedal, 49/57 crashes, 51 ride, and the toms 41/43/45/47/48/50.

## 7. Example

```
# song: Back In Black - AC/DC
# songId=1024 revisionId=9880696 track=6 (Phil Rudd | Sonor Phonic Series | Drums)
# time_s  midi  name
0.000000  42  hihat_closed
0.638298  42  hihat_closed
1.276596  42  hihat_closed
5.106383  36  kick
5.106383  57  crash_2
5.425532  42  hihat_closed
5.744681  38  snare
5.744681  42  hihat_closed
13.404255  38  snare
13.563830  36  kick
13.563830  46  hihat_open
```

Reading the example: at 5.106383 s a kick and a second crash are hit together. At 5.744681 s a
snare and a closed hi-hat are hit together.

## 8. Reference implementations

**Parse** (tolerant: ignores comments and blank lines, splits on any whitespace):

```python
def read_notes(path):
    notes = []
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        t, midi, *rest = line.split()
        notes.append((float(t), int(midi), rest[0] if rest else None))
    return notes
```

**Write** (canonical form):

```python
def write_notes(path, notes, header_lines=()):
    # notes: iterable of (time_s: float, midi: int); name is looked up from the §6 table
    lines = [f"# {h}" for h in header_lines]
    lines.append("# time_s  midi  name")
    for t, m in sorted(notes):
        lines.append(f"{t:.6f}  {m}  {GM_DRUMS.get(m, 'unknown')}")
    open(path, "w", encoding="utf-8").write("\n".join(lines) + "\n")
```

## 9. Rules summary for generators (checklist)

1. Optional `# ...` metadata lines, then exactly `# time_s  midi  name`.
2. One hit per line: `%.6f`, two spaces, integer MIDI number, two spaces, name from §6.
3. Sort by time, then by MIDI number. Use separate lines for simultaneous hits.
4. Times are in seconds, non-negative, and measured from the song start.
5. No blank lines, no trailing spaces, and a single final newline.
6. Do not add extra columns. Durations, velocities, and similar fields are not part of this format.
