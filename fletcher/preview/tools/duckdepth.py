"""SFX level (broadband, 300 ms peak RMS after each loud SFX) in a ducked SFX stem vs the undecked one."""
import json, re, sys, wave
import numpy as np
a, b, name = sys.argv[1], sys.argv[2], sys.argv[3]
LOUD = ("chair_impact", "cymbal_impact", "stand_crash", "fist_slam_stand", "clap_big", "drum_impact", "door_slam")
def lev(d):
    with wave.open(f"{d}/{name}_sfx.wav") as f:
        x = np.frombuffer(f.readframes(f.getnframes()), np.int16).astype(float) / 32768
    return x
xa, xb = lev(a), lev(b)
ev = [json.loads(re.sub(r"(?<=[:,\[])(-?)inf\b", r"\1Infinity", l)) for l in open(f"{a}/{name}_sfx.jsonl")]
for e in ev:
    if e["ev"] == "sfx" and e["sfx"] in LOUD:
        i = int(e["t"] * 24000); w = 480
        def pk(x): 
            seg = x[i:i + 7200]; n = len(seg) // w
            return 10 * np.log10((seg[:n * w].reshape(n, w) ** 2).mean(axis=1).max() + 1e-12)
        print(f"{e['t']:6.2f} {e['sfx']:16s} {pk(xa):6.1f} -> {pk(xb):6.1f} dB  ({pk(xb) - pk(xa):+.1f})")
