"""Clone a real sheet file, shift every note by a uniform random amount in [-50, 50] ms, run
compare_performance.py on it, and check its printed output against the planted shifts.

Run as a script to write the jittered clone to out/ for inspection:
  python tests/test_uniform_jitter.py [source.drums.txt] [--seed N]
"""

import re
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import compare_performance as cp  # noqa: E402

SOURCE = ROOT / "out" / "metallica-master_of_puppets.drums.txt"
MAX_SHIFT_S = 0.050
SEED = 0
TOLERANCE_S = 0.010  # compare_performance's default "on time" band

ROW = re.compile(r"^\s*(\d+\.\d{6})\s+(\w+)\s+(\d+\.\d{6})\s+([+-]\d+\.\d) ms\s+(on time|early|late)")


def make_jittered(source, dest, seed=SEED, max_shift=MAX_SHIFT_S):
    """Writes the clone (in file_format.md's canonical form) and returns
    {(played_time, midi): (sheet_time, shift)} so results can be checked."""
    sheet = cp.read_notes(source)
    shifts = np.random.default_rng(seed).uniform(-max_shift, max_shift, len(sheet))
    truth = {}
    for h, s in zip(sheet, shifts):
        t = round(max(0.0, h.time + float(s)), 6)  # the format has no negative times
        truth[(t, h.midi)] = (h.time, t - h.time)
    lines = ["# source: jittered clone of " + Path(source).name + f"  (uniform ±{max_shift * 1000:.0f} ms, seed {seed})",
             "# time_s  midi  name"]
    lines += [f"{t:.6f}  {m}  {cp.drum_name(m)}" for t, m in sorted(truth)]
    Path(dest).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return truth


@pytest.fixture(scope="module")
def result(tmp_path_factory):
    if not SOURCE.exists():
        pytest.skip(f"{SOURCE.name} not imported (run: python cli.py 455118)")
    played_path = tmp_path_factory.mktemp("jitter") / "played.drums.txt"
    truth = make_jittered(SOURCE, played_path)
    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        cp.main([str(played_path), str(SOURCE)])
    return truth, buf.getvalue()


def parsed_rows(output):
    rows = []
    for line in output.splitlines():
        m = ROW.match(line)
        if m:
            rows.append((float(m[1]), m[2], float(m[3]), float(m[4]) / 1000, m[5]))
    return rows


def summary(output, label):
    return re.search(rf"^\s*{re.escape(label)}\s*(.+)$", output, re.M)[1]


def test_clone_is_valid_format(tmp_path):
    if not SOURCE.exists():
        pytest.skip("source not imported")
    dest = tmp_path / "c.txt"
    truth = make_jittered(SOURCE, dest)
    data = [l for l in dest.read_text().splitlines() if not l.startswith("#")]
    assert all(re.fullmatch(r"\d+\.\d{6}  \d+  [a-z0-9_]+", l) for l in data)
    assert data == sorted(data, key=lambda l: (float(l.split()[0]), int(l.split()[1])))
    shifts = np.array([s for _, s in truth.values()])
    assert np.abs(shifts).max() <= MAX_SHIFT_S + 1e-6
    assert len(truth) == len(cp.read_notes(SOURCE))  # no collisions lost a note


def test_no_offset_detected(result):
    _, out = result
    assert summary(out, "offset applied:").startswith("+0.000000 s")


def test_every_hit_reported_once(result):
    truth, out = result
    rows = parsed_rows(out)
    extra = out.count("EXTRA")
    assert len(rows) + extra == len(truth)


def test_printed_error_and_verdict_are_consistent(result):
    _, out = result
    for played, _, sheet, err, verdict in parsed_rows(out):
        assert err == pytest.approx(played - sheet, abs=0.00006)  # printed to 0.1 ms
        expected = "on time" if abs(err) <= TOLERANCE_S else ("late" if err > 0 else "early")
        assert verdict == expected or abs(abs(err) - TOLERANCE_S) < 0.0001


def test_hits_paired_with_their_source_note(result):
    truth, out = result
    source_of = {(t, cp.drum_name(m)): (st, s) for (t, m), (st, s) in truth.items()}
    rows = parsed_rows(out)
    right = [r for r in rows if source_of[(r[0], r[1])][0] == r[2]]
    assert len(right) / len(truth) >= 0.99
    # where the pairing is right, the reported error is exactly the planted shift
    for played, name, sheet, err, _ in right:
        assert err == pytest.approx(source_of[(played, name)][1], abs=0.00006)


def order_swapped(truth):
    """Hits whose rank among same-drum played hits differs from their source note's rank: the
    jitter moved them past a neighbour, so no matcher can tell the two apart."""
    played_by, sheet_by = {}, {}
    for (t, m), (st, _) in truth.items():
        played_by.setdefault(m, []).append(t)
        sheet_by.setdefault(m, []).append(st)
    swapped = set()
    for m in played_by:
        p_rank = {t: i for i, t in enumerate(sorted(played_by[m]))}
        s_rank = {st: i for i, st in enumerate(sorted(sheet_by[m]))}
        swapped |= {(t, m) for (t, mm), (st, _) in truth.items() if mm == m and p_rank[t] != s_rank[st]}
    return swapped


def test_only_unavoidable_mistakes(result):
    """Every hit is paired (no extras/missed) and every wrong pairing is an order swap."""
    truth, out = result
    assert summary(out, "extra:").split()[0] == "0"
    assert summary(out, "missed:").split()[0] == "0"
    swapped = {(t, cp.drum_name(m)) for t, m in order_swapped(truth)}
    source_of = {(t, cp.drum_name(m)): st for (t, m), (st, _) in truth.items()}
    wrong = {(r[0], r[1]) for r in parsed_rows(out) if source_of[(r[0], r[1])] != r[2]}
    assert wrong <= swapped


def test_no_pairing_exceeds_the_window(result):
    _, out = result
    errs = np.array([r[3] for r in parsed_rows(out)])
    assert np.abs(errs).max() <= MAX_SHIFT_S + 0.0001


def test_summary_matches_uniform_distribution(result):
    _, out = result
    ms = lambda label: float(re.match(r"([+-]?\d+\.\d)", summary(out, label))[1])
    # uniform on [-50, 50] ms: mean 0, mean |x| 25, std 50/sqrt(3) = 28.9; 20% within ±10 ms
    assert abs(ms("mean error:")) < 2.0
    assert ms("mean |error|:") == pytest.approx(25.0, abs=1.5)
    assert ms("std dev:") == pytest.approx(28.9, abs=1.5)
    counts = re.search(r"on time \(±10 ms\): (\d+)\s+early: (\d+)\s+late: (\d+)", out)
    on, early, late = map(int, counts.groups())
    total = on + early + late
    assert on / total == pytest.approx(0.20, abs=0.02)
    assert early / total == pytest.approx(0.40, abs=0.03)
    assert late / total == pytest.approx(0.40, abs=0.03)


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("source", nargs="?", default=str(SOURCE))
    p.add_argument("--seed", type=int, default=SEED)
    a = p.parse_args()
    dest = Path(a.source).with_name(Path(a.source).name.replace(".drums.txt", f".jitter50_seed{a.seed}.drums.txt"))
    make_jittered(a.source, dest, a.seed)
    print(f"Wrote {dest}\nCompare with: python compare_performance.py {dest} {a.source}")
