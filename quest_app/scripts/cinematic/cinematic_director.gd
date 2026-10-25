extends Node
## Film-style cut-ins on Terence's big moves (the cutoffs, the stares, the shouting, the
## throws): when a move in the shot table starts, the view cuts to a close-up of him and
## plays the move's shots, timed on the move's animation clock.
##
## XR: the headset owns the camera, so the cut moves the player instead. On every cut the
## XROrigin3D is put where the tracked head lands exactly on the shot's camera position,
## heading the way the shot looks, with the player's live head tracking (pitch, roll, small
## head motion) still on top. Shots that move (a push-in, the operator following him) move
## the origin each step the same way. A head-locked blink to black of BLINK seconds covers
## each cut. The passthrough kit window is hidden for the sequence, since it shows the real
## drums at the throne, and the origin and the kit window are put back exactly at the end.
## Desktop: the cinematic camera becomes the current camera for the shots, with the grade
## and the letterbox drawn over the screen, then the previous camera comes back.
##
## Timing follows Terence's AnimBlender (its started/advanced signals report authored
## animation time), so at another tempo the cuts land on the same animation frames.
## A one-shot's shots may run on into the idle stare that follows it; any other animation
## starting (a new move, walking home) ends the sequence there.

const ShotTable := preload("res://scripts/cinematic/shot_table.gd")
const ShotFramer := preload("res://scripts/cinematic/shot_framer.gd")

const NEAR := 0.02
## XR: the blink at each cut, real seconds. Fully black for the first half, then it clears.
const BLINK := 0.08
## XR: the blink quad, head-locked this far ahead of the eyes and this wide (covers the view).
const BLINK_DIST := 0.1
const BLINK_SIZE := 0.6

## Emitted on every cut: the shot (index into the move's shots) now on screen.
signal shot_started(move: StringName, index: int, framing: StringName)
## Emitted when a sequence is over (its last shot ended, or something else took over).
signal sequence_ended(move: StringName)

var terence: Terence
var head: Node3D                      # the player's head (XRCamera3D in XR, the desktop camera otherwise)
var xr_mode := false
## A working copy of ShotTable.SHOTS (tests change it).
var table: Dictionary
var framer: ShotFramer
## The current shot's camera pose and vertical fov (degrees), in both modes.
var shot_transform := Transform3D.IDENTITY
var shot_fov := 0.0
## Desktop only
var cam: Camera3D
var overlay: CanvasLayer
var _prev_camera: Camera3D            # the camera to cut back to
## XR only
var xr_origin: Node3D                 # the rig's XROrigin3D; the head is under it
var kit_window: Node3D                # Rehearsal's KitWindow (passthrough onto the real kit)
var blink: MeshInstance3D             # head-locked black quad, visible only during a blink
var _blink_mat: StandardMaterial3D
var _blink_left := 0.0
var _rest_local := Transform3D.IDENTITY    # the origin's own transform before the sequence
var _rest_global := Transform3D.IDENTITY
var _kit_was_visible := true
var _cut_head_pos := Vector3.ZERO     # the head's position in the origin's frame at the last cut
var _cut_head_yaw := 0.0              # and its heading there (radians)
## Sequence state
var _move: StringName = &""           # the move whose shots are playing, or empty
var _shots: Array = []
var _shot := -1
var _base := 0.0                      # frames of sequence time before the current animation
var _len := 0.0                       # frames in the current animation
var _seq_frame := 0.0
var _in_tail := false


## `head_node` is the player's eye (rig.camera). In XR (`use_xr`), `origin` is the rig's
## XROrigin3D with the head under it, and `kit` is the passthrough kit window; both are
## required there and must be null on the desktop.
func setup(t: Terence, head_node: Node3D, use_xr: bool, origin: Node3D, kit: Node3D) -> bool:
	if t == null or t.blender == null or t.skeleton == null:
		push_error("CinematicDirector: needs a set-up Terence")
		return false
	if head_node == null:
		push_error("CinematicDirector: needs the player's head node (rig.camera)")
		return false
	if use_xr:
		if origin == null:
			push_error("CinematicDirector: XR needs the rig's XROrigin3D to move the view")
			return false
		if not origin.is_ancestor_of(head_node):
			push_error("CinematicDirector: the head %s is not under the XR origin %s" % [head_node.name, origin.name])
			return false
		if kit == null:
			push_error("CinematicDirector: XR needs the KitWindow to hide during close-ups")
			return false
	elif origin != null or kit != null:
		push_error("CinematicDirector: an XR origin or kit window was given for a desktop cut")
		return false
	terence = t
	head = head_node
	xr_mode = use_xr
	table = ShotTable.SHOTS.duplicate(true)
	if not ShotTable.check(table, terence.meta, Terence.CLOSE_MOVES):
		return false
	framer = ShotFramer.new(terence, terence.skeleton)
	if not framer.ok():
		return false
	if xr_mode:
		xr_origin = origin
		kit_window = kit
		_build_blink()
	else:
		_build_desktop()
	terence.move_started.connect(_on_move_started)
	terence.blender.advanced.connect(_on_advanced)
	return true


func _build_desktop() -> void:
	cam = Camera3D.new()
	cam.name = "CinematicCamera"
	cam.near = NEAR
	cam.keep_aspect = Camera3D.KEEP_WIDTH
	add_child(cam)
	overlay = CanvasLayer.new()
	overlay.name = "CinematicOverlay"
	overlay.layer = 10
	overlay.visible = false
	add_child(overlay)
	var rect := ColorRect.new()
	rect.set_anchors_preset(Control.PRESET_FULL_RECT)
	rect.mouse_filter = Control.MOUSE_FILTER_IGNORE
	var mat := ShaderMaterial.new()
	mat.shader = load("res://shaders/cinematic_overlay.gdshader")
	mat.set_shader_parameter("aspect", ShotFramer.ASPECT)
	rect.material = mat
	overlay.add_child(rect)


## The blink: a black quad just ahead of the eyes, on the head, drawn over everything.
func _build_blink() -> void:
	_blink_mat = StandardMaterial3D.new()
	_blink_mat.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	_blink_mat.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
	_blink_mat.no_depth_test = true
	_blink_mat.cull_mode = BaseMaterial3D.CULL_DISABLED
	_blink_mat.disable_fog = true
	_blink_mat.render_priority = Material.RENDER_PRIORITY_MAX
	_blink_mat.albedo_color = Color(0.0, 0.0, 0.0, 1.0)
	var quad := QuadMesh.new()
	quad.size = Vector2(BLINK_SIZE, BLINK_SIZE)
	blink = MeshInstance3D.new()
	blink.name = "CutBlink"
	blink.mesh = quad
	blink.material_override = _blink_mat
	blink.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	blink.position = Vector3(0.0, 0.0, -BLINK_DIST)
	blink.visible = false
	head.add_child(blink)


## True while a shot is on screen.
func is_active() -> bool:
	return _move != &""


func active_move() -> StringName:
	return _move


func shot_index() -> int:
	return _shot


## Sequence time in animation frames since the move started (it includes the idle tail).
func sequence_frame() -> float:
	return _seq_frame


func _on_move_started(id: StringName) -> void:
	if is_active():
		# a one-shot that played to its end hands over to the idle stare: keep going in it
		if id == Terence.HUB and not _in_tail and _seq_frame >= _len - 1e-3 \
				and not terence.meta["moves"][String(_move)]["loop"] and _shots[-1]["to"] > _len:
			_in_tail = true
			_base = _len
			_len = float(terence.meta["moves"][String(id)]["frames"])
			return
		_end()
	if not table.has(id) or not table[id]["enabled"]:
		return
	_shots = ShotTable.resolve(table, id, terence.meta)
	if _shots.is_empty():
		return
	_move = id
	_shot = -1
	_base = 0.0
	_len = float(terence.meta["moves"][String(id)]["frames"])
	_seq_frame = 0.0
	_in_tail = false
	if _begin():
		_update(0.0, 0.0)


## `advanced` reports authored seconds; the sequence clock is in frames.
func _on_advanced(anim: StringName, from_pos: float, to_pos: float, wrapped: bool) -> void:
	if not is_active() or anim != (Terence.HUB if _in_tail else _move):
		return
	var dt := to_pos - from_pos + (_len / ShotTable.FPS if wrapped else 0.0)
	if wrapped:
		_base += _len
	_update(_base + to_pos * ShotTable.FPS, dt)


func _update(frame_t: float, dt: float) -> void:
	_seq_frame = frame_t
	if frame_t >= _shots[-1]["to"]:
		_end()
		return
	var i := _shot if _shot >= 0 else 0
	while i < _shots.size() - 1 and frame_t >= _shots[i]["to"]:
		i += 1
	var cut := i != _shot
	if cut:
		_shot = i
		framer.cut()
		if xr_mode:
			_snapshot_head()
			_start_blink()
		shot_started.emit(_move, i, _shots[i]["framing"])
	var f := framer.frame(_shots[i], frame_t, player_eye(), dt)
	shot_transform = f["transform"]
	shot_fov = f["fov"]
	if xr_mode:
		xr_origin.global_transform = origin_for(shot_transform, _cut_head_pos, _cut_head_yaw)
	else:
		cam.global_transform = shot_transform
		cam.fov = ShotFramer.hfov(shot_fov)


## Where the player's eye is in the room, as if the view had not moved: the framer aims
## shots from the throne, so in XR the head's tracked pose is read against the origin's
## resting place, not where the cut has put it.
func player_eye() -> Vector3:
	if not xr_mode or not is_active():
		return head.global_position
	return _rest_global * (xr_origin.global_transform.affine_inverse() * head.global_position)


## XR: the tracked head's pose in the origin's frame at this cut. The shot is placed against
## it, so the head lands on the shot pose at the cut, and moving the head afterwards still
## moves the view.
func _snapshot_head() -> void:
	var local := xr_origin.global_transform.affine_inverse() * head.global_transform
	_cut_head_pos = local.origin
	_cut_head_yaw = heading(local.basis)


## The origin transform that puts a head at `head_pos` (heading `head_yaw`) in the origin's
## frame exactly on `shot`'s position, heading the way `shot` looks. Only the heading turns
## the origin: it stays upright, so the floor stays level and the head's own pitch and roll
## are kept.
static func origin_for(shot: Transform3D, head_pos: Vector3, head_yaw: float) -> Transform3D:
	var b := Basis(Vector3.UP, heading(shot.basis) - head_yaw)
	return Transform3D(b, shot.origin - b * head_pos)


## The heading (rotation about +Y, radians) a basis looks along. Looking far down at the
## drums, the top of the head points ahead, so it is mixed in (both point the same way
## there, so the heading does not jump).
static func heading(b: Basis) -> float:
	var f := -b.z + b.y * 0.5 if b.z.y > 0.7 else -b.z
	return atan2(-f.x, -f.z)


## The cut in.
func _begin() -> bool:
	if xr_mode:
		if not is_instance_valid(xr_origin) or not is_instance_valid(kit_window):
			push_error("CinematicDirector: the XR origin or kit window is gone, so no cut for %s" % _move)
			_move = &""
			return false
		_rest_local = xr_origin.transform
		_rest_global = xr_origin.global_transform
		_kit_was_visible = kit_window.visible
		kit_window.visible = false
		return true
	_prev_camera = get_viewport().get_camera_3d()
	if _prev_camera == null:
		push_error("CinematicDirector: no current camera to cut back to, so no cut for %s" % _move)
		_move = &""
		return false
	cam.make_current()
	overlay.visible = true
	return true


## The cut out, straight back to where the player was.
func _end() -> void:
	var move := _move
	_move = &""
	_shot = -1
	_in_tail = false
	if xr_mode:
		if not is_instance_valid(xr_origin) or not is_instance_valid(kit_window):
			push_error("CinematicDirector: the XR origin or kit window is gone; cannot put the view back")
		else:
			xr_origin.transform = _rest_local
			kit_window.visible = _kit_was_visible
			_start_blink()
	else:
		overlay.visible = false
		cam.current = false
		if not is_instance_valid(_prev_camera):
			push_error("CinematicDirector: the camera to cut back to is gone")
		else:
			_prev_camera.make_current()
		_prev_camera = null
	sequence_ended.emit(move)


func _start_blink() -> void:
	_blink_left = BLINK
	_blink_mat.albedo_color.a = 1.0
	blink.visible = true


## The blink runs in real time.
func update(delta: float) -> void:
	if _blink_left <= 0.0:
		return
	_blink_left -= delta
	if _blink_left <= 0.0:
		_blink_left = 0.0
		blink.visible = false
		return
	_blink_mat.albedo_color.a = clampf(_blink_left / (BLINK * 0.5), 0.0, 1.0)


func _process(delta: float) -> void:
	update(delta)


func _exit_tree() -> void:
	if is_active():
		_end()
