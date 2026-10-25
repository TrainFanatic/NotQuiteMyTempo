class_name ImportValidator
extends RefCounted
## Checks the imported .glb assets (task C1): scale/height, humanoid bones (Godot's
## SkeletonProfileHumanoid, incl. fingers), triangle limits, expected animation names,
## and that every texture keeps nearest filtering. validate() returns a list of problem
## strings; empty means every asset passes.

const LIMITS := {"band": 2300, "terence": 2300, "instruments": 500, "props": 900, "room": 10000}
const BAND_ANIMS := ["rest", "raise", "play_loop", "lower", "freeze", "startle", "look_down",
	"look_at_drummer", "flip_page", "arms_rest"]
const EXTRA_BONES := ["Instrument", "InstrumentSlide", "PropR", "PropL"]
const OPTIONAL_PROFILE_BONES := ["Root", "LeftEye", "RightEye", "Jaw"]
const ROOM_MARKERS := ["spot_terence", "spot_drummer", "seat_sax_1", "seat_trumpet_4", "seat_bass_1"]


static func asset_paths() -> PackedStringArray:
	var out := PackedStringArray()
	for dir in ["band", "instruments", "props", "room", "terence"]:
		var d := DirAccess.open("res://assets/%s" % dir)
		if d == null:
			continue
		for f in d.get_files():
			if f.ends_with(".glb"):
				out.append("res://assets/%s/%s" % [dir, f])
	return out


static func validate(paths: PackedStringArray = asset_paths(), terence_moves: Array = []) -> Array:
	var problems: Array = []
	if paths.is_empty():
		problems.append("no .glb files under res://assets (run tools/sync_assets.sh)")
	for path in paths:
		problems.append_array(validate_one(path, terence_moves))
	return problems


static func validate_one(path: String, terence_moves: Array = []) -> Array:
	var p: Array = []
	var group := path.get_base_dir().get_file()
	var scene: PackedScene = load(path)
	if scene == null:
		return ["%s: failed to load" % path]
	var root: Node3D = scene.instantiate()
	var tris := 0
	var meshes := root.find_children("*", "MeshInstance3D", true, false)
	var top := -INF
	var bottom := INF
	for mi in meshes:
		var mesh: Mesh = mi.mesh
		for s in mesh.get_surface_count():
			var arrays := mesh.surface_get_arrays(s)
			var idx: PackedInt32Array = arrays[Mesh.ARRAY_INDEX]
			tris += idx.size() / 3 if idx.size() > 0 else (arrays[Mesh.ARRAY_VERTEX] as PackedVector3Array).size() / 3
			var mat := mi.get_active_material(s) as BaseMaterial3D
			if mat and mat.albedo_texture and mat.texture_filter not in [BaseMaterial3D.TEXTURE_FILTER_NEAREST, BaseMaterial3D.TEXTURE_FILTER_NEAREST_WITH_MIPMAPS]:
				p.append("%s: material %s is not nearest-filtered" % [path, mat.resource_name])
			if mat and mat.albedo_texture:
				var size: Vector2 = mat.albedo_texture.get_size()
				if max(size.x, size.y) > 512 or min(size.x, size.y) < 64:
					p.append("%s: texture %s is %s (want 64-512 px)" % [path, mat.albedo_texture.resource_path, size])
		var aabb: AABB = mi.global_transform * mi.get_aabb() if mi.is_inside_tree() else _world_aabb(mi)
		top = max(top, aabb.end.y)
		bottom = min(bottom, aabb.position.y)
	if LIMITS.has(group) and tris > LIMITS[group]:
		p.append("%s: %d triangles > %d" % [path, tris, LIMITS[group]])
	var skels := root.find_children("*", "Skeleton3D", true, false)
	if group in ["band", "terence"]:
		if skels.is_empty():
			p.append("%s: no Skeleton3D" % path)
		else:
			var skel: Skeleton3D = skels[0]
			var profile := SkeletonProfileHumanoid.new()
			for i in profile.bone_size:
				var b := String(profile.get_bone_name(i))
				if b in OPTIONAL_PROFILE_BONES:
					continue
				if skel.find_bone(b) < 0:
					p.append("%s: missing humanoid bone %s" % [path, b])
			if not (top > 1.6 and top < 1.95) or abs(bottom) > 0.03:
				p.append("%s: character spans %.2f..%.2f m (want feet at 0, top 1.6-1.95)" % [path, bottom, top])
		var ap: AnimationPlayer = root.find_children("*", "AnimationPlayer", true, false).front() if not root.find_children("*", "AnimationPlayer", true, false).is_empty() else null
		if ap == null:
			p.append("%s: no AnimationPlayer" % path)
		else:
			var want: Array = BAND_ANIMS.duplicate() if group == "band" else terence_moves
			if group == "band" and path.contains("trombone"):
				want.append("spit_valve")
			for a in want:
				if not ap.has_animation(StringName(a)):
					p.append("%s: missing animation %s" % [path, a])
	if group == "room":
		for m in ROOM_MARKERS:
			if root.find_child(m, true, false) == null:
				p.append("%s: missing marker %s" % [path, m])
		if top > 3.2:
			p.append("%s: room is %.2f m tall (want about 3.0)" % [path, top])
	root.free()
	return p


static func _world_aabb(mi: MeshInstance3D) -> AABB:
	var xf := Transform3D.IDENTITY
	var n: Node = mi
	while n != null and n is Node3D:
		xf = (n as Node3D).transform * xf
		n = n.get_parent()
	return xf * mi.get_aabb()
