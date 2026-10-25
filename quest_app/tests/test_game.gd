extends TestCase
## The game (scripts/game, Rehearsal.game_mode): the menu on its stand, gaze dwell and clicks,
## library.songs, the song flow on session.state (count_in -> backing scheduled at the mapped
## time, band raised before t0, the director following), results, demo scenes and the song
## download. The hub points where nothing listens; its messages are fed in through
## message_received, the path a real frame takes. The backing player is replaced by a
## recorder, so no audio is needed. Settings and the song cache go to user://test_* paths.

const GameSession := preload("res://scripts/game/game_session.gd")
const SongCache := preload("res://scripts/game/song_cache.gd")
const NO_SERVER := "ws://127.0.0.1:9"
const HTTP_PORT := 18766
const FIX := "res://tests/fixtures/"
const SONGS := [
	{"id": "caravan", "title": "Caravan", "artist": "Duke Ellington", "bpm": 120, "beats_per_bar": 4,
		"bars": 32, "backing": false, "arrangement": false, "imported_at": "t1"},
	{"id": "whiplash", "title": "Whiplash", "artist": "Hank Levy", "bpm": 150, "beats_per_bar": 7,
		"bars": 40, "backing": false, "arrangement": false, "imported_at": "t1"},
]


## Records what the game asks of the backing player (scripts/game/backing_player.gd's API).
class FakeBacking:
	extends Node
	var calls: Array = []
	var state := 0
	var _t0 := 0.0
	var _st := 0.0
	func load(dir: String) -> bool:
		calls.append(["load", dir])
		return true
	func is_loaded() -> bool:
		return true
	func is_playing() -> bool:
		return state == 2
	func play_at(local_t: float, song_t: float) -> void:
		calls.append(["play_at", local_t, song_t])
		_t0 = local_t
		_st = song_t
		state = 1
	func seek(song_t: float, local_t: float = -1.0) -> void:
		play_at(local_t, song_t)
	func stop(fade_s: float = 0.0) -> void:
		calls.append(["stop", fade_s])
		state = 3
	func song_time() -> float:
		return _st + (Time.get_ticks_usec() / 1e6 - _t0)
	func last(kind: String) -> Array:
		for i in range(calls.size() - 1, -1, -1):
			if calls[i][0] == kind:
				return calls[i]
		return []


func _scene() -> Rehearsal:
	GameSession.settings_path = "user://test_game_settings.cfg"
	SongCache.root = "user://test_songs/"
	_wipe("user://test_songs/")
	DirAccess.remove_absolute(GameSession.settings_path)
	var r := Rehearsal.new()
	r.sfx_enabled = false
	r.cinematics_enabled = false
	r.intro_enabled = false
	r.game_mode = true
	r.fletcher_hub_url = NO_SERVER
	mount(r)
	var fake := FakeBacking.new()
	r.game.backing.queue_free()
	r.game.backing = fake
	r.game.add_child(fake)
	return r


static func _wipe(dir: String) -> void:
	var d := DirAccess.open(dir)
	if d == null:
		return
	for sub in d.get_directories():
		_wipe(dir + sub + "/")
	for f in d.get_files():
		d.remove(f)
	DirAccess.remove_absolute(dir)


func _feed(r: Rehearsal, type: String, data: Dictionary) -> void:
	r.fletcher.hub.message_received.emit(type, data, {"type": type, "src": "test", "data": data})


func _frames(n: int) -> void:
	for i in n:
		await tree.process_frame


func _wait_for(cond: Callable, timeout: float) -> bool:
	var end := Time.get_ticks_msec() + int(timeout * 1000.0)
	while Time.get_ticks_msec() < end:
		if cond.call():
			return true
		await tree.process_frame
	return cond.call()


func _eye(r: Rehearsal) -> Vector3:
	return r.marker_node("spot_drummer").global_position + Vector3.UP * PlayerRig.SEATED_EYE_HEIGHT


static func _now() -> float:
	return Time.get_ticks_usec() / 1e6


func test_menu_builds() -> void:
	var r := _scene()
	if not check(r.game != null, "game mode builds the session"):
		return
	check(r.fletcher != null, "game mode turns Fletcher on")
	check(r.showcase == null and r.injector == null, "no showcase or demo until picked")
	var m = r.game.menu
	check(m.visible, "menu up once the game starts (no intro)")
	check_eq(m.page, "main", "main page")
	for id in ["demos", "settings"]:
		check(m.panel.has_item(id), "main page has %s" % id)
	check(not m.panel.has_item("play"), "no PLAY before a song is picked")
	var eye := _eye(r)
	var d: float = m.panel.global_position.distance_to(eye)
	check(d > 0.6 and d < 1.1, "the stand is within reach of the throne (%.2f m)" % d)
	check(m.panel.global_transform.basis.z.dot((eye - m.panel.global_position).normalized()) > 0.99, "the paper faces the eye")
	check(m.panel.global_position.y < eye.y, "the paper sits below eye level")
	var pole: MeshInstance3D = m.get_node("Stand/Pole")
	check_near(pole.global_position.y - (pole.mesh as CylinderMesh).height * 0.5, r.marker_node("spot_drummer").global_position.y, 0.01, "the stand stands on the floor")
	check(not r.game.hud.visible and not r.game.card.visible, "no HUD or card on the menu")


func test_library_songs_populate() -> void:
	var r := _scene()
	var m = r.game.menu
	_feed(r, "library.songs", {"songs": SONGS, "base_url": "http://10.0.0.5:8766/songs/"})
	check_eq(r.game.library_url(), "http://10.0.0.5:8766/songs/", "base_url kept")
	check(m.panel.has_item("song:caravan") and m.panel.has_item("song:whiplash"), "both songs listed")
	check(m.panel.item_text("song:caravan").begins_with("Caravan - Duke Ellington"), "title and artist (%s)" % m.panel.item_text("song:caravan"))
	_feed(r, "library.songs", {"songs": [SONGS[1]]})
	check(not m.panel.has_item("song:caravan"), "a new list replaces the old")
	check_eq(m.selected, "whiplash", "a single song is picked for you")
	check(m.panel.has_item("play"), "PLAY then shows")
	var many := []
	for i in 12:
		many.append({"id": "s%d" % i, "title": "Song %d" % i})
	_feed(r, "library.songs", {"songs": many})
	check(m.panel.has_item("scroll"), "a long list pages")
	m.panel.pick("scroll")
	check(m.panel.has_item("song:s4") and not m.panel.has_item("song:s0"), "next page")


func test_gaze_dwell_selects() -> void:
	var r := _scene()
	var m = r.game.menu
	_feed(r, "library.songs", {"songs": SONGS})
	await _frames(3)   # layout
	var p = m.panel
	var eye := _eye(r)
	var dir: Vector3 = (p.item_world("song:whiplash") - eye).normalized()
	check(p.hover_ray(eye, dir), "the gaze ray hits the paper")
	check_eq(p.hovered, "song:whiplash", "and the song under it")
	p.step(1.4)
	check_eq(m.selected, "", "not picked before the dwell")
	check(p.dwell > 1.3, "the dwell ring is filling")
	p.step(0.15)
	check_eq(m.selected, "whiplash", "picked after 1.5 s")
	check(p.has_item("play"), "PLAY offered for it")
	check(not p.hover_ray(eye, -dir), "looking away misses the paper")
	# a resting gaze never picks what the next page puts under it (CANCEL where PLAY was)
	await _frames(2)
	m.show_page("demo")
	await _frames(2)
	var at_back: Vector3 = (p.item_world("back") - eye).normalized()
	p.hover_ray(eye, at_back)
	p.step(1.6)
	check_eq(m.page, "main", "BACK picked by dwell")
	await _frames(2)
	p.hover_ray(eye, at_back)   # same spot: now whatever main has there
	p.step(3.0)
	check_eq(m.page, "main", "the gaze staying put picks nothing new (hovered %s)" % p.hovered)
	p.hover_ray(eye, (p.item_world("demos") - eye).normalized())
	p.step(1.6)
	check_eq(m.page, "demo", "moving the gaze re-arms the dwell")
	m.show_page("main")
	# the same through GameInput's frame loop (what the headset does), real time
	await _frames(2)
	r.game.input.scripted_ray = [eye, (p.item_world("settings") - eye).normalized()]
	check(await _wait_for(func(): return m.page == "settings", 2.5), "dwelling on SETTINGS opens it (page %s)" % m.page)
	r.game.input.scripted_ray = []


func test_click_picks_at_once() -> void:
	var r := _scene()
	var m = r.game.menu
	await _frames(3)
	var eye := _eye(r)
	r.game.input.scripted_ray = [eye, (m.panel.item_world("demos") - eye).normalized()]
	await _frames(2)
	r.game.input.click()   # a pinch or a mouse click
	await _frames(2)
	check_eq(m.page, "demo", "a click picks without the dwell")
	r.game.input.scripted_ray = []


func test_play_sends_start() -> void:
	var r := _scene()
	var g = r.game
	_feed(r, "library.songs", {"songs": SONGS})
	g.play("caravan")
	check_eq(g.menu.page, "message", "no hub: PLAY says so")
	g.hub._open = true   # pretend the socket is up for one call
	g.play("caravan")
	g.hub._open = false
	check_eq(g.sent[-1], ["session.command", {"action": "start", "song_id": "caravan", "from_bar": 1}], "PLAY asks the laptop to start")
	check_eq(g.menu.page, "waiting", "and waits for the count-in")


func test_count_in_schedules_backing() -> void:
	var r := _scene()
	var g = r.game
	var fake: FakeBacking = g.backing
	_feed(r, "library.songs", {"songs": SONGS})
	g.hub.clock_offset = 100.0          # this device = laptop + 100 s
	var t0_local := _now() + 1.0
	_feed(r, "session.state", {"state": "count_in", "song_id": "caravan", "t0": t0_local - 100.0,
		"from_bar": 9, "song_time_at_t0": 16.0, "bpm": 120.0, "bar": 9, "beat": 1})
	check(not g.menu.visible, "menu hides on count_in")
	check(g.hud.visible, "the HUD shows")
	check(await _wait_for(func(): return not fake.last("play_at").is_empty(), 2.0), "backing scheduled")
	var call := fake.last("play_at")
	if call.size() == 3:
		check_near(call[1], t0_local, 0.002, "play_at at t0 mapped onto this device's clock")
		check_near(call[2], 16.0, 1e-6, "from song_time_at_t0")
	var sax: BandMember = r.band[0]
	check(await _wait_for(func(): return g.raised, 1.0), "band raised")
	check(_now() < t0_local, "before t0 (%.2f s early)" % (t0_local - _now()))
	check(sax.current_state() in [&"raise", &"play_loop"], "the whole band up without an arrangement (%s)" % sax.current_state())
	check(await _wait_for(func(): return g.following, 1.5), "follows the clock from t0")
	await _frames(2)
	check_eq(g.hud.bar, 9, "HUD on the from_bar")
	_feed(r, "judgment.note", {"error_ms": -30.0, "grade": "off", "bar": 9, "beat": 1.0, "drum": "snare", "song_time": 16.0})
	_feed(r, "judgment.note", {"error_ms": -25.0, "grade": "good", "bar": 9, "beat": 2.0, "drum": "snare", "song_time": 16.5})
	check_eq(g.hud.trend(), "rushing", "early hits read as rushing")
	_feed(r, "session.state", {"state": "stopped", "song_id": "caravan", "bar": 10})
	check(not fake.last("stop").is_empty(), "stopped stops the backing")
	check(g.card.visible, "the back-to-menu card after a cutoff")
	var loads := fake.calls.filter(func(c): return c[0] == "load").size()
	var t1 := _now() + 0.5
	_feed(r, "session.state", {"state": "count_in", "song_id": "caravan", "t0": t1 - 100.0,
		"from_bar": 10, "song_time_at_t0": 18.0, "bpm": 120.0, "reason": "restart"})
	call = fake.last("play_at")
	check(call.size() == 3 and absf(call[1] - t1) < 0.002 and absf(call[2] - 18.0) < 1e-6, "a restart seeks the backing (%s)" % [call])
	check_eq(fake.calls.filter(func(c): return c[0] == "load").size(), loads, "without loading the song again")
	check(not g.card.visible, "the card goes on the count-back-in")
	_feed(r, "session.state", {"state": "idle"})
	check(g.menu.visible and g.menu.page == "main", "quit (idle) brings the menu back")
	check(not g.following and not g.hud.visible, "and ends the song")


func test_arrangement_raises_only_its_parts() -> void:
	var r := _scene()
	var g = r.game
	var song := {"id": "fixture", "title": "Fixture", "bpm": 100, "beats_per_bar": 4, "bars": 4,
		"backing": false, "arrangement": true, "imported_at": "t1"}
	# the cache, as a download would have left it
	var dir := SongCache.song_dir("fixture")
	DirAccess.make_dir_recursive_absolute(dir)
	DirAccess.copy_absolute(FIX + "type1_tracks.mid", dir + "arrangement.mid")
	var f := FileAccess.open(dir + "band_tracks.json", FileAccess.WRITE)
	f.store_string(JSON.stringify({"Trumpets": ["trumpet"], "Trombones": "trombone", "Strings": ["violin"]}))
	f.close()
	var meta := song.duplicate()
	meta["_missing"] = []
	f = FileAccess.open(dir + "song.json", FileAccess.WRITE)
	f.store_string(JSON.stringify(meta))
	f.close()
	check(SongCache.is_cached(song), "cached")
	_feed(r, "library.songs", {"songs": [song]})
	g.hub.clock_offset = 0.0
	var t0 := _now() + 1.0
	# song time 4.0: trombones come in at 4.8 (raise at 4.2), trumpets are done (lower 2.8)
	_feed(r, "session.state", {"state": "count_in", "song_id": "fixture", "t0": t0, "from_bar": 3,
		"song_time_at_t0": 4.0, "bpm": 100.0})
	check(g.has_phrases(), "arrangement.mid loaded through band_tracks.json (unknown parts skipped)")
	check_eq(g.director.phrases.size(), 8, "4 trumpets + 4 trombones follow the arrangement")
	var by := {}
	for m in r.band:
		by[m.member_name] = m
	_feed(r, "session.state", {"state": "playing", "song_id": "fixture", "bar": 3, "bpm": 100.0})
	await _frames(2)
	check_eq(by["band_alto_sax_1"].current_state(), &"rest", "Fletcher's whole-band raise held back while the arrangement decides")
	check(await _wait_for(func(): return g.following, 2.0), "following")
	check(await _wait_for(func(): return by["band_trombone_1"].current_state() in [&"raise", &"play_loop"], 1.5), "trombones up for their entrance (%s)" % by["band_trombone_1"].current_state())
	check(by["band_trumpet_1"].current_state() == &"rest", "trumpets resting (%s)" % by["band_trumpet_1"].current_state())
	check(by["band_alto_sax_1"].current_state() == &"rest", "saxes have no part")


func test_results_show() -> void:
	var r := _scene()
	var g = r.game
	_feed(r, "library.songs", {"songs": SONGS})
	_feed(r, "session.state", {"state": "count_in", "song_id": "caravan", "t0": _now() + 5.0, "song_time_at_t0": 0.0})
	check(not g.menu.visible, "menu away during the song")
	_feed(r, "session.state", {"state": "finished", "song_id": "caravan", "bar": 32})
	_feed(r, "session.result", {"song_id": "caravan", "bars_played": 32, "cutoffs": 2, "notes": 400,
		"perfect": 200, "good": 120, "off": 50, "miss": 30, "extra": 7, "mean_error_ms": -8.4,
		"rushing_bars": [3, 4, 17], "dragging_bars": []})
	check(g.menu.visible, "the menu comes back with the result")
	check_eq(g.menu.page, "results", "results page")
	var texts: Array = g.menu.panel.content.find_children("*", "Label", true, false).map(func(l): return l.text)
	check("Caravan" in texts, "song title")
	check("200" in texts and "perfect" in texts, "grade counts")
	check(texts.any(func(t): return t.contains("-8 ms")), "mean error")
	check(texts.any(func(t): return t.contains("3, 4, 17")), "rushing bars")
	check(g.menu.panel.has_item("play") and g.menu.panel.has_item("back"), "AGAIN and MENU")


func test_result_bar_lists() -> void:
	# session.result's rushing_bars / dragging_bars are bar-number lists (floats after JSON)
	var M = load("res://scripts/game/game_menu.gd")
	check_eq(M._bars([3.0, 4.0, 17.0]), "3, 4, 17", "short list as is")
	check_eq(M._bars([35.0, 4.0, 31.0, 33.0, 34.0, 36.0, 34.0]), "4, 31, 33-36", "sorted, runs as ranges")
	check_eq(M._bars([]), "none", "empty")
	check_eq(M._bars(null), "none", "missing")
	check_eq(M._bars([1.0, 3.0, 5.0, 7.0, 9.0, 11.0, 13.0, 15.0]), "1, 3, 5, 7, 9, 11 ... (8 bars)", "long list cut")
	var r := _scene()
	var g = r.game
	_feed(r, "library.songs", {"songs": SONGS})
	_feed(r, "session.result", {"song_id": "caravan", "notes": 10, "rushing_bars": [],
		"dragging_bars": [12.0, 13.0, 14.0, 20.0]})
	var texts: Array = g.menu.panel.content.find_children("*", "Label", true, false).map(func(l): return l.text)
	check(texts.any(func(t): return t.contains("rushing: none") and t.contains("dragging: 12-14, 20")),
		"results page shows both lists (%s)" % [texts])


func test_demo_scene_and_back() -> void:
	var r := _scene()
	var g = r.game
	g.menu.show_page("demo")
	g.menu.panel.pick("demo:showcase")
	check(r.showcase != null, "the showcase runs")
	check(not g.menu.visible and g.card.visible, "menu away, back card up")
	await _frames(3)
	g.card.pick("card")
	check(r.showcase == null, "showcase gone")
	check(g.menu.visible and g.menu.page == "main", "back on the menu")
	g.start_demo("timeline")
	check(r.injector != null, "the demo timeline runs")
	_feed(r, "session.state", {"state": "count_in", "song_id": "caravan", "t0": _now() + 5.0, "song_time_at_t0": 0.0})
	check(r.injector == null, "a song started from the laptop ends the demo")


func test_settings_saved() -> void:
	var r := _scene()
	var g = r.game
	g.menu.show_page("settings")
	g.menu.panel.pick("mic:quest")
	check_eq(g.mic_source, "quest", "mic source")
	check_eq(g.sent[-1][0], "quest.settings", "told to the laptop")
	g.menu.panel.pick("latency:+")
	g.menu.panel.pick("latency:+")
	check_eq(g.latency_ms, 20, "audio latency up in 10 ms steps")
	check_near(g.backing.extra_latency_s if "extra_latency_s" in g.backing else 0.02, 0.02, 1e-6, "on the backing")
	check_near(r.fletcher.voice.extra_latency_s, 0.02, 1e-6, "and on Fletcher's voice")
	var cf := ConfigFile.new()
	cf.load(GameSession.settings_path)
	check_eq(int(cf.get_value("game", "latency_ms", -1)), 20, "saved")
	g.menu.show_page("keypad")
	for k in "10.0.0.7":
		g.menu._on_activated("key:" + k, k)
	g.menu._on_activated("key:ok", "ok")
	check_eq(g.hub.url, "ws://10.0.0.7:9", "hub URL from the keypad (port kept)")
	check_eq(GameSession.saved_hub_url(), "ws://10.0.0.7:9", "saved")
	check_eq(g.library_url(), "http://10.0.0.7:8766/songs/", "library guessed from the hub host until library.songs says")


func test_song_download() -> void:
	SongCache.root = "user://test_songs/"
	_wipe("user://test_songs/")
	var server := TCPServer.new()
	if not check(server.listen(HTTP_PORT, "127.0.0.1") == OK, "test HTTP server listens"):
		return
	var body := PackedByteArray()
	for i in 3000:
		body.append(i % 251)
	var cache := SongCache.new()
	mount(cache)
	var done := []
	cache.prepared.connect(func(id, dir): done.append([id, dir]))
	cache.failed.connect(func(id, why): done.append([id, "FAILED " + why]))
	var song := {"id": "dl", "backing": true, "arrangement": true, "imported_at": "x1"}
	cache.fetch(song, "http://127.0.0.1:%d/songs/" % HTTP_PORT)
	var paths := []
	var end := Time.get_ticks_msec() + 5000
	var peers := []
	while done.is_empty() and Time.get_ticks_msec() < end:
		if server.is_connection_available():
			peers.append([server.take_connection(), PackedByteArray()])
		for p in peers:
			var c: StreamPeerTCP = p[0]
			c.poll()
			if c.get_status() != StreamPeerTCP.STATUS_CONNECTED:
				continue
			var n := c.get_available_bytes()
			if n > 0:
				p[1].append_array(c.get_data(n)[1])
			var req: String = p[1].get_string_from_ascii()
			if req.contains("\r\n\r\n") and not "sent" in p:
				var path := req.get_slice(" ", 1)
				paths.append(path)
				var ok := path == "/songs/dl/backing.ogg"
				var payload := body if ok else "nope".to_ascii_buffer()
				var head := "HTTP/1.1 %s\r\nContent-Length: %d\r\nConnection: close\r\n\r\n" % ["200 OK" if ok else "404 Not Found", payload.size()]
				c.put_data(head.to_ascii_buffer())
				c.put_data(payload)
				p.append("sent")
		await tree.process_frame
	server.stop()
	check_eq(done, [["dl", "user://test_songs/dl/"]], "downloaded")
	check_eq(paths, ["/songs/dl/backing.ogg", "/songs/dl/arrangement.mid", "/songs/dl/band_tracks.json"], "asked for each file")
	check_eq(FileAccess.get_file_as_bytes("user://test_songs/dl/backing.ogg"), body, "backing.ogg intact")
	check(not FileAccess.file_exists("user://test_songs/dl/arrangement.mid"), "a 404 leaves no file")
	check(not FileAccess.file_exists("user://test_songs/dl/backing.ogg.part"), "no .part left")
	check(SongCache.is_cached(song), "cached for this import")
	done.clear()
	cache.fetch(song, "http://127.0.0.1:%d/songs/" % HTTP_PORT)
	check_eq(done.size(), 1, "a cached song is ready at once, no download")
	var reimported := song.duplicate()
	reimported["imported_at"] = "x2"
	check(not SongCache.is_cached(reimported), "a re-import downloads again")
