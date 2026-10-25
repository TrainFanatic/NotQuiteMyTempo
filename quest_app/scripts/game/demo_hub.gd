class_name DemoHub
extends FletcherHub
## The judge demo: a FletcherHub that needs no laptop. It replays one real session recorded
## with the whole laptop stack (tools/judge_demo/record_demo.py, packed into res://demo/ by
## pack_demo.py) frame by frame on its recorded schedule, shifted to now, so the game, the
## drum chart, the HUD, the results and Fletcher (Terence's moves and barks) run exactly as
## they did live. A virtual drummer plays the recorded takes from the kit, the way the laptop's
## file mic fed them to the judge: take n from the n-th count_in, from just before its bar,
## stopped by the cutoff, the end of the song or idle. Then the whole session loops.
## Turned on by the export feature nqmt_demo or `-- --judge-demo`.

const FEATURE := "nqmt_demo"
const ARG := "--judge-demo"
const DIR := "res://demo/caravan/"
const LEAD_S := 0.5        # before the recording's first frame
const LOOP_GAP_S := 6.0    # after its last frame, before the session starts again
const PRE_ROLL_S := 0.25   # of a take before its count_in's bar (nqmt/file_mic.py)
const DRUM_BUS := &"Band"
const SongCache := preload("res://scripts/game/song_cache.gd")

## Where the virtual drummer's kit sounds from (Rehearsal points it at the drum throne).
var drum_anchor: Node3D

var frames: Array = []        # recorded envelopes, in time order
var takes: Array = []         # AudioStreamWAV per count_in
var take_origin := 0.0        # song time of a take's sample 0
var song: Dictionary = {}
var _offset := 0.0            # local seconds - recorded seconds, this pass
var _next := 0
var _running := false
var _loop_at := 0.0
var _count_ins := 0
var _drums: AudioStreamPlayer3D
var _take_at := INF           # local time the current take starts, INF = none pending
var _take_pos := 0.0


static func requested() -> bool:
	return OS.has_feature(FEATURE) or ARG in OS.get_cmdline_user_args()


func _ready() -> void:
	url = "demo"
	var meta = JSON.parse_string(FileAccess.get_file_as_string(DIR + "demo.json"))
	if not meta is Dictionary:
		push_error("DemoHub: no %sdemo.json (tools/judge_demo/pack_demo.py)" % DIR)
		return
	take_origin = float(meta["take_origin_s"])
	for k in int(meta["takes"]):
		var s = load(DIR + "take%d.wav" % k)
		if not s is AudioStreamWAV:
			push_error("DemoHub: %stake%d.wav did not load" % [DIR, k])
			return
		takes.append(s)
	var text := FileAccess.get_file_as_string(DIR + "session.jsonl")
	for line in text.split("\n", false):
		var env = JSON.parse_string(line)
		if not env is Dictionary or typeof(env.get("t")) not in [TYPE_FLOAT, TYPE_INT]:
			push_error("DemoHub: bad frame in session.jsonl: %s" % line.left(120))
			return
		frames.append(env)
	if frames.is_empty():
		push_error("DemoHub: %ssession.jsonl is empty" % DIR)
		return
	song = JSON.parse_string(FileAccess.get_file_as_string(DIR + "song/song.json"))
	if not song is Dictionary or not _seed_song_cache():
		return
	_drums = AudioStreamPlayer3D.new()
	_drums.name = "DemoDrums"
	_drums.bus = DRUM_BUS
	_drums.unit_size = 4.0
	add_child(_drums)
	print("[DemoHub] judge demo: %d frames, %d takes, song %s" % [frames.size(), takes.size(), song["id"]])


## The song's library files straight into the game's song cache, so nothing is downloaded.
func _seed_song_cache() -> bool:
	var dir := SongCache.song_dir(str(song["id"]))
	if DirAccess.make_dir_recursive_absolute(dir) != OK:
		push_error("DemoHub: cannot make %s" % dir)
		return false
	for f in ["song.json", "drums.drums.txt", "drums.tempo.json"]:
		var src := FileAccess.get_file_as_string(DIR + "song/" + f)
		if src == "":
			push_error("DemoHub: %ssong/%s is missing" % [DIR, f])
			return false
		var w := FileAccess.open(dir + f, FileAccess.WRITE)
		if w == null:
			push_error("DemoHub: cannot write %s%s" % [dir, f])
			return false
		w.store_string(src)
		w.close()
	return true


## Starts the replay (FletcherSetup.release, once the entrance is over).
func start() -> void:
	if frames.is_empty() or takes.is_empty() or drum_anchor == null:
		push_error("DemoHub: cannot start (frames %d, takes %d, drum anchor %s)" % [frames.size(), takes.size(), drum_anchor])
		return
	_drums.global_position = drum_anchor.global_position
	_begin_pass()
	_running = true
	_open = true
	connected.emit()


func _begin_pass() -> void:
	_offset = _now() + LEAD_S - float(frames[0]["t"])
	clock_offset = _offset
	_next = 0
	_count_ins = 0
	_loop_at = 0.0
	_stop_take()


func _process(_delta: float) -> void:
	if not _running:
		return
	var now := _now()
	while _next < frames.size() and float(frames[_next]["t"]) + _offset <= now:
		_emit(frames[_next])
		_next += 1
	if _next >= frames.size():
		if _loop_at == 0.0:
			_loop_at = now + LOOP_GAP_S
		elif now >= _loop_at:
			_begin_pass()
	if _take_at != INF and now >= _take_at:
		_drums.play(_take_pos + (now - _take_at))
		_take_at = INF


func _emit(env: Dictionary) -> void:
	var data: Dictionary = (env.get("data", {}) as Dictionary).duplicate(true)
	var type := str(env["type"])
	if type == "session.state" and str(env.get("src", "")) == "session":
		match str(data.get("state", "")):
			"count_in":
				_start_take(data)
			"finished", "idle":
				_stop_take()
	elif type == "session.command" and str(data.get("action", "")) == "stop":
		_stop_take()
	var copy := env.duplicate(true)
	message_received.emit(type, data, copy)


## Take n on the n-th count_in, heard so song time s lands at t0 + (s - song_time_at_t0).
func _start_take(d: Dictionary) -> void:
	var take: AudioStreamWAV = takes[mini(_count_ins, takes.size() - 1)]
	_count_ins += 1
	var t0 := to_local_time(float(d["t0"]))
	var st0 := float(d["song_time_at_t0"])
	_take_pos = maxf(0.0, st0 - PRE_ROLL_S - take_origin)
	_take_at = t0 + (_take_pos + take_origin - st0)
	_drums.stream = take


func _stop_take() -> void:
	_take_at = INF
	if _drums != null:
		_drums.stop()


func is_open() -> bool:
	return _running


## The game's own messages have nowhere to go: the recording already holds the answers.
func send(_type: String, _data: Dictionary) -> bool:
	return _running
