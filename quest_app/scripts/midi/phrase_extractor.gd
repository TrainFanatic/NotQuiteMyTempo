class_name PhraseExtractor
extends RefCounted
## Turns a parsed MIDI file into play spans per track, for driving the band:
##   a phrase is a run of notes with no gap of `rest_beats` or more;
##   raise_at = phrase start - raise_lead (instrument up to the mouth in time);
##   lower_at = phrase end + lower_delay (instrument comes down after the rest starts).
## A track's phrases never overlap: when the next raise would come before this lower,
## the instrument just stays up (the phrases merge).
## Result: {track name: [{start, end, raise_at, lower_at, notes}]}, times in seconds.

const DEFAULT_REST_BEATS := 2.0
const DEFAULT_RAISE_LEAD := 0.6     # matches the 12-frame raise at 24 fps plus margin
const DEFAULT_LOWER_DELAY := 0.4


static func extract(smf: Dictionary, rest_beats: float = DEFAULT_REST_BEATS,
		raise_lead: float = DEFAULT_RAISE_LEAD, lower_delay: float = DEFAULT_LOWER_DELAY) -> Dictionary:
	if smf.is_empty() or not smf.has("tracks"):
		push_error("PhraseExtractor: empty or invalid parse result")
		return {}
	var rest_ticks := int(rest_beats * smf["division"])
	var out := {}
	for track in smf["tracks"]:
		var notes: Array = track["notes"]
		if notes.is_empty():
			continue
		var key: String = track["name"] if not String(track["name"]).is_empty() else "track_%d" % track["index"]
		if out.has(key):
			push_error("PhraseExtractor: two tracks named %s; rename one in the MIDI file" % key)
			return {}
		var phrases: Array = []
		var cur := {"start_tick": notes[0]["start_tick"], "end_tick": notes[0]["end_tick"], "notes": 1}
		for i in range(1, notes.size()):
			var n: Dictionary = notes[i]
			if n["start_tick"] - cur["end_tick"] >= rest_ticks:
				phrases.append(cur)
				cur = {"start_tick": n["start_tick"], "end_tick": n["end_tick"], "notes": 1}
			else:
				cur["end_tick"] = max(cur["end_tick"], n["end_tick"])
				cur["notes"] += 1
		phrases.append(cur)
		var timed: Array = []
		for p in phrases:
			var start := SMFParser.tick_to_seconds(p["start_tick"], smf["tempo_map"], smf["division"])
			var end := SMFParser.tick_to_seconds(p["end_tick"], smf["tempo_map"], smf["division"])
			var ph := {"start": start, "end": end, "raise_at": max(0.0, start - raise_lead),
				"lower_at": end + lower_delay, "notes": p["notes"]}
			if not timed.is_empty() and ph["raise_at"] <= timed[-1]["lower_at"]:
				timed[-1]["end"] = ph["end"]
				timed[-1]["lower_at"] = ph["lower_at"]
				timed[-1]["notes"] += ph["notes"]
			else:
				timed.append(ph)
		out[key] = timed
	return out


## The band state a player should be in at song time t, given their phrases.
static func state_at(phrases: Array, t: float) -> StringName:
	for p in phrases:
		if t < p["raise_at"]:
			return &"resting"
		if t < p["start"]:
			return &"raising"
		if t < p["lower_at"]:
			return &"playing"
	return &"resting"
