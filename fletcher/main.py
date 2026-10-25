"""Orchestrator: hub in -> brain -> barks / tirade / voice -> hub out.

Cutoff sequence (the only place an LLM is involved):
  1. fletcher.state fist_cutoff + session.command stop + "Stop!" bark (instant)
  2. glare for cfg.stare_s (or until the bark ends); meanwhile the line is already drafted
     (speculatively, before the cutoff) or drafting now
  3. fletcher.speech, then fletcher.audio chunks as the voice streams
  4. wait until his last word has played, then session.command restart
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
import wave
from pathlib import Path

from . import brain as B
from .adapt import Adapter
from .barks import BarkPicker
from .config import Config
from .contract import (FLETCHER_AUDIO, FLETCHER_SPEECH, FLETCHER_STATE, JUDGMENT_ALERT, JUDGMENT_BAR,
                       JUDGMENT_NOTE, SESSION_COMMAND, SESSION_STATE, now)
from .hub_client import HubClient
from .local_audio import LocalPlayer
from .text import merge_short, split_gestures, strip_tags
from .tirade import Draft, TiradeWriter
from .tirade import AUTO_EVERY, GESTURES, auto_gesture
from .voice import TURN, TTSError, build_voices

log = logging.getLogger("fletcher")


class Fletcher:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.hub = HubClient(cfg.hub_url, cfg.src)
        self.adapter = Adapter(cfg.error_sign, cfg.beats_per_bar)
        self.brain = B.Brain(cfg)
        self.writer = TiradeWriter(cfg)
        self.voices = build_voices(cfg)
        self.barks = BarkPicker()
        self.player = LocalPlayer(cfg.sample_rate) if cfg.play_local else None
        self.draft: Draft | None = None
        self.cut_task: asyncio.Task | None = None
        self.utterances = 0
        self.speech_end = 0.0
        self.stats: dict[str, list] = {"first_audio_ms": [], "speculative_hits": [], "cutoffs": []}

    # -- main loop -----------------------------------------------------------
    async def run(self) -> None:
        log.info("config: %s", self.cfg.describe())
        log.info("tirades: %s | voice: %s", "Claude " + self.cfg.llm_model if self.writer.live else "canned lines",
                 " -> ".join(v.name for v in self.voices) or "barks only")
        await asyncio.gather(self.hub.run(), self._consume(), self._ticker())

    async def _consume(self) -> None:
        handlers = {SESSION_STATE: self.brain.on_session_state, JUDGMENT_NOTE: self.brain.on_note,
                    JUDGMENT_BAR: self.brain.on_bar, JUDGMENT_ALERT: self.brain.on_alert}
        async for raw in self.hub.messages():
            msg = self.adapter.normalize(raw)
            if msg is None:
                continue
            fn = handlers.get(msg["type"])
            if fn is None:
                continue  # not ours; ignore unknown types (contract rule)
            try:
                if msg["type"] == SESSION_STATE and msg["data"].get("state") == "playing":
                    for v in self.voices:
                        asyncio.create_task(v.warm())  # open the socket before any cutoff
                effects = fn(msg["data"], msg.get("_rx", now()))
                await self.apply(effects)
            except Exception:
                log.exception("failed handling %s", msg.get("type"))

    async def _ticker(self) -> None:
        while True:
            await asyncio.sleep(0.1)
            try:
                await self.apply(self.brain.tick(now()))
            except Exception:
                log.exception("tick failed")

    # -- effects -------------------------------------------------------------
    async def apply(self, effects: list) -> None:
        for e in effects:
            if isinstance(e, B.SetState):
                await self.send_state(e.mood, e.anger, e.action, e.look_at)
            elif isinstance(e, B.Bark):
                await self.bark(e.category)
            elif isinstance(e, B.Speculate):
                self._cancel_draft()
                self.draft = Draft(self.writer, e.facts, e.level).start()
                log.info("drafting speculatively: %s since bar %d (level %d)", e.facts.kind, e.facts.since_bar, e.level)
            elif isinstance(e, B.CancelSpeculation):
                if self.draft:
                    log.info("drummer recovered; draft discarded")
                self._cancel_draft()
            elif isinstance(e, B.Cutoff):
                if self.cut_task and not self.cut_task.done():
                    continue
                self.cut_task = asyncio.create_task(self.cutoff(e.facts, e.level))

    def _cancel_draft(self) -> None:
        if self.draft:
            self.draft.cancel()
        self.draft = None

    async def send_state(self, mood: str, anger: float, action: str, look_at: str = "drummer", **extra) -> None:
        await self.hub.send(FLETCHER_STATE, {"mood": mood, "anger": round(anger, 2), "action": action,
                                             "look_at": look_at, **extra})
        log.info("state %-9s anger %.2f action %s%s", mood, anger, action,
                 f" gesture {extra['gesture']} @chunk {extra.get('at_index')}" if "gesture" in extra else "")

    async def send_gestures(self, names: list[str], uid: str, at_index: int) -> None:
        """Body cues from the line. `action` stays a contract value; the optional
        `gesture` names the specific move and `at_index` the fletcher.audio chunk
        of this utterance where it should start."""
        for g in names:
            action, look = GESTURES.get(g, ("lean_in", "drummer"))
            await self.send_state("furious", 1.0, action, look, gesture=g, utterance_id=uid, at_index=at_index)

    async def bark(self, category: str) -> float:
        picked = self.barks.pick(category) if category != "bar" else None
        if picked is None:
            return 0.0
        return await self._send_clip(*picked)

    async def _send_clip(self, clip_id: str, text: str) -> float:
        self.utterances += 1
        uid = f"u-{self.utterances:04d}"
        await self.hub.send(FLETCHER_SPEECH, {"utterance_id": uid, "text": text, "clip_id": clip_id,
                                              "sample_rate": self.cfg.sample_rate})
        log.info("bark %s: %s", clip_id, text)
        if self.player:
            self.player.play_wav(self.barks.path(clip_id))
        return self.barks.duration(clip_id)

    # -- the cutoff ------------------------------------------------------------
    async def cutoff(self, facts: B.Facts, level: int) -> None:
        t_cut = now()
        await self.send_state("furious", 1.0, "fist_cutoff", "drummer")
        await self.hub.send(SESSION_COMMAND, {"action": "stop", "from_bar": facts.from_bar})
        bark_end = t_cut + (await self.bark("stop") + 0.15 if self.cfg.cutoff_bark else 0.0)
        log.info("CUTOFF #%d at bar %d: %s since bar %d (sev %.2f); restart from bar %d",
                 facts.cutoff_number, facts.current_bar, facts.kind, facts.since_bar, facts.severity, facts.from_bar)
        log.info("fact sheet:\n%s", facts.sheet())

        draft = self.draft
        hit = draft is not None and draft.compatible(facts, level)
        self.stats["speculative_hits"].append(hit)
        if not hit:
            self._cancel_draft()
            draft = Draft(self.writer, facts, level).start()
        else:
            log.info("using speculative draft (%s)", "ready" if draft.done.is_set() else "still writing")
        self.draft = None

        if not await draft.wait_first(self.cfg.llm_first_sentence_timeout_s):
            log.warning("no line from Claude in time (%s); using a canned line", draft.error or "timeout")
            draft.cancel()
            draft = Draft(self.writer, facts, level, canned=True).start()
            await draft.wait_first(1.0)

        speak_at = max(t_cut + self.cfg.stare_s, bark_end)
        spoke = await self.speak(draft, t_cut, speak_at, facts, level)
        if not spoke:  # voice down: barks only
            await asyncio.sleep(max(0.0, speak_at - now()))
            if not self.cfg.cutoff_bark:
                await asyncio.sleep(await self.bark("stop") + 0.2)
            d2 = await self._send_clip(*self.barks.bar_clip(facts.from_bar))
            self.speech_end = now() + d2 + self.cfg.quest_latency_s
        await draft.done.wait()
        if draft.text:
            self.writer.recent.append(strip_tags(draft.text))

        await asyncio.sleep(max(0.0, self.speech_end + self.cfg.restart_gap_s - now()))
        await self.send_state("irritated", self.brain.anger, "point", "drummer")
        await self.hub.send(SESSION_COMMAND, {"action": "restart", "from_bar": facts.from_bar})
        self.stats["cutoffs"].append(facts.from_bar)
        log.info("restart from bar %d", facts.from_bar)
        await self.apply(self.brain.on_restarted(facts.from_bar))

    async def speak(self, draft: Draft, t_cut: float, speak_at: float, facts: B.Facts, level: int) -> bool:
        """Stream the draft through the first voice engine that works, starting no
        earlier than speak_at (the glare, or the end of the cutoff bark)."""
        for engine in self.voices:
            self.utterances += 1
            uid = f"u-{self.utterances:04d}"
            index, total, first_at, carry = 0, 0, None, b""
            audio = bytearray()
            # Gesture cues come out of the text here; each sentence's cues fire on
            # the first audio chunk after the previous sentence's TURN marker.
            # Engines without markers fire them as the sentence goes to the voice.
            cues: list[list[str]] = []
            turns, fired, marks = 0, 0, getattr(engine, "marks_turns", False)

            async def cleaned(uid=uid, cues=cues, marks=marks):
                held: list[str] = []
                since, last = 0, None
                async for s in merge_short(draft.iter_sentences()):
                    text, names = split_gestures(s)
                    held += names
                    if not text:
                        continue
                    since = 0 if held else since + 1
                    if since >= AUTO_EVERY:
                        held.append(auto_gesture(text, last, self.writer.rng))
                        since = 0
                    last = held[-1] if held else last
                    cues.append(held)
                    held = []
                    if not marks and first_at_box[0] is not None:
                        await self.send_gestures(cues[-1], uid, index_box[0])
                    yield text
                if held:
                    await self.send_gestures(held, uid, index_box[0])

            first_at_box, index_box = [None], [0]
            gen = engine.speak(cleaned())
            try:
                async for pcm in gen:
                    if pcm is TURN:
                        turns += 1
                        continue
                    if first_at is None:
                        await asyncio.sleep(max(0.0, speak_at - now()))  # the glare
                        text = strip_tags(draft.text)
                        await self.send_state("furious", 1.0, "lean_in", "drummer")
                        await self.hub.send(FLETCHER_SPEECH, {"utterance_id": uid, "text": text, "stream": True,
                                                              "sample_rate": self.cfg.sample_rate})
                        first_at = now()
                        ms = (first_at - t_cut) * 1000
                        self.stats["first_audio_ms"].append(ms)
                        log.info("first audio %.0f ms after cutoff (%s): %s", ms, engine.name, text)
                        early, first_at_box[0] = len(cues), first_at  # later cues: cleaned() sends them
                        if not marks:
                            for c in cues[:early]:
                                await self.send_gestures(c, uid, 0)
                    while marks and fired < len(cues) and fired <= turns:
                        await self.send_gestures(cues[fired], uid, index)
                        fired += 1
                    pcm = carry + pcm
                    if len(pcm) % 2:
                        pcm, carry = pcm[:-1], pcm[-1:]
                    else:
                        carry = b""
                    if not pcm:
                        continue
                    await self.hub.send(FLETCHER_AUDIO, {"utterance_id": uid, "index": index,
                                                         "pcm_b64": base64.b64encode(pcm).decode(), "final": False})
                    if self.player:
                        self.player.play_pcm(pcm)
                    audio += pcm
                    index += 1
                    index_box[0] = index
                    total += len(pcm)
            except (TTSError, Exception) as e:
                log.warning("voice %s failed: %r", engine.name, e)
            finally:
                await gen.aclose()
            if first_at is not None:
                await self.hub.send(FLETCHER_AUDIO, {"utterance_id": uid, "index": index, "pcm_b64": "",
                                                     "final": True, "text": strip_tags(draft.text)})
                seconds = total / 2 / self.cfg.sample_rate
                self.speech_end = first_at + seconds + self.cfg.quest_latency_s
                log.info("line: %.1f s of audio in %d chunks: %s", seconds, index, draft.text)
                if engine.name != "tone":
                    self._save_line(bytes(audio), engine.name, draft.text, facts, level,
                                    self.stats["first_audio_ms"][-1])
                return True
        return False

    def _save_line(self, pcm: bytes, engine: str, text: str, facts: B.Facts, level: int, first_ms: float) -> None:
        """Keep every voiced tirade (wav + one index.jsonl row) so they can be
        listened to and reviewed later. Never let this break a cutoff."""
        if not self.cfg.lines_dir:
            return
        try:
            d = Path(self.cfg.lines_dir)
            d.mkdir(parents=True, exist_ok=True)
            name = f"{time.strftime('%Y%m%d-%H%M%S')}_c{facts.cutoff_number}_{facts.kind}_L{level}.wav"
            with wave.open(str(d / name), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(self.cfg.sample_rate)
                w.writeframes(pcm)
            row = {"file": name, "text": text, "engine": engine, "level": level, "kind": facts.kind,
                   "from_bar": facts.from_bar, "seconds": round(len(pcm) / 2 / self.cfg.sample_rate, 2),
                   "first_audio_ms": round(first_ms)}
            with (d / "index.jsonl").open("a") as fh:
                fh.write(json.dumps(row) + "\n")
            log.info("saved line to %s", d / name)
        except OSError as e:
            log.warning("could not save line: %r", e)


def setup_logging(level: str) -> None:
    logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO),
                        format="%(asctime)s.%(msecs)03d %(name)-17s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("websockets").setLevel(logging.WARNING)
    for noisy in ("httpx", "httpx2", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    logging.Formatter.converter = time.localtime
