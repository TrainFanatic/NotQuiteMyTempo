extends Node
## Movie harness: Adam's rehearsal + FletcherHub/Voice/Link, replaying a recorded hub stream
## ({"rx", "msg"} jsonl) on the movie clock. User args (after --):
##   stream=<jsonl> log=<events jsonl> barks=<dir> cam=drummer|wide from=<rx> to=<rx>
##   lead=<s> countoff=<wav> bpm=<n> tail=<s>
## Logs every voice/Terence/sfx/link event with its movie time and frame.

var a := {}
var r
var terence
var hub: FletcherHub
var voice: FletcherVoice
var link: FletcherLink
var rows := []
var next_row := 0
var t_base := 0.0   # movie time = rx - from + lead
var end_t := 0.0
var logf: FileAccess
var hud: Label
var hud_move := ""
var hud_say := ""
var hud_sfx := ""
var sfx_seen = null
var co_player: AudioStreamPlayer3D
var co_at := INF
var playing_at := INF
var events_started := false
var track_cam: Camera3D
var track_at := Vector3.INF


func _ready() -> void:
	for s in OS.get_cmdline_user_args():
		var kv := s.split("=", true, 1)
		a[kv[0]] = kv[1] if kv.size() > 1 else "1"
	MovieClock.fps = float(a.get("fps", "30"))
	logf = FileAccess.open(a["log"], FileAccess.WRITE)
	r = load("res://scripts/rehearsal.gd").new()
	r.run_demo = false
	r.run_showcase = false
	r.intro_enabled = false
	r.cinematics_enabled = a.get("cinematics", "0") == "1"
	r.fletcher_enabled = true   # Adam's real wiring: scripts/fletcher/fletcher_setup.gd (Voice bus)
	r.fletcher_hub_url = "ws://127.0.0.1:1"   # nothing there: the harness emits the hub's messages
	add_child(r)
	terence = r.terence
	terence.move_started.connect(func(id): _ev("move_started", {"move": String(id)}); hud_move = String(id))
	terence.move_finished.connect(func(id): _ev("move_finished", {"move": String(id)}))
	hub = r.fletcher.hub
	voice = r.fletcher.voice
	link = r.fletcher.link
	var head: Node3D = r.fletcher.head
	if a.has("barks"):
		voice.barks_dir = a["barks"]
	if a.has("countoff_dir"):
		voice.countoff_dir = a["countoff_dir"]
	if a.has("duck_max"):   # "before" renders: duck_max=0 cutoff_lead=0.3
		voice.duck_max_db = float(a["duck_max"])
	if a.has("cutoff_lead"):
		voice.cutoff_lead_max_s = float(a["cutoff_lead"])
	voice.speaking.connect(func(u): _ev("speaking", {"uid": u}))
	voice.finished.connect(func(u): _ev("voice_finished", {"uid": u}))
	voice.subtitle.connect(func(t): hud_say = t)
	voice.countoff_word.connect(func(c): _ev("countoff_word", {"clip": c}); hud_say = c)
	voice.cue_due.connect(func(c): _ev("cue_due", {"gesture": c["gesture"], "uid": c["utterance_id"], "anchor": c["anchor"], "due_t": c["due_t"], "priority": c["priority"]}))
	link.move_requested.connect(func(m): _ev("move_requested", {"move": m}))
	link.move_dropped.connect(func(m, late): _ev("move_dropped", {"move": m, "late": late}))
	_stem(a.get("stem", ""))
	_camera()
	_hud()
	_load_stream()
	# the stream's laptop clock (rx, t0) -> movie time, as FletcherHub would estimate it
	hub.clock_offset = t_base
	if a.has("countoff"):
		co_player = AudioStreamPlayer3D.new()
		co_player.unit_size = voice.unit_size
		co_player.stream = AudioStreamWAV.load_from_file(a["countoff"])
		head.add_child(co_player)


## stem=voice: SFX bus muted. stem=sfx: the Voice bus still runs (a sidechain may listen to
## it) but sends to a muted sink bus instead of Master.
func _stem(which: String) -> void:
	if which == "voice":
		AudioServer.set_bus_mute(AudioServer.get_bus_index(&"SFX"), true)
	elif which == "sfx":
		AudioServer.add_bus(1)   # a bus can only send to one before it in the list
		var sink := 1
		AudioServer.set_bus_name(sink, &"Sink")
		AudioServer.set_bus_mute(sink, true)
		AudioServer.set_bus_send(AudioServer.get_bus_index(&"Voice"), &"Sink")
	if which != "":
		_ev("stem", {"stem": which})


func _camera() -> void:
	if a.get("cam", "drummer") == "drummer":
		return  # Adam's desktop camera at the drummer's seat (PlayerRig)
	var cam := Camera3D.new()
	if a["cam"] == "track":
		track_cam = cam
	cam.fov = 60.0
	add_child(cam)
	var eye := _vec(a.get("eye", "4.8,2.5,-3.6"))
	cam.look_at_from_position(eye, _vec(a.get("target", "0.4,0.9,-0.2")), Vector3.UP)
	cam.fov = float(a.get("fov", "70"))
	cam.current = true
	# where the drummer sits (the real kit is passthrough in the headset): a faint ring
	var ring := MeshInstance3D.new()
	var tm := TorusMesh.new()
	tm.inner_radius = 0.45
	tm.outer_radius = 0.5
	ring.mesh = tm
	var mat := StandardMaterial3D.new()
	mat.albedo_color = Color(0.3, 0.8, 1.0)
	mat.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	ring.material_override = mat
	add_child(ring)
	ring.global_position = r.marker_node("spot_drummer").global_position + Vector3.UP * 0.02


static func _vec(s: String) -> Vector3:
	var p := s.split(",")
	return Vector3(float(p[0]), float(p[1]), float(p[2]))


func _hud() -> void:
	var layer := CanvasLayer.new()
	add_child(layer)
	hud = Label.new()
	hud.position = Vector2(12, 8)
	hud.add_theme_font_size_override("font_size", 18)
	hud.add_theme_color_override("font_color", Color(1, 1, 0.8))
	hud.add_theme_color_override("font_outline_color", Color.BLACK)
	hud.add_theme_constant_override("outline_size", 6)
	layer.add_child(hud)


func _load_stream() -> void:
	var from := float(a.get("from", "-1e9"))
	var to := float(a.get("to", "1e9"))
	var lead := float(a.get("lead", "1.0"))
	for line in FileAccess.get_file_as_string(a["stream"]).split("\n", false):
		var row = JSON.parse_string(line)
		var ty: String = row["msg"]["type"]
		if ty.begins_with("judgment") or float(row["rx"]) < from or float(row["rx"]) > to:
			continue
		rows.append(row)
	t_base = lead - from if from > -1e8 else lead - float(rows[0]["rx"])
	var bpm := float(a.get("bpm", "94"))
	for row in rows:
		var d: Dictionary = row["msg"]["data"]
		if row["msg"]["type"] == "session.command" and d.get("action") == "restart":
			var restart := float(row["rx"]) + t_base
			co_at = restart - 60.0 / bpm  # the rendered count-off starts one beat before "five"
			playing_at = restart + 4 * 60.0 / bpm  # t0: the downbeat after "eight"
	end_t = float(rows[-1]["rx"]) + t_base + float(a.get("tail", "3.0"))
	if playing_at < INF:
		end_t = maxf(end_t, playing_at + float(a.get("tail", "3.0")))
	_ev("stream", {"rows": rows.size(), "t_base": t_base, "countoff_at": co_at, "t0": playing_at, "end": end_t})


func _ev(kind: String, data: Dictionary) -> void:
	data["ev"] = kind
	data["t"] = MovieClock.now()
	data["frame"] = Engine.get_process_frames()
	logf.store_line(JSON.stringify(data))


func _process(_delta: float) -> void:
	var now := MovieClock.now()
	while next_row < rows.size() and float(rows[next_row]["rx"]) + t_base <= now:
		var m: Dictionary = rows[next_row]["msg"]
		next_row += 1
		var d: Dictionary = m["data"]
		var ev := {"type": m["type"]}
		for k in d:
			if k != "pcm_b64":
				ev[k] = d[k]
		if m["type"] == "fletcher.audio":
			ev["bytes"] = Marshalls.base64_to_raw(str(d.get("pcm_b64", ""))).size()
		_ev("msg", ev)
		hub.message_received.emit(m["type"], d, {"type": m["type"], "t": now})
	if now >= co_at and co_player:
		co_at = INF
		co_player.play()
		_ev("countoff_audio", {})
	if now >= playing_at:
		playing_at = INF
		var st := {"state": "playing", "bpm": float(a.get("bpm", "94")), "bar": 1, "beat": 1}
		_ev("msg", {"type": "session.state", "synth": true, "state": "playing"})
		hub.message_received.emit("session.state", st, {"type": "session.state", "t": now})
	_poll_sfx()
	if track_cam:
		# side camera that keeps Terence (chest height) in frame, smoothed so it doesn't jitter
		var want: Vector3 = terence.global_position + Vector3.UP * float(a.get("track_h", "1.1"))
		track_at = want if track_at == Vector3.INF else track_at.lerp(want, 0.15)
		track_cam.look_at(track_at, Vector3.UP)
	hud.text = "t %6.2f s  frame %d\nmove: %s%s\nvoice: %s\nsfx: %s" % [now, Engine.get_process_frames(), hud_move,
		"  (walking)" if terence.is_travelling() else "", hud_say if voice.is_speaking() or hud_say.begins_with("co_") else "", hud_sfx]
	if now >= end_t:
		logf.close()
		get_tree().quit()


func _poll_sfx() -> void:
	var sc = terence.get_node_or_null("SfxCues")
	if sc == null:
		return
	var f: Array = sc.fired
	var start := 0
	if sfx_seen != null:
		start = f.size()
		for i in range(f.size() - 1, -1, -1):
			if is_same(f[i], sfx_seen):
				start = i + 1
				break
	for i in range(start, f.size()):
		_ev("sfx", {"anim": String(f[i]["anim"]), "sfx": f[i]["sfx"], "cue_time": f[i]["time"]})
		hud_sfx = "%s (%s)" % [f[i]["sfx"], f[i]["anim"]]
	if f.size():
		sfx_seen = f[-1]
