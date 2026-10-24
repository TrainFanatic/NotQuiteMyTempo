"""Shared native ABI, recording validation, and canonical note IO."""

import csv
import ctypes as ct
import hashlib
import json
import math
import os
from pathlib import Path
import struct
import sys
import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
RATE, FEATURES, MAX_CLASSES = 48000, 256, 16
FAMILIES = ("kick", "snare", "hihat", "tom", "crash", "ride")
MIDIS = (36, 38, 42, 47, 49, 51)
NAMES = dict(
    zip(MIDIS, ("kick", "snare", "hihat_closed", "tom_low_mid", "crash", "ride"))
)
GROUPS = (
    (35, 36),
    (37, 38, 40, 91),
    (42, 44, 46, 92),
    (41, 43, 45, 47, 48, 50),
    (49, 52, 55, 57, 95, 96, 97, 98),
    (51, 53, 59, 93, 94),
)
NORMALIZE = {m: target for group, target in zip(GROUPS, MIDIS) for m in group}


class Candidate(ct.Structure):
    _fields_ = [
        ("sample", ct.c_int64),
        ("time_s", ct.c_double),
        ("available_sample", ct.c_int64),
        ("class_count", ct.c_uint32),
        ("family_mask", ct.c_uint32),
        ("midi", ct.c_int32 * MAX_CLASSES),
        ("probability", ct.c_float * MAX_CLASSES),
        ("features", ct.c_float * FEATURES),
    ]


class Stats(ct.Structure):
    _fields_ = [
        (name, ct.c_uint64)
        for name in ("candidates", "rejected", "gaps", "discarded_pending")
    ]


def library_path():
    if os.environ.get("DRUM_TRANSCRIBER_LIB"):
        return Path(os.environ["DRUM_TRANSCRIBER_LIB"])
    suffix = (
        ".dylib" if sys.platform == "darwin" else ".dll" if os.name == "nt" else ".so"
    )
    name = ("" if os.name == "nt" else "lib") + "drum_transcriber" + suffix
    candidates = [ROOT / "build" / name, ROOT / "build" / "Release" / name]
    return next((p for p in candidates if p.exists()), candidates[0])


class Native:
    def __init__(self, model=None, origin=0.0):
        path = library_path()
        if not path.exists():
            raise FileNotFoundError(f"Build the native library first; missing {path}")
        self.lib = ct.CDLL(str(path))
        self.lib.dt_create.argtypes = [ct.c_void_p, ct.c_size_t]
        self.lib.dt_create.restype = ct.c_void_p
        self.lib.dt_destroy.argtypes = [ct.c_void_p]
        self.lib.dt_destroy.restype = None
        self.lib.dt_reset.argtypes = [ct.c_void_p, ct.c_double]
        self.lib.dt_process.argtypes = [
            ct.c_void_p,
            ct.POINTER(ct.c_float),
            ct.c_size_t,
            ct.c_int64,
        ]
        self.lib.dt_finish.argtypes = [ct.c_void_p]
        self.lib.dt_poll_candidate.argtypes = [ct.c_void_p, ct.POINTER(Candidate)]
        self.lib.dt_get_stats.argtypes = [ct.c_void_p, ct.POINTER(Stats)]
        blob = Path(model).read_bytes() if isinstance(model, (str, Path)) else model
        buf = ct.create_string_buffer(blob) if blob is not None else None
        self.handle = self.lib.dt_create(buf, len(blob) if blob is not None else 0)
        if not self.handle:
            raise ValueError("Invalid/incompatible model (or allocation failure)")
        try:
            self.reset(origin)
        except Exception:
            self.close()
            raise

    def reset(self, origin=0):
        self._check(self.lib.dt_reset(self.handle, origin))

    @staticmethod
    def _check(status):
        if status < 0:
            raise ValueError(
                f"Native transcriber status {status}; see drum_transcriber.h"
            )
        return status

    def drain(self):
        result = []
        while True:
            c = Candidate()
            status = self._check(self.lib.dt_poll_candidate(self.handle, ct.byref(c)))
            if not status:
                return result
            result.append(c)

    def process(self, audio, first_sample):
        a = np.ascontiguousarray(audio, dtype=np.float32)
        if a.ndim != 1:
            raise ValueError("Native input must be mono")
        status = self._check(
            self.lib.dt_process(
                self.handle,
                a.ctypes.data_as(ct.POINTER(ct.c_float)),
                len(a),
                first_sample,
            )
        )
        return self.drain(), status

    def finish(self):
        self._check(self.lib.dt_finish(self.handle))
        return self.drain()

    def stats(self):
        s = Stats()
        self._check(self.lib.dt_get_stats(self.handle, ct.byref(s)))
        return {name: getattr(s, name) for name, _ in s._fields_}

    def close(self):
        if self.handle:
            self.lib.dt_destroy(self.handle)
            self.handle = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def read_audio(path):
    audio, rate = sf.read(path, dtype="float32", always_2d=True)
    if rate != RATE or audio.shape[1] != 1:
        raise ValueError(
            f"{path}: prepare a 48 kHz mono WAV with prepare_recording.py first"
        )
    a = audio[:, 0]
    if not len(a) or not np.all(np.isfinite(a)) or np.max(np.abs(a)) > 1:
        raise ValueError(f"{path}: empty, nonfinite, or out-of-range audio")
    return a


def extract(audio, model=None, chunk=480, origin=0):
    if not 1 <= chunk <= 4800:
        raise ValueError("chunk must be 1..4800")
    with Native(model, origin) as core:
        result = []
        for start in range(0, len(audio), chunk):
            candidates, _ = core.process(audio[start : start + chunk], start)
            result.extend(candidates)
        result.extend(core.finish())
        return result


def events(candidates):
    return sorted(
        (c.time_s, c.midi[k])
        for c in candidates
        if c.time_s >= 0
        for k in range(c.class_count)
        if c.family_mask & (1 << k)
    )


def note_line(t, midi):
    if not math.isfinite(t) or t < 0 or midi not in NAMES:
        raise ValueError(f"Invalid representative note: {t}, {midi}")
    return f"{t:.6f}  {midi}  {NAMES[midi]}\n"


def write_notes(path, notes, description="live transcription"):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    text = f"# {description}\n# time_s  midi  name\n"
    text += "".join(note_line(t, m) for t, m in sorted(set(notes)))
    p.write_text(text, encoding="utf-8", newline="\n")


def read_notes(path):
    result = []
    for number, line in enumerate(Path(path).read_text().splitlines(), 1):
        if not line.strip() or line.startswith("#"):
            continue
        try:
            t, m, *_ = line.split()
            t = float(t)
            m = int(m)
            if not math.isfinite(t) or t < 0 or not 0 <= m <= 127:
                raise ValueError()
        except ValueError as e:
            raise ValueError(f"{path}:{number}: invalid note") from e
        result.append((t, m))
    return sorted(set(result))


def load_kit(path):
    kit = json.loads(Path(path).read_text())
    if not isinstance(kit.get("pieces"), dict) or not kit["pieces"]:
        raise ValueError("kit needs a nonempty pieces mapping")
    if any(v not in FAMILIES for v in kit["pieces"].values()):
        raise ValueError("Unknown drum family in kit")
    return kit


def read_labels(path, kit, duration):
    result = []
    with Path(path).open(newline="") as f:
        rows = csv.DictReader(f)
        if rows.fieldnames != ["time_s", "piece_id"]:
            raise ValueError(f"{path}: expected time_s,piece_id")
        for row in rows:
            t = float(row["time_s"])
            piece = row["piece_id"]
            if not math.isfinite(t) or not 0 <= t < duration:
                raise ValueError(f"{path}: annotation outside recording")
            if piece not in kit["pieces"]:
                raise ValueError(f"{path}: unknown piece {piece}")
            result.append((t, MIDIS[FAMILIES.index(kit["pieces"][piece])]))
    return sorted(set(result))


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def export_model(mean, scale, weights, bias, thresholds, midis=MIDIS):
    mean, scale, weights, bias, thresholds = map(
        lambda x: np.asarray(x, dtype="<f4"), (mean, scale, weights, bias, thresholds)
    )
    n = len(midis)
    if (
        mean.shape != (FEATURES,)
        or scale.shape != (FEATURES,)
        or weights.shape != (n, FEATURES)
        or bias.shape != (n,)
        or thresholds.shape != (n,)
    ):
        raise ValueError("Wrong model shapes")
    blob = (
        struct.pack("<8s5I", b"DTRN0001", 1, RATE, FEATURES, n, 7680)
        + mean.tobytes()
        + scale.tobytes()
    )
    for k, midi in enumerate(midis):
        blob += (
            struct.pack("<Iff", midi, float(bias[k]), float(thresholds[k]))
            + weights[k].tobytes()
        )
    return blob


def peak_rss_bytes():
    """Whole desktop process high-water RSS, not isolated native/Quest memory."""
    try:
        import resource
    except ImportError:
        return None
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(rss if sys.platform == "darwin" else rss * 1024)
