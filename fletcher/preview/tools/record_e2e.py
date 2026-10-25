"""Run hub.e2e (dry run: no API calls) and record every hub envelope with its receive time.
With --line <cached wav>, Fletcher's voice and draft are swapped for a cached real line:
the draft yields that line's sentences and the voice streams its PCM per sentence with
TURN markers (like Eleven v4), so main.py's own code makes the gesture cues and timing."""
import argparse, asyncio, json, sys, time, wave
sys.path.insert(0, "/Users/aaronwang/NotQuiteMyTempo")
import numpy as np
from websockets.asyncio.client import connect
from hub import e2e as E
import fletcher.main as FM
from fletcher.text import SentenceSplitter
from fletcher.voice import TURN
from fletcher.main import setup_logging

ap = argparse.ArgumentParser()
ap.add_argument("out"); ap.add_argument("--line"); ap.add_argument("--port", type=int, default=8811)
ap.add_argument("--chunk-s", type=float, default=0.3); ap.add_argument("--speedup", type=float, default=2.0)
A = ap.parse_args()
LINES = "/Users/aaronwang/NotQuiteMyTempo/fletcher/lines"

if A.line:
    row = next(json.loads(l) for l in open(f"{LINES}/index.jsonl") if json.loads(l)["file"] == A.line)
    with wave.open(f"{LINES}/{A.line}") as w:
        SR = w.getframerate(); PCM = np.frombuffer(w.readframes(w.getnframes()), np.int16)
    sp = SentenceSplitter(); SENTS = sp.feed(row["text"] + " ") + sp.flush()

    class CachedDraft(FM.Draft):
        async def _run(self):
            for s in SENTS:
                await asyncio.sleep(0.05)
                self._push(s)
            self.done.set(); self._changed.set()   # no bar append: the audio is fixed
        def compatible(self, facts, level): return True

    def segments(got):
        """Split PCM per sentence: the cut for each boundary goes at the pause (>=100 ms
        below -35 dB re peak) nearest its character-proportional position."""
        from fletcher.text import strip_tags
        x = PCM.astype(float) / 32768; hop = int(0.01 * SR)
        env = np.sqrt(np.convolve(x * x, np.ones(hop) / hop, "same"))[::hop]
        quiet = env < 10 ** (-35 / 20) * env.max()
        runs, i = [], 0
        while i < len(quiet):
            if quiet[i]:
                j = i
                while j < len(quiet) and quiet[j]: j += 1
                if j - i >= 10 and i > 0 and j < len(quiet): runs.append((i + j) // 2 * hop)
                i = j
            else: i += 1
        lens = [len(strip_tags(g)) for g in got]; tot = sum(lens); acc = 0; cuts = []
        for L in lens[:-1]:
            acc += L; want = acc / tot * len(PCM)
            cuts.append(min(runs, key=lambda c: abs(c - want)) if runs else int(want))
        b = [0] + sorted(set(cuts)) + [len(PCM)]
        return [PCM[b[k]:b[k + 1]] for k in range(len(b) - 1)]

    class CachedVoice:
        name, marks_turns = "cached", True
        def __init__(self, cfg): pass
        async def warm(self): pass
        async def close(self): pass
        async def speak(self, sentences):
            got = [s async for s in sentences]
            segs = segments(got)
            print("CACHED sentences:", json.dumps([[s, round(len(g) / SR, 2)] for s, g in zip(got, segs)]))
            await asyncio.sleep(0.4)
            n = int(A.chunk_s * SR)
            for g in segs:
                for i in range(0, len(g), n):
                    yield g[i:i + n].tobytes()
                    await asyncio.sleep(A.chunk_s / A.speedup)
                yield TURN
    FM.Draft = CachedDraft
    FM.build_voices = lambda cfg: [CachedVoice(cfg)]

async def recorder(fh):
    while True:
        try:
            async with connect(f"ws://localhost:{A.port}", max_size=None) as ws:
                async for raw in ws:
                    fh.write(json.dumps({"rx": time.monotonic(), "msg": json.loads(raw)}) + "\n")
        except Exception:
            await asyncio.sleep(0.05)

async def main():
    with open(A.out, "w", buffering=1) as fh:
        rec = asyncio.create_task(recorder(fh))
        rc = await E.e2e(5, 0.07, 1.0, False, A.port)
        rec.cancel()
    return rc

setup_logging("INFO")
sys.exit(asyncio.run(main()))
