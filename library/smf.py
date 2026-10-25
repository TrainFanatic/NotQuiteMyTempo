"""Standard MIDI File read/write, no dependencies (formats 0 and 1, PPQN timing).

Mirrors Adam's quest_app/scripts/midi/smf_parser.gd where it matters to the headset: the track
name is the last 0x03 meta of the track, tempo events from every track are merged into one map,
note-on with velocity 0 is a note-off. Unlike his parser this one keeps every raw event so a
file can be written back (filtered) byte-for-byte equivalent.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from fractions import Fraction

DEFAULT_US_PER_QN = 500_000
DRUM_CHANNEL = 9  # MIDI channel 10, zero-based


class MidiError(ValueError):
    pass


@dataclass
class Event:
    tick: int          # absolute
    data: bytes        # status byte + payload, running status resolved (meta: FF type len data)

    @property
    def status(self) -> int:
        return self.data[0]

    @property
    def is_meta(self) -> bool:
        return self.data[0] == 0xFF

    @property
    def meta_type(self) -> int | None:
        return self.data[1] if self.is_meta else None

    @property
    def channel(self) -> int | None:
        return self.data[0] & 0x0F if 0x80 <= self.data[0] < 0xF0 else None

    @property
    def kind(self) -> int | None:
        return self.data[0] & 0xF0 if 0x80 <= self.data[0] < 0xF0 else None

    def meta_payload(self) -> bytes:
        _, n = _read_vlq(self.data, 2)
        return self.data[n:]


@dataclass
class Note:
    note: int
    channel: int
    velocity: int
    start_tick: int
    end_tick: int


@dataclass
class Track:
    index: int
    events: list[Event] = field(default_factory=list)

    @property
    def name(self) -> str:
        name = ""
        for e in self.events:
            if e.meta_type == 0x03:
                name = e.meta_payload().decode("utf-8", "replace")
        return name

    @property
    def key(self) -> str:
        """The name Adam's PhraseExtractor uses for this track."""
        return self.name if self.name else f"track_{self.index}"

    def notes(self) -> list[Note]:
        out, open_ = [], {}
        for e in self.events:
            k = e.kind
            if k not in (0x80, 0x90):
                continue
            ch, n, vel = e.channel, e.data[1], e.data[2]
            if k == 0x90 and vel > 0:
                if (ch, n) in open_:  # re-strike: close the old one here
                    s, v = open_.pop((ch, n))
                    out.append(Note(n, ch, v, s, e.tick))
                open_[(ch, n)] = (e.tick, vel)
            elif (ch, n) in open_:
                s, v = open_.pop((ch, n))
                out.append(Note(n, ch, v, s, e.tick))
        end = self.end_tick
        for (ch, n), (s, v) in open_.items():
            out.append(Note(n, ch, v, s, max(s, end)))
        out.sort(key=lambda x: (x.start_tick, x.note))
        return out

    @property
    def end_tick(self) -> int:
        return self.events[-1].tick if self.events else 0

    def programs(self) -> dict[int, int]:
        """channel -> first program change."""
        out = {}
        for e in self.events:
            if e.kind == 0xC0 and e.channel not in out:
                out[e.channel] = e.data[1]
        return out


@dataclass
class MidiFile:
    format: int
    division: int
    tracks: list[Track]

    def tempo_events(self) -> list[tuple[int, int]]:
        """[(tick, us_per_qn)], merged over tracks, sorted, last wins at a tick, starts at tick 0."""
        by_tick = {}
        for t in self.tracks:
            for e in t.events:
                if e.meta_type == 0x51:
                    p = e.meta_payload()
                    if len(p) != 3:
                        raise MidiError(f"bad tempo event length {len(p)}")
                    by_tick[e.tick] = (p[0] << 16) | (p[1] << 8) | p[2]
        if 0 not in by_tick:
            by_tick[0] = DEFAULT_US_PER_QN
        return sorted(by_tick.items())

    def time_signatures(self) -> list[tuple[int, int, int]]:
        """[(tick, numerator, denominator)], sorted, last wins at a tick, starts at tick 0 (4/4)."""
        by_tick = {}
        for t in self.tracks:
            for e in t.events:
                if e.meta_type == 0x58:
                    p = e.meta_payload()
                    if len(p) < 2:
                        raise MidiError("bad time signature event")
                    by_tick[e.tick] = (p[0], 1 << p[1])
        if 0 not in by_tick:
            by_tick[0] = (4, 4)
        return [(k, *v) for k, v in sorted(by_tick.items())]

    def tick_seconds(self):
        """Exact tick -> seconds (Fraction) function for this file's tempo map."""
        segs, sec = [], Fraction(0)
        evs = self.tempo_events()
        for i, (tick, us) in enumerate(evs):
            if i:
                pt, pus = evs[i - 1]
                sec += Fraction((tick - pt) * pus, 1_000_000 * self.division)
            segs.append((tick, sec, us))

        def f(tick: int) -> Fraction:
            j = 0
            lo, hi = 0, len(segs)
            while lo < hi:  # last seg with seg.tick <= tick
                mid = (lo + hi) // 2
                if segs[mid][0] <= tick:
                    j, lo = mid, mid + 1
                else:
                    hi = mid
            t0, s0, us = segs[j]
            return s0 + Fraction((tick - t0) * us, 1_000_000 * self.division)
        return f

    @property
    def end_tick(self) -> int:
        return max((t.end_tick for t in self.tracks), default=0)

    def to_bytes(self) -> bytes:
        out = bytearray(b"MThd" + struct.pack(">IHHH", 6, self.format, len(self.tracks), self.division))
        for t in self.tracks:
            body, last, status = bytearray(), 0, None
            evs = sorted(t.events, key=lambda e: e.tick)  # stable: keeps file order within a tick
            if not evs or evs[-1].meta_type != 0x2F:
                evs.append(Event(evs[-1].tick if evs else 0, b"\xff\x2f\x00"))
            for e in evs:
                body += _vlq(e.tick - last)
                last = e.tick
                body += e.data
            out += b"MTrk" + struct.pack(">I", len(body)) + body
        return bytes(out)


def _vlq(n: int) -> bytes:
    out = [n & 0x7F]
    n >>= 7
    while n:
        out.append((n & 0x7F) | 0x80)
        n >>= 7
    return bytes(reversed(out))


def _read_vlq(b: bytes, pos: int) -> tuple[int, int]:
    n = 0
    for _ in range(4):
        if pos >= len(b):
            raise MidiError("truncated variable-length number")
        c = b[pos]
        pos += 1
        n = (n << 7) | (c & 0x7F)
        if not c & 0x80:
            return n, pos
    raise MidiError("variable-length number longer than 4 bytes")


_DATA_LEN = {0x80: 2, 0x90: 2, 0xA0: 2, 0xB0: 2, 0xC0: 1, 0xD0: 1, 0xE0: 2}


def parse(b: bytes) -> MidiFile:
    if b[:4] != b"MThd":
        raise MidiError("not a MIDI file (no MThd)")
    hlen, fmt, ntracks, division = struct.unpack(">IHHH", b[4:14])
    if hlen < 6:
        raise MidiError(f"bad header length {hlen}")
    if fmt > 1:
        raise MidiError(f"format {fmt} is not supported (only 0 and 1)")
    if division & 0x8000:
        raise MidiError("SMPTE time division is not supported")
    pos = 8 + hlen
    tracks = []
    for i in range(ntracks):
        if b[pos:pos + 4] != b"MTrk":
            raise MidiError(f"expected MTrk for track {i}")
        (tlen,) = struct.unpack(">I", b[pos + 4:pos + 8])
        start, end = pos + 8, pos + 8 + tlen
        if end > len(b):
            raise MidiError(f"track {i} runs past the end of the file")
        tracks.append(_parse_track(b, start, end, i))
        pos = end
    return MidiFile(fmt, division, tracks)


def _parse_track(b: bytes, pos: int, end: int, index: int) -> Track:
    tr, tick, status = Track(index), 0, None
    while pos < end:
        delta, pos = _read_vlq(b, pos)
        tick += delta
        c = b[pos]
        if c == 0xFF:
            mlen, p2 = _read_vlq(b, pos + 2)
            data = b[pos:p2 + mlen]
            pos = p2 + mlen
            status = None
            tr.events.append(Event(tick, bytes(data)))
            if data[1] == 0x2F:
                break
            continue
        if c in (0xF0, 0xF7):
            slen, p2 = _read_vlq(b, pos + 1)
            tr.events.append(Event(tick, bytes(b[pos:p2 + slen])))
            pos = p2 + slen
            status = None
            continue
        if c & 0x80:
            status = c
            pos += 1
        elif status is None:
            raise MidiError(f"track {index} uses running status before any status byte")
        n = _DATA_LEN.get(status & 0xF0)
        if n is None:
            raise MidiError(f"unknown status byte 0x{status:02x} in track {index}")
        tr.events.append(Event(tick, bytes([status]) + bytes(b[pos:pos + n])))
        pos += n
    return tr


def read(path) -> MidiFile:
    with open(path, "rb") as f:
        return parse(f.read())


# ---- builders (tests, fixtures) -------------------------------------------------------------

def meta(tick: int, mtype: int, payload: bytes) -> Event:
    return Event(tick, bytes([0xFF, mtype]) + _vlq(len(payload)) + payload)


def tempo(tick: int, bpm: float) -> Event:
    us = round(60_000_000 / bpm)
    return meta(tick, 0x51, us.to_bytes(3, "big"))


def time_sig(tick: int, num: int, den: int) -> Event:
    return meta(tick, 0x58, bytes([num, den.bit_length() - 1, 24, 8]))


def track_name(name: str) -> Event:
    return meta(0, 0x03, name.encode())


def program(tick: int, ch: int, prog: int) -> Event:
    return Event(tick, bytes([0xC0 | ch, prog]))


def note_events(ch: int, note: int, start: int, end: int, vel: int = 100) -> list[Event]:
    return [Event(start, bytes([0x90 | ch, note, vel])), Event(end, bytes([0x80 | ch, note, 0]))]


def sort_events(events: list[Event]) -> list[Event]:
    """Meta first, then note-offs, then everything else, within a tick."""
    def rank(e):
        if e.is_meta:
            return 0
        if e.kind == 0x80 or (e.kind == 0x90 and e.data[2] == 0):
            return 1
        return 2
    return sorted(events, key=lambda e: (e.tick, rank(e)))
