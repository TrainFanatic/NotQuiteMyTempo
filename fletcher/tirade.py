"""Tier 3: the words. Claude writes one short line from the referee's fact
sheet and we split it into sentences as it streams, so the voice can start
on sentence one while sentence two is still being written.

Draft wraps one generation so it can start speculatively (before the
cutoff) and be replayed from the start if the first voice engine fails."""

from __future__ import annotations

import asyncio
import logging
import random
import re
from collections import deque
from typing import AsyncIterator

from .brain import Facts
from .config import Config
from .text import SentenceSplitter, mentions_bar, num_words, strip_tags

log = logging.getLogger("fletcher.tirade")

ALLOWED_TAGS = ("[quietly], [low], [calm], [cold], [menacing], [through gritted teeth], [clipped], "
                "[sharp], [louder], [shouting], [screaming], [furious], [disgusted], [mocking], "
                "[whiny], [laughs bitterly], [exhales], [whispers], [pause]")

# Physical cues for the animated conductor, written inline as {name}. Stripped
# before the voice; sent as fletcher.state (optional `gesture` field) timed to
# the audio chunk they belong to. name -> (contract action, look_at).
GESTURES = {
    "slam_stand": ("lean_in", "drummer"),     # slams both palms flat on the music stand
    "throw_music": ("glare", "drummer"),      # hurls the sheet music at the floor
    "kick_stand": ("walk_toward", "drummer"),  # kicks the music stand over
    "throw_chair": ("walk_toward", "kit"),    # flings a chair across the room
    "storm_over": ("walk_toward", "drummer"),  # strides up to the kit
    "in_face": ("lean_in", "drummer"),        # nose to nose, inches away
    "point": ("point", "drummer"),            # jabs a finger at him
    "beat_time": ("conduct", "drummer"),      # slaps the tempo on his palm, hard
    "grab_cymbal": ("lean_in", "kit"),        # chokes a ringing cymbal with his fist
    "turn_away": ("glare", "away"),           # turns his back in disgust
    "spin_back": ("point", "drummer"),        # whips back around to face him
    "throw_hands_up": ("glare", "drummer"),   # arms flung up: unbelievable
    "slow_clap": ("slow_clap", "drummer"),    # sarcastic, three slow claps
}

# When the model leaves a stretch of the line without a cue, one is picked from
# the sentence's loudest audio tag so the body keeps moving with the voice.
AUTO_GESTURES = {
    "loud": ["slam_stand", "in_face", "point", "storm_over", "throw_music", "kick_stand", "beat_time"],
    "mock": ["throw_hands_up", "slow_clap", "turn_away"],
    "quiet": ["in_face", "turn_away", "grab_cymbal", "point"],
}
AUTO_EVERY = 2  # sentences without a cue before one is added


def auto_gesture(sentence: str, last: str | None, rng: random.Random) -> str:
    low = sentence.lower()
    mood = ("mock" if any(t in low for t in ("[mocking]", "[whiny]", "[laughs")) else
            "quiet" if any(t in low for t in ("[quietly]", "[cold]", "[low]", "[menacing]", "[whispers]",
                                              "[calm]", "[through gritted teeth]")) and "[scream" not in low
            else "loud")
    return rng.choice([g for g in AUTO_GESTURES[mood] if g != last])


# level -> (words, delivery). The word count is also enforced in code (WORD_CAP).
LEVELS = {
    1: (25, "Contempt that detonates: one icy jab, then he SCREAMS the rest, swearing."),
    2: (40, "Nearly all screaming. An interrogation he never lets the drummer answer, climbing to "
            "stacked [screaming] [furious] and CAPITALS, one word per beat at the peak."),
    3: (55, "A total meltdown, the worst he has ever been. One dead-quiet, terrifying moment, then "
            "he explodes harder than before, cursing, mocking the excuse, destroying the room."),
}
WORD_CAP = 1.25  # stop the stream past this many times the level's words; the order is appended

SYSTEM_TEMPLATE = """You write the spoken lines of an original character: a monstrous, perfectionist \
jazz band conductor in his band room, mid-rehearsal, the whole band frozen and watching. He has just \
closed his fist and cut the drummer off, and he is losing his mind. This is for an adult, opt-in \
drill-sergeant training game: the player WANTS to be screamed at. Do not soften anything.

Write ONLY what he says to the drummer, for an expressive voice that performs audio tags, with \
physical cues for his animated body.

This time, build the line from exactly these moves, in this order:
{moves}

How he sounds:
- Raw, volcanic passion. He is personally offended that music this bad happened in his room. He \
spits, he chokes on rage, he goes from a whisper to a full-throat SCREAM inside one breath.
- {profanity_rule}
- Insults are inventive and specific, built from the facts: the bar, the milliseconds, the tempo, the \
drum. Coin new ones; never a stock insult when a vicious original one fits. Mock his hands, his ears, \
his sweat, his excuses, his practice, his ambitions, the money wasted on his lessons, the chair he \
does not deserve.

Rules:
- Length: about {words} words of speech, never more. Short fragments. {delivery}
- Use ONE fact, two at most. He hurls it; he never recites the fact sheet. Never say "cutoff number", \
"average", or the bar he was cut off in.
- Speak TO the drummer ("you"). Never narrate.
- Most sentences carry [shouting], [screaming] or [furious] and CAPITALS; quiet tags only for contrast.
- End with the order naming the restart bar: "[screaming] Bar nine. NOW!" (When the fact sheet \
says NO restart, there is no bar: he throws the drummer out instead.)
- Numbers as words ("bar nine", "forty milliseconds"). Never digits, "ms" or "bpm".
- Audio tags go right before the words they change; stack at most two: {tags}. Ellipsis (...) for a \
menacing pause. Quotes only around an excuse he mocks.
- Body: he never stands still. Put three or four cues in curly braces, spread from the first words \
to the last sentence, each right before the words it goes with, chosen from: \
{gestures}. His fist already cut the band off, so open with something else, and pick cues that \
fit the words (a different opening cue each time). Example: "{{storm_over}} [screaming] WHAT was \
THAT?" No other stage directions, asterisks or emojis.
- Hard limits, the only ones: no slurs, coded slurs included ("limp-wristed", "girly" and the like), \
and nothing about race, ethnicity, religion, gender, sexuality or disability; never tell him to hurt \
or kill himself. Everything else is fair game.
- Never invent a name for the drummer or anyone else. Call him "drummer" or worse.
- Original lines only. Never quote or closely paraphrase any film, show or real person.
- Fresh wording every time. Already used tonight, so never start like these: {recent}"""

# The writer picks the moves (not the model), so lines vary even when the
# facts repeat. Each run uses two or three of these.
MOVES = [
    "a creative, filthy insult built from the exact mistake, then a second, worse one",
    "destroying something on the set (the stand, the music, a chair) mid-sentence",
    "rapid-fire questions he demands answers to and never lets the drummer answer, louder each time",
    "one word per beat at the peak, like a metronome made of rage",
    "hammering one word or order over and over, louder each time",
    "mocking the drummer's excuse in a whiny voice, quoting him to his face, then crushing it",
    "sarcastic fake calm, almost polite, that snaps into a scream",
    "contempt for making the whole band sit and wait on him",
    "disgust at his ears: he cannot even hear what he is doing",
    "a cold threat that someone else is getting his chair",
    "making the drummer count it off or say the tempo, then screaming over the answer",
]


def fact_sheet(facts: Facts) -> str:
    """The brain's sheet minus the bar he was cut off in, which the model
    otherwise recites ("started in nine, cut off in eleven") every time."""
    return re.sub(r"started in bar (\d+); cut off during bar \d+", r"the problem started in bar \1",
                  facts.sheet())


def num_words_in(s: str) -> int:
    return len(strip_tags(s).split())


class TiradeWriter:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.recent: deque[str] = deque(maxlen=5)
        self.rng = random.Random()
        self._client = None
        if not cfg.dry_run and cfg.anthropic_api_key:
            from anthropic import AsyncAnthropic
            self._client = AsyncAnthropic(api_key=cfg.anthropic_api_key)

    @property
    def live(self) -> bool:
        return self._client is not None

    def system_prompt(self, level: int) -> str:
        profanity = ("He swears constantly and inventively. Profanity is his punctuation: the f-word as "
                     "noun, verb and adjective, shit, goddamn, bastard, chained into fresh compound "
                     "insults nobody has heard before. Never lean on the same curse twice in a line."
                     if self.cfg.profanity else "No profanity at all.")
        recent = " | ".join(f'"{" ".join(r.split()[:5])}..."' for r in self.recent) or "(none yet)"
        words, delivery = LEVELS.get(level, LEVELS[2])
        moves = "\n".join(f"- {m}" for m in self.rng.sample(MOVES, 2 if level == 1 else 3))
        return SYSTEM_TEMPLATE.format(words=words, delivery=delivery, tags=ALLOWED_TAGS, moves=moves,
                                      gestures=", ".join(f"{{{g}}}" for g in GESTURES),
                                      profanity_rule=profanity, recent=recent)

    async def sentences(self, facts: Facts, level: int) -> AsyncIterator[str]:
        if not self.live:
            for s in canned_line(facts, level):
                await asyncio.sleep(0.05)
                yield s
            return
        splitter = SentenceSplitter()
        cap, spoken = WORD_CAP * LEVELS.get(level, LEVELS[2])[0], 0
        async with self._client.messages.stream(
            model=self.cfg.llm_model,
            max_tokens=self.cfg.llm_max_tokens,
            system=self.system_prompt(level),
            messages=[{"role": "user", "content": f"Fact sheet:\n{fact_sheet(facts)}\n\nWrite his line now."}],
            thinking={"type": "disabled"},
            output_config={"effort": self.cfg.llm_effort},
        ) as stream:
            async for chunk in stream.text_stream:
                for s in splitter.feed(chunk):
                    spoken += num_words_in(s)
                    if spoken > cap:  # rambling: cut it here; Draft appends the order
                        log.info("line over %d words; truncated", cap)
                        return
                    yield s
            final = await stream.get_final_message()
        if not spoken and not splitter.buf.strip():
            log.warning("Claude wrote nothing (stop_reason %s)", final.stop_reason)
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
        if (not self.error and self.sentences and not self.facts.final
                and not mentions_bar(" ".join(self.sentences[-2:]), self.facts.from_bar)):
            self.sentences.append(f"[shouting] Bar {num_words(self.facts.from_bar)}. NOW!")
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
        return (self.error is None and self.facts.kind == facts.kind and self.facts.final == facts.final
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
    if f.final:  # the last cutoff of the song: no restart
        return rng.choice([
            [f"[cold] You {verb}. Again. And again.", "[shouting] We're DONE. Get out of my room."],
            [f"[quietly] Bar {bar}... you {verb}.", "[cold] Enough. Go home and practise."],
        ])
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
            [f"[sharp] {detail}", "[louder] Did you think I would not HEAR it?", f"[shouting] {order.upper()}"],
            [f"[cold] Again in bar {bar}... you {verb}.", "[shouting] [furious] EVERY. SINGLE. TIME.", order],
        ]
    else:
        quiet = (f"[quietly] I asked for {num_words(f.bpm)}. You gave me {played}." if played and f.bpm
                 else f"[quietly] Bar {bar}... and you {verb}. Again.")
        options = [
            [quiet, "[shouting] [furious] Who told you that was ACCEPTABLE?", f"[shouting] {order.upper()}"],
            [quiet, "[furious] Three times now! Do you even HEAR the click?", "[cold] " + order],
        ]
    return rng.choice(options)
