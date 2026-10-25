class_name PlayerRig
extends Node3D
## Where the player's eyes are. On the Quest (Android export) or with `-- --xr` on the
## command line it starts OpenXR with passthrough, and stops with an error if either is
## unavailable: this is a mixed-reality app, and without passthrough the real drum kit
## would be invisible. Anywhere else it is a desktop preview camera at the drummer's
## seated eye height, looking at Terence.

const SEATED_EYE_HEIGHT := 1.15

var xr_mode := false
var camera: Camera3D


static func xr_requested() -> bool:
	return OS.has_feature("android") or "--xr" in OS.get_cmdline_user_args()


func setup(drummer_spot: Transform3D, look_target: Vector3) -> bool:
	name = "PlayerRig"
	xr_mode = xr_requested()
	if xr_mode:
		return _setup_xr(drummer_spot)
	camera = Camera3D.new()
	camera.name = "DesktopCamera"
	camera.fov = 70.0
	camera.near = 0.05
	add_child(camera)
	var eye := drummer_spot.origin + Vector3.UP * SEATED_EYE_HEIGHT
	camera.look_at_from_position(eye, look_target, Vector3.UP)
	camera.current = true
	return true


func _setup_xr(drummer_spot: Transform3D) -> bool:
	var xr := XRServer.find_interface("OpenXR")
	if xr == null:
		push_error("PlayerRig: XR requested but the OpenXR interface isn't available")
		get_tree().quit(1)
		return false
	if not xr.is_initialized() and not xr.initialize():
		push_error("PlayerRig: OpenXR failed to initialise (is the headset connected and the runtime running?)")
		get_tree().quit(1)
		return false
	get_viewport().transparent_bg = true
	get_viewport().use_xr = true
	# The runtime only reports its blend modes once the OpenXR session has begun, which is
	# after _ready, so passthrough is switched on (or the app stops) from session_begun.
	xr.session_begun.connect(_enable_passthrough.bind(xr), CONNECT_ONE_SHOT)
	var origin := XROrigin3D.new()
	origin.name = "XROrigin3D"
	# the floor under the drum throne, facing Terence (marker forward is -Z)
	origin.transform = drummer_spot
	add_child(origin)
	camera = XRCamera3D.new()
	camera.name = "XRCamera3D"
	camera.near = 0.05
	origin.add_child(camera)
	for hand in ["left_hand", "right_hand"]:
		var c := XRController3D.new()
		c.name = hand
		c.tracker = hand
		origin.add_child(c)
	return true


func _enable_passthrough(xr: XRInterface) -> void:
	var modes: Array = xr.get_supported_environment_blend_modes()
	if not XRInterface.XR_ENV_BLEND_MODE_ALPHA_BLEND in modes:
		push_error("PlayerRig: passthrough (alpha blend) unsupported by the runtime (modes %s); check the Meta passthrough export option" % [modes])
		get_tree().quit(1)
		return
	if not xr.set_environment_blend_mode(XRInterface.XR_ENV_BLEND_MODE_ALPHA_BLEND):
		push_error("PlayerRig: the runtime refused alpha-blend passthrough")
		get_tree().quit(1)
		return
	print("PlayerRig: passthrough on")
