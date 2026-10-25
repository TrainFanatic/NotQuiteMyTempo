extends RefCounted
## The sound effects and their cue tables, loaded once from a folder (res://assets/sfx by
## default, copied there by tools/sync_assets.sh):
##   sfx_manifest.json  {"sample_rate", "sounds": {name: {"file", "seconds", ...}}}
##   sfx_cues.json      {"fps", "version", "terence": {action: [cue]},
##                       "band": {"default" | kind | member: {action: [cue]}}}
##   <name>.wav         one per manifest entry, imported by Godot as AudioStreamWAV
## A cue is {"frame", "sfx", "anchor", "gain_db", "pitch_jitter"}. Every problem (missing
## file, unknown sound, malformed cue) is a push_error and the load returns null.
## Bone anchors are checked later against each character's skeleton (SfxCuePlayer).
## Loaded with preload() by its users, so no global class cache entry is needed.

const DIR := "res://assets/sfx"
const MANIFEST := "sfx_manifest.json"
const CUES := "sfx_cues.json"
const VERSION := 1
const CUE_KEYS := ["frame", "sfx", "anchor", "gain_db", "pitch_jitter"]
const PROP_ANCHORS := ["PropR", "PropL"]
const WORLD_PREFIX := "world:"
## pitch_jitter is a fraction of the base pitch; more than this is an authoring mistake.
const MAX_PITCH_JITTER := 0.5
## Allowed difference between the manifest's "seconds" and the imported stream length.
const LENGTH_TOLERANCE := 0.01

var dir := ""
var sample_rate := 0
## name -> AudioStreamWAV
var streams := {}
var fps := 0.0
var terence_cues := {}
## "default", kinds and member names -> {action: [cue]}
var band_cues := {}


## Loads `folder`/sfx_manifest.json, sfx_cues.json and every WAV. Null on any problem.
static func load_dir(folder: String = DIR):
	var manifest = _read_json(folder.path_join(MANIFEST))
	if manifest == null:
		return null
	var cues = _read_json(folder.path_join(CUES))
	if cues == null:
		return null
	return from_data(manifest, cues, folder)


## Builds a library from parsed manifest and cue dictionaries; WAVs come from `folder`.
static func from_data(manifest: Dictionary, cues: Dictionary, folder: String):
	var lib = new()
	lib.dir = folder
	if not lib._load_manifest(manifest) or not lib._load_cues(cues):
		return null
	return lib


static func _read_json(path: String):
	if not FileAccess.file_exists(path):
		push_error("SfxLibrary: missing %s (run tools/sync_assets.sh)" % path)
		return null
	var data = JSON.parse_string(FileAccess.get_file_as_string(path))
	if not data is Dictionary:
		push_error("SfxLibrary: %s is not a JSON object" % path)
		return null
	return data


func _load_manifest(m: Dictionary) -> bool:
	for key in ["sample_rate", "sounds"]:
		if not m.has(key):
			push_error("SfxLibrary: %s lacks '%s'" % [MANIFEST, key])
			return false
	sample_rate = int(m["sample_rate"])
	var sounds = m["sounds"]
	if not sounds is Dictionary or sounds.is_empty():
		push_error("SfxLibrary: %s has no sounds" % MANIFEST)
		return false
	for sfx_name in sounds:
		var entry = sounds[sfx_name]
		if not entry is Dictionary or not entry.has("file") or not entry.has("seconds"):
			push_error("SfxLibrary: sound %s needs 'file' and 'seconds'" % sfx_name)
			return false
		var path: String = dir.path_join(entry["file"])
		if not ResourceLoader.exists(path):
			push_error("SfxLibrary: sound %s: missing %s (run tools/sync_assets.sh, then import)" % [sfx_name, path])
			return false
		var stream = load(path)
		if not stream is AudioStreamWAV:
			push_error("SfxLibrary: %s did not import as AudioStreamWAV" % path)
			return false
		if (stream as AudioStreamWAV).mix_rate != sample_rate:
			push_error("SfxLibrary: %s is %d Hz, manifest says %d Hz" % [path, stream.mix_rate, sample_rate])
			return false
		if abs(stream.get_length() - float(entry["seconds"])) > LENGTH_TOLERANCE:
			push_error("SfxLibrary: %s is %.3f s, manifest says %.3f s" % [path, stream.get_length(), float(entry["seconds"])])
			return false
		streams[StringName(sfx_name)] = stream
	return true


func _load_cues(c: Dictionary) -> bool:
	for key in ["fps", "version", "terence", "band"]:
		if not c.has(key):
			push_error("SfxLibrary: %s lacks '%s'" % [CUES, key])
			return false
	if int(c["version"]) != VERSION:
		push_error("SfxLibrary: %s is version %s, this code reads version %d" % [CUES, c["version"], VERSION])
		return false
	fps = float(c["fps"])
	if fps <= 0.0:
		push_error("SfxLibrary: %s has fps %s" % [CUES, c["fps"]])
		return false
	if not _check_table("terence", c["terence"]):
		return false
	terence_cues = c["terence"]
	if not c["band"] is Dictionary:
		push_error("SfxLibrary: %s 'band' is not an object" % CUES)
		return false
	for key in c["band"]:
		if not _check_table("band." + key, c["band"][key]):
			return false
	band_cues = c["band"]
	return true


func _check_table(where: String, table) -> bool:
	if not table is Dictionary:
		push_error("SfxLibrary: %s table %s is not an object" % [CUES, where])
		return false
	for action in table:
		var list = table[action]
		if not list is Array:
			push_error("SfxLibrary: %s %s.%s is not a list of cues" % [CUES, where, action])
			return false
		for cue in list:
			var problem := _cue_problem(cue)
			if problem != "":
				push_error("SfxLibrary: %s %s.%s: %s" % [CUES, where, action, problem])
				return false
	return true


## Empty if the cue is well formed and names a known sound; otherwise what is wrong.
func _cue_problem(cue) -> String:
	if not cue is Dictionary:
		return "cue %s is not an object" % [cue]
	for key in CUE_KEYS:
		if not cue.has(key):
			return "cue %s lacks '%s'" % [cue, key]
	var frame = cue["frame"]
	if not (frame is float or frame is int) or frame < 0 or float(frame) != floor(float(frame)):
		return "frame %s is not a whole number >= 0" % [frame]
	if not streams.has(StringName(str(cue["sfx"]))):
		return "unknown sound '%s' (not in %s)" % [cue["sfx"], MANIFEST]
	if not (cue["gain_db"] is float or cue["gain_db"] is int):
		return "gain_db %s is not a number" % [cue["gain_db"]]
	var j = cue["pitch_jitter"]
	if not (j is float or j is int) or j < 0.0 or j > MAX_PITCH_JITTER:
		return "pitch_jitter %s is not within 0..%s" % [j, MAX_PITCH_JITTER]
	if not cue["anchor"] is String or (cue["anchor"] as String).is_empty():
		return "anchor %s is not a name" % [cue["anchor"]]
	var anchor: String = cue["anchor"]
	if anchor.begins_with(WORLD_PREFIX) and parse_world(anchor) == null:
		return "anchor '%s' is not world:<x>,<y>,<z>" % anchor
	return ""


## "world:x,y,z" -> Vector3 in the character's Blender local axes (Z up, facing -Y),
## or null if malformed. world_local() turns it into the Godot local frame.
static func parse_world(anchor: String):
	var parts := anchor.trim_prefix(WORLD_PREFIX).split(",")
	if parts.size() != 3:
		return null
	for p in parts:
		if not p.strip_edges().is_valid_float():
			return null
	return Vector3(parts[0].strip_edges().to_float(), parts[1].strip_edges().to_float(), parts[2].strip_edges().to_float())


## A "world:" anchor as a point in the character node's Godot local frame. The cue file
## writes it in Blender axes (Z up, facing -Y); Godot characters are Y up, facing +Z, so
## Blender (x, y, z) is Godot (x, z, -y).
static func world_local(anchor: String) -> Vector3:
	var b: Vector3 = parse_world(anchor)
	return Vector3(b.x, b.z, -b.y)


## Kind of a band member: its name without "band_" and the trailing "_<n>"
## (band_trombone_2 -> trombone, band_tenor_sax_1 -> tenor_sax).
static func kind_of(member: String) -> String:
	return member.trim_prefix("band_").rsplit("_", true, 1)[0]


## Terence's cues: {action: [cue]}.
func terence_table() -> Dictionary:
	return terence_cues


## A band member's cues, per action from the most specific table that has the action:
## the member's own, then its kind's, then "default". Tables replace, they do not merge.
func band_table(member: String) -> Dictionary:
	var out := {}
	for key in ["default", kind_of(member), member]:
		if band_cues.has(key):
			for action in band_cues[key]:
				out[action] = band_cues[key][action]
	return out


## Which band table each action of `member` came from: {action: table key}.
func band_sources(member: String) -> Dictionary:
	var out := {}
	for key in ["default", kind_of(member), member]:
		if band_cues.has(key):
			for action in band_cues[key]:
				out[action] = key
	return out


## Every band table key must be "default", a member in `members` or the kind of one, so a
## typo in sfx_cues.json does not quietly leave a player silent.
func check_band_keys(members: Array) -> bool:
	var known := {"default": true}
	for m in members:
		known[m] = true
		known[kind_of(m)] = true
	for key in band_cues:
		if not known.has(key):
			push_error("SfxLibrary: %s band table '%s' matches no band member or kind" % [CUES, key])
			return false
	return true
