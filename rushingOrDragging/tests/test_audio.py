import wave

import numpy as np
import pytest

import test_file_with_audio as audio
from songsterr.drum_map import GM_DRUMS

SR = audio.SAMPLE_RATE
REAL_KIT = audio.DEFAULT_KIT_DIR
needs_real_kit = pytest.mark.skipif(not any(REAL_KIT.glob("*.wav")), reason="drum kit not downloaded")


def onsets(x, threshold=1e-6, min_gap=1000):
    """Sample indices where sound starts after at least `min_gap` silent samples (or at 0)."""
    loud = np.flatnonzero(np.abs(x) > threshold)
    if len(loud) == 0:
        return []
    keep = np.concatenate([[True], np.diff(loud) > min_gap])
    return [int(i) for i in loud[keep]]


@pytest.fixture
def fake_kit(tmp_path):
    """A kit where every sample is 30 samples of silence, then a 200-sample tone."""
    kit = tmp_path / "kit"
    kit.mkdir()
    tone = np.concatenate([np.zeros(30), 0.5 * np.sin(np.arange(200) / 3)])
    prefixes = {p for p, _ in audio.KIT_MAP.values()} | {"37-Pearl-14-SideStick", "35-JojoMayer-2-StickClick"}
    for p in prefixes:
        for layer in range(1, 6):
            audio.write_wav(kit / f"{p}-{layer}.wav", tone * layer / 5)
    audio.sound_for.cache_clear()
    yield kit
    audio.sound_for.cache_clear()


def test_hits_start_on_exact_sample_after_trimming_lead_silence(fake_kit):
    times = [0.25, 1.0, 1.75, 2.5333]
    x = audio.render([(t, 38) for t in times], kit_dir=fake_kit)
    assert onsets(x) == [round(t * SR) for t in times]


def test_start_offset_and_end_window(fake_kit):
    x = audio.render([(1.0, 36), (5.0, 36), (9.0, 36)], start=4.0, end=6.0, kit_dir=fake_kit)
    assert len(x) == 2 * SR + 1
    assert onsets(x) == [round(1.0 * SR)]


def test_negative_time_kept_with_negative_start(fake_kit):
    x = audio.render([(-0.05, 38), (0.0, 36)], start=-0.1, kit_dir=fake_kit)
    assert onsets(x)[0] == round(0.05 * SR)


def test_layer_changes_loudness(fake_kit):
    soft = audio.sound_for(38, fake_kit, 1)
    hard = audio.sound_for(38, fake_kit, 5)
    assert np.abs(hard).max() == pytest.approx(5 * np.abs(soft).max(), rel=1e-3)


def test_pitch_and_choke_options(fake_kit):
    base = audio.sound_for(48, fake_kit, 4)     # 12" tom, unpitched
    low = audio.sound_for(45, fake_kit, 4)      # same sample pitched down -> longer
    assert len(low) > len(base)
    crash = audio.sound_for(57, fake_kit, 4)
    assert len(audio.choke(np.ones(SR), 0.15)) < SR and len(crash) > 0


def test_no_clipping_on_dense_mix(fake_kit):
    notes = [(i * 0.001, m) for i in range(300) for m in (36, 38, 49, 42)]
    assert np.abs(audio.render(notes, kit_dir=fake_kit)).max() <= 0.98 + 1e-9


def test_trim_to_onset_starts_at_zero_crossing():
    x = np.concatenate([np.zeros(100), 1e-4 * np.ones(5), np.sin(np.arange(400) / 4)])
    y = audio.trim_to_onset(x)
    assert 280 < len(y) <= 405 and abs(y[0]) < 0.3


def test_cli_end_to_end(tmp_path, fake_kit, capsys):
    notes = tmp_path / "n.txt"
    notes.write_text("# header\n# time_s  midi  name\n0.000000  36  kick\n0.500000  38  snare\n0.6  87  surdo_open\n")
    audio.main([str(notes), "--kit", str(fake_kit)])
    out = capsys.readouterr()
    assert (tmp_path / "n.wav").exists() and "3 hits" in out.out
    assert "[87]" in out.err  # warned about the stand-in sound
    with wave.open(str(tmp_path / "n.wav")) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, SR)


def test_bad_line_reports_location(tmp_path):
    notes = tmp_path / "n.txt"
    notes.write_text("0.0 36 kick\nnot a note\n")
    with pytest.raises(SystemExit, match=r"n.txt:2"):
        audio.read_notes(notes)


@needs_real_kit
def test_real_kit_hit_lands_on_exact_sample():
    audio.sound_for.cache_clear()
    audio.lead_in.cache_clear()
    for midi in audio.KIT_MAP:
        t = 0.5
        x = audio.render([(t, midi)])
        hit = np.flatnonzero(np.abs(x) >= 0.02 * np.abs(x).max())[0]
        assert hit == round(t * SR), midi


@needs_real_kit
def test_real_kit_has_every_mapped_sample():
    audio.sound_for.cache_clear()
    for midi in list(GM_DRUMS) + [120]:
        s = audio.sound_for(midi)
        assert len(s) > 100 and np.isfinite(s).all() and np.abs(s).max() > 0.05, midi
        # trimmed: the hit is within the first millisecond
        assert np.flatnonzero(np.abs(s) >= 0.02 * np.abs(s).max())[0] <= SR // 1000, midi
