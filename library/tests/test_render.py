"""backing.ogg alignment: sample 0 = song time 0 (bar 1 downbeat), across a pickup and a tempo change."""

import subprocess

import numpy as np
import pytest
import soundfile as sf

from library import render, smf
from library.midi_import import backing_midi, import_midi
from library.tests.conftest import needs_fluidsynth
from library.tests.fixtures import expected_bar_start, pickup_song

SR = render.SR


def onsets_ms(x, times, thr_frac=0.01):
    """Per expected time, (first sample above thr_frac*peak within -100..+50 ms) - expected, in ms."""
    x = np.abs(x)
    thr = x.max() * thr_frac
    out = []
    for t in times:
        i0 = max(int((t - 0.1) * SR), 0)
        seg = x[i0:int((t + 0.05) * SR)]
        out.append(((i0 + np.flatnonzero(seg > thr)[0]) / SR - t) * 1000)
    return np.array(out)


@needs_fluidsynth
def test_fluidsynth_block_latency_is_compensated():
    """A sharp note exactly on a block boundary and one off it: 0..1.33 ms late, never early."""
    for tick in (0, 960, 1000, 1234):
        mf = smf.MidiFile(1, 480, [smf.Track(0, [smf.tempo(0, 120)]),
                                   smf.Track(1, smf.sort_events([smf.program(0, 0, 115)] + smf.note_events(0, 72, tick, tick + 60)))])
        a = render.render_midi(mf.to_bytes())
        err = onsets_ms(a[:, 0], [tick / 960], thr_frac=1e-3)[0]
        assert -0.05 <= err <= 1.4, (tick, err)


@needs_fluidsynth
def test_backing_aligned_to_bar1_downbeat_with_pickup_and_tempo_change(tmp_path):
    (tmp_path / "probe.mid").write_bytes(pickup_song(probe=True))
    info = import_midi(tmp_path / "probe.mid", songs_dir=tmp_path, log=lambda *_: None)
    assert info["backing"] and info["pickup_s"] == 0.5
    downbeats = [expected_bar_start(b) for b in range(1, 7)]

    # the render itself (before Vorbis): within FluidSynth's 64-sample block
    raw = render.render_midi(backing_midi(smf.parse(pickup_song(probe=True)), set()).to_bytes(), start_s=0.5)
    err = onsets_ms(raw[:, 0], downbeats)
    assert np.all(err >= -0.05) and np.all(err <= 1.4), err

    # the published file: 48 kHz stereo Ogg Vorbis; codec pre-echo may lead a transient by ~2 ms
    a, sr = sf.read(str(tmp_path / "probe" / "backing.ogg"), always_2d=True)
    assert sr == SR and a.shape[1] == 2
    assert sf.info(str(tmp_path / "probe" / "backing.ogg")).format == "OGG"
    err = onsets_ms(a[:, 0], downbeats)
    assert np.all(np.abs(err) <= 3.0), err
    assert len(a) / SR >= info["duration_s"]

    # an independent decoder (ffmpeg) agrees sample for sample: no decoder-dependent offset
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(tmp_path / "probe" / "backing.ogg"),
                        "-f", "f32le", "-ac", "2", "-"], capture_output=True)
    if r.returncode == 0:
        y = np.frombuffer(r.stdout, np.float32).reshape(-1, 2)
        assert len(y) == len(a) and np.abs(y - a).max() < 1e-4


@needs_fluidsynth
def test_drum_kit_is_not_in_the_backing(tmp_path):
    """Only the kit is removed: a drums-only file renders (almost) silence, band percussion stays."""
    mf = smf.parse(pickup_song())
    kit_only = smf.MidiFile(1, mf.division, [mf.tracks[0], mf.tracks[1]])
    a = render.render_midi(backing_midi(kit_only, set()).to_bytes(), start_s=0.5)
    b = render.render_midi(backing_midi(kit_only, set()).to_bytes(), start_s=0.0)
    assert np.abs(a[SR:]).max() < 1e-4     # once the pickup tambourine has rung out: nothing
    assert np.abs(b[:SR // 2]).max() > 1e-2  # the pickup's tambourine is band percussion


def test_click_track_on_the_beats(tmp_path):
    (tmp_path / "c.mid").write_bytes(pickup_song())
    import_midi(tmp_path / "c.mid", songs_dir=tmp_path, backing=False, log=lambda *_: None)
    a, sr = sf.read(str(tmp_path / "c" / "click.ogg"), always_2d=True)
    beats = [expected_bar_start(b) + k * (expected_bar_start(b + 1) - expected_bar_start(b)) / 4
             for b in range(1, 7) for k in range(4)]
    err = onsets_ms(a[:, 0], beats, thr_frac=0.05)
    assert np.all(np.abs(err) <= 3.0), err
