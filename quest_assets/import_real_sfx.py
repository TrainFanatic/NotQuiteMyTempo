#!/usr/bin/env python3
"""Real recorded sound effects for Terence and his props.

Replaces the synthesized versions of the 35 sounds Terence's cues use with real recordings
(the band is silent, so its sounds stay synthesized and unused). Run after gen_sfx.py,
which would otherwise overwrite them:

    python3 gen_sfx.py && python3 import_real_sfx.py

Sources are the original downloads in sfx/sources/ (Freesound HQ previews and BigSoundBank
files, CC0 or CC-BY, see SOURCES below and sfx/CREDITS.md). Each sound is cut from its
source automatically: the envelope's events are found, the chosen one is cut from just
before its attack to where it has decayed (or the next event starts), faded, high-passed,
pitched or layered if listed, and peak-normalized to the manifest's level. Output is dry
48 kHz mono 16-bit PCM starting on the transient, as the cue tables expect.

Decoding uses macOS afconvert. Anything missing, silent or mis-cut raises.
"""

import json
import os
import subprocess
import sys
import tempfile
import wave
from fractions import Fraction

import numpy as np
from scipy import signal

HERE = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.join(HERE, "sfx", "sources")
OUT_DIR = os.path.join(HERE, "sfx")
SR = 48000
PRE_ROLL = 0.003          # kept before the attack
FADE_OUT = 0.015
HIGHPASS_HZ = 40.0
FLOOR_DB = -45.0          # an event ends once it decays this far below its peak


class ImportError_(Exception):
    pass


FS = "https://freesound.org/"
# name: source file, which event ("first", "loudest", "last" or an index into the events
# in time order), longest allowed cut, how slowly it may start (attack window), optional
# pitch factor and layers [(file, event, gain_db, offset_s)], and the credit.
SOURCES = {
    "body_thud": dict(file="body_thud.mp3", event="loudest", max_s=0.45, attack_s=0.05,
                      credit=("Thud / Falling on wooden floor", FS + "people/Breviceps/sounds/447922/", "Breviceps", "CC0")),
    "chair_grab": dict(file="chair_grab.mp3", event="loudest", max_s=0.9, attack_s=0.3,
                       credit=("Unfolding metal chair", FS + "people/SpliceSound/sounds/188219/", "SpliceSound", "CC0")),
    "chair_impact": dict(file="chair_impact.mp3", event="loudest", max_s=1.6, attack_s=0.05,
                         credit=("Chair Thrown, Crash", FS + "people/issalcake/sounds/115918/", "issalcake", "CC0")),
    "clap_1": dict(file="clap_1.mp3", event="loudest", max_s=0.35, attack_s=0.02,
                   credit=("Clap, Single, 4", FS + "people/InspectorJ/sounds/404549/", "InspectorJ", "CC BY 4.0")),
    "clap_2": dict(file="clap_2.mp3", event="loudest", max_s=0.35, attack_s=0.02,
                   credit=("Clap, Single, 17", FS + "people/InspectorJ/sounds/404548/", "InspectorJ", "CC BY 4.0")),
    "clap_3": dict(file="clap_3.mp3", event="loudest", max_s=0.35, attack_s=0.02, pitch=0.94,
                   credit=("HandClap1", FS + "people/Landau00/sounds/463687/", "Landau00", "CC0")),
    "clap_big": dict(file="clap_big.mp3", event="loudest", max_s=0.6, attack_s=0.02,
                     credit=("Single clap", FS + "people/randbsoundbites/sounds/789991/", "randbsoundbites", "CC0")),
    "cloth_rustle_1": dict(file="cloth_rustle_1.mp3", event="loudest", isolate=True, max_s=0.5, highpass_hz=150.0, attack_s=0.2,
                           credit=("Foley - Cotton clothes rustling", FS + "people/khenshom/sounds/481075/", "khenshom", "CC0")),
    "cloth_rustle_2": dict(file="cloth_rustle_2.mp3", event="loudest", max_s=0.65, highpass_hz=150.0, attack_s=0.25,
                           credit=("Clothes Rustling Movement 13_26", FS + "people/Joao_Janz/sounds/493501/", "Joao_Janz", "CC0")),
    "cowbell_hit_1": dict(file="cowbell_hit_1.mp3", event="loudest", isolate=True, max_s=1.0, attack_s=0.02,
                          credit=("cowbell_03", FS + "people/Manjushri/sounds/210101/", "Manjushri", "CC0")),
    "cowbell_hit_2": dict(file="cowbell_hit_2.mp3", event="first", max_s=0.9, attack_s=0.02,
                          credit=("cowbell - one hit dof", FS + "people/SOOTHSAYER_ORCHESTRA/sounds/556434/", "Pieter Hendriks (Soothsayer Orchestra)", "CC0")),
    "cymbal_grab": dict(file="cymbal_grab.mp3", event="first", max_s=0.45, attack_s=0.02,
                        credit=("Meinl Byzance 16 Extra Dry Thin Crash, choked", FS + "people/ankealtd/sounds/696206/", "ankealtd", "CC0")),
    "cymbal_impact": dict(file="cymbal_impact.mp3", event="first", max_s=3.0, attack_s=0.02,
                          credit=("Cymbale 16\" #5", "https://bigsoundbank.com/cymbale-16-5-s2315.html", "Joseph Sardin (BigSoundBank)", "CC0")),
    "door_slam": dict(file="door_slam.wav", event="loudest", max_s=0.9, attack_s=0.05,
                      credit=("Door Slamming #4", "https://bigsoundbank.com/door-slamming-4-s1501.html", "Joseph Sardin (BigSoundBank)", "CC0")),
    "drum_grab": dict(file="drum_grab.mp3", event="loudest", isolate=True, max_s=0.4, attack_s=0.02,
                      credit=("snare tuning foley, 14x6.5 Brass Ludwig", FS + "people/CVLTIV8R/sounds/809830/", "CVLTIV8R", "CC0")),
    # no real drum-drop recording with a usable license exists: a real snare rattle over a
    # real body/floor thud
    "drum_impact": dict(file="drum_impact.mp3", event="loudest", max_s=0.7, attack_s=0.02,
                        layers=[("body_thud.mp3", "loudest", -2.0, 0.0)],
                        credit=("Sounds For Earthquakes - Snare and Drumsticks (+ Breviceps thud)", FS + "people/RutgerMuller/sounds/51116/", "RutgerMuller", "CC0")),
    "fist_slam_stand": dict(file="fist_slam_metal.mp3", event="first", max_s=0.75, attack_s=0.02,
                            layers=[("fist_slam_desk.mp3", "first", -3.0, 0.0)],
                            credit=("Metal Sheet Hit + Desk slam", FS + "people/Stereo%20Surgeon/sounds/262516/", "Stereo Surgeon, HenryHardhat", "CC0")),
    # the downloaded scuff (Al3x-K 848694) is only low thuds with no squeak; until a real
    # squeak is fetched, a heavier step from the same dress-shoe walk as the footsteps
    "foot_scuff": dict(file="foot_steps.mp3", event=24, isolate=True, max_s=0.4, attack_s=0.05,
                       credit=("Footsteps Men's Dress Shoes Hardwood Floor Wood", FS + "people/cjfilmmaker/sounds/328340/", "CJ Johnson (cjfilmmaker)", "CC BY 3.0")),
    # all four steps from one foley walk so they match; indices skip the first steps
    "foot_step_1": dict(file="foot_steps.mp3", event=4, isolate=True, max_s=0.3, attack_s=0.02,
                        credit=("Footsteps Men's Dress Shoes Hardwood Floor Wood", FS + "people/cjfilmmaker/sounds/328340/", "CJ Johnson (cjfilmmaker)", "CC BY 3.0")),
    "foot_step_2": dict(file="foot_steps.mp3", event=9, isolate=True, max_s=0.3, attack_s=0.02,
                        credit=("Footsteps Men's Dress Shoes Hardwood Floor Wood", FS + "people/cjfilmmaker/sounds/328340/", "CJ Johnson (cjfilmmaker)", "CC BY 3.0")),
    "foot_step_3": dict(file="foot_steps.mp3", event=14, isolate=True, max_s=0.3, attack_s=0.02,
                        credit=("Footsteps Men's Dress Shoes Hardwood Floor Wood", FS + "people/cjfilmmaker/sounds/328340/", "CJ Johnson (cjfilmmaker)", "CC BY 3.0")),
    "foot_step_4": dict(file="foot_steps.mp3", event=19, isolate=True, max_s=0.3, attack_s=0.02,
                        credit=("Footsteps Men's Dress Shoes Hardwood Floor Wood", FS + "people/cjfilmmaker/sounds/328340/", "CJ Johnson (cjfilmmaker)", "CC BY 3.0")),
    "foot_stomp": dict(file="foot_stomp.mp3", event="loudest", max_s=0.4, attack_s=0.02,
                       credit=("Foot Stomp", FS + "people/JakeEaton/sounds/559069/", "JakeEaton", "CC0")),
    "paper_flutter": dict(file="paper_flutter.mp3", event="first", max_s=1.0, attack_s=0.15,
                          credit=("Papers Tossed into Air; falling 1", FS + "people/RossBell/sounds/389437/", "RossBell", "CC0")),
    "paper_grab": dict(file="paper_grab.mp3", event="first", max_s=0.4, attack_s=0.05,
                       credit=("Paper Grab", FS + "people/danluxradium/sounds/767489/", "danluxradium", "CC0")),
    "paper_land": dict(file="paper_land.mp3", event="loudest", max_s=0.6, attack_s=0.03,
                       credit=("Papers falling", FS + "people/EmiLeigh/sounds/205911/", "EmiLeigh", "CC0")),
    "slap_1": dict(file="slap_1.mp3", event="loudest", max_s=0.3, attack_s=0.02,
                   credit=("Face slap sound effect", FS + "people/magicjoshua/sounds/704179/", "magicjoshua", "CC0")),
    "slap_2": dict(file="slap_2.mp3", event="loudest", max_s=0.3, attack_s=0.02,
                   credit=("Slap", FS + "people/MrDrPrFr/sounds/337148/", "MrDrPrFr", "CC0")),
    "slap_3": dict(file="slap_3.mp3", event="loudest", max_s=0.3, attack_s=0.02,
                   credit=("Slap in the Face", FS + "people/Ultra-Edward/sounds/815785/", "Ultra-Edward", "CC0")),
    # no music-stand topple recording exists under a usable license: the loudest tripod
    # stand drop over a metal bar landing on wood
    "stand_crash": dict(file="stand_crash.mp3", event="loudest", max_s=1.4, attack_s=0.02,
                        layers=[("stand_crash_bar.mp3", "first", -6.0, 0.0)],
                        credit=("Metallic Clatter (+ Iron Bar Drop onto Wood)", FS + "people/pickanothername/sounds/592588/", "pickanothername, dkudos", "CC0")),
    "stand_rattle": dict(file="stand_rattle.mp3", event="loudest", max_s=1.0, attack_s=0.03,
                         credit=("Music Stand Manipulation", FS + "people/ROBAMOS/sounds/339014/", "ROBAMOS", "CC0")),
    # a glass tap pitched up toward the small crystal of a wristwatch
    "watch_tap": dict(file="watch_tap.mp3", event="first", max_s=0.1, attack_s=0.01, pitch=1.7,
                      credit=("Glass Tap", FS + "people/Unicornaphobist/sounds/262958/", "Unicornaphobist", "CC0")),
    "whoosh_big": dict(file="whoosh_big.mp3", event="loudest", max_s=0.55, highpass_hz=250.0, attack_s=0.3,
                       credit=("Punch-miss whoosh", FS + "people/DrMinky/sounds/174435/", "DrMinky", "CC BY 4.0")),
    "whoosh_small": dict(file="whoosh_small.mp3", event="loudest", max_s=0.3, highpass_hz=250.0, attack_s=0.15,
                         credit=("Swing Woosh", FS + "people/Jofae/sounds/389590/", "Jofae", "CC0")),
    "whoosh_spin": dict(file="whoosh_spin.mp3", event="loudest", max_s=0.7, highpass_hz=250.0, attack_s=0.35,
                        credit=("Quick woosh (spin)", FS + "people/sophiehall3535/sounds/245936/", "sophiehall3535", "CC0")),
}


def decode(name):
    path = os.path.join(SRC_DIR, name)
    if not os.path.isfile(path):
        raise ImportError_(f"missing source {path}")
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "x.wav")
        subprocess.run(["afconvert", "-f", "WAVE", "-d", f"LEI16@{SR}", "-c", "1", path, out], check=True)
        with wave.open(out) as w:
            if w.getframerate() != SR or w.getnchannels() != 1:
                raise ImportError_(f"{name}: afconvert gave {w.getframerate()} Hz x{w.getnchannels()}")
            x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float64) / 32768.0
    if len(x) == 0 or np.max(np.abs(x)) < 1e-3:
        raise ImportError_(f"{name}: decoded silent")
    return x


def envelope_db(x):
    win = int(0.005 * SR)
    env = np.sqrt(signal.convolve(x * x, np.ones(win) / win, mode="same") + 1e-12)
    return 20 * np.log10(env)


def events(x, attack_s):
    """[(start, peak, end)] sample indices of the separate events in x, in time order."""
    db = envelope_db(x)
    top = db.max()
    gap = int(0.12 * SR)
    peaks, _ = signal.find_peaks(db, height=top - 30.0, prominence=12.0, distance=gap)
    if len(peaks) == 0:
        peaks = np.array([int(np.argmax(db))])
    out = []
    for i, p in enumerate(peaks):
        lo = max(0, p - int(attack_s * SR))
        if i > 0:
            lo = max(lo, peaks[i - 1] + 1)
        quiet = np.where(db[lo:p] < db[p] - 25.0)[0]
        start = lo + (quiet[-1] if len(quiet) else 0)
        nxt = peaks[i + 1] if i + 1 < len(peaks) else len(x)
        below = np.where(db[p:nxt] < db[p] + FLOOR_DB)[0]
        end = p + (below[0] if len(below) else nxt - p)
        out.append((start, p, end))
    return out, db


def pick(evs, db, which, name):
    if which == "first":
        return evs[0]
    if which == "last":
        return evs[-1]
    if which == "loudest":
        return max(evs, key=lambda e: db[e[1]])
    if not isinstance(which, int) or which >= len(evs):
        raise ImportError_(f"{name}: event {which} asked for, source has {len(evs)} events")
    return evs[which]


def cut(x, attack_s, which, max_s, name, isolate=False):
    """isolate: stop at the next event (one step out of a walk); otherwise the sound runs on
    through its own clatter and tail until it has decayed, up to max_s."""
    evs, db = events(x, attack_s)
    start, peak, end = pick(evs, db, which, name)
    a = max(0, start - int(PRE_ROLL * SR))
    if isolate:
        b = min(end, a + int(max_s * SR))
    else:
        limit = min(len(x), a + int(max_s * SR))
        loud = np.where(db[peak:limit] > db[peak] + FLOOR_DB)[0]
        b = peak + (loud[-1] + 1 if len(loud) else 1)
    seg = x[a:b].copy()
    if len(seg) < int(0.02 * SR):
        raise ImportError_(f"{name}: cut is only {len(seg) / SR:.3f} s")
    return seg, len(evs)


def finish(seg, peak_dbfs, highpass_hz=HIGHPASS_HZ):
    sos = signal.butter(4, highpass_hz, "highpass", fs=SR, output="sos")
    seg = signal.sosfilt(sos, seg)
    seg -= np.mean(seg)
    n_in = int(0.001 * SR)
    seg[:n_in] *= np.linspace(0, 1, n_in)
    n_out = min(int(FADE_OUT * SR), len(seg) // 3)
    seg[-n_out:] *= np.linspace(1, 0, n_out) ** 2
    seg[-1] = 0.0
    pk = np.max(np.abs(seg))
    if pk < 1e-4:
        raise ImportError_("silent after processing")
    return seg * (10 ** (peak_dbfs / 20.0) / pk)


def repitch(seg, factor):
    # play faster (factor > 1) or slower: resample to len / factor at the same rate
    fr = Fraction(factor).limit_denominator(64)
    return signal.resample_poly(seg, fr.denominator, fr.numerator)


def write_wav(path, seg):
    q = np.round(np.clip(seg, -1, 1) * 32767).astype(np.int16)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(q.tobytes())
    return q


def main():
    man_path = os.path.join(OUT_DIR, "sfx_manifest.json")
    with open(man_path) as f:
        manifest = json.load(f)
    if manifest.get("sample_rate") != SR:
        raise ImportError_(f"manifest is {manifest.get('sample_rate')} Hz, expected {SR}; run gen_sfx.py first")
    credits = []
    print(f"{'sound':16s} {'events':>6s} {'seconds':>8s}  source")
    for name, s in SOURCES.items():
        if name not in manifest["sounds"]:
            raise ImportError_(f"{name} is not in the manifest")
        entry = manifest["sounds"][name]
        x = decode(s["file"])
        seg, n_ev = cut(x, s["attack_s"], s["event"], s["max_s"], name, s.get("isolate", False))
        if "pitch" in s:
            seg = repitch(seg, s["pitch"])
        for lf, lev, gain_db, off in s.get("layers", []):
            lseg, _ = cut(decode(lf), 0.02, lev, s["max_s"], f"{name} layer {lf}", isolate=True)
            lseg = lseg / np.max(np.abs(lseg)) * np.max(np.abs(seg)) * 10 ** (gain_db / 20.0)
            i = int(off * SR)
            if len(seg) < i + len(lseg):
                seg = np.pad(seg, (0, i + len(lseg) - len(seg)))
            seg[i:i + len(lseg)] += lseg
        seg = finish(seg, entry["peak_dbfs"], s.get("highpass_hz", HIGHPASS_HZ))
        write_wav(os.path.join(OUT_DIR, entry["file"]), seg)
        title, url, author, lic = s["credit"]
        entry["seconds"] = round(len(seg) / SR, 4)
        entry["source"] = {"title": title, "url": url, "author": author, "license": lic, "recorded": True}
        credits.append((name, title, url, author, lic))
        print(f"{name:16s} {n_ev:6d} {entry['seconds']:8.3f}  {author} ({lic})")
    with open(man_path, "w") as f:
        json.dump(manifest, f, indent=2)
        f.write("\n")
    with open(os.path.join(OUT_DIR, "CREDITS.md"), "w") as f:
        f.write("# Sound effect credits\n\nThe sounds Terence and his props make are real recordings, "
                "cut and levelled by `import_real_sfx.py` from the files in `sources/`. "
                "CC BY sounds need this credit wherever the game is distributed.\n\n"
                "| Sound | Recording | Author | License |\n|---|---|---|---|\n")
        for name, title, url, author, lic in credits:
            f.write(f"| `{name}` | [{title}]({url}) | {author} | {lic} |\n")
    print(f"{len(credits)} real sounds written, manifest and CREDITS.md updated")


if __name__ == "__main__":
    try:
        main()
    except ImportError_ as e:
        print(f"import_real_sfx FAILED: {e}", file=sys.stderr)
        sys.exit(1)
