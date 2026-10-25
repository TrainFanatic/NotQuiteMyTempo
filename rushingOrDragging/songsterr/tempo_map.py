"""Tempo/meter sidecar for a converted track: `<stem>.tempo.json` next to `<stem>.drums.txt`.

`.drums.txt` carries only note times. This module exports the bar grid and tempo map that
`convert.py` uses to produce those times, so consumers can recover bar numbers, beats and bpm.

Times use the same origin and the same exact (Fraction) arithmetic as `convert.convert()`:
bar 1's downbeat is 0.0 s, repeats are expanded with `convert.playback_order`, and tempo
changes are integrated exactly before the single final float conversion. So a note written on a
downbeat has exactly the same float time as that bar's `start_s`.

Schema (format "tempo-map/1"):
  source         songId / revisionId / track / name, whichever are known
  time_origin    human-readable statement of the time origin
  bars[]         one row per *played* bar, in playback order:
                   bar             1-based playback index
                   source_measure  0-based index of the written measure in the track JSON
                   start_s         time of the downbeat (s)
                   pos_whole       musical position of the downbeat, in whole notes from 0
                   numerator, denominator
                   bpm             quarter-note bpm in force at the downbeat
                   beat_s          seconds per 1/denominator note at the downbeat
                                   (= 60/bpm for x/4; half that for x/8)
                   bar_s           duration of the bar (s), including any mid-bar tempo change
  tempo_changes[] in time order, duplicates at one position collapsed (last wins, as convert does):
                   time_s, pos_whole, bpm (quarter-note), written_bpm, written_unit (4 = quarter),
                   linear (copied from the JSON; NOTE convert.py treats every change as a step)
  end_s          end of the last played bar
"""

import json
from bisect import bisect_right
from fractions import Fraction
from pathlib import Path

from .convert import DEFAULT_BPM, _tempo_offset, playback_order

FORMAT = "tempo-map/1"
TIME_ORIGIN = "first downbeat of bar 1 = 0.0 s (same origin as the .drums.txt)"
EPS = 1e-6  # .drums.txt times are rounded to microseconds


def written_signatures(measures):
    """Per written measure, (num, den), carried forward in written order (as extract_events does)."""
    out, sig = [], [4, 4]
    for m in measures:
        sig = m.get("signature", sig)
        out.append((int(sig[0]), int(sig[1])))
    return out


def exact_tempo_changes(track):
    """[(pos_whole: Fraction, sec_per_whole: Fraction, automation_or_None)] in playback order.

    Mirrors extract_events(): same positions, same sec/whole, same default insertion and sort.
    """
    measures = track["measures"]
    sigs = written_signatures(measures)
    by_measure = {}
    for a in track.get("automations", {}).get("tempo", []):
        by_measure.setdefault(a["measure"], []).append(a)
    out, pos = [], Fraction(0)
    for idx in playback_order(measures):
        for a in by_measure.get(idx, []):
            off = _tempo_offset(measures[idx], a.get("position", 0))
            out.append((pos + off, Fraction(60 * a.get("type", 4)) / Fraction(str(a["bpm"])), a))
        pos += Fraction(*sigs[idx])
    if not out or out[0][0] > 0:
        out.insert(0, (Fraction(0), Fraction(240, DEFAULT_BPM), None))
    out.sort(key=lambda x: x[0])
    return out


class _Clock:
    """Exact musical position -> seconds, using convert.to_seconds' rule (last change at a pos wins)."""

    def __init__(self, changes):
        self.segs = []  # (pos, sec, spw, automation)
        for pos, spw, a in changes:
            if self.segs and self.segs[-1][0] == pos:
                self.segs[-1] = (pos, self.segs[-1][1], spw, a)
                continue
            sec = Fraction(0) if not self.segs else self.segs[-1][1] + (pos - self.segs[-1][0]) * self.segs[-1][2]
            self.segs.append((pos, sec, spw, a))
        self._pos = [s[0] for s in self.segs]

    def seg(self, pos):
        return self.segs[max(bisect_right(self._pos, pos) - 1, 0)]

    def seconds(self, pos):
        p0, s0, spw, _ = self.seg(pos)
        return s0 + (pos - p0) * spw


def extract_tempo_map(track, source=None):
    """Track JSON -> tempo-map dict (a TempoMap, i.e. a dict with cached bisect arrays)."""
    measures = track["measures"]
    sigs = written_signatures(measures)
    clock = _Clock(exact_tempo_changes(track))

    if source is None:
        source = {k: track[k] for k in ("songId", "revisionId") if k in track}
        if "name" in track:
            source["name"] = track["name"]

    bars, pos = [], Fraction(0)
    for n, idx in enumerate(playback_order(measures), start=1):
        num, den = sigs[idx]
        spw = clock.seg(pos)[2]
        start, end = clock.seconds(pos), clock.seconds(pos + Fraction(num, den))
        bars.append({
            "bar": n, "source_measure": idx, "start_s": float(start), "pos_whole": float(pos),
            "numerator": num, "denominator": den,
            "bpm": float(240 / spw), "beat_s": float(spw / den), "bar_s": float(end - start),
        })
        pos += Fraction(num, den)

    changes = [{
        "time_s": float(s), "pos_whole": float(p), "bpm": float(240 / spw),
        "written_bpm": a["bpm"] if a else DEFAULT_BPM, "written_unit": a.get("type", 4) if a else 4,
        "linear": bool(a.get("linear", False)) if a else False,
    } for p, s, spw, a in clock.segs]

    return TempoMap({
        "format": FORMAT, "source": source, "time_origin": TIME_ORIGIN,
        "bars": bars, "tempo_changes": changes, "end_s": float(clock.seconds(pos)),
    })


class TempoMap(dict):
    """A plain JSON dict plus lazily built sorted arrays so bar_at is O(log n)."""

    _cache = None

    def _arrays(self):
        if self._cache is None:
            self._cache = ([b["start_s"] for b in self["bars"]], [c["time_s"] for c in self["tempo_changes"]])
        return self._cache


def _as_map(tm):
    return tm if isinstance(tm, TempoMap) else TempoMap(tm)


def sidecar_path(drums_path):
    """out/song.drums.txt -> out/song.tempo.json (any other suffix is just replaced)."""
    p = Path(drums_path)
    name = p.name[: -len(".drums.txt")] if p.name.endswith(".drums.txt") else p.stem
    return p.with_name(name + ".tempo.json")


def write_tempo_map(tempo_map_or_track, path, source=None):
    """Write the sidecar. `path` may be the .drums.txt path (sidecar name is derived) or a .json path.

    Accepts either a tempo map or a raw track JSON (converted with extract_tempo_map). Returns the path.
    """
    tm = tempo_map_or_track
    if "measures" in tm:
        tm = extract_tempo_map(tm, source=source)
    path = Path(path)
    if not path.name.endswith(".json"):
        path = sidecar_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(tm, indent=1) + "\n", encoding="utf-8")
    return path


def load_tempo_map(path):
    tm = TempoMap(json.loads(Path(path).read_text(encoding="utf-8")))
    if tm.get("format") != FORMAT:
        raise ValueError(f"{path}: not a {FORMAT} file (format={tm.get('format')!r})")
    return tm


def bar_at(tempo_map, t_s, eps=EPS):
    """Time (s) -> (bar, beat) with bar 1-based and beat 0-based in 1/denominator units.

    A time within `eps` of a downbeat counts as that downbeat (exactly beat 0.0), so microsecond
    rounding in .drums.txt never pushes a downbeat note into the previous bar. Times before 0
    (beforeBeat grace notes) give bar 1 with a negative beat; times past end_s stay in the last bar.
    """
    tm = _as_map(tempo_map)
    starts, ttimes = tm._arrays()
    i = max(bisect_right(starts, t_s + eps) - 1, 0)
    b = tm["bars"][i]
    if abs(t_s - b["start_s"]) <= eps:
        return b["bar"], 0.0
    c = tm["tempo_changes"][max(bisect_right(ttimes, t_s) - 1, 0)]
    pos = c["pos_whole"] + (t_s - c["time_s"]) * c["bpm"] / 240
    return b["bar"], (pos - b["pos_whole"]) * b["denominator"]


def bar_start(tempo_map, bar):
    """1-based playback bar -> downbeat time (s). bar = len(bars)+1 gives end_s."""
    bars = tempo_map["bars"]
    if bar == len(bars) + 1:
        return tempo_map["end_s"]
    if not 1 <= bar <= len(bars):
        raise IndexError(f"bar {bar} out of range 1..{len(bars) + 1}")
    return bars[bar - 1]["start_s"]
