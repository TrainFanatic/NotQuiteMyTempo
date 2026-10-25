"""python -m pytest hub/tests -q

Onsets-only hits are judged against the note group ScoreGate gave them (error = onset - that
group's time), not re-searched by Chris's LiveMatcher with its own drift. Found on a real
library song (songs/bond_demo, a MIDI import): ride/splash triplets, cymbal re-triggers and a
pickup played into a restart bar pulled the matcher ~145 ms off, so a clean take graded as misses
and Fletcher cut off at bars 33-35 forever. Each detector artefact found there has a test here,
plus the whole song rendered clean and drifting through Kevin's transcriber."""

import subprocess
import sys

import numpy as np
import pytest

from hub.bridge import BarClock, Referee, ScoreGate, cp
from hub.tests.test_bridge import ROOT, notes_of

BPM = 143.0
BEAT = 60 / BPM


def ref_for(notes, bpm=BPM):
    return Referee([cp.Hit(t, m) for t, m in notes], BarClock(bpm))


def feed(ref, onsets):
    out = []
    for s in onsets:
        out += ref.feed_line(f"{s:.6f}  0  onset", rx=s)
    return notes_of(out + ref.settle())


def test_error_is_onset_minus_the_gates_group_and_livematcher_is_not_consulted():
    # ride + splash triplets with a snare on the beats, played 10 ms early throughout
    trip = BEAT / 3
    notes = []
    for k in range(24):
        t = 1.0 + k * trip
        notes += [(t, 51), (t, 55)] + ([(t, 40)] if k % 3 == 0 else [])
    ref = ref_for(notes)
    got = feed(ref, [1.0 + k * trip - 0.010 for k in range(24)])
    assert len(got) == len(notes) and all(n["grade"] == "perfect" for n in got)
    assert all(n["error_ms"] == pytest.approx(-10.0, abs=0.05) for n in got)
    assert not ref.matcher.recent  # LiveMatcher's drift never saw these
    assert ref.finish() == []  # all paired: nothing left to flush as a miss


def test_skipped_groups_are_misses_once_and_finish_does_not_repeat_them():
    notes = [(1.0 + k * 0.4, 38) for k in range(6)]
    ref = ref_for(notes)
    got = feed(ref, [1.0, 1.4, 2.6])  # 1.8 and 2.2 skipped
    assert [n["grade"] for n in got] == ["perfect", "perfect", "miss", "miss", "perfect"]
    tail = notes_of(ref.finish())
    assert [n["grade"] for n in tail] == ["miss"] and tail[0]["song_time"] == pytest.approx(3.0)
    assert ref.matcher.missed == 3


def test_a_retrigger_reaching_the_next_group_does_not_take_it():
    # splash at 1.0 re-attacks 70 ms into its decay; the snare + ride is a triplet (140 ms) later
    ref = ref_for([(1.0, 55), (1.14, 40), (1.14, 51)])
    got = feed(ref, [1.0, 1.07, 1.135])
    assert [(n["drum"], n["error_ms"]) for n in got] == [
        ("splash", 0.0), ("snare_electric", -5.0), ("ride", -5.0)]
    assert ref.gate.dropped == 1


def test_lookahead_gives_the_group_to_the_nearer_of_two_onsets():
    # 214 bpm eighths: a kick re-triggers 95 ms into its decay (45 ms early for the snare: not
    # enough for the jump rule), the real snare comes 48 ms later. The early onset is held, then
    # displaced by the nearer one.
    ref = Referee([cp.Hit(t, m) for t, m in [(1.0, 36), (1.14, 38), (1.28, 42)]], BarClock(214.0))
    got = feed(ref, [1.0, 1.095, 1.143, 1.28])
    assert [(n["drum"], n["error_ms"], n["grade"]) for n in got] == [
        ("kick", 0.0, "perfect"), ("snare", 3.0, "perfect"), ("hihat_closed", 0.0, "perfect")]
    assert ref.gate.dropped == 1


def test_an_early_hit_keeps_its_group_when_its_own_tail_follows():
    ref = ref_for([(1.0, 38), (1.5, 38)])
    got = feed(ref, [0.94, 0.98, 1.5])  # 60 ms early, then its double trigger 40 ms later
    assert [n["error_ms"] for n in got] == [-60.0, 0.0]


def test_held_onset_settles_on_tick_without_another_line():
    ref = ref_for([(1.0, 38), (1.5, 38)])
    ref.feed_line("0.950000  0  onset", rx=0.950 + 0.17)
    assert ref.gate.held is not None
    assert ref.tick(0.950 + 0.17 + 0.05) == []  # a nearer onset could still be on its way
    got = notes_of(ref.tick(0.950 + 0.17 + 0.2))
    assert [n["error_ms"] for n in got] == [-50.0] and ref.gate.held is None


def test_a_detected_pair_inside_one_group_does_not_steal_the_next():
    # 32nd-note snare pair 35 ms apart (one group), then snare + ride 35 ms after that
    ref = ref_for([(1.0, 40), (1.035, 40), (1.07, 40), (1.07, 51)])
    got = feed(ref, [0.983, 1.028, 1.048])
    assert [n["error_ms"] for n in got] == [-17.0, -17.0, -22.0, -22.0]


def test_a_cymbal_buried_in_the_stroke_before_it_is_not_a_miss_but_a_drum_is():
    ref = ref_for([(1.0, 40), (1.07, 55), (1.14, 40), (1.21, 40), (1.5, 38)])
    got = feed(ref, [1.0, 1.14, 1.5])  # no onset for the splash or the snare at 1.21
    assert [(n["drum"], n["grade"]) for n in got] == [
        ("snare_electric", "perfect"), ("snare_electric", "perfect"), ("snare_electric", "miss"),
        ("snare", "perfect")]
    assert ref.unheard == 1


def test_a_restart_pickup_does_not_take_the_downbeat():
    # bar 3 restart; the take's pre-roll has the tom fill's last note 80 ms before the downbeat
    bar = 4 * BEAT
    notes = [(2 * bar - 0.08, 45), (2 * bar, 36), (2 * bar, 49), (2 * bar + BEAT, 38)]
    ref = ref_for(notes)
    ref.align(3, 10.0)
    on = [10.0 - 0.081, 10.0 - 0.006, 10.0 + BEAT]
    got = feed(ref, on)
    assert [(n["drum"], n["error_ms"]) for n in got] == [("kick", -6.0), ("crash", -6.0), ("snare", 0.0)]


LIBRARY = ROOT / "songs"
LT = ROOT / "live_transcription"


@pytest.mark.skipif(not (LIBRARY / "bond_demo" / "drums.drums.txt").exists(), reason="songs/bond_demo missing")
@pytest.mark.skipif(not any((LT / "build" / f"libdrum_transcriber.{x}").exists() for x in ("dylib", "so")),
                    reason="build Kevin's native core first (live_transcription/README.md)")
@pytest.mark.parametrize("seed,sd,clean", [(1, 0.003, True), (5, 0.07, False)])
def test_bond_demo_rendered_through_the_transcriber(tmp_path, seed, sd, clean):
    """The real MIDI import, rendered with the sample kit (hub.app_e2e's takes), through
    transcribe_wav.py --onsets-only: clean = nearly all perfect/good and no bar Fletcher would
    stop on; drifting = graded badly throughout."""
    from hub import app_e2e as ae
    from hub.session import load_song

    song = load_song(LIBRARY, "bond_demo")
    wav, = ae.make_takes(tmp_path, song.sheet, [(seed, sd)])
    out = subprocess.run([sys.executable, str(LT / "tools/transcribe_wav.py"), str(wav), "--onsets-only",
                          "--mute-window-ms", "0", "--song-origin", str(-ae.LEAD_S),
                          "--output", str(tmp_path / "on.txt")], check=True, capture_output=True, text=True).stdout
    ref = Referee(cp.read_notes(song.sheet), BarClock(song.bpm_at(1), song.beats_per_bar, song.tempo_map))
    ref.align(1, 0.0)
    msgs = []
    for l in out.splitlines():
        if l.strip() and not l.startswith("#"):
            msgs += ref.feed_line(l, rx=float(l.split()[0]))
    notes = notes_of(msgs + ref.finish())
    grades = {g: sum(n["grade"] == g for n in notes) for g in ("perfect", "good", "off", "miss", "extra")}
    bad_bars = {}
    for n in notes:
        if n["grade"] in ("off", "miss"):
            bad_bars[n["bar"]] = bad_bars.get(n["bar"], 0) + 1
    print(f"\nbond_demo seed {seed} sd {sd}: {grades}, unheard {ref.unheard}, worst bars "
          f"{sorted(bad_bars.items(), key=lambda kv: -kv[1])[:4]}")
    total = sum(grades.values())
    if clean:
        assert grades["perfect"] + grades["good"] >= 0.98 * total
        assert grades["miss"] <= 5 and grades["off"] <= 5 and grades["extra"] <= 5
        assert max(bad_bars.values(), default=0) <= 3
        errs = [abs(n["error_ms"]) for n in notes if n["error_ms"] is not None]
        assert np.mean(errs) < 15
    else:
        assert grades["off"] + grades["miss"] >= 0.4 * total
