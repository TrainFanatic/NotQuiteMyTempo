extends RefCounted
## The cinematic shot library: which Terence moves cut to a close-up, and the shots each one
## gets. Data only, plus the code that resolves and checks it against the moves metadata.
## Loaded with preload() by its users, so no global class cache entry is needed.
##
## SHOTS[move] = {"enabled": bool, "shots": [shot, ...]}. Set "enabled" to false to opt a
## move out. Each shot is a dictionary:
##   framing  one of FRAMINGS (see shot_framer.gd for what each one frames)
##   camera   "static", "push_in" (slow dolly in over the shot) or "snap_zoom" (a crash zoom
##            from wide to the shot's lens over ShotFramer.SNAP_FRAMES, at the "accent" frame)
##   from/to  when the shot is on screen, in authored animation frames (24 fps) from the start
##            of the move. Either an int, or a frame spec naming a key timing of the move in
##            terence_v004_moves.json, so the cuts follow the animation when it is re-authored:
##              "start" (0), "end" (the move's frame count), "<key>" (an int key such as
##              "cutoff"), "<key>[i]" (element i of a list key such as "impacts"), each with
##              an optional "+N" or "-N" frame offset, e.g. "impacts[2]-3", "end+12".
##            Shots are back to back: each "from" is the previous shot's "to" (hard cuts). A
##            one-shot move may run past its end into the idle stare that follows it (a held
##            beat after the gesture); a looping move may run into its next pass.
##   fov      vertical field of view of the 2.39:1 frame, degrees
##   accent   snap_zoom only: the frame spec where the zoom snaps in
## Shots are timed in animation time, so they stretch and shrink with the tempo like the moves.

const FPS := 24.0
const FRAMINGS := [&"extreme_close_face", &"close_face", &"fist_insert", &"low_angle_medium", &"over_shoulder"]
const CAMERA_MOVES := [&"static", &"push_in", &"snap_zoom"]
const SHOT_KEYS := ["framing", "camera", "from", "to", "fov"]
## A shot must hold at least this long and at most this long at the authored rate (seconds).
const MIN_HOLD := 0.5
const MAX_HOLD := 2.5

const SHOTS := {
	# --- tier 1: the cutoffs. Each lands on its gesture frame, then holds on his face. ---
	&"t1_flick": {"enabled": true, "shots": [
		{"framing": &"close_face", "camera": &"snap_zoom", "accent": "flick-2", "from": "start", "to": "end+10", "fov": 24.0},
	]},
	&"t1_wave_off": {"enabled": true, "shots": [
		{"framing": &"low_angle_medium", "camera": &"push_in", "from": "start", "to": "wave+4", "fov": 34.0},
		{"framing": &"extreme_close_face", "camera": &"static", "from": "wave+4", "to": "end+10", "fov": 20.0},
	]},
	&"t1_fist_cutoff": {"enabled": true, "shots": [
		# the insert rides the wind-up, snaps on the slam and holds the frozen fist
		{"framing": &"fist_insert", "camera": &"snap_zoom", "accent": "cutoff-2", "from": "start", "to": "hold_start+10", "fov": 26.0},
		{"framing": &"close_face", "camera": &"push_in", "from": "hold_start+10", "to": "end+6", "fov": 24.0},
	]},
	&"t1_clap_stop": {"enabled": true, "shots": [
		{"framing": &"low_angle_medium", "camera": &"static", "from": "start", "to": "clap+6", "fov": 34.0},
		{"framing": &"extreme_close_face", "camera": &"snap_zoom", "accent": "clap+6", "from": "clap+6", "to": "end+12", "fov": 20.0},
	]},
	&"t1_fist_slam": {"enabled": true, "shots": [
		{"framing": &"low_angle_medium", "camera": &"snap_zoom", "accent": "slam-2", "from": "start", "to": "slam+4", "fov": 34.0},
		{"framing": &"close_face", "camera": &"push_in", "from": "slam+4", "to": "end+12", "fov": 24.0},
	]},
	# --- tier 2 ---
	&"t2_judging_stare": {"enabled": true, "shots": [
		{"framing": &"close_face", "camera": &"push_in", "from": "start", "to": 48, "fov": 24.0},
		{"framing": &"extreme_close_face", "camera": &"static", "from": 48, "to": "end", "fov": 18.0},
	]},
	&"t2_point_player": {"enabled": true, "shots": [
		{"framing": &"over_shoulder", "camera": &"snap_zoom", "accent": "point", "from": "start", "to": "point+10", "fov": 9.0},
		{"framing": &"extreme_close_face", "camera": &"static", "from": "point+10", "to": "end+6", "fov": 20.0},
	]},
	# --- tier 3: in your face. He stands 0.7 m away, so these end with the move, and no shot
	# here pulls back far enough to put the camera behind the player's throne. ---
	&"t3_loom": {"enabled": true, "shots": [
		{"framing": &"close_face", "camera": &"push_in", "from": "start", "to": 40, "fov": 30.0},
		{"framing": &"extreme_close_face", "camera": &"static", "from": 40, "to": "end", "fov": 20.0},
	]},
	&"t3_whisper": {"enabled": true, "shots": [
		{"framing": &"close_face", "camera": &"push_in", "from": "start", "to": "close", "fov": 26.0},
		{"framing": &"extreme_close_face", "camera": &"push_in", "from": "close", "to": "end", "fov": 18.0},
	]},
	# --- tier 4 ---
	&"t4_shout": {"enabled": true, "shots": [
		{"framing": &"close_face", "camera": &"snap_zoom", "accent": "accents[0]-1", "from": "start", "to": "accents[0]+12", "fov": 24.0},
		{"framing": &"extreme_close_face", "camera": &"snap_zoom", "accent": "accents[1]-1", "from": "accents[0]+12", "to": "end+6", "fov": 18.0},
	]},
	&"t4_charge": {"enabled": true, "shots": [
		{"framing": &"low_angle_medium", "camera": &"static", "from": "start", "to": "stomps[0]+3", "fov": 36.0},
		{"framing": &"close_face", "camera": &"snap_zoom", "accent": "stop-2", "from": "stomps[0]+3", "to": "end+14", "fov": 24.0},
	]},
	# --- tier 5 ---
	&"t5_rhythm_slaps": {"enabled": true, "shots": [
		{"framing": &"close_face", "camera": &"static", "from": "start", "to": "impacts[0]", "fov": 26.0},
		{"framing": &"over_shoulder", "camera": &"static", "from": "impacts[0]", "to": "impacts[1]", "fov": 50.0},
		{"framing": &"extreme_close_face", "camera": &"static", "from": "impacts[1]", "to": "impacts[2]", "fov": 20.0},
		{"framing": &"close_face", "camera": &"snap_zoom", "accent": "impacts[3]-1", "from": "impacts[2]", "to": "end", "fov": 24.0},
	]},
	&"t5_chair_throw": {"enabled": true, "shots": [
		{"framing": &"low_angle_medium", "camera": &"static", "from": "start", "to": "release+4", "fov": 40.0},
		{"framing": &"extreme_close_face", "camera": &"push_in", "from": "release+4", "to": "end+6", "fov": 20.0},
	]},
	# --- approval: the payoff ---
	&"ap_grin": {"enabled": true, "shots": [
		{"framing": &"close_face", "camera": &"push_in", "from": "start", "to": "end+12", "fov": 22.0},
	]},
}


## Resolves a frame spec (see the header) against one move's metadata. Reports and returns
## -1 for anything it cannot resolve; frames are never guessed.
static func resolve_frame(spec, move_meta: Dictionary, move: StringName) -> int:
	if spec is int:
		return spec
	if not spec is String:
		push_error("ShotTable: %s frame spec %s is neither an int nor a String" % [move, spec])
		return -1
	var re := RegEx.create_from_string("^([a-z_]+)(?:\\[(\\d+)\\])?([+-]\\d+)?$")
	var m := re.search(spec)
	if m == null:
		push_error("ShotTable: %s frame spec '%s' is malformed (want e.g. 'cutoff+5', 'impacts[1]', 'end')" % [move, spec])
		return -1
	var key := m.get_string(1)
	var offset := int(m.get_string(3)) if m.get_string(3) != "" else 0
	var base := 0
	if key == "start":
		base = 0
	elif key == "end":
		base = int(move_meta["frames"])
	else:
		if not move_meta.has(key):
			push_error("ShotTable: %s has no key timing '%s' in the moves metadata (has %s)" % [move, key, move_meta.keys()])
			return -1
		var v = move_meta[key]
		if m.get_string(2) != "":
			if not v is Array:
				push_error("ShotTable: %s '%s' is not a list, so '%s' cannot index it" % [move, key, spec])
				return -1
			var i := int(m.get_string(2))
			if i >= v.size():
				push_error("ShotTable: %s '%s' has %d entries, '%s' is out of range" % [move, key, v.size(), spec])
				return -1
			v = v[i]
		if not (v is int or v is float):
			push_error("ShotTable: %s '%s' is not a frame number (%s)" % [move, spec, v])
			return -1
		base = int(v)
	return base + offset


## The shots of `move` with every frame resolved: [{framing, camera, from, to, fov, accent}],
## frames as ints. `meta` is Terence's moves metadata. Returns [] and reports on any problem.
static func resolve(table: Dictionary, move: StringName, meta: Dictionary) -> Array:
	if not table.has(move):
		push_error("ShotTable: no shots for %s" % move)
		return []
	var entry: Dictionary = table[move]
	for key in ["enabled", "shots"]:
		if not entry.has(key):
			push_error("ShotTable: %s lacks '%s'" % [move, key])
			return []
	if not meta["moves"].has(String(move)):
		push_error("ShotTable: %s is not a move in the moves metadata" % move)
		return []
	var mm: Dictionary = meta["moves"][String(move)]
	var out: Array = []
	for i in entry["shots"].size():
		var s: Dictionary = entry["shots"][i]
		for key in SHOT_KEYS:
			if not s.has(key):
				push_error("ShotTable: %s shot %d lacks '%s'" % [move, i, key])
				return []
		if not StringName(s["framing"]) in FRAMINGS:
			push_error("ShotTable: %s shot %d has unknown framing %s (have %s)" % [move, i, s["framing"], FRAMINGS])
			return []
		if not StringName(s["camera"]) in CAMERA_MOVES:
			push_error("ShotTable: %s shot %d has unknown camera move %s (have %s)" % [move, i, s["camera"], CAMERA_MOVES])
			return []
		var snap: bool = StringName(s["camera"]) == &"snap_zoom"
		if snap != s.has("accent"):
			push_error("ShotTable: %s shot %d: 'accent' belongs to snap_zoom shots, and every snap_zoom needs one" % [move, i])
			return []
		var r := {"framing": StringName(s["framing"]), "camera": StringName(s["camera"]),
			"from": resolve_frame(s["from"], mm, move), "to": resolve_frame(s["to"], mm, move),
			"fov": float(s["fov"]), "accent": resolve_frame(s["accent"], mm, move) if snap else -1}
		if r["from"] < 0 or r["to"] < 0 or (snap and r["accent"] < 0):
			return []
		if r["to"] <= r["from"]:
			push_error("ShotTable: %s shot %d ends (frame %d) before it starts (frame %d)" % [move, i, r["to"], r["from"]])
			return []
		if snap and (r["accent"] < r["from"] or r["accent"] >= r["to"]):
			push_error("ShotTable: %s shot %d snaps at frame %d, outside its frames %d..%d" % [move, i, r["accent"], r["from"], r["to"]])
			return []
		if r["fov"] <= 1.0 or r["fov"] >= 120.0:
			push_error("ShotTable: %s shot %d fov %.1f is not a lens" % [move, i, r["fov"]])
			return []
		if i == 0 and r["from"] != 0:
			push_error("ShotTable: %s's first shot must start at frame 0 (the cut in), not %d" % [move, r["from"]])
			return []
		if i > 0 and r["from"] != out[i - 1]["to"]:
			push_error("ShotTable: %s shot %d starts at frame %d, but shot %d ends at %d (shots are back to back)" % [move, i, r["from"], i - 1, out[i - 1]["to"]])
			return []
		out.append(r)
	if out.is_empty():
		push_error("ShotTable: %s has no shots (set enabled to false to opt it out)" % move)
	return out


## Checks every move in the table resolves, and that close-up one-shots (played away from his
## spot, so he walks home after) end with the move: the idle stare never follows them there.
static func check(table: Dictionary, meta: Dictionary, close_moves: Array) -> bool:
	for move in table:
		var shots := resolve(table, move, meta)
		if shots.is_empty():
			return false
		var mm: Dictionary = meta["moves"][String(move)]
		if move in close_moves and not mm["loop"] and shots[-1]["to"] > int(mm["frames"]):
			push_error("ShotTable: %s is a close-up one-shot (he walks home after it), so its shots must end by frame %d, not %d" % [move, mm["frames"], shots[-1]["to"]])
			return false
	return true
