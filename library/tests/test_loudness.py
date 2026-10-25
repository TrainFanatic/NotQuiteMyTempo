"""Backing loudness: render.loudness() is BS.1770 (checked on its reference sine), and
render.normalise() lands every song on TARGET_LUFS unless the peak ceiling stops it."""

import numpy as np
import pytest

from library import render

SR = render.SR


def sine(db, f=997.0, s=5.0, ch=1):
    t = np.arange(int(s * SR)) / SR
    x = (10 ** (db / 20) * np.sin(2 * np.pi * f * t)).astype(np.float32)
    return np.repeat(x[:, None], ch, axis=1)


def test_loudness_matches_bs1770_reference():
    assert render.loudness(sine(-20)) == pytest.approx(-23.01, abs=0.05)  # mono 997 Hz
    assert render.loudness(sine(-20, ch=2)) == pytest.approx(-20.0, abs=0.05)
    assert render.loudness(np.zeros((SR, 2), np.float32)) == float("-inf")


def test_normalise_to_target_or_peak_ceiling():
    quiet = sine(-40, ch=2)  # steady: reaches the target
    assert render.loudness(render.normalise(quiet)) == pytest.approx(render.TARGET_LUFS, abs=0.05)
    loud = sine(-6, ch=2)
    assert render.loudness(render.normalise(loud)) == pytest.approx(render.TARGET_LUFS, abs=0.05)
    spiky = sine(-50, ch=2)
    spiky[SR] = 0.9  # a peak 50 dB over the rest: the -1 dBFS ceiling wins
    y = render.normalise(spiky)
    assert np.abs(y).max() == pytest.approx(render.PEAK, rel=1e-4)
    assert render.loudness(y) < render.TARGET_LUFS
