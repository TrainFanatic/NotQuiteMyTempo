extends TestCase
## Fletcher in the rehearsal (scripts/fletcher, Rehearsal.fletcher_enabled). The hub points
## at a port where nothing listens, so it only retries; hub messages are fed in through its
## message_received signal, the same path a real frame takes. FletcherVoice and FletcherLink
## time everything on the wall clock, so these tests run real frames (not stepped by hand).

const FletcherSetup := preload("res://scripts/fletcher/fletcher_setup.gd")
const NO_SERVER := "ws://127.0.0.1:9"
const BARK := "again_1"          # any clip in res://fletcher_barks
const DT := 1.0 / 30.0


func _scene(intro := false, sfx := false) -> Rehearsal:
	var r := Rehearsal.new()
	r.run_demo = true            # Fletcher must take over from the demo
	r.sfx_enabled = sfx
	r.cinematics_enabled = false
	r.intro_enabled = intro
	r.fletcher_enabled = true
	r.fletcher_hub_url = NO_SERVER
	mount(r)
	return r


func _feed(r: Rehearsal, type: String, data: Dictionary) -> void:
	r.fletcher.hub.message_received.emit(type, data, {"type": type, "src": "test", "data": data})


## Real frames until `cond` holds or `timeout` wall-clock seconds pass. True if it held.
func _wait_for(cond: Callable, timeout: float) -> bool:
	var end := Time.get_ticks_msec() + int(timeout * 1000.0)
	while Time.get_ticks_msec() < end:
		if cond.call():
			return true
		await tree.process_frame
	return cond.call()


func _started(r: Rehearsal) -> Array:
	var started := []
	r.terence.move_started.connect(func(id): started.append(String(id)))
	return started


func test_fletcher_assembles() -> void:
	var r := _scene(false, true)
	var f: FletcherSetup = r.fletcher
	if not check(f != null, "Fletcher set up"):
		return
	check(r.injector == null and r.showcase == null, "no demo or showcase while Fletcher drives Terence")
	check_eq(f.hub.url, NO_SERVER, "hub URL from fletcher_hub_url")
	check(not f.is_held() and f.link.is_inside_tree(), "link live without the intro")
	check_eq(f.voice.get_parent(), f.head, "voice on the head attachment")
	check_eq(f.head.get_parent(), r.terence.skeleton, "head attachment on Terence's skeleton")
	check_eq(f.head.bone_name, "Head", "on the Head bone")
	var players := f.voice.find_children("*", "AudioStreamPlayer3D", false, false)
	check(players.size() >= 2, "voice has its players")
	check(players.all(func(p): return p.bus == &"Voice"), "every Fletcher player on the Voice bus")
	check(r.sfx_pool.voices.all(func(v): return v.bus == &"SFX" and not v in players), "SFX pool keeps its own voices on SFX")
	await tree.process_frame
	check(f.voice.global_position.distance_to(r.terence.global_position) > 1.5, "voice up at his head (%.2f m over his feet)" % (f.voice.global_position.y - r.terence.global_position.y))


func test_bark_cue_plays_its_move() -> void:
	var r := _scene()
	var started := _started(r)
	# the cue comes first, as it can from Fletcher; its anchor is 0.1 s into the bark
	_feed(r, "fletcher.state", {"mood": "angry", "anger": 0.6, "action": "point", "look_at": "drummer",
		"gesture": "slam_stand", "utterance_id": "b1", "at_s": 0.1, "priority": 1})
	await tree.process_frame
	check(not "t1_fist_slam" in started, "nothing before the bark")
	var heard := [false]
	r.fletcher.voice.speaking.connect(func(uid): heard[0] = heard[0] or uid == "b1")
	_feed(r, "fletcher.speech", {"utterance_id": "b1", "clip_id": BARK, "text": "Again."})
	check(await _wait_for(func(): return "t1_fist_slam" in started, 2.0), "slam_stand -> t1_fist_slam (started %s)" % [started])
	check(heard[0], "the bark sounded")
	check_eq(String(r.terence.current_move()), "t1_fist_slam", "Terence plays it")


func test_fist_cutoff_state() -> void:
	var r := _scene()
	var started := _started(r)
	_feed(r, "fletcher.state", {"mood": "furious", "anger": 0.95, "action": "fist_cutoff", "look_at": "drummer"})
	check(await _wait_for(func(): return "t1_fist_cutoff" in started, 1.0), "fist_cutoff -> t1_fist_cutoff (started %s)" % [started])


func test_countoff_cue_raises_the_finger() -> void:
	var r := _scene()
	var started := _started(r)
	var words := []
	r.fletcher.voice.countoff_word.connect(func(c): words.append(c))
	var bpm := 120.0
	# t0 = the downbeat after "eight"; the finger starts 5 beats before it (0.3 s from now).
	# With no hub frames yet the clock offset is unknown, so laptop time = local time.
	var t0 := Time.get_ticks_usec() / 1e6 + 5.0 * 60.0 / bpm + 0.3
	_feed(r, "fletcher.state", {"mood": "cold", "anger": 0.4, "action": "glare", "look_at": "drummer",
		"gesture": "t0_countoff_finger", "utterance_id": "c1", "t0": t0, "bpm": bpm, "priority": 2,
		"clips": ["co_five_sharp", "co_six_sharp", "co_seven_sharp", "co_eight_sharp"]})
	await tree.process_frame
	check(not "t0_countoff_finger" in started, "the finger waits for t0 - 5 beats")
	check(await _wait_for(func(): return "t0_countoff_finger" in started, 1.5), "count-off cue -> t0_countoff_finger (started %s)" % [started])
	check(await _wait_for(func(): return words.size() == 4, 3.0), "the four count words played (%s)" % [words])


func test_intro_holds_fletcher() -> void:
	var r := _scene(true)
	var f: FletcherSetup = r.fletcher
	var started := _started(r)
	check(f.is_held() and not f.link.is_inside_tree(), "held while the intro runs")
	_feed(r, "session.state", {"state": "idle", "bpm": 100})
	_feed(r, "fletcher.state", {"mood": "angry", "anger": 0.6, "action": "point", "look_at": "drummer",
		"gesture": "slam_stand", "utterance_id": "b1", "at_s": 0.0})
	_feed(r, "fletcher.speech", {"utterance_id": "b1", "clip_id": BARK, "text": "Again."})
	var spoke := [false]
	f.voice.finished.connect(func(uid): spoke[0] = spoke[0] or uid == "b1")
	check(await _wait_for(func(): return spoke[0], 3.0), "he can still talk during the intro")
	check(not "t1_fist_slam" in started, "but his cue does not move Terence")
	check_eq(r.terence.bpm, 120.0, "tempo held back too")
	# run the rest of the entrance by hand, as test_intro does
	var t := 0.0
	while r.intro.is_running() and t < 40.0:
		for m in r.band:
			m.step(DT)
		r.terence.step(DT)
		r.intro.step(DT)
		t += DT
	check(not f.is_held() and f.link.is_inside_tree(), "released on intro_finished")
	check(r.injector == null, "no demo after the intro either")
	check_eq(f.link.bpm, 100.0, "the held session.state reached the link")
	check_eq(r.terence.bpm, 100.0, "and Terence's tempo")
	_feed(r, "fletcher.state", {"mood": "angry", "anger": 0.7, "action": "point", "look_at": "drummer",
		"gesture": "slam_stand", "utterance_id": "b2", "at_s": 0.0})
	_feed(r, "fletcher.speech", {"utterance_id": "b2", "clip_id": BARK, "text": "Again."})
	check(await _wait_for(func(): return "t1_fist_slam" in started, 2.0), "after the intro the cue moves him (started %s)" % [started])


func test_cmdline_parsing() -> void:
	# no user args in the test run: Fletcher stays off unless the scene asks for it
	check(not FletcherSetup.requested(), "not requested without -- --fletcher")
	check_eq(FletcherSetup.requested_url(), "", "no URL without -- --fletcher")
	var r := Rehearsal.new()
	r.run_demo = false
	r.sfx_enabled = false
	r.intro_enabled = false
	mount(r)
	check(r.fletcher == null, "fletcher_enabled defaults off: Adam's behaviour unchanged")
