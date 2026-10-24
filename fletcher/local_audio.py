"""Optional: play Fletcher on the laptop speakers (--play-local), handy when
testing without the Quest. Needs `pip install sounddevice`."""

from __future__ import annotations

import logging
import queue
import threading
import wave
from pathlib import Path

log = logging.getLogger("fletcher.local_audio")


class LocalPlayer:
    def __init__(self, sample_rate: int):
        self.sr = sample_rate
        self.q: queue.Queue[bytes | None] = queue.Queue()
        self.ok = False
        try:
            import sounddevice as sd  # noqa: F401
        except Exception as e:
            log.warning("local playback off (sounddevice unavailable: %s)", e)
            return
        self.ok = True
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        import sounddevice as sd
        with sd.RawOutputStream(samplerate=self.sr, channels=1, dtype="int16") as out:
            while True:
                chunk = self.q.get()
                if chunk is None:
                    return
                out.write(chunk)

    def play_pcm(self, pcm: bytes) -> None:
        if self.ok and pcm:
            self.q.put(pcm)

    def play_wav(self, path: Path) -> None:
        if not self.ok or not path.exists():
            return
        with wave.open(str(path), "rb") as w:
            if w.getframerate() != self.sr or w.getnchannels() != 1 or w.getsampwidth() != 2:
                log.warning("skip %s: expected %d Hz mono 16-bit", path.name, self.sr)
                return
            self.q.put(w.readframes(w.getnframes()))
