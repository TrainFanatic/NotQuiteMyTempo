extends Node
## Plays a character's sound cues in step with its animation. One per character (Terence or
## a band member), added as its child. The character must expose:
##   blender    AnimBlender with signals advanced(anim, from_pos, to_pos, wrapped) and
##              started(anim), plus `current` and `player`
##   skeleton   its Skeleton3D (bone anchors)
##   prop_anchor(bone) -> Node3D   only if a cue uses the PropR/PropL anchors
## "world:x,y,z" anchors are in the character's Blender axes (SfxLibrary.world_local) and
## placed with the character node's transform as it was when the move started.
## Cues follow the dominant animation only (blender.current), in authored time, so tempo
## scaling needs nothing here and a fading-out animation never fires. A cue at time t fires
## when t is in (from_pos, to_pos] of an advance, or (from_pos, length] + [0, to_pos] when
## the step wrapped a loop; frame 0 also fires on `started`. Each pass fires each cue once.
## Loaded with preload() by its users, so no global class cache entry is needed.

const SfxLibrary := preload("res://scripts/sfx/sfx_library.gd")
const SfxVoicePool := preload("res://scripts/sfx/sfx_voice_pool.gd")

## Seconds of slack when checking a cue against its animation's length (frame rounding).
const LENGTH_SLACK := 1e-3

var character: Node3D
var library: SfxLibrary
var pool: SfxVoicePool
## Seeded in tests for repeatable pitch jitter.
var rng := RandomNumberGenerator.new()
## action -> [{time, stream, gain_db, pitch_jitter, kind, bone, prop, local}] sorted by time
var _cues := {}
## The character's transform when the current move started (world: anchors).
var _move_xform: Transform3D
var _move_anim: StringName = &""
## Every cue fired, newest last: {anim, time, voice}. For tests and debugging.
var fired: Array[Dictionary] = []
const FIRED_KEEP := 64


## Binds to `char_node` and checks `table` ({action: [cue]}) against its animations and
## skeleton. Actions in `optional` may be missing from the character (band "default"
## entries that only some instruments have); any other unknown action is an error.
func setup(char_node: Node3D, table: Dictionary, lib: SfxLibrary, voice_pool: SfxVoicePool, optional: Array = []) -> bool:
	character = char_node
	library = lib
	pool = voice_pool
	name = "SfxCues"
	var who := str(char_node.name)
	var blender = char_node.get("blender")
	var skel = char_node.get("skeleton")
	if blender == null or skel == null:
		push_error("SfxCuePlayer %s: character has no blender or skeleton (set it up first)" % who)
		return false
	for sig in ["advanced", "started"]:
		if not blender.has_signal(sig):
			push_error("SfxCuePlayer %s: AnimBlender has no '%s' signal" % [who, sig])
			return false
	var anim_player: AnimationPlayer = blender.player
	var skeleton: Skeleton3D = skel
	for action in table:
		if not anim_player.has_animation(action):
			if action in optional:
				continue
			push_error("SfxCuePlayer %s: cue table names animation '%s', which it does not have" % [who, action])
			return false
		var length: float = anim_player.get_animation(action).length
		var list: Array[Dictionary] = []
		for cue in table[action]:
			var c := _compile(who, action, cue, length, skeleton)
			if c.is_empty():
				return false
			list.append(c)
		list.sort_custom(func(a, b): return a["time"] < b["time"])
		_cues[StringName(action)] = list
	_move_xform = char_node.global_transform
	_move_anim = blender.current
	blender.advanced.connect(_on_advanced)
	blender.started.connect(_on_started)
	return true


func _compile(who: String, action: String, cue: Dictionary, length: float, skeleton: Skeleton3D) -> Dictionary:
	var t := float(cue["frame"]) / library.fps
	if t > length + LENGTH_SLACK:
		push_error("SfxCuePlayer %s: %s cue at frame %d is past the animation's end (%.3f s)" % [who, action, int(cue["frame"]), length])
		return {}
	var c := {"time": t, "stream": library.streams[StringName(str(cue["sfx"]))],
		"gain_db": float(cue["gain_db"]), "pitch_jitter": float(cue["pitch_jitter"]),
		"kind": "", "bone": -1, "prop": "", "local": Vector3.ZERO, "sfx": str(cue["sfx"])}
	var anchor: String = cue["anchor"]
	if anchor in SfxLibrary.PROP_ANCHORS:
		if not character.has_method("prop_anchor"):
			push_error("SfxCuePlayer %s: %s cue anchors on %s, but the character has no prop_anchor()" % [who, action, anchor])
			return {}
		c["kind"] = "prop"
		c["prop"] = anchor
	elif anchor.begins_with(SfxLibrary.WORLD_PREFIX):
		c["kind"] = "world"
		c["local"] = SfxLibrary.world_local(anchor)
	else:
		var bone := skeleton.find_bone(anchor)
		if bone < 0:
			push_error("SfxCuePlayer %s: %s cue anchors on '%s', which is not a bone of its skeleton" % [who, action, anchor])
			return {}
		c["kind"] = "bone"
		c["bone"] = bone
	return c


func _on_started(anim: StringName) -> void:
	_capture(anim)
	for c in _cues.get(anim, []):
		if c["time"] == 0.0:
			_fire(anim, c)


func _on_advanced(anim: StringName, from_pos: float, to_pos: float, wrapped: bool) -> void:
	var blender = character.get("blender")
	if anim != blender.current:
		return   # a fading-out animation: only the dominant one carries cues
	if anim != _move_anim:
		_capture(anim)   # entered part way (no `started`)
	if not wrapped and to_pos < from_pos:
		push_error("SfxCuePlayer %s: %s went back from %.3f to %.3f s without wrapping" % [character.name, anim, from_pos, to_pos])
		return
	var list: Array = _cues.get(anim, [])
	if list.is_empty():
		return
	if wrapped:
		for c in list:
			if c["time"] > from_pos:
				_fire(anim, c)
		for c in list:
			if c["time"] <= to_pos:
				_fire(anim, c)
	else:
		for c in list:
			if c["time"] > from_pos and c["time"] <= to_pos:
				_fire(anim, c)


func _capture(anim: StringName) -> void:
	_move_anim = anim
	_move_xform = character.global_transform


func _fire(anim: StringName, c: Dictionary) -> void:
	var pos: Vector3
	var follow: Node3D = null
	match c["kind"]:
		"bone":
			var skel: Skeleton3D = character.get("skeleton")
			pos = skel.global_transform * skel.get_bone_global_pose(c["bone"]).origin
		"world":
			pos = _move_xform * c["local"]
		"prop":
			follow = character.call("prop_anchor", c["prop"])
			if follow == null:
				push_error("SfxCuePlayer %s: %s cue '%s' anchors on %s, but no prop is there" % [character.name, anim, c["sfx"], c["prop"]])
				return
			pos = follow.global_position
	var j: float = c["pitch_jitter"]
	var pitch := 1.0 + (rng.randf_range(-j, j) if j > 0.0 else 0.0)
	var voice := pool.play(c["stream"], pos, c["gain_db"], pitch, follow)
	fired.append({"anim": anim, "time": c["time"], "sfx": c["sfx"], "voice": voice, "pos": pos, "pitch": pitch})
	if fired.size() > FIRED_KEEP:
		fired.pop_front()


## Adds a cue player to every band member and to Terence, all sharing `voice_pool`.
## Any problem is a push_error and false.
static func attach_all(band: Array, terence: Node3D, lib: SfxLibrary, voice_pool: SfxVoicePool) -> bool:
	var names: Array = band.map(func(m): return m.member_name)
	if not lib.check_band_keys(names):
		return false
	for m in band:
		var sources := lib.band_sources(m.member_name)
		var optional := sources.keys().filter(func(a): return sources[a] == "default")
		var p = new()
		m.add_child(p)
		if not p.setup(m, lib.band_table(m.member_name), lib, voice_pool, optional):
			return false
	# default actions may be absent from some members (or shadowed by complete kind tables),
	# but one that no member has at all is a typo
	if lib.band_cues.has("default"):
		for a in lib.band_cues["default"]:
			if not band.any(func(m): return (m.blender.player as AnimationPlayer).has_animation(a)):
				push_error("SfxCuePlayer: band default cue action '%s' matches no band member's animation" % a)
				return false
	var tp = new()
	terence.add_child(tp)
	return tp.setup(terence, lib.terence_table(), lib, voice_pool)
