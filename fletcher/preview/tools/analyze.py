"""Measure sync in a rendered movie: voice onsets found in the movie's own audio track (by
cross-correlating the source clips) vs Terence's key frames (move_started from the event log
+ the key frame from Adam's moves json, tempo-scaled). Prints a markdown table, writes JSON."""
import json, subprocess, sys, wave
from pathlib import Path
import numpy as np
sys.path.insert(0, "/Users/aaronwang/NotQuiteMyTempo")
from fletcher import countoff as co

name, kind = sys.argv[1], sys.argv[2]          # out/<name>.avi|.jsonl, gallery|cutoff
M = Path("/Users/aaronwang/.claude/jobs/7b7e33c1/tmp/agent_movie")
F = Path("/Users/aaronwang/NotQuiteMyTempo/fletcher")
SR = 24000
import os
OUT = Path(os.environ.get("OUTDIR", str(M / "out")))
MOVES = json.load(open(os.environ.get("MOVES", str(M / "qa4/assets/terence/terence_v004_moves.json"))))["moves"]
CUES = json.load(open(F / "barks/cues.json"))
import re
EV = [json.loads(re.sub(r"(?<=[:,\[])(-?)inf\b", r"\1Infinity", l)) for l in open(OUT / f"{name}.jsonl")]
wav = OUT / f"{name}_audio.wav"
subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(OUT / f"{name}.avi"), "-ac", "1", "-ar", str(SR),
                "-c:a", "pcm_s16le", str(wav)], check=True)
def readw(p):
    with wave.open(str(p)) as w:
        x = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(float) / 32768
        return x, w.getframerate()
AUD, _ = readw(wav)

def locate(clip, around, lo=-0.4, hi=1.5):
    """Movie time where `clip` (array @24k) starts, searched near `around`."""
    a = max(0, int((around + lo) * SR)); b = min(len(AUD), int((around + hi) * SR) + len(clip))
    seg = AUD[a:b]
    n = 1 << int(np.ceil(np.log2(len(seg) + len(clip))))
    c = np.fft.irfft(np.fft.rfft(seg, n) * np.conj(np.fft.rfft(clip, n)), n)[:len(seg) - len(clip) + 1]
    k = int(np.argmax(c))
    e = np.sqrt(np.convolve(seg ** 2, np.ones(len(clip)), "valid")[:len(c)] * np.sum(clip ** 2)) + 1e-12
    return (a + k) / SR, float(c[k] / e[k])

KEYS = ["cutoff", "slam", "flick", "wave", "clap", "point", "taps", "cue", "nods", "accents", "hit", "claps", "finger_up"]
def key_frames(move):
    m = MOVES.get(move, {})
    for k in KEYS:
        v = m.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool): return [(k, v)]
        if isinstance(v, list) and v: return [(k, x) for x in v]
    out = []
    for k, v in m.items():
        if k in ("frames", "seconds", "loop", "utility", "speed_mps", "beat_frames"): continue
        if isinstance(v, (int, float)) and not isinstance(v, bool): out.append((k, v))
        elif isinstance(v, list) and v and all(isinstance(i, (int, float)) for i in v): out += [(k, i) for i in v]
    return out

def fs(frame, bpm): return frame / 24 * 120 / bpm

def starts(move, after, before=1e9):
    return [e["t"] for e in EV if e["ev"] == "move_started" and e["move"] == move and after <= e["t"] < before]

def finishes(move, after):
    return [e["t"] for e in EV if e["ev"] == "move_finished" and e["move"] == move and e["t"] >= after]

def bpm_at(t):
    b = 120.0
    for e in EV:
        if e["ev"] == "msg" and e.get("type") == "session.state" and e.get("bpm") and e["t"] <= t: b = e["bpm"]
    return b

rows, notes = [], []
speech = {e["utterance_id"]: e for e in EV if e["ev"] == "msg" and e.get("type") == "fletcher.speech"}
speaking = {e["uid"]: e["t"] for e in EV if e["ev"] == "speaking"}
ended = {e["uid"]: e["t"] for e in EV if e["ev"] == "voice_finished"}
cuemsgs = [e for e in EV if e["ev"] == "msg" and e.get("type") == "fletcher.state" and "gesture" in e]
dues = [e for e in EV if e["ev"] == "cue_due"]
dropped = [e for e in EV if e["ev"] == "move_dropped"]
GM = {"slam_stand": "t1_fist_slam", "throw_music": "t4_throw_music", "kick_stand": "t4_sweep_stand",
      "throw_chair": "t5_chair_throw", "storm_over": "t4_charge", "in_face": "t3_loom", "point": "t2_point_player",
      "beat_time": "t5_rhythm_slaps", "grab_cymbal": "t5_cymbal_throw", "turn_away": "t1_wave_off",
      "spin_back": "t2_point_player", "throw_hands_up": "t4_shout", "slow_clap": "t3_clap_in_face"}

for uid, sp in speech.items():
    clip = sp.get("clip_id")
    t_sp = speaking.get(uid)
    if t_sp is None: continue
    if clip:
        x, sr = readw(F / f"barks/{clip}.wav")
        if sr != SR: x = np.interp(np.arange(int(len(x) * SR / sr)) * sr / SR, np.arange(len(x)), x)
        t_clip, q = locate(x, t_sp)
        onset = t_clip + co.detect_onset(x, SR)
        words = [("onset", onset)]
        for c in (CUES.get(clip) or {}).get("cues", []):
            if c.get("emphasis_s") is not None: words.append((f"emph {c['gesture']}", t_clip + c["emphasis_s"]))
        held = t_sp - sp["t"]
        label = f"bark {clip} \"{sp.get('text')}\"" + (f" (held {held * 1000:.0f} ms)" if held > 0.02 else "")
    else:
        continue  # the line is handled below
    for cm in [c for c in cuemsgs if c["utterance_id"] == uid]:
        g = cm["gesture"]; mv = GM.get(g, g)
        due = next((d["t"] for d in dues if d["uid"] == uid and d["gesture"] == g), None)
        st = starts(mv, (due or t_sp) - 0.05, (due or t_sp) + 6)
        anchor = next(f"{k} {cm[k]}" for k in ("at_s", "after_s", "at_index", "t0") if k in cm)
        if not st:
            rows.append([label, f"{g} ({anchor})", f"{t_clip - t_sp:+.3f}", "-", "NOT PLAYED" + (" (dropped)" if any(d["move"] == mv for d in dropped) else ""), "", ""])
            continue
        kfs = key_frames(mv); b = bpm_at(st[0])
        k0 = kfs[0] if kfs else ("start", 0)
        tk = st[0] + fs(k0[1], b)
        target = dict(words).get(f"emph {g}", onset)
        rows.append([label, f"{g} -> {mv} ({anchor})", f"{t_clip - t_sp:+.3f}", f"{st[0]:.3f}",
                     f"{k0[0]} f{k0[1]} @ {tk:.3f}", f"{target:.3f}", f"{(tk - target) * 1000:+.0f}"])

line = [u for u, s in speech.items() if s.get("stream")]
if line:
    uid = line[0]
    lw = "20261024-171410_c3_rushing_L3.wav"
    x, _ = readw(F / f"lines/{lw}")
    t_line, q = locate(x[:SR * 3], speaking[uid], -0.3, 1.0)
    seg_s = [9.45, 5.64, 4.37, 1.1]; starts_s = np.cumsum([0] + seg_s[:-1])
    sent_on = [t_line + s + co.detect_onset(x[int(s * SR):int((s + 1.5) * SR)], SR) for s in starts_s]
    notes.append(f"line {uid}: speaking event {speaking[uid]:.3f}, audio found at {t_line:.3f} (corr {q:.2f}); "
                 f"line end event {ended.get(uid, float('nan')):.3f}, audio ends {t_line + len(x) / SR:.3f}")
    for cm in [c for c in cuemsgs if c["utterance_id"] == uid]:
        g = cm["gesture"]; mv = GM.get(g, g)
        due = next((d["t"] for d in dues if d["uid"] == uid and d["gesture"] == g), None)
        anchor = next(f"{k} {cm[k]}" for k in ("at_s", "after_s", "at_index", "t0") if k in cm)
        if "at_index" in cm:
            firsts = np.cumsum([0] + [int(np.ceil(x / 0.3 - 1e-9)) for x in seg_s[:-1]])
            si = int(np.searchsorted(firsts, cm["at_index"], side="right") - 1)
            target, tname = sent_on[si], f"sentence {si + 1} onset"
            due = next((d["t"] for d in dues if d["uid"] == uid and d["gesture"] == g and d["t"] >= target - 0.6), due)
        elif "t0" in cm:
            continue  # the count-off: below
        else:
            target, tname = t_line + len(x) / SR + cm.get("after_s", 0), f"line end + {cm.get('after_s')}"
        st = starts(mv, (due or target) - 0.05, (due or target) + 8)
        if not st:
            rows.append([f"line ({tname})", f"{g} -> {mv} ({anchor})", "", f"due {due}", "NOT PLAYED" + (" (dropped)" if any(d["move"] == mv for d in dropped) else ""), f"{target:.3f}", ""])
            continue
        kfs = key_frames(mv); b = bpm_at(st[0])
        k0 = kfs[0] if kfs else ("start", 0)
        tk = st[0] + fs(k0[1], b)
        rows.append([f"line ({tname})", f"{g} -> {mv} ({anchor})", f"due {due:.3f}" if due else "", f"{st[0]:.3f}",
                     f"{k0[0]} f{k0[1]} @ {tk:.3f}", f"{target:.3f}", f"{(st[0] - target) * 1000:+.0f} start / {(tk - target) * 1000:+.0f} key"])
    stream = next(e for e in EV if e["ev"] == "stream")
    co_cue = next((c for c in cuemsgs if "t0" in c), None)
    words = [e for e in EV if e["ev"] == "countoff_word"]
    man = json.load(open(F / "countoff/manifest.json"))
    if co_cue and words:
        b = co_cue["bpm"]; beat = 60 / b
        t0 = co_cue["t0"] + stream["t_base"]
        fing = starts("t0_countoff_finger", t_line)
        restart = [e["t"] for e in EV if e["ev"] == "msg" and e.get("type") == "session.command" and e.get("action") == "restart"]
        band = [e["t"] for e in EV if e["ev"] == "msg" and e.get("synth")]
        notes.append(f"count-off cue: t0 {t0:.3f} (movie), line audio ends {t_line + len(x) / SR:.3f}; restart command {restart}; "
                     f"band back in (synthesized playing at t0) {band}; finger starts {fing} (want {t0 - 5 * beat:.3f})")
        for k, w in enumerate(words):
            cx, _ = readw(F / f"countoff/{w['clip']}.wav")
            tc, q = locate(cx, w["t"], -0.2, 0.3)
            on = tc + man[w["clip"]]["onset_s"]
            beat_t = t0 - (len(words) - k) * beat
            tk = fing[0] + fs(12 * (k + 1), b) if fing else float("nan")
            rows.append([f"count-off {w['clip']}", "t0_countoff_finger finger_up", f"word vs beat {(on - beat_t) * 1000:+.0f} ms (corr {q:.2f})",
                         f"{fing[0]:.3f}" if fing else "-", f"f{12 * (k + 1)} @ {tk:.3f}", f"{on:.3f}", f"{(tk - on) * 1000:+.0f}"])
    co_ev = [e["t"] for e in EV if e["ev"] == "countoff_audio"]
    if co_ev and not words:
        cx, _ = readw(M / "countoff_94_sharp.wav")
        t_co, q = locate(cx, co_ev[0], -0.2, 0.5)
        beat = 60 / 94
        words = [t_co + beat * (k + 1) for k in range(4)]  # onsets sit on the beats by construction
        fing = starts("t0_countoff_finger", t_line)
        notes.append(f"count-off audio event {co_ev[0]:.3f}, found {t_co:.3f} (corr {q:.2f}); finger starts {fing}")
        if fing:
            for k, w in enumerate(words):
                tk = fing[0] + fs(12 * (k + 1), 94)
                rows.append([f"count-off \"{['five','six','seven','eight'][k]}\"", "t0_countoff_finger finger_up",
                             "", f"{fing[0]:.3f}", f"f{12 * (k + 1)} @ {tk:.3f}", f"{w:.3f}", f"{(tk - w) * 1000:+.0f}"])

print("| utterance | cue -> move (anchor) | audio vs speaking ev (s) | move start | key frame | voice target | offset ms |")
print("|---|---|---|---|---|---|---|")
for r in rows: print("| " + " | ".join(str(c) for c in r) + " |")
for n in notes: print("- " + n)
print("- dropped:", [(d["move"], round(d["late"], 2), round(d["t"], 2)) for d in dropped])
seq = [(round(e["t"], 2), e["ev"], e.get("move") or e.get("uid") or e.get("sfx")) for e in EV if e["ev"] in ("move_started", "speaking", "voice_finished", "countoff_audio")]
print("- sequence:", seq)
sfx = [(round(e["t"], 2), e["sfx"], e["anim"]) for e in EV if e["ev"] == "sfx"]
print("- terence sfx:", sfx)
json.dump({"rows": rows, "notes": notes, "sfx": sfx}, open(OUT / f"{name}_sync.json", "w"), indent=1)
