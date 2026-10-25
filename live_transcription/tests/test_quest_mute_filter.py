"""QuestMuteFilter: the onsets-only path drops the Quest's un-mute re-attacks, keeps real hits."""

from types import SimpleNamespace
import numpy as np
from common import QuestMuteFilter, RATE


def quest_like(hits, mute=(0.1, 0.15), seconds=2.0):
    """Ringing hits; the mic is muted to ~-90 dB from 0.1 to 0.15 s after each (Quest 2)."""
    rng = np.random.default_rng(4)
    a = 10 ** (-90 / 20) * rng.standard_normal(int(seconds * RATE))
    for t in hits:
        i, n = int(t * RATE), int(0.4 * RATE)
        ring = 0.3 * rng.standard_normal(n) * np.exp(-np.arange(n) / (0.15 * RATE))
        a[i : i + n] += ring[: len(a) - i]
        a[i + int(mute[0] * RATE) : i + int(mute[1] * RATE)] = 10 ** (-90 / 20)
    return a.astype(np.float32)


def run(audio, onsets, delay=0.17, **kw):
    """Feed 10 ms blocks; each onset reaches the filter `delay` after its sample, like the core's."""
    f, kept, todo = QuestMuteFilter(**kw), [], list(onsets)
    for start in range(0, len(audio), 480):
        f.feed(audio[start : start + 480], start)
        while todo and (start + 480) / RATE >= todo[0] + delay:
            t = todo.pop(0)
            if f.keep(SimpleNamespace(sample=int(t * RATE))):
                kept.append(t)
    return kept


def test_unmute_re_attack_dropped_real_hits_kept():
    audio = quest_like([0.2, 0.8])
    # 0.2 and 0.8 are hits; 0.37 is the ring coming back after the mute
    assert run(audio, [0.2, 0.37, 0.8]) == [0.2, 0.8]


def test_no_mute_in_between_keeps_a_close_onset():
    audio = quest_like([0.2, 0.32], mute=(0.0, 0.0))
    assert run(audio, [0.2, 0.32]) == [0.2, 0.32]


def test_past_the_window_a_hit_after_silence_is_kept():
    audio = quest_like([0.2, 0.6])
    assert run(audio, [0.2, 0.6]) == [0.2, 0.6]
    assert run(audio, [0.2, 0.6], window_s=0.5) == [0.2]  # the flag really is the window
