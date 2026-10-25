extends Node3D
## A fixed set of AudioStreamPlayer3D voices on the SFX bus, shared by every character, so a
## full band can never start more than `size` sounds at once on the Quest. When all voices
## are busy the oldest one is stolen. Busy time is tracked here (stream length / pitch), not
## read back from the audio thread, so stealing is deterministic.
## A voice can follow a node (a prop in the hand or in flight) until its sound ends.
## Loaded with preload() by its users, so no global class cache entry is needed.

const BUS := &"SFX"
const DEFAULT_VOICES := 24
## Distance model for a 10 x 7.6 m rehearsal room, listener in the drummer's seat:
## inverse distance, full level within UNIT_SIZE metres, silent past MAX_DISTANCE.
const UNIT_SIZE := 2.5
const MAX_DISTANCE := 20.0
## A gentle high cut that grows with distance (Godot's built-in per-voice filter, cheap).
const FILTER_CUTOFF_HZ := 9000.0
const FILTER_DB := -8.0

var voices: Array[AudioStreamPlayer3D] = []
## Parallel to voices: seconds of sound left, start order, node to follow (or null).
var _remaining: Array[float] = []
var _started: Array[int] = []
var _follow: Array = []
var _starts := 0


func setup(voice_count: int = DEFAULT_VOICES) -> bool:
	name = "SfxVoices"
	if voice_count <= 0:
		push_error("SfxVoicePool: voice count must be positive, got %d" % voice_count)
		return false
	if AudioServer.get_bus_index(BUS) < 0:
		push_error("SfxVoicePool: no '%s' audio bus (res://default_bus_layout.tres)" % BUS)
		return false
	for i in voice_count:
		var v := AudioStreamPlayer3D.new()
		v.name = "Voice%d" % i
		v.bus = BUS
		v.attenuation_model = AudioStreamPlayer3D.ATTENUATION_INVERSE_DISTANCE
		v.unit_size = UNIT_SIZE
		v.max_distance = MAX_DISTANCE
		v.attenuation_filter_cutoff_hz = FILTER_CUTOFF_HZ
		v.attenuation_filter_db = FILTER_DB
		v.doppler_tracking = AudioStreamPlayer3D.DOPPLER_TRACKING_DISABLED
		add_child(v)
		voices.append(v)
		_remaining.append(0.0)
		_started.append(0)
		_follow.append(null)
	return true


## Starts `stream` at global `pos`. With `follow`, the voice tracks that node until the
## sound ends. Returns the voice used.
func play(stream: AudioStream, pos: Vector3, gain_db: float, pitch: float, follow: Node3D = null) -> AudioStreamPlayer3D:
	if voices.is_empty():
		push_error("SfxVoicePool: play() before setup()")
		return null
	if pitch <= 0.0:
		push_error("SfxVoicePool: pitch must be positive, got %s" % pitch)
		return null
	var i := _free_voice()
	var v := voices[i]
	v.stop()
	v.stream = stream
	v.volume_db = gain_db
	v.pitch_scale = pitch
	v.global_position = pos
	_starts += 1
	_started[i] = _starts
	_remaining[i] = stream.get_length() / pitch
	_follow[i] = follow
	v.play()
	return v


## An idle voice, or the oldest busy one.
func _free_voice() -> int:
	var oldest := 0
	for i in voices.size():
		if _remaining[i] <= 0.0:
			return i
		if _started[i] < _started[oldest]:
			oldest = i
	return oldest


func active_count() -> int:
	var n := 0
	for r in _remaining:
		if r > 0.0:
			n += 1
	return n


## Ages the voices and moves the ones that follow a node.
func step(delta: float) -> void:
	for i in voices.size():
		if _remaining[i] <= 0.0:
			continue
		_remaining[i] -= delta
		if _remaining[i] <= 0.0:
			_follow[i] = null
			continue
		var f = _follow[i]
		if f != null:
			if is_instance_valid(f) and (f as Node3D).is_inside_tree():
				voices[i].global_position = (f as Node3D).global_position
			else:
				_follow[i] = null   # the prop was removed; the sound stays where it last was


func _process(delta: float) -> void:
	step(delta)
