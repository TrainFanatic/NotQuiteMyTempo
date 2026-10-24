"""Render a timestamped drum note file (`time_s  midi  name` per line) straight to a WAV file,
using recorded acoustic drum samples.

  python test_file_with_audio.py out/metallica-master_of_puppets.drums.txt
  python test_file_with_audio.py notes.txt -o check.wav --start 60 --end 90
  python test_file_with_audio.py notes.txt --layer 5            # hit everything harder

Samples: "AVL Drumkits - Black Pearl" by Glen MacArthur (CC BY-SA), a Pearl kit with Sabian/Zildjian
cymbals, five velocity layers per drum. It's downloaded to samples/ on first run.

Each sample is trimmed to its transient, so the hit starts at exactly round(time_s * sample_rate).
Only numpy is needed; WAVs are read and written with the standard library.
"""

import argparse
import sys
import tarfile
import tempfile
import urllib.request
import wave
from functools import lru_cache
from pathlib import Path

import numpy as np

SAMPLE_RATE = 44100
TAIL_S = 3.0  # room after the last hit so cymbals can ring out
ONSET_THRESHOLD = 0.02  # fraction of a sample's peak that marks where the hit begins

KIT_NAME = "AVLDrumkits-BlackPearl-2023"
KIT_URL = ("https://downloads.sourceforge.net/project/hydrogen/Sound%20Libraries/"
           "Main%20sound%20libraries/AVLDrumkits-BlackPearl-2023.h2drumkit")
DEFAULT_KIT_DIR = Path(__file__).resolve().parent / "samples" / KIT_NAME
DEFAULT_LAYER = 4  # of 5; the format has no velocities, so every hit uses one dynamic level

# Our note number (GM, plus Guitar Pro's 91-98) -> (kit sample prefix, options).
# The kit numbers its own files and only partly follows GM above 47, hence the explicit table.
#   pitch: resample factor (the kit has two toms; the other GM toms are pitched from them)
#   choke: seconds before the cymbal is grabbed and faded out
#   layer: fixed velocity layer instead of the default
KIT_MAP = {
    31: ("35-JojoMayer-2-StickClick", {}),
    35: ("36-Pearl-22-Kick", {}),
    36: ("36-Pearl-22-Kick", {}),
    37: ("37-Pearl-14-SideStick", {}),
    38: ("38-Pearl-14-Snare", {}),
    39: ("39-HandClap", {}),
    40: ("40-Pearl-14-SnareEdge", {}),
    91: ("38-Pearl-14-Snare", {"layer": 5}),  # rim shot: hardest snare hit
    41: ("41-Pearl-16-FloorTom", {"pitch": 0.89}),
    43: ("41-Pearl-16-FloorTom", {}),
    45: ("45-Pearl-12-Tom", {"pitch": 0.84}),
    47: ("45-Pearl-12-Tom", {"pitch": 0.94}),
    48: ("45-Pearl-12-Tom", {}),
    50: ("45-Pearl-12-Tom", {"pitch": 1.12}),
    42: ("42-Sabian-13-HatClosed", {}),
    44: ("44-Sabian-13-HatPedal", {}),
    46: ("46-Sabian-13-HatSemi", {}),
    92: ("46-Sabian-13-HatSemi", {"choke": 0.18}),  # half-open: semi-open hat, damped sooner
    49: ("49-SabianAA-16-Crash", {}),
    97: ("50-SabianAA-16-CrashChoke", {}),
    57: ("57-SabianAAX-17-Crash", {}),
    98: ("57-SabianAAX-17-Crash", {"choke": 0.15}),
    51: ("51-SabianAAX-20-Ride", {}),
    59: ("51-SabianAAX-20-Ride", {}),
    93: ("59-SabianAAX-20-RideShank", {}),  # ride edge: struck with the stick shank
    94: ("52-SabianAAX-20-RideChoke", {}),
    53: ("53-SabianAAX-20-RideBell", {}),
    52: ("62-Wuhan-18-ChinaCrash", {}),
    96: ("62-Wuhan-18-ChinaCrash", {"choke": 0.15}),
    55: ("55-Zildjian-10-Splash", {}),
    95: ("55-Zildjian-10-Splash", {"choke": 0.1}),
    54: ("54-Tambourine", {}),
    56: ("56-Cowbell", {}),
    69: ("61-Maraca", {}),  # cabasa
    70: ("61-Maraca", {}),
}


# ---- kit download & samples ----------------------------------------------------------------

def ensure_kit(kit_dir=DEFAULT_KIT_DIR):
    kit_dir = Path(kit_dir)
    if kit_dir.is_dir() and any(kit_dir.glob("*.wav")):
        return kit_dir
    if kit_dir != DEFAULT_KIT_DIR:
        raise SystemExit(f"No WAV samples in {kit_dir}")
    print(f"Downloading drum samples ({KIT_NAME}, ~16 MB)...", file=sys.stderr)
    kit_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / "kit.tar.gz"
        req = urllib.request.Request(KIT_URL, headers={"User-Agent": "Wget/1.21"})
        with urllib.request.urlopen(req, timeout=120) as resp:
            archive.write_bytes(resp.read())
        with tarfile.open(archive) as tar:
            tar.extractall(kit_dir.parent, filter="data")  # refuses absolute/../ paths and links
    if not any(kit_dir.glob("*.wav")):
        raise SystemExit(f"Download finished but no samples found in {kit_dir}")
    return kit_dir


def read_wav(path):
    """Mono float array from a 16- or 24-bit PCM WAV, resampled to SAMPLE_RATE if needed."""
    with wave.open(str(path)) as w:
        width, channels, rate = w.getsampwidth(), w.getnchannels(), w.getframerate()
        raw = w.readframes(w.getnframes())
    if width == 2:
        x = np.frombuffer(raw, "<i2").astype(np.float64) / 32768
    elif width == 3:
        b = np.frombuffer(raw, np.uint8).reshape(-1, 3).astype(np.int32)
        x = ((b[:, 0] | b[:, 1] << 8 | b[:, 2] << 16) << 8 >> 8) / 8388608
    else:
        raise SystemExit(f"{path}: only 16/24-bit PCM WAVs are supported")
    x = x.reshape(-1, channels).mean(axis=1)
    return resample(x, rate / SAMPLE_RATE) if rate != SAMPLE_RATE else x


def resample(x, factor):
    """Linear-interpolation resample; factor > 1 raises pitch (and shortens the sound)."""
    n = int(len(x) / factor)
    return np.interp(np.arange(n) * factor, np.arange(len(x)), x)


def trim_to_onset(x):
    """Drop the near-silence recorded before the hit so it sounds exactly at its timestamp."""
    loud = np.flatnonzero(np.abs(x) >= ONSET_THRESHOLD * np.abs(x).max())
    if not len(loud):
        return x
    i = loud[0]
    # back up (at most 1 ms) to the preceding zero crossing so the cut doesn't click
    lo = max(0, i - SAMPLE_RATE // 1000)
    crossings = np.flatnonzero(np.diff(np.signbit(x[lo:i + 1])))
    return x[lo + crossings[-1] + 1:] if len(crossings) else x[i:]


def choke(x, after_s, fade_s=0.03):
    n = int(after_s * SAMPLE_RATE)
    if n >= len(x):
        return x
    fade = np.exp(-np.arange(len(x) - n) / (fade_s / 5 * SAMPLE_RATE))
    out = x.copy()
    out[n:] *= fade
    return out[: n + int(fade_s * SAMPLE_RATE)]


def _sample_file(kit_dir, prefix, layer):
    layered = kit_dir / f"{prefix}-{layer}.wav"
    if layered.exists():
        return layered
    single = kit_dir / f"{prefix}.wav"  # e.g. chokes, which have one layer
    if single.exists():
        return single
    options = sorted(kit_dir.glob(f"{prefix}-*.wav"))
    if not options:
        raise SystemExit(f"Kit is missing samples for {prefix} in {kit_dir}")
    return options[min(layer, len(options)) - 1]


@lru_cache(maxsize=None)
def sound_for(midi, kit_dir=DEFAULT_KIT_DIR, layer=DEFAULT_LAYER):
    """The trimmed, ready-to-mix sample for a note number. Unmapped numbers fall back to the
    nearest-sounding kit piece: 35-81 percussion we don't have uses the side stick, else the
    stick click, so every hit stays audible."""
    kit_dir = Path(kit_dir)
    prefix, opts = KIT_MAP.get(midi, ("37-Pearl-14-SideStick" if 35 <= midi <= 81 else "35-JojoMayer-2-StickClick", {}))
    x = trim_to_onset(read_wav(_sample_file(kit_dir, prefix, opts.get("layer", layer))))
    if "pitch" in opts:
        x = resample(x, opts["pitch"])
    if "choke" in opts:
        x = choke(x, opts["choke"])
    return x


@lru_cache(maxsize=None)
def lead_in(midi, kit_dir=DEFAULT_KIT_DIR, layer=DEFAULT_LAYER):
    """Samples between the zero-crossing start and the hit itself (<= 1 ms); placed early by this."""
    x = sound_for(midi, kit_dir, layer)
    loud = np.flatnonzero(np.abs(x) >= ONSET_THRESHOLD * np.abs(x).max())
    return int(loud[0]) if len(loud) else 0


def unmapped(notes):
    return sorted({m for _, m in notes if m not in KIT_MAP})


# ---- rendering -----------------------------------------------------------------------------

def read_notes(path):
    """Returns [(time_s, midi)], skipping `#` comments and blank lines."""
    notes = []
    for lineno, line in enumerate(Path(path).read_text().splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split()
        try:
            notes.append((float(fields[0]), int(fields[1])))
        except (IndexError, ValueError):
            raise SystemExit(f"{path}:{lineno}: expected `time_s midi [name]`, got {line!r}")
    return notes


def render(notes, start=0.0, end=None, kit_dir=DEFAULT_KIT_DIR, layer=DEFAULT_LAYER):
    """Mixes each note's sample so its hit lands at sample round((time - start) * SAMPLE_RATE).

    Hits before `start` (and their ringing tails) are dropped, so a leading grace note at a
    negative time is cut from the default render unless you pass a negative --start.
    """
    notes = [(t, m) for t, m in notes if t >= start and (end is None or t < end)]
    last = max((t for t, _ in notes), default=start)
    length = int(((end if end is not None else last + TAIL_S) - start) * SAMPLE_RATE) + 1
    out = np.zeros(length)
    for t, midi in notes:
        s = sound_for(midi, Path(kit_dir), layer)
        i = round((t - start) * SAMPLE_RATE) - lead_in(midi, Path(kit_dir), layer)
        if i < 0:  # only possible for a hit within 1 ms of the render start
            s, i = s[-i:], 0
        n = min(len(s), length - i)
        out[i:i + n] += s[:n]
    peak = np.abs(out).max()
    if peak > 0.98:
        out *= 0.98 / peak  # global normalize: keeps relative levels and timing untouched
    return out


def write_wav(path, x):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes())


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("notes_file", help="file with `time_s midi name` lines")
    p.add_argument("-o", "--output", help="output .wav (default: next to the input)")
    p.add_argument("--start", type=float, default=0.0, help="render from this time (s); output starts at 0")
    p.add_argument("--end", type=float, help="stop rendering at this time (s)")
    p.add_argument("--layer", type=int, default=DEFAULT_LAYER, choices=range(1, 6),
                   help=f"velocity layer 1 (soft) to 5 (hard); default {DEFAULT_LAYER}")
    p.add_argument("--kit", default=str(DEFAULT_KIT_DIR), help="folder with the kit's WAV samples")
    args = p.parse_args(argv)

    notes = read_notes(args.notes_file)
    if not notes:
        raise SystemExit(f"No notes found in {args.notes_file}")
    kit_dir = ensure_kit(Path(args.kit))
    missing = unmapped(notes)
    if missing:
        print(f"note: no kit sample for {missing}; using a stand-in sound", file=sys.stderr)
    audio = render(notes, args.start, args.end, kit_dir, args.layer)
    out = Path(args.output) if args.output else Path(args.notes_file).with_suffix(".wav")
    write_wav(out, audio)
    used = sum(1 for t, _ in notes if t >= args.start and (args.end is None or t < args.end))
    msg = f"Wrote {used} hits ({len(audio) / SAMPLE_RATE:.1f}s of audio) to {out}"
    skipped_early = sum(1 for t, _ in notes if t < args.start)
    if skipped_early and args.start == 0:
        msg += f"  ({skipped_early} hit(s) before 0s dropped; use --start -0.1 to include them)"
    print(msg)


if __name__ == "__main__":
    main()
