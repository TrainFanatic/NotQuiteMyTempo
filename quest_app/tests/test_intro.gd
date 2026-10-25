extends TestCase
## Terence's entrance (scripts/intro.gd, Rehearsal.intro_enabled): he starts outside
## door_main, the door swings open, he walks to spot_terence, the band reacts, the door
## shuts behind him with a sound, he checks his watch and stares, and only then does the
## showcase or demo start. Stepped by hand at 30 fps.

const Intro := preload("res://scripts/intro.gd")
const DT := 1.0 / 30.0
## Generous bound on the whole entrance at 120 BPM (it takes about 10 s).
const MAX_SECONDS := 16.0


func _scene(sfx := false, demo := false, showcase := false, intro := true) -> Rehearsal:
	var r := Rehearsal.new()
	r.run_demo = demo
	r.run_showcase = showcase
	r.sfx_enabled = sfx
	r.cinematics_enabled = false
	r.intro_enabled = intro
	mount(r)
	return r


## One frame in the tree's order: band, Terence, the voice pool, then the intro.
func _step(r: Rehearsal) -> void:
	for m in r.band:
		m.step(DT)
	r.terence.step(DT)
	if r.sfx_pool != null:
		r.sfx_pool.step(DT)
	r.intro.step(DT)


## Steps until the entrance is over; returns the seconds it took (or INF).
func _run_to_end(r: Rehearsal) -> float:
	var t := 0.0
	while r.intro.is_running() and t < MAX_SECONDS * 2.0:
		_step(r)
		t += DT
	return t if r.intro.phase == Intro.Phase.DONE else INF


func _flat(v: Vector3) -> Vector3:
	return Vector3(v.x, 0.0, v.z)


func test_intro_starts_outside_the_door() -> void:
	var r := _scene()
	var intro: Intro = r.intro
	check(intro != null and intro.is_running(), "the entrance runs on start")
	var door := r.room.find_child("door_main", true, false) as Node3D
	var inward := door.global_transform.basis.z.normalized()
	var t := r.terence
	check(t.global_transform.is_equal_approx(intro.outside_transform()), "Terence at the outside spot")
	check_near(intro.depth_inside(), -Intro.OUTSIDE_DIST, 1e-3, "he waits behind the door's plane")
	var along := (t.global_position - door.global_position) - inward * inward.dot(t.global_position - door.global_position)
	check(_flat(along).length() < 0.6, "in line with the door leaf (%.2f m aside)" % _flat(along).length())
	check(t.global_transform.basis.z.dot(inward) > 0.999, "facing into the room")
	check_near(intro.door_angle(), 0.0, 1e-6, "door shut")
	check(door.find_child("door_main_hinge", false, false) != null, "the leaf is split off on a hinge")
	check(not intro.dark.visible, "no dark doorway while shut")
	# nothing moves in the silence before the door
	for i in int(Intro.PRE_BEATS * 0.5 / DT) - 2:
		_step(r)
	check(t.global_transform.is_equal_approx(intro.outside_transform()), "still outside before the door opens")
	check_near(intro.door_angle(), 0.0, 1e-6, "door still shut")


func test_intro_full_entrance() -> void:
	var r := _scene(true)
	var intro: Intro = r.intro
	var t := r.terence
	var door := r.room.find_child("door_main", true, false) as Node3D
	var home := t.home
	var count := [0, 0]
	intro.finished.connect(func(): count[0] += 1)
	r.intro_finished.connect(func(): count[1] += 1)
	var started: Array[StringName] = []
	t.move_started.connect(func(id): started.append(id))
	var band_states := {}
	for m in r.band:
		band_states[m.member_name] = []
		m.state_changed.connect(func(who, s): band_states[who].append(s))
	var max_angle := 0.0
	var angle_at_crossing := -1.0
	var dist_when_closing := -1.0
	var max_dist_off_plan := 0.0
	var time := 0.0
	var was_inside := false
	while intro.is_running() and time < MAX_SECONDS * 2.0:
		var prev_state := intro.door_state
		_step(r)
		time += DT
		max_angle = max(max_angle, intro.door_angle())
		if not was_inside and intro.depth_inside() > 0.0:
			was_inside = true
			angle_at_crossing = intro.door_angle()
		if prev_state == Intro.DoorState.OPEN and intro.door_state == Intro.DoorState.CLOSING:
			dist_when_closing = _flat(t.global_position - intro.hinge_position()).length()
	check_eq(intro.phase, Intro.Phase.DONE, "the entrance finishes")
	check(time < MAX_SECONDS, "within %.0f s (took %.2f s)" % [MAX_SECONDS, time])
	check(time > 6.0, "not rushed (took %.2f s)" % time)
	# the door
	check_near(max_angle, Intro.OPEN_ANGLE, 0.01, "the door swings fully open")
	check(angle_at_crossing > deg_to_rad(80.0), "the door is open when he walks through (%.0f deg)" % rad_to_deg(angle_at_crossing))
	check(dist_when_closing > intro._leaf_width + 0.3, "the door waits until he is clear of its swing (%.2f m)" % dist_when_closing)
	check_eq(intro.door_state, Intro.DoorState.SHUT, "the door is shut at the end")
	check_near(intro.door_angle(), 0.0, 1e-6, "back flush in its frame")
	check(not intro.dark.visible, "the dark doorway is hidden again")
	check(intro.door_voice != null, "the door sounds as it shuts")
	if intro.door_voice != null:
		check(intro.door_voice.stream == r.sfx_library.streams[&"door_slam"], "the sound is door_slam")
		check_near(intro.door_voice.volume_db, Intro.DOOR_SOUND_DB, 1e-4, "quieter than the storm-out slam")
		check(intro.door_voice.global_position.distance_to(door.global_position) < 1.3, "at the door")
	# Terence
	check(t.global_position.distance_to(home.origin) < 1e-3, "on spot_terence")
	check(t.global_transform.basis.z.dot(home.basis.z) > 0.9999, "with his spot's facing")
	var drummer := r.marker_node("spot_drummer")
	var to_drummer := _flat(drummer.global_position - t.global_position).normalized()
	check(t.global_transform.basis.z.dot(to_drummer) > 0.7, "turned toward the drummer")
	var head_local := Vector3(t.meta["targets"]["DRUMMER_HEAD"][0], t.meta["targets"]["DRUMMER_HEAD"][2], -t.meta["targets"]["DRUMMER_HEAD"][1])
	check(_flat(t.global_transform * head_local - drummer.global_position).length() < 0.05, "his moves' DRUMMER_HEAD target is the drummer from here")
	check(&"walk" in started, "he walks in with his walk cycle")
	check(Intro.WATCH_MOVE in started, "he checks his watch on arrival")
	check_eq(t.current_move(), Terence.HUB, "ends in the stare at the drummer")
	check(not t.is_travelling(), "standing still")
	# the band
	for m in r.band:
		var seen: Array = band_states[m.member_name]
		check(&"startle" in seen, "%s startles as he comes in" % m.member_name)
		check(&"look_down" in seen, "%s looks down at the music once he is at his spot" % m.member_name)
		check(seen.find(&"startle") < seen.rfind(&"look_down"), "%s startles first" % m.member_name)
	var startles := intro.reactions.filter(func(e): return e["reaction"] == &"startle")
	check_eq(startles.size(), r.band.size(), "one startle each")
	# the signal, once
	check_eq(count[0], 1, "Intro.finished fires once")
	check_eq(count[1], 1, "Rehearsal.intro_finished fires once")
	for i in 90:
		_step(r)
	check_eq(count[0], 1, "and not again")
	check_eq(count[1], 1, "Rehearsal's not again either")


func test_intro_follows_tempo() -> void:
	var slow := _scene()
	var t120 := _run_to_end(slow)
	var fast := _scene()
	check(fast.director.set_tempo(240.0), "tempo set")
	var t240 := _run_to_end(fast)
	check(t120 < MAX_SECONDS and t240 < MAX_SECONDS, "both finish (%.2f s, %.2f s)" % [t120, t240])
	var ratio := t240 / t120
	# beats, walk and moves all halve; clamped crossfades and the final turn keep it a little over
	check(ratio > 0.45 and ratio < 0.6, "double tempo takes about half as long (%.2f s vs %.2f s, ratio %.2f)" % [t240, t120, ratio])
	check(fast.terence.global_position.distance_to(fast.terence.home.origin) < 1e-3, "and still ends on spot_terence")


func test_demo_waits_for_the_intro() -> void:
	var r := _scene(false, true)
	check(r.injector == null, "no demo during the entrance")
	var t := 0.0
	while r.intro.is_running() and t < MAX_SECONDS:
		_step(r)
		t += DT
		if r.intro.is_running() and r.injector != null:
			check(false, "the demo started before the entrance was over")
			break
	check(r.injector != null, "the demo starts once he is in")
	if r.injector != null:
		check_eq(r.injector.time, 0.0, "from the top")
		check(not r.injector.timeline.is_empty(), "with its timeline")


func test_showcase_waits_for_the_intro() -> void:
	var r := _scene(false, false, true)
	check(r.showcase == null, "no showcase during the entrance")
	var t := 0.0
	while r.intro.is_running() and t < MAX_SECONDS:
		_step(r)
		t += DT
		if r.intro.is_running() and r.showcase != null:
			check(false, "the showcase started before the entrance was over")
			break
	check(r.showcase != null and r.showcase.running, "the showcase starts once he is in")
	if r.showcase != null:
		r.showcase.running = false


func test_intro_off_skips_it() -> void:
	var r := _scene(false, true, false, false)
	check(r.intro == null, "no entrance")
	check(r.injector != null, "the demo starts at once")
	check(r.terence.global_position.distance_to(r.marker_node("spot_terence").global_position) < 1e-3, "Terence already on his spot")
	var door := r.room.find_child("door_main", true, false) as Node3D
	check(door.find_child("door_main_hinge", false, false) == null, "the door is left as imported")


func test_intro_fails_loudly() -> void:
	var r := _scene(false, false, false, false)
	print("  (expected errors follow)")
	# no door
	var room: Node3D = load(Rehearsal.ROOM_GLB).instantiate()
	mount(room)
	room.find_child("door_main", true, false).name = "door_gone"
	var a: Intro = Intro.new()
	mount(a)
	check(not a.setup(r.terence, r.band, room, null, null), "a room without door_main is an error")
	check(not a.start(), "and it cannot start")
	# a door merged into one surface cannot swing
	var room2: Node3D = load(Rehearsal.ROOM_GLB).instantiate()
	mount(room2)
	var d := room2.find_child("door_main", true, false) as MeshInstance3D
	var merged := ArrayMesh.new()
	merged.add_surface_from_arrays(Mesh.PRIMITIVE_TRIANGLES, (d.mesh as ArrayMesh).surface_get_arrays(0))
	d.mesh = merged
	var b: Intro = Intro.new()
	mount(b)
	check(not b.setup(r.terence, r.band, room2, null, null), "a door without a separate leaf is an error")
	# sound on, but half configured
	var room3: Node3D = load(Rehearsal.ROOM_GLB).instantiate()
	mount(room3)
	var c: Intro = Intro.new()
	mount(c)
	check(not c.setup(r.terence, r.band, room3, Node3D.new(), null), "a voice pool without a library is an error")
	# the band must be at rest to react
	var room4: Node3D = load(Rehearsal.ROOM_GLB).instantiate()
	mount(room4)
	var e: Intro = Intro.new()
	mount(e)
	check(e.setup(r.terence, r.band, room4, null, null), "sets up on a good room")
	r.band[0].set_band_state(&"playing")
	r.band[0].step(1.0)
	check(not e.start(), "a band member already playing is an error")
