"""ElevenLabs voice. Each engine turns an async stream of sentences into an
async stream of raw PCM (16-bit mono little-endian, cfg.sample_rate).

DialogueVoice  Eleven v4 Turbo over the Text to Dialogue WebSocket. Performs
               audio tags. One connection is opened when a run starts and
               kept warm with keep_alive, so a cutoff never pays setup time.
               The Text to Speech WebSocket refuses v4 models.
FlashVoice     Flash v2.5 over the Text to Speech WebSocket, per utterance.
               Fallback only: it would read tags aloud, so we strip them.
ToneVoice      --dry-run: a quiet hum the length of the line, no network.

Protocol notes (checked Oct 2026): voice id goes in the first message, text
goes in `inputs`, flush after every sentence or the server waits for ~40
characters, replies are snake_case with is_final_audio_for_turn after each
flush; there is no stop message, close the socket to interrupt; the server
drops a socket after 20 s of silence, so send keep_alive every ~15 s.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import math
import struct
from typing import AsyncIterator
from urllib.parse import urlencode

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from .config import Config
from .text import strip_tags

log = logging.getLogger("fletcher.voice")


class TTSError(Exception):
    pass


class DialogueVoice:
    URL = "wss://api.elevenlabs.io/v1/text-to-dialogue/stream-input"
    name = "v4"

    def __init__(self, cfg: Config, model: str | None = None):
        self.cfg = cfg
        self.model = model or cfg.tts_model
        self.ws = None
        self._lock = asyncio.Lock()
        self._ka_task: asyncio.Task | None = None
        self._last_send = 0.0

    async def _connect(self) -> None:
        params = urlencode({"model_id": self.model, "output_format": f"pcm_{self.cfg.sample_rate}",
                            "language_code": "en"})
        self.ws = await connect(f"{self.URL}?{params}",
                                additional_headers={"xi-api-key": self.cfg.elevenlabs_api_key},
                                max_size=None, open_timeout=5)
        await self._send({"voices": [self.cfg.elevenlabs_voice_id],
                          "voice_settings": {"stability": self.cfg.tts_stability}})
        if self._ka_task is None or self._ka_task.done():
            self._ka_task = asyncio.create_task(self._keepalive())
        log.info("voice socket open (%s)", self.model)

    async def _send(self, obj: dict) -> None:
        await self.ws.send(json.dumps(obj))
        self._last_send = asyncio.get_running_loop().time()

    async def _keepalive(self) -> None:
        while True:
            await asyncio.sleep(5)
            if self.ws is None or self._lock.locked():
                continue
            if asyncio.get_running_loop().time() - self._last_send > 14:
                try:
                    await self._send({"keep_alive": True})
                except Exception:
                    self.ws = None

    async def warm(self) -> None:
        async with self._lock:
            if self.ws is None:
                try:
                    await self._connect()
                except Exception as e:
                    log.warning("voice warm-up failed: %r", e)
                    self.ws = None

    async def close(self) -> None:
        if self.ws is not None:
            try:
                await self._send({"close_socket": True})
                await self.ws.close()
            except Exception:
                pass
        self.ws = None

    async def speak(self, sentences: AsyncIterator[str]) -> AsyncIterator[bytes]:
        async with self._lock:
            if self.ws is None:
                try:
                    await self._connect()
                except Exception as e:
                    self.ws = None
                    raise TTSError(f"connect failed: {e!r}") from e
            ws = self.ws
            loop = asyncio.get_running_loop()
            state = {"sent": 0}

            async def sender() -> None:
                first = True
                async for s in sentences:
                    s = s.strip()
                    if not s:
                        continue
                    await self._send({"inputs": [{"text": s + " ", "voice_id": self.cfg.elevenlabs_voice_id,
                                                  "new_turn": first}], "flush": True})
                    first = False
                    state["sent"] += 1

            sender_task = asyncio.create_task(sender())
            recv_task: asyncio.Task | None = None
            finals = 0
            got_audio = False
            begin = last_rx = loop.time()
            try:
                while True:
                    if sender_task.done():
                        if sender_task.exception():
                            raise sender_task.exception()
                        if finals >= state["sent"]:
                            break
                    if recv_task is None:
                        recv_task = asyncio.ensure_future(ws.recv())
                    waiting = {recv_task} if sender_task.done() else {recv_task, sender_task}
                    done, _ = await asyncio.wait(waiting, timeout=0.5, return_when=asyncio.FIRST_COMPLETED)
                    if recv_task in done:
                        raw, recv_task = recv_task.result(), None
                        last_rx = loop.time()
                        msg = json.loads(raw)
                        if msg.get("error"):
                            raise TTSError(f"{msg.get('error')}: {msg.get('message')}")
                        if msg.get("audio"):
                            got_audio = True
                            yield base64.b64decode(msg["audio"])
                        if msg.get("is_final_audio_for_turn"):
                            finals += 1
                    elif not done:
                        idle = loop.time() - last_rx
                        if sender_task.done() and got_audio and idle > 1.5:
                            log.debug("no final marker, assuming the line is finished")
                            break
                        if loop.time() - begin > 12 or (not got_audio and idle > 6):
                            raise TTSError("timed out waiting for audio")
            except (ConnectionClosed, OSError) as e:
                self.ws = None
                raise TTSError(f"socket closed: {e!r}") from e
            except TTSError:
                await self._drop()
                raise
            finally:
                if recv_task is not None:
                    recv_task.cancel()
                if not sender_task.done():
                    sender_task.cancel()

    async def _drop(self) -> None:
        ws, self.ws = self.ws, None
        if ws is not None:
            try:
                await ws.close()
            except Exception:
                pass


class FlashVoice:
    URL = "wss://api.elevenlabs.io/v1/text-to-speech/{voice}/stream-input"
    name = "flash"

    def __init__(self, cfg: Config):
        self.cfg = cfg

    async def warm(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def speak(self, sentences: AsyncIterator[str]) -> AsyncIterator[bytes]:
        params = urlencode({"model_id": self.cfg.tts_fallback_model,
                            "output_format": f"pcm_{self.cfg.sample_rate}"})
        url = self.URL.format(voice=self.cfg.elevenlabs_voice_id) + "?" + params
        try:
            ws = await connect(url, additional_headers={"xi-api-key": self.cfg.elevenlabs_api_key},
                               max_size=None, open_timeout=5)
        except Exception as e:
            raise TTSError(f"flash connect failed: {e!r}") from e

        async def sender() -> None:
            await ws.send(json.dumps({"text": " ", "voice_settings": {"stability": 0.4, "similarity_boost": 0.8}}))
            async for s in sentences:
                s = strip_tags(s)
                if s:
                    await ws.send(json.dumps({"text": s + " ", "flush": True}))
            await ws.send(json.dumps({"text": ""}))  # end of input

        sender_task = asyncio.create_task(sender())
        got_audio = False
        try:
            while True:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=8)
                except ConnectionClosed:
                    break
                except asyncio.TimeoutError as e:
                    raise TTSError("flash timed out") from e
                msg = json.loads(raw)
                if msg.get("error"):
                    raise TTSError(f"{msg.get('error')}: {msg.get('message')}")
                if msg.get("audio"):
                    got_audio = True
                    yield base64.b64decode(msg["audio"])
                if msg.get("isFinal"):
                    break
            if sender_task.done() and sender_task.exception():
                raise TTSError(f"flash send failed: {sender_task.exception()!r}")
            if not got_audio:
                raise TTSError("flash returned no audio")
        finally:
            if not sender_task.done():
                sender_task.cancel()
            try:
                await ws.close()
            except Exception:
                pass


class ToneVoice:
    """Dry run: a soft hum, roughly as long as the words would take to say."""
    name = "tone"

    def __init__(self, cfg: Config):
        self.cfg = cfg

    async def warm(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def speak(self, sentences: AsyncIterator[str]) -> AsyncIterator[bytes]:
        sr = self.cfg.sample_rate
        async for s in sentences:
            words = max(1, len(strip_tags(s).split()))
            seconds = 0.33 * words + 0.25
            await asyncio.sleep(0.12)  # pretend network + model latency
            yield tone_pcm(seconds, sr)


def tone_pcm(seconds: float, sr: int, hz: float = 165.0, amp: float = 0.08) -> bytes:
    n = int(seconds * sr)
    out = bytearray()
    for i in range(n):
        env = min(1.0, i / (0.02 * sr), (n - i) / (0.05 * sr))
        out += struct.pack("<h", int(32767 * amp * env * math.sin(2 * math.pi * hz * i / sr)))
    return bytes(out)


def build_voices(cfg: Config) -> list:
    """Engines to try in order for a generated line."""
    if cfg.dry_run:
        return [ToneVoice(cfg)]
    if cfg.tts_engine == "none" or not (cfg.elevenlabs_api_key and cfg.elevenlabs_voice_id):
        return []
    if cfg.tts_engine == "flash":
        return [FlashVoice(cfg)]
    return [DialogueVoice(cfg), FlashVoice(cfg)]
