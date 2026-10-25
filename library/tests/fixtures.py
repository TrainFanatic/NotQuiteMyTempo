"""Synthetic MIDI files for the tests (built with library.smf, no external files)."""

from library import smf

DIV = 480


def pickup_song(pickup_beats: int = 1, bars: int = 6, bpm1: float = 120.0, bpm2: float = 90.0,
                change_bar: int = 3, fmt: int = 1, probe: bool = False) -> bytes:
    """A pickup of `pickup_beats` quarter notes (its own n/4 signature), then 4/4 bars.

    Bar numbers below are song bars (bar 1 = first full bar). Tempo changes to bpm2 at the
    downbeat of `change_bar`. probe=True: the only band part is a woodblock (GM 115, sharp attack,
    short decay) on every downbeat and in the pickup, for measuring backing alignment. Drums: kick on every beat, snare on 2 and 4, crash on bar 1, plus a
    hi-hat and a tambourine (band percussion) in the pickup. Piano: a short C5 on every downbeat
    and one in the pickup. Bass: a long note per bar. Trumpet: a phrase in bars 2-3.
    """
    P = pickup_beats * DIV
    bar = 4 * DIV
    conductor = [smf.track_name("Pickup test"), smf.tempo(0, bpm1)]
    if pickup_beats:
        conductor += [smf.time_sig(0, pickup_beats, 4), smf.time_sig(P, 4, 4)]
    else:
        conductor += [smf.time_sig(0, 4, 4)]
    conductor.append(smf.tempo(P + (change_bar - 1) * bar, bpm2))
    drums = [smf.track_name("Drums")]
    piano = [smf.track_name("Piano"), smf.program(0, 0, 0)]
    bass = [smf.track_name("Bass"), smf.program(0, 1, 33)]
    tpt = [smf.track_name("Trumpet"), smf.program(0, 2, 56)]
    if pickup_beats:
        drums += smf.note_events(9, 42, 0, 60) + smf.note_events(9, 54, 0, 60)
        piano += smf.note_events(0, 72, 0, 120)
    for b in range(bars):
        s = P + b * bar
        for k in range(4):
            drums += smf.note_events(9, 36, s + k * DIV, s + k * DIV + 60)
            if k in (1, 3):
                drums += smf.note_events(9, 38, s + k * DIV, s + k * DIV + 60)
        if b == 0:
            drums += smf.note_events(9, 49, s, s + 60)
        piano += smf.note_events(0, 72, s, s + 120, 110)
        bass += smf.note_events(1, 36, s, s + bar - 10, 90)
    tpt += smf.note_events(2, 67, P + bar, P + 3 * bar - 10)
    tracks = [conductor, drums, piano, bass, tpt]
    if probe:
        wood = [smf.track_name("Piano"), smf.program(0, 0, 115)] + smf.note_events(0, 72, 0, 60)
        for b in range(bars):
            wood += smf.note_events(0, 72, P + b * bar, P + b * bar + 60, 120)
        tracks = [conductor, drums, wood]
    if fmt == 0:
        allev = [e for t in tracks for e in t if not (e.meta_type == 0x03)]
        return smf.MidiFile(0, DIV, [smf.Track(0, smf.sort_events(allev))]).to_bytes()
    return smf.MidiFile(1, DIV, [smf.Track(i, smf.sort_events(t)) for i, t in enumerate(tracks)]).to_bytes()


def expected_bar_start(b: int, bpm1=120.0, bpm2=90.0, change_bar=3) -> float:
    """Song time of the downbeat of song bar b (1-based), with MIDI's whole-microsecond tempos."""
    q1, q2 = round(60_000_000 / bpm1) / 1e6, round(60_000_000 / bpm2) / 1e6
    if b <= change_bar:
        return (b - 1) * 4 * q1
    return (change_bar - 1) * 4 * q1 + (b - change_bar) * 4 * q2
