"""Message envelope helpers. The formats live in CONTRACT.md at the repo root;
this file only builds and parses them. Never change a format here: if one
looks wrong, raise it with the team and change CONTRACT.md first."""

from __future__ import annotations

import json
import time
from typing import Any

# Types we read
SESSION_STATE = "session.state"
JUDGMENT_NOTE = "judgment.note"
JUDGMENT_BAR = "judgment.bar"
JUDGMENT_ALERT = "judgment.alert"

# Types we write
SESSION_COMMAND = "session.command"
FLETCHER_STATE = "fletcher.state"
FLETCHER_SPEECH = "fletcher.speech"
FLETCHER_AUDIO = "fletcher.audio"

MOODS = ("calm", "watching", "irritated", "furious", "approving")
ACTIONS = (
    "idle", "conduct", "glare", "lean_in", "point",
    "fist_cutoff", "walk_toward", "nod", "slow_clap",
)
LOOK_AT = ("drummer", "kit", "away")


def now() -> float:
    """The one clock: laptop monotonic seconds."""
    return time.monotonic()


def envelope(type_: str, src: str, seq: int, data: dict[str, Any], t: float | None = None) -> dict:
    return {"type": type_, "src": src, "seq": seq, "t": now() if t is None else t, "data": data}


def parse(raw: str | bytes) -> dict | None:
    """Parse one frame. Returns None for anything that isn't an envelope.
    Unknown types and fields are kept; callers ignore what they don't use."""
    try:
        msg = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(msg, dict) or not isinstance(msg.get("type"), str):
        return None
    if not isinstance(msg.get("data"), dict):
        msg["data"] = {}
    return msg
