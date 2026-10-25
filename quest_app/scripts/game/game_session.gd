extends Node
## The game: menu -> song -> result, following the laptop's session controller
## (hub/session.py, messages in CONTRACT.md). Rehearsal builds it with `game_mode`
## (`-- --game`); it needs Fletcher's hub (scripts/fletcher), which game mode turns on.
## The headset never decides the session: PLAY sends `session.command start` and everything
## after follows `session.state`, so the laptop console can drive the same flow with no
## headset input at all.
##   idle      the menu (main page, or the result just in)
##   count_in  menu away; the song's files from the library (SongCache, user://songs/<id>/),
##             backing.ogg into the BackingPlayer (scripts/game/backing_player.gd) scheduled
##             so song_time_at_t0 is heard at t0 (laptop clock, FletcherHub.to_local_time),
##             arrangement.mid into SceneDirector.load_song for who plays when. The band
##             raises RAISE_LEAD before t0, and from t0 on the director follows the backing's
##             playback clock every frame. A restart (Fletcher's count-back-in) is just
##             another count_in at a later song time: the backing seeks.
##   stopped   the backing stops (Fletcher's fist; his link lowers the band). A small
##             "back to menu" card appears.
##   finished  the band lowers; session.result brings up the results page.
## Demo scenes (Adam's showcase and demo timeline) run through Rehearsal.start_demo_scene.
## Settings (hub URL, mic source) are kept in user://game_settings.cfg.
## Loaded with preload() by its users.

const GameMenu := preload("res://scripts/game/game_menu.gd")
const GamePanel := preload("res://scripts/game/game_panel.gd")
const GameHud := preload("res://scripts/game/game_hud.gd")
const GameInput := preload("res://scripts/game/game_input.gd")
const SongCache := preload("res://scripts/game/song_cache.gd")
const ChartStand := preload("res://scripts/game/chart_stand.gd")
const DrumChart := preload("res://scripts/game/drum_chart.gd")
const BACKING_PLAYER := "res://scripts/game/backing_player.gd"
const MIC_STREAMER := "res://scripts/game/mic_streamer.gd"   # agent C's mic.pcm sender, if present
const CALIBRATION_PLAYER := "res://scripts/game/calibration_player.gd"   # calibrate.start claps
## Where the settings are kept (tests point it elsewhere).
static var settings_path := "user://game_settings.cfg"
const LIBRARY_PORT := 8766
const START_TIMEOUT_S := 8.0      # PLAY with no count_in by then: tell the player
const RAISE_LEAD_120 := 0.6       # s before an entrance the band starts raising, at 120 BPM (= PhraseExtractor)
const COUNT_IN_BEATS := 5.0       # count_in without t0: the downbeat is this far off
## The small card that leads back to the menu during a demo or after a cutoff, in the
## drummer's frame (as GameMenu.PLACE): right of the music stand, clear of its paper (the
## drum chart stays on the stand after a cutoff).
const CARD_AT := {"x": 0.72, "f": 0.66, "y": 0.95}
## `-- --game-log`: one line per step of the flow, `[game] <local s> <event> <args>` (e2e tests).
const LOG_ARG := "--game-log"

signal state_changed(state: String)

var rehearsal: Node
var hub: FletcherHub
var director: SceneDirector
var menu: GameMenu
var chart: ChartStand             # the drum chart on the menu's stand during a song
var card: GamePanel
var hud: GameHud
var input: GameInput
var cache: SongCache
var backing: Node                 # BackingPlayer (play_at, seek, stop, song_time, load)
var mic_streamer: Node
var calibration: Node             # CalibrationPlayer
var proxy: DirectorProxy

var state := "idle"               # the last session.state
var songs: Array = []
var base_url := ""
var song := {}                    # song.json of the session's song
var prepared_id := ""             # song whose files are loaded into backing + director
var t0_local := INF               # this device's clock: downbeat of from_bar
var song_time_at_t0 := 0.0
var from_bar := 1
var bpm := 120.0
var midi_t0_s := 0.0              # arrangement.mid time = song time + midi_t0_s (song.json; 0 without a pickup)
var scheduled := false            # this count_in is handed to the backing
var raised := false
var following := false            # the director follows the song clock
var demo := ""                    # "", "showcase", "timeline"
var hub_url := ""
var mic_source := "quest" if OS.has_feature("nqmt_game") else "laptop"   # the Quest export streams its mic
## Speaker latency Godot doesn't report (0 on the Quest), measured once and set in Settings:
## the backing and Fletcher's voice (barks, count-off words) both start this much earlier.
var latency_ms := 0
var bridge_bar := 0
var _start_sent_at := -INF
var _shown := false               # the menu is allowed on screen (after the intro)
var _have_phrases := false
var recenters := 0                # quest.recenter requests (tests, logs)
var sent: Array = []              # [type, data] of everything this node sent (tests, logs)
var _log := false


## Saved settings (user://game_settings.cfg), or "" for the hub URL when none.
static func saved_hub_url() -> String:
	var cf := ConfigFile.new()
	return str(cf.get_value("game", "hub_url", "")) if cf.load(settings_path) == OK else ""


func setup(r: Node) -> bool:
	name = "GameSession"
	rehearsal = r
	if r.fletcher == null or r.director == null or r.rig == null:
		push_error("GameSession: needs the built scene and Fletcher's hub (game mode turns Fletcher on)")
		return false
	hub = r.fletcher.hub
	director = r.director
	hub_url = hub.url
	_load_settings()
	cache = SongCache.new()
	cache.name = "SongCache"
	add_child(cache)
	cache.prepared.connect(_on_prepared)
	cache.failed.connect(_on_fetch_failed)
	backing = (load(BACKING_PLAYER) as GDScript).new()
	backing.name = "BackingPlayer"
	add_child(backing)
	# Latency calibration (scripts/game/calibration_player.gd): claps on calibrate.start.
	calibration = (load(CALIBRATION_PLAYER) as GDScript).new()
	calibration.name = "CalibrationPlayer"
	calibration.hub = hub
	calibration.backing_ref = backing
	add_child(calibration)
	# Fletcher's link raises "all" when the bridge first says playing; with a loaded
	# arrangement the phrases decide who plays, so that one message is held back.
	proxy = DirectorProxy.new(director, self)
	if r.fletcher.link:
		r.fletcher.link.director = proxy
	var drummer: Node3D = r.marker_node("spot_drummer")
	var eye: float = PlayerRig.SEATED_EYE_HEIGHT
	menu = GameMenu.new()
	r.add_child(menu)
	menu.build()
	menu.place(drummer.global_transform, eye)
	menu.mic_source = mic_source
	menu.latency_ms = latency_ms
	menu.set_status(hub.is_open(), hub.url)
	menu.chosen.connect(_on_chosen)
	menu.visible = false
	chart = ChartStand.new()
	r.add_child(chart)
	chart.build(menu)
	chart.visible = false
	card = GamePanel.new()
	card.name = "BackCard"
	card.size_m = Vector2(0.26, 0.075)
	card.pixels = Vector2i(200, 58)
	r.add_child(card)
	card.build()
	card.add_button("BACK TO MENU", "card")
	card.activated.connect(_on_card)
	_place_card(drummer.global_transform, eye)
	card.visible = false
	hud = GameHud.new()
	r.add_child(hud)
	hud.build()
	hud.place(drummer.global_transform, eye)
	hud.visible = false
	input = GameInput.new()
	input.name = "GameInput"
	input.camera = r.rig.camera
	input.xr_mode = r.rig.xr_mode
	input.panels = [menu.panel, card]
	add_child(input)
	hub.message_received.connect(_on_message)
	hub.connected.connect(_on_connected)
	if r.fletcher.voice:
		r.fletcher.voice.subtitle.connect(func(text): hud.say(text))
	_apply_mic()
	_apply_latency()
	_log = LOG_ARG in OS.get_cmdline_user_args()
	return true


func _say(event: String, arg = "") -> void:
	if _log:
		print("[game] %.4f %s %s" % [_now(), event, str(arg)])


## The intro is over: the menu may show (Rehearsal calls this from _start_game).
func open() -> void:
	_shown = true
	if state in ["idle", "finished", ""] and demo == "":
		menu.visible = true
	if hub.is_open():
		_on_connected()


func _place_card(drummer: Transform3D, eye_height: float) -> void:
	var p := drummer.origin + drummer.basis.x.normalized() * CARD_AT.x \
		- drummer.basis.z.normalized() * CARD_AT.f + Vector3.UP * CARD_AT.y
	var eye := drummer.origin + Vector3.UP * eye_height
	var z := (eye - p).normalized()
	var x := Vector3.UP.cross(z).normalized()
	card.global_transform = Transform3D(Basis(x, z.cross(x), z), p)


## ---- hub ------------------------------------------------------------------

func _send(type: String, data: Dictionary) -> bool:
	sent.append([type, data])
	return hub.send(type, data)


func _on_connected() -> void:
	_send("library.request", {})
	_send("session.request", {})
	_send("quest.settings", {"mic_source": mic_source})


func _on_message(type: String, data: Dictionary, _env: Dictionary) -> void:
	match type:
		"library.songs":
			_on_library(data)
		"session.state":
			_on_state(data)
		"session.result":
			_on_result(data)
		"judgment.note":
			hud.note(data)
		"session.command":   # Fletcher's stop / restart name the bar he restarts from
			if str(data.get("action", "")) in ["stop", "restart"] and typeof(data.get("from_bar")) in [TYPE_INT, TYPE_FLOAT]:
				chart.set_restart_bar(int(data["from_bar"]))
		"quest.recenter":    # the laptop console's Recenter button (CONTRACT.md)
			recenter()


func _on_library(d: Dictionary) -> void:
	var list = d.get("songs", [])
	songs = list if list is Array else []
	_say("library", "%d songs %s" % [songs.size(), d.get("base_url", "")])
	if str(d.get("base_url", "")) != "":
		base_url = str(d["base_url"])
	menu.set_songs(songs)


func library_url() -> String:
	if base_url != "":
		return base_url
	var host := hub.url.trim_prefix("ws://").trim_prefix("wss://").get_slice(":", 0).get_slice("/", 0)
	return "http://%s:%d/songs/" % [host, LIBRARY_PORT]


func song_info(id: String) -> Dictionary:
	for s in songs:
		if s is Dictionary and str(s.get("id", "")) == id:
			return s
	return {}


func _on_state(d: Dictionary) -> void:
	var st := str(d.get("state", ""))
	if d.has("error"):
		menu.show_message("The laptop said: %s" % d["error"])
		_show_menu()
		return
	if typeof(d.get("bpm")) in [TYPE_INT, TYPE_FLOAT] and float(d["bpm"]) >= 40.0:
		bpm = float(d["bpm"])
	var prev := state
	state = st
	match st:
		"count_in":
			_on_count_in(d)
		"playing":
			if typeof(d.get("bar")) in [TYPE_INT, TYPE_FLOAT]:
				bridge_bar = int(d["bar"])
		"stopped":
			following = false
			backing.stop(0.15)
			card.visible = demo == ""
		"finished":
			following = false
			backing.stop(0.5)
			director.set_band_state("all", &"lowering")
			card.visible = false
		"idle":
			if prev != "idle":
				_end_song()
			if menu.page != "results":
				menu.show_page("main")
			if demo == "":
				_show_menu()
	if st != prev:
		_say("state", st)
		state_changed.emit(st)


func _on_result(d: Dictionary) -> void:
	_say("result", JSON.stringify(d))
	if songs.is_empty() and d.has("title"):
		menu.songs = [{"id": d.get("song_id", ""), "title": d["title"]}]
	menu.show_results(d)
	if demo == "":
		_show_menu()


## ---- the song -----------------------------------------------------------

func _on_count_in(d: Dictionary) -> void:
	var id := str(d.get("song_id", song.get("id", "")))
	var t0 := hub.to_local_time(float(d["t0"])) if typeof(d.get("t0")) in [TYPE_INT, TYPE_FLOAT] \
		else _now() + COUNT_IN_BEATS * 60.0 / bpm
	var has_st := typeof(d.get("song_time_at_t0")) in [TYPE_INT, TYPE_FLOAT]
	if scheduled and id == str(song.get("id", "")) and absf(t0 - t0_local) < 0.02 and not has_st:
		return   # an echo of the count_in already scheduled (no new numbers)
	if demo != "":
		stop_demo()
	var info := song_info(id)
	if info.is_empty():
		info = {"id": id, "title": str(d.get("title", id)), "bars": d.get("bars", 0),
			"beats_per_bar": d.get("beats_per_bar", 4)}
	song = info
	from_bar = int(d.get("from_bar", d.get("bar", 1)))
	song_time_at_t0 = float(d["song_time_at_t0"]) if has_st else _bar_start_estimate(from_bar)
	t0_local = t0
	midi_t0_s = float(song.get("midi_t0_s", 0.0)) if typeof(song.get("midi_t0_s")) in [TYPE_INT, TYPE_FLOAT] else 0.0
	scheduled = false
	raised = false
	following = false
	bridge_bar = 0
	_start_sent_at = -INF
	menu.visible = false
	card.visible = false
	if str(d.get("reason", "")) != "restart":
		chart.set_restart_bar(0)
	if prepared_id != id:
		chart.set_chart(null, str(song.get("title", id)), "Fetching the chart...")
	chart.show_bar(from_bar)
	chart.visible = true
	hud.start(str(song.get("title", id)), int(song.get("bars", 0)))
	if prepared_id == id:
		_schedule()
	else:
		cache.fetch(song, library_url())


func _bar_start_estimate(bar: int) -> float:
	var bpb := float(song.get("beats_per_bar", 4))
	return (bar - 1) * bpb * 60.0 / float(song.get("bpm", bpm))


func _on_prepared(id: String, dir: String) -> void:
	if id != str(song.get("id", "")) or not state in ["count_in", "playing"]:
		return   # a prefetch (a song picked in the menu): nothing to start
	_say("prepared", dir)
	_load_song(id, dir)
	_schedule()


func _on_fetch_failed(id: String, why: String) -> void:
	push_warning("GameSession: song %s did not download (%s); playing without the backing" % [id, why])
	if id == str(song.get("id", "")) and state in ["count_in", "playing"]:
		prepared_id = id   # nothing to load: the band follows the clock alone
		_unload_director()
		chart.set_chart(null, str(song.get("title", id)), "No chart: the song did not download.")
		_schedule()


func _load_song(id: String, dir: String) -> void:
	backing.load(dir)
	var c = DrumChart.load_dir(dir)
	chart.set_chart(c, str(song.get("title", id)), "" if c != null else "No drum chart for this song.")
	chart.show_bar(from_bar)
	_unload_director()
	var midi := dir.path_join("arrangement.mid")
	var tracks := _band_tracks(dir.path_join("band_tracks.json"), midi)
	if not tracks.is_empty() and director.load_song(midi, tracks):
		_have_phrases = true
	prepared_id = id


## band_tracks.json ({track: [section...]} or {track: section}), keeping only tracks the MIDI
## really has and targets the director knows.
func _band_tracks(path: String, midi: String) -> Dictionary:
	if not FileAccess.file_exists(path) or not FileAccess.file_exists(midi):
		return {}
	var raw = JSON.parse_string(FileAccess.get_file_as_string(path))
	if raw is Dictionary and raw.get("tracks") is Dictionary:
		raw = raw["tracks"]
	if not raw is Dictionary:
		return {}
	var smf := SMFParser.parse_file(midi)
	if smf.is_empty():
		return {}
	var names := PhraseExtractor.extract(smf).keys()
	var out := {}
	for k in raw:
		var targets = raw[k] if raw[k] is Array else [raw[k]]
		targets = targets.filter(_is_target)
		if k in names and not targets.is_empty():
			out[k] = targets
	return out


## A band target the director knows (its resolve() without the error for unknown names).
func _is_target(t) -> bool:
	var n := str(t)
	return n == "all" or director.members.has(n) or director.members.values().any(
		func(m): return m.section == n or (n == "saxes" and m.section.ends_with("sax")))


func _unload_director() -> void:
	_have_phrases = false
	director.follow_song = false
	director.phrases.clear()


func has_phrases() -> bool:
	return _have_phrases


func _schedule() -> void:
	scheduled = true
	_say("play_at", "%.4f %.4f phrases=%s" % [t0_local, song_time_at_t0, _have_phrases])
	backing.play_at(t0_local, song_time_at_t0)


func raise_lead() -> float:
	return RAISE_LEAD_120 * 120.0 / bpm


## The song time heard now (backing's playback clock, or the session clock without one).
func song_time() -> float:
	if backing.is_loaded() and backing.state != 0:
		return backing.song_time()
	return song_time_at_t0 + (_now() - t0_local)


func _process(_delta: float) -> void:
	menu.set_status(hub.is_open(), hub.url)
	if _start_sent_at > -INF and _now() - _start_sent_at > START_TIMEOUT_S and state != "count_in":
		_start_sent_at = -INF
		menu.show_message("The laptop did not start the song. Is  python -m nqmt  running there?")
	if not scheduled or not state in ["count_in", "playing"]:
		return
	var now := _now()
	if not raised and now >= t0_local - raise_lead():
		_raise()
	if not following and now >= t0_local:
		following = true
	if following:
		var st := song_time()
		if _have_phrases:
			director.set_song_position(st + midi_t0_s)
		chart.set_time(st, true)
		hud.set_bar(bridge_bar if bridge_bar > 0 else _bar_estimate(st))


func _bar_estimate(st: float) -> int:
	var bar_s := float(song.get("beats_per_bar", 4)) * 60.0 / bpm
	return from_bar + floori((st - song_time_at_t0) / bar_s + 1e-6)


## Up before the downbeat: everyone with a part at t0 (or the whole band without an
## arrangement). From t0 on the phrases take over (set_song_position).
func _raise() -> void:
	raised = true
	_say("raise", "%.3f s before t0" % (t0_local - _now()))
	if not _have_phrases:
		director.set_band_state("all", &"playing")
		return
	var at := song_time_at_t0 + midi_t0_s + raise_lead()
	for member in director.phrases:
		if PhraseExtractor.state_at(director.phrases[member], at) in [&"raising", &"playing"]:
			director.set_band_state(member, &"playing")


func _end_song() -> void:
	following = false
	scheduled = false
	raised = false
	backing.stop(0.3)
	hud.visible = false
	card.visible = false
	chart.visible = false
	if director.follow_song or _have_phrases:
		_unload_director()
	director.set_band_state("all", &"lowering")


func _show_menu() -> void:
	card.visible = false
	chart.visible = false
	hud.visible = false
	if _shown:
		menu.visible = true


## ---- menu picks ------------------------------------------------------------

func _on_chosen(id: String, arg: Variant) -> void:
	match id:
		"select":
			var s := song_info(str(arg))
			if not s.is_empty():
				cache.fetch(s, library_url())   # prefetch while the player reads the page
		"play":
			play(str(arg))
		"quit":
			quit()
		"back":
			if state == "finished":
				quit()   # the laptop's session back to idle too
		"demo":
			start_demo(str(arg))
		"mic":
			mic_source = str(arg)
			_save_settings()
			_apply_mic()
			_send("quest.settings", {"mic_source": mic_source})
		"hub_url":
			set_hub_url(str(arg))
		"latency":
			set_latency_ms(int(arg))


func play(id: String) -> void:
	if not hub.is_open():
		menu.show_message("Not connected to the laptop (%s). Check Settings." % hub.url)
		return
	var s := song_info(id)
	if not s.is_empty():
		cache.fetch(s, library_url())
	_send("session.command", {"action": "start", "song_id": id, "from_bar": 1})
	_start_sent_at = _now()
	menu.show_waiting(str(s.get("title", id)))


func quit() -> void:
	_start_sent_at = -INF
	if not _send("session.command", {"action": "quit"}):
		_on_state({"state": "idle"})   # no laptop: just go back
	else:
		menu.show_page("main")


func start_demo(kind: String) -> void:
	demo = kind
	menu.visible = false
	chart.visible = false
	card.visible = true
	rehearsal.start_demo_scene(kind == "showcase")


func stop_demo() -> void:
	demo = ""
	rehearsal.stop_demo_scene()
	card.visible = false


func _on_card(_id: String, _arg: Variant) -> void:
	if demo != "":
		stop_demo()
		menu.show_page("main")
		_show_menu()
	else:
		quit()


## Turn the room so the drummer, sitting at the real kit facing it, faces the room's front:
## the kit window lands on the real kit and the stand is in front (PlayerRig.recenter).
## quest.recenter from the laptop console, at any time.
func recenter() -> bool:
	recenters += 1
	_say("recenter")
	if not rehearsal.rig.xr_mode:
		return false   # the desktop camera is always on the seat
	rehearsal.rig.request_recenter()
	return true


## ---- settings --------------------------------------------------------------

func set_hub_url(url: String) -> void:
	hub_url = url
	_save_settings()
	if hub.url == url:
		return
	hub.url = url
	base_url = ""
	hub._ws.close()   # FletcherHub reconnects to hub.url within reconnect_s


func set_latency_ms(ms: int) -> void:
	latency_ms = clampi(ms, 0, 500)
	menu.latency_ms = latency_ms
	_save_settings()
	_apply_latency()


func _apply_latency() -> void:
	var s := latency_ms / 1000.0
	if "extra_latency_s" in backing:
		backing.extra_latency_s = s
	var voice = rehearsal.fletcher.voice if rehearsal.fletcher else null
	if voice != null and "extra_latency_s" in voice:
		voice.extra_latency_s = s


func _load_settings() -> void:
	var cf := ConfigFile.new()
	if cf.load(settings_path) == OK:
		mic_source = str(cf.get_value("game", "mic_source", mic_source))
		latency_ms = int(cf.get_value("game", "latency_ms", latency_ms))


func _save_settings() -> void:
	var cf := ConfigFile.new()
	cf.set_value("game", "hub_url", hub_url)
	cf.set_value("game", "mic_source", mic_source)
	cf.set_value("game", "latency_ms", latency_ms)
	cf.save(settings_path)


func _apply_mic() -> void:
	var want := mic_source == "quest"
	if want and mic_streamer == null and ResourceLoader.exists(MIC_STREAMER):
		mic_streamer = (load(MIC_STREAMER) as GDScript).new()
		mic_streamer.name = "MicStreamer"
		if "hub" in mic_streamer:
			mic_streamer.set("hub", hub)
		add_child(mic_streamer)
	elif not want and mic_streamer != null:
		mic_streamer.queue_free()
		mic_streamer = null


static func _now() -> float:
	return Time.get_ticks_usec() / 1e6


## FletcherLink talks to the director through this: it passes everything on except the
## link's whole-band raise while an arrangement decides who plays, and its whole-band freeze
## (the fist) goes only to the players who are playing: one resting through a rest in the
## arrangement can't freeze (BandMember allows it from play_loop only).
class DirectorProxy:
	extends RefCounted
	var director: SceneDirector
	var terence: Terence
	var game

	func _init(d: SceneDirector, g) -> void:
		director = d
		terence = d.terence
		game = g

	func handle_message(msg: Dictionary) -> bool:
		if game.has_phrases() and str(msg.get("type", "")) == "set_band_state" \
				and str(msg.get("target", "")) == "all" and str(msg.get("state", "")) in ["playing", "raising"]:
			return true
		if str(msg.get("type", "")) == "react" and str(msg.get("target", "")) == "all" \
				and str(msg.get("reaction", "")) == "freeze":
			for m in director.members.values():
				match m.current_state():
					&"play_loop":
						m.react(&"freeze")
					&"raise":
						m.set_band_state(&"lowering")
			return true
		return director.handle_message(msg)
