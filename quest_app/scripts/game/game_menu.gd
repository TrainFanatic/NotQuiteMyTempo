extends Node3D
## The in-headset menu: a sheet of manuscript paper on a black music stand in front of the
## drummer's throne, just below eye level so Terence's stare still shows over it. Built from
## a GamePanel (scripts/game/game_panel.gd). Pages:
##   main      the song list (library.songs), PLAY, DEMO SCENES, SETTINGS, the hub status
##   demo      Adam's showcase (every move) or the scripted rehearsal demo
##   settings  the hub URL (a keypad page edits the laptop's IP) and the mic source
##   waiting   PLAY was picked; waiting for the laptop's count_in
##   results   session.result
##   message   a line of text and OK
## The menu only shows things and reports picks (`chosen`); GameSession decides what they
## do, and the laptop console can do all of it without the menu (the menu follows
## session.state). Loaded with preload() by its users.

signal chosen(id: String, arg: Variant)

const GamePanel := preload("res://scripts/game/game_panel.gd")
const ROWS := 5                          # songs per page of the list
const STAND_COLOR := Color(0.07, 0.07, 0.08)
## Where the stand goes, in the drummer's frame (x right, f forward toward Terence, metres
## from the floor under the throne): a little right of centre so the kit stays in view.
const PLACE := {"x": 0.18, "f": 0.85, "y": 0.92}

var panel: GamePanel
var page := ""
var songs: Array = []                    # song.json dictionaries from library.songs
var selected := ""                       # song id
var scroll := 0
var connected := false
var hub_url := ""
var mic_source := "laptop"
var latency_ms := 0
var result := {}
var _keypad := ""
var _message := ""
var _waiting_title := ""


func build() -> void:
	name = "GameMenu"
	panel = GamePanel.new()
	panel.name = "Panel"
	add_child(panel)
	panel.build()
	panel.activated.connect(_on_activated)
	_build_stand()
	show_page("main")


## Stand the menu in front of the drummer, the paper turned toward the seated eye.
func place(drummer: Transform3D, eye_height: float) -> void:
	var right := drummer.basis.x.normalized()
	var fwd := -drummer.basis.z.normalized()
	var centre := drummer.origin + right * PLACE.x + fwd * PLACE.f + Vector3.UP * PLACE.y
	var eye := drummer.origin + Vector3.UP * eye_height
	global_transform = Transform3D(Basis(), centre)
	var z := (eye - centre).normalized()
	var x := Vector3.UP.cross(z).normalized()
	panel.transform = Transform3D(Basis(x, z.cross(x), z), Vector3.ZERO)
	# the pole stays plumb under the paper's lower edge, from the floor
	var floor_y := -(centre.y - drummer.origin.y)
	var bottom := panel.transform * Vector3(0, -panel.size_m.y * 0.5, 0)
	var pole_h := bottom.y - floor_y - 0.02
	var pole := get_node("Stand/Pole") as MeshInstance3D
	(pole.mesh as CylinderMesh).height = pole_h
	pole.position = Vector3(bottom.x, floor_y + pole_h * 0.5, bottom.z)
	(get_node("Stand/Base") as Node3D).position = Vector3(bottom.x, floor_y, bottom.z)
	(get_node("Stand/Lip") as Node3D).transform = panel.transform * Transform3D(Basis(), Vector3(0, -panel.size_m.y * 0.5 - 0.0125, 0.02))


func _build_stand() -> void:
	var mat := StandardMaterial3D.new()
	mat.albedo_color = STAND_COLOR
	mat.metallic = 0.5
	mat.roughness = 0.45
	var stand := Node3D.new()
	stand.name = "Stand"
	add_child(stand)
	var pole := MeshInstance3D.new()
	pole.name = "Pole"
	var cm := CylinderMesh.new()
	cm.top_radius = 0.011
	cm.bottom_radius = 0.011
	cm.radial_segments = 6
	cm.height = 0.8
	pole.mesh = cm
	pole.material_override = mat
	stand.add_child(pole)
	var base := Node3D.new()
	base.name = "Base"
	stand.add_child(base)
	for i in 3:   # three splayed feet, PS2-cheap boxes
		var leg := MeshInstance3D.new()
		var bm := BoxMesh.new()
		bm.size = Vector3(0.018, 0.012, 0.32)
		leg.mesh = bm
		leg.material_override = mat
		leg.rotation = Vector3(0.12, TAU * i / 3.0, 0)
		leg.position = Basis(Vector3.UP, TAU * i / 3.0) * Vector3(0, 0.02, 0.15)
		base.add_child(leg)
	var lip := MeshInstance3D.new()
	lip.name = "Lip"
	var lm := BoxMesh.new()
	lm.size = Vector3(panel.size_m.x + 0.04, 0.025, 0.04)
	lip.mesh = lm
	lip.material_override = mat
	stand.add_child(lip)


## ---- state from GameSession ---------------------------------------------

func set_songs(list: Array) -> void:
	var fresh := list.filter(func(s): return s is Dictionary and s.has("id"))
	if JSON.stringify(fresh) == JSON.stringify(songs):
		return   # the library repeats itself: don't reset a dwell in progress
	songs = fresh
	if not songs.any(func(s): return str(s["id"]) == selected):
		selected = str(songs[0]["id"]) if songs.size() == 1 else ""
	scroll = clampi(scroll, 0, maxi(0, songs.size() - 1))
	if page == "main":
		show_page("main")


func set_status(is_connected: bool, url: String) -> void:
	if is_connected == connected and url == hub_url:
		return
	connected = is_connected
	hub_url = url
	if page in ["main", "settings"]:
		show_page(page)


func song(id: String) -> Dictionary:
	for s in songs:
		if str(s["id"]) == id:
			return s
	return {}


func show_message(text: String) -> void:
	_message = text
	show_page("message")


func show_waiting(title: String) -> void:
	_waiting_title = title
	show_page("waiting")


func show_results(r: Dictionary) -> void:
	result = r
	show_page("results")


## ---- pages ---------------------------------------------------------------

func show_page(p: String) -> void:
	page = p
	panel.clear()
	_header()
	match p:
		"main":
			_main()
		"demo":
			panel.label("Demo scenes", 22, GamePanel.INK)
			panel.add_button("SHOWCASE  (every move, in turn)", "demo:showcase", "showcase", null, true)
			panel.add_button("REHEARSAL  (the scripted demo)", "demo:timeline", "timeline", null, true)
			panel.label("Look at \"Back to menu\" to leave a demo.", 16, GamePanel.INK_SOFT)
			panel.add_button("BACK", "back")
		"settings":
			_settings()
		"keypad":
			_keypad_page()
		"waiting":
			panel.label(_waiting_title, 24, GamePanel.INK)
			panel.label("Waiting for the band...", 20, GamePanel.INK_SOFT)
			panel.label("Terence counts you in: 5, 6, 7, 8.", 18, GamePanel.INK_SOFT)
			panel.add_button("CANCEL", "quit")
		"results":
			_results()
		"message":
			var l := panel.label(_message, 20, GamePanel.INK)
			l.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
			l.custom_minimum_size = Vector2(panel.pixels.x - 40, 0)
			panel.add_button("OK", "back")


func _header() -> void:
	var h := panel.row()
	var t := panel.label("NOT QUITE MY TEMPO", 22, GamePanel.RED, h)
	t.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	t.clip_text = true
	panel.label("o hub" if connected else "x no hub", 16, GamePanel.INK if connected else GamePanel.RED, h)
	var staff := Control.new()   # five ruled lines under the title: manuscript paper
	staff.custom_minimum_size = Vector2(0, 14)
	staff.draw.connect(func():
		for i in 5:
			staff.draw_line(Vector2(0, 1 + i * 3), Vector2(staff.size.x, 1 + i * 3), GamePanel.INK_SOFT, 1.0))
	panel.content.add_child(staff)


func _main() -> void:
	if songs.is_empty():
		var l := panel.label("No songs yet." if connected else "Not connected to the laptop.", 20, GamePanel.INK)
		l.custom_minimum_size = Vector2(0, 30)
		panel.label("Drop a MIDI file in songs/inbox on the laptop." if connected
			else "Start  python -m nqmt  there, or set the hub in Settings.", 16, GamePanel.INK_SOFT)
	else:
		var rows := ROWS if songs.size() <= ROWS else ROWS - 1
		scroll = clampi(scroll, 0, maxi(0, songs.size() - rows))
		for i in range(scroll, mini(songs.size(), scroll + rows)):
			var s: Dictionary = songs[i]
			var id := str(s["id"])
			panel.add_button(_song_line(s), "song:" + id, id, null, true, id == selected)
		if songs.size() > rows:
			var nav := panel.row()
			if scroll > 0:
				panel.add_button("< prev", "scroll", -rows, nav)
			if scroll + rows < songs.size():
				panel.add_button("next >", "scroll", rows, nav)
			panel.label("  %d-%d of %d" % [scroll + 1, mini(songs.size(), scroll + rows), songs.size()], 16, GamePanel.INK_SOFT, nav)
	var spacer := Control.new()
	spacer.size_flags_vertical = Control.SIZE_EXPAND_FILL
	panel.content.add_child(spacer)
	var b := panel.row()
	if selected != "":
		panel.add_button("PLAY", "play", selected, b)
	panel.add_button("DEMO SCENES", "demos", null, b)
	panel.add_button("SETTINGS", "settings", null, b)


static func _song_line(s: Dictionary) -> String:
	var line := str(s.get("title", s["id"]))
	if str(s.get("artist", "")) != "":
		line += " - " + str(s["artist"])
	if typeof(s.get("bpm")) in [TYPE_INT, TYPE_FLOAT]:
		line += "   %d bpm" % roundi(float(s["bpm"]))
	return line


func _settings() -> void:
	panel.label("Settings", 22, GamePanel.INK)
	var h := panel.row()
	panel.label("Hub  ", 20, GamePanel.INK, h)
	var u := panel.label(hub_url, 18, GamePanel.INK_SOFT, h)
	u.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	panel.add_button("CHANGE", "edit_hub", null, h)
	panel.label("connected" if connected else "not connected (retrying every second)", 16,
		GamePanel.INK_SOFT if connected else GamePanel.RED)
	var m := panel.row()
	panel.label("Mic  ", 20, GamePanel.INK, m)
	panel.add_button("LAPTOP", "mic:laptop", "laptop", m, false, mic_source == "laptop")
	panel.add_button("QUEST", "mic:quest", "quest", m, false, mic_source == "quest")
	var a := panel.row()
	panel.label("Audio latency  ", 20, GamePanel.INK, a)
	panel.add_button(" - ", "latency:-", -10, a)
	panel.label(" %d ms " % latency_ms, 20, GamePanel.INK, a)
	panel.add_button(" + ", "latency:+", 10, a)
	panel.label("Speaker delay: band and Fletcher start this much early.", 16, GamePanel.INK_SOFT)
	panel.add_button("BACK", "back")


func _keypad_page() -> void:
	panel.label("Laptop IP:  " + (_keypad if _keypad != "" else "_"), 22, GamePanel.INK)
	for keys in [["1", "2", "3", "4", "5"], ["6", "7", "8", "9", "0"]]:
		var r := panel.row()
		for k in keys:
			panel.add_button("  %s  " % k, "key:" + k, k, r)
	var r2 := panel.row()
	panel.add_button("  .  ", "key:.", ".", r2)
	panel.add_button("DEL", "key:del", "del", r2)
	panel.add_button("OK", "key:ok", "ok", r2)
	panel.add_button("CANCEL", "settings", null, r2)


func _results() -> void:
	var r := result
	var s := song(str(r.get("song_id", "")))
	panel.label(str(s.get("title", r.get("song_id", "Results"))), 22, GamePanel.INK)
	var notes := int(r.get("notes", 0))
	var grid := GridContainer.new()
	grid.columns = 4
	grid.add_theme_constant_override("h_separation", 18)
	panel.content.add_child(grid)
	for kv in [["bars", r.get("bars_played", 0)], ["cutoffs", r.get("cutoffs", 0)],
			["perfect", r.get("perfect", 0)], ["good", r.get("good", 0)],
			["off", r.get("off", 0)], ["miss", r.get("miss", 0)],
			["extra", r.get("extra", 0)], ["notes", notes]]:
		panel.label(kv[0], 17, GamePanel.INK_SOFT, grid)
		panel.label(str(int(kv[1])) if typeof(kv[1]) in [TYPE_INT, TYPE_FLOAT] else str(kv[1]), 19, GamePanel.INK, grid)
	var err = r.get("mean_error_ms")
	if typeof(err) in [TYPE_INT, TYPE_FLOAT]:
		var e := float(err)
		panel.label("mean %+.0f ms  (%s)" % [e, "late" if e > 0 else "early" if e < 0 else "dead on"], 19, GamePanel.INK)
	var rush = r.get("rushing_bars", [])
	var drag = r.get("dragging_bars", [])
	panel.label("rushing: %s   dragging: %s" % [_bars(rush), _bars(drag)], 16, GamePanel.RED)
	var b := panel.row()
	panel.add_button("AGAIN", "play", str(r.get("song_id", "")), b)
	panel.add_button("MENU", "back", null, b)


## session.result's rushing_bars / dragging_bars: a list of bar numbers (JSON floats), shown
## sorted with runs of 3+ as ranges ("4, 31-35"); a long list is cut with its count.
static func _bars(v) -> String:
	if typeof(v) in [TYPE_INT, TYPE_FLOAT]:
		return "%d bars" % int(v)
	if not (v is Array):
		return "none"
	var bars: Array = []
	for x in v:
		if typeof(x) in [TYPE_INT, TYPE_FLOAT] and not bars.has(int(x)):
			bars.append(int(x))
	if bars.is_empty():
		return "none"
	bars.sort()
	var parts: Array = []
	var i := 0
	while i < bars.size():
		var j := i
		while j + 1 < bars.size() and bars[j + 1] == bars[j] + 1:
			j += 1
		if j - i >= 2:
			parts.append("%d-%d" % [bars[i], bars[j]])
		else:
			for k in range(i, j + 1):
				parts.append(str(bars[k]))
		i = j + 1
	var text := ", ".join(parts.slice(0, 6))
	if parts.size() > 6:
		text += " ... (%d bars)" % bars.size()
	return text


## ---- picks ---------------------------------------------------------------

func _on_activated(id: String, arg: Variant) -> void:
	if id.begins_with("song:"):
		selected = str(arg)
		show_page("main")
		chosen.emit("select", selected)
	elif id == "scroll":
		scroll += int(arg)
		show_page("main")
	elif id == "demos":
		show_page("demo")
	elif id == "settings":
		show_page("settings")
	elif id == "back":
		show_page("main")
		chosen.emit("back", null)
	elif id == "edit_hub":
		_keypad = hub_url.trim_prefix("ws://").get_slice(":", 0)
		show_page("keypad")
	elif id.begins_with("key:"):
		_key(str(arg))
	elif id.begins_with("demo:"):
		chosen.emit("demo", arg)
	elif id.begins_with("latency:"):
		latency_ms = clampi(latency_ms + int(arg), 0, 500)
		show_page("settings")
		chosen.emit("latency", latency_ms)
	elif id.begins_with("mic:"):
		mic_source = str(arg)
		show_page("settings")
		chosen.emit("mic", mic_source)
	else:
		chosen.emit(id, arg)   # play, demo, quit: GameSession's business


func _key(k: String) -> void:
	match k:
		"del":
			_keypad = _keypad.left(-1)
		"ok":
			if _keypad.is_valid_ip_address():
				var port := hub_url.get_slice(":", 2) if hub_url.get_slice_count(":") > 2 else "8765"
				chosen.emit("hub_url", "ws://%s:%s" % [_keypad, port])
				show_page("settings")
				return
			_keypad = ""
		_:
			if _keypad.length() < 15:
				_keypad += k
	show_page("keypad")
