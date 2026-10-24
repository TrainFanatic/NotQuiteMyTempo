import io
from pathlib import Path

import numpy as np
import pytest

import compare_performance as cp

OUT = Path(__file__).resolve().parent.parent / "out"


def hits(*pairs):
    return [cp.Hit(t, m) for t, m in pairs]


def run(played, sheet, offset=0.0, window=0.1):
    cp.match(played, sheet, offset, window)
    return [(p.time, p.match.time if p.match else None) for p in played]


def test_exact_and_shifted_hits():
    sheet = hits((0.0, 36), (0.5, 38), (1.0, 36))
    played = hits((0.004, 36), (0.49, 38), (1.02, 36))
    assert run(played, sheet) == [(0.004, 0.0), (0.49, 0.5), (1.02, 1.0)]


def test_hit_goes_to_nearest_note_not_a_taken_one():
    # 16ths at 70 ms; a hit 40 ms late is closer to the next note, so it must not be paired back
    sheet = hits((0.0, 38), (0.07, 38), (0.14, 38))
    played = hits((0.0, 38), (0.11, 38))
    assert run(played, sheet) == [(0.0, 0.0), (0.11, 0.14)]


def test_window_is_half_gap_and_capped():
    sheet = hits((0.0, 36), (0.07, 36))
    assert run(hits((0.036, 36)), sheet)[0][1] == 0.07     # nearer the second note
    assert run(hits((0.5, 36)), hits((0.0, 36), (2.0, 36)), window=0.1)[0][1] is None  # beyond cap


def test_duplicate_hit_one_wins_other_extra():
    sheet = hits((1.0, 38))
    played = hits((0.99, 38), (1.005, 38))
    assert run(played, sheet) == [(0.99, None), (1.005, 1.0)]


def test_family_fallback_and_exact_preference():
    sheet = hits((1.0, 49), (1.0, 57), (2.0, 45))
    played = hits((1.01, 57), (1.02, 98), (2.01, 47))   # crash_2, a choked crash_2, wrong tom
    run(played, sheet)
    assert [(p.midi, p.match.midi) for p in played] == [(57, 57), (98, 49), (47, 45)]


def test_different_families_never_match():
    assert run(hits((1.0, 38)), hits((1.0, 36))) == [(1.0, None)]


def test_simultaneous_same_drum_family_both_matched():
    sheet = hits((1.0, 42), (1.0, 44))     # hands on closed hat, foot on pedal
    played = hits((1.003, 44), (1.008, 42))
    run(played, sheet)
    assert all(p.match and p.match.midi == p.midi for p in played)


def test_offset_estimation_and_zero_preference():
    rng = np.random.default_rng(1)
    sheet = hits(*[(i * 0.25, 42) for i in range(200)], *[(i * 0.5, 36) for i in range(100)])
    late = [cp.Hit(h.time + 3.2 + rng.normal(0, 0.008), h.midi) for h in sheet]
    assert cp.estimate_offset(late, sheet) == pytest.approx(3.2, abs=0.004)
    consistently_late = [cp.Hit(h.time + 0.03, h.midi) for h in sheet]
    assert cp.estimate_offset(consistently_late, sheet) == 0.0  # 30 ms late is reported, not hidden


def test_read_notes_dedupes_and_sorts(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("# time_s  midi  name\n1.000000  38  snare\n0.500000  36  kick\n1.000000  38  snare\n\n")
    assert [(h.time, h.midi) for h in cp.read_notes(f)] == [(0.5, 36), (1.0, 38)]


def test_report_lines():
    sheet = hits((0.0, 36), (0.5, 38), (1.0, 49))
    played = hits((0.02, 36), (0.497, 38), (0.75, 42))
    cp.match(played, sheet)
    buf = io.StringIO()
    cp.report(played, sheet, 0.0, 0.010, out=buf)
    text = buf.getvalue()
    assert "+20.0 ms" in text and "late" in text
    assert "-3.0 ms" in text and "on time" in text
    assert "EXTRA" in text and "MISSED" in text
    assert "matched:          2" in text


@pytest.mark.skipif(not (OUT / "metallica-master_of_puppets.drums.txt").exists(), reason="song not imported")
def test_simulated_performance_of_real_song(tmp_path, capsys):
    """Jitter, a constant start offset, swapped cymbals, misses and extras on Master of Puppets."""
    rng = np.random.default_rng(7)
    sheet_path = OUT / "metallica-master_of_puppets.drums.txt"
    sheet = cp.read_notes(sheet_path)
    offset, lines, truth = 1.75, [], {}
    for h in sheet:
        if rng.random() < 0.03:
            continue  # missed
        err = float(np.clip(rng.normal(0.004, 0.012), -0.03, 0.03))
        midi = {49: 57, 57: 49}.get(h.midi, h.midi) if rng.random() < 0.1 else h.midi
        t = round(h.time + offset + err, 6)
        lines.append((t, midi))
        truth[(t, midi)] = h.time
    for _ in range(40):  # extras: snare hits well away from any snare note
        lines.append((round(float(rng.uniform(5, 480)), 6), 38))
    played_path = tmp_path / "played.txt"
    played_path.write_text("# time_s  midi  name\n" + "".join(
        f"{t:.6f}  {m}  {cp.drum_name(m)}\n" for t, m in sorted(lines)))

    played = cp.read_notes(played_path)
    est = cp.estimate_offset(played, sheet)
    assert est == pytest.approx(offset + 0.004, abs=0.003)
    cp.match(played, sheet, est)
    right = sum(1 for p in played if p.match and truth.get((p.time, p.midi)) == p.match.time)
    wrong = sum(1 for p in played if p.match and truth.get((p.time, p.midi)) not in (None, p.match.time))
    assert right / len(truth) > 0.97 and wrong / len(truth) < 0.01

    cp.main([str(played_path), str(sheet_path), "--summary-only"])
    assert "offset applied:   +1.75" in capsys.readouterr().out
