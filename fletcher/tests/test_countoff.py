"""Count-off tests on dry-run (synthetic) clips: no keys, deterministic."""

import asyncio

import numpy as np
import pytest

from fletcher import countoff as co
from fletcher.config import Config
from fletcher.generate_countoff import generate, synth_word


@pytest.fixture(scope="module")
def clip_dir(tmp_path_factory):
    d = tmp_path_factory.mktemp("countoff")
    asyncio.run(generate(Config(), d, None, dry_run=True))
    return d


@pytest.mark.parametrize("bpm", [60, 94, 120, 200, 260])
def test_schedule_math(bpm):
    beat = 60 / bpm
    s = co.schedule(bpm)
    assert [c for _, c in s] == [co.clip_id(w, "sharp") for w in ("five", "six", "seven", "eight")]
    assert [t for t, _ in s] == pytest.approx([-4 * beat, -3 * beat, -2 * beat, -beat])
    two = co.schedule(bpm, bars=2, style="menacing")
    assert [c for _, c in two] == [co.clip_id(w, "quiet") for w in co.WORDS]
    assert two[-1][0] == pytest.approx(-beat) and two[0][0] == pytest.approx(-8 * beat)


def test_schedule_three_four_and_styles():
    s = co.schedule(90, beats_per_bar=3)
    assert [c for _, c in s] == [co.clip_id(w, "sharp") for w in ("one", "two", "three")]
    assert [t for t, _ in s] == pytest.approx([-2.0, -4 / 3, -2 / 3])
    assert [c for _, c in co.schedule(120, style="build")] == [
        "co_five_quiet", "co_six_quiet", "co_seven_sharp", "co_eight_shout"]
    assert all(c.endswith("_shout") for _, c in co.schedule(120, style="shouting"))


def test_bpm_range_edges():
    assert len(co.schedule(co.BPM_MIN)) == 4 and len(co.schedule(co.BPM_MAX)) == 4
    for bad in (co.BPM_MIN - 1, co.BPM_MAX + 1, 0):
        with pytest.raises(ValueError):
            co.schedule(bad)
    with pytest.raises(ValueError):
        co.schedule(120, beats_per_bar=4, bars=3)
    with pytest.raises(ValueError):
        co.schedule(120, style="polite")


def test_detect_onset_ignores_hiss_and_silence():
    x = synth_word("six", "sharp")  # 80 ms silence + 70 ms hiss + vowel at 150 ms
    assert co.detect_onset(x, co.CLIP_SR) == pytest.approx(0.150, abs=0.004)


def test_manifest_onsets_measured(clip_dir):
    clips = co.load_clips(clip_dir)
    assert len(clips) == 24
    for c in clips.values():
        assert c.onset_s == pytest.approx(co.detect_onset(c.audio, co.CLIP_SR), abs=1e-4)
    # hissy words have a longer pre-onset than vowel-initial ones
    assert clips["co_six_sharp"].onset_s > clips["co_eight_sharp"].onset_s + 0.05


CASES = [(b, bpb, bars, st) for b in (40, 60, 94, 120, 200, 260)
         for bpb, bars, st in ((4, 1, "build"), (4, 2, "shouting"), (3, 1, "menacing"))]


@pytest.mark.parametrize("bpm,bpb,bars,style", CASES)
def test_render_onsets_on_beat_no_overlap_no_clip(clip_dir, bpm, bpb, bars, style):
    sched = co.schedule(bpm, bpb, bars, style)
    beat = 60 / bpm
    buf, t0 = co.render_array(sched, clip_dir=clip_dir)
    assert t0 == pytest.approx(sched[0][0] - beat)
    assert len(buf) == round(-t0 * co.CLIP_SR)  # ends exactly at the downbeat
    errs = co.measure_onsets(buf, t0, sched)
    assert max(abs(e) for e in errs) <= 0.002, errs
    assert np.abs(buf).max() <= 1.0
    pcm = np.frombuffer(co.render(sched, clip_dir=clip_dir), dtype="<i2")
    assert len(pcm) == len(buf) and np.abs(pcm).max() < 32767
    placed = co.plan(sched, clip_dir)
    for a, b in zip(placed, placed[1:]):
        assert a.end_s <= b.start_s + 1e-9
    assert placed[-1].end_s <= 0.0
    assert placed[0].start_s >= t0


def test_long_words_are_shortened(clip_dir):
    sched = co.schedule(260, style="shouting")
    beat = 60 / 260
    placed = co.plan(sched, clip_dir)
    assert all(p.natural_s > beat for p in placed)  # the synthetic shouts are longer than a beat
    for p in placed:
        assert p.onset_s <= co.PRE_FRAC * beat + 1e-9  # pre-onset part fits before the beat
        assert len(p.audio) / co.CLIP_SR - p.onset_s <= beat - co.GAP_S + 1e-9  # rest fits in it
        assert len(p.audio) / co.CLIP_SR < p.natural_s
        assert 1.0 < p.ratio <= co.MAX_RATIO
    slow = co.plan(co.schedule(60, style="shouting"), clip_dir)
    assert all(p.ratio == 1.0 and not p.trimmed for p in slow)


def test_wsola_keeps_pitch_and_scales_length():
    sr = co.CLIP_SR
    t = np.arange(int(0.6 * sr)) / sr
    x = 0.5 * np.sin(2 * np.pi * 200 * t)
    y = co.wsola(x, 1.5, sr)
    assert len(y) == round(len(x) / 1.5)
    spec = np.abs(np.fft.rfft(y[1000:-1000] * np.hanning(len(y) - 2000)))
    f = np.fft.rfftfreq(len(y) - 2000, 1 / sr)[np.argmax(spec)]
    assert f == pytest.approx(200, abs=5)


def test_resampled_render_keeps_length(clip_dir):
    sched = co.schedule(120)
    a = co.render(sched, clip_dir=clip_dir)
    b = co.render(sched, sample_rate=48000, clip_dir=clip_dir)
    assert len(b) == pytest.approx(2 * len(a), abs=4)
