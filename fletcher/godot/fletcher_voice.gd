class_name FletcherVoice
extends Node3D
## Fletcher's voice, played from his head: bundled barks (fletcher.speech with
## clip_id) and streamed lines (fletcher.speech stream=true, then fletcher.audio
## chunks of 16-bit little-endian mono PCM, base64). Godot port of
## fletcher/unity/FletcherVoice.cs.
##
## Body cues: fletcher.state messages carrying {gesture, utterance_id} and one
## anchor are held until the anchor is reached, then emitted as cue_due (and
## gesture_cue, the older 4-argument form). They may arrive before their
## utterance; that's normal. Anchors (see the shared cue spec):
##   at_index  chunk N of a streamed line starts PLAYING (lines only)
##   at_s      seconds after the utterance's first sound (lines: audio played;
##             barks: the bark player's position). May be NEGATIVE for a bark: the
##             move must start before the word, so the clip is held back by -min(at_s)
##             (at most bark_lead_max_s) and those moves start right away
##   after_s   seconds after the utterance finished playing (barks: clip end)
## Cues with `t0` (the count-off) are not anchors here: FletcherLink schedules them and
## calls play_countoff() for the words.
## A cue whose utterance never starts is dropped after cue_expiry_s.
##
## Setup: put this node on Terence's head bone, copy fletcher/barks/*.wav into
## barks_dir, and call handle_message(type, data) for every hub message (or let
## FletcherLink do it).

signal subtitle(text: String)
signal speaking(utterance_id: String)  ## first sound of a bark or line
signal finished(utterance_id: String)  ## a streamed line or a bark has played out (or was cut off)
signal gesture_cue(gesture: String, action: String, look_at: String, utterance_id: String)
## The same moment as gesture_cue, with everything FletcherLink's queue needs:
## {gesture, action, look_at, utterance_id, priority, anchor, due_t}. due_t is
## when the anchor was reached (this device's seconds, Time.get_ticks_usec()/1e6);
## it can be a little before now when the anchor passed between two frames.
signal cue_due(cue: Dictionary)
signal countoff_word(clip_id: String)  ## a count-off word clip starts (its onset is onset_s later)

@export var barks_dir := "res://fletcher_barks"
@export var sample_rate := 24000  ## must match fletcher.speech sample_rate (ElevenLabs pcm_24000)
@export var prebuffer_s := 0.15   ## collected before a line starts, to ride out Wi-Fi jitter
@export var buffer_s := 0.5       ## generator ring size
## AudioStreamPlayer3D attenuation scale: 0 dB at this distance. Terence's conducting spot is
## 4.96 m from the seated eye, where 2.0 put his voice 7.9 dB down and under the band
## (quest_app/AUDIO.md, levels). Closer than this is capped at +3 dB (max_db).
@export var unit_size := 5.0
@export var cue_expiry_s := 30.0  ## a cue whose utterance hasn't started by then is dropped
@export var bark_lead_max_s := 0.3  ## longest a bark is held back for a move that starts before its word (= Fletcher's bark_lead_max_s)
@export var cutoff_lead_max_s := 0.5  ## the same for the cutoff fist (t1_fist_cutoff, priority 2) on "Stop!" (= Fletcher's cutoff_lead_max_s)
@export var extra_latency_s := 0.0  ## speaker latency Godot doesn't report (it says 0 on the Quest); same value as BackingPlayer.extra_latency_s after calibration
@export var countoff_dir := "res://fletcher_countoff"  ## fletcher/countoff/*.wav + manifest.json (onset_s per clip)

## Ducking: while he speaks, the bus the scene's sound effects use (Adam's "SFX") is turned
## down so impacts and crashes don't mask his words; with no speech it is left alone. Follows
## the peak meter of the bus his own players are on ("Voice" in quest_app), like a sidechain
## compressor with a cap. Inert when either bus is missing or both are the same bus.
@export var duck_bus := &"SFX"
@export var duck_threshold_db := -42.0  ## voice peak above this ducks...
@export var duck_ratio := 5.0           ## ...by (peak - threshold) * (1 - 1/ratio) dB...
@export var duck_max_db := 18.0         ## ...at most this much
@export var duck_attack_s := 0.01
@export var duck_release_s := 0.45

var level := 0.0  ## loudness of what is playing now, 0..1-ish; drive the jaw with it
var duck_db := 0.0  ## the current duck (<= 0 dB), on top of duck_bus's own volume

var _stream_player: AudioStreamPlayer3D
var _bark_player: AudioStreamPlayer3D
var _playback: AudioStreamGeneratorPlayback
var _capacity := 0
var _barks := {}

var _uid := ""
var _pending := PackedVector2Array()  # received, not yet handed to the generator
var _received := 0                    # frames received for _uid
var _pushed := 0                      # frames handed to the generator
var _chunk_start := {}                # chunk index -> first frame of that chunk
var _cues := []                       # [{uid, anchor, value, gesture, action, look_at, priority, born}]
var _started := false
var _final := false
## utterance id -> {start: first sound (s), end: finished (s) or -1}. Kept a while
## after the end so after_s cues can still fire.
var _utt := {}
var _bark_uid := ""
var _bark_end := INF                  # when the bark's last sample is heard (clip length after its start)
var _bark_play_at := INF              # a held-back bark starts at this time (lead for its moves)
var _co_players := []                 # AudioStreamPlayer3D per count-off word
var _co_sched := []                   # [{at, clip, stream}] count-off words not yet started
var _duck_base := NAN                 # duck_bus's own volume, restored on exit
var _co_onsets := {}                  # clip_id -> onset_s (count-off manifest)


func _ready() -> void:
	_stream_player = _player("FletcherStream")
	_bark_player = _player("FletcherBark")
	_bark_player.finished.connect(_on_bark_finished)
	for i in 2:  # alternate, so one word can be cut cleanly when the next starts
		_co_players.append(_player("FletcherCount%d" % i))
	var gen := AudioStreamGenerator.new()
	gen.mix_rate = sample_rate
	gen.buffer_length = buffer_s
	_stream_player.stream = gen
	_stream_player.play()
	_playback = _stream_player.get_stream_playback()
	_capacity = _playback.get_frames_available()


func _player(n: String) -> AudioStreamPlayer3D:
	var p := AudioStreamPlayer3D.new()
	p.name = n
	p.unit_size = unit_size
	add_child(p)
	return p


func handle_message(type: String, data: Dictionary) -> void:
	match type:
		"fletcher.speech":
			_on_speech(data)
		"fletcher.audio":
			_on_audio(data)
		"fletcher.state":
			if data.has("gesture") and data.has("utterance_id") and not data.has("t0"):
				_add_cue(data)


func _add_cue(d: Dictionary) -> void:
	var anchor := "at_index"
	var value := 0.0
	for k in ["at_index", "at_s", "after_s"]:
		if d.has(k) and typeof(d[k]) in [TYPE_INT, TYPE_FLOAT]:
			anchor = k
			value = float(d[k]) if k == "at_s" else maxf(0.0, float(d[k]))
			break
	_cues.append({"uid": str(d["utterance_id"]), "anchor": anchor, "value": value,
				  "gesture": str(d["gesture"]), "action": str(d.get("action", "")),
				  "look_at": str(d.get("look_at", "drummer")),
				  "priority": int(d.get("priority", 1)) if typeof(d.get("priority")) in [TYPE_INT, TYPE_FLOAT] else 1,
				  "born": _now()})


## True while a cue for `uid` (any utterance if empty) is still waiting for its anchor.
func has_pending_cues(uid: String = "") -> bool:
	return _cues.any(func(c): return uid == "" or c["uid"] == uid)


## True while a cue naming `gesture` is still waiting for its anchor.
func has_pending_gesture(gesture: String) -> bool:
	return _cues.any(func(c): return c["gesture"] == gesture)


## True while a line or a bark is sounding.
func is_speaking() -> bool:
	return _started or _bark_uid != ""


func _on_speech(d: Dictionary) -> void:
	var text := str(d.get("text", ""))
	if text != "":
		subtitle.emit(text)
	var clip := str(d.get("clip_id", ""))
	if clip != "":
		var uid := str(d.get("utterance_id", clip))
		if _bark_uid != "":
			# cut off by the new bark; replacing the stream emits no finished. Its cues not
			# yet due are moot (as for an interrupted line): ending it would fire them all now
			var old := _bark_uid
			_cues = _cues.filter(func(c): return c["uid"] != old)
			_end(old)
		var s := _bark(clip)
		if s:
			var lead := bark_lead(uid)
			_bark_player.stream = s
			_bark_uid = uid
			# heard from output latency after play(); timed on the wall clock, which is
			# smoother than get_playback_position() (that one moves a mix buffer at a time)
			var heard := _now() + lead + out_latency()
			_bark_end = heard + s.get_length()
			_utt[uid] = {"start": heard, "end": -1.0}
			if lead > 0.0:
				_bark_play_at = _now() + lead  # its early moves fire now, from _fire_cues
			else:
				_bark_player.play()
				speaking.emit(uid)
		else:
			# no sound, but the body still moves: a zero-length utterance
			push_warning("[FletcherVoice] missing bark %s in %s" % [clip, barks_dir])
			_utt[uid] = {"start": _now(), "end": -1.0}
			_end(uid)
	elif d.get("stream", false):
		_reset(str(d.get("utterance_id", "")))
		if int(d.get("sample_rate", sample_rate)) != sample_rate:
			push_warning("[FletcherVoice] line is %s Hz, generator is %d" % [d.get("sample_rate"), sample_rate])


## How long a bark is held back: its earliest negative at_s cue, capped at bark_lead_max_s
## (cutoff_lead_max_s for the cutoff fist, priority 2).
func bark_lead(uid: String) -> float:
	var lead := 0.0
	for c in _cues:
		if c["uid"] == uid and c["anchor"] == "at_s":
			var cap := cutoff_lead_max_s if c["gesture"] == "t1_fist_cutoff" and int(c["priority"]) >= 2 else bark_lead_max_s
			lead = maxf(lead, minf(-float(c["value"]), cap))
	return lead


func _on_bark_finished() -> void:
	if _bark_uid != "":
		_end(_bark_uid)


func _end(uid: String) -> void:
	var t := _now()
	if uid == _bark_uid:
		_bark_uid = ""
		t = minf(t, _bark_end)
		_bark_end = INF
		_bark_play_at = INF  # a held-back bark that never started stays silent
	if _utt.has(uid) and _utt[uid]["end"] < 0.0:
		_utt[uid]["end"] = t
	finished.emit(uid)


func _reset(uid: String) -> void:
	if _started and _uid != "":
		# a new line interrupts the old one: its remaining cues are moot
		var old := _uid
		_cues = _cues.filter(func(c): return c["uid"] != old)
		_started = false
		_end(old)
	_uid = uid
	# clear_buffer() refuses while the generator plays, so restart it to drop the old line
	_stream_player.stop()
	_stream_player.play()
	_playback = _stream_player.get_stream_playback()
	_capacity = _playback.get_frames_available()
	_pending.clear()
	_received = 0
	_pushed = 0
	_chunk_start.clear()
	_started = false
	_final = false


func _on_audio(d: Dictionary) -> void:
	if str(d.get("utterance_id", "")) != _uid:
		return
	var idx := int(d.get("index", 0))
	if not _chunk_start.has(idx):
		_chunk_start[idx] = _received
	var b64 := str(d.get("pcm_b64", ""))
	if b64 != "":
		var pcm := Marshalls.base64_to_raw(b64)
		var n := pcm.size() / 2
		var start := _pending.size()
		_pending.resize(start + n)
		for i in n:
			var v := pcm.decode_s16(2 * i) / 32768.0
			_pending[start + i] = Vector2(v, v)
		_received += n
	if d.get("final", false):
		_final = true


## Frames of the current line that have reached the speaker so far.
func played_frames() -> int:
	if not _started:
		return 0
	var queued := _capacity - _playback.get_frames_available()
	var out_latency := int(out_latency() * sample_rate)
	return maxi(0, _pushed - queued - out_latency)


func _process(delta: float) -> void:
	_duck(delta)
	if _now() >= _bark_play_at:
		_bark_play_at = INF
		_bark_player.play()
		speaking.emit(_bark_uid)
	_play_countoff_words()
	if _bark_uid != "" and _now() >= _bark_end:
		_end(_bark_uid)  # don't wait for the player's finished, which lags a mix buffer
	if _uid != "" and not _started and _received > 0 \
			and (_received >= int(prebuffer_s * sample_rate) or _final):
		_started = true
		_utt[_uid] = {"start": _now(), "end": -1.0}
		speaking.emit(_uid)
	if _started and _pending.size() > 0:
		var n := mini(_playback.get_frames_available(), _pending.size())
		if n > 0:
			_playback.push_buffer(_pending.slice(0, n))
			_pending = _pending.slice(n)
			_pushed += n
	_fire_cues()
	_update_level()
	if _started and _final and _pending.is_empty() and played_frames() >= _pushed - int(out_latency() * sample_rate):
		var done := _uid
		_started = false
		_uid = ""
		_end(done)


## Seconds of `uid` heard so far: INF once it is over, -INF before it exists, negative
## while a held-back bark has not sounded yet.
func _elapsed(uid: String) -> float:
	if not _utt.has(uid):
		return -INF
	if _utt[uid]["end"] >= 0.0:
		return INF
	if uid == _uid and _started:
		return played_frames() / float(sample_rate)
	return _now() - _utt[uid]["start"]  # a bark (negative during its lead)


## When the cue's anchor is reached (this device's seconds), or -1 if not yet.
func _due_time(c: Dictionary) -> float:
	var uid: String = c["uid"]
	match c["anchor"]:
		"at_index":
			if uid == _uid and _started and _chunk_start.has(int(c["value"])) \
					and played_frames() >= _chunk_start[int(c["value"])]:
				return _now()
		"at_s":
			var e := _elapsed(uid)
			if e >= c["value"]:
				return minf(_now(), _utt[uid]["start"] + c["value"]) if e == INF else _now()
		"after_s":
			if _utt.has(uid) and _utt[uid]["end"] >= 0.0 and _now() >= _utt[uid]["end"] + c["value"]:
				return _utt[uid]["end"] + c["value"]
	return -1.0


func _fire_cues() -> void:
	if _cues.is_empty():
		return
	var now := _now()
	var keep := []
	var due := []
	for c in _cues:
		var t := _due_time(c)
		if t >= 0.0:
			due.append([t, c])
		elif not _utt.has(c["uid"]) and now - c["born"] > cue_expiry_s:
			pass  # its utterance never came
		elif c["anchor"] == "at_index" and _utt.has(c["uid"]) and _utt[c["uid"]]["end"] >= 0.0:
			pass  # the line is over and that chunk never played
		else:
			keep.append(c)
	_cues = keep
	due.sort_custom(func(a, b): return a[0] < b[0])
	for pair in due:
		var c: Dictionary = pair[1]
		gesture_cue.emit(c["gesture"], c["action"], c["look_at"], c["uid"])
		cue_due.emit({"gesture": c["gesture"], "action": c["action"], "look_at": c["look_at"],
					  "utterance_id": c["uid"], "priority": c["priority"], "anchor": c["anchor"],
					  "due_t": pair[0]})
	# forget utterances that ended long ago and have nothing left to fire
	for uid in _utt.keys():
		if _utt[uid]["end"] >= 0.0 and now - _utt[uid]["end"] > cue_expiry_s and not has_pending_cues(uid):
			_utt.erase(uid)


func _duck(delta: float) -> void:
	var di := AudioServer.get_bus_index(duck_bus)
	var vi := AudioServer.get_bus_index(_bark_player.bus)
	if di < 0 or vi < 0 or di == vi:
		return
	if is_nan(_duck_base):
		_duck_base = AudioServer.get_bus_volume_db(di)
	var peak := maxf(AudioServer.get_bus_peak_volume_left_db(vi, 0), AudioServer.get_bus_peak_volume_right_db(vi, 0))
	var target := -clampf((peak - duck_threshold_db) * (1.0 - 1.0 / duck_ratio), 0.0, duck_max_db)
	var tau := duck_attack_s if target < duck_db else duck_release_s
	duck_db += (target - duck_db) * (1.0 - exp(-delta / maxf(tau, 0.001)))
	AudioServer.set_bus_volume_db(di, _duck_base + duck_db)


func _exit_tree() -> void:
	var di := AudioServer.get_bus_index(duck_bus)
	if di >= 0 and not is_nan(_duck_base):
		AudioServer.set_bus_volume_db(di, _duck_base)


func _update_level() -> void:
	var target := 0.0
	if _started and _pending.size() > 0:
		var s := 0.0
		var m := mini(256, _pending.size())
		for i in m:
			s += _pending[i].x * _pending[i].x
		target = sqrt(s / m)
	elif _bark_player.playing:
		target = 0.3  # barks are loud; AudioStreamWAV doesn't expose samples cheaply
	level = lerpf(level, target, 0.4)


## ---- count-off --------------------------------------------------------

## Schedule the count-off words by their ONSETS (manifest onset_s) on beats -n..-1 before
## t0 (this device's seconds): "five six seven eight" on -4..-1.
## A word that starts while the previous one rings cuts it (fast tempos).
func play_countoff(t0: float, bpm: float, clips: Array) -> void:
	if _co_onsets.is_empty():
		var m = JSON.parse_string(FileAccess.get_file_as_string(countoff_dir.path_join("manifest.json")))
		if typeof(m) == TYPE_DICTIONARY:
			for k in m:
				_co_onsets[k] = float(m[k].get("onset_s", 0.0))
	if clips.is_empty():
		clips = ["co_five_sharp", "co_six_sharp", "co_seven_sharp", "co_eight_sharp"]
	var beat := 60.0 / maxf(bpm, 1.0)
	_co_sched.clear()
	for k in clips.size():
		var id := str(clips[k])
		var s := _clip(countoff_dir, id)
		if s == null:
			push_warning("[FletcherVoice] missing count-off clip %s in %s" % [id, countoff_dir])
			continue
		var word := t0 - (clips.size() - k) * beat
		_co_sched.append({"at": word - float(_co_onsets.get(id, 0.0)) - out_latency(),
						  "clip": id, "stream": s})


func _play_countoff_words() -> void:
	var now := _now()
	while not _co_sched.is_empty() and now >= _co_sched[0]["at"]:
		var w: Dictionary = _co_sched.pop_front()
		var p: AudioStreamPlayer3D = _co_players[0]
		_co_players.push_back(_co_players.pop_front())
		_co_players[0].stop()  # the previous word: cut when the next one starts
		p.stream = w["stream"]
		p.play(minf(now - w["at"], 0.05))  # started a frame late: skip the few ms we missed
		countoff_word.emit(w["clip"])


static func _now() -> float:
	return Time.get_ticks_usec() / 1e6


func _bark(clip: String) -> AudioStream:
	return _clip(barks_dir, clip)


func _clip(dir: String, clip: String) -> AudioStream:
	var path := "%s/%s.wav" % [dir, clip]
	if _barks.has(path):
		return _barks[path]
	var s: AudioStream = null
	if ResourceLoader.exists(path):
		s = load(path)  # imported (exported builds)
	elif FileAccess.file_exists(path):
		s = AudioStreamWAV.load_from_file(path)  # raw wav on disk (dev, user://)
	_barks[path] = s
	return s


## Time from handing audio to Godot until it is heard. Barks, count-off and backing all use
## the same number, so they stay in step with each other and with t0.
func out_latency() -> float:
	return AudioServer.get_output_latency() + extra_latency_s
