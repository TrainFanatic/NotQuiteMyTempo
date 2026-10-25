class_name SceneDirector
extends Node
## The one API the rest of the game talks to (drum input, voice, AI and scoring live with
## teammates and call in here). Everything is also reachable as a message dictionary via
## handle_message(), which is what the fake injector and any network/IPC bridge use:
##   {"type": "play_move", "id": "t1_fist_cutoff"}
##   {"type": "stop_move"}
##   {"type": "set_band_state", "target": "all" | "<section>" | "<member>", "state": "playing"}
##   {"type": "react", "target": ..., "reaction": "look_at_drummer"}
##   {"type": "set_tempo", "bpm": 300}
##   {"type": "load_song", "midi": "res://songs/x.mid", "tracks": {"Trumpets": ["trumpet"]}}
##   {"type": "set_song_position", "seconds": 12.5}
## Targets: "all", a section (alto_sax, tenor_sax, baritone_sax, trumpet, trombone, piano,
## guitar, bass), "saxes" (every sax), or a member name (band_trumpet_2).

signal message_handled(msg: Dictionary, ok: bool)

const Tempo := preload("res://scripts/tempo.gd")

var terence: Terence
var members: Dictionary = {}          # member name -> BandMember
var phrases: Dictionary = {}          # member name -> [phrase]
var song_position := 0.0
var bpm := 120.0
var follow_song := false              # when a song is loaded, band states follow its phrases
## While a song is loaded: its tempo map and PPQN. set_song_position turns song time into a
## beat for the beat-locked loops and follows the map's tempo changes.
var tempo_map: Array = []
var division := 0
var song_beat := 0.0


func setup(terence_node: Terence, band: Array) -> void:
	terence = terence_node
	for m in band:
		members[m.member_name] = m


func play_move(id: StringName) -> bool:
	return terence.play_move(id)


func stop_move() -> void:
	terence.stop()


func resolve(target: String) -> Array:
	if target == "all":
		return members.values()
	if members.has(target):
		return [members[target]]
	var out: Array = []
	for m in members.values():
		if m.section == target or (target == "saxes" and m.section.ends_with("sax")):
			out.append(m)
	if out.is_empty():
		push_error("SceneDirector: no band member or section matches '%s'" % target)
	return out


func set_band_state(target: String, state: StringName) -> bool:
	var group := resolve(target)
	if group.is_empty():
		return false
	var ok := true
	for m in group:
		ok = m.set_band_state(state) and ok
	return ok


func react(target: String, reaction: StringName) -> bool:
	var group := resolve(target)
	if group.is_empty():
		return false
	var ok := true
	for m in group:
		ok = m.react(reaction) and ok
	return ok


## Every animation scales by bpm / 120 (see scripts/tempo.gd). Tempos outside
## Tempo.MIN_BPM..Tempo.MAX_BPM are rejected. With a song loaded, the song's tempo map takes
## over again at the next set_song_position.
func set_tempo(new_bpm: float) -> bool:
	if not Tempo.check_bpm(new_bpm, "SceneDirector"):
		return false
	bpm = new_bpm
	var ok := terence.set_tempo(bpm)
	for m in members.values():
		ok = m.set_tempo(bpm) and ok
	return ok


## tracks: {MIDI track name: [targets]}, which players read which part.
func load_song(midi_path: String, tracks: Dictionary) -> bool:
	var smf := SMFParser.parse_file(midi_path)
	if smf.is_empty():
		return false
	var by_track := PhraseExtractor.extract(smf)
	if by_track.is_empty():
		return false
	phrases.clear()
	for track_name in tracks:
		if not by_track.has(track_name):
			push_error("SceneDirector: %s has no track named %s (has %s)" % [midi_path, track_name, by_track.keys()])
			return false
		for target in tracks[track_name]:
			for m in resolve(target):
				phrases[m.member_name] = by_track[track_name]
	var tm: Array = smf["tempo_map"]
	for e in tm:
		if not Tempo.check_bpm(60000000.0 / e["us_per_qn"], "SceneDirector: %s at tick %d" % [midi_path, e["tick"]]):
			return false
	tempo_map = tm
	division = smf["division"]
	if not set_tempo(SMFParser.bpm_at(0, tm)):
		return false
	follow_song = true
	song_position = 0.0
	_lock_beat(0.0)
	return true


## Song time in seconds. Moves the band through its phrases, follows the tempo map, and
## gives every character the song's beat for the beat-locked loops.
func set_song_position(seconds: float) -> void:
	song_position = seconds
	if not follow_song:
		return
	_lock_beat(seconds)
	for member_name in phrases:
		var m: BandMember = members[member_name]
		var want := PhraseExtractor.state_at(phrases[member_name], seconds)
		var have := m.current_state()
		var have_state: StringName = BandMember.STATES.find_key(have) if BandMember.STATES.values().has(have) else &""
		if want == &"resting" and have_state == &"playing":
			m.set_band_state(&"lowering")
		elif (want == &"raising" or want == &"playing") and have_state in [&"resting", &"lowering"]:
			m.set_band_state(&"playing")
		elif want == &"resting" and have_state == &"":
			pass   # mid-reaction: leave it alone, it returns to rest by itself


func _lock_beat(seconds: float) -> void:
	var ticks := SMFParser.seconds_to_ticks(seconds, tempo_map, division)
	song_beat = ticks / division
	var song_bpm := SMFParser.bpm_at(maxi(0, floori(ticks)), tempo_map)
	if song_bpm != bpm:
		set_tempo(song_bpm)
	terence.set_song_beat(song_beat)
	for m in members.values():
		m.set_song_beat(song_beat)


func handle_message(msg: Dictionary) -> bool:
	var ok := false
	match String(msg.get("type", "")):
		"play_move":
			ok = _need(msg, ["id"]) and play_move(StringName(msg["id"]))
		"stop_move":
			stop_move()
			ok = true
		"set_band_state":
			ok = _need(msg, ["target", "state"]) and set_band_state(msg["target"], StringName(msg["state"]))
		"react":
			ok = _need(msg, ["target", "reaction"]) and react(msg["target"], StringName(msg["reaction"]))
		"set_tempo":
			ok = _need(msg, ["bpm"]) and set_tempo(float(msg["bpm"]))
		"load_song":
			ok = _need(msg, ["midi", "tracks"]) and load_song(msg["midi"], msg["tracks"])
		"set_song_position":
			ok = _need(msg, ["seconds"])
			if ok:
				set_song_position(float(msg["seconds"]))
		_:
			push_error("SceneDirector: unknown message type in %s" % msg)
	message_handled.emit(msg, ok)
	return ok


func _need(msg: Dictionary, keys: Array) -> bool:
	for k in keys:
		if not msg.has(k):
			push_error("SceneDirector: message %s is missing '%s'" % [msg, k])
			return false
	return true
