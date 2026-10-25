"""Live audio sources for transcribe_wav.py --input: the laptop microphone and the Quest's
mic.pcm stream on the team hub. Each yields 48 kHz mono float32 blocks with an absolute
first-sample index (the core's input contract) and maps a sample index to the laptop clock,
time.monotonic() seconds, the hub's one clock (fletcher.contract.now(), CONTRACT.md).

Clock estimates are running minima that may creep upward by `creep` s/s (100 ppm) so slow
crystal drift between two clocks is followed: every delay (scheduling, buffering, network)
only ever makes an observation late, so the smallest recent one is the best estimate."""

import base64
import json
import math
import queue
import threading
import time
import numpy as np
from common import RATE

MAX_BLOCK = 4800  # DT_MAX_BLOCK


class RunningMin:
    """min(previous + creep * elapsed, new observation)."""

    def __init__(self, creep=1e-4):
        self.creep, self.value, self.at = creep, None, None

    def update(self, observed, at):
        if self.value is None:
            self.value = observed
        else:
            self.value = min(self.value + self.creep * max(0.0, at - self.at), observed)
        self.at = at
        return self.value


class Resampler:
    """Streaming windowed-sinc resampler to 48 kHz on absolute sample indices: output k is the
    input at position k * rate / 48000, so indices on both sides stay exact across blocks.
    Adds `half` input samples of lookahead (0.36 ms at 44.1 kHz). Identity at 48 kHz."""

    def __init__(self, rate, half=16, beta=8.0):
        self.rate, self.half, self.beta = int(rate), half, beta
        self.cutoff = 0.95 * 0.5 * min(self.rate, RATE) / self.rate  # cycles per input sample
        self.reset(0)

    def reset(self, first_input):
        """Start a new segment (after a gap) whose first input sample is `first_input`."""
        self.buf = np.zeros(0, dtype=np.float64)
        self.buf_start = first_input
        self.next_out = -(-first_input * RATE // self.rate)  # ceil: first output inside it
        self.segment_in = first_input

    def out_index(self, input_index):
        return -(-input_index * RATE // self.rate)

    def process(self, block, first_input):
        """Append a contiguous block; returns (48 kHz samples, index of the first one)."""
        if self.rate == RATE:
            return np.asarray(block, dtype=np.float32), first_input
        if first_input != self.buf_start + len(self.buf):
            self.reset(first_input)
        self.buf = np.concatenate([self.buf, np.asarray(block, dtype=np.float64)])
        last_in = self.buf_start + len(self.buf) - 1
        # the last output whose taps up to base + half are all present
        last_out = ((last_in - self.half) * RATE) // self.rate
        start = self.next_out
        if last_out < start:
            return np.zeros(0, dtype=np.float32), start
        k = np.arange(start, last_out + 1, dtype=np.int64)
        base = (k * self.rate) // RATE
        frac = (k * self.rate - base * RATE) / RATE
        taps = np.arange(-self.half + 1, self.half + 1)
        idx = base[:, None] + taps[None, :]
        d = taps[None, :] - frac[:, None]  # input position minus output position
        w = 2 * self.cutoff * np.sinc(2 * self.cutoff * d)
        w *= np.i0(self.beta * np.sqrt(np.clip(1 - (d / self.half) ** 2, 0, 1))) / np.i0(
            self.beta
        )
        w /= w.sum(axis=1, keepdims=True)
        local = idx - self.buf_start
        valid = (local >= 0) & (idx >= self.segment_in)  # zeros before a segment start
        x = np.where(valid, self.buf[np.clip(local, 0, len(self.buf) - 1)], 0.0)
        y = np.sum(x * w, axis=1)
        self.next_out = last_out + 1
        keep_from = ((self.next_out * self.rate) // RATE) - self.half
        drop = max(0, keep_from - self.buf_start)
        self.buf, self.buf_start = self.buf[drop:], self.buf_start + drop
        return np.clip(y, -1.0, 1.0).astype(np.float32), start


def split(block, first):
    for i in range(0, len(block), MAX_BLOCK):
        yield block[i : i + MAX_BLOCK], first + i


class LaptopMic:
    """sounddevice input at 48 kHz mono float32. A block's first sample is dated with
    PortAudio's inputBufferAdcTime when the host gives one (mapped from stream time to
    time.monotonic() through currentTime), else arrival time minus the block length; then
    minus `latency_s` (default: the stream's reported input latency). On macOS CoreAudio the
    ADC time is only ~1.8 ms before the callback's buffer end and leaves out the device's
    input latency (41.5 ms on a MacBook Pro mic): a speaker->mic loopback with PortAudio's
    output DAC time as truth put the uncorrected dates 44 ms late, the corrected ones 2.5 ms
    late. Calibrate with `--mic-latency-ms` on other hardware."""

    def __init__(self, device=None, blocksize=480, sd=None, creep=1e-4, latency_s=None):
        if sd is None:
            import sounddevice as sd
        self.q = queue.Queue()
        self.position = 0
        self.overflows = 0
        self.adc_blocks = 0
        self.blocks_seen = 0
        self.epoch = RunningMin(creep)
        self.stream = sd.InputStream(
            samplerate=RATE,
            channels=1,
            dtype="float32",
            blocksize=blocksize,
            device=device,
            latency="low",
            callback=self._callback,
        )
        reported = float(np.atleast_1d(self.stream.latency)[0])
        self.latency = reported if latency_s is None else latency_s
        self.stream.start()

    def _callback(self, indata, frames, time_info, status):
        arrived = time.monotonic()
        adc = getattr(time_info, "inputBufferAdcTime", 0.0) or 0.0
        current = getattr(time_info, "currentTime", 0.0) or 0.0
        if adc > 0 and current > 0 and 0 <= current - adc < 1.0:
            first_t, exact = arrived - (current - adc) - self.latency, True
        else:
            first_t, exact = arrived - frames / RATE - self.latency, False
        self.q.put(
            (indata[:, 0].copy(), first_t, exact, bool(getattr(status, "input_overflow", False)))
        )

    def blocks(self, stop=lambda: False):
        while not stop():
            try:
                block, first_t, exact, overflow = self.q.get(timeout=0.5)
            except queue.Empty:
                continue
            first = self.position
            if overflow and self.epoch.value is not None:
                self.overflows += 1  # samples were lost: date the block from its timestamp
                first = max(first, round((first_t - self.epoch.value) * RATE))
            self.blocks_seen += 1
            self.adc_blocks += exact
            self.epoch.update(first_t - first / RATE, first_t)
            self.position = first + len(block)
            yield from split(np.clip(block, -1.0, 1.0), first)

    def time_of(self, sample):
        return sample / RATE + self.epoch.value

    def stats(self):
        return {
            "input": "laptop mic",
            "input_latency_s": self.latency,
            "blocks": self.blocks_seen,
            "blocks_with_adc_time": self.adc_blocks,
            "overflows": self.overflows,
        }

    def close(self):
        self.stream.stop()
        self.stream.close()


class MicPcmAssembler:
    """Turns mic.pcm frames (CONTRACT.md) into in-order 48 kHz blocks on the laptop clock.

    Order and gaps: frames are released in first_sample order. A frame that starts after the
    expected sample with the next seq is a capture gap on the headset (discarded frames) and
    goes through at once; with a missing seq it is held up to `reorder_s` for the missing frame,
    then released as a gap. Late duplicates are dropped. A gap restarts the core's acoustic
    state (dt_process accepts forward gaps). A stream that jumps backwards (app restarted) is
    appended after the old one with fresh clock estimates (a restart: the rate changes, seq
    starts again at 1, or local_t jumps back more than 1 s).

    Clock: device time of sample s = s / rate + epoch (running min of local_t - first/rate).
    Laptop - device offset: A = running min of (arrival - envelope t) overestimates it by the
    one-way delay; if the headset reports its FletcherHub.clock_offset (B = min(local - laptop
    t), overestimating the reverse by the other way's delay), (A - B) / 2 cancels a symmetric
    delay (NTP style). Jitter = arrival - (envelope t + offset) above its minimum."""

    def __init__(self, reorder_s=0.06, creep=1e-4):
        self.reorder_s, self.creep = reorder_s, creep
        self.held = {}  # first_sample -> (frame, deadline)
        self.base48 = 0  # this stream's sample 0 in the output index
        self.end48 = 0  # next output sample index
        self.counts = dict(frames=0, late=0, gaps=0, lost_s=0.0, restarts=0, reordered=0)
        self.delays = []  # arrival - mapped send time, s
        self._new_stream()

    def _new_stream(self, rate=None):
        self.rate = rate
        self.expected = None  # next first_sample of this stream (device rate)
        self.last_seq = None
        self.last_local_t = -math.inf
        self.epoch = RunningMin(self.creep)
        self.a = RunningMin(self.creep)
        self.b = None
        self.resampler = Resampler(rate) if rate else None

    @property
    def offset(self):
        if self.a.value is None:
            return None
        return (self.a.value - self.b) / 2 if self.b is not None else self.a.value

    def time_of(self, sample48):
        return sample48 / RATE + self.epoch.value + self.offset

    def feed(self, envelope, rx):
        """One hub frame (parsed envelope) received at laptop time rx. Returns blocks."""
        if envelope.get("type") != "mic.pcm":
            return []
        d = envelope.get("data") or {}
        try:
            seq, rate, first = int(d["seq"]), int(d["rate"]), int(d["first_sample"])
            local_t, t = float(d["local_t"]), float(envelope.get("t", d["local_t"]))
            pcm = np.frombuffer(base64.b64decode(d["pcm_b64"]), dtype="<i2")
        except (KeyError, TypeError, ValueError):
            return []
        if int(d.get("channels", 1)) != 1 or not len(pcm) or rate <= 0:
            return []
        out = []
        restart = self.rate is not None and (
            rate != self.rate
            or (seq == 1 and self.last_seq is not None and self.last_seq > 1)
            or local_t < self.last_local_t - 1.0  # the headset's clock started over
        )
        self.last_local_t = local_t
        if restart:
            out += self._flush_all()
            self.counts["restarts"] += 1
            self.base48 = self.end48 + RATE // 10  # a 0.1 s gap between the two streams
        if self.rate is None or restart:
            self._new_stream(rate)
        self.counts["frames"] += 1
        self.a.update(rx - t, rx)
        if isinstance(d.get("clock_offset"), (int, float)) and math.isfinite(d["clock_offset"]):
            self.b = float(d["clock_offset"])
        self.delays.append(rx - t - self.offset)
        frame = (seq, first, local_t, pcm)
        if self.expected is not None and first < self.expected:
            self.counts["late"] += 1
            return out
        if (
            self.expected is None
            or first == self.expected
            or (self.last_seq is not None and seq == self.last_seq + 1)
        ):
            if self.held and first == self.expected:
                self.counts["reordered"] += 1  # it arrived after a later frame
            out += self._release(frame)
            out += self._drain_held()
        else:
            self.held[first] = (frame, rx + self.reorder_s)
        return out

    def due(self, now):
        """Release held frames whose missing predecessor did not come in time."""
        out = []
        while self.held:
            first = min(self.held)
            if self.held[first][1] > now:
                break
            out += self._release(self.held.pop(first)[0])
            out += self._drain_held()
        return out

    def _flush_all(self):
        out = []
        while self.held:
            out += self._release(self.held.pop(min(self.held))[0])
        return out

    def _drain_held(self):
        out = []
        while self.expected in self.held:
            out += self._release(self.held.pop(self.expected)[0])
        return out

    def _release(self, frame):
        seq, first, local_t, pcm = frame
        if self.expected is not None and first > self.expected:
            self.counts["gaps"] += 1
            self.counts["lost_s"] += (first - self.expected) / self.rate
        self.expected = first + len(pcm)
        self.last_seq = seq
        # device seconds of this stream's sample 0, mapped into the output index
        self.epoch.update(local_t - first / self.rate - self.base48 / RATE, local_t)
        block, first48 = self.resampler.process(pcm.astype(np.float32) / 32768.0, first)
        first48 += self.base48
        self.end48 = max(self.end48, first48 + len(block))
        return list(split(block, first48)) if len(block) else []

    def stats(self):
        d = np.asarray(self.delays) if self.delays else np.zeros(1)
        jitter = d - d.min()
        return {
            "input": "hub mic.pcm",
            "rate": self.rate,
            **{k: round(v, 4) if isinstance(v, float) else v for k, v in self.counts.items()},
            "laptop_minus_device_s": self.offset,
            "symmetric_offset": self.b is not None,
            "jitter_ms_p50": float(np.percentile(jitter, 50) * 1000),
            "jitter_ms_p95": float(np.percentile(jitter, 95) * 1000),
            "jitter_ms_max": float(jitter.max() * 1000),
        }


class HubMic:
    """Reads mic.pcm from the hub (a WebSocket client like any other; reconnects every
    second). Frames are timestamped on arrival in the receiving thread."""

    def __init__(self, url, src="kevin", reorder_s=0.06, idle_exit_s=None):
        self.url, self.src, self.idle_exit_s = url, src, idle_exit_s
        self.assembler = MicPcmAssembler(reorder_s)
        self.q = queue.Queue()
        self.closed = threading.Event()
        self.ever_received = False
        threading.Thread(target=self._receive, daemon=True).start()

    def _receive(self):
        from websockets.sync.client import connect

        while not self.closed.is_set():
            try:
                with connect(self.url, max_size=None) as ws:
                    self.ws = ws
                    for raw in ws:
                        self.q.put((raw, time.monotonic()))
            except Exception:
                pass
            if self.closed.wait(1.0):
                break

    def blocks(self, stop=lambda: False):
        a = self.assembler
        last_rx = time.monotonic()
        while not stop() and not self.closed.is_set():
            try:
                raw, rx = self.q.get(timeout=0.02)
            except queue.Empty:
                raw = None
            now = time.monotonic()
            if raw is not None:
                try:
                    msg = json.loads(raw)
                except ValueError:
                    msg = None
                if isinstance(msg, dict) and msg.get("type") == "mic.pcm":
                    self.ever_received, last_rx = True, rx
                    for block in a.feed(msg, rx):
                        yield block
            for block in a.due(now):
                yield block
            if (
                self.idle_exit_s is not None
                and self.ever_received
                and now - last_rx > self.idle_exit_s
            ):
                break
        for block in a._flush_all():
            yield block

    def time_of(self, sample):
        return self.assembler.time_of(sample)

    def stats(self):
        return self.assembler.stats()

    def close(self):
        self.closed.set()
        ws = getattr(self, "ws", None)
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass


def open_input(spec, chunk=480, reorder_s=0.06, idle_exit_s=None, mic_latency_s=None):
    """`mic`, `mic:<device name or index>`, or `hub:ws://host:port`."""
    kind, _, rest = spec.partition(":")
    if kind == "mic":
        device = int(rest) if rest.strip().isdigit() else (rest or None)
        return LaptopMic(device, blocksize=chunk, latency_s=mic_latency_s)
    if kind == "hub":
        return HubMic(rest or "ws://localhost:8765", reorder_s=reorder_s, idle_exit_s=idle_exit_s)
    raise ValueError(f"unknown --input {spec!r}; use mic[:device] or hub:ws://host:port")
