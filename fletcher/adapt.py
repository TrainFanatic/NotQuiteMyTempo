"""The ONLY place that knows how teammates' real messages differ from CONTRACT.md.

The referee's code has drifted since the plan was written. Instead of
sprinkling special cases through the brain, map whatever actually arrives
onto the contract shapes here. To update it: open the newest
recordings/*.jsonl, look at what Chris really sends, and edit the tables.

normalize() returns a contract-shaped message, or None to drop it.
"""

from __future__ import annotations

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

# Some referees send early/late as a label instead of a signed number.
EARLY_WORDS = {"early", "rushing", "rushed", "ahead"}
LATE_WORDS = {"late", "dragging", "dragged", "behind"}


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
                else:
                    data["error_ms"] = _num(err)
            elif err is not None:
                data["error_ms"] = _num(err) * self.error_sign
            if data.get("bar") is None:
                data["bar"] = self._bar_at(msg.get("t"))
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

    def _bar_at(self, t: Any) -> int | None:
        if self.t0 is None or not self.bpm or t is None:
            return None
        bar_len = self.beats_per_bar * 60.0 / self.bpm
        return max(1, int((_num(t) - self.t0) // bar_len) + 1)


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
