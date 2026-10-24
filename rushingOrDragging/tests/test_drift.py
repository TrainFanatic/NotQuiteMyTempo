"""Simulate a drummer who rushes and drags, run compare_performance.py on it, and check the output.

Timing model, per hit at sheet time t:
  error(t) = drift(t) + jitter
  drift  : Ornstein-Uhlenbeck process, so the player wanders ahead of or behind the beat for
           several seconds and is pulled back. Stationary std DRIFT_SD, time constant DRIFT_TAU.
  jitter : uniform in [-r(t), r(t)], where the range r(t) itself wanders (a second OU process
           squashed into [JITTER_MIN, JITTER_MAX]), so some passages are tight and others sloppy.

Run as a script to write a drifting clone to out/ for inspection:
  python tests/test_drift.py [source.drums.txt] [--seed N] [--drift-sd MS] [--offset S]
"""

import contextlib
import io
import re
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import compare_performance as cp  # noqa: E402
from test_uniform_jitter import parsed_rows, summary, order_swapped  # noqa: E402

SOURCE = ROOT / "out" / "metallica-master_of_puppets.drums.txt"
DRIFT_TAU = 8.0      # s: how long a rush/drag tends to last
JITTER_TAU = 5.0     # s: how quickly the sloppiness changes
JITTER_MIN, JITTER_MAX = 0.003, 0.020

SCENARIOS = {
    # name: (seed, drift std in s, constant start offset in s)
    "realistic": (11, 0.015, 0.0),
    "heavy": (5, 0.035, 0.0),
    "heavy_with_start_offset": (5, 0.035, 2.5),
}


def timing_errors(sheet_times, rng, drift_sd, n_hits_at):
    """Returns (drift, jitter_range) per distinct sheet time; jitter is drawn per hit later."""
    t = np.asarray(sheet_times)
    drift, z = np.zeros(len(t)), np.zeros(len(t))
    for i in range(1, len(t)):
        dt = t[i] - t[i - 1]
        a, b = np.exp(-dt / DRIFT_TAU), np.exp(-dt / JITTER_TAU)
        drift[i] = drift[i - 1] * a + drift_sd * np.sqrt(1 - a * a) * rng.normal()
        z[i] = z[i - 1] * b + np.sqrt(1 - b * b) * rng.normal()
    jitter_range = JITTER_MIN + (JITTER_MAX - JITTER_MIN) / (1 + np.exp(-1.5 * z))
    return drift, jitter_range


def make_drifting(source, dest, seed, drift_sd, offset=0.0):
    """Writes the clone and returns {(played_time, midi): {"sheet", "error", "drift"}} where
    error is relative to the sheet *excluding* the constant start offset."""
    sheet = cp.read_notes(source)
    rng = np.random.default_rng(seed)
    times = sorted({h.time for h in sheet})
    drift, jitter_range = timing_errors(times, rng, drift_sd, None)
    at = {t: i for i, t in enumerate(times)}
    truth = {}
    for h in sheet:
        i = at[h.time]
        t = round(max(0.0, h.time + offset + drift[i] + rng.uniform(-1, 1) * jitter_range[i]), 6)
        truth[(t, h.midi)] = {"sheet": h.time, "error": t - offset - h.time, "drift": drift[i]}
    lines = [f"# source: drifting clone of {Path(source).name}  (drift sd {drift_sd * 1000:.0f} ms, "
             f"jitter {JITTER_MIN * 1000:.0f}-{JITTER_MAX * 1000:.0f} ms, offset {offset:g} s, seed {seed})",
             "# time_s  midi  name"]
    lines += [f"{t:.6f}  {m}  {cp.drum_name(m)}" for t, m in sorted(truth)]
    Path(dest).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return truth


def run_compare(played_path, *extra):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        cp.main([str(played_path), str(SOURCE), *extra])
    return buf.getvalue()


@pytest.fixture(scope="module", params=list(SCENARIOS))
def scenario(request, tmp_path_factory):
    if not SOURCE.exists():
        pytest.skip(f"{SOURCE.name} not imported (run: python cli.py 455118)")
    seed, drift_sd, offset = SCENARIOS[request.param]
    path = tmp_path_factory.mktemp(request.param) / "played.drums.txt"
    truth = make_drifting(SOURCE, path, seed, drift_sd, offset)
    return request.param, truth, offset, path, run_compare(path)


def applied_offset(out):
    return float(re.match(r"([+-]\d+\.\d+)", summary(out, "offset applied:"))[1])


def correctly_paired(truth, out):
    source_of = {(t, cp.drum_name(m)): v["sheet"] for (t, m), v in truth.items()}
    return [r for r in parsed_rows(out) if source_of[(r[0], r[1])] == r[2]]


def test_simulation_really_rushes_and_drags(scenario):
    """Guard against a too-gentle simulation: it must spend time well ahead of and behind the beat."""
    name, truth, *_ = scenario
    drift = np.array([v["drift"] for v in truth.values()])
    threshold = 0.010 if name == "realistic" else 0.035  # beyond 0.035 s, naive matching breaks
    assert (drift < -threshold).mean() > 0.05 and (drift > threshold).mean() > 0.05


def test_offset(scenario):
    _, _, offset, _, out = scenario
    if offset == 0:
        assert applied_offset(out) == 0.0
    else:  # the detected offset may absorb a few ms of the drift's average, but no more
        assert applied_offset(out) == pytest.approx(offset, abs=0.02)


def test_every_hit_paired_with_its_source(scenario):
    _, truth, *_, out = scenario
    assert summary(out, "extra:").split()[0] == "0"
    assert summary(out, "missed:").split()[0] == "0"
    right = correctly_paired(truth, out)
    wrong = {(r[0], r[1]) for r in parsed_rows(out)} - {(r[0], r[1]) for r in right}
    swapped = {(t, cp.drum_name(m)) for t, m in order_swapped(
        {k: (v["sheet"], v["error"]) for k, v in truth.items()})}
    assert wrong <= swapped  # anything else would be a matcher mistake
    assert len(right) / len(truth) >= 0.999


def test_reported_error_is_the_planted_error(scenario):
    _, truth, offset, _, out = scenario
    by_key = {(t, cp.drum_name(m)): v for (t, m), v in truth.items()}
    shift = applied_offset(out) - offset  # whatever part of the drift the offset absorbed
    worst = max(abs(err - (by_key[(p, n)]["error"] - shift)) for p, n, s, err, _ in correctly_paired(truth, out))
    assert worst <= 0.00006  # printed to 0.1 ms


def test_timeline_follows_the_drift(scenario):
    """The 'timing over time' rows must match the planted errors segment by segment."""
    _, truth, offset, _, out = scenario
    shift = applied_offset(out) - offset
    planted = {}
    for v in truth.values():
        planted.setdefault(int(v["sheet"] // 10), []).append(v["error"] - shift)
    rows = re.findall(r"^\s+(\d+\.\d)s\s+n=(\d+)\s+([+-]\d+\.\d) ms", out, re.M)
    assert len(rows) == len(planted)
    for start, n, med in rows:
        seg = planted[int(float(start) // 10)]
        assert int(n) == len(seg)
        assert float(med) / 1000 == pytest.approx(float(np.median(seg)), abs=0.0002)
    meds = [float(m) for _, _, m in rows]
    assert min(meds) < -5 and max(meds) > 5  # both rushing and dragging show up


def test_drift_tracking_is_what_makes_heavy_drift_work(scenario):
    name, truth, _, path, out = scenario
    if name != "heavy":
        pytest.skip("contrast only needed once")
    naive = run_compare(path, "--no-drift-tracking")
    assert len(correctly_paired(truth, out)) == len(truth)              # tracked: every hit right
    assert len(truth) - len(correctly_paired(truth, naive)) >= 20        # naive: drags past half a 16th


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("source", nargs="?", default=str(SOURCE))
    p.add_argument("--seed", type=int, default=SCENARIOS["heavy"][0])
    p.add_argument("--drift-sd", type=float, default=SCENARIOS["heavy"][1] * 1000, help="ms")
    p.add_argument("--offset", type=float, default=0.0, help="constant start offset, s")
    a = p.parse_args()
    dest = Path(a.source).with_name(Path(a.source).name.replace(
        ".drums.txt", f".drift{a.drift_sd:g}ms_seed{a.seed}.drums.txt"))
    make_drifting(a.source, dest, a.seed, a.drift_sd / 1000, a.offset)
    print(f"Wrote {dest}\nCompare with: python compare_performance.py {dest} {a.source}")
