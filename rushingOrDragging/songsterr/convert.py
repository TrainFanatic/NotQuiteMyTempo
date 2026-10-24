"""Part 2: turn a Songsterr track JSON into (time_s, midi) note events.

Pipeline: playback order (repeats) -> musical positions -> swing -> tempo map -> seconds.

Musical time is tracked in whole notes with Fraction so long songs don't drift; it's only
converted to float seconds at the very end.

Songsterr track JSON shape (as observed):
  measures[]: {signature?: [num, den], repeatStart?, repeat?: N, tripletFeel?, voices[]}
  voices[].beats[]: {duration: [n, d], rest?, graceNote?: "onBeat"|"beforeBeat", notes[]}
  notes[]: {fret: <GM drum number>, string, rest?, tie?, ghost?, accentuated?}
  automations.tempo[]: {measure, position, bpm, type: <beat unit, 4 = quarter>}
`duration` already accounts for dots and tuplets.
"""

import warnings
from dataclasses import dataclass
from fractions import Fraction

from .drum_map import drum_name

DEFAULT_BPM = 120
SWING_UNITS = {"8th": Fraction(1, 8), "16th": Fraction(1, 16)}


@dataclass(frozen=True)
class Note:
    time_s: float
    midi: int

    @property
    def name(self):
        return drum_name(self.midi)


def playback_order(measures):
    """Expand repeats into the list of written-measure indices in the order they're played.

    `repeatStart: true` opens a section, and `repeat: N` on the closing measure means the section
    is played N times in total (a single measure can carry both). Alternate endings use the
    `alternateEnding` key (list of passes, or a single int), which hasn't been seen in real data
    yet, so treat it as best effort.
    """
    order = []
    i, start, current_pass = 0, 0, 1
    jumped_back = False
    reset_pending = False  # a section just closed; its pass count still applies to trailing endings
    while i < len(measures):
        m = measures[i]
        endings = m.get("alternateEnding")
        if isinstance(endings, int):
            endings = [endings]
        if m.get("repeatStart") and not jumped_back:
            start, current_pass, reset_pending = i, 1, False
        elif reset_pending and not endings:
            current_pass, reset_pending = 1, False
        jumped_back = False

        if endings and current_pass not in endings:
            i += 1
            continue
        order.append(i)
        times = m.get("repeat")
        if times and current_pass < times:
            current_pass += 1
            i, jumped_back = start, True
            continue
        if times:
            start, reset_pending = i + 1, True
        i += 1
    return order


def _swing(offset, unit):
    """Warp a position within a measure so straight `unit` pairs are played long-short (2:1)."""
    window = 2 * unit
    base = (offset // window) * window
    r = offset - base
    long_part = window * Fraction(2, 3)
    if r <= unit:
        return base + r * long_part / unit
    return base + long_part + (r - unit) * (window - long_part) / unit


def _beat_offsets(beats):
    """Yields (offset_within_voice, beat). Grace notes take no time in the measure."""
    t = Fraction(0)
    for b in beats:
        dur = Fraction(*b["duration"])
        grace = b.get("graceNote")
        yield (t - dur if grace == "beforeBeat" else t), b
        if not grace:
            t += dur


def extract_events(track):
    """Returns sorted (musical_pos, midi) events plus tempo changes as (musical_pos, sec_per_whole)."""
    measures = track["measures"]
    tempo_by_measure = {}
    for a in track.get("automations", {}).get("tempo", []):
        tempo_by_measure.setdefault(a["measure"], []).append(a)

    # Signature and triplet feel carry forward in *written* order, so resolve them up front.
    signatures, feels = [], []
    sig, feel = [4, 4], None
    for m in measures:
        sig = m.get("signature", sig)
        if "tripletFeel" in m:
            feel = m["tripletFeel"]
            if feel and feel not in SWING_UNITS and feel not in ("off", "none"):
                warnings.warn(f"Unsupported tripletFeel {feel!r}; playing straight")
        signatures.append(Fraction(sig[0], sig[1]))
        feels.append(SWING_UNITS.get(feel))

    events, tempos = [], []
    pos = Fraction(0)
    for idx in playback_order(measures):
        m = measures[idx]
        length = signatures[idx]
        swing_unit = feels[idx]

        for a in tempo_by_measure.get(idx, []):
            offset = _tempo_offset(m, a.get("position", 0))
            tempos.append((pos + offset, Fraction(60 * a.get("type", 4)) / Fraction(str(a["bpm"]))))

        for voice in m.get("voices", []):
            for offset, beat in _beat_offsets(voice.get("beats", [])):
                if beat.get("rest"):
                    continue
                if swing_unit is not None and not beat.get("graceNote"):
                    offset = _swing(offset, swing_unit)
                for n in beat.get("notes", []):
                    if n.get("rest") or n.get("tie"):
                        continue
                    events.append((pos + offset, int(n["fret"])))
        pos += length

    if not tempos or tempos[0][0] > 0:
        tempos.insert(0, (Fraction(0), Fraction(240, DEFAULT_BPM)))
    tempos.sort(key=lambda x: x[0])
    events.sort()
    return events, tempos


def _tempo_offset(measure, position):
    """Tempo `position` is taken to be a beat index in the first voice (only 0 seen so far)."""
    if not position:
        return Fraction(0)
    beats = measure["voices"][0]["beats"]
    return sum((Fraction(*b["duration"]) for b in beats[:position] if not b.get("graceNote")), Fraction(0))


def to_seconds(events, tempos):
    """Integrate the piecewise-constant tempo map to convert musical positions to seconds."""
    out = []
    ti = 0
    seg_pos, seg_sec, spw = tempos[0][0], Fraction(0), tempos[0][1]
    for pos, midi in events:
        while ti + 1 < len(tempos) and tempos[ti + 1][0] <= pos:
            ti += 1
            seg_sec += (tempos[ti][0] - seg_pos) * spw
            seg_pos, spw = tempos[ti][0], tempos[ti][1]
        out.append(Note(float(seg_sec + (pos - seg_pos) * spw), midi))
    return out


def convert(track):
    """Track JSON -> list[Note], sorted by time then MIDI number."""
    events, tempos = extract_events(track)
    notes = to_seconds(events, tempos)
    return sorted(notes, key=lambda n: (n.time_s, n.midi))


def format_notes(notes, header_lines=()):
    lines = [f"# {h}" for h in header_lines]
    lines.append("# time_s  midi  name")
    lines += [f"{n.time_s:.6f}  {n.midi}  {n.name}" for n in notes]
    return "\n".join(lines) + "\n"
