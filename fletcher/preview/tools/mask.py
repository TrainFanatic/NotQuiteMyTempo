"""Where Adam's SFX mask Fletcher's voice, from two stems of the same deterministic movie:
<name>_voice (SFX bus muted) and <name>_sfx (Voice sent to a muted sink, so a sidechain on SFX
still hears it). Speech band (300-3400 Hz), 20 ms frames. A voiced frame (voice within 35 dB of
its peak) is MASKED when SFX >= voice - 3 dB there. Masked frames are grouped into events and
attributed to the SFX logged in the 0.6 s before.
    mask.py <out dir> <scenario>"""
import json, re, subprocess, sys, wave
from pathlib import Path
import numpy as np
O, name = Path(sys.argv[1]), sys.argv[2]
SR, HOP = 24000, 480
def stem(s):
    w = O / f"{name}_{s}.wav"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(O / f"{name}_{s}.avi"), "-ac", "1", "-ar", str(SR),
                    "-c:a", "pcm_s16le", str(w)], check=True)
    with wave.open(str(w)) as f:
        x = np.frombuffer(f.readframes(f.getnframes()), np.int16).astype(float) / 32768
    X = np.fft.rfft(x); fr = np.fft.rfftfreq(len(x), 1 / SR); X[(fr < 300) | (fr > 3400)] = 0
    y = np.fft.irfft(X, len(x))
    n = len(y) // HOP
    return 10 * np.log10(np.mean(y[:n * HOP].reshape(n, HOP) ** 2, axis=1) + 1e-12)
v, s = stem("voice"), stem("sfx")
n = min(len(v), len(s)); v, s = v[:n], s[:n]
EV = [json.loads(re.sub(r"(?<=[:,\[])(-?)inf\b", r"\1Infinity", l)) for l in open(O / f"{name}_sfx.jsonl")]
sfx = [(e["t"], e["sfx"], e["anim"]) for e in EV if e["ev"] == "sfx"]
voiced = v > v.max() - 35
masked = voiced & (s >= v - 3)
events, i = [], 0
while i < n:
    if masked[i]:
        j = i
        while j < n and masked[j]: j += 1
        t0, t1 = i * HOP / SR, j * HOP / SR
        who = sorted({f"{nm}({an})" for t, nm, an in sfx if t0 - 0.6 <= t <= t1})
        events.append({"t": round(t0, 2), "dur_ms": round((t1 - t0) * 1000), "worst_snr_db": round(float((v[i:j] - s[i:j]).min()), 1),
                       "sfx": who})
        i = j
    else: i += 1
tot_v = voiced.sum() * HOP / SR
print(f"{name}: voiced {tot_v:.1f} s, masked {masked.sum() * HOP / SR * 1000:.0f} ms ({100 * masked.sum() / max(1, voiced.sum()):.1f} %), "
      f"voice peak {v.max():.1f} dB, sfx peak {s.max():.1f} dB (speech band)")
for e in events:
    if e["dur_ms"] >= 40: print("  ", e)
json.dump({"voiced_s": tot_v, "masked_ms": float(masked.sum() * HOP / SR * 1000), "events": events},
          open(O / f"{name}_mask.json", "w"), indent=1)
