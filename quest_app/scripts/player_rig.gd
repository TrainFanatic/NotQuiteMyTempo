class_name PlayerRig
extends Node3D
## Where the player's eyes are. On the Quest (Android export) or with `-- --xr` on the
## command line it starts OpenXR with passthrough, and stops with an error if either is
## unavailable: this is a mixed-reality app, and without passthrough the real drum kit
## would be invisible. Anywhere else it is a desktop preview camera at the drummer's
## seated eye height, looking at Terence.

const SEATED_EYE_HEIGHT := 1.15
## The OpenXR Vendors plugin's Meta passthrough wrapper (an Engine singleton on the Quest).
const FB_PASSTHROUGH := &"OpenXRFbPassthroughExtensionWrapper"
## Its LayerPurpose for full (reconstruction) passthrough, the one alpha blend starts.
const FB_LAYER_PURPOSE_RECONSTRUCTION := 0
## The self-test reads the passthrough state back this long after it was switched on: the
## plugin starts its layer on the render thread and reports back a frame or two later.
const PASSTHROUGH_SELF_TEST_DELAY := 3.0
const PASSTHROUGH_DEBUG_ARG := "--passthrough-debug"

var xr_mode := false
var camera: Camera3D
## XR only: the tracking space the head and hands sit in (the cinematic cuts move it).
var origin: XROrigin3D
var _xr: XRInterface
var _fb_passthrough: Object


static func xr_requested() -> bool:
	return OS.has_feature("android") or "--xr" in OS.get_cmdline_user_args()


## -- --passthrough-debug: the room's walls, floor and ceiling are drawn see-through
## (Rehearsal._add_kit_window), so passthrough can be confirmed at a glance. On the Quest:
##   adb shell am start -n com.notquitemytempo.app/com.godot.game.GodotApp --esa command_line_params "--,--passthrough-debug"
## (tools/build_quest.sh install-debug does this).
static func passthrough_debug_requested() -> bool:
	return PASSTHROUGH_DEBUG_ARG in OS.get_cmdline_user_args()


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
	origin = XROrigin3D.new()
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
	_xr = xr
	var modes: Array = xr.get_supported_environment_blend_modes()
	if not XRInterface.XR_ENV_BLEND_MODE_ALPHA_BLEND in modes:
		push_error("PlayerRig: passthrough (alpha blend) unsupported by the runtime (modes %s); check the Meta passthrough export option" % [modes])
		get_tree().quit(1)
		return
	if not Engine.has_singleton(FB_PASSTHROUGH):
		push_error("PlayerRig: %s missing; the OpenXR Vendors plugin (Meta) isn't loaded, so nothing would draw the passthrough layer" % FB_PASSTHROUGH)
		get_tree().quit(1)
		return
	_fb_passthrough = Engine.get_singleton(FB_PASSTHROUGH)
	if not _fb_passthrough.call(&"is_passthrough_supported"):
		push_error("PlayerRig: XR_FB_passthrough isn't enabled for this session (xr/openxr/extensions/meta/passthrough and the export option)")
		get_tree().quit(1)
		return
	_fb_passthrough.connect(&"openxr_fb_passthrough_stopped", _on_passthrough_stopped)
	_fb_passthrough.connect(&"openxr_fb_passthrough_state_changed", _on_passthrough_state_changed)
	# With alpha blend the plugin starts XR_FB_passthrough and a reconstruction layer under
	# the app's frame, and Godot submits the frame with source alpha (premultiplied), so
	# passthrough shows wherever the frame has alpha 0.
	if not xr.set_environment_blend_mode(XRInterface.XR_ENV_BLEND_MODE_ALPHA_BLEND):
		push_error("PlayerRig: the runtime refused alpha-blend passthrough")
		get_tree().quit(1)
		return
	print("PlayerRig: passthrough requested (alpha blend)")
	get_tree().create_timer(PASSTHROUGH_SELF_TEST_DELAY).timeout.connect(passthrough_self_test)


## Reads back what is actually running and prints it; push_error unless the environment
## blend mode is alpha blend, the viewport keeps its alpha, and the plugin's passthrough
## is started with its reconstruction layer. Returns whether everything checked out.
func passthrough_self_test() -> bool:
	var blend: int = _xr.get_environment_blend_mode()
	var started: bool = _fb_passthrough.call(&"is_passthrough_started")
	var layer: int = _fb_passthrough.call(&"get_current_layer_purpose")
	var transparent := get_viewport().transparent_bg
	print("PlayerRig: passthrough self-test: blend_mode=%s started=%s layer=%s transparent_bg=%s opacity=%.2f" % [
		_blend_mode_name(blend), started, _layer_name(layer), transparent,
		_fb_passthrough.call(&"get_texture_opacity_factor")])
	var problems: Array[String] = []
	if blend != XRInterface.XR_ENV_BLEND_MODE_ALPHA_BLEND:
		problems.append("blend mode is %s, not ALPHA_BLEND" % _blend_mode_name(blend))
	if not started:
		problems.append("XR_FB_passthrough is not started (paused or failed, see the OpenXR_Passthrough log)")
	if layer != FB_LAYER_PURPOSE_RECONSTRUCTION:
		problems.append("passthrough layer is %s, not RECONSTRUCTION" % _layer_name(layer))
	if not transparent:
		problems.append("the viewport's transparent_bg is off, so the frame's alpha never reaches the compositor")
	if not problems.is_empty():
		push_error("PlayerRig: passthrough is NOT running: " + "; ".join(problems))
		return false
	print("PlayerRig: passthrough self-test passed")
	return true


func _on_passthrough_stopped() -> void:
	push_error("PlayerRig: passthrough stopped by the runtime")


func _on_passthrough_state_changed(event_type: int) -> void:
	# OpenXRFbPassthroughExtensionWrapper.PassthroughStateChangedEvent
	match event_type:
		0:
			push_error("PlayerRig: passthrough hit a non-recoverable error")
		1:
			push_error("PlayerRig: passthrough hit a recoverable error (paused until restored)")
		2:
			print("PlayerRig: passthrough restored after an error")
		_:
			push_error("PlayerRig: unknown passthrough state event %d" % event_type)


static func _blend_mode_name(mode: int) -> String:
	match mode:
		XRInterface.XR_ENV_BLEND_MODE_OPAQUE:
			return "OPAQUE"
		XRInterface.XR_ENV_BLEND_MODE_ADDITIVE:
			return "ADDITIVE"
		XRInterface.XR_ENV_BLEND_MODE_ALPHA_BLEND:
			return "ALPHA_BLEND"
	return "UNKNOWN(%d)" % mode


static func _layer_name(purpose: int) -> String:
	match purpose:
		-1:
			return "NONE"
		0:
			return "RECONSTRUCTION"
		1:
			return "PROJECTED"
	return "UNKNOWN(%d)" % purpose
