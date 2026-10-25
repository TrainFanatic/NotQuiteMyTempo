"""Write the backing-track fixtures for tests/test_backing_player.gd:
    ../../.venv/bin/python make_backing_fixtures.py      (needs soundfile, in the repo venv)

backing/backing.ogg   4.0 s, Ogg Vorbis, 48 kHz stereo like songs/<id>/backing.ogg: silence
                      with a 20 ms 1 kHz tone burst (sharp onset) at 1.0, 2.0 and 3.0 s
backing/click.ogg     the same format, bursts at 0.5, 1.5, 2.5 and 3.5 s (the optional click)
The burst onsets are the markers the test finds in the mixed output.
"""

from pathlib import Path

import numpy as np
import soundfile as sf

HERE = Path(__file__).resolve().parent / "backing"
RATE = 48000
LENGTH_S = 4.0


def bursts(times):
    a = np.zeros(int(LENGTH_S * RATE), np.float32)
    n = int(0.02 * RATE)
    t = np.arange(n) / RATE
    tone = 0.5 * np.sin(2 * np.pi * 1000 * t) * np.exp(-t / 0.008)
    for s in times:
        i = round(s * RATE)
        a[i : i + n] += tone
    return np.stack([a, a], axis=1)


HERE.mkdir(exist_ok=True)
sf.write(HERE / "backing.ogg", bursts([1.0, 2.0, 3.0]), RATE, format="OGG", subtype="VORBIS")
sf.write(HERE / "click.ogg", bursts([0.5, 1.5, 2.5, 3.5]), RATE, format="OGG", subtype="VORBIS")
