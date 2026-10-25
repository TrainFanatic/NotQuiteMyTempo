"""Orchestrator: hub in -> brain -> barks / tirade / voice -> hub out.

Cutoff sequence (the only place an LLM is involved):
  1. fletcher.state fist_cutoff + session.command stop + "Stop!" bark (instant)
  2. glare for cfg.stare_s (or until the bark ends); meanwhile the line is already drafted
     (speculatively, before the cutoff) or drafting now
  3. fletcher.speech, then fletcher.audio chunks as the voice streams
  4. wait until his last word has played, then session.command restart
Body cues (cues.py): barks carry their own; after the line a stare, the restart instruction and
the count-off (fingers on 5-6-7-8, "five" = the restart). While an utterance or that sequence
plays, the brain's plain action changes are held back and sent when it is over.
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
from . import countoff as CO
from .cues import (COUNTOFF, COUNTOFF_LEAD_BEATS, DEFAULT_BPM, FIST, MOVE_ACTION, WALK_IN, BarkCues, bark_lead, countoff_t0,
                   gesture_move, post_line)
from .contract import (FLETCHER_AUDIO, FLETCHER_SPEECH, FLETCHER_STATE, JUDGMENT_ALERT, JUDGMENT_BAR,
                       JUDGMENT_NOTE, SESSION_COMMAND, SESSION_RESULT, SESSION_STATE, now)
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
        self.bark_cues = BarkCues()
        self.player = LocalPlayer(cfg.sample_rate) if cfg.play_local else None
        self.draft: Draft | None = None
        self.cut_task: asyncio.Task | None = None
        self.utterances = 0
        self.speech_end = 0.0
        self.last_uid, self.last_len_s = "", 0.0
        self.busy_until = 0.0  # an utterance (or the post-line sequence) plays until then
        self.held: B.SetState | None = None
        self._last_gesture: tuple[str, str] | None = None  # (utterance, move) last cued in a line
        self._restart_t0: float | None = None  # t0 of the last restart we sent (its count_in is ours)
        self.stats: dict[str, list] = {"first_audio_ms": [], "speculative_hits": [], "cutoffs": []}

    # -- main loop -----------------------------------------------------------
    async def run(self) -> None:
        log.info("config: %s", self.cfg.describe())
        log.info("tirades: %s | voice: %s", "Claude " + self.cfg.llm_model if self.writer.live else "canned lines",
                 " -> ".join(v.name for v in self.voices) or "barks only")
        await asyncio.gather(self.hub.run(), self._consume(), self._ticker())

    async def _consume(self) -> None:
        handlers = {SESSION_STATE: self.brain.on_session_state, JUDGMENT_NOTE: self.brain.on_note,
                    JUDGMENT_BAR: self.brain.on_bar, JUDGMENT_ALERT: self.brain.on_alert,
                    SESSION_RESULT: self.brain.on_result}
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
                start = msg["type"] == SESSION_STATE and self._session_change(msg["data"])
                effects = fn(msg["data"], msg.get("_rx", now()))
                await self.apply(effects)
                if start:
                    await self.count_in(msg["data"])
            except Exception:
                log.exception("failed handling %s", msg.get("type"))

    async def _ticker(self) -> None:
        while True:
            await asyncio.sleep(0.1)
            try:
                if self.held and not self._busy():
                    e, self.held = self.held, None
                    await self.send_state(e.mood, e.anger, e.action, e.look_at)
                await self.apply(self.brain.tick(now()))
            except Exception:
                log.exception("tick failed")

    # -- effects -------------------------------------------------------------
    async def apply(self, effects: list) -> None:
        for e in effects:
            if isinstance(e, B.SetState):
                if self._busy():
                    self.held = e  # don't stomp on a line in progress; the ticker sends it later
                else:
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
            elif isinstance(e, B.EndSong):
                if self.cut_task and not self.cut_task.done():
                    continue
                await self.end_song(e.reason, e.bar)

    async def end_song(self, reason: str, bar: int) -> None:
        """The drummer isn't playing (or the song is over for him): stop the band and send the
        session back to the menu with a result, instead of raging at silence or restarting."""
        log.info("ENDING the song at bar %d: %s", bar, reason)
        self._cancel_draft()
        await self.apply(self.brain.on_ended())
        await self.hub.send(SESSION_COMMAND, {"action": "quit", "reason": reason})

    # -- the session controller (hub/session.py) ------------------------------
    def _session_change(self, d: dict) -> bool:
        """A new song (count_in with reason "start", not the count_in answering our own restart)
        or the end of the session: drop the cutoff in progress and reset the mood. Returns True
        for a song start, so the caller counts the drummer in once the brain has seen it."""
        st = d.get("state")
        own = (self._restart_t0 is not None and isinstance(d.get("t0"), (int, float))
               and abs(float(d["t0"]) - self._restart_t0) < 1e-3)
        start = st == "count_in" and not own and (
            d.get("reason") == "start" or ("reason" not in d and "song_time_at_t0" in d))
        if start or st == "idle":
            if self.cut_task and not self.cut_task.done():
                self.cut_task.cancel()
                log.info("session %s: cutoff sequence abandoned", "restarted" if start else "ended")
            self._cancel_draft()
            self.brain.reset()
            self._restart_t0, self.busy_until, self.held = None, 0.0, None
        return start

    async def count_in(self, d: dict) -> None:
        """Song start: Terence counts the drummer in on the controller's t0, with the same count-off
        cue as after a tirade (finger from t0 - 5 beats, the words on beats -4..-1)."""
        t0, bpm = d.get("t0"), float(d.get("bpm") or self.brain.bpm or DEFAULT_BPM)
        if not isinstance(t0, (int, float)):
            return
        bar = int(d.get("from_bar") or d.get("bar") or 1)
        lead = COUNTOFF_LEAD_BEATS * 60.0 / bpm
        log.info("song %s from bar %d: count-off t0 in %.2f s (%.0f bpm)", d.get("song_id"), bar, t0 - now(), bpm)
        if self.cfg.start_bark and bar == 1 and t0 - lead - now() > 1.5:
            await self.bark("top")
        uid = self.last_uid or f"u-{self.utterances:04d}"
        await self.send_cue(COUNTOFF, uid, 2, t0=round(float(t0), 4), bpm=round(bpm, 3),
                            clips=self._countoff_clips(bpm))
        self.busy_until = max(self.busy_until, float(t0))

    def _busy(self) -> bool:
        return now() < self.busy_until or (self.cut_task is not None and not self.cut_task.done())

    def _cancel_draft(self) -> None:
        if self.draft:
            self.draft.cancel()
        self.draft = None

    async def send_state(self, mood: str, anger: float, action: str, look_at: str = "drummer", **extra) -> None:
        await self.hub.send(FLETCHER_STATE, {"mood": mood, "anger": round(anger, 2), "action": action,
                                             "look_at": look_at, **extra})
        anchor = next((f"{k} {extra[k]}" for k in ("at_index", "at_s", "after_s", "t0") if k in extra), "")
        log.info("state %-9s anger %.2f action %s%s", mood, anger, action,
                 f" gesture {extra['gesture']} {anchor} p{extra.get('priority', 1)}" if "gesture" in extra else "")

    async def send_cue(self, gesture: str, uid: str, priority: int = 1, **anchor) -> None:
        """A Terence move (or GESTURES name) tied to utterance `uid` by one anchor."""
        mood, action, look = self.brain.last_sent or ("calm", "idle", "drummer")
        mood = "furious" if self.brain.phase == "cutoff" else mood
        await self.send_state(mood, self.brain.anger, MOVE_ACTION.get(gesture, action), look,
                              gesture=gesture, utterance_id=uid, priority=priority, **anchor)

    async def send_gestures(self, names: list[str], uid: str, at_index: int) -> None:
        """Body cues from the line. `action` stays a contract value; the optional
        `gesture` names the specific move and `at_index` the fletcher.audio chunk
        of this utterance where it should start. Names are resolved to Terence move ids here:
        without cfg.closeups, to moves he plays where he stands (cues.STATIONARY)."""
        for g in names:
            action, look = GESTURES.get(g, ("lean_in", "drummer"))
            move = gesture_move(g, self.cfg.closeups)
            if self._last_gesture == (uid, move):
                continue  # the same move twice in a row: it would restart (a thrown prop pops back)
            self._last_gesture = (uid, move)
            await self.send_state("furious", 1.0, action, look, gesture=move, utterance_id=uid, at_index=at_index)

    async def bark(self, category: str, cues: bool = True) -> float:
        picked = self.barks.pick(category) if category != "bar" else None
        if picked is None:
            return 0.0
        return await self._send_clip(*picked, category=category if cues else None)

    async def _send_clip(self, clip_id: str, text: str, category: str | None = "bar",
                         lead_s: float = 0.0) -> float:
        """Send a bark (its cues first). Returns how long until its last sample is heard,
        including the lead: a cue with a negative at_s makes the headset hold the clip back
        (capped at bark_lead_max_s) so the move's key frame can land on the word."""
        self.utterances += 1
        uid = f"u-{self.utterances:04d}"
        cues = (self.bark_cues.get(clip_id, category, self.brain.phase == "playing", self.brain.bpm)
                if category else [])
        lead_s = max(lead_s, bark_lead([a for _, a, _ in cues], self.cfg.bark_lead_max_s))
        dur = self.barks.duration(clip_id) + lead_s
        self.last_uid, self.last_len_s = uid, dur
        self.busy_until = max(self.busy_until, now() + dur + self.cfg.quest_latency_s)
        for g, at_s, prio in cues:
            await self.send_cue(g, uid, prio, at_s=at_s)
        await self.hub.send(FLETCHER_SPEECH, {"utterance_id": uid, "text": text, "clip_id": clip_id,
                                              "sample_rate": self.cfg.sample_rate})
        log.info("bark %s: %s", clip_id, text)
        if self.player:
            self.player.play_wav(self.barks.path(clip_id))
        return dur

    # -- the cutoff ------------------------------------------------------------
    async def cutoff(self, facts: B.Facts, level: int) -> None:
        t_cut = now()
        stop = self.barks.pick("stop") if self.cfg.cutoff_bark else None
        if stop:  # the fist rides on the "Stop!" bark (sent next): its fist frame on the word
            uid = f"u-{self.utterances + 1:04d}"
            fist_at = self.bark_cues.fist_at_s(stop[0], self.brain.bpm)
            await self.send_state("furious", 1.0, "fist_cutoff", "drummer", gesture=FIST,
                                  utterance_id=uid, at_s=fist_at, priority=2)
            if self.cfg.closeups:  # walk in once the fist is done, so he is at the kit for the line
                await self.send_state("furious", 1.0, "lean_in", "drummer", gesture=WALK_IN,
                                      utterance_id=uid, after_s=0.0, priority=1)
        else:
            await self.send_state("furious", 1.0, "fist_cutoff", "drummer", priority=2)
        await self.hub.send(SESSION_COMMAND, {"action": "stop", "from_bar": facts.from_bar})
        bark_end = t_cut
        if stop:
            lead = bark_lead([fist_at], self.cfg.cutoff_lead_max_s)
            bark_end += await self._send_clip(*stop, category=None, lead_s=lead) + 0.15
            log.info("cutoff bark %s held %.0f ms so the fist lands on the word", stop[0], lead * 1000)
        log.info("CUTOFF #%d at bar %d: %s since bar %d (sev %.2f); %s",
                 facts.cutoff_number, facts.current_bar, facts.kind, facts.since_bar, facts.severity,
                 "LAST ONE, the song ends" if facts.final else f"restart from bar {facts.from_bar}")
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
        if facts.final:  # the restart loop guard: no count-off, the song ends after his line
            if not spoke:
                await asyncio.sleep(max(0.0, speak_at - now()))
                self.speech_end = now() + await self.bark("again", cues=False) + self.cfg.quest_latency_s
            await draft.done.wait()
            if draft.text:
                self.writer.recent.append(strip_tags(draft.text))
            await asyncio.sleep(max(0.0, self.speech_end + self.cfg.post_stare_s - now()))
            await self.end_song("cutoffs", facts.current_bar)
            return
        if not spoke:  # voice down: barks only
            await asyncio.sleep(max(0.0, speak_at - now()))
            if not self.cfg.cutoff_bark:
                await asyncio.sleep(await self.bark("stop", cues=False) + 0.2)
            d2 = await self._send_clip(*self.barks.bar_clip(facts.from_bar), category=None)
            self.speech_end = now() + d2 + self.cfg.quest_latency_s
        bpm = self.brain.bpm or DEFAULT_BPM
        beat = 60.0 / bpm
        stare = self.cfg.post_stare_s
        if self.cfg.closeups:  # he may be at the kit: give him time to walk home first
            stare += self.cfg.closeup_return_s * 120.0 / bpm
        seq = post_line(bpm, stare, self.cfg.restart_move)
        for g, anchor, val, prio in seq:
            await self.send_cue(g, self.last_uid, prio, **{anchor: val})
        # One clock for the count-off: t0 = the restart bar's downbeat (laptop monotonic). The
        # headset maps it with hub.to_local_time and plays the finger and the words from it.
        t0 = countoff_t0(max(self.speech_end, now()), bpm, stare)
        await self.send_cue(COUNTOFF, self.last_uid, 2, t0=round(t0, 4), bpm=round(bpm, 3),
                            clips=self._countoff_clips(bpm))
        restart_at = t0 - self.cfg.count_in_beats * beat
        self.busy_until = max(self.busy_until, t0)
        log.info("post-line: %s; count-off t0 in %.2f s, finger at line end + %.2f s (%.0f bpm)",
                 ", ".join(f"{g} {a} {v}" for g, a, v, _ in seq), t0 - now(), stare, bpm)
        await draft.done.wait()
        if draft.text:
            self.writer.recent.append(strip_tags(draft.text))

        await asyncio.sleep(max(0.0, restart_at - now()))
        # t0 lets the bridge start the bar on the count-off downbeat even if this arrives late
        self._restart_t0 = round(t0, 4)
        await self.hub.send(SESSION_COMMAND, {"action": "restart", "from_bar": facts.from_bar,
                                              "t0": round(t0, 4)})
        self.stats["cutoffs"].append(facts.from_bar)
        log.info("restart from bar %d", facts.from_bar)
        await self.apply(self.brain.on_restarted(facts.from_bar))

    def _countoff_clips(self, bpm: float) -> list[str]:
        """The count-off word clip ids (fletcher/countoff), one per beat before t0."""
        try:
            return [c for _, c in CO.schedule(min(max(bpm, CO.BPM_MIN), CO.BPM_MAX), self.cfg.beats_per_bar,
                                              1, self.cfg.countoff_style)]
        except ValueError:
            return [c for _, c in CO.schedule(min(max(bpm, CO.BPM_MIN), CO.BPM_MAX))]

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
                        await self.send_state("furious", 1.0, "lean_in", "drummer", priority=0)
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
                self.last_uid, self.last_len_s = uid, seconds
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
