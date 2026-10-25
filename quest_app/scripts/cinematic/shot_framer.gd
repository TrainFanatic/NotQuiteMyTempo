extends RefCounted
## Frames one shot from Terence's skeleton: where the cinematic camera sits and what it looks
## at, recomputed every step so the shot follows him, with a steady (smoothed) operator.
## Loaded with preload() by its users, so no global class cache entry is needed.
##
## Every framing has a subject point on a bone (Head for faces, RightHand for the fist,
## Neck for the medium) and puts the camera on the player's side of him: its direction from
## the subject is halfway between where his face points and the way to the player, turned
## `azimuth` degrees for a three-quarter view. `height` raises (or, negative, lowers for a
## low angle) the camera, and `look_room` shifts the aim toward where he looks so his face
## sits off centre with space in front of it. over_shoulder is the exception: it sits just
## over the player's right shoulder and looks at his face (the player has no avatar, so a
## shot onto the player would show an empty throne).

## The Head anchor is in the head bone's frame (+Y up through the skull, +Z out of the face);
## other anchors are world-space offsets from their bone, whose axes are not as tidy.
const FRAMINGS := {
	&"extreme_close_face": {"bone": "Head", "anchor": Vector3(0.0, 0.10, 0.07), "dist": 0.42, "height": -0.05, "azimuth": 14.0, "look_room": 0.015, "follow": 7.0},
	&"close_face": {"bone": "Head", "anchor": Vector3(0.0, 0.07, 0.05), "dist": 0.80, "height": -0.10, "azimuth": 20.0, "look_room": 0.05, "follow": 8.0},
	&"fist_insert": {"bone": "RightHand", "anchor": Vector3(0.0, 0.05, 0.0), "dist": 0.55, "height": -0.12, "azimuth": -18.0, "look_room": 0.0, "follow": 24.0},
	&"low_angle_medium": {"bone": "Neck", "anchor": Vector3(0.0, -0.08, 0.0), "dist": 1.90, "height": -0.50, "azimuth": 22.0, "look_room": 0.06, "follow": 6.0},
	&"over_shoulder": {"bone": "Head", "anchor": Vector3(0.0, 0.07, 0.05), "shoulder": Vector3(0.22, 0.04, 0.16), "follow": 8.0},
}
## push_in dollies from DOLLY_FROM to DOLLY_TO times the framing's distance; snap_zoom starts
## SNAP_WIDE times wider than the shot's lens and crashes in at the accent.
const DOLLY_FROM := 1.2
const DOLLY_TO := 0.9
const SNAP_WIDE := 1.8
const SNAP_FRAMES := 3.0
## The cinema frame's aspect ratio (2.39:1).
const ASPECT := 2.39

var skeleton: Skeleton3D
var terence: Node3D
var _bones := {}
var _subject := Vector3.ZERO      # smoothed subject point
var _dir := Vector3.FORWARD       # smoothed camera direction from the subject (horizontal)
var _primed := false
var _ok := true


func _init(terence_node: Node3D, terence_skeleton: Skeleton3D) -> void:
	terence = terence_node
	skeleton = terence_skeleton
	for f in FRAMINGS:
		var bone: String = FRAMINGS[f]["bone"]
		var i := skeleton.find_bone(bone)
		if i < 0:
			push_error("ShotFramer: Terence's skeleton has no %s bone (framing %s)" % [bone, f])
			_ok = false
			continue
		_bones[bone] = i


## False when a bone a framing needs is missing (already reported).
func ok() -> bool:
	return _ok


## The next frame() snaps to the subject instead of easing toward it (a cut).
func cut() -> void:
	_primed = false


func bone_global(bone: String) -> Transform3D:
	var xf := skeleton.global_transform * skeleton.get_bone_global_pose(_bones[bone])
	return Transform3D(xf.basis.orthonormalized(), xf.origin)


## Where his face is (the point between the eyes, roughly), in world space.
func face_point() -> Vector3:
	return bone_global("Head") * FRAMINGS[&"close_face"]["anchor"]


## The camera transform and vertical fov (degrees) for `shot` at sequence time `frame`
## (animation frames, fractional). `head` is the player's head; `dt` the animation time
## since the last call, which paces the follow.
func frame(shot: Dictionary, frame_t: float, head: Vector3, dt: float) -> Dictionary:
	var f: Dictionary = FRAMINGS[shot["framing"]]
	var bxf := bone_global(f["bone"])
	var subject: Vector3 = bxf * f["anchor"] if f["bone"] == "Head" else bxf.origin + f["anchor"]
	var face_fwd := _flat(bxf.basis.z) if f["bone"] == "Head" else _flat(bone_global("Head").basis.z)
	var to_player := _flat(head - subject)
	var dir := (face_fwd + to_player).normalized()
	if dir.length() < 0.5:
		dir = to_player
	var k := 1.0 - exp(-float(f["follow"]) * maxf(dt, 0.0))
	if not _primed:
		_subject = subject
		_dir = dir
		_primed = true
	else:
		_subject = _subject.lerp(subject, k)
		_dir = _dir.slerp(dir, k).normalized()
	var progress := clampf((frame_t - shot["from"]) / float(shot["to"] - shot["from"]), 0.0, 1.0)
	var fov: float = shot["fov"]
	if shot["camera"] == &"snap_zoom":
		var into := clampf((frame_t - float(shot["accent"])) / SNAP_FRAMES, 0.0, 1.0)
		fov *= lerpf(SNAP_WIDE, 1.0, 1.0 - pow(1.0 - into, 3.0))
	var eye: Vector3
	if shot["framing"] == &"over_shoulder":
		var look := (_subject - head).normalized()
		var right := look.cross(Vector3.UP).normalized()
		var sh: Vector3 = f["shoulder"]
		eye = head + right * sh.x + Vector3.UP * sh.y - look * sh.z
	else:
		var dist: float = f["dist"]
		if shot["camera"] == &"push_in":
			dist *= lerpf(DOLLY_FROM, DOLLY_TO, smoothstep(0.0, 1.0, progress))
		var d := _dir.rotated(Vector3.UP, deg_to_rad(f["azimuth"]))
		eye = _subject + d * dist + Vector3.UP * float(f["height"])
	var aim := _subject
	if f.has("look_room") and f["look_room"] > 0.0:
		# shift the aim toward where he looks, across the camera's axis
		var axis := _flat(_subject - eye)
		var side := to_player - axis * to_player.dot(axis)
		if side.length() > 0.05:
			aim += side.normalized() * float(f["look_room"])
	return {"transform": Transform3D(Basis.looking_at(aim - eye, Vector3.UP), eye), "fov": fov}


## Horizontal field of view (degrees) that gives `vfov` vertically on a 2.39:1 frame. The
## camera keeps its width, so a wider window shows the same frame and the bars hide the rest.
static func hfov(vfov: float) -> float:
	return rad_to_deg(2.0 * atan(tan(deg_to_rad(vfov) / 2.0) * ASPECT))


static func _flat(v: Vector3) -> Vector3:
	return Vector3(v.x, 0.0, v.z).normalized()
