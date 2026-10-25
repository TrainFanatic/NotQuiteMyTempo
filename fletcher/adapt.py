"""The ONLY place that knows how teammates' real messages differ from CONTRACT.md.

The referee's code has drifted since the plan was written. Instead of
sprinkling special cases through the brain, map whatever actually arrives
onto the contract shapes here. To update it: open the newest
recordings/*.jsonl, look at what Chris really sends, and edit the tables.

normalize() returns a contract-shaped message, or None to drop it.

What Chris's referee really does (rushingOrDragging/live_compare.py, 2026-10-24):
it publishes nothing to a hub. It reads `time_s midi name` lines and prints one
line per verdict change (--plain). Everything in it is SECONDS, positive = late
(same sign as the contract), song time from the start of the sheet, no bars.
parse_live_compare_line() turns that printed line into a judgment.note.
"""

from __future__ import annotations

import re
from typing import Any

# Other names a teammate might use for a type -> contract type.
TYPE_ALIASES: dict[str, str] = {
    "note": "judgment.note",
    "judgement.note": "judgment.note",
    "hit": "judgment.note",
    "timing": "judgment.note",
    "bar": "judgment.bar",
    "judgement.bar": "judgment.bar",
    "alert": "judgment.alert",
    "judgement.alert": "judgment.alert",
    "state": "session.state",
}

# Per contract type: other field names -> contract field name.
FIELD_ALIASES: dict[str, dict[str, str]] = {
    "judgment.note": {
        "error": "error_ms",
        "offset_ms": "error_ms",
        "delta_ms": "error_ms",
        "timing_ms": "error_ms",
        "instrument": "drum",
        "piece": "drum",
        "measure": "bar",
        "rating": "grade",
    },
    "judgment.bar": {
        "measure": "bar",
        "ratio": "tempo_ratio",
        "mean_error": "mean_error_ms",
        "avg_error_ms": "mean_error_ms",
    },
    "judgment.alert": {
        "type": "kind",
        "level": "severity",
        "start_bar": "since_bar",
        "message": "detail",
    },
    "session.state": {
        "status": "state",
        "tempo": "bpm",
        "measure": "bar",
    },
}

# Chris's own names for a hit's error; his code measures in seconds.
SECONDS_FIELDS = ("err", "error_s", "median")

# Some referees send early/late as a label instead of a signed number.
EARLY_WORDS = {"early", "rushing", "rushed", "ahead"}
LATE_WORDS = {"late", "dragging", "dragged", "behind"}
ON_TIME_WORDS = {"perfect", "on time", "on_time"}

# live_compare.py PlainDisplay: "     0.673s  DRAGGING!  (median +35.1 ms over 1 hit(s))"
LIVE_COMPARE_LINE = re.compile(
    r"^\s*(?P<t>[\d.]+)s\s+(?P<word>PERFECT|RUSHING|DRAGGING)!\s+"
    r"\(median (?P<ms>[+-][\d.]+) ms over (?P<n>\d+) hit")


def parse_live_compare_line(text: str) -> dict[str, Any] | None:
    """One line of `live_compare.py --plain` output -> a judgment.note envelope
    (not yet normalized). Returns None for anything else (headers, summary)."""
    m = LIVE_COMPARE_LINE.match(text)
    if m is None:
        return None
    return {"type": "judgment.note", "src": "chris",
            "data": {"error_ms": float(m["ms"]), "song_time": float(m["t"]),
                     "verdict": m["word"].lower(), "n_recent": int(m["n"])}}


class Adapter:
    """Holds the little bit of state needed to fill gaps (e.g. a note
    without a bar number gets one from session.state's t0 and bpm)."""

    def __init__(self, error_sign: int = 1, beats_per_bar: int = 4):
        self.error_sign = 1 if error_sign >= 0 else -1
        self.beats_per_bar = beats_per_bar
        self.t0: float | None = None
        self.bpm: float | None = None

    def normalize(self, msg: dict[str, Any]) -> dict[str, Any] | None:
        typ = TYPE_ALIASES.get(msg.get("type", ""), msg.get("type", ""))
        data = dict(msg.get("data") or {})
        if typ == "judgment.note" and "error_ms" not in data:
            for k in SECONDS_FIELDS:
                if isinstance(data.get(k), (int, float)):
                    data["error_ms"] = data.pop(k) * 1000.0
                    break
        for alt, canon in FIELD_ALIASES.get(typ, {}).items():
            if alt in data and canon not in data:
                data[canon] = data.pop(alt)

        if typ == "session.state":
            if data.get("t0") is not None:
                self.t0 = _num(data["t0"])
            if data.get("bpm") is not None:
                self.bpm = _num(data["bpm"])

        elif typ == "judgment.note":
            err = data.get("error_ms")
            if isinstance(err, str):
                word = err.strip().lower()
                if word in EARLY_WORDS:
                    data["error_ms"] = -25.0
                elif word in LATE_WORDS:
                    data["error_ms"] = 25.0
                elif word in ON_TIME_WORDS:
                    data["error_ms"] = 0.0
                else:
                    data["error_ms"] = _num(err)
            elif err is not None:
                data["error_ms"] = _num(err) * self.error_sign
            if data.get("bar") is None:
                data["bar"] = self._bar_at(msg.get("t"), data.get("song_time"))
            data.setdefault("grade", _grade(data.get("error_ms")))

        elif typ == "judgment.alert":
            if data.get("severity") is not None:
                data["severity"] = max(0.0, min(1.0, _num(data["severity"])))
            if isinstance(data.get("kind"), str):
                data["kind"] = data["kind"].strip().lower()

        elif typ == "judgment.bar":
            if isinstance(data.get("trend"), str):
                data["trend"] = data["trend"].strip().lower()

        out = dict(msg)
        out["type"] = typ
        out["data"] = data
        return out

    def _bar_at(self, t: Any, song_time: Any = None) -> int | None:
        """From song time (Chris: seconds since the sheet's first note) if
        given, else from the hub clock t and session.state's t0."""
        if not self.bpm:
            return None
        if song_time is not None:
            elapsed = _num(song_time)
        elif self.t0 is not None and t is not None:
            elapsed = _num(t) - self.t0
        else:
            return None
        bar_len = self.beats_per_bar * 60.0 / self.bpm
        return max(1, int(elapsed // bar_len) + 1)


def _num(x: Any) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _grade(err: Any) -> str | None:
    if err is None:
        return None
    a = abs(_num(err))
    if a <= 15:
        return "perfect"
    if a <= 35:
        return "good"
    if a <= 80:
        return "off"
    return "miss"
