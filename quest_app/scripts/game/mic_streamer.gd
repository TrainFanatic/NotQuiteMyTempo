class_name MicStreamer
extends Node
## Streams the headset microphone to the laptop as `mic.pcm` hub frames (CONTRACT.md), so
## Kevin's transcriber can hear the drums from the Quest (live_transcription/tools/
## transcribe_wav.py --input hub:ws://...). Opt-in: GameSession adds it only when the mic
## source is "quest" and frees it otherwise.
##
## Capture is Kevin's recorder's (live_transcription/quest_recorder/recorder.gd):
## AudioStreamMicrophone -> a muted bus with an AudioEffectCapture, so the mic is never heard.
## The bus is made here at runtime (QuestMicStream) if the layout has none, so the game's
## default_bus_layout.tres needs no change. Needs `audio/driver/enable_input=true` in
## project.godot and the RECORD_AUDIO permission (asked for here on Android).
##
## Frames: mono (the mean of the two channels, as prepare_recording.py does), 16-bit LE, about
## `frame_ms` each at the mix rate (44.1 kHz on Quest 2; the laptop resamples to 48 kHz).
## `first_sample` counts from the stream's start and skips ahead over frames the capture
## buffer discarded, so the laptop sees the gap; `seq` goes up by one per frame even when the
## hub is down, so the laptop sees a lost frame too.
##
## Clock: `local_t` is this device's clock (Time.get_ticks_usec, FletcherHub's) of the frame's
## first sample: epoch + first_sample / rate - input_latency_s, where epoch is the running
## minimum of (last mix time - samples captured so far / rate): a late _process only ever
## makes that later, so the minimum is the closest to the mix. The minimum may creep up 100 ppm
## to follow the audio clock's drift against the tick clock. `clock_offset` (FletcherHub's
## min(local - laptop t)) rides along so the laptop can cancel the network delay both ways.
## The mic's own input latency (hardware -> mix) is not known yet: measure and set
## input_latency_s.

const RECORD_AUDIO := "android.permission.RECORD_AUDIO"
const CREEP := 1e-4

## FletcherHub, or anything with send(type, data) -> bool and clock_offset.
var hub: Object
@export var frame_ms := 50.0
@export var input_latency_s := 0.0
@export var bus_name := &"QuestMicStream"

var sample_rate := 0
var sent_frames := 0       ## frames the hub accepted
var lost_frames := 0       ## frames dropped because the hub was down
var discarded_samples := 0 ## capture buffer overflows (the gap the laptop will see)

var _capture: AudioEffectCapture
var _mic: AudioStreamPlayer
var _made_bus := false
var _seq := 0
var _pending := PackedFloat32Array()
var _pending_first := 0     ## stream sample index of _pending[0]
var _captured := 0          ## samples taken from the capture so far, including discarded ones
var _discarded_seen := 0
var _epoch := INF
var _epoch_at := 0.0


func _ready() -> void:
	if OS.get_name() == "Android" and not OS.get_granted_permissions().has(RECORD_AUDIO):
		OS.request_permission(RECORD_AUDIO)
	var idx := AudioServer.get_bus_index(bus_name)
	if idx < 0:
		AudioServer.add_bus()
		idx = AudioServer.bus_count - 1
		AudioServer.set_bus_name(idx, bus_name)
		AudioServer.set_bus_send(idx, &"Master")
		AudioServer.set_bus_mute(idx, true)  # capture runs on a muted bus; nothing is heard
		var cap := AudioEffectCapture.new()
		cap.buffer_length = 1.0
		AudioServer.add_bus_effect(idx, cap)
		_made_bus = true
	for e in AudioServer.get_bus_effect_count(idx):
		if AudioServer.get_bus_effect(idx, e) is AudioEffectCapture:
			_capture = AudioServer.get_bus_effect(idx, e)
	if _capture == null:
		push_warning("[MicStreamer] no AudioEffectCapture on bus %s" % bus_name)
		return
	sample_rate = int(AudioServer.get_mix_rate())
	if not ProjectSettings.get_setting("audio/driver/enable_input", false):
		push_warning("[MicStreamer] audio/driver/enable_input is off in project.godot: no mic, nothing sent")
		return
	_mic = AudioStreamPlayer.new()
	_mic.stream = AudioStreamMicrophone.new()
	_mic.bus = bus_name
	add_child(_mic)
	_capture.clear_buffer()
	_discarded_seen = _capture.get_discarded_frames()


func _exit_tree() -> void:
	if _made_bus:
		var idx := AudioServer.get_bus_index(bus_name)
		if idx >= 0:
			AudioServer.remove_bus(idx)
		_made_bus = false


func _process(_delta: float) -> void:
	if _capture == null or _mic == null:
		return
	if OS.get_name() == "Android" and not OS.get_granted_permissions().has(RECORD_AUDIO):
		return
	if not _mic.playing:
		_mic.play()
	var now := _now()
	var mix_t := now - AudioServer.get_time_since_last_mix()
	while _capture.get_frames_available() > 0:
		var block := _capture.get_buffer(mini(_capture.get_frames_available(), 4096))
		if block.is_empty():
			break
		push_frames(block, mix_t)
	var discarded := _capture.get_discarded_frames()
	if discarded > _discarded_seen:
		note_discarded(discarded - _discarded_seen)
		_discarded_seen = discarded


## Adds captured stereo frames; mix_t = this device's time of the mix that produced the
## newest of them. Sends every complete frame. (Public so tests can feed it.)
func push_frames(block: PackedVector2Array, mix_t: float) -> void:
	if sample_rate <= 0:
		return
	var start := _pending.size()
	_pending.resize(start + block.size())
	for i in block.size():
		var f := block[i]
		_pending[start + i] = (f.x + f.y) * 0.5
	_captured += block.size()
	var candidate := mix_t - float(_captured) / sample_rate
	if _epoch == INF:
		_epoch = candidate
	else:
		_epoch = minf(_epoch + CREEP * maxf(0.0, mix_t - _epoch_at), candidate)
	_epoch_at = mix_t
	var n := frame_samples()
	while _pending.size() >= n:
		_send(_pending.slice(0, n))
		_pending = _pending.slice(n)


## The capture dropped `count` frames after what was read: send what is pending, then jump.
func note_discarded(count: int) -> void:
	if count <= 0:
		return
	if not _pending.is_empty():
		_send(_pending)
		_pending = PackedFloat32Array()
	_pending_first += count
	_captured += count
	discarded_samples += count


func frame_samples() -> int:
	return maxi(1, int(round(sample_rate * frame_ms / 1000.0)))


func _send(mono: PackedFloat32Array) -> void:
	var pcm := PackedByteArray()
	pcm.resize(mono.size() * 2)
	for i in mono.size():
		pcm.encode_s16(i * 2, int(round(clampf(mono[i], -1.0, 1.0) * 32767.0)))
	_seq += 1
	var data := {
		"seq": _seq,
		"rate": sample_rate,
		"channels": 1,
		"first_sample": _pending_first,
		"local_t": _epoch + float(_pending_first) / sample_rate - input_latency_s,
		"pcm_b64": Marshalls.raw_to_base64(pcm),
	}
	var offset = hub.get("clock_offset") if hub != null else null
	if typeof(offset) == TYPE_FLOAT and offset != INF:
		data["clock_offset"] = offset
	_pending_first += mono.size()
	if hub != null and hub.send("mic.pcm", data):
		sent_frames += 1
	else:
		lost_frames += 1


static func _now() -> float:
	return Time.get_ticks_usec() / 1e6
