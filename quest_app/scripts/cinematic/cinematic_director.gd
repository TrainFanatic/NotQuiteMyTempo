extends Node
## Film-style cut-ins on Terence's big moves (the cutoffs, the stares, the shouting, the
## throws): when a move in the shot table starts, a virtual camera cuts to a close-up of him
## and plays the move's shots, timed on the move's animation clock.
##
## XR (panel mode): the player's own camera never moves (moving it makes people sick).
## Instead a letterboxed 2.39:1 "cinema panel" appears at the cut, PANEL_DIST in front of the
## player and a little below eye line, clear of Terence, and stays put in the room for the
## whole sequence. It shows the cinematic camera, rendered into a small SubViewport that
## only renders while a shot is on, and fades out after the last shot.
## Desktop: a true cut. The cinematic camera becomes the current camera for the shots, with
## the grade and the letterbox drawn over the screen, then the previous camera comes back.
##
## Timing follows Terence's AnimBlender (its started/advanced signals report authored
## animation time), so at another tempo the cuts land on the same animation frames.
## A one-shot's shots may run on into the idle stare that follows it; any other animation
## starting (a new move, walking home) ends the sequence there.

const ShotTable := preload("res://scripts/cinematic/shot_table.gd")
const ShotFramer := preload("res://scripts/cinematic/shot_framer.gd")

## The cinematic camera's resolution in panel mode (2.39:1, low to keep the Quest at 72 fps).
const PANEL_RES := Vector2i(640, 268)
## Panel size and placement, metres, relative to the player's head when the sequence starts.
const PANEL_WIDTH := 0.9
const PANEL_BAR := 0.04               # black bar above and below the picture
const PANEL_DIST := 1.3
const PANEL_DROP := 0.10              # centre this far below eye height
## The panel keeps this clear (degrees, beyond its own half width) of Terence's body,
## taken as TERENCE_HALF_WIDTH metres either side of his head.
const PANEL_MARGIN := 3.0
const TERENCE_HALF_WIDTH := 0.24
const FADE_OUT := 0.2                 # real seconds
## The panel is on its own render layer, so the cinematic camera never films it.
const PANEL_LAYER := 20
const NEAR := 0.02

## Emitted on every cut: the shot (index into the move's shots) now on screen.
signal shot_started(move: StringName, index: int, framing: StringName)
## Emitted when a sequence is over (its last shot ended, or something else took over).
signal sequence_ended(move: StringName)

var terence: Terence
var head: Node3D                      # the player's head (XRCamera3D in XR, the desktop camera otherwise)
var panel_mode := false
## A working copy of ShotTable.SHOTS (tests change it).
var table: Dictionary
var framer: ShotFramer
var cam: Camera3D
var viewport: SubViewport             # panel mode only
var panel: MeshInstance3D             # panel mode only
var overlay: CanvasLayer              # desktop only
var _panel_mat: ShaderMaterial
var _prev_camera: Camera3D            # desktop: the camera to cut back to
## Sequence state
var _move: StringName = &""           # the move whose shots are playing, or empty
var _shots: Array = []
var _shot := -1
var _base := 0.0                      # frames of sequence time before the current animation
var _len := 0.0                       # frames in the current animation
var _seq_frame := 0.0
var _in_tail := false
var _fade := 0.0                      # seconds of fade-out left


## `head_node` is the player's eye (rig.camera). `world_env` is the scene's Environment; the
## panel's camera uses a copy without tonemapping, as the panel is tonemapped again with the
## rest of the scene.
func setup(t: Terence, head_node: Node3D, use_panel: bool, world_env: Environment) -> bool:
	if t == null or t.blender == null or t.skeleton == null:
		push_error("CinematicDirector: needs a set-up Terence")
		return false
	if head_node == null:
		push_error("CinematicDirector: needs the player's head node (rig.camera)")
		return false
	if world_env == null:
		push_error("CinematicDirector: needs the scene's Environment")
		return false
	terence = t
	head = head_node
	panel_mode = use_panel
	table = ShotTable.SHOTS.duplicate(true)
	if not ShotTable.check(table, terence.meta, Terence.CLOSE_MOVES):
		return false
	framer = ShotFramer.new(terence, terence.skeleton)
	if not framer.ok():
		return false
	cam = Camera3D.new()
	cam.name = "CinematicCamera"
	cam.near = NEAR
	cam.keep_aspect = Camera3D.KEEP_WIDTH
	cam.cull_mask &= ~(1 << (PANEL_LAYER - 1))
	if panel_mode:
		_build_panel(world_env)
	else:
		_build_overlay()
	terence.move_started.connect(_on_move_started)
	terence.blender.advanced.connect(_on_advanced)
	return true


func _build_panel(world_env: Environment) -> void:
	viewport = SubViewport.new()
	viewport.name = "CinematicViewport"
	viewport.size = PANEL_RES
	viewport.render_target_update_mode = SubViewport.UPDATE_DISABLED
	viewport.render_target_clear_mode = SubViewport.CLEAR_MODE_ALWAYS
	viewport.msaa_3d = Viewport.MSAA_DISABLED
	viewport.positional_shadow_atlas_size = 0
	viewport.audio_listener_enable_3d = false
	viewport.transparent_bg = false
	add_child(viewport)
	var env := world_env.duplicate() as Environment
	env.background_mode = Environment.BG_COLOR
	env.background_color = Color(0.0, 0.0, 0.0, 1.0)
	env.tonemap_mode = Environment.TONE_MAPPER_LINEAR
	env.glow_enabled = false
	cam.environment = env
	viewport.add_child(cam)
	cam.current = true
	_panel_mat = ShaderMaterial.new()
	_panel_mat.shader = load("res://shaders/cinematic_panel.gdshader")
	_panel_mat.render_priority = Material.RENDER_PRIORITY_MAX
	_panel_mat.set_shader_parameter("frame_tex", viewport.get_texture())
	var pic_h := PANEL_WIDTH / ShotFramer.ASPECT
	var quad := QuadMesh.new()
	quad.size = Vector2(PANEL_WIDTH, pic_h + 2.0 * PANEL_BAR)
	_panel_mat.set_shader_parameter("bar", PANEL_BAR / quad.size.y)
	panel = MeshInstance3D.new()
	panel.name = "CinemaPanel"
	panel.mesh = quad
	panel.material_override = _panel_mat
	panel.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	panel.layers = 1 << (PANEL_LAYER - 1)
	panel.visible = false
	panel.top_level = true
	add_child(panel)


func _build_overlay() -> void:
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


## True while a shot is on screen (not counting the panel's fade-out).
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
	if i != _shot:
		_shot = i
		framer.cut()
		shot_started.emit(_move, i, _shots[i]["framing"])
	var f := framer.frame(_shots[i], frame_t, head.global_position, dt)
	cam.global_transform = f["transform"]
	cam.fov = ShotFramer.hfov(f["fov"])


## The cut in.
func _begin() -> bool:
	if panel_mode:
		panel.global_transform = place_panel(head.global_transform, framer.face_point())
		_fade = 0.0
		_panel_mat.set_shader_parameter("fade", 1.0)
		panel.visible = true
		viewport.render_target_update_mode = SubViewport.UPDATE_ALWAYS
		return true
	_prev_camera = get_viewport().get_camera_3d()
	if _prev_camera == null:
		push_error("CinematicDirector: no current camera to cut back to, so no cut for %s" % _move)
		_move = &""
		return false
	cam.make_current()
	overlay.visible = true
	return true


## The cut out: the panel freezes on its last frame and fades; the desktop cuts straight back.
func _end() -> void:
	var move := _move
	_move = &""
	_shot = -1
	_in_tail = false
	if panel_mode:
		viewport.render_target_update_mode = SubViewport.UPDATE_DISABLED
		_fade = FADE_OUT
	else:
		overlay.visible = false
		cam.current = false
		if not is_instance_valid(_prev_camera):
			push_error("CinematicDirector: the camera to cut back to is gone")
		else:
			_prev_camera.make_current()
		_prev_camera = null
	sequence_ended.emit(move)


## The panel's transform for a sequence starting now: PANEL_DIST along the player's view
## (levelled), PANEL_DROP below the eyes, facing them; swung sideways if it would cover
## Terence, whose face is at `face`.
static func place_panel(head_xf: Transform3D, face: Vector3) -> Transform3D:
	var eye := head_xf.origin
	# levelled view direction; looking down at the drums, the top of the head points ahead
	var fwd := ShotFramer._flat(-head_xf.basis.z + head_xf.basis.y)
	var to_t := ShotFramer._flat(face - eye)
	var dist_t := Vector2(face.x - eye.x, face.z - eye.z).length()
	var need := rad_to_deg(atan((PANEL_WIDTH / 2.0) / PANEL_DIST) + atan(TERENCE_HALF_WIDTH / dist_t)) + PANEL_MARGIN
	var off := rad_to_deg(to_t.signed_angle_to(fwd, Vector3.UP))
	var dir := fwd
	if absf(off) < need:
		# swing it out past him on the side the player already looks toward
		dir = to_t.rotated(Vector3.UP, deg_to_rad(need if off >= 0.0 else -need))
	var pos := eye + dir * PANEL_DIST + Vector3.DOWN * PANEL_DROP
	return Transform3D(Basis.looking_at(pos - eye, Vector3.UP), pos)


## The panel's fade-out runs in real time.
func update(delta: float) -> void:
	if _fade <= 0.0:
		return
	_fade -= delta
	if _fade <= 0.0:
		_fade = 0.0
		panel.visible = false
	_panel_mat.set_shader_parameter("fade", _fade / FADE_OUT)


func _process(delta: float) -> void:
	update(delta)


func _exit_tree() -> void:
	if is_active() and not panel_mode:
		_end()
