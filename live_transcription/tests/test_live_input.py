"""Live input (tools/live_input.py, transcribe_wav.py --input): the Quest mic.pcm stream through
a real hub, the laptop mic through a fake sounddevice, and the clock mapping both feed."""

import base64
import json
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
import numpy as np
import pytest
import soundfile as sf
from common import ROOT, RATE, Native
from live_input import LaptopMic, MicPcmAssembler, Resampler

TAKE = ROOT / "data" / "recordings" / "single_snare_01"
DEV = 1000.0  # fake headset clock = laptop clock + DEV


def frame(seq, first, pcm, local_t, t, rate=44100, clock_offset=None):
    data = {
        "seq": seq,
        "rate": rate,
        "channels": 1,
        "first_sample": first,
        "local_t": local_t,
        "pcm_b64": base64.b64encode(np.asarray(pcm, "<i2").tobytes()).decode(),
    }
    if clock_offset is not None:
        data["clock_offset"] = clock_offset
    return {"type": "mic.pcm", "src": "quest_app", "seq": seq, "t": t, "data": data}


def clicks(times, rate, seconds):
    a = np.zeros(int(seconds * rate))
    rng = np.random.default_rng(3)
    for t in times:
        i, n = int(round(t * rate)), int(0.05 * rate)
        a[i : i + n] += 0.5 * rng.standard_normal(n) * np.exp(-np.arange(n) / (0.01 * rate))
    return np.clip(a, -1, 1)


def onsets(blocks, time_of):
    """Native onsets (no model) from (block, first) pairs, on time_of's clock."""
    out = []
    with Native() as core:
        for block, first in blocks:
            cands, _ = core.process(block, first)
            out += [time_of(c.sample) for c in cands]
        out += [time_of(c.sample) for c in core.finish()]
    return out


def test_resampler_is_continuous_across_block_sizes():
    rate = 44100
    x = np.sin(2 * np.pi * 1000 * np.arange(rate) / rate) * 0.5
    r, out, pos = Resampler(rate), [], 0
    for n in [7, 2205, 1, 999, 4096] * 6:
        if pos >= len(x):
            break
        y, first = r.process(x[pos : pos + n], pos)
        assert first == sum(map(len, out))
        out.append(y)
        pos += n
    y = np.concatenate(out)
    k = np.arange(len(y))
    want = np.sin(2 * np.pi * 1000 * k / RATE) * 0.5
    assert np.max(np.abs(y[100:] - want[100:])) < 2e-3


def test_assembler_orders_frames_fills_gaps_and_maps_the_clock():
    rate, n = 44100, 2205  # 50 ms frames at the Quest's 44.1 kHz mix rate
    hits = [0.3, 0.9, 1.6, 2.4]
    pcm = (clicks(hits, rate, 3.0) * 32767).astype("<i2")
    t0 = 500.0  # laptop time of sample 0
    frames = []
    for k in range(len(pcm) // n):
        first, block = k * n, pcm[k * n : (k + 1) * n]
        if k == 10:  # headset discarded 300 frames: seq continues, first_sample jumps
            first, block = first + 300, block[300:]
        send = t0 + (k + 1) * n / rate
        frames.append(
            (
                frame(k + 1, first, block, t0 + first / rate + DEV, send + DEV,
                      clock_offset=DEV + 0.002),
                send + 0.002 + (0.03 if k % 7 == 0 else 0.0),  # 2 ms each way, some jitter
            )
        )
    frames[20], frames[21] = frames[21], frames[20]  # reordered on the way
    del frames[40]  # lost: never arrives
    a, blocks = MicPcmAssembler(reorder_s=0.06), []
    for env, rx in frames:
        blocks += a.feed(env, rx)
        blocks += a.due(rx)
    blocks += a.due(1e9)
    firsts = [f for _, f in blocks]
    assert firsts == sorted(firsts)
    assert a.counts["gaps"] == 2 and a.counts["reordered"] == 1 and a.counts["late"] == 0
    assert a.offset == pytest.approx(-DEV, abs=1e-6)  # symmetric delay cancels
    assert a.stats()["jitter_ms_max"] == pytest.approx(30, abs=0.1)
    got = onsets(blocks, a.time_of)
    for h in hits:
        assert min(abs(g - (t0 + h)) for g in got) < 0.002


def test_assembler_restarted_stream_continues_after_the_old_one():
    a = MicPcmAssembler()
    one = a.feed(frame(1, 0, np.zeros(2400, "<i2"), 10.0, 10.05, rate=48000), 5.05)
    two = a.feed(frame(2, 2400, np.zeros(2400, "<i2"), 10.05, 10.1, rate=48000), 5.1)
    # app restarted: device clock and seq start over
    three = a.feed(frame(1, 0, np.zeros(2400, "<i2"), 0.5, 0.55, rate=48000), 6.0)
    assert [f for _, f in one + two] == [0, 2400]
    assert three[0][1] == 4800 + RATE // 10 and a.counts["restarts"] == 1
    assert a.time_of(three[0][1]) == pytest.approx(6.0 - 0.05, abs=1e-6)


class FakeStream:
    """sounddevice.InputStream stand-in: a thread calls back with 10 ms blocks of `audio` in
    real time, with PortAudio-style stream times (input latency 8 ms, its own time base)."""

    latency = 0.008

    def __init__(self, audio, adc=True, **kw):
        self.audio, self.adc, self.kw = audio, adc, kw
        self.callback, self.blocksize = kw["callback"], kw["blocksize"]
        self.running = False

    def start(self):
        self.running = True
        self.started = time.monotonic()
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        n = self.blocksize
        for k in range(len(self.audio) // n):
            # the block's last sample hits the ADC at started + (k+1)n/RATE; delivered after
            # the input latency plus a little scheduling noise
            due = self.started + (k + 1) * n / RATE + self.latency
            time.sleep(max(0, due - time.monotonic()) + (0.004 if k % 5 == 0 else 0))
            if not self.running:
                return
            now = time.monotonic()
            stream_t = now + 77.0  # PortAudio's stream clock is not time.monotonic()
            # like CoreAudio: the reported ADC time leaves out the device's input latency
            adc = self.started + k * n / RATE + self.latency + 77.0 if self.adc else 0.0
            info = SimpleNamespace(currentTime=stream_t, inputBufferAdcTime=adc)
            status = SimpleNamespace(input_overflow=False)
            self.callback(self.audio[k * n : (k + 1) * n, None], n, info, status)

    def stop(self):
        self.running = False

    def close(self):
        pass


@pytest.mark.parametrize("adc", [True, False])
def test_laptop_mic_dates_blocks_on_the_monotonic_clock(adc):
    hits = [0.25, 0.7, 1.1]
    audio = clicks(hits, RATE, 1.5).astype(np.float32)
    streams = []

    def make(**kw):
        streams.append(FakeStream(audio, adc, **kw))
        return streams[-1]

    mic = LaptopMic(blocksize=480, sd=SimpleNamespace(InputStream=make))
    assert streams[0].kw["samplerate"] == RATE and streams[0].kw["channels"] == 1
    total = len(audio) // 480 * 480
    blocks = mic.blocks(stop=lambda: mic.position >= total)
    got = onsets(blocks, mic.time_of)
    mic.close()
    start = streams[0].started
    errors = [min(abs(g - (start + h)) for g in got) for h in hits]
    # exact ADC times: sub-ms; fallback (arrival - block - latency): scheduling noise only
    assert max(errors) < (0.001 if adc else 0.003), errors
    assert mic.stats()["blocks_with_adc_time"] == (mic.stats()["blocks"] if adc else 0)


def _labels():
    rows = (TAKE / "labels.csv").read_text().split()[1:]
    return [float(r.split(",")[0]) for r in rows]


@pytest.mark.skipif(not (TAKE / "source.wav").exists(), reason="needs the local Quest take")
def test_quest_take_through_the_hub_matches_labels_on_the_laptop_clock(tmp_path):
    """A fake headset streams the real 44.1 kHz Quest take as mic.pcm through hub/server.py in
    real time (50 ms frames, jitter, one reordered pair, one lost frame, one capture gap); the
    transcriber's lines on the laptop clock land on the labels as well as the WAV replay does."""
    websockets = pytest.importorskip("websockets")
    sys.path.insert(0, str(ROOT.parent))
    from fletcher.dev.mini_hub import free_port, run_hub
    from websockets.sync.client import connect
    import asyncio

    seconds = 12.0
    x, rate = sf.read(TAKE / "source.wav", dtype="float32", always_2d=True)
    pcm = np.round(np.clip(x.mean(axis=1)[: int(seconds * rate)], -1, 1) * 32767).astype("<i2")
    port = free_port()
    ready = threading.Event()

    def hub():
        async def go():
            ev = asyncio.Event()
            task = asyncio.create_task(run_hub(port, None, ev))
            await ev.wait()
            ready.set()
            await task

        asyncio.run(go())

    threading.Thread(target=hub, daemon=True).start()
    assert ready.wait(5)
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "tools" / "transcribe_wav.py"), "--input",
         f"hub:ws://127.0.0.1:{port}", "--onsets-only", "--clock", "monotonic",
         "--idle-exit-s", "1.0", "--output", str(tmp_path / "live.txt")],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=ROOT,
    )
    lines = []

    def read():
        for line in proc.stdout:
            lines.append((line, time.monotonic()))

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    time.sleep(1.5)  # transcriber connects
    n, rng = int(0.05 * rate), np.random.default_rng(7)
    t0 = time.monotonic() + 0.2  # laptop time of the take's sample 0
    with connect(f"ws://127.0.0.1:{port}") as ws:
        order = list(range(len(pcm) // n))
        order[50], order[51] = order[51], order[50]
        for k in order:
            if k == 30:
                continue  # lost frame (silence before the first hit)
            first, block = k * n, pcm[k * n : (k + 1) * n]
            if k == 40:
                first, block = first + 500, block[500:]  # headset discarded 500 frames
            ready_at = t0 + (k + 1) * n / rate
            delay = 0.003 + rng.uniform(0, 0.015) + (0.08 if k % 40 == 39 else 0)
            time.sleep(max(0, ready_at + delay - time.monotonic()))
            env = frame(k + 1, first, block, t0 + first / rate + DEV,
                        time.monotonic() - 0.003 + DEV, rate, clock_offset=DEV + 0.003)
            ws.send(json.dumps(env))
    proc.wait(timeout=10)
    reader.join(timeout=2)
    summary = json.loads(proc.stderr.read().strip().splitlines()[-1])
    got = [(float(l.split()[0]), at) for l, at in lines if not l.startswith("#")]
    assert summary["live"]["gaps"] == 2 and summary["live"]["reordered"] == 1
    # the WAV replay of the same take (prepared audio.wav, song clock) as the reference
    wav = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "transcribe_wav.py"), str(TAKE / "audio.wav"),
         "--onsets-only", "--output", str(tmp_path / "wav.txt")],
        capture_output=True, text=True, cwd=ROOT, check=True,
    ).stdout.split("\n")
    ref = [float(l.split()[0]) for l in wav if l and not l.startswith("#")]
    labels = [t for t in _labels() if t < seconds - 0.3]
    latencies, compared = [], 0
    for label in labels:
        want = min(ref, key=lambda r: abs(r - label))
        if abs(want - label) > 0.03:
            continue  # the WAV replay misses it too
        t, at = min(got, key=lambda g: abs(g[0] - (t0 + label)))
        live_err, wav_err = t - (t0 + label), want - label
        # The 44.1 kHz int16 stream resamples slightly differently from audio.wav, so the
        # core may pick a different candidate frame; it must be no further from the label.
        # Systematic: under 1 ms (half the hub's one-way relay delay; the fake's own reverse
        # path is instant, so the symmetric estimate can't cancel it).
        assert abs(live_err) < abs(wav_err) + 0.003, (label, live_err, wav_err)
        latencies.append(at - (t0 + label))
        compared += 1
    assert compared >= 10
    print(f"\nhub path: {compared} labels, hit->stdout p50 {np.median(latencies)*1000:.0f} ms "
          f"max {max(latencies)*1000:.0f} ms, jitter p95 {summary['live']['jitter_ms_p95']:.1f} ms")
