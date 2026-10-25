"""Latency calibration (nqmt/calibrate.py): clap matching, the bridge applying the result, and the
whole loop against a simulated headset (nqmt/sim_headset.py: mic.pcm with claps a known delay
after their scheduled times, through the real hub and Kevin's transcriber)."""

import json
import random
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from hub.bridge import SessionBridge
from nqmt import calibrate as cal

ROOT = Path(__file__).resolve().parents[2]


def test_match_ignores_stray_onsets_and_echoes():
    rng = random.Random(3)
    ts = [10.0 + k for k in range(8)]
    onsets = [t + 0.2 + rng.gauss(0, 0.002) for t in ts]
    onsets += [t + 0.26 for t in ts[:4]]                  # a reflection after half the claps
    onsets += [rng.uniform(9, 19) for _ in range(20)]     # a noisy room: 2 onsets/s
    res = cal.summarize(cal.match(ts, sorted(onsets)), 8)
    assert res["ok"] and res["matched"] == 8
    assert abs(res["latency_ms"] - 200) < 3


def test_summarize_fails_on_too_few_or_scattered():
    assert not cal.summarize([0.2, None, None, None, None, None, 0.2, 0.2], 8)["ok"]
    assert not cal.summarize([0.1, 0.2, 0.15, 0.3, 0.12, 0.25, 0.18, 0.22], 8)["ok"]
    assert cal.summarize([None] * 8, 8)["error"]


def test_save_keeps_other_mics(tmp_path):
    p = tmp_path / "calibration.json"
    cal.save("quest", {"latency_ms": 180.0, "spread_ms": 4.0, "matched": 8, "n": 8}, p)
    cal.save("laptop", {"latency_ms": 20.0, "matched": 8, "n": 8}, p)
    assert cal.latency_ms("quest", p) == 180.0 and cal.latency_ms("laptop", p) == 20.0
    assert cal.latency_ms("file", p) is None


def test_bridge_applies_calibration_per_mic():
    sb = SessionBridge("songs", input_latency_s=0.01, mic="quest", mic_latency={"laptop": 0.02},
                       follow_mic=True)
    assert sb.input_latency_s == 0.01  # no quest entry yet: the command-line value
    sb.on_calibration({"ok": True, "mic": "quest", "latency_ms": 180.0})
    assert sb.input_latency_s == pytest.approx(0.18)
    sb.on_calibration({"ok": False, "mic": "quest", "latency_ms": 999.0})
    assert sb.input_latency_s == pytest.approx(0.18)  # a failed run changes nothing
    sb.on_settings({"mic_source": "laptop"})
    assert sb.input_latency_s == pytest.approx(0.02)
    sb.on_calibration({"ok": True, "mic": "quest", "latency_ms": 150.0})
    assert sb.input_latency_s == pytest.approx(0.02)  # stored for quest, laptop still in use
    sb.on_settings({"mic_source": "quest"})
    assert sb.input_latency_s == pytest.approx(0.15)
    fixed = SessionBridge("songs", mic="quest", mic_latency={"quest": 0.1, "laptop": 0.02})
    fixed.on_settings({"mic_source": "laptop"})
    assert fixed.input_latency_s == pytest.approx(0.1)  # --mic-fixed: the headset doesn't switch it


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.mark.parametrize("delay_ms", [180.0])
def test_calibration_loop_with_simulated_headset(tmp_path, delay_ms):
    """The real chain minus acoustics: hub, mic.pcm from another clock, transcriber, matching."""
    port = _free_port()
    url = f"ws://127.0.0.1:{port}"
    py = sys.executable
    hub = subprocess.Popen([py, "-m", "hub.server", "--port", str(port), "--no-record"], cwd=ROOT,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    sim = None
    try:
        time.sleep(1.0)
        sim = subprocess.Popen([py, "-m", "nqmt.sim_headset", "--hub", url, "--delay-ms", str(delay_ms),
                                "--jitter-ms", "1"], cwd=ROOT, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
        out = tmp_path / "calibration.json"
        r = subprocess.run([py, "-m", "nqmt.calibrate", "--hub", url, "--mic", "quest", "--n", "6",
                            "--interval-s", "0.6", "--out", str(out), "--record-dir", str(tmp_path)],
                           cwd=ROOT, capture_output=True, text=True, timeout=90)
        assert r.returncode == 0, r.stdout + r.stderr
        saved = json.loads(out.read_text())["quest"]
        assert saved["matched"] == 6
        assert abs(saved["latency_ms"] - delay_ms) < 6, saved
    finally:
        for p in (sim, hub):
            if p is not None:
                p.terminate()
                p.wait(5)
