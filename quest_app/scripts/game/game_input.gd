extends Node
## Pointing at the game's panels (scripts/game/game_panel.gd) without controllers:
##   headset  gaze: the ray straight out of the XR camera; resting on an item for the panel's
##            dwell_s picks it (a ring fills round the cursor). A hand pinch (thumb tip to
##            index tip, OpenXR hand tracking) picks the gazed item at once.
##   desktop  the mouse: hovering dwells the same way, a left click picks at once.
##   scripted `scripted_ray` (origin, dir) overrides both: the movie renders and tests.
## Every visible panel in `panels` is tried; the nearest hit gets the pointer.
## Loaded with preload() by its users.

const PINCH_ON_M := 0.02      # thumb and index tips closer than this: pinched
const PINCH_OFF_M := 0.035    # ...and released past this (hysteresis: no chatter)
const HAND_TRACKERS := [&"/user/hand_tracker/left", &"/user/hand_tracker/right"]

var camera: Camera3D
var xr_mode := false
var panels: Array = []
var scripted_ray := []        # [origin, dir] or empty
var dwell_enabled := true
var target = null             # the panel under the pointer, or null
var _pinched := {}            # tracker name -> bool
var _mouse := Vector2(-1, -1)
var _click := false


func _process(delta: float) -> void:
	var ray := _ray()
	target = null
	var best := INF
	for p in panels:
		if not is_instance_valid(p) or not p.is_visible_in_tree() or ray.is_empty():
			continue
		var d: float = p.ray_distance(ray[0], ray[1])
		if d < best:
			best = d
			target = p
	for p in panels:
		if not is_instance_valid(p):
			continue
		if p == target:
			p.hover_ray(ray[0], ray[1])
		elif p.cursor.x >= 0.0 or p.hovered != "":
			p.unhover()
	if target != null:
		if _click or _pinch_edge():
			target.click()
		elif dwell_enabled:
			target.step(delta)
	else:
		_pinch_edge()   # keep the pinch state current while looking elsewhere
	_click = false


func _ray() -> Array:
	if scripted_ray.size() == 2:
		return scripted_ray
	if camera == null or not is_instance_valid(camera):
		return []
	if xr_mode:
		var xf := camera.global_transform
		return [xf.origin, -xf.basis.z]
	if _mouse.x < 0.0:
		return []
	return [camera.project_ray_origin(_mouse), camera.project_ray_normal(_mouse)]


func _unhandled_input(event: InputEvent) -> void:
	if xr_mode:
		return
	if event is InputEventMouseMotion:
		_mouse = event.position
	elif event is InputEventMouseButton and event.pressed and event.button_index == MOUSE_BUTTON_LEFT:
		_mouse = event.position
		_click = true


## Pick what is pointed at on the next frame (tests; also what a pinch does).
func click() -> void:
	_click = true


## True on the frame a hand closes into a pinch.
func _pinch_edge() -> bool:
	if not xr_mode:
		return false
	var edge := false
	for name in HAND_TRACKERS:
		var t := XRServer.get_tracker(name) as XRHandTracker
		if t == null or not t.has_tracking_data:
			_pinched[name] = false
			continue
		var thumb := t.get_hand_joint_transform(XRHandTracker.HAND_JOINT_THUMB_TIP).origin
		var index := t.get_hand_joint_transform(XRHandTracker.HAND_JOINT_INDEX_FINGER_TIP).origin
		var d := thumb.distance_to(index)
		var was: bool = _pinched.get(name, false)
		var now := d < PINCH_ON_M or (was and d < PINCH_OFF_M)
		if now and not was:
			edge = true
		_pinched[name] = now
	return edge
