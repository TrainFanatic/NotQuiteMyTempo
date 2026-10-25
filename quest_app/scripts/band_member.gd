class_name BandMember
extends Node3D
## One band player: the imported character, its instrument on the "Instrument" bone
## (trombone slide on "InstrumentSlide"), and its animations played through AnimBlender:
##   resting (rest) -> raising (raise) -> playing (play_loop) -> lowering (lower) -> resting
## plus reactions. Every switch is a crossfade; raise, lower and one-shot reactions fade on
## to the next state by themselves when they end, and freeze holds until it is released
## with set_band_state. Time is scaled by tempo: the animations are authored at 120 BPM, and
## each one follows its policy in TEMPO_POLICY (see scripts/tempo.gd). While a song is
## loaded, the director feeds the song's beat in with set_song_beat and play_loop stays
## phase-locked to it (one loop is one 4-beat bar).

signal state_changed(member: String, state: StringName)

const AnimBlender := preload("res://scripts/anim_blender.gd")
const Tempo := preload("res://scripts/tempo.gd")
const AUTHORED_BPM := Tempo.AUTHORED_BPM
## Crossfades in seconds at 120 BPM; Tempo.fade_seconds scales and clamps them.
const XFADE := 0.25           # a requested switch
const XFADE_FLOW := 0.2       # raise into play_loop, lower into rest
const XFADE_RETURN := 0.3     # a one-shot reaction back to rest
## Tempo policy per animation (Tempo.SYNCED, LOCKED or FREE). Animations not listed are
## SYNCED. Add an entry with Tempo.FREE to keep an action in real time.
const TEMPO_POLICY := {
	&"play_loop": Tempo.LOCKED,
}
## Poses with the instrument up.
const UP := [&"raise", &"play_loop", &"freeze"]
## One-shots that hold their last pose until told otherwise.
const HOLDS := [&"freeze"]
const STATES := {&"resting": &"rest", &"raising": &"raise", &"playing": &"play_loop", &"lowering": &"lower"}
## Reaction -> the states it may be triggered from.
const REACTIONS := {
	&"freeze": [&"play_loop"],
	&"startle": [&"rest"], &"look_down": [&"rest"], &"look_at_drummer": [&"rest"],
	&"flip_page": [&"rest"], &"arms_rest": [&"rest"], &"spit_valve": [&"rest"],
}

var member_name := ""
var instrument_name := ""
var section := ""
var bpm := AUTHORED_BPM
var character: Node3D
var skeleton: Skeleton3D
var player: AnimationPlayer
var blender: AnimBlender
var instrument: Node3D
var slide: Node3D
## The song's beat (quarter notes from its start) while a song is loaded.
var song_beat := 0.0
var _beat_locked := false
var _last_node: StringName = &""
var _return_to: StringName = &"rest"   # where the current reaction goes when it ends


func setup(member: String, instrument_glb: String) -> void:
	member_name = member
	instrument_name = instrument_glb
	name = member
	section = member.trim_prefix("band_").rsplit("_", true, 1)[0]
	var scene: PackedScene = load("res://assets/band/%s.glb" % member)
	if scene == null:
		push_error("BandMember: missing res://assets/band/%s.glb" % member)
		return
	character = scene.instantiate()
	add_child(character)
	skeleton = character.find_children("*", "Skeleton3D", true, false)[0]
	player = character.find_children("*", "AnimationPlayer", true, false)[0]
	if not instrument_glb.is_empty():
		_attach_instrument(instrument_glb)
	_setup_animation()


func _attach_instrument(glb: String) -> void:
	var scene: PackedScene = load("res://assets/instruments/%s.glb" % glb)
	if scene == null:
		push_error("BandMember %s: missing instrument %s" % [member_name, glb])
		return
	instrument = scene.instantiate()
	var att := BoneAttachment3D.new()
	att.name = "InstrumentAttachment"
	att.bone_name = "Instrument"
	skeleton.add_child(att)
	att.add_child(instrument)
	instrument.transform = StateMachineBuilder.ATTACH_OFFSET
	var slides := instrument.find_children("*_slide", "Node3D", true, false)
	if not slides.is_empty():
		if skeleton.find_bone("InstrumentSlide") < 0:
			push_error("BandMember %s: trombone without an InstrumentSlide bone" % member_name)
			return
		var slide_att := BoneAttachment3D.new()
		slide_att.name = "SlideAttachment"
		slide_att.bone_name = "InstrumentSlide"
		skeleton.add_child(slide_att)
		slide = slides[0]
		slide.reparent(slide_att, false)
		slide.transform = StateMachineBuilder.ATTACH_OFFSET


func _setup_animation() -> void:
	for n in STATES.values():
		if not player.has_animation(n):
			push_error("BandMember %s: missing animation %s" % [member_name, n])
			return
	if not Tempo.check_policy_table(TEMPO_POLICY, player, "BandMember %s" % member_name):
		return
	blender = AnimBlender.new(player)
	blender.play(&"rest", 0.0)
	blender.advance(0.0)


## state: resting / raising / playing / lowering. Raising flows into playing on its own,
## lowering into resting. Every switch crossfades, wherever the player is in a move.
func set_band_state(state: StringName) -> bool:
	if not STATES.has(state):
		push_error("BandMember %s: unknown band state %s" % [member_name, state])
		return false
	var cur := blender.current
	var up := cur in UP
	match state:
		&"raising", &"playing":
			if cur == &"freeze":
				_play(&"play_loop", XFADE)          # released from the freeze
			elif not up:
				_play(&"raise", XFADE)              # from rest, a reaction, or half lowered
		&"lowering", &"resting":
			if up:
				_play(&"lower", XFADE)
			elif cur != &"rest" and cur != &"lower":
				_play(&"rest", XFADE)               # out of a reaction
	return true


func react(reaction: StringName) -> bool:
	if not REACTIONS.has(reaction) or not player.has_animation(reaction):
		push_error("BandMember %s: no reaction %s" % [member_name, reaction])
		return false
	var current := blender.current
	if current == reaction:
		return true
	if not current in REACTIONS[reaction]:
		push_error("BandMember %s: %s isn't allowed from %s" % [member_name, reaction, current])
		return false
	_return_to = current
	_play(reaction, XFADE)
	return true


func current_state() -> StringName:
	return blender.current


func set_tempo(new_bpm: float) -> bool:
	if not Tempo.check_bpm(new_bpm, "BandMember %s" % member_name):
		return false
	bpm = new_bpm
	return true


static func tempo_policy(anim: StringName) -> StringName:
	return TEMPO_POLICY.get(anim, Tempo.SYNCED)


## The song is at `beat` (quarter notes, may be fractional). Between calls the beat runs on
## at the current tempo. Beat-locked loops follow it until clear_song_beat().
func set_song_beat(beat: float) -> void:
	song_beat = beat
	_beat_locked = true


func clear_song_beat() -> void:
	_beat_locked = false


func is_beat_locked() -> bool:
	return _beat_locked


func step(delta: float) -> void:
	if _beat_locked:
		song_beat += delta * bpm / 60.0
	blender.advance(_anim_delta(delta))
	if blender.finished() and not blender.current in HOLDS:
		match blender.current:
			&"raise":
				_play(&"play_loop", XFADE_FLOW)
			&"lower":
				_play(&"rest", XFADE_FLOW)
			_:
				_play(_return_to, XFADE_RETURN)     # a one-shot reaction is over
	var node := blender.current
	if node != _last_node:
		_last_node = node
		state_changed.emit(member_name, node)


func _speed(anim: StringName) -> float:
	return Tempo.rate(tempo_policy(anim), bpm)


## Animation time for this step: tempo-scaled, and for a beat-locked loop nudged toward the
## song's beat.
func _anim_delta(delta: float) -> float:
	var anim := blender.current
	if _beat_locked and tempo_policy(anim) == Tempo.LOCKED and player.is_playing():
		var length := blender.length()
		return Tempo.locked_delta(blender.position(), length, Tempo.locked_position(song_beat, length), delta, _speed(anim))
	return delta * _speed(anim)


## Fades are authored in seconds at 120 BPM (see Tempo.fade_seconds); the player runs on
## tempo-scaled time. A beat-locked loop joins the song at the song's beat.
func _play(anim: StringName, xfade: float) -> void:
	var policy := tempo_policy(anim)
	var at := 0.0
	if _beat_locked and policy == Tempo.LOCKED:
		at = Tempo.locked_position(song_beat, player.get_animation(anim).length)
	blender.play(anim, Tempo.fade_seconds(xfade, policy, bpm) * _speed(anim), at)


func _process(delta: float) -> void:
	step(delta)
