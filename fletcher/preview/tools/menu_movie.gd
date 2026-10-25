extends Node
## Movie harness for the game menu (quest_app/scripts/game): Adam's rehearsal in game mode,
## a scripted gaze picking a song and PLAY, then the laptop's side played in by hand on the
## movie clock: count_in, Fletcher's count-off, the bridge's playing/notes, a cutoff, the
## result. Run from a quest_app copy whose game and Fletcher scripts tell time with
## MovieClock (render_menu.sh does all that). User args (after --):
##   song=<id> (default bond_demo)   songs=<repo songs dir>   intro=1 (Terence's entrance first)
## The camera turns with the gaze, as the head would in the headset; a caption names each step.

const GameSession := preload("res://scripts/game/game_session.gd")
const SongCache := preload("res://scripts/game/song_cache.gd")

var a := {}
var r
var game
var cam: Camera3D
var eye: Vector3
var look := Vector3.ZERO          # current gaze direction
var from_dir := Vector3.ZERO
var to_target = null              # String item id, "terence", or null (hold)
var move_t0 := 0.0
var move_s := 0.8
var caption: Label
var events := []                  # [[movie t, Callable]]
var t0 := INF
var bpm := 120.0
var bar_s := 2.0
var song := {}
var phase := "start"


func _ready() -> void:
	for s in OS.get_cmdline_user_args():
		var kv := s.split("=", true, 1)
		a[kv[0]] = kv[1] if kv.size() > 1 else "1"
	MovieClock.fps = 30.0
	GameSession.settings_path = "user://movie_game_settings.cfg"
	SongCache.root = "user://movie_songs/"
	var songs := _songs(a.get("songs", ""))
	var id: String = a.get("song", "bond_demo")
	for s in songs:
		if s["id"] == id:
			song = s
	if song.is_empty():
		push_error("menu_movie: no song %s in %s" % [id, a.get("songs", "")])
		get_tree().quit(1)
		return
	_cache(song, a["songs"].path_join(id))
	bpm = float(song.get("bpm", 120))
	bar_s = float(song.get("beats_per_bar", 4)) * 60.0 / bpm
	r = load("res://scripts/rehearsal.gd").new()
	r.game_mode = true
	r.intro_enabled = a.get("intro", "0") == "1"
	r.cinematics_enabled = false
	r.fletcher_hub_url = "ws://127.0.0.1:1"   # nothing there: this harness plays the laptop
	add_child(r)
	game = r.game
	game.hub.clock_offset = 0.0              # the "laptop clock" here is the movie clock
	game.hub.process_mode = Node.PROCESS_MODE_DISABLED
	game.hub._open = true                    # show "hub" connected; this harness sends nothing
	game.menu.chosen.disconnect(game._on_chosen)
	game.menu.chosen.connect(_on_chosen)
	_feed("library.songs", {"songs": songs, "base_url": "http://127.0.0.1:8766/songs/"})
	cam = r.rig.camera
	eye = cam.global_position
	look = -cam.global_transform.basis.z
	_caption()
	if r.intro_enabled:
		r.intro_finished.connect(func(): _start_script(0.5), CONNECT_ONE_SHOT)
		_say("Terence's entrance (scripts/intro.gd)")
	else:
		_start_script(0.0)


func _songs(dir: String) -> Array:
	var out := []
	var d := DirAccess.open(dir)
	if d == null:
		return out
	for sub in d.get_directories():
		var path := dir.path_join(sub).path_join("song.json")
		if not FileAccess.file_exists(path):
			continue
		var j = JSON.parse_string(FileAccess.get_file_as_string(path))
		if j is Dictionary and j.has("id"):
			out.append(j)
	return out


## The song as a download would have left it in the cache (user://movie_songs/<id>/).
func _cache(s: Dictionary, src: String) -> void:
	var dst := SongCache.song_dir(s["id"])
	DirAccess.make_dir_recursive_absolute(dst)
	for f in SongCache.files_for(s):
		DirAccess.copy_absolute(src.path_join(f), dst + f)
	var meta := s.duplicate()
	meta["_missing"] = []
	var fa := FileAccess.open(dst + "song.json", FileAccess.WRITE)
	fa.store_string(JSON.stringify(meta))
	fa.close()


func _caption() -> void:
	var layer := CanvasLayer.new()
	add_child(layer)
	caption = Label.new()
	caption.position = Vector2(16, 676)
	caption.add_theme_font_size_override("font_size", 20)
	caption.add_theme_color_override("font_color", Color(1, 1, 0.85))
	caption.add_theme_color_override("font_outline_color", Color.BLACK)
	caption.add_theme_constant_override("outline_size", 6)
	layer.add_child(caption)


func _say(text: String) -> void:
	caption.text = text
	print("[menu_movie] %.2f %s" % [MovieClock.now(), text])


func _feed(type: String, data: Dictionary) -> void:
	game.hub.message_received.emit(type, data, {"type": type, "src": "movie", "t": MovieClock.now(), "data": data})


func _at(dt: float, f: Callable) -> void:
	events.append([MovieClock.now() + dt, f])


func _gaze(target, seconds := 0.8) -> void:
	from_dir = look
	to_target = target
	move_t0 = MovieClock.now()
	move_s = seconds


func _start_script(delay: float) -> void:
	_say("The menu: a sheet on a music stand in front of the throne")
	_gaze("terence", 0.01)
	_at(delay + 1.4, func():
		_say("Gaze at a song: the red ring fills, 1.5 s picks it (a pinch picks at once)")
		_gaze("song:" + song["id"], 0.9))
	phase = "pick_song"


func _on_chosen(id: String, arg) -> void:
	if id == "select" and phase == "pick_song":
		phase = "pick_play"
		_at(0.5, func():
			_say("Then PLAY. Everything here can also be done from the laptop console")
			_gaze("play", 0.7))
	elif id == "play":
		phase = "waiting"
		game.menu.show_waiting(str(song.get("title", song["id"])))
		_say("PLAY sent session.command start; the laptop answers with count_in")
		_at(0.7, _count_in)
	else:
		game._on_chosen(id, arg)


## What hub/session.py and Fletcher do on a start: count_in with t0, then his count-off cue.
func _count_in() -> void:
	var now := MovieClock.now()
	t0 = now + 5.0 * 60.0 / bpm + 2.5
	_feed("session.state", {"state": "count_in", "song_id": song["id"], "title": song.get("title", ""),
		"bars": song.get("bars", 0), "beats_per_bar": song.get("beats_per_bar", 4), "bpm": bpm, "bar": 1,
		"beat": 1, "from_bar": 1, "t0": t0, "song_time_at_t0": 0.0, "reason": "start"})
	var clips := ["co_five_sharp", "co_six_sharp", "co_seven_sharp", "co_eight_sharp"]
	_feed("fletcher.state", {"mood": "calm", "anger": 0.0, "action": "idle", "look_at": "drummer",
		"gesture": "t0_countoff_finger", "utterance_id": "u-0000", "priority": 2, "t0": t0, "bpm": bpm, "clips": clips})
	_say("count_in: the menu goes, the backing is scheduled for t0, Terence counts you in")
	_at(0.4, func(): _gaze("terence", 1.0))
	events.append([t0 - 0.6 * 120.0 / bpm - 0.05, func(): _say("The band raises before the downbeat (phrases from arrangement.mid)")])
	events.append([t0 + 0.2, _first_hit])
	var cut := t0 + 5.0 * bar_s + 0.1
	for b in range(1, 6):
		for beat in int(song.get("beats_per_bar", 4)):
			var st := (b - 1) * bar_s + beat * 60.0 / bpm
			var err := -4.0 - 9.0 * (b - 1) - 2.0 * beat   # creeping ahead: rushing
			events.append([t0 + st + err / 1000.0 + 0.03, _note.bind(b, beat + 1.0, err, st)])
		if b > 1:
			events.append([t0 + (b - 1) * bar_s + 0.03, _bar.bind(b)])
	events.append([t0 + 3.6 * bar_s, func():
		_say("HUD: the bar, Fletcher's line, and the referee's timing (rushing)")
		_feed("fletcher.state", {"mood": "calm", "anger": 0.2, "action": "point", "look_at": "drummer",
			"gesture": "t2_point_player", "utterance_id": "u-0001", "priority": 1, "at_s": -0.2})
		_feed("fletcher.speech", {"utterance_id": "u-0001", "text": "Rushing.", "clip_id": "rushing_1", "sample_rate": 24000})])
	events.append([cut, func():
		_feed("fletcher.state", {"mood": "furious", "anger": 1.0, "action": "fist_cutoff", "look_at": "drummer",
			"gesture": "t1_fist_cutoff", "utterance_id": "u-0002", "at_s": -0.2, "priority": 2})
		_feed("session.command", {"action": "stop", "from_bar": 5})
		_feed("fletcher.speech", {"utterance_id": "u-0002", "text": "Stop!", "clip_id": "stop_1", "sample_rate": 24000})
		_feed("session.state", {"state": "stopped", "song_id": song["id"], "bpm": bpm, "bar": 5, "beat": 1})
		_say("The fist: session stopped, the backing stops with the band")])
	events.append([cut + 3.0, func():
		_say("Quit from the console (or BACK TO MENU): session.result, then idle")
		_feed("session.result", {"song_id": song["id"], "title": song.get("title", ""), "completed": false,
			"bars_played": 5, "cutoffs": 1, "notes": 20, "perfect": 6, "good": 8, "off": 5, "miss": 1, "extra": 2,
			"mean_error_ms": -21.4, "rushing_bars": [3, 4, 5], "dragging_bars": []})
		_feed("session.state", {"state": "idle"})
		_gaze("results", 1.0)])
	events.append([cut + 8.0, func(): get_tree().quit()])


func _first_hit() -> void:
	_feed("session.state", {"state": "playing", "song_id": song["id"], "bpm": bpm, "bar": 1, "beat": 1})
	_feed("fletcher.state", {"mood": "calm", "anger": 0.0, "action": "conduct", "look_at": "drummer"})
	_say("t0: backing.ogg from sample 0, the band in, Terence conducts")


func _bar(b: int) -> void:
	_feed("session.state", {"state": "playing", "song_id": song["id"], "bpm": bpm, "bar": b, "beat": 1})


func _note(b: int, beat: float, err: float, st: float) -> void:
	_feed("judgment.note", {"drum": "snare", "bar": b, "beat": beat, "error_ms": err,
		"grade": "perfect" if absf(err) <= 15 else "good" if absf(err) <= 35 else "off", "song_time": st})


func _target_point(t) -> Vector3:
	if t == "terence":
		return r.terence.global_position + Vector3.UP * 1.55
	if t == "results":
		return game.menu.panel.global_position
	return game.menu.panel.item_world(t) if game.menu.panel.has_item(t) else game.menu.panel.global_position


func _process(_delta: float) -> void:
	var now := MovieClock.now()
	var due := events.filter(func(e): return now >= e[0])
	events = events.filter(func(e): return now < e[0])
	for e in due:
		e[1].call()
	if to_target != null:
		var want := (_target_point(to_target) - eye).normalized()
		var f := clampf((now - move_t0) / maxf(move_s, 0.001), 0.0, 1.0)
		f = f * f * (3.0 - 2.0 * f)
		look = from_dir.slerp(want, f) if from_dir != Vector3.ZERO else want
	cam.look_at(eye + look, Vector3.UP)
	game.input.scripted_ray = [eye, look]
