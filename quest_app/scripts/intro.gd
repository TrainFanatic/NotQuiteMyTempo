extends Node
## Terence's entrance at the start of the game (Rehearsal.intro_enabled). The room is quiet,
## the band sits at rest, then:
##   1. Terence waits OUTSIDE_DIST behind the main door (the room's door_main), facing in.
##   2. After PRE_BEATS the door leaf swings into the room (OPEN_BEATS). Half way open he
##      walks in with his normal walk, straight through the doorway to THRESHOLD_DIST
##      inside, then around the band (WalkPlanner) to spot_terence, turning to its facing.
##      That is the facing every move is authored from, with the gaze on the drummer.
##   3. As he crosses the doorway the band startles, a ripple outward from the door
##      (REACT_BEATS_PER_M later per metre).
##   4. Once he is clear of the leaf's swing the door shuts behind him (CLOSE_BEATS) and a
##      quieter door_slam sounds at the latch, through the shared SFX voice pool.
##   5. At his spot the band drops its eyes to the music (look_down). He waits ARRIVE_BEATS,
##      checks his watch (t0_check_watch), then stares at the drummer (t0_stare_drummer,
##      his idle) for STARE_BEATS.
##   6. `finished`, once. Rehearsal then starts the showcase or the demo.
## Every wait here is counted in beats at Terence's tempo, and every animation and the walk
## are tempo-scaled by their owners, so the whole entrance follows set_tempo.
## door_main is one mesh in the room glb (frame, leaf and handle). The leaf and handle are
## split off at runtime into a hinged node, found by material: the leaf surface carries the
## door's own material (door_main, as build_room.py names it) and the handle is gilt. While
## the door is open, a black quad behind the leaf stands in for the dark corridor, since the
## room's wall is solid there.
## Driven by step(delta) from _process, so tests can step it by hand.
## Loaded with preload() by its users, so no global class cache entry is needed.

signal finished

const DOOR := "door_main"
const HANDLE_MATERIAL := "gilt"
const OUTSIDE_DIST := 0.8       # m behind the door where he waits
const THRESHOLD_DIST := 0.75    # m inside, where the straight leg ends and the route begins
const OPEN_ANGLE := PI * 95.0 / 180.0
const DARK_OFFSET := 0.01       # m the dark doorway quad sits in front of the wall (the closed leaf hides it)
## Beats at Terence's tempo (0.5 s each at 120 BPM).
const PRE_BEATS := 3.0          # silence before the door moves
const OPEN_BEATS := 1.5
const WALK_AT_OPEN := 0.5       # fraction of the opening when he starts walking
const CLOSE_BEATS := 2.0
const REACT_BEATS_PER_M := 0.15
const REACT_WAIT_BEATS := 8.0   # a member not free to react by then is an error
const ARRIVE_BEATS := 1.0
const STARE_BEATS := 4.0
## Beyond the leaf's swing radius, the door waits for this much more room (body radius + air).
const CLOSE_CLEARANCE := 0.4
const ENTRY_REACTION := &"startle"
const SETTLE_REACTION := &"look_down"
const WATCH_MOVE := &"t0_check_watch"
const DOOR_SOUND := &"door_slam"
const DOOR_SOUND_DB := -10.0    # t4_storm_out slams it at 0 dB; this is a door pulled to
const DOOR_SOUND_HEIGHT := 1.0  # m, the latch

enum DoorState { CLOSED, OPENING, OPEN, CLOSING, SHUT }
enum Phase { IDLE, OUTSIDE, WALKING, ARRIVED, WATCH, STARE, DONE, FAILED }

var terence: Terence
var band: Array = []
var pool: Node3D                 # SfxVoicePool, or null with sound effects off
var door_stream: AudioStream
var door: MeshInstance3D
var hinge: Node3D                # the leaf's pivot (child of door), turned about its Y
var leaf: MeshInstance3D
var dark: MeshInstance3D
var door_state := DoorState.CLOSED
var phase := Phase.IDLE
## Beats since start().
var beat := 0.0
## Every reaction fired: {member, reaction, beat}. For tests and debugging.
var reactions: Array[Dictionary] = []
## The door sound's voice once it has played.
var door_voice: AudioStreamPlayer3D
var _open_sign := 1.0            # sign of the hinge rotation that swings the leaf inward
var _free_dir := 1.0             # +1 when the leaf's free edge is at +X from the hinge, else -1
var _leaf_width := 0.0
var _progress := 0.0             # of the current door motion, 0..1
var _phase_beat := 0.0
var _entered := false
var _pending: Array[Dictionary] = []   # {member, reaction, at}
var _outside: Transform3D
var _threshold: Vector3
var _inward: Vector3


## Binds to the scene. `voice_pool` and `library` are both null with sound effects off;
## with them, the library must have DOOR_SOUND. Any problem is a push_error and false.
func setup(terence_node: Terence, members: Array, room: Node3D, voice_pool: Node3D, library: RefCounted) -> bool:
	name = "Intro"
	terence = terence_node
	band = members
	if terence == null or terence.blender == null or terence.planner == null:
		push_error("Intro: Terence is not set up")
		return false
	if room == null:
		push_error("Intro: no room")
		return false
	for id in [WATCH_MOVE, Terence.HUB, Terence.WALK]:
		if not terence.has_move(id):
			push_error("Intro: Terence has no move %s (%s)" % [id, Terence.MOVES_JSON])
			return false
	if band.is_empty():
		push_error("Intro: no band to react")
		return false
	for m in band:
		for r in [ENTRY_REACTION, SETTLE_REACTION]:
			if not BandMember.REACTIONS.has(r) or not m.player.has_animation(r):
				push_error("Intro: band member %s has no reaction %s" % [m.member_name, r])
				return false
	if (voice_pool == null) != (library == null):
		push_error("Intro: the voice pool and the sound library come together (both or neither)")
		return false
	if voice_pool != null:
		if not library.streams.has(DOOR_SOUND):
			push_error("Intro: the sound library has no %s" % DOOR_SOUND)
			return false
		pool = voice_pool
		door_stream = library.streams[DOOR_SOUND]
	door = room.find_child(DOOR, true, false) as MeshInstance3D
	if door == null:
		push_error("Intro: room has no %s mesh to enter through" % DOOR)
		return false
	if not _split_door(room):
		return false
	return _place_route()


## Splits the leaf and handle surfaces off door_main into hinge/leaf, leaving the frame.
func _split_door(room: Node3D) -> bool:
	var mesh := door.mesh as ArrayMesh
	if mesh == null:
		push_error("Intro: %s has no ArrayMesh" % DOOR)
		return false
	var leaf_s := -1
	var handle_s := -1
	for s in mesh.get_surface_count():
		var mat := door.get_active_material(s)
		var mat_name := mat.resource_name if mat != null else ""
		if mat_name == DOOR:
			leaf_s = s
		elif mat_name == HANDLE_MATERIAL:
			handle_s = s
	if leaf_s < 0 or handle_s < 0:
		push_error("Intro: %s needs a leaf surface (material %s) and a handle surface (material %s) to swing; it has %d surfaces" % [DOOR, DOOR, HANDLE_MATERIAL, mesh.get_surface_count()])
		return false
	if mesh.get_surface_count() < 3:
		push_error("Intro: %s has no frame surfaces besides its leaf and handle" % DOOR)
		return false
	var leaf_box := _surface_aabb(mesh, leaf_s)
	var handle_box := _surface_aabb(mesh, handle_s)
	_leaf_width = leaf_box.size.x
	if _leaf_width < 0.3 or leaf_box.size.y < 1.5:
		push_error("Intro: %s leaf is %s, not a door leaf in the door's XY plane" % [DOOR, leaf_box.size])
		return false
	# the door's local +Z must point into the room (build_room.py: local -Y into the room)
	var shell := room.find_child("room_shell", true, false) as MeshInstance3D
	if shell == null:
		push_error("Intro: room has no room_shell to tell inside from outside")
		return false
	var centre := (shell.global_transform * shell.get_aabb()).get_center()
	_inward = door.global_transform.basis.z.normalized()
	if _inward.dot(centre - door.global_position) <= 0.0:
		push_error("Intro: %s's local +Z points out of the room; the door convention changed" % DOOR)
		return false
	var frame_mesh := ArrayMesh.new()
	var leaf_mesh := ArrayMesh.new()
	for s in mesh.get_surface_count():
		var dst := leaf_mesh if s == leaf_s or s == handle_s else frame_mesh
		dst.add_surface_from_arrays(mesh.surface_get_primitive_type(s), mesh.surface_get_arrays(s))
		dst.surface_set_material(dst.get_surface_count() - 1, door.get_active_material(s))
	door.mesh = frame_mesh
	# hinged on the edge away from the handle, on the leaf's room-side face
	var hinge_at_min := handle_box.get_center().x > leaf_box.get_center().x
	var hinge_x := leaf_box.position.x if hinge_at_min else leaf_box.end.x
	# the free edge points +X (hinge at min) or -X from the hinge; turning it toward +Z
	# (inward) is a negative turn about Y for +X, positive for -X
	_free_dir = 1.0 if hinge_at_min else -1.0
	_open_sign = -_free_dir
	hinge = Node3D.new()
	hinge.name = DOOR + "_hinge"
	door.add_child(hinge)
	hinge.position = Vector3(hinge_x, 0.0, leaf_box.end.z)
	leaf = MeshInstance3D.new()
	leaf.name = DOOR + "_leaf"
	leaf.mesh = leaf_mesh
	hinge.add_child(leaf)
	leaf.position = -hinge.position
	var quad := QuadMesh.new()   # faces +Z, into the room
	quad.size = Vector2(leaf_box.size.x, leaf_box.size.y)
	var black := StandardMaterial3D.new()
	black.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	black.albedo_color = Color.BLACK
	quad.material = black
	dark = MeshInstance3D.new()
	dark.name = DOOR + "_dark"
	dark.mesh = quad
	dark.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	door.add_child(dark)
	dark.position = Vector3(leaf_box.get_center().x, leaf_box.get_center().y, leaf_box.position.z + DARK_OFFSET)
	dark.visible = false
	_apply_leaf(0.0)
	return true


static func _surface_aabb(mesh: ArrayMesh, s: int) -> AABB:
	var verts: PackedVector3Array = mesh.surface_get_arrays(s)[Mesh.ARRAY_VERTEX]
	var box := AABB(verts[0], Vector3.ZERO)
	for v in verts:
		box = box.expand(v)
	return box


## Where he waits outside, and where the doorway leg ends, both in line with the leaf.
func _place_route() -> bool:
	var xf := door.global_transform
	var leaf_x := hinge.position.x + _free_dir * _leaf_width * 0.5
	var floor_y := terence.home.origin.y
	var out := xf * Vector3(leaf_x, 0.0, -OUTSIDE_DIST)
	var inn := xf * Vector3(leaf_x, 0.0, THRESHOLD_DIST)
	out.y = floor_y
	inn.y = floor_y
	var flat := Vector3(_inward.x, 0.0, _inward.z).normalized()
	_outside = Transform3D(Basis(Vector3.UP, atan2(flat.x, flat.z)), out)   # he faces +Z
	_threshold = inn
	var p := Vector2(inn.x, inn.z)
	if not terence.planner.bounds.has_point(p):
		push_error("Intro: the doorway leg ends at %s, off the walkable floor %s" % [inn, terence.planner.bounds])
		return false
	if not terence.planner._inside(p).is_empty():
		push_error("Intro: the doorway leg ends at %s, inside an obstacle" % inn)
		return false
	return true


func outside_transform() -> Transform3D:
	return _outside


func threshold() -> Vector3:
	return _threshold


## Global position of the hinge line on the floor.
func hinge_position() -> Vector3:
	return hinge.global_position


## Current leaf angle from shut, radians (>= 0).
func door_angle() -> float:
	return abs(hinge.rotation.y)


## Puts Terence outside the door and starts the entrance.
func start() -> bool:
	if hinge == null:
		push_error("Intro: start() before a successful setup()")
		return false
	for m in band:
		if m.current_state() != &"rest":
			push_error("Intro: band member %s is in %s, not at rest" % [m.member_name, m.current_state()])
			return false
	terence.stop()
	terence.global_transform = _outside
	beat = 0.0
	door_state = DoorState.CLOSED
	phase = Phase.OUTSIDE
	return true


func is_running() -> bool:
	return phase != Phase.IDLE and phase != Phase.DONE and phase != Phase.FAILED


func _fail(why: String) -> void:
	push_error("Intro: " + why)
	phase = Phase.FAILED


func step(delta: float) -> void:
	if not is_running():
		return
	var beats := delta * terence.bpm / 60.0
	beat += beats
	_step_door(beats)
	_step_terence()
	_step_reactions()
	if phase == Phase.STARE and beat - _phase_beat >= STARE_BEATS \
			and door_state == DoorState.SHUT and _pending.is_empty():
		phase = Phase.DONE
		finished.emit()


func _step_door(beats: float) -> void:
	match door_state:
		DoorState.CLOSED:
			if beat >= PRE_BEATS:
				door_state = DoorState.OPENING
				_progress = 0.0
				dark.visible = true
		DoorState.OPENING:
			_progress = min(1.0, _progress + beats / OPEN_BEATS)
			var f := 1.0 - (1.0 - _progress) * (1.0 - _progress)   # eases out as it swings wide
			_apply_leaf(OPEN_ANGLE * f)
			if _progress >= 1.0:
				door_state = DoorState.OPEN
		DoorState.OPEN:
			if _entered and _clear_of_swing():
				door_state = DoorState.CLOSING
				_progress = 0.0
		DoorState.CLOSING:
			_progress = min(1.0, _progress + beats / CLOSE_BEATS)
			_apply_leaf(OPEN_ANGLE * (1.0 - _progress * _progress))   # gathers speed and lands
			if _progress >= 1.0:
				door_state = DoorState.SHUT
				dark.visible = false
				_door_sound()


func _apply_leaf(angle: float) -> void:
	hinge.rotation = Vector3(0.0, _open_sign * angle, 0.0)


func _clear_of_swing() -> bool:
	var d := terence.global_position - hinge.global_position
	d.y = 0.0
	return d.length() > _leaf_width + CLOSE_CLEARANCE


func _door_sound() -> void:
	if pool == null:
		return   # sound effects are off (Rehearsal.sfx_enabled)
	var latch := door.global_transform * Vector3(hinge.position.x + _free_dir * _leaf_width, DOOR_SOUND_HEIGHT, hinge.position.z)
	door_voice = pool.play(door_stream, latch, DOOR_SOUND_DB, 1.0)
	if door_voice == null:
		_fail("the door sound did not play")


## How far past the door's plane he is, metres (negative outside).
func depth_inside() -> float:
	return (terence.global_position - door.global_position).dot(_inward)


func _step_terence() -> void:
	match phase:
		Phase.OUTSIDE:
			var opened := door_state == DoorState.OPEN \
				or (door_state == DoorState.OPENING and _progress >= WALK_AT_OPEN)
			if opened:
				if not terence.walk_in(_threshold):
					_fail("Terence found no way from the door to spot_terence")
					return
				phase = Phase.WALKING
		Phase.WALKING:
			if not _entered and depth_inside() > 0.0:
				_entered = true
				for m in band:
					var dist: float = m.global_position.distance_to(door.global_position)
					_pending.append({"member": m, "reaction": ENTRY_REACTION, "at": beat + dist * REACT_BEATS_PER_M})
			if not terence.is_travelling():
				if not terence._at(terence.home):
					_fail("Terence stopped walking at %s, not on spot_terence" % terence.global_position)
					return
				phase = Phase.ARRIVED
				_phase_beat = beat
				for m in band:
					_pending.append({"member": m, "reaction": SETTLE_REACTION, "at": beat})
		Phase.ARRIVED:
			if beat - _phase_beat >= ARRIVE_BEATS:
				if not terence.play_move(WATCH_MOVE):
					_fail("Terence could not play %s" % WATCH_MOVE)
					return
				phase = Phase.WATCH
		Phase.WATCH:
			if terence.current_move() == Terence.HUB:   # the watch check is over
				phase = Phase.STARE
				_phase_beat = beat


## Fires each pending reaction once its member is free for it (a startle still playing
## holds back the look_down that follows it).
func _step_reactions() -> void:
	var keep: Array[Dictionary] = []
	for p in _pending:
		var m: BandMember = p["member"]
		var r: StringName = p["reaction"]
		if beat < p["at"]:
			keep.append(p)
		elif m.current_state() in BandMember.REACTIONS[r]:
			if not m.react(r):
				_fail("band member %s could not %s" % [m.member_name, r])
				return
			reactions.append({"member": m.member_name, "reaction": r, "beat": beat})
		elif beat - p["at"] > REACT_WAIT_BEATS:
			_fail("band member %s is still in %s, too busy to %s" % [m.member_name, m.current_state(), r])
			return
		else:
			keep.append(p)
	_pending = keep


func _process(delta: float) -> void:
	step(delta)
