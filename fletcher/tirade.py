"""Tier 3: the words. Claude writes one short line from the referee's fact
sheet and we split it into sentences as it streams, so the voice can start
on sentence one while sentence two is still being written.

Draft wraps one generation so it can start speculatively (before the
cutoff) and be replayed from the start if the first voice engine fails."""

from __future__ import annotations

import asyncio
import logging
import random
from collections import deque
from typing import AsyncIterator

from .brain import Facts
from .config import Config
from .text import SentenceSplitter, num_words, mentions_bar

log = logging.getLogger("fletcher.tirade")

ALLOWED_TAGS = "[quietly], [cold], [sharp], [shouting], [furious], [whispers], [pause]"

LEVELS = {
    1: ("One short cold line about the mistake, then the order. At most 18 words in total. "
        "Controlled and quiet, no shouting. Start with [cold] or [quietly]."),
    2: ("At most two short sentences plus the order, about 25 words in total. Sharp and "
        "contemptuous, with one [sharp] or [shouting] moment."),
    3: ("At most two sentences plus the order, about 30 words in total. Start dead quiet with "
        "[quietly], then explode: put [shouting] or [furious] before the last sentence."),
}

SYSTEM_TEMPLATE = """You write the spoken lines of an original character: a ruthless, perfectionist \
jazz band conductor running a rehearsal in a mixed-reality drum trainer. He has just closed his \
fist and cut the drummer off. A referee gives you objective facts about the drummer's actual mistake.

Write ONLY the words he says next, as plain text for a text-to-speech voice.

Rules:
- Name the specific mistake from the fact sheet: the bar, rushing or dragging or missed notes, and the \
milliseconds or tempo if given. Specific beats generic every time.
- Length and delivery: {level_rule}
- End with a short order that names the restart bar from the fact sheet, like "From bar nine. Now."
- Write every number as words ("bar nine", "forty milliseconds"). Never write digits, "ms" or "bpm".
- You may put these audio tags right before the words they change: {tags}. Use an ellipsis (...) \
for a menacing pause. No other markup, quotes, stage directions, asterisks or emojis.
- Brutal about the playing, never about the person: nothing about identity, body, family or worth, \
no slurs, nothing about self-harm or violence. {profanity_rule}
- Original lines only. Never quote or closely paraphrase any film, show or real person.
- Do not reuse or closely echo these recent lines: {recent}"""


class TiradeWriter:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.recent: deque[str] = deque(maxlen=5)
        self._client = None
        if not cfg.dry_run and cfg.anthropic_api_key:
            from anthropic import AsyncAnthropic
            self._client = AsyncAnthropic(api_key=cfg.anthropic_api_key)

    @property
    def live(self) -> bool:
        return self._client is not None

    def system_prompt(self, level: int) -> str:
        profanity = ("Mild profanity is allowed, sparingly." if self.cfg.profanity
                     else "No profanity at all.")
        recent = " | ".join(self.recent) if self.recent else "(none yet)"
        return SYSTEM_TEMPLATE.format(level_rule=LEVELS.get(level, LEVELS[2]), tags=ALLOWED_TAGS,
                                      profanity_rule=profanity, recent=recent)

    async def sentences(self, facts: Facts, level: int) -> AsyncIterator[str]:
        if not self.live:
            for s in canned_line(facts, level):
                await asyncio.sleep(0.05)
                yield s
            return
        splitter = SentenceSplitter()
        async with self._client.messages.stream(
            model=self.cfg.llm_model,
            max_tokens=self.cfg.llm_max_tokens,
            system=self.system_prompt(level),
            messages=[{"role": "user", "content": f"Fact sheet:\n{facts.sheet()}\n\nWrite his line now."}],
            thinking={"type": "disabled"},
            output_config={"effort": self.cfg.llm_effort},
        ) as stream:
            async for chunk in stream.text_stream:
                for s in splitter.feed(chunk):
                    yield s
        for s in splitter.flush():
            yield s


class Draft:
    """One line in progress. Sentences accumulate as they stream in; any number
    of readers can replay them from the start (needed for voice fallback)."""

    def __init__(self, writer: TiradeWriter, facts: Facts, level: int, canned: bool = False):
        self.writer, self.facts, self.level, self.canned = writer, facts, level, canned
        self.sentences: list[str] = []
        self.done = asyncio.Event()
        self._changed = asyncio.Event()
        self.error: BaseException | None = None
        self.order_added = False
        self.task: asyncio.Task | None = None

    def start(self) -> "Draft":
        self.task = asyncio.create_task(self._run())
        return self

    async def _run(self) -> None:
        try:
            if self.canned:
                for s in canned_line(self.facts, self.level):
                    self._push(s)
            else:
                async for s in self.writer.sentences(self.facts, self.level):
                    self._push(s)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # network, auth, rate limit...
            log.warning("tirade generation failed: %r", e)
            self.error = e
        finally:
            self._finish()

    def _push(self, s: str) -> None:
        self.sentences.append(s)
        self._changed.set()

    def _finish(self) -> None:
        # Guarantee he names the restart bar, whatever the model wrote.
        if not self.error and self.sentences and not mentions_bar(" ".join(self.sentences), self.facts.from_bar):
            self.sentences.append(f"[sharp] From bar {num_words(self.facts.from_bar)}. Now.")
            self.order_added = True
        self.done.set()
        self._changed.set()

    def cancel(self) -> None:
        if self.task and not self.task.done():
            self.task.cancel()

    @property
    def text(self) -> str:
        return " ".join(self.sentences)

    def compatible(self, facts: Facts, level: int) -> bool:
        return (self.error is None and self.facts.kind == facts.kind
                and self.facts.from_bar == facts.from_bar and self.level == level)

    async def wait_first(self, timeout: float) -> bool:
        """True once a first sentence exists; False on timeout, error or empty output."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while not self.sentences:
            if self.done.is_set():
                return False
            remaining = deadline - loop.time()
            if remaining <= 0:
                return False
            self._changed.clear()
            if self.sentences or self.done.is_set():
                continue
            try:
                await asyncio.wait_for(self._changed.wait(), remaining)
            except asyncio.TimeoutError:
                return False
        return True

    async def iter_sentences(self) -> AsyncIterator[str]:
        i = 0
        while True:
            if i < len(self.sentences):
                yield self.sentences[i]
                i += 1
                continue
            if self.done.is_set():
                return
            self._changed.clear()
            if i < len(self.sentences) or self.done.is_set():
                continue
            await self._changed.wait()


# ---------------------------------------------------------- canned lines ---
# Used in --dry-run, without an API key, or when Claude is slow or down.
# Original lines, templated with the real facts.

def canned_line(f: Facts, level: int, rng: random.Random | None = None) -> list[str]:
    rng = rng or random.Random()
    bar = num_words(f.since_bar)
    frm = num_words(f.from_bar)
    ms = num_words(abs(f.mean_error_ms)) if f.mean_error_ms is not None else None
    side = ("early" if (f.mean_error_ms or 0) < 0 else "late") if f.mean_error_ms is not None else None
    verb = {"rushing": "rushed", "dragging": "dragged", "missed_notes": "dropped notes",
            "lost": "lost the time", "stopped_playing": "stopped"}.get(f.kind, "fell apart")
    order = f"From bar {frm}. Now."
    detail = (f"{ms.capitalize()} milliseconds {side} in bar {num_words(f.current_bar)}." if ms and side and f.kind in ("rushing", "dragging")
              else f"You {verb} in bar {bar}.")
    played = num_words(f.bpm * f.tempo_ratio) if f.bpm and f.tempo_ratio else None

    if level <= 1:
        options = [
            [f"[cold] Bar {bar}. You {verb}.", f"[pause] {order}"],
            [f"[quietly] {detail}", order],
        ]
    elif level == 2:
        options = [
            [f"[sharp] {detail}", "Did you think I would not hear it?", order],
            [f"[cold] Again in bar {bar}... you {verb}.", "[sharp] Every single time.", order],
        ]
    else:
        quiet = (f"[quietly] I asked for {num_words(f.bpm)}. You gave me {played}." if played and f.bpm
                 else f"[quietly] Bar {bar}... and you {verb}. Again.")
        options = [
            [quiet, "[shouting] Who told you that was acceptable?", order],
            [quiet, "[furious] Three times now! Do you even hear the click?", order],
        ]
    return rng.choice(options)
