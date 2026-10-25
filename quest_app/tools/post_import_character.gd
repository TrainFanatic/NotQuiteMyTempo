@tool
extends EditorScenePostImport
## Post-import for band members and Terence (set on their .glb.import files).
## Godot strips a "_loop" suffix from animation names; the work order names them exactly
## (band "play_loop"), so restore that, and set loop modes from the asset metadata:
## band loops are fixed, Terence's come from terence_v004_moves.json.

const BAND_LOOPS := ["rest", "play_loop", "arms_rest"]
const TERENCE_META := "res://assets/terence/terence_v004_moves.json"


func _post_import(scene: Node) -> Object:
	var players := scene.find_children("*", "AnimationPlayer", true, false)
	if players.is_empty():
		push_error("post_import_character: no AnimationPlayer in %s" % get_source_file())
		return scene
	var ap: AnimationPlayer = players[0]
	var lib: AnimationLibrary = ap.get_animation_library(&"")
	if lib.has_animation(&"play") and not lib.has_animation(&"play_loop"):
		lib.rename_animation(&"play", &"play_loop")
	var loops: Array = []
	if get_source_file().get_file().begins_with("terence"):
		var text := FileAccess.get_file_as_string(TERENCE_META)
		if text.is_empty():
			push_error("post_import_character: cannot read %s" % TERENCE_META)
			return scene
		var meta: Dictionary = JSON.parse_string(text)
		for move in meta["moves"]:
			if meta["moves"][move]["loop"]:
				loops.append(move)
	else:
		loops = BAND_LOOPS
	for anim_name in lib.get_animation_list():
		var anim := lib.get_animation(anim_name)
		anim.loop_mode = Animation.LOOP_LINEAR if String(anim_name) in loops else Animation.LOOP_NONE
	return scene
