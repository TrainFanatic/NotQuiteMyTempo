"""Tier 2: barks. Short clips generated once (generate_barks.py) and bundled
in the Unity app, so at demo time we send only a clip_id: no network, ~0 ms.

Lines are original. Tags are performed by the v4 model at generation time."""

from __future__ import annotations

import json
import random
from pathlib import Path

from .text import num_words, strip_tags

BARK_DIR = Path(__file__).resolve().parent / "barks"
MANIFEST = BARK_DIR / "manifest.json"
MAX_BAR_CLIP = 32

CATALOG: dict[str, list[tuple[str, str]]] = {
    "rushing": [("rushing_1", "[sharp] Rushing."), ("rushing_2", "[cold] You're rushing."),
                ("rushing_3", "[sharp] Ahead of me. Sit back.")],
    "dragging": [("dragging_1", "[sharp] Dragging."), ("dragging_2", "[cold] You're dragging."),
                 ("dragging_3", "[sharp] Behind. Push it.")],
    "no": [("no_1", "[sharp] No."), ("no_2", "[cold] No, no, no.")],
    "watch": [("watch_1", "[sharp] Watch my hand."), ("watch_2", "[cold] Eyes on me.")],
    "stop": [("stop_1", "[shouting] Stop!"), ("stop_2", "[sharp] Stop.")],
    "again": [("again_1", "[cold] Again."), ("again_2", "[quietly] Again.")],
    "top": [("top_1", "[sharp] From the top.")],
    "praise": [("praise_1", "[quietly] Good."), ("praise_2", "[cold] ...Better.")],
    "count": [("count_1", "[sharp] Five, six, seven, eight.")],
}
# Categories the brain asks for that reuse another category's clips. "waiting" (the drummer
# isn't playing): dedicated clips ("Any time now.", "Play!") would need generate_barks.py AND a
# headset build with them in quest_app/fletcher_barks/, so for now he says "Watch my hand." /
# "Eyes on me." (with an impatient-watch move, cues.CATEGORY_MOVES).
ALIASES = {"waiting": "watch"}
for _n in range(1, MAX_BAR_CLIP + 1):
    CATALOG.setdefault("bar", []).append((f"bar_{_n:02d}", f"[sharp] From bar {num_words(_n)}."))


def all_clips() -> list[tuple[str, str, str]]:
    return [(cid, text, cat) for cat, items in CATALOG.items() for cid, text in items]


class BarkPicker:
    def __init__(self, rng: random.Random | None = None):
        self.rng = rng or random.Random()
        self.last: dict[str, str] = {}
        self.manifest: dict[str, dict] = {}
        if MANIFEST.exists():
            try:
                self.manifest = json.loads(MANIFEST.read_text())
            except ValueError:
                self.manifest = {}

    def pick(self, category: str) -> tuple[str, str] | None:
        category = ALIASES.get(category, category)
        items = CATALOG.get(category)
        if not items:
            return None
        choices = [i for i in items if i[0] != self.last.get(category)] or items
        cid, text = self.rng.choice(choices)
        self.last[category] = cid
        return cid, strip_tags(text)

    def bar_clip(self, bar: int) -> tuple[str, str]:
        n = max(1, min(MAX_BAR_CLIP, bar))
        cid = f"bar_{n:02d}"
        return cid, f"From bar {num_words(n)}."

    def duration(self, clip_id: str) -> float:
        return float(self.manifest.get(clip_id, {}).get("duration_s", 0.9))

    def path(self, clip_id: str) -> Path:
        return BARK_DIR / f"{clip_id}.wav"
