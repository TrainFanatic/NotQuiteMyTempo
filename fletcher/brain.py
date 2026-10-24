"""Fletcher's mood machine. Pure logic, no network, no clock of its own:
every method takes `t` (laptop monotonic seconds) and returns a list of
effects for the orchestrator (main.py) to carry out. That keeps it fully
testable with synthetic messages.

The decision to cut off is deterministic and lives here, NOT in the LLM:
an LLM call takes hundreds of milliseconds and can't be trusted to fire on
the beat. Claude only writes the words once this brain decides to stop.
"""

from __future__ import annotations

import random
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from .config import Config
from .text import num_words

TEMPO_KINDS = ("rushing", "dragging")


# ---------------------------------------------------------------- facts ---

@dataclass
class Facts:
    """Compact, objective description of the mistake. Becomes the LLM's fact sheet."""
    kind: str
    severity: float
    since_bar: int
    current_bar: int
    from_bar: int
    detail: str = ""
    tempo_ratio: float | None = None
    mean_error_ms: float | None = None
    misses: int = 0
    accuracy: float | None = None
    bpm: float | None = None
    cutoff_number: int = 1
    per_drum: dict[str, dict[str, float]] = field(default_factory=dict)

    def sheet(self) -> str:
        lines = [
            f"problem: {self.kind.replace('_', ' ')} (severity {self.severity:.2f})",
            f"started in bar {self.since_bar}; cut off during bar {self.current_bar}",
        ]
        if self.detail:
            lines.append(f"referee detail: {self.detail}")
        if self.bpm and self.tempo_ratio:
            played = self.bpm * self.tempo_ratio
            lines.append(f"tempo: asked for {self.bpm:.0f} beats per minute, drummer played about {played:.0f}")
        if self.mean_error_ms is not None:
            side = "early" if self.mean_error_ms < 0 else "late"
            lines.append(f"average timing error: {abs(self.mean_error_ms):.0f} milliseconds {side}")
        for drum, s in sorted(self.per_drum.items(), key=lambda kv: -abs(kv[1]["mean_ms"])):
            side = "early" if s["mean_ms"] < 0 else "late"
            lines.append(f"{drum.replace('_', ' ')}: {int(s['n'])} hits, average {abs(s['mean_ms']):.0f} milliseconds {side}")
        if self.misses:
            lines.append(f"missed notes recently: {self.misses}")
        if self.accuracy is not None:
            lines.append(f"last bar accuracy: {self.accuracy:.0%}")
        lines.append(f"restart bar (say it): bar {self.from_bar} ({num_words(self.from_bar)})")
        lines.append(f"this is cutoff number {self.cutoff_number} in this run")
        return "\n".join(lines)


# -------------------------------------------------------------- effects ---

@dataclass
class SetState:
    mood: str
    anger: float
    action: str
    look_at: str = "drummer"


@dataclass
class Bark:
    category: str


@dataclass
class Speculate:
    facts: Facts
    level: int


@dataclass
class CancelSpeculation:
    pass


@dataclass
class Cutoff:
    facts: Facts
    level: int  # 1 cold one-liner, 2 sharp, 3 full explosion


# ---------------------------------------------------------------- brain ---

class Brain:
    def __init__(self, cfg: Config, rng: random.Random | None = None):
        self.cfg = cfg
        self.rng = rng or random.Random()
        self.reset()

    # -- lifecycle -----------------------------------------------------------
    def reset(self) -> None:
        self.phase = "idle"  # idle | ready | playing | stopped | cutoff
        self.anger = 0.0
        self.bar = 0
        self.play_start_bar = 1
        self.bpm: float | None = None
        self.alerts: dict[str, dict[str, Any]] = {}
        self.bar_added: dict[tuple[str, int], float] = {}
        self.bars: deque[dict] = deque(maxlen=8)
        self.notes: deque[dict] = deque(maxlen=64)
        self.last_bark_t = -1e9
        self.last_bark_kind: str | None = None
        self.cutoffs = 0
        self.spec_kind: str | None = None
        self.spec_bar = -1
        self.problem_bar = 0  # where the last cut-off problem began
        self.last_sent: tuple | None = None
        self.last_sent_anger = 0.0
        self.good_streak = 0
        self.approved = False
        self.approve_until_bar = -1
        self.external_alert_t = -1e9
        self.external_bars = False
        self.last_tick: float | None = None
        self.note_bar = 0

    def cut_threshold(self) -> float:
        return max(self.cfg.cutoff_floor, self.cfg.cutoff_anger - self.cfg.patience_step * self.cutoffs)

    # -- inputs ----------------------------------------------------------------
    def on_session_state(self, d: dict, t: float) -> list:
        st = d.get("state")
        if d.get("bpm"):
            self.bpm = float(d["bpm"])
        if d.get("bar") is not None:
            self._advance_bar(int(d["bar"]))
        if st == "playing":
            if self.phase not in ("playing", "cutoff"):
                self.phase = "playing"
                self.play_start_bar = max(1, int(d.get("bar") or self.bar or 1))
                self._clear_run_state()
        elif st == "idle":
            self.reset()
        elif st in ("calibrating", "count_in"):
            if self.phase != "cutoff":
                self.phase = "ready"
        elif st == "stopped":
            if self.phase == "playing":
                self.phase = "stopped"
        return self._state_effects()

    def on_note(self, d: dict, t: float) -> list:
        eff: list = []
        bar = d.get("bar")
        if bar is not None:
            bar = int(bar)
            if not self.external_bars and self.phase == "playing" and 0 < self.note_bar < bar:
                eff += self._close_bar_from_notes(self.note_bar)
            self.note_bar = bar
            self._advance_bar(bar)
        err = d.get("error_ms")
        self.notes.append({
            "drum": d.get("drum", "?"), "bar": d.get("bar") or self.bar,
            "err": None if err is None else float(err), "grade": d.get("grade"), "t": t,
        })
        if self.phase != "playing":
            return eff
        if d.get("grade") == "miss":
            self.anger = min(1.0, self.anger + self.cfg.miss_gain)
        if not self.external_bars and t - self.external_alert_t > 4.0:
            eff += self._derive_from_notes(t)
        eff += self._evaluate(t)
        return eff

    def on_bar(self, d: dict, t: float) -> list:
        self.external_bars = True
        bar = int(d.get("bar") or self.bar or 1)
        self._advance_bar(bar)
        self.bars.append(d)
        if self.phase != "playing":
            return []
        eff: list = []
        acc = d.get("accuracy")
        trend = d.get("trend")
        misses = int(d.get("misses") or 0)
        good = (acc is None or acc >= 0.85) and trend in (None, "steady") and misses == 0
        eff += self._bar_verdict(good)

        if trend in TEMPO_KINDS:
            sev = self._bar_severity(d)
            prev = self.alerts.get(trend)
            if prev and self._is_active(prev, t) and prev["sev"] >= sev:
                prev["bar"], prev["t"] = self.bar, t  # referee's alert still holds: keep it alive
            else:
                since = prev["since_bar"] if prev and self._is_active(prev, t) else bar
                eff += self._apply_alert(trend, sev, since, _bar_detail(d, bar), t)
        if misses >= 2:
            eff += self._apply_alert("missed_notes", _clamp(0.5 + 0.15 * (misses - 2)), bar,
                                     f"{misses} missed notes in bar {bar}", t)
        if acc is not None and acc < 0.5:
            eff += self._apply_alert("lost", _clamp(0.6 + (0.5 - acc)), bar,
                                     f"bar {bar} accuracy {acc:.0%}", t)
        eff += self._evaluate(t)
        return eff

    def on_alert(self, d: dict, t: float) -> list:
        self.external_alert_t = t
        if self.phase != "playing":
            return []
        kind = d.get("kind") or "rushing"
        sev = _clamp(float(d.get("severity") or 0.0))
        since = int(d.get("since_bar") or self.bar or 1)
        eff = self._apply_alert(kind, sev, since, str(d.get("detail") or ""), t)
        eff += self._evaluate(t)
        return eff

    def tick(self, t: float) -> list:
        dt = 0.0 if self.last_tick is None else max(0.0, t - self.last_tick)
        self.last_tick = t
        if self.phase != "playing":
            return self._state_effects()
        if not self._active_alerts(t):
            self.anger = max(0.0, self.anger - self.cfg.idle_decay_per_s * dt)
        return self._evaluate(t)

    def on_restarted(self, from_bar: int) -> list:
        """Called by the orchestrator right after it sends session.command restart."""
        self.phase = "playing"
        self.bar = from_bar
        self.play_start_bar = from_bar
        self.anger *= 0.35  # still annoyed
        self._clear_run_state()
        return self._state_effects()

    # -- internals ---------------------------------------------------------------
    def _clear_run_state(self) -> None:
        self.alerts.clear()
        self.bar_added.clear()
        self.good_streak = 0
        self.approved = False
        self.approve_until_bar = -1
        self.spec_kind = None

    def _advance_bar(self, bar: int) -> None:
        if bar > self.bar or self.phase in ("idle", "ready"):
            self.bar = bar
        for key in [k for k in self.bar_added if k[1] < self.bar - 4]:
            del self.bar_added[key]

    def _apply_alert(self, kind: str, sev: float, since: int, detail: str, t: float) -> list:
        rec = self.alerts.get(kind)
        high_since = None
        if sev >= self.cfg.sustain_severity:
            carried = rec and self._is_active(rec, t) and rec.get("high_since_bar") is not None
            high_since = rec["high_since_bar"] if carried else self.bar
        if rec and rec["since_bar"] < since and self._is_active(rec, t):
            since = rec["since_bar"]  # keep the bar where the problem began
        self.alerts[kind] = {"kind": kind, "sev": sev, "since_bar": max(1, since), "detail": detail,
                             "bar": self.bar, "t": t, "high_since_bar": high_since}
        key = (kind, self.bar)
        prev = self.bar_added.get(key, 0.0)
        if sev > prev:  # count each kind at most once per bar, by its peak severity
            self.anger = min(1.0, self.anger + self.cfg.alert_gain * (sev - prev))
            self.bar_added[key] = sev
        return self._maybe_bark(kind, sev, t)

    def _bar_verdict(self, good: bool) -> list:
        if good:
            self.anger = max(0.0, self.anger - self.cfg.bar_decay)
            self.good_streak += 1
        else:
            self.good_streak = 0
        return self._maybe_approve()

    def _close_bar_from_notes(self, bar: int) -> list:
        notes = [n for n in self.notes if n["bar"] == bar]
        if not notes:
            return []
        errs = [n["err"] for n in notes if n["err"] is not None and n["grade"] not in ("miss", "extra")]
        misses = sum(1 for n in notes if n["grade"] == "miss")
        acc = sum(1 for e in errs if abs(e) <= 35) / len(notes)
        mean = sum(errs) / len(errs) if errs else 0.0
        good = misses == 0 and acc >= 0.85 and abs(mean) < self.cfg.derive_threshold_ms
        return self._bar_verdict(good)

    @staticmethod
    def _bar_severity(d: dict) -> float:
        sev = 0.2
        ratio = d.get("tempo_ratio")
        if ratio is not None:
            dev = abs(float(ratio) - 1.0)
            if dev >= 0.03:
                sev = max(sev, _clamp(0.3 + (dev - 0.03) / 0.05 * 0.7))
        mean = d.get("mean_error_ms")
        if mean is not None and abs(float(mean)) >= 20:
            sev = max(sev, _clamp(0.3 + (abs(float(mean)) - 20) / 40))
        return sev

    def _derive_from_notes(self, t: float) -> list:
        """Fallback referee: if only raw early/late notes arrive, find drift ourselves."""
        hits = [n for n in self.notes if n["err"] is not None and n["grade"] not in ("miss", "extra")]
        eff: list = []
        if len(hits) >= 6:
            recent = hits[-8:]
            mean = sum(n["err"] for n in recent) / len(recent)
            if abs(mean) >= self.cfg.derive_threshold_ms:
                kind = "rushing" if mean < 0 else "dragging"
                sev = _clamp(0.3 + (abs(mean) - self.cfg.derive_threshold_ms) / 40.0)
                since = recent[-1]["bar"]
                for n in reversed(hits):  # walk back while hits are clearly off the same way
                    if (n["err"] < 0) != (mean < 0) or abs(n["err"]) < self.cfg.derive_threshold_ms / 2:
                        break
                    since = n["bar"]
                side = "early" if mean < 0 else "late"
                eff += self._apply_alert(kind, sev, int(since or self.bar or 1),
                                         f"last {len(recent)} hits average {abs(mean):.0f} ms {side}", t)
        misses = sum(1 for n in self.notes if n["grade"] == "miss" and n["bar"] == self.bar)
        if misses >= 2:
            eff += self._apply_alert("missed_notes", _clamp(0.5 + 0.15 * (misses - 2)), self.bar,
                                     f"{misses} missed notes in bar {self.bar}", t)
        return eff

    def _is_active(self, rec: dict, t: float) -> bool:
        return (self.bar - rec["bar"]) <= 1 and (t - rec["t"]) < 4.0

    def _active_alerts(self, t: float) -> dict[str, dict]:
        return {k: r for k, r in self.alerts.items() if self._is_active(r, t)}

    def _maybe_bark(self, kind: str, sev: float, t: float) -> list:
        if self.phase != "playing" or sev < self.cfg.bark_severity:
            return []
        if self.anger >= self.cut_threshold():
            return []  # a cutoff is about to happen; the fist says it
        gap = t - self.last_bark_t
        if gap < self.cfg.bark_cooldown_s or (kind == self.last_bark_kind and gap < 2 * self.cfg.bark_cooldown_s):
            return []
        category = {"rushing": "rushing", "dragging": "dragging", "missed_notes": "no",
                    "lost": "watch", "stopped_playing": "no"}.get(kind)
        if not category:
            return []
        self.last_bark_t, self.last_bark_kind = t, kind
        return [Bark(category)]

    def _maybe_approve(self) -> list:
        if (self.cutoffs > 0 and not self.approved and self.good_streak >= self.cfg.approve_bars
                and self.bar > self.problem_bar and self.anger < 0.35):  # nod only once past the trouble spot
            self.approved = True
            self.approve_until_bar = self.bar + 1
            eff: list = []
            if self.rng.random() < self.cfg.praise_chance:
                eff.append(Bark("praise"))
            return eff + self._state_effects()
        return []

    def _evaluate(self, t: float) -> list:
        if self.phase != "playing":
            return []
        eff: list = []
        active = self._active_alerts(t)
        thr = self.cut_threshold()
        bars_in = self.bar - self.play_start_bar
        sustained = any(
            r["sev"] >= self.cfg.sustain_severity and r["high_since_bar"] is not None
            and self.bar - r["high_since_bar"] >= 1
            for r in active.values()
        )
        if active and bars_in >= self.cfg.grace_bars and (self.anger >= thr or sustained):
            self.cutoffs += 1
            facts = self._facts(t)
            self.problem_bar = facts.since_bar
            self.phase = "cutoff"
            self.anger = max(self.anger, 0.85)
            self.spec_kind = None
            return [Cutoff(facts, level=min(3, self.cutoffs))]

        worst = max(active.values(), key=lambda r: r["sev"]) if active else None
        want = worst is not None and (self.anger >= thr - self.cfg.speculate_margin
                                      or worst["sev"] >= self.cfg.speculate_severity)
        if want and (self.spec_kind != worst["kind"] or self.bar > self.spec_bar):
            # (re)draft with fresh facts: on a new problem, and once per bar while it lasts
            self.spec_kind, self.spec_bar = worst["kind"], self.bar
            eff.append(Speculate(self._facts(t, cutoff_number=self.cutoffs + 1), level=min(3, self.cutoffs + 1)))
        elif (not want and self.spec_kind is not None
              and self.anger < thr - self.cfg.speculate_margin - 0.1):
            eff.append(CancelSpeculation())
            self.spec_kind = None
        return eff + self._state_effects()

    def _facts(self, t: float, cutoff_number: int | None = None) -> Facts:
        active = self._active_alerts(t) or self.alerts
        worst = max(active.values(), key=lambda r: r["sev"]) if active else {
            "kind": "lost", "sev": 0.5, "since_bar": max(1, self.bar - 1), "detail": ""}
        since = int(worst["since_bar"])
        from_bar = since
        pb = self.cfg.phrase_bars
        if pb and pb > 0:
            from_bar = ((since - 1) // pb) * pb + 1
        from_bar = max(1, min(from_bar, self.bar or from_bar))
        last = self.bars[-1] if self.bars else {}
        hits = [n for n in self.notes if n["err"] is not None and n["grade"] not in ("miss", "extra")
                and (n["bar"] or 0) >= since]
        per: dict[str, dict[str, float]] = {}
        for n in hits:
            s = per.setdefault(n["drum"], {"sum": 0.0, "n": 0.0})
            s["sum"] += n["err"]
            s["n"] += 1
        per_drum = {d: {"mean_ms": s["sum"] / s["n"], "n": s["n"]} for d, s in per.items() if s["n"] >= 2}
        tail = hits[-8:]
        mean = (sum(n["err"] for n in tail) / len(tail)) if tail else last.get("mean_error_ms")
        misses = sum(1 for n in self.notes if n["grade"] == "miss" and (n["bar"] or 0) >= since)
        return Facts(
            kind=worst["kind"], severity=float(worst["sev"]), since_bar=since,
            current_bar=self.bar or since, from_bar=from_bar, detail=worst.get("detail", ""),
            tempo_ratio=last.get("tempo_ratio"), mean_error_ms=mean, misses=misses,
            accuracy=last.get("accuracy"), bpm=self.bpm,
            cutoff_number=cutoff_number if cutoff_number is not None else self.cutoffs,
            per_drum=per_drum,
        )

    def _state_effects(self) -> list:
        if self.phase == "cutoff":
            return []
        a = self.anger
        if self.phase in ("idle",):
            state = ("calm", "idle", "drummer")
        elif self.phase == "ready":
            state = ("watching", "conduct", "drummer")
        elif self.phase == "stopped":
            state = ("watching", "idle", "drummer")
        elif self.bar <= self.approve_until_bar and a < 0.3:
            state = ("approving", "nod", "drummer")
        elif a < 0.2:
            state = ("calm", "conduct", "drummer")
        elif a < 0.45:
            state = ("watching", "conduct", "drummer")
        elif a < 0.7:
            state = ("irritated", "glare", "drummer")
        else:
            state = ("furious", "lean_in", "drummer")
        if state != self.last_sent or abs(a - self.last_sent_anger) >= 0.1:
            self.last_sent = state
            self.last_sent_anger = a
            return [SetState(state[0], round(a, 2), state[1], state[2])]
        return []


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _bar_detail(d: dict, bar: int) -> str:
    parts = []
    if d.get("tempo_ratio") is not None:
        parts.append(f"tempo ratio {float(d['tempo_ratio']):.2f}")
    if d.get("mean_error_ms") is not None:
        m = float(d["mean_error_ms"])
        parts.append(f"mean error {abs(m):.0f} ms {'early' if m < 0 else 'late'}")
    return f"bar {bar}: " + ", ".join(parts) if parts else f"bar {bar}"
