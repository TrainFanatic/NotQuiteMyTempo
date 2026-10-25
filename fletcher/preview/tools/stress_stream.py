"""SFX-vs-voice stress stream: a cached real tirade (16-bit 24 kHz) streamed in 0.3 s chunks while
Terence plays the loudest prop moves every 13 chunks (3.9 s). Band stopped, 94 bpm. No API."""
import base64, json, sys, wave
LINE = "/Users/aaronwang/NotQuiteMyTempo/fletcher/lines/20261024-171741_c2_dragging_L2.wav"
MOVES = ["t5_chair_throw", "t5_cymbal_throw", "t1_fist_slam", "t4_sweep_stand", "t5_drum_throw", "t1_clap_stop"]
out, rows = sys.argv[1], []
def add(t, typ, d): rows.append({"rx": t, "msg": {"type": typ, "src": "aaron", "t": t, "data": d}})
add(0.2, "session.state", {"state": "stopped", "bpm": 94.0, "bar": 1, "beat": 1})
add(0.3, "fletcher.state", {"mood": "furious", "anger": 1.0, "action": "glare", "look_at": "drummer"})
with wave.open(LINE) as w:
    pcm = w.readframes(w.getnframes())
n = int(0.3 * 24000) * 2
t = 1.0
for k, m in enumerate(MOVES):
    add(t, "fletcher.state", {"mood": "furious", "anger": 1.0, "action": "glare", "look_at": "drummer",
                              "gesture": m, "utterance_id": "s1", "at_index": 13 * k, "priority": 1})
add(t, "fletcher.speech", {"utterance_id": "s1", "text": "(stress line)", "stream": True, "sample_rate": 24000})
chunks = [pcm[i:i + n] for i in range(0, len(pcm), n)]
for i, c in enumerate(chunks):
    add(t + i * 0.15, "fletcher.audio", {"utterance_id": "s1", "index": i, "pcm_b64": base64.b64encode(c).decode(), "final": False})
add(t + len(chunks) * 0.15, "fletcher.audio", {"utterance_id": "s1", "index": len(chunks), "pcm_b64": "", "final": True})
with open(out, "w") as fh:
    for r in sorted(rows, key=lambda r: r["rx"]):
        fh.write(json.dumps(r) + "\n")
print(len(chunks), "chunks,", len(pcm) / 48000, "s")
