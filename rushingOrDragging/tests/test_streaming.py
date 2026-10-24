"""Streaming: stream_notes.py timing/format, the online matcher in live_compare.py, and the two
programs connected by a real pipe."""

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import compare_performance as cp  # noqa: E402
import live_compare as lc  # noqa: E402
from test_drift import SCENARIOS, SOURCE, make_drifting  # noqa: E402

PY = sys.executable
needs_song = pytest.mark.skipif(not SOURCE.exists(), reason="song not imported")


def write_notes(path, pairs):
    path.write_text("# time_s  midi  name\n" + "".join(
        f"{t:.6f}  {m}  {cp.drum_name(m)}\n" for t, m in sorted(pairs)))
    return path


def pipe(played, sheet, stream_args=(), live_args=(), timeout=60):
    """Runs `stream_notes.py played | live_compare.py sheet` and returns live_compare's stdout."""
    s = subprocess.Popen([PY, "stream_notes.py", str(played), *stream_args],
                         cwd=ROOT, stdout=subprocess.PIPE)
    out = subprocess.run([PY, "live_compare.py", str(sheet), *live_args], cwd=ROOT, stdin=s.stdout,
                         capture_output=True, text=True, timeout=timeout)
    s.stdout.close()
    s.wait(timeout=5)
    assert out.returncode == 0, out.stderr
    return out.stdout


# ---- stream_notes.py -----------------------------------------------------------------------

def test_streams_canonical_lines_on_time(tmp_path):
    pairs = [(0.0, 36), (0.25, 42), (0.25, 38), (0.5, 92), (1.0, 49)]
    f = write_notes(tmp_path / "n.txt", pairs)
    proc = subprocess.Popen([PY, "stream_notes.py", str(f), "--speed", "2"], cwd=ROOT,
                            stdout=subprocess.PIPE, text=True)
    t0, got = time.perf_counter(), []
    for line in proc.stdout:
        got.append((time.perf_counter() - t0, line.rstrip("\n")))
    proc.wait()
    expected = [l for l in f.read_text().splitlines() if not l.startswith("#")]
    assert [l for _, l in got] == expected  # identical, canonical format
    for (arrived, line) in got:
        due = float(line.split()[0]) / 2
        assert due - 0.005 <= arrived <= due + 0.08  # process start-up dominates the slack


def test_start_skips_earlier_notes(tmp_path):
    f = write_notes(tmp_path / "n.txt", [(0.1, 36), (5.0, 38), (5.05, 42)])
    t0 = time.perf_counter()
    out = subprocess.run([PY, "stream_notes.py", str(f), "--start", "5"], cwd=ROOT,
                         capture_output=True, text=True).stdout
    assert out.splitlines() == ["5.000000  38  snare", "5.050000  42  hihat_closed"]
    assert time.perf_counter() - t0 < 2  # didn't wait for the skipped 5 s


def test_reader_closing_early_is_quiet(tmp_path):
    f = write_notes(tmp_path / "n.txt", [(i * 0.001, 36) for i in range(2000)])
    s = subprocess.Popen([PY, "stream_notes.py", str(f), "--speed", "100"], cwd=ROOT,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    subprocess.run(["head", "-n", "3"], stdin=s.stdout, capture_output=True)
    s.stdout.close()
    assert s.wait(timeout=10) == 0
    assert b"Traceback" not in s.stderr.read()


# ---- online matching -----------------------------------------------------------------------

@needs_song
@pytest.mark.parametrize("name,min_correct", [("realistic", 0.999), ("heavy", 0.99)])
def test_live_matcher_on_drifting_player(tmp_path, name, min_correct):
    seed, drift_sd, _ = SCENARIOS[name]
    truth = make_drifting(SOURCE, tmp_path / "p.txt", seed, drift_sd)
    m = lc.LiveMatcher(cp.read_notes(SOURCE))
    right = 0
    for (t, midi) in sorted(truth):  # arrival order
        hit, err = m.feed(t, midi)
        if hit.match is not None and hit.match.time == truth[(t, midi)]["sheet"]:
            right += 1
            assert err == pytest.approx(truth[(t, midi)]["error"], abs=1e-9)
    assert right / len(truth) >= min_correct


def test_live_matcher_extra_missed_and_order():
    sheet = [cp.Hit(t, 38) for t in (1.0, 1.07, 1.14, 2.0)]
    m = lc.LiveMatcher(sheet)
    assert m.feed(1.0, 38)[1] == pytest.approx(0)
    assert m.feed(1.16, 38)[0].match is sheet[2]   # 1.07 skipped (never played)
    assert m.feed(1.5, 38)[1] is None              # nothing within 100 ms: extra
    m.feed(2.3, 36)                                # time moves on: 1.07 and 2.0 are now missed
    assert (m.extra, m.missed) == (2, 2)


def test_recent_median_follows_newest_on_even_split():
    recent = [(1.0, 0.03), (1.1, 0.03), (1.2, 0.03), (1.3, -0.03), (1.4, -0.03), (1.5, -0.03)]
    assert lc.recent_median(recent, 1.5, 0.5) == -0.03
    assert lc.recent_median([(1.0, 0.02)], 1.0, 0.5) == 0.02


def test_family_fallback_live():
    m = lc.LiveMatcher([cp.Hit(1.0, 49)])
    hit, err = m.feed(1.01, 57)
    assert hit.match.midi == 49 and err == pytest.approx(0.01)


@pytest.mark.parametrize("prev,median,expected", [
    (None, 0.005, "perfect"), (None, -0.02, "rushing"), (None, 0.02, "dragging"),
    ("perfect", 0.011, "perfect"),      # inside tolerance + hysteresis: no flicker
    ("perfect", 0.013, "dragging"),
    ("dragging", 0.009, "dragging"),    # must come back below tolerance - hysteresis
    ("dragging", 0.007, "perfect"),
    ("dragging", -0.009, "perfect"),    # crossed to the other side: no stale "dragging"
    ("rushing", 0.02, "dragging"),
])
def test_hysteresis(prev, median, expected):
    assert lc.smoothed_state(prev, median, 0.010, 0.002) == expected


# ---- the two programs together -------------------------------------------------------------

def test_pipe_reports_each_state(tmp_path):
    sheet = write_notes(tmp_path / "sheet.txt", [(i * 0.1, 38) for i in range(60)])
    played = write_notes(tmp_path / "played.txt",
                         [(i * 0.1, 38) for i in range(20)] +              # 0-2 s on time
                         [(i * 0.1 + 0.03, 38) for i in range(20, 40)] +   # 2-4 s 30 ms late
                         [(i * 0.1 - 0.03, 38) for i in range(40, 60)])    # 4-6 s 30 ms early
    out = pipe(played, sheet, ["--speed", "10"], ["--drift-window", "0.3"])
    states = [l.split()[1] for l in out.splitlines() if l.strip().endswith("hit(s))")]
    assert states == ["PERFECT!", "DRAGGING!", "RUSHING!"]
    assert "matched:          60" in out and "extra:            0" in out


def test_arrival_clock_uses_wall_time(tmp_path):
    sheet = write_notes(tmp_path / "sheet.txt", [(0.5 + i * 0.25, 36) for i in range(8)])
    # timestamps in the lines say "perfect", but they're streamed 60 ms late in wall time
    played = write_notes(tmp_path / "played.txt", [(0.56 + i * 0.25, 36) for i in range(8)])
    stamps = pipe(played, sheet, [], ["--clock", "line"])
    assert "DRAGGING!" in stamps
    # arrival clock: process start-up adds a few ms, but 60 ms late must still read as dragging
    arrival = pipe(played, sheet, [], ["--clock", "arrival"])
    assert "DRAGGING!" in arrival and "RUSHING!" not in arrival


@needs_song
def test_pipe_on_real_song_matches_batch_comparer(tmp_path):
    seed, drift_sd, _ = SCENARIOS["realistic"]
    played = tmp_path / "p.txt"
    make_drifting(SOURCE, played, seed, drift_sd)
    live = pipe(played, SOURCE, ["--speed", "60"], timeout=120)
    batch = subprocess.run([PY, "compare_performance.py", str(played), str(SOURCE), "--summary-only"],
                           cwd=ROOT, capture_output=True, text=True).stdout
    for label in ("played hits:", "matched:", "extra:", "missed:", "mean error:", "median error:"):
        assert cp_line(live, label) == cp_line(batch, label), label


@needs_song
def test_partial_stream_counts_missed_only_in_span():
    out = pipe(SOURCE, SOURCE, ["--start", "100", "--speed", "200"], ["--no-summary"], timeout=60)
    assert out  # state lines printed
    out = pipe(SOURCE, SOURCE, ["--start", "480", "--speed", "5"])
    assert "missed:           0" in out and "extra:            0" in out


def cp_line(text, label):
    return next(l for l in text.splitlines() if l.strip().startswith(label)).strip()


def test_regular_file_on_stdin_does_not_hang(tmp_path):
    """kqueue never signals EOF on regular files; live_compare must read them straight through."""
    sheet = write_notes(tmp_path / "s.txt", [(i * 0.5, 36) for i in range(10)])
    with open(sheet) as f:
        out = subprocess.run([PY, "live_compare.py", str(sheet)], cwd=ROOT, stdin=f,
                             capture_output=True, text=True, timeout=10)
    assert "PERFECT!" in out.stdout and "matched:          10" in out.stdout


def test_full_screen_view_in_a_terminal(tmp_path):
    """Run under a pseudo-terminal: big-letter state, then the screen is restored."""
    import pty
    sheet = write_notes(tmp_path / "s.txt", [(i * 0.1, 38) for i in range(20)])
    played = write_notes(tmp_path / "p.txt", [(i * 0.1 + 0.04, 38) for i in range(20)])
    pid, fd = pty.fork()
    if pid == 0:  # child: stdout is the pty, stdin the played file
        os.chdir(ROOT)
        os.dup2(os.open(str(played), os.O_RDONLY), 0)
        os.execv(PY, [PY, "live_compare.py", str(sheet), "--drift-window", "0"])
    screen = b""
    while True:
        try:
            chunk = os.read(fd, 65536)
        except OSError:
            break
        if not chunk:
            break
        screen += chunk
    os.waitpid(pid, 0)
    text = screen.decode("utf-8", "replace")
    assert "\x1b[?1049h" in text and "\x1b[?1049l" in text      # entered and left the alt screen
    assert "█████" in text and "\x1b[1;31m" in text              # big red DRAGGING! art
    assert "\x1b[?25h" in text                                    # cursor shown again
    assert "Summary" in text.split("\x1b[?1049l")[-1]            # summary printed after restoring
