class_name Terence
extends Node3D
## Terence: the imported character, played through AnimBlender so every switch between
## moves is a crossfade, including interrupting a move half way. HUB is his idle; one-shot
## moves fade back to it on their own when they end. Props ride the PropR/PropL bones for
## the moves that use them; thrown ones are left in the room where they land.
## Close-up moves need him in front of the player: he walks there around the band
## (WalkPlanner over the room), turns to face the player, then plays the move, and walks
## back to his spot once a one-shot move is over.
## Tempo: every animation is authored at 120 BPM and follows its policy in TEMPO_POLICY
## (see scripts/tempo.gd). Walking scales too: his travel speed follows the walk cycle's
## playback rate, so his feet never slide. While a song is loaded, the director feeds the
## song's beat in with set_song_beat and t0_conduct stays phase-locked to it.

signal move_started(id: StringName)
signal move_finished(id: StringName)

const AnimBlender := preload("res://scripts/anim_blender.gd")
const Tempo := preload("res://scripts/tempo.gd")
const WalkPlanner := preload("res://scripts/walk_planner.gd")

## Every move that brings a prop, and what happens to it. One entry per prop:
##   bone   the bone it rides (PropR / PropL)
##   glb    res://assets/props/<glb>.glb
##   thrown left in the room where it lands (the move's "lands"/"arrives" frame), removed
##          PROP_LIFETIME seconds later or when the same move starts again
##   hides  a room node hidden while the prop exists (the room's own stand would double it)
##   split  {child name pattern: bone}: parts of the glb that ride another bone
const PROPS := {
	&"t5_chair_throw": [{"bone": "PropR", "glb": "chair_folded", "thrown": true, "hides": "", "split": {}}],
	&"t5_drum_throw": [{"bone": "PropR", "glb": "spare_drum", "thrown": true, "hides": "", "split": {}}],
	&"t5_cymbal_throw": [{"bone": "PropR", "glb": "cymbal", "thrown": true, "hides": "", "split": {}}],
	&"t5_cowbell": [{"bone": "PropL", "glb": "cowbell", "thrown": false, "hides": "", "split": {"cowbell_stick*": "PropR"}}],
	&"t4_sweep_stand": [{"bone": "PropR", "glb": "music_stand_knockable", "thrown": true, "hides": "stand_terence", "split": {}}],
	&"t4_throw_music": [{"bone": "PropR", "glb": "sheet_music_pages", "thrown": true, "hides": "stand_terence", "split": {}}],
}
const MOVES_JSON := "res://assets/terence/terence_v004_moves.json"
const GLB := "res://assets/terence/terence_v004.glb"
const HUB := &"t0_stare_drummer"
## Looping walk cycle played while travelling. Optional: without it he glides in HUB.
const WALK := &"walk"
const AUTHORED_BPM := Tempo.AUTHORED_BPM
## Tempo policy per move (Tempo.SYNCED, LOCKED or FREE). Moves not listed, walk included,
## are SYNCED. Add an entry with Tempo.FREE to keep a move in real time.
const TEMPO_POLICY := {
	&"t0_conduct": Tempo.LOCKED,
}
## Authored with Terence NEAR_DIST in front of the player, facing them.
const CLOSE_MOVES := [&"t3_loom", &"t3_whisper", &"t3_crouch", &"t3_clap_in_face", &"t3_fake_warmth",
	&"t5_rhythm_slaps", &"t5_cowbell"]
const NEAR_DIST := 0.70
const CIRCLE_RADIUS := 1.30   # t3_circle_kit starts this far from the player's head
## Crossfades in seconds at 120 BPM; Tempo.fade_seconds scales and clamps them.
const XFADE := 0.25           # one move into another
const XFADE_RETURN := 0.3     # a finished one-shot back to HUB, and into or out of walking
## Travel speeds at 120 BPM; at other tempos they scale with the walk cycle.
const WALK_SPEED := 1.4       # m/s
const WALK_SPEED_AGGRESSIVE := 1.9   # on the way to a t4/t5 move
const AGGRESSIVE_PREFIXES := ["t4_", "t5_"]
const TURN_SPEED := 4.0       # rad/s at 120 BPM
const TURN_IN_PLACE := 0.6    # rad: with the heading off by more, turn on the spot before walking
const AT_SPOT := 0.02         # m: already standing where a move needs him
const PROP_LIFETIME := 8.0    # seconds a thrown prop stays in the room

static var _walk_warned := false

var moves: Array[StringName] = []
var meta: Dictionary = {}
var bpm := AUTHORED_BPM
var home: Transform3D
var player_head := Vector3.ZERO      # global position of the player's head (set by the director)
var character: Node3D
var skeleton: Skeleton3D
var player: AnimationPlayer
var blender: AnimBlender
var planner: WalkPlanner
## Ground speed of the walk cycle at its authored rate (moves.json walk speed_mps).
var walk_speed_mps := 0.0
## The song's beat (quarter notes from its start) while a song is loaded.
var song_beat := 0.0
var _beat_locked := false
var _attachments := {}
var _room_nodes := {}                # name -> room Node3D that PROPS may hide
var _props: Array[Dictionary] = []   # {node, move, spec, landed, age}
var _current: StringName = &""
var _plays := -1                     # blender.plays last seen: a restart counts as a new start
# travel
var _route := PackedVector3Array()
var _route_i := 0
var _travelling := false
var _travel_speed := WALK_SPEED
var _arrive_at: Transform3D
var _queued: StringName = &""


func setup(home_transform: Transform3D, head: Vector3, room: Node3D) -> void:
	name = "Terence"
	home = home_transform
	player_head = head
	global_transform = home
	if room == null:
		push_error("Terence: setup needs the room (for walking routes and room props)")
		return
	var away := home.origin - head
	away.y = 0.0
	if away.length() < 0.5:
		push_error("Terence: home and player overlap; close moves cannot be placed")
		return
	planner = WalkPlanner.from_room(room) as WalkPlanner
	if planner == null:
		return
	var text := FileAccess.get_file_as_string(MOVES_JSON)
	if text.is_empty():
		push_error("Terence: cannot read %s" % MOVES_JSON)
		return
	meta = JSON.parse_string(text)
	var scene: PackedScene = load(GLB)
	character = scene.instantiate()
	add_child(character)
	skeleton = character.find_children("*", "Skeleton3D", true, false)[0]
	player = character.find_children("*", "AnimationPlayer", true, false)[0]
	for id in meta["moves"]:
		if not player.has_animation(id):
			push_error("Terence: glb lacks move %s" % id)
			return
		moves.append(StringName(id))
		# moves.json is the authority on loops; tools/post_import_character.gd applies it on import
		var want := Animation.LOOP_LINEAR if meta["moves"][id]["loop"] else Animation.LOOP_NONE
		if player.get_animation(id).loop_mode != want:
			push_error("Terence: move %s has the wrong loop mode; is %s.import missing import_script/path?" % [id, GLB])
			return
	if not _setup_walk():
		return
	for bone in ["PropR", "PropL"]:
		var att := BoneAttachment3D.new()
		att.name = bone + "Attachment"
		att.bone_name = bone
		skeleton.add_child(att)
		_attachments[bone] = att
	if not _check_props(room):
		return
	if not Tempo.check_policy_table(TEMPO_POLICY, player, "Terence"):
		return
	blender = AnimBlender.new(player)
	blender.play(HUB, 0.0)
	blender.advance(0.0)


## The walk cycle must loop and have a moves.json entry with its ground speed (speed_mps),
## which ties travel speed to playback rate. Missing entirely, he travels in HUB and says so
## once.
func _setup_walk() -> bool:
	if not player.has_animation(WALK):
		if not _walk_warned:
			push_warning("Terence: %s has no '%s' animation, so he glides in %s while travelling to and from close-up moves. Add a looping walk action to the glb to fix it." % [GLB, WALK, HUB])
			_walk_warned = true
		return true
	if not meta["moves"].has(String(WALK)):
		push_error("Terence: %s has '%s' but %s has no entry for it (needs loop and speed_mps)" % [GLB, WALK, MOVES_JSON])
		return false
	var entry: Dictionary = meta["moves"][String(WALK)]
	if not entry["loop"]:
		push_error("Terence: %s marks '%s' as not looping" % [MOVES_JSON, WALK])
		return false
	if not entry.has("speed_mps") or float(entry["speed_mps"]) <= 0.0:
		push_error("Terence: %s '%s' needs a positive speed_mps (its ground speed)" % [MOVES_JSON, WALK])
		return false
	walk_speed_mps = float(entry["speed_mps"])
	return true


func _check_props(room: Node3D) -> bool:
	for id in PROPS:
		if not has_move(id):
			push_error("Terence: PROPS names unknown move %s" % id)
			return false
		for spec in PROPS[id]:
			for key in ["bone", "glb", "thrown", "hides", "split"]:
				if not spec.has(key):
					push_error("Terence: PROPS[%s] lacks '%s'" % [id, key])
					return false
			var path := "res://assets/props/%s.glb" % spec["glb"]
			if not ResourceLoader.exists(path):
				push_error("Terence: missing prop %s" % path)
				return false
			if spec["thrown"] and _land_time(id) < 0.0:
				push_error("Terence: thrown prop move %s has no 'lands' or 'arrives' frame in %s" % [id, MOVES_JSON])
				return false
			if spec["hides"] != "":
				var n := room.find_child(spec["hides"], true, false) as Node3D
				if n == null:
					push_error("Terence: room has no %s to hide during %s" % [spec["hides"], id])
					return false
				_room_nodes[spec["hides"]] = n
	return true


func has_move(id: StringName) -> bool:
	return id in moves


## Start a move (by its exact ID). Looping moves keep going until another move or stop().
## If the move needs him somewhere else, he walks there first and the move waits for him;
## a newer request replaces a waiting one.
func play_move(id: StringName) -> bool:
	if not has_move(id):
		push_error("Terence: unknown move %s" % id)
		return false
	var target := _target_for(id)
	if _at(target):
		_travelling = false
		_queued = &""
		global_transform = target
		_start(id, XFADE)
		return true
	return _travel_to(target, id, _speed_for(id))


## Back to the hub (idle stare) and home position, walking if he is away.
func stop() -> void:
	if _at(home):
		_travelling = false
		_queued = &""
		if blender.current != HUB:
			_start(HUB, XFADE_RETURN)
		return
	_travel_to(home, HUB, WALK_SPEED)


func current_move() -> StringName:
	return blender.current


## The move waiting for him to arrive, or empty.
func queued_move() -> StringName:
	return _queued


func is_travelling() -> bool:
	return _travelling


func set_tempo(new_bpm: float) -> bool:
	if not Tempo.check_bpm(new_bpm, "Terence"):
		return false
	bpm = new_bpm
	return true


static func tempo_policy(id: StringName) -> StringName:
	return TEMPO_POLICY.get(id, Tempo.SYNCED)


## The song is at `beat` (quarter notes, may be fractional). Between calls the beat runs on
## at the current tempo. Beat-locked moves follow it until clear_song_beat().
func set_song_beat(beat: float) -> void:
	song_beat = beat
	_beat_locked = true


func clear_song_beat() -> void:
	_beat_locked = false


func is_beat_locked() -> bool:
	return _beat_locked


## The node that sounds for whatever is on `bone` (PropR or PropL): the newest prop that
## came with that bone (still carried, or thrown and now flying or lying in the room), or
## the bone attachment itself when no such prop exists.
func prop_anchor(bone: String) -> Node3D:
	if not _attachments.has(bone):
		push_error("Terence: no prop bone %s (have %s)" % [bone, _attachments.keys()])
		return null
	for i in range(_props.size() - 1, -1, -1):
		var rec: Dictionary = _props[i]
		if rec["bone"] == bone and is_instance_valid(rec["node"]):
			return rec["node"]
	return _attachments[bone]


## Where `id` is played: in front of the player for close-up moves, otherwise home.
func _target_for(id: StringName) -> Transform3D:
	if id in CLOSE_MOVES:
		return _facing_player(NEAR_DIST)
	if id == &"t3_circle_kit":
		return _facing_player(CIRCLE_RADIUS)
	return home


func _facing_player(dist: float) -> Transform3D:
	var head_floor := Vector3(player_head.x, home.origin.y, player_head.z)
	var away := home.origin - head_floor
	away.y = 0.0
	var pos := head_floor + away.normalized() * dist
	# characters face +Z: point +Z from pos toward the player
	return Transform3D(Basis(Vector3.UP, _yaw_of(head_floor - pos)), pos)


func _at(target: Transform3D) -> bool:
	return global_position.distance_to(target.origin) < AT_SPOT \
		and abs(angle_difference(_yaw(), _yaw_of(target.basis.z))) < 0.05


func _speed_for(id: StringName) -> float:
	for pre in AGGRESSIVE_PREFIXES:
		if String(id).begins_with(pre):
			return WALK_SPEED_AGGRESSIVE
	return WALK_SPEED


func _walk_anim() -> StringName:
	return WALK if has_move(WALK) else HUB


## Walks home from where he stands now, which may be off the planner's floor (outside a
## doorway): a straight leg to `via`, which must be on the floor, then the planner's route
## around the band to home, turning to home's facing. HUB follows on arrival. Used by the
## entrance (scripts/intro.gd).
func walk_in(via: Vector3) -> bool:
	var floor_via := Vector3(via.x, home.origin.y, via.z)
	var rest := planner.plan(floor_via, home.origin)
	if rest.is_empty():
		return false
	var route := PackedVector3Array([global_position])
	route.append_array(rest)
	return _follow(route, home, HUB, WALK_SPEED)


func _travel_to(target: Transform3D, then: StringName, speed: float) -> bool:
	var route := planner.plan(global_position, target.origin)
	if route.is_empty():
		return false
	return _follow(route, target, then, speed)


func _follow(route: PackedVector3Array, target: Transform3D, then: StringName, speed: float) -> bool:
	_route = route
	_route_i = 1
	_arrive_at = target
	_queued = then
	_travel_speed = speed
	_travelling = true
	if blender.current != _walk_anim():
		_start(_walk_anim(), XFADE_RETURN)
	return true


## Moves him along the route at walking speed, facing where he goes, then turns him to the
## target's facing and starts the queued move.
func _travel(delta: float) -> void:
	var yaw := _yaw()
	var pos := global_position
	var budget := travel_speed() * delta
	var turn := TURN_SPEED * _walk_tempo_rate() * delta
	while _route_i < _route.size() and budget > 0.0:
		var to := _route[_route_i] - pos
		to.y = 0.0
		if to.length() < 1e-4:
			_route_i += 1
			continue
		var want := _yaw_of(to)
		var err := angle_difference(yaw, want)
		yaw = rotate_toward(yaw, want, turn)
		turn = 0.0
		if abs(err) > TURN_IN_PLACE:
			break
		var stride: float = min(budget, to.length())
		pos += to.normalized() * stride
		budget -= stride
		if stride >= to.length() - 1e-6:
			pos = Vector3(_route[_route_i].x, pos.y, _route[_route_i].z)
			_route_i += 1
	if _route_i >= _route.size():
		var final := _yaw_of(_arrive_at.basis.z)
		yaw = rotate_toward(yaw, final, turn)
		if abs(angle_difference(yaw, final)) < 0.01:
			global_transform = _arrive_at
			_arrive()
			return
	global_transform = Transform3D(Basis(Vector3.UP, yaw), pos)


func _arrive() -> void:
	_travelling = false
	var id := _queued
	_queued = &""
	if id != blender.current:
		_start(id, XFADE)


## Fades are authored in seconds at 120 BPM (see Tempo.fade_seconds); the player runs on
## each move's own rate. A beat-locked move joins the song at the song's beat.
func _start(id: StringName, xfade: float) -> void:
	var policy := tempo_policy(id)
	var at := 0.0
	if _beat_locked and policy == Tempo.LOCKED:
		at = Tempo.locked_position(song_beat, player.get_animation(id).length)
	blender.play(id, Tempo.fade_seconds(xfade, policy, bpm) * _anim_speed(id), at)


## Playback rate of `id`: its tempo policy, and for the walk cycle while travelling also the
## gait (the travel speed asked for over the cycle's authored ground speed).
func _anim_speed(id: StringName) -> float:
	var r := Tempo.rate(tempo_policy(id), bpm)
	if id == WALK and _travelling:
		r *= _travel_speed / walk_speed_mps
	return r


func _walk_tempo_rate() -> float:
	return Tempo.rate(tempo_policy(_walk_anim()), bpm)


## Metres per second he covers while travelling now: the walk cycle's ground speed times its
## playback rate, so his feet keep their grip at any tempo. (Gliding in HUB without a walk
## cycle, the tempo still scales it.)
func travel_speed() -> float:
	if has_move(WALK):
		return walk_speed_mps * _anim_speed(WALK)
	return _travel_speed * _walk_tempo_rate()


## Animation time for this step: the move's rate, and for a beat-locked loop nudged toward
## the song's beat.
func _anim_delta(delta: float) -> float:
	var id := blender.current
	if _beat_locked and tempo_policy(id) == Tempo.LOCKED and player.is_playing():
		var length := blender.length()
		return Tempo.locked_delta(blender.position(), length, Tempo.locked_position(song_beat, length), delta, _anim_speed(id))
	return delta * _anim_speed(id)


func _yaw() -> float:
	return _yaw_of(global_transform.basis.z)


static func _yaw_of(dir: Vector3) -> float:
	return atan2(dir.x, dir.z)


## Seconds into `id` where its thrown prop lands, or -1 when moves.json has no such frame.
func _land_time(id: StringName) -> float:
	var m: Dictionary = meta["moves"][String(id)]
	for key in ["lands", "arrives"]:
		if m.has(key):
			return float(m[key]) / float(meta["fps"])
	return -1.0


func _remove_prop(rec: Dictionary) -> void:
	var p: Node = rec["node"]
	if is_instance_valid(p):
		p.get_parent().remove_child(p)   # gone now, not at the end of the frame
		p.queue_free()


## A new animation took over: props still in his hands go, thrown props left by an earlier
## run of the same move go, then the new move's props appear.
func _on_anim_changed(id: StringName) -> void:
	var keep: Array[Dictionary] = []
	for rec in _props:
		if not rec["landed"] or rec["move"] == id:
			_remove_prop(rec)
		else:
			keep.append(rec)
	_props = keep
	if PROPS.has(id):
		for spec in PROPS[id]:
			_spawn_prop(id, spec)
	_update_room_nodes()


func _spawn_prop(id: StringName, spec: Dictionary) -> void:
	var scene: PackedScene = load("res://assets/props/%s.glb" % spec["glb"])
	var prop: Node3D = scene.instantiate()
	_attachments[spec["bone"]].add_child(prop)
	prop.transform = StateMachineBuilder.ATTACH_OFFSET
	_props.append({"node": prop, "move": id, "spec": spec, "landed": false, "age": 0.0, "bone": spec["bone"]})
	for pattern in spec["split"]:
		var parts := prop.find_children(pattern, "Node3D", true, false)
		if parts.is_empty():
			push_error("Terence: %s glb has no %s" % [spec["glb"], pattern])
			continue
		var part: Node3D = parts[0]
		var bone: String = spec["split"][pattern]
		part.reparent(_attachments[bone], false)
		part.transform = StateMachineBuilder.ATTACH_OFFSET
		_props.append({"node": part, "move": id, "spec": spec, "landed": false, "age": 0.0, "bone": bone})


## Thrown props leave his hand at the landing frame and stay in the room (world space);
## landed ones age out after PROP_LIFETIME.
func _update_props(delta: float) -> void:
	var keep: Array[Dictionary] = []
	var changed := false
	for rec in _props:
		if rec["landed"]:
			rec["age"] += delta
			if rec["age"] >= PROP_LIFETIME:
				_remove_prop(rec)
				changed = true
				continue
		elif rec["spec"]["thrown"] and rec["move"] == blender.current \
				and (blender.finished() or blender.position() >= _land_time(rec["move"])):
			_drop(rec)
		keep.append(rec)
	_props = keep
	if changed:
		_update_room_nodes()


func _drop(rec: Dictionary) -> void:
	var p: Node3D = rec["node"]
	# the attachment re-syncs at the end of the frame; take the pose the skeleton has now
	var bone_xf := skeleton.get_bone_global_pose(skeleton.find_bone(rec["bone"]))
	var xf := skeleton.global_transform * bone_xf * p.transform
	var world := get_parent()
	p.reparent(world, false)
	p.global_transform = xf
	rec["landed"] = true
	rec["age"] = 0.0


func _update_room_nodes() -> void:
	for n in _room_nodes:
		var hidden := _props.any(func(rec): return rec["spec"]["hides"] == n)
		(_room_nodes[n] as Node3D).visible = not hidden


func _sync_current() -> void:
	if blender.plays == _plays:
		return
	_plays = blender.plays
	if _current != &"":
		move_finished.emit(_current)
	_current = blender.current
	_on_anim_changed(_current)
	move_started.emit(_current)


func step(delta: float) -> void:
	if _beat_locked:
		song_beat += delta * bpm / 60.0
	_sync_current()
	if _travelling:
		_travel(delta)
		_sync_current()
	blender.advance(_anim_delta(delta))
	_update_props(delta)
	if blender.finished() and not _travelling:
		# a one-shot is over: back to the hub, walking home first if it was played away
		if _at(home):
			_start(HUB, XFADE_RETURN)
		else:
			_travel_to(home, HUB, WALK_SPEED)
	_sync_current()


func _process(delta: float) -> void:
	step(delta)
