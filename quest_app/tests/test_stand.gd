extends TestCase
## The drummer's music stand (scripts/game/chart_stand.gd, drum_chart.gd) and recentering
## (PlayerRig.recenter_transform, quest.recenter): where the stand stands, the chart's bars,
## lanes, page and playhead through a song, the restart bar, and the recenter math.

const GameSession := preload("res://scripts/game/game_session.gd")
const SongCache := preload("res://scripts/game/song_cache.gd")
const DrumChart := preload("res://scripts/game/drum_chart.gd")
const GamePanel := preload("res://scripts/game/game_panel.gd")
const NO_SERVER := "ws://127.0.0.1:9"
## Two bars of 4/4 at 120 BPM (2 s each), then one of 3/4 (1.5 s).
const TEMPO := {"bars": [
	{"bar": 1, "start_s": 0.0, "bar_s": 2.0, "numerator": 4, "denominator": 4},
	{"bar": 2, "start_s": 2.0, "bar_s": 2.0, "numerator": 4, "denominator": 4},
	{"bar": 3, "start_s": 4.0, "bar_s": 1.5, "numerator": 3, "denominator": 4}], "end_s": 5.5}
const SHEET := "# time_s  midi  name\n0.000000  36  kick\n0.000000  49  crash\n0.500000  42  hihat_closed\n" \
	+ "1.000000  38  snare\n1.999990  45  tom_low\n2.500000  51  ride\n4.750000  44  hihat_pedal\n4.750000  70  maracas\n"


func _scene() -> Rehearsal:
	GameSession.settings_path = "user://test_stand_settings.cfg"
	SongCache.root = "user://test_stand_songs/"
	DirAccess.remove_absolute(GameSession.settings_path)
	var r := Rehearsal.new()
	r.sfx_enabled = false
	r.cinematics_enabled = false
	r.intro_enabled = false
	r.game_mode = true
	r.fletcher_hub_url = NO_SERVER
	mount(r)
	return r


func _feed(r: Rehearsal, type: String, data: Dictionary) -> void:
	r.fletcher.hub.message_received.emit(type, data, {"type": type, "src": "test", "data": data})


func _eye(r: Rehearsal) -> Vector3:
	return r.marker_node("spot_drummer").global_position + Vector3.UP * PlayerRig.SEATED_EYE_HEIGHT


func test_stand_placement() -> void:
	var r := _scene()
	var g = r.game
	var paper: GamePanel = g.menu.panel
	var eye := _eye(r)
	var drummer := r.marker_node("spot_drummer").global_transform
	var d := paper.global_position.distance_to(eye)
	check(d >= 0.7 and d <= 0.9, "reading distance from the seated eye (%.2f m)" % d)
	var local := drummer.affine_inverse() * paper.global_position
	check(-local.z > 0.6, "in front of the throne (%.2f m)" % -local.z)
	var top := paper.global_transform * Vector3(0, paper.size_m.y * 0.5, 0)
	check(top.y < eye.y, "the paper's top edge is under the eye line (%.2f < %.2f)" % [top.y, eye.y])
	check(g.chart.panel.global_transform.is_equal_approx(paper.global_transform), "the chart is the stand's paper, where the menu is")
	check(g.chart.panel.size_m == paper.size_m and g.chart.panel.pixels == paper.pixels, "same sheet size")
	check(g.chart.has_node("Stand/Pole") and (g.chart.get_node("Stand/Pole") as Node3D).global_position.is_equal_approx(
		(g.menu.get_node("Stand/Pole") as Node3D).global_position), "on the same stand")
	var mat := paper.quad.material_override as BaseMaterial3D
	check(mat.render_priority > 0 and mat.transparency != BaseMaterial3D.TRANSPARENCY_DISABLED,
		"the paper draws after the kit window (it stands inside its dome)")
	check(mat.texture_filter == BaseMaterial3D.TEXTURE_FILTER_NEAREST, "PS2 look: nearest filtered")
	check(paper.ray_pixel(eye, (g.card.global_position - eye).normalized()).x < 0.0,
		"the back-to-menu card is clear of the stand's paper")
	check(not g.chart.visible, "no chart on the menu")


func test_chart_parse() -> void:
	var c = DrumChart.parse(SHEET, TEMPO)
	if not check(c != null, "parsed"):
		return
	check_eq(c.bar_count(), 3, "three bars from the tempo map")
	check_eq(c.beats(3), 3, "3/4 bar")
	check_eq(c.hits_in(1), [[0.0, 5], [0.0, 0], [0.25, 2], [0.5, 3]], "bar 1: kick+crash, hi-hat, snare, in lanes")
	check_eq(c.hits_in(2).map(func(h): return h[1]), [4, 1], "a hit a hair before the bar line is the next bar's downbeat; ride")
	check_near(c.hits_in(2)[0][0], 0.0, 1e-3, "on the downbeat")
	check_eq(c.hits_in(3), [[0.5, 2]], "hi-hat pedal in the hi-hat lane; maracas have no lane")
	check_eq(c.bar_at(-1.0), 1, "before the song: bar 1")
	check_eq(c.bar_at(3.0), 2, "bar 2")
	check_eq(c.bar_at(4.0), 3, "on a bar line: the new bar")
	check_eq(c.bar_at(9.0), 3, "after the end: the last bar")
	check_near(c.fraction(4.75), 0.5, 1e-6, "half way through the 3/4 bar")
	check(DrumChart.parse(SHEET, {}) == null, "no tempo map, no chart")
	check_eq(DrumChart.slots(1), [1, 2, 3, 4], "first page")
	check_eq(DrumChart.slots(3), [5, 6, 3, 4], "passed slots turn to the bars after the page")
	check_eq(DrumChart.slots(6), [9, 6, 7, 8], "")
	for bar in range(1, 40):
		var s := DrumChart.slots(bar)
		check(bar in s and s.all(func(b): return b >= bar and b < bar + 4), "page at bar %d reads ahead only (%s)" % [bar, s])


func test_song_cache_fetches_the_chart() -> void:
	var song := {"id": "x", "backing": true, "arrangement": false, "notes": 12}
	check_eq(SongCache.files_for(song), ["backing.ogg", "drums.drums.txt", "drums.tempo.json"], "a song with a sheet brings its chart")


func test_chart_follows_the_song() -> void:
	var r := _scene()
	var g = r.game
	var song := {"id": "chart", "title": "Chart", "bpm": 120, "beats_per_bar": 4, "bars": 3,
		"backing": false, "arrangement": false, "notes": 6, "imported_at": "t1"}
	var dir := SongCache.song_dir("chart")
	DirAccess.make_dir_recursive_absolute(dir)
	for kv in [["drums.drums.txt", SHEET], ["drums.tempo.json", JSON.stringify(TEMPO)]]:
		var f := FileAccess.open(dir + kv[0], FileAccess.WRITE)
		f.store_string(kv[1])
		f.close()
	var meta := song.duplicate()
	meta["_missing"] = []
	var mf := FileAccess.open(dir + "song.json", FileAccess.WRITE)
	mf.store_string(JSON.stringify(meta))
	mf.close()
	check(SongCache.is_cached(song), "cached with its chart")
	_feed(r, "library.songs", {"songs": [song]})
	g.hub.clock_offset = 0.0
	var t0 := Time.get_ticks_usec() / 1e6 + 0.3
	_feed(r, "session.state", {"state": "count_in", "song_id": "chart", "t0": t0, "from_bar": 2,
		"song_time_at_t0": 2.0, "bpm": 120.0, "reason": "start"})
	check(g.chart.visible and not g.menu.visible, "the chart takes the stand's paper on count_in")
	check(g.chart.chart != null, "the chart loaded from the cache")
	check_eq(g.chart.current, 2, "the page opens on from_bar")
	check(g.chart.playhead_x() < 0.0, "no playhead during the count-in")
	var end := Time.get_ticks_msec() + 2000
	while not g.following and Time.get_ticks_msec() < end:
		await tree.process_frame
	await tree.process_frame
	var x1: float = g.chart.playhead_x()
	await tree.create_timer(0.25).timeout
	var x2: float = g.chart.playhead_x()
	check(x1 >= 0.0 and x2 > x1, "the playhead follows the song time (%.1f -> %.1f px)" % [x1, x2])
	check_eq(g.chart.current, 2, "in bar 2")
	_feed(r, "session.command", {"action": "stop", "from_bar": 3})
	_feed(r, "session.state", {"state": "stopped", "song_id": "chart", "bar": 2})
	check_eq(g.chart.restart_bar, 3, "Fletcher's restart bar marked")
	check(g.chart.visible and g.card.visible, "chart stays up after the cutoff, with the card beside it")
	_feed(r, "session.state", {"state": "count_in", "song_id": "chart", "t0": Time.get_ticks_usec() / 1e6 + 1.0,
		"from_bar": 3, "song_time_at_t0": 4.0, "bpm": 120.0, "reason": "restart"})
	check_eq(g.chart.restart_bar, 3, "still marked on the count-back-in")
	check_eq(g.chart.current, 3, "page on the restart bar")
	_feed(r, "session.state", {"state": "idle"})
	check(not g.chart.visible and g.menu.visible, "idle: back to the menu on the same stand")


func test_recenter_math() -> void:
	var drummer := Transform3D(Basis(Vector3.UP, deg_to_rad(40.0)), Vector3(1.9, 0.0, 1.45))
	# sat backwards: the head faces tracking +Z, 0.3 m to the side and 0.2 m back of the tracking origin
	for yaw in [180.0, 0.0, 73.0, -120.0]:
		var head := Transform3D(Basis(Vector3.UP, deg_to_rad(yaw)) * Basis(Vector3.RIGHT, deg_to_rad(-20.0)), Vector3(0.3, 1.2, 0.2))
		var o := PlayerRig.recenter_transform(drummer, head)
		var world := o * head
		check_near(world.origin.x, drummer.origin.x, 1e-5, "head over the throne x (yaw %d)" % yaw)
		check_near(world.origin.z, drummer.origin.z, 1e-5, "head over the throne z (yaw %d)" % yaw)
		check_near(world.origin.y, 1.2, 1e-5, "height from tracking (yaw %d)" % yaw)
		var f := -world.basis.z
		var want := -drummer.basis.z
		check(Vector2(f.x, f.z).normalized().dot(Vector2(want.x, want.z)) > 0.99999, "facing the drummer's front (yaw %d)" % yaw)
		check(f.y < -0.3, "pitch untouched (yaw %d)" % yaw)
		check(o.basis.y.is_equal_approx(Vector3.UP), "the origin stays level")


func test_recenter_rig_and_message() -> void:
	var r := _scene()
	check(not r.game.recenter(), "desktop: nothing to recenter")
	var rig := PlayerRig.new()
	rig.drummer_spot = r.marker_node("spot_drummer").global_transform
	rig.origin = XROrigin3D.new()
	rig.add_child(rig.origin)
	mount(rig)
	var hit := []
	rig.recentered.connect(func(): hit.append(1))
	var head := Transform3D(Basis(Vector3.UP, PI), Vector3(0.1, 1.1, -0.4))
	check(rig.recenter(head), "recentered")
	var eye: Vector3 = (rig.origin.global_transform * head).origin
	check(Vector2(eye.x, eye.z).is_equal_approx(Vector2(rig.drummer_spot.origin.x, rig.drummer_spot.origin.z)), "head over the throne")
	check_eq(hit.size(), 1, "recentered signal")
	_feed(r, "quest.recenter", {})
	check_eq(r.game.recenters, 2, "quest.recenter from the console reaches the session")
