class_name SMFParser
extends RefCounted
## Standard MIDI File parser (formats 0 and 1, PPQN timing).
## Handles running status, note-on with velocity 0 as note-off, tempo changes (merged into
## one tempo map across tracks), track names, sysex and unknown meta events (skipped).
## Errors are reported with push_error and an empty result; nothing is guessed.
##
## Result: {format, division, tracks: [{name, notes: [{note, channel, velocity, start_tick,
##          end_tick, start, end}], events: [...]}], tempo_map: [{tick, us_per_qn}], length}
## Times (start/end/length) are in seconds.

const DEFAULT_US_PER_QN := 500000   # the SMF spec's tempo when no tempo event is given (120 BPM)


static func parse_file(path: String) -> Dictionary:
	var bytes := FileAccess.get_file_as_bytes(path)
	if bytes.is_empty():
		push_error("SMFParser: cannot read %s" % path)
		return {}
	return parse(bytes)


static func parse(bytes: PackedByteArray) -> Dictionary:
	var r := _Reader.new(bytes)
	if r.tag() != "MThd":
		push_error("SMFParser: not a MIDI file (no MThd)")
		return {}
	var hlen := r.u32()
	if hlen < 6:
		push_error("SMFParser: bad header length %d" % hlen)
		return {}
	var format := r.u16()
	var ntracks := r.u16()
	var division := r.u16()
	r.pos += hlen - 6
	if format > 1:
		push_error("SMFParser: format %d is not supported (only 0 and 1)" % format)
		return {}
	if division & 0x8000:
		push_error("SMFParser: SMPTE time division is not supported")
		return {}
	if format == 0 and ntracks != 1:
		push_error("SMFParser: format 0 must have exactly one track, has %d" % ntracks)
		return {}
	var tracks: Array = []
	var tempo_events: Array = []
	for t in ntracks:
		if r.tag() != "MTrk":
			push_error("SMFParser: expected MTrk for track %d" % t)
			return {}
		var tlen := r.u32()
		var end := r.pos + tlen
		if end > bytes.size():
			push_error("SMFParser: track %d runs past the end of the file" % t)
			return {}
		var track := _parse_track(r, end, t, tempo_events)
		if track.is_empty():
			return {}
		tracks.append(track)
		r.pos = end
	var tempo_map := _tempo_map(tempo_events)
	var length := 0.0
	for track in tracks:
		for n in track["notes"]:
			n["start"] = tick_to_seconds(n["start_tick"], tempo_map, division)
			n["end"] = tick_to_seconds(n["end_tick"], tempo_map, division)
			length = max(length, n["end"])
	return {"format": format, "division": division, "tracks": tracks, "tempo_map": tempo_map, "length": length}


static func _parse_track(r: _Reader, end: int, index: int, tempo_events: Array) -> Dictionary:
	var tick := 0
	var status := 0
	var name := ""
	var open := {}          # channel*128+note -> [start_tick, velocity]
	var notes: Array = []
	var events: Array = []
	while r.pos < end:
		tick += r.vlq()
		var b := r.peek()
		if b & 0x80:
			status = r.u8()
		elif status == 0:
			push_error("SMFParser: track %d uses running status before any status byte" % index)
			return {}
		if status == 0xFF:
			var mtype := r.u8()
			var mlen := r.vlq()
			var data := r.read(mlen)
			status = 0      # running status is cancelled by meta events
			match mtype:
				0x03:
					name = data.get_string_from_utf8()
				0x51:
					if mlen != 3:
						push_error("SMFParser: bad tempo event length %d" % mlen)
						return {}
					tempo_events.append({"tick": tick, "us_per_qn": (data[0] << 16) | (data[1] << 8) | data[2]})
				0x2F:
					break
			events.append({"tick": tick, "type": "meta", "meta": mtype})
			continue
		if status == 0xF0 or status == 0xF7:
			r.read(r.vlq())
			status = 0
			continue
		var kind := status & 0xF0
		var ch := status & 0x0F
		match kind:
			0x80, 0x90:
				var note := r.u8()
				var vel := r.u8()
				var key := ch * 128 + note
				if kind == 0x90 and vel > 0:
					if open.has(key):      # retrigger without note-off: close the old one
						notes.append(_note(note, ch, open[key], tick))
					open[key] = [tick, vel]
				elif open.has(key):
					notes.append(_note(note, ch, open[key], tick))
					open.erase(key)
				events.append({"tick": tick, "type": "note_on" if kind == 0x90 and vel > 0 else "note_off",
					"channel": ch, "note": note, "velocity": vel})
			0xA0, 0xB0, 0xE0:
				r.read(2)
			0xC0, 0xD0:
				r.read(1)
			_:
				push_error("SMFParser: unknown status byte 0x%02x in track %d" % [status, index])
				return {}
	for key in open:      # notes still held at the end of the track end there
		notes.append(_note(key % 128, key / 128, open[key], tick))
	notes.sort_custom(func(a, b): return a["start_tick"] < b["start_tick"])
	return {"name": name, "index": index, "notes": notes, "events": events, "end_tick": tick}


static func _note(note: int, ch: int, start: Array, end_tick: int) -> Dictionary:
	return {"note": note, "channel": ch, "velocity": start[1], "start_tick": start[0], "end_tick": end_tick}


static func _tempo_map(events: Array) -> Array:
	events.sort_custom(func(a, b): return a["tick"] < b["tick"])
	var tm: Array = []
	if events.is_empty() or events[0]["tick"] > 0:
		tm.append({"tick": 0, "us_per_qn": DEFAULT_US_PER_QN})
	for e in events:
		if not tm.is_empty() and tm[-1]["tick"] == e["tick"]:
			tm[-1] = e
		else:
			tm.append(e)
	return tm


static func tick_to_seconds(tick: int, tempo_map: Array, division: int) -> float:
	var seconds := 0.0
	for i in tempo_map.size():
		var seg_start: int = tempo_map[i]["tick"]
		if tick <= seg_start:
			break
		var seg_end: int = tempo_map[i + 1]["tick"] if i + 1 < tempo_map.size() else tick
		var span: int = min(tick, seg_end) - seg_start
		seconds += float(span) * tempo_map[i]["us_per_qn"] / 1000000.0 / division
	return seconds


## The inverse of tick_to_seconds: the (fractional) tick at `seconds` into the song. Past
## the last tempo change the last tempo holds; before 0 the first tempo runs backwards.
static func seconds_to_ticks(seconds: float, tempo_map: Array, division: int) -> float:
	var elapsed := 0.0
	for i in tempo_map.size():
		var sec_per_tick: float = tempo_map[i]["us_per_qn"] / 1000000.0 / division
		var seg_start: int = tempo_map[i]["tick"]
		if i + 1 < tempo_map.size():
			var seg_len: float = (tempo_map[i + 1]["tick"] - seg_start) * sec_per_tick
			if seconds < elapsed + seg_len:
				return seg_start + (seconds - elapsed) / sec_per_tick
			elapsed += seg_len
		else:
			return seg_start + (seconds - elapsed) / sec_per_tick
	push_error("SMFParser: empty tempo map")
	return 0.0


## Song beat (quarter notes, fractional) at `seconds`.
static func seconds_to_beats(seconds: float, tempo_map: Array, division: int) -> float:
	return seconds_to_ticks(seconds, tempo_map, division) / division


static func bpm_at(tick: int, tempo_map: Array) -> float:
	var us := DEFAULT_US_PER_QN
	for e in tempo_map:
		if e["tick"] <= tick:
			us = e["us_per_qn"]
	return 60000000.0 / us


class _Reader:
	var b: PackedByteArray
	var pos := 0

	func _init(bytes: PackedByteArray) -> void:
		b = bytes

	func peek() -> int:
		return b[pos]

	func u8() -> int:
		pos += 1
		return b[pos - 1]

	func u16() -> int:
		return (u8() << 8) | u8()

	func u32() -> int:
		return (u16() << 16) | u16()

	func tag() -> String:
		if pos + 4 > b.size():
			return ""
		return read(4).get_string_from_ascii()

	func read(n: int) -> PackedByteArray:
		var out := b.slice(pos, pos + n)
		pos += n
		return out

	func vlq() -> int:
		var v := 0
		for i in 4:
			var c := u8()
			v = (v << 7) | (c & 0x7F)
			if c & 0x80 == 0:
				return v
		push_error("SMFParser: variable-length quantity longer than 4 bytes")
		return v
