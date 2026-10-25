extends TestCase
## The judge demo's replayer (scripts/game/demo_hub.gd) on the packed session in res://demo/.

const SongCache := preload("res://scripts/game/song_cache.gd")


func _demo() -> DemoHub:
	var d := DemoHub.new()
	mount(d)
	var kit := Node3D.new()
	mount(kit)
	d.drum_anchor = kit
	return d


func test_demo_pack_loads() -> void:
	var d := _demo()
	check(d.frames.size() > 100, "the recorded session loaded (%d frames)" % d.frames.size())
	check_eq(d.takes.size(), 2, "a sloppy and a clean take")
	check_eq(str(d.song.get("id", "")), "caravan-head", "the demo song")
	check(not d.frames.any(func(f): return str(f.get("src", "")) == "quest"), "no headset frames in the replay")
	var types := d.frames.map(func(f): return str(f["type"]))
	for t in ["library.songs", "judgment.note", "fletcher.state", "fletcher.speech", "session.result"]:
		check(t in types, "the replay has %s" % t)
	var stops := d.frames.filter(func(f): return f["type"] == "session.command" and f["data"].get("action") == "stop")
	check_eq(stops.size(), 1, "one cutoff")


func test_song_cache_seeded() -> void:
	var d := _demo()
	var listed: Dictionary = {}
	for f in d.frames:
		if f["type"] == "library.songs":
			listed = f["data"]["songs"][0]
			break
	check(SongCache.is_cached(listed), "the game finds the song in its cache without a download")


func test_replay_runs_on_the_clock() -> void:
	var d := _demo()
	var got := []
	d.message_received.connect(func(type, data, _env): got.append([type, data]))
	var opened := [false]
	d.connected.connect(func(): opened[0] = true)
	d.start()
	check(opened[0] and d.is_open(), "the demo hub opens on start")
	await tree.create_timer(DemoHub.LEAD_S + 0.2).timeout
	check(got.size() >= 1, "the first frames arrive on schedule")
	check(got.size() < d.frames.size(), "not all at once")
	# laptop timestamps map onto this device's clock for this pass
	var first: float = d.frames[0]["t"]
	check_near(d.to_local_time(first), d._offset + first, 1e-6, "to_local_time uses the replay offset")


func test_only_requested_by_feature_or_arg() -> void:
	check(not DemoHub.requested(), "off unless the nqmt_demo feature or --judge-demo is given")
