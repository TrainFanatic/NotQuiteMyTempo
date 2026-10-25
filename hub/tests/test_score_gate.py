"""python -m pytest hub/tests -q

The model-free live path: Kevin's --onsets-only lines (midi 0, drum unknown) -> ScoreGate ->
Chris's LiveMatcher. Performances are his drift simulator with the drums stripped; one test
renders his fixture with the sample kit and runs it through the real transcriber."""

import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from hub.bridge import ScoreGate, cp
from hub.tests.test_bridge import B, BPM, Config, ROOT, notes_of, referee, sheet_path, td  # noqa: F401


def onset_take(sheet_path, tmp_path, seed, sd, tails=()):
    """Chris's drifting take as the onset detector would hear it: one onset per chord, at its
    earliest attack, drum unknown; `tails` = indices of chords that also re-trigger 30-55 ms
    later. Returns (lines, truth {(sheet time, midi): error s})."""
    played = td.make_drifting(sheet_path, tmp_path / "perf.txt", seed=seed, drift_sd=sd)
    chords: dict[float, list] = {}
    for (t, m), v in played.items():
        chords.setdefault(v["sheet"], []).append((t, m))
    rng = np.random.default_rng(seed)
    onsets, truth = [], {}
    for k, (sheet_t, hits) in enumerate(sorted(chords.items())):
        first = min(t for t, _ in hits)
        onsets.append(first)
        if k in tails:
            onsets.append(first + rng.uniform(0.030, 0.055))
        truth.update({(sheet_t, m): first - sheet_t for _, m in hits})
    return [f"{t:.6f}  0  onset" for t in sorted(onsets)], truth


def judge(sheet_path, lines):
    ref = referee(sheet_path)
    msgs = [m for l in lines for m in ref.feed_line(l, rx=float(l.split()[0]))]
    msgs += ref.settle()  # the input ended: the gate's held onset (the bridge loop does this)
    return ref, notes_of(msgs)


@pytest.mark.parametrize("seed,sd", [(3, 0.04), (5, 0.07), (11, 0.015)])
def test_gated_onsets_give_the_true_errors_and_drop_tails(sheet_path, tmp_path, seed, sd):
    tails = set(range(1, 200, 7))
    lines, truth = onset_take(sheet_path, tmp_path, seed, sd, tails)
    ref, notes = judge(sheet_path, lines)
    judged = [n for n in notes if n["error_ms"] is not None]
    assert len(judged) == len(truth)  # every written note paired, chords get all their drums
    assert not [n for n in notes if n["error_ms"] is None]  # no misses, no extras
    assert ref.gate.dropped == len([k for k in tails if k < len({s for s, _ in truth})])
    sheet = {(h.time, h.midi) for h in cp.read_notes(sheet_path)}
    for n in judged:  # song_time - error = the written note's time
        key = min(sheet, key=lambda k: abs(k[0] - (n["song_time"] - n["error_ms"] / 1000))
                  + (0 if cp.drum_name(k[1]) == n["drum"] else 1))
        assert n["error_ms"] == pytest.approx(truth[key] * 1000, abs=0.1)


def test_far_onsets_are_extras_and_known_drums_skip_the_gate(sheet_path):
    sheet = cp.read_notes(sheet_path)
    gate = ScoreGate(sheet)
    assert gate.assign(sheet[0].time + 0.004) == [42]
    assert gate.assign(sheet[0].time + 0.040) == []  # tail
    assert gate.assign(sheet[0].time + 0.4) is None  # between groups: extra
    ref = referee(sheet_path)
    first_chord = [n for n in sheet if n.time == sheet[8].time]  # kick + crash at 5.106
    assert sheet[11].midi == 38  # snare at 5.745
    assert len(first_chord) == 2
    out = notes_of(ref.feed_line(f"{first_chord[0].time:.6f}  0  onset", rx=0.0))
    played = [n for n in out if n["error_ms"] is not None]  # the 8 hi-hats before it are misses
    assert sorted(n["drum"] for n in played) == sorted(n.name for n in first_chord)
    t = first_chord[0].time
    assert ref.feed_line(f"{t + 0.08:.6f}  0  onset", rx=0.0) == []  # the kick's own re-trigger
    out = notes_of(ref.feed_line(f"{t + 0.5:.6f}  0  onset", rx=0.0))  # nowhere near a note
    assert out[-1]["grade"] == "extra" and out[-1]["error_ms"] is None
    out = notes_of(ref.feed_line(f"{sheet[11].time:.6f}  38  snare", rx=0.0))
    assert out[-1]["drum"] == "snare" and out[-1]["error_ms"] == 0.0
    assert ref.gate.dropped == 1  # the snare line took the old path, not the gate


def mood(sheet_path, lines):
    br, ref = B.Brain(Config(dry_run=True)), referee(sheet_path)
    for l in lines:
        s = float(l.split()[0])
        for typ, d in ref.feed_line(l, rx=s):
            eff = br.on_session_state(d, s) if typ == "session.state" else br.on_note(d, s) + br.tick(s)
            for e in eff:
                if isinstance(e, B.Cutoff):
                    return br.anger, e.facts
    return br.anger, None


def test_fletcher_cuts_off_heavy_drift_from_onsets_alone(sheet_path, tmp_path):
    lines, _ = onset_take(sheet_path, tmp_path, seed=5, sd=0.07, tails=set(range(0, 200, 5)))
    _, facts = mood(sheet_path, lines)
    assert facts is not None and facts.current_bar >= 3
    assert facts.kind in ("rushing", "dragging")


def test_fletcher_stays_calm_on_mild_wobble_from_onsets_alone(sheet_path, tmp_path):
    lines, _ = onset_take(sheet_path, tmp_path, seed=11, sd=0.015, tails=set(range(0, 200, 5)))
    anger, facts = mood(sheet_path, lines)
    assert facts is None and anger < 0.3


LT = ROOT / "live_transcription"


@pytest.mark.skipif(not (LT / "build" / "libdrum_transcriber.dylib").exists()
                    and not (LT / "build" / "libdrum_transcriber.so").exists(),
                    reason="build Kevin's native core first (live_transcription/README.md)")
def test_rendered_audio_through_transcriber_and_gate(sheet_path, tmp_path):
    """Kit audio of a drifting take -> transcribe_wav.py --onsets-only -> gate -> matcher."""
    import soundfile as sf
    sys.path.insert(0, str(ROOT / "datacollection"))
    import sim_session as ss

    truth = td.make_drifting(sheet_path, tmp_path / "perf.txt", seed=3, drift_sd=0.03)
    lead = 0.5  # audio starts 0.5 s before song time 0
    events = [(t, m, 4) for t, m in sorted(truth)]
    n = int((max(t for t, _ in truth) + lead + 2.0) * 48000)
    x = ss.render(events, 48000, n, lambda t, m: t + lead)
    x = 0.8 * x / np.abs(x).max() + 10 ** (-70 / 20) * np.random.default_rng(1).standard_normal(n)
    sf.write(tmp_path / "take.wav", x.astype(np.float32), 48000, subtype="FLOAT")
    out = subprocess.run([sys.executable, str(LT / "tools/transcribe_wav.py"), str(tmp_path / "take.wav"),
                          "--onsets-only", "--song-origin", str(-lead), "--output", str(tmp_path / "on.txt")],
                         check=True, capture_output=True, text=True).stdout
    lines = [l for l in out.splitlines() if not l.startswith("#")]
    assert lines and all(l.endswith("  0  onset") for l in lines)
    ref, notes = judge(sheet_path, lines)
    by_sheet = {}
    for (t, m), v in truth.items():
        by_sheet.setdefault(v["sheet"], []).append(t)
    errs = []
    for nt in notes:
        if nt["error_ms"] is None:
            continue
        sheet_t = min(by_sheet, key=lambda s: abs(s - (nt["song_time"] - nt["error_ms"] / 1000)))
        errs.append(nt["error_ms"] - (min(by_sheet[sheet_t]) - sheet_t) * 1000)  # vs earliest attack
    errs = np.abs(errs)
    misses = sum(n["grade"] == "miss" for n in notes)
    extras = sum(n["grade"] == "extra" for n in notes)
    print(f"\naudio: {len(lines)} onsets, {len(errs)}/{len(truth)} notes judged, {misses} miss, "
          f"{extras} extra, {ref.gate.dropped} tails dropped; |error - truth| mean "
          f"{errs.mean():.2f} ms, p95 {np.percentile(errs, 95):.2f} ms, max {errs.max():.2f} ms")
    assert len(errs) >= 0.95 * len(truth) and extras <= 0.05 * len(truth)
    assert np.percentile(errs, 95) < 10.0


# Real Quest 2 takes (live_transcription/data/recordings/single_{mid,high}_tom_01, single strokes
# confirmed by the drummer): labels.csv hits, and what transcribe_wav.py --onsets-only emitted
# around them with its Quest mute filter on. The Quest mutes the mic ~0.1 s into a hit and
# un-mutes it; where that dip was too shallow for the filter, the ring re-attacks 158-173 ms later.
QUEST_HITS = [21.511292, 22.040146, 22.876042, 39.932396, 40.565125]  # high tom shifted +30 s
QUEST_ONSETS = [21.511292, 21.535, 21.555, 21.6, 22.040104, 22.060417, 22.1985, 22.246208,
                22.876021, 39.932542, 39.965708, 40.105063, 40.565042, 40.615917]


def test_quest_unmute_re_attacks_are_dropped_not_extras():
    sheet = [cp.Hit(t, 38) for t in QUEST_HITS]
    gate = ScoreGate(sheet)
    out = [gate.assign(s) for s in QUEST_ONSETS]
    assert [s for s, r in zip(QUEST_ONSETS, out) if r] == pytest.approx(QUEST_HITS, abs=0.001)
    assert None not in out  # no phantom reaches the matcher as an extra
    old = ScoreGate(sheet, ring=0.15)  # the previous default let these re-attacks through
    assert [old.assign(s) for s in QUEST_ONSETS].count(None) == 2
