"""MIDI file -> songs/<song_id>/ (song.json, drums sheet + tempo map, arrangement, backing).

    python -m library import-midi path/to/song.mid [--id ID] [--title T] [--artist A]

Time origin: song time 0 is the downbeat of bar 1, exactly as in Chris's sheets. Bar 1 is the
first bar of the MIDI file, except when the file opens with a pickup (anacrusis), i.e. a first
bar shorter than the second whose length comes from its own time signature (MuseScore and most
notation programs export a pickup that way). Then bar 1 is the first full bar, the pickup lies at
negative song time, and `song.json` records `pickup_s`. Because .drums.txt times are
non-negative and backing.ogg starts at song time 0, pickup notes are not in the sheet or the
backing; `midi_t0_s` says where song time 0 is in arrangement.mid (seconds from MIDI time 0).

Drums: every note on MIDI channel 10 whose number is in one of Chris's drum families
(compare_performance.FAMILIES: kick, snare, hi-hat, crash-like, ride, toms) goes to the sheet
unchanged. Other channel-10 sounds (tambourine, cowbell, claps...) are band percussion: they stay
in the backing. `--drum-track` reads a named track as drums whatever its channel.
"""

from __future__ import annotations

import datetime as dt
import shutil
from fractions import Fraction
from pathlib import Path

from . import catalog, render, smf
from .catalog import ARRANGEMENT, BACKING, BAND_TRACKS, CLICK, SHEET, SONG_JSON, TEMPO

catalog.chris_on_path()
import compare_performance as cp  # noqa: E402
from songsterr import tempo_map as chris_tm  # noqa: E402
from songsterr.drum_map import drum_name  # noqa: E402

KIT = frozenset(cp.FAMILY_OF)

GM_FAMILIES = ["piano", "chromatic_perc", "organ", "guitar", "bass", "strings", "ensemble", "brass",
               "reed", "pipe", "synth_lead", "synth_pad", "synth_fx", "ethnic", "percussive", "sfx"]
# Track-name keywords -> Adam's band targets (scene_director.resolve: member, section or "saxes").
NAME_SECTIONS = [
    (("trumpet", "tpt", "flugel", "cornet"), "trumpet"),
    (("trombone", "tbn", "bone"), "trombone"),
    (("alto",), "alto_sax"), (("tenor",), "tenor_sax"), (("bari",), "baritone_sax"),
    (("sax", "reed", "clarinet", "flute"), "saxes"),
    (("bass",), "bass"),
    (("guitar", "gtr"), "guitar"),
    (("piano", "keys", "keyboard", "organ", "rhodes", "pno", "vibe"), "piano"),
]
PROGRAM_SECTIONS = {**{p: "piano" for p in range(0, 24)}, **{p: "guitar" for p in range(24, 32)},
                    **{p: "bass" for p in range(32, 40)}, 56: "trumpet", 57: "trombone", 58: "trombone",
                    59: "trumpet", 60: "trumpet", 61: "trumpet", 62: "trumpet", 63: "trumpet",
                    64: "alto_sax", 65: "alto_sax", 66: "tenor_sax", 67: "baritone_sax",
                    **{p: "saxes" for p in range(68, 80)}}


class SongImportError(ValueError):
    pass


# ---- bar grid and tempo map ---------------------------------------------------------------

def bar_grid(mf: smf.MidiFile, end_tick: int) -> list[dict]:
    """Bars from tick 0 to past end_tick: [{start, length, num, den}] in (Fraction) ticks.

    A time signature takes effect at its tick; one that falls inside a bar cuts that bar short.
    """
    sigs = mf.time_signatures()
    whole = 4 * mf.division
    bars, tick, si = [], Fraction(0), 0
    end = max(end_tick, 1)
    while tick < end or not bars:
        while si + 1 < len(sigs) and sigs[si + 1][0] <= tick:
            si += 1
        _, num, den = sigs[si]
        length = Fraction(whole * num, den)
        nxt = sigs[si + 1][0] if si + 1 < len(sigs) else None
        if nxt is not None and tick < nxt < tick + length:
            length = Fraction(nxt) - tick
        bars.append({"start": tick, "length": length, "num": num, "den": den})
        tick += length
    return bars


def has_pickup(mf: smf.MidiFile, bars: list[dict]) -> bool:
    if len(bars) < 2:
        return False
    a, b = bars[0], bars[1]
    sig_change_at_b = any(t == b["start"] for t, *_ in mf.time_signatures()[1:])
    return sig_change_at_b and a["length"] < b["length"]


def tempo_map(mf: smf.MidiFile, bars: list[dict], origin: Fraction, source: dict) -> chris_tm.TempoMap:
    """Chris's tempo-map/1 dict for the bars at/after `origin` (tick). Same rules as his:
    exact Fractions, one float conversion at the end, bar 1 downbeat = 0.0 s."""
    sec = mf.tick_seconds()
    whole = 4 * mf.division
    temps = mf.tempo_events()

    def sec_at(tick: Fraction) -> Fraction:
        # tick_seconds takes ints; bar starts can be fractional ticks, interpolate exactly
        i = int(tick)
        s0 = sec(i)
        return s0 if tick == i else s0 + (sec(i + 1) - s0) * (tick - i)

    def us_at(tick: Fraction) -> int:
        us = temps[0][1]
        for t, u in temps:
            if t <= tick:
                us = u
        return us

    t0 = sec_at(origin)
    rows = []
    kept = [b for b in bars if b["start"] >= origin]
    for n, b in enumerate(kept, start=1):
        bpm = Fraction(60_000_000, us_at(b["start"]))
        start, stop = sec_at(b["start"]) - t0, sec_at(b["start"] + b["length"]) - t0
        rows.append({
            "bar": n, "source_measure": bars.index(b), "start_s": float(start),
            "pos_whole": float((b["start"] - origin) / whole),
            "numerator": b["num"], "denominator": b["den"],
            "bpm": float(bpm), "beat_s": float(60 / bpm * Fraction(4, b["den"])),
            "bar_s": float(stop - start),
        })
    changes = [{"time_s": 0.0, "pos_whole": 0.0, "bpm": float(Fraction(60_000_000, us_at(origin)))}]
    for t, us in temps:
        if t > origin:
            changes.append({"time_s": float(sec_at(Fraction(t)) - t0),
                            "pos_whole": float((t - origin) / whole), "bpm": float(Fraction(60_000_000, us))})
    for c in changes:
        c.update(written_bpm=round(c["bpm"], 3), written_unit=4, linear=False)
    end = kept[-1]["start"] + kept[-1]["length"] if kept else origin
    return chris_tm.TempoMap({
        "format": chris_tm.FORMAT, "source": source, "time_origin": chris_tm.TIME_ORIGIN,
        "bars": rows, "tempo_changes": changes, "end_s": float(sec_at(end) - t0),
    })


# ---- tracks ----------------------------------------------------------------------------------

def split_format0(mf: smf.MidiFile) -> smf.MidiFile:
    """Format 0 -> format 1 with one track per channel (so the band can follow each part)."""
    conductor = smf.Track(0, [e for e in mf.tracks[0].events if e.channel is None and e.meta_type != 0x2F])
    by_ch: dict[int, list] = {}
    for e in mf.tracks[0].events:
        if e.channel is not None:
            by_ch.setdefault(e.channel, []).append(e)
    tracks = [conductor]
    for i, ch in enumerate(sorted(by_ch), start=1):
        evs = by_ch[ch]
        prog = next((e.data[1] for e in evs if e.kind == 0xC0), 0)
        name = "Drums" if ch == smf.DRUM_CHANNEL else f"{GM_FAMILIES[prog // 8]}_ch{ch + 1}"
        tracks.append(smf.Track(i, [smf.track_name(name)] + evs))
    return smf.MidiFile(1, mf.division, tracks)


def section_for(track: smf.Track) -> str | None:
    name = track.name.lower()
    for words, sec in NAME_SECTIONS:
        if any(w in name for w in words):
            return sec
    progs = [p for ch, p in track.programs().items() if ch != smf.DRUM_CHANNEL]
    return PROGRAM_SECTIONS.get(progs[0]) if progs else None


def is_kit_note(track: smf.Track, n: smf.Note, drum_tracks: set[str]) -> bool:
    if track.key in drum_tracks:
        return True
    return n.channel == smf.DRUM_CHANNEL and n.note in KIT


def backing_midi(mf: smf.MidiFile, drum_tracks: set[str]) -> smf.MidiFile:
    """The file with every drum-kit note removed (band percussion stays)."""
    tracks = []
    for tr in mf.tracks:
        if tr.key in drum_tracks:
            evs = [e for e in tr.events if e.kind not in (0x80, 0x90)]
        else:
            evs = [e for e in tr.events
                   if not (e.channel == smf.DRUM_CHANNEL and e.kind in (0x80, 0x90) and e.data[1] in KIT)]
        tracks.append(smf.Track(tr.index, evs))
    return smf.MidiFile(mf.format, mf.division, tracks)


# ---- import ----------------------------------------------------------------------------------

def import_midi(path, song_id: str | None = None, title: str | None = None, artist: str = "",
                songs_dir: Path | None = None, drum_tracks: tuple[str, ...] = (), backing: bool = True,
                pickup: bool | None = None, log=print) -> dict:
    """Import one MIDI file. Returns its song.json. Raises SongImportError on unusable input."""
    path = Path(path)
    raw = path.read_bytes()
    try:
        mf = smf.parse(raw)
    except (smf.MidiError, IndexError) as e:
        raise SongImportError(f"{path.name}: {e}") from None
    split = mf.format == 0
    if split:
        mf = split_format0(mf)
        raw_out = mf.to_bytes()
    else:
        raw_out = raw
    song_id = song_id or catalog.slug(path.stem)
    title = title or path.stem.replace("_", " ").strip()

    drum_set = set(drum_tracks)
    missing = drum_set - {t.key for t in mf.tracks}
    if missing:
        raise SongImportError(f"no track named {sorted(missing)} (tracks: {[t.key for t in mf.tracks]})")
    per_track = [(tr, tr.notes()) for tr in mf.tracks]
    last = max((n.end_tick for _, ns in per_track for n in ns), default=0)
    bars = bar_grid(mf, last)
    use_pickup = has_pickup(mf, bars) if pickup is None else (pickup and len(bars) > 1)
    origin = bars[1]["start"] if use_pickup else Fraction(0)
    sec = mf.tick_seconds()
    t0 = sec(int(origin)) if origin == int(origin) else None
    if t0 is None:
        raise SongImportError("pickup does not end on a whole tick")

    drum_names, hits, dropped = set(), set(), 0
    band_notes = 0
    for tr, notes in per_track:
        for n in notes:
            if is_kit_note(tr, n, drum_set):
                drum_names.add(tr.key)
                t = sec(n.start_tick) - t0
                if t < 0:
                    dropped += 1
                    continue
                hits.add((round(float(t), 6), n.note))
            else:
                band_notes += 1
    if not hits:
        raise SongImportError(f"{path.name}: no drum-kit notes on MIDI channel 10"
                           f" (tracks: {[t.key for t in mf.tracks]}; use --drum-track NAME)")

    source = {"midi": path.name, "track": ", ".join(sorted(drum_names))}
    tm = tempo_map(mf, bars, origin, source)
    band = {}
    for tr, notes in per_track:
        if tr.key in drum_names or not any(not is_kit_note(tr, n, drum_set) for n in notes):
            continue
        sec_name = section_for(tr)
        if sec_name and tr.key not in band:
            band[tr.key] = [sec_name]

    want_backing = backing and band_notes > 0
    why = render.available() if want_backing else None
    with catalog.Staging(song_id, songs_dir) as st:
        d = st.dir
        lines = [f"# source: {path.name}  track={source['track']}", "# time_s  midi  name"]
        lines += [f"{t:.6f}  {m}  {drum_name(m)}" for t, m in sorted(hits)]
        (d / SHEET).write_text("\n".join(lines) + "\n", encoding="utf-8")
        chris_tm.write_tempo_map(tm, d / TEMPO)
        (d / ARRANGEMENT).write_bytes(raw_out)
        catalog.write_json(d / BAND_TRACKS, band)
        rendered = False
        if want_backing and why is None:
            audio = render.render_midi(backing_midi(mf, drum_set).to_bytes(), start_s=float(t0))
            render.write_ogg(d / BACKING, render.normalise(audio), min_s=tm["end_s"])
            rendered = True
        elif want_backing:
            log(f"warning: no backing for {song_id}: {why}")
        render.write_ogg(d / CLICK, render.click_track(tm))
        first = tm["bars"][0]
        song = {
            "id": song_id, "title": title, "artist": artist,
            "bpm": round(first["bpm"], 3), "beats_per_bar": first["numerator"],
            "bars": len(tm["bars"]), "duration_s": round(tm["end_s"], 3),
            "source": "midi", "drum_midi_track": source["track"],
            "backing": rendered, "arrangement": True, "imported_at": _now(),
            "click": True, "notes": len(hits),
            "pickup_s": round(float(t0), 6) if use_pickup else 0.0,
            "midi_t0_s": round(float(t0), 6), "pickup_drum_notes_dropped": dropped,
            "format0_split": split, "original_file": path.name,
        }
        catalog.write_json(d / SONG_JSON, song)
        st.commit()
    log(f"imported {song_id}: {len(hits)} drum notes, {len(tm['bars'])} bars, {song['duration_s']} s,"
        f" backing={'yes' if rendered else 'no'}{', pickup %.3f s' % song['pickup_s'] if use_pickup else ''}")
    return song


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def import_inbox(songs_dir: Path | None = None, log=print) -> list[dict]:
    """Import every *.mid in songs/inbox/; done files go to inbox/imported/, bad ones to inbox/failed/."""
    box = catalog.inbox(songs_dir)
    box.mkdir(parents=True, exist_ok=True)
    out = []
    for f in sorted(box.iterdir()):
        if not f.is_file() or f.suffix.lower() not in (".mid", ".midi") or f.name.startswith("."):
            continue
        try:
            out.append(import_midi(f, songs_dir=songs_dir, log=log))
            dest = box / "imported"
        except Exception as e:  # noqa: BLE001 - one bad file must not stop the rest
            log(f"failed {f.name}: {e}")
            dest = box / "failed"
            dest.mkdir(exist_ok=True)
            (dest / (f.name + ".error.txt")).write_text(str(e) + "\n", encoding="utf-8")
        dest.mkdir(exist_ok=True)
        shutil.move(str(f), dest / f.name)
    return out
