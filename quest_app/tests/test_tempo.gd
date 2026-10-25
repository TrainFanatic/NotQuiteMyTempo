extends TestCase
## Tempo: every animation scales with bpm / 120, walking keeps its feet, the AnimBlender
## signals report authored time, and beat-locked loops follow a loaded song's beat.

const Tempo := preload("res://scripts/tempo.gd")
const TEMPOS := [60.0, 120.0, 300.0]
const SONG := "res://tests/fixtures/type0_running_status.mid"


func _member(member: String, inst: String) -> BandMember:
	var m := BandMember.new()
	mount(m)
	m.setup(member, inst)
	m.set_process(false)
	return m


func _room() -> Node3D:
	var room: Node3D = load(Rehearsal.ROOM_GLB).instantiate()
	mount(room)
	return room


func _terence(room: Node3D) -> Terence:
	var spot := room.find_child("spot_terence", true, false) as Node3D
	var drummer := room.find_child("spot_drummer", true, false) as Node3D
	var t := Terence.new()
	mount(t)
	t.set_process(false)
	t.setup(Rehearsal.character_at(spot), drummer.global_position + Vector3.UP * Rehearsal.PLAYER_HEAD_HEIGHT, room)
	return t


## A director over Terence and a few players (no Rehearsal scene, so nothing else listens).
func _director() -> SceneDirector:
	var t := _terence(_room())
	var band: Array = [_member("band_alto_sax_1", "alto_sax_1"), _member("band_trombone_1", "trombone_1"),
		_member("band_bass_1", "upright_bass_1")]
	var d := SceneDirector.new()
	mount(d)
	d.setup(t, band)
	return d


## Records, in real (stepped) time, when `anim` starts, wraps and reaches its end on `b`.
## rec.clock must be advanced by the caller before each step.
func _recorder(b) -> Dictionary:
	var rec := {"clock": 0.0, "started": {}, "ends": {}, "wraps": {}}
	b.started.connect(func(a: StringName) -> void:
		rec["started"][a] = rec["clock"]
		rec["ends"].erase(a)
		rec["wraps"].erase(a))
	b.advanced.connect(func(a: StringName, from_pos: float, to_pos: float, wrapped: bool) -> void:
		if wrapped and not rec["wraps"].has(a):
			rec["wraps"][a] = rec["clock"]
		if not b.is_looping(a) and to_pos >= b.player.get_animation(a).length - 1e-6 and not rec["ends"].has(a):
			rec["ends"][a] = rec["clock"])
	return rec


func _step_until(node, rec: Dictionary, done: Callable, limit: float, dt: float) -> bool:
	var t := 0.0
	while not done.call() and t < limit:
		rec["clock"] += dt
		node.step(dt)
		t += dt
	return done.call()


## Real duration of one pass of `anim` (start to end, or start to first wrap for a loop).
func _duration(rec: Dictionary, anim: StringName, looping: bool) -> float:
	var stop: Dictionary = rec["wraps"] if looping else rec["ends"]
	if not rec["started"].has(anim) or not stop.has(anim):
		return -1.0
	return stop[anim] - rec["started"][anim]


func test_tempo_range_is_enforced() -> void:
	var m := _member("band_trumpet_1", "trumpet_1")
	for bad in [Tempo.MIN_BPM - 0.1, Tempo.MAX_BPM + 0.1, 0.0, -120.0, NAN]:
		check(not m.set_tempo(bad), "band rejects %s BPM" % bad)
	check_eq(m.bpm, 120.0, "a rejected tempo leaves the old one")
	check(m.set_tempo(Tempo.MIN_BPM) and m.set_tempo(Tempo.MAX_BPM), "the range ends are allowed")
	var d := _director()
	check(not d.terence.set_tempo(401.0), "Terence rejects 401 BPM")
	check(not d.set_tempo(39.0), "the director rejects 39 BPM")
	check(not d.handle_message({"type": "set_tempo", "bpm": 1000}), "and as a message")
	check_eq(d.bpm, 120.0, "director tempo unchanged")
	check_eq(d.terence.bpm, 120.0, "Terence tempo unchanged")


## Every band action, at 60, 120 and 300 BPM, lasts length * 120 / bpm (within a frame),
## and one-shots go on to their next state by themselves.
func test_every_band_action_scales() -> void:
	var dt := 1.0 / 120.0
	for bpm: float in TEMPOS:
		var m := _member("band_trombone_1", "trombone_1")
		m.set_tempo(bpm)
		var b = m.blender
		var rec := _recorder(b)
		var frame := 1.0 / 24.0 * 120.0 / bpm
		var expect := func(anim: StringName) -> float: return m.player.get_animation(anim).length * 120.0 / bpm
		var cur := func(): return m.current_state()
		# rest loops; restart it so the recorder sees its start
		b.play(&"rest", 0.0)
		_step_until(m, rec, func(): return rec["wraps"].has(&"rest"), 20.0, dt)
		check_near(_duration(rec, &"rest", true), expect.call(&"rest"), frame, "%d BPM rest loop" % bpm)
		m.set_band_state(&"playing")
		_step_until(m, rec, func(): return rec["wraps"].has(&"play_loop"), 20.0, dt)
		check_near(_duration(rec, &"raise", false), expect.call(&"raise"), frame, "%d BPM raise" % bpm)
		check_near(rec["started"].get(&"play_loop", -1.0) - rec["started"][&"raise"], expect.call(&"raise"), frame, "%d BPM raise flows into play_loop on time" % bpm)
		check_near(_duration(rec, &"play_loop", true), expect.call(&"play_loop"), frame, "%d BPM play_loop" % bpm)
		m.react(&"freeze")
		_step_until(m, rec, func(): return rec["ends"].has(&"freeze"), 10.0, dt)
		check_near(_duration(rec, &"freeze", false), expect.call(&"freeze"), frame, "%d BPM freeze" % bpm)
		_step_until(m, rec, func(): return false, 0.3, dt)
		check_eq(m.current_state(), &"freeze", "%d BPM freeze holds" % bpm)
		m.set_band_state(&"lowering")
		_step_until(m, rec, func(): return cur.call() == &"rest", 10.0, dt)
		check_near(_duration(rec, &"lower", false), expect.call(&"lower"), frame, "%d BPM lower" % bpm)
		check_eq(m.current_state(), &"rest", "%d BPM lower returns to rest" % bpm)
		for reaction in [&"startle", &"look_down", &"look_at_drummer", &"flip_page", &"spit_valve"]:
			check(m.react(reaction), "%d BPM %s" % [bpm, reaction])
			_step_until(m, rec, func(): return cur.call() == &"rest", 10.0, dt)
			check_near(_duration(rec, reaction, false), expect.call(reaction), frame, "%d BPM %s" % [bpm, reaction])
			check_near(rec["started"][&"rest"] - rec["started"][reaction], expect.call(reaction), frame, "%d BPM %s returns to rest on time" % [bpm, reaction])
			check_eq(m.current_state(), &"rest", "%d BPM %s returns to rest" % [bpm, reaction])
		m.react(&"arms_rest")
		_step_until(m, rec, func(): return rec["wraps"].has(&"arms_rest"), 20.0, dt)
		check_near(_duration(rec, &"arms_rest", true), expect.call(&"arms_rest"), frame, "%d BPM arms_rest loop" % bpm)
		m.set_band_state(&"resting")
		_step_until(m, rec, func(): return false, 0.5, dt)
		check_eq(m.current_state(), &"rest", "%d BPM out of arms_rest" % bpm)
		m.queue_free()


## Every Terence move (walk included), at 60, 120 and 300 BPM, lasts length * 120 / bpm
## (within a frame); one-shots go back to the hub on their own.
func test_every_terence_move_scales() -> void:
	var room := _room()
	var t := _terence(room)
	var dt := 1.0 / 120.0
	var rec := _recorder(t.blender)
	check(t.has_move(Terence.WALK), "the glb has a walk cycle")
	for bpm: float in TEMPOS:
		t.set_tempo(bpm)
		var frame := 1.0 / 24.0 * 120.0 / bpm
		for id in t.moves:
			var looping: bool = t.meta["moves"][String(id)]["loop"]
			t.global_transform = t._target_for(id)
			check(t.play_move(id), "%d BPM play %s" % [bpm, id])
			var want: float = t.player.get_animation(id).length * 120.0 / bpm
			var done := func(): return (rec["wraps"] if looping else rec["ends"]).has(id)
			_step_until(t, rec, done, want + 1.0, dt)
			check_near(_duration(rec, id, looping), want, frame, "%d BPM %s lasts %.3f s" % [bpm, id, want])
			if not looping:
				_step_until(t, rec, func(): return t.current_move() != id, 1.0, dt)
				var back := t.current_move() == Terence.HUB or (t.is_travelling() and t.queued_move() == Terence.HUB)
				check(back, "%d BPM %s returns to the hub (now %s)" % [bpm, id, t.current_move()])
			t.global_transform = t.home
			t.stop()
			_step_until(t, rec, func(): return false, 0.05, dt)
			check(not t.is_travelling() and t.current_move() == Terence.HUB, "%d BPM back at the hub after %s" % [bpm, id])


## A t5_rhythm_slaps impact at frame f lands f / 24 * 120 / bpm seconds after the start.
func test_one_shot_events_stay_proportional() -> void:
	var room := _room()
	var t := _terence(room)
	var dt := 1.0 / 240.0
	var impacts: Array = t.meta["moves"]["t5_rhythm_slaps"]["impacts"]
	for bpm: float in TEMPOS:
		t.set_tempo(bpm)
		t.global_transform = t._target_for(&"t5_rhythm_slaps")
		var hits: Array = []
		var clock := {"t": 0.0, "start": -1.0}
		var on_start := func(a: StringName) -> void:
			if a == &"t5_rhythm_slaps":
				clock["start"] = clock["t"]
		var on_adv := func(a: StringName, from_pos: float, to_pos: float, _w: bool) -> void:
			if a != &"t5_rhythm_slaps":
				return
			for f in impacts:
				var at: float = f / 24.0
				if from_pos < at and at <= to_pos:
					# interpolate inside the step: authored time runs at bpm / 120
					hits.append(clock["t"] - (to_pos - at) * 120.0 / bpm - clock["start"])
		t.blender.started.connect(on_start)
		t.blender.advanced.connect(on_adv)
		t.play_move(&"t5_rhythm_slaps")
		var limit := 5.0 * 120.0 / bpm
		while clock["t"] < limit and hits.size() < impacts.size():
			clock["t"] += dt
			t.step(dt)
		t.blender.started.disconnect(on_start)
		t.blender.advanced.disconnect(on_adv)
		check_eq(hits.size(), impacts.size(), "%d BPM every impact reported once" % bpm)
		for i in min(hits.size(), impacts.size()):
			check_near(hits[i], impacts[i] / 24.0 * 120.0 / bpm, 1e-4, "%d BPM impact at frame %d" % [bpm, impacts[i]])
		t.global_transform = t.home
		t.stop()
		t.step(dt)


## His travel speed follows the walk cycle's playback rate: node speed over anim rate is the
## cycle's authored ground speed at every tempo, so the feet never slide.
func test_walk_scales_without_sliding() -> void:
	var room := _room()
	var t := _terence(room)
	var dt := 1.0 / 60.0
	var grip: float = t.meta["moves"]["walk"]["speed_mps"]
	check_near(t.walk_speed_mps, grip, 1e-6, "ground speed read from moves.json")
	for bpm: float in TEMPOS:
		for leg in [[&"t3_loom", Terence.WALK_SPEED], [&"t5_rhythm_slaps", Terence.WALK_SPEED_AGGRESSIVE]]:
			t.global_transform = t.home
			t.stop()
			t.set_tempo(bpm)
			var anim_step := {"d": 0.0}
			var on_adv := func(a: StringName, from_pos: float, to_pos: float, wrapped: bool) -> void:
				if a == Terence.WALK:
					anim_step["d"] = to_pos - from_pos + (t.blender.length() if wrapped else 0.0)
			t.blender.advanced.connect(on_adv)
			check(t.play_move(leg[0]), "%d BPM walk to %s" % [bpm, leg[0]])
			var ratios: Array = []
			var fastest := 0.0
			var prev := t.global_position
			var time := 0.0
			while t.is_travelling() and time < 60.0:
				anim_step["d"] = 0.0
				t.step(dt)
				time += dt
				var moved := Vector2(t.global_position.x - prev.x, t.global_position.z - prev.z).length()
				prev = t.global_position
				fastest = max(fastest, moved / dt)
				if moved > 1e-5 and anim_step["d"] > 0.0 and t.current_move() == Terence.WALK:
					ratios.append(moved / anim_step["d"])
			t.blender.advanced.disconnect(on_adv)
			check(not t.is_travelling(), "%d BPM arrived at %s" % [bpm, leg[0]])
			var tag := "%d BPM to %s" % [bpm, leg[0]]
			check_near(fastest, leg[1] * bpm / 120.0, 0.01 * leg[1] * bpm / 120.0, "%s: travel speed scales with tempo" % tag)
			check(not ratios.is_empty(), "%s: walked" % tag)
			if ratios.is_empty():
				continue
			check_near(ratios.max(), grip, grip * 0.005, "%s: metres per second of walk cycle is its ground speed" % tag)
			var full := ratios.filter(func(r): return r > grip * 0.995)
			check(full.size() > 0.8 * ratios.size(), "%s: %d of %d strides at the cycle's ground speed (the rest are corners)" % [tag, full.size(), ratios.size()])


## advanced reports authored positions with from == the previous to, sums to the authored
## time stepped, and wraps on loops at high tempo; started fires on (re)starts.
func test_blender_signals() -> void:
	var m := _member("band_alto_sax_1", "alto_sax_1")
	var b = m.blender
	var events: Array = []
	var starts: Array = []
	b.advanced.connect(func(a, f, to, w): events.append([a, f, to, w]))
	b.started.connect(func(a): starts.append(a))
	m.set_tempo(400.0)
	m.set_band_state(&"playing")
	check_eq(starts, [&"raise"], "started on raise")
	var dt := 1.0 / 30.0
	for i in 60:
		m.step(dt)
	check_eq(starts, [&"raise", &"play_loop"], "started on play_loop when the raise is over")
	var loop_len: float = m.player.get_animation(&"play_loop").length
	var rate := 400.0 / 120.0
	var wraps := 0
	var prev_to := -1.0
	var ok_steps := true
	var ok_chain := true
	for e in events:
		if e[0] != &"play_loop":
			continue
		var moved: float = (e[2] - e[1]) + (loop_len if e[3] else 0.0)
		ok_steps = ok_steps and abs(moved - dt * rate) < 1e-4
		if prev_to >= 0.0:
			ok_chain = ok_chain and abs(e[1] - prev_to) < 1e-4
		prev_to = e[2]
		if e[3]:
			wraps += 1
			check(e[1] > e[2], "a wrap goes from the end round to the start (%.3f -> %.3f)" % [e[1], e[2]])
	check(ok_steps, "each play_loop step reports dt * bpm / 120 of authored time")
	check(ok_chain, "each step starts where the last one ended")
	check(wraps >= 2, "play_loop wrapped at 400 BPM (%d times)" % wraps)
	var raise_events := events.filter(func(e): return e[0] == &"raise")
	check(not raise_events.is_empty() and raise_events[-1][2] == m.player.get_animation(&"raise").length, "raise reports its end")
	check(raise_events.all(func(e): return not e[3]), "a one-shot never wraps")
	var n := events.size()
	b.advance(0.0)
	check_eq(events.size(), n, "no event for a zero step")
	# restarting the current animation starts it again
	b.play(&"play_loop", 0.1)
	check_eq(starts[-1], &"play_loop", "restart emits started")
	m.step(dt)
	check_near(events[-1][1], 0.0, 1e-6, "a restart reports from 0")


## With the type 0 fixture loaded (120 BPM, 150 from beat 4), play_loop and t0_conduct
## follow the song's beat through the tempo change, and correct gently after a seek.
func test_loops_lock_to_the_song_beat() -> void:
	var d := _director()
	var band: Array = d.members.values()
	var smf := SMFParser.parse_file(SONG)
	var tm: Array = smf["tempo_map"]
	var div: int = smf["division"]
	check_near(SMFParser.seconds_to_beats(2.0, tm, div), 4.0, 1e-6, "beat 4 at 2.0 s")
	check_near(SMFParser.seconds_to_beats(2.4, tm, div), 5.0, 1e-6, "150 BPM after it")
	check_near(SMFParser.seconds_to_beats(1.0, tm, div), 2.0, 1e-6, "120 BPM before it")
	# free-running before a song: no beat lock
	check(not band[0].is_beat_locked() and not d.terence.is_beat_locked(), "no lock without a song")
	check(d.load_song(SONG, {}), "song loads")
	check(band.all(func(m): return m.is_beat_locked()) and d.terence.is_beat_locked(), "every character follows the song")
	d.set_band_state("all", &"playing")
	d.play_move(&"t0_conduct")
	var watch: Array = band.duplicate()
	var blenders: Array = watch.map(func(m): return m.blender)
	blenders.append(d.terence.blender)
	var names: Array = watch.map(func(m): return m.member_name)
	names.append("Terence")
	var locked: Array = [&"play_loop", &"play_loop", &"play_loop", &"t0_conduct"]
	var steps: Array = []
	for b in blenders:
		var moved: Array = []
		steps.append(moved)
		b.advanced.connect(func(a, f, to, w):
			moved.append((to - f) + (b.length() if w else 0.0) if a == b.current else -1.0))
	var dt := 1.0 / 60.0
	var t := 0.0
	var offset := 0.0
	var worst := [0.0, 0.0, 0.0, 0.0]
	var worst_after_seek := [0.0, 0.0, 0.0, 0.0]
	var seek_err := [0.0, 0.0, 0.0, 0.0]
	var gentle := true
	var seen_150 := false
	while t < 9.0:
		if t >= 5.0 and offset == 0.0:
			offset = 0.25     # the song seeks a quarter second ahead: half a beat at 150 BPM
		d.set_song_position(t + offset)
		for moved in steps:
			moved.clear()
		for m in band:
			m.step(dt)
		d.terence.step(dt)
		t += dt
		seen_150 = seen_150 or band[0].bpm == 150.0
		var beat := SMFParser.seconds_to_beats(t + offset, tm, div)
		var rate := d.bpm / 120.0
		for i in blenders.size():
			var b = blenders[i]
			if b.current != locked[i] or t < 0.7:
				continue
			var length: float = b.length()
			var err: float = abs(fposmod(Tempo.locked_position(beat, length) - b.position() + length / 2.0, length) - length / 2.0)
			for s in steps[i]:
				if s >= 0.0:
					gentle = gentle and abs(s / (dt * rate) - 1.0) <= Tempo.LOCK_MAX_DEV + 0.02
			if t < 5.0:
				worst[i] = max(worst[i], err)
			elif t < 5.1:
				seek_err[i] = max(seek_err[i], err)
			elif t > 7.0:
				worst_after_seek[i] = max(worst_after_seek[i], err)
	check(seen_150, "the tempo map's change to 150 BPM reached the band")
	check_eq(d.bpm, 150.0, "director follows the tempo map")
	for i in blenders.size():
		# authored seconds; 0.02 s is 1/25 of a beat
		check(worst[i] < 0.02, "%s %s within 0.02 s of the song beat through the tempo change (worst %.4f s)" % [names[i], locked[i], worst[i]])
		check(seek_err[i] > 0.25, "%s: the seek put it %.3f s off the beat" % [names[i], seek_err[i]])
		check(worst_after_seek[i] < 0.02, "%s back on the beat within 2 s of a seek (worst %.4f s)" % [names[i], worst_after_seek[i]])
	check(gentle, "beat-lock corrections never move a loop more than %.0f%% off tempo" % (Tempo.LOCK_MAX_DEV * 100.0))


## prop_anchor follows the prop: the attachment with nothing in hand, the chair while it is
## carried, the same chair once thrown into the room.
func test_prop_anchor() -> void:
	var room := _room()
	var t := _terence(room)
	var att: Node3D = t._attachments["PropR"]
	check(t.prop_anchor("PropR") == att, "no prop: the bone attachment")
	t.play_move(&"t5_chair_throw")
	t.step(1.0 / 30.0)
	var chair := t.prop_anchor("PropR")
	check(chair != att and chair.get_parent() == att, "carried: the chair on PropR")
	check(t.prop_anchor("PropL") == t._attachments["PropL"], "PropL is still empty")
	var time := 0.0
	while not t._props.any(func(rec): return rec["landed"]) and time < 4.0:
		t.step(1.0 / 30.0)
		time += 1.0 / 30.0
	check(t.prop_anchor("PropR") == chair, "thrown: still the chair")
	check(chair.get_parent() == t.get_parent(), "now in the room")
	check(t.prop_anchor("Head") == null, "unknown bone is an error")
	check(t.skeleton != null and t.blender != null, "skeleton and blender exposed")
