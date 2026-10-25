extends TestCase
## C3: band and Terence state machines, instrument and prop attachment.


func _member(name: String, inst: String) -> BandMember:
	var m := BandMember.new()
	mount(m)
	m.setup(name, inst)
	m.set_process(false)   # the test steps time itself
	return m


## Steps animation time, then lets one frame run so BoneAttachment3D nodes re-sync.
func _run(node, seconds: float, dt: float = 1.0 / 30.0) -> void:
	var t := 0.0
	while t < seconds:
		node.step(dt)
		t += dt
	await tree.process_frame


func _bone_pos(skel: Skeleton3D, bone: String) -> Vector3:
	return skel.global_transform * skel.get_bone_global_pose(skel.find_bone(bone)).origin


func test_band_cycle() -> void:
	var m := _member("band_alto_sax_1", "alto_sax_1")
	check_eq(m.current_state(), &"rest", "starts resting")
	m.set_band_state(&"playing")
	await _run(m, 1.0)
	check_eq(m.current_state(), &"play_loop", "raise flows into play_loop")
	await _run(m, 4.5)
	check_eq(m.current_state(), &"play_loop", "play_loop keeps looping")
	m.set_band_state(&"lowering")
	await _run(m, 1.0)
	check_eq(m.current_state(), &"rest", "lower returns to rest")


func test_sax_reaches_the_mouth() -> void:
	var m := _member("band_alto_sax_1", "alto_sax_1")
	m.set_band_state(&"playing")
	await _run(m, 1.0)
	var head := _bone_pos(m.skeleton, "Head")
	var mouthpiece: Vector3 = m.instrument.global_position
	var fwd := m.global_transform.basis.z
	check(mouthpiece.distance_to(head) < 0.2, "mouthpiece within 20 cm of the head joint (%.3f)" % mouthpiece.distance_to(head))
	check((mouthpiece - head).dot(fwd) > 0.05, "mouthpiece is in front of the face")
	var aabb_center := _instrument_center(m.instrument)
	check(aabb_center.y < head.y - 0.15, "sax body hangs below the mouth")


func test_instrument_on_lap_at_rest() -> void:
	var m := _member("band_trumpet_1", "trumpet_1")
	await _run(m, 0.5)
	var hips := _bone_pos(m.skeleton, "Hips")
	check(abs(m.instrument.global_position.y - hips.y) < 0.35, "trumpet near lap height at rest")


func test_trombone_slide_moves_along_its_axis() -> void:
	var m := _member("band_trombone_1", "trombone_1")
	check(m.slide != null, "slide split onto InstrumentSlide")
	m.set_band_state(&"playing")
	await _run(m, 0.8)
	var offsets: Array = []
	for i in 8:
		await _run(m, 0.25)
		var local := m.instrument.global_transform.affine_inverse() * m.slide.global_position
		offsets.append(local)
	var ys: Array = offsets.map(func(v): return v.y)
	var zs: Array = offsets.map(func(v): return v.z)
	var span: float = zs.max() - zs.min()
	check(span > 0.15, "slide travels along the instrument axis (%.3f m)" % span)
	check(ys.max() - ys.min() < 0.01, "slide stays on its axis")


func test_reactions() -> void:
	var m := _member("band_trombone_2", "trombone_2")
	check(m.react(&"look_at_drummer"), "look_at_drummer from rest")
	await _run(m, 0.3)
	check_eq(m.current_state(), &"look_at_drummer", "in reaction")
	await _run(m, 2.0)
	check_eq(m.current_state(), &"rest", "one-shot reaction returns to rest")
	check(m.react(&"spit_valve"), "trombones have spit_valve")
	check(not m.react(&"freeze") or false, "freeze isn't allowed from rest")
	check(not m.react(&"juggle"), "unknown reaction is rejected")


func test_tempo_scales_time() -> void:
	var m := _member("band_alto_sax_2", "alto_sax_2")
	m.set_tempo(240.0)
	m.set_band_state(&"playing")
	await _run(m, 0.5)
	check_eq(m.current_state(), &"play_loop", "at 240 BPM the 0.5 s raise is done in 0.25 s")


func _room() -> Node3D:
	var room: Node3D = load(Rehearsal.ROOM_GLB).instantiate()
	mount(room)
	return room


## Terence on spot_terence of `room`, the player's head over spot_drummer, as Rehearsal does.
func _terence(room: Node3D) -> Terence:
	var spot := room.find_child("spot_terence", true, false) as Node3D
	var drummer := room.find_child("spot_drummer", true, false) as Node3D
	var t := Terence.new()
	mount(t)
	t.set_process(false)
	t.setup(Rehearsal.character_at(spot), drummer.global_position + Vector3.UP * Rehearsal.PLAYER_HEAD_HEIGHT, room)
	return t


## Steps until `done` returns true (or `limit` seconds pass); returns the time taken.
func _run_until(node, done: Callable, limit: float, dt: float = 1.0 / 30.0) -> float:
	var t := 0.0
	while not done.call() and t < limit:
		node.step(dt)
		t += dt
	await tree.process_frame
	return t


## Every bone's position in skeleton space.
func _pose(skel: Skeleton3D) -> PackedVector3Array:
	var out := PackedVector3Array()
	for i in skel.get_bone_count():
		out.append(skel.get_bone_global_pose(i).origin)
	return out


## Largest single-bone distance between two poses.
func _pose_dist(a: PackedVector3Array, b: PackedVector3Array) -> float:
	var d := 0.0
	for i in a.size():
		d = max(d, a[i].distance_to(b[i]))
	return d


## Switching at `t` from what `a` plays now: `a` was switched by the code under test,
## `cut` was switched the same way and then hard-cut (no blend), `stay` kept the old move.
## Checks the switch blends: no jump on the first frame, both moves visible in the mix,
## and the new move fully in once the fade is over.
func _check_crossfade(what: String, a, cut, stay, p0: PackedVector3Array, fade: float) -> void:
	var dt := 1.0 / 60.0
	for n in [a, cut, stay]:
		n.step(dt)
	var jump_cut := _pose_dist(_pose(cut.skeleton), p0)
	var jump := _pose_dist(_pose(a.skeleton), p0)
	check(jump_cut > 0.05, "%s: a hard cut would jump (%.3f m)" % [what, jump_cut])
	check(jump < 0.3 * jump_cut, "%s: first frame after the switch moves %.3f m, a cut %.3f m" % [what, jump, jump_cut])
	var max_step := 0.0
	var prev := _pose(a.skeleton)
	var mixed := false
	var t := dt
	while t < fade + 0.1:
		for n in [a, cut, stay]:
			n.step(dt)
		t += dt
		var now := _pose(a.skeleton)
		max_step = max(max_step, _pose_dist(now, prev))
		prev = now
		if t > fade * 0.3 and t < fade * 0.7:
			var to_old := _pose_dist(now, _pose(stay.skeleton))
			var to_new := _pose_dist(now, _pose(cut.skeleton))
			mixed = mixed or (to_old > 0.01 and to_new > 0.01)
	check(mixed, "%s: half way through the fade both moves have weight" % what)
	check(max_step < 0.5 * jump_cut, "%s: no frame jumps (largest step %.3f m, cut %.3f m)" % [what, max_step, jump_cut])
	check(_pose_dist(_pose(a.skeleton), _pose(cut.skeleton)) < 0.005, "%s: the new move is fully in after the fade" % what)


## Steps until the animation changes away from `from`; then measures how smoothly it goes
## on: the largest per-frame step must be a small part of the whole change.
func _check_smooth_return(what: String, n, from: StringName, to: StringName, limit: float) -> void:
	var dt := 1.0 / 60.0
	var t := 0.0
	var current: Callable = n.current_move if n is Terence else n.current_state
	while current.call() == from and t < limit:
		n.step(dt)
		t += dt
	check_eq(current.call(), to, "%s returns to %s on its own" % [what, to])
	var p_end := _pose(n.skeleton)
	var prev := p_end
	var max_step := 0.0
	var s := 0.0
	while s < 0.5:
		n.step(dt)
		s += dt
		var now := _pose(n.skeleton)
		max_step = max(max_step, _pose_dist(now, prev))
		prev = now
	var gap := _pose_dist(prev, p_end)
	check(gap > 0.03, "%s: the end pose differs from %s (%.3f m)" % [what, to, gap])
	check(max_step < 0.35 * gap, "%s: the return is blended (largest step %.3f m of %.3f m)" % [what, max_step, gap])


func test_terence_every_move() -> void:
	var room := _room()
	var t := _terence(room)
	check_eq(t.moves.size(), t.meta["moves"].size(), "one playable animation per move")
	check(t.moves.size() >= 45, "45 moves (plus walk once the glb has it)")
	var at_home := func(): return not t.is_travelling() and t.global_position.distance_to(t.home.origin) < 0.05
	for id in t.moves:
		if id == Terence.HUB:
			continue
		check(t.play_move(id), "play %s" % id)
		await _run_until(t, func(): return not t.is_travelling(), 20.0)
		await _run(t, 0.2)
		check_eq(t.current_move(), id, "%s is playing" % id)
		if not t.meta["moves"][String(id)]["loop"]:
			await _run(t, float(t.meta["moves"][String(id)]["seconds"]) + 0.6)
			await _run_until(t, at_home, 20.0)
			check_eq(t.current_move(), Terence.HUB, "%s returns to the hub" % id)
		else:
			t.stop()
			await _run_until(t, at_home, 20.0)
			await _run(t, 0.6)
		check(at_home.call(), "back home after %s" % id)
	check(not t.play_move(&"t9_nonsense"), "unknown move rejected")


func test_terence_props_and_close_placement() -> void:
	var room := _room()
	var t := _terence(room)
	var head := t.player_head
	t.play_move(&"t5_chair_throw")
	await _run(t, 0.2)
	check_eq(t._attachments["PropR"].get_child_count(), 1, "chair attached to PropR")
	check(t.play_move(&"t5_cowbell"), "cowbell queued")
	check(t.is_travelling(), "cowbell is a close move: he walks over first")
	await _run(t, 0.2)
	check_eq(t._attachments["PropR"].get_child_count(), 0, "the unthrown chair goes when he sets off")
	check_eq(t._attachments["PropL"].get_child_count(), 0, "no cowbell before he arrives")
	await _run_until(t, func(): return not t.is_travelling(), 20.0)
	await _run(t, 0.2)
	check_eq(t.current_move(), &"t5_cowbell", "cowbell plays on arrival")
	check_eq(t._attachments["PropL"].get_child_count(), 1, "cowbell on PropL")
	check(t._attachments["PropR"].get_children().any(func(c): return String(c.name).begins_with("cowbell_stick")), "stick on PropR")
	var d := Vector2(t.global_position.x - head.x, t.global_position.z - head.z).length()
	check_near(d, Terence.NEAR_DIST, 0.05, "close move stands NEAR_DIST from the player")
	var to_player := (Vector3(head.x, 0, head.z) - t.global_position).normalized()
	check(t.global_transform.basis.z.dot(to_player) > 0.99, "faces the player")
	t.stop()
	await _run_until(t, func(): return not t.is_travelling(), 20.0)
	check(t.global_position.distance_to(t.home.origin) < 0.05, "walked back home")
	check_eq(t.current_move(), Terence.HUB, "at the hub")
	check_eq(t._attachments["PropL"].get_child_count(), 0, "props cleared at the hub")


func test_terence_crossfades_mid_move() -> void:
	var room := _room()
	var a := _terence(room)
	var cut := _terence(room)
	var stay := _terence(room)
	for n in [a, cut, stay]:
		n.play_move(&"t1_wave_off")
		_steps(n, 0.6)
	var p0 := _pose(a.skeleton)
	check_eq(a.current_move(), &"t1_wave_off", "half way through a one-shot")
	a.play_move(&"t2_point_player")
	cut.play_move(&"t2_point_player")
	cut.player.play(&"t2_point_player", 0.0)   # reference: the old hard cut
	check_eq(a.current_move(), &"t2_point_player", "the interrupting move takes over at once")
	_check_crossfade("one-shot into one-shot", a, cut, stay, p0, Terence.XFADE)
	# a looping move into a one-shot, and the same move restarted
	for n in [a, cut, stay]:
		n.play_move(&"t0_conduct")
		_steps(n, 1.3)
	p0 = _pose(a.skeleton)
	a.play_move(&"t0_conduct")
	cut.play_move(&"t0_conduct")
	cut.player.play(&"t0_conduct", 0.0)
	_check_crossfade("restarting a looping move", a, cut, stay, p0, Terence.XFADE)


func test_terence_one_shot_returns_blended() -> void:
	var room := _room()
	var t := _terence(room)
	t.play_move(&"t1_fist_cutoff")
	_steps(t, 0.2)
	_check_smooth_return("t1_fist_cutoff", t, &"t1_fist_cutoff", Terence.HUB, 3.0)


func test_terence_walks_around_the_band() -> void:
	var room := _room()
	var t := _terence(room)
	var seats: Array = room.find_children("seat_*", "Node3D", true, false).map(func(s): return (s as Node3D).global_position)
	check_eq(seats.size(), 16, "16 seats to keep clear of")
	var clearance := func(p: Vector3) -> float:
		var c := INF
		for s in seats:
			c = min(c, Vector2(p.x - s.x, p.z - s.z).length())
		return c
	var target: Transform3D = t._target_for(&"t3_loom")
	var straight := INF
	for i in 21:
		straight = min(straight, clearance.call(t.global_position.lerp(target.origin, i / 20.0)))
	check(straight < 0.5, "the straight line would brush the band (%.2f m)" % straight)
	check(t.play_move(&"t3_loom"), "close move accepted")
	check(t.is_travelling(), "he walks there")
	check_eq(t.queued_move(), &"t3_loom", "the move waits for him")
	var walk := Terence.WALK if t.has_move(Terence.WALK) else Terence.HUB
	for leg in [[&"t3_loom", target], [&"stop", t.home]]:
		if leg[0] == &"stop":
			t.stop()
		var dt := 1.0 / 30.0
		var min_clear := INF
		var max_speed := 0.0
		var min_facing := 1.0
		var moves_ok := true
		var time := 0.0
		var prev := t.global_position
		while t.is_travelling() and time < 20.0:
			t.step(dt)
			time += dt
			var step_v := t.global_position - prev
			step_v.y = 0.0
			prev = t.global_position
			min_clear = min(min_clear, clearance.call(t.global_position))
			max_speed = max(max_speed, step_v.length() / dt)
			if step_v.length() > 1e-4:
				min_facing = min(min_facing, t.global_transform.basis.z.dot(step_v.normalized()))
			if t.is_travelling() and t.current_move() != walk:
				moves_ok = false
		await tree.process_frame
		var end: Transform3D = leg[1]
		check(not t.is_travelling(), "%s: arrived within 20 s (%.1f s)" % [leg[0], time])
		check(moves_ok, "%s: %s plays while travelling" % [leg[0], walk])
		check(min_clear >= 0.5, "%s: keeps %.2f m from every seat (want 0.5)" % [leg[0], min_clear])
		check(max_speed <= Terence.WALK_SPEED * 1.01, "%s: walking speed (%.2f m/s)" % [leg[0], max_speed])
		check(min_facing > 0.8, "%s: faces where he walks (%.2f)" % [leg[0], min_facing])
		check(t.global_position.distance_to(end.origin) < 0.05, "%s: arrives within 5 cm" % leg[0])
		check(t.global_transform.basis.z.dot(end.basis.z) > 0.99, "%s: turned to the target facing" % leg[0])
	check_eq(t.current_move(), Terence.HUB, "home at the hub")
	# a newer request while walking replaces the waiting one; aggressive moves walk faster
	t.play_move(&"t3_whisper")
	await _run(t, 0.5)
	t.play_move(&"t5_rhythm_slaps")
	check_eq(t.queued_move(), &"t5_rhythm_slaps", "the newer request waits instead")
	var fastest := 0.0
	var prev_pos := t.global_position
	while t.is_travelling():
		t.step(1.0 / 30.0)
		fastest = max(fastest, t.global_position.distance_to(prev_pos) * 30.0)
		prev_pos = t.global_position
	check(fastest > Terence.WALK_SPEED * 1.2, "t5 moves walk faster (%.2f m/s)" % fastest)
	await _run(t, 0.1)
	check_eq(t.current_move(), &"t5_rhythm_slaps", "and play on arrival")
	# a one-shot played away from home walks him back on its own
	await _run_until(t, func(): return t.current_move() != &"t5_rhythm_slaps", 6.0)
	check(t.is_travelling(), "walking home after the one-shot")
	await _run_until(t, func(): return not t.is_travelling(), 20.0)
	check(t.global_position.distance_to(t.home.origin) < 0.05, "home again")


func test_room_stand_and_thrown_props() -> void:
	var room := _room()
	var t := _terence(room)
	var stand := room.find_child("stand_terence", true, false) as Node3D
	check(stand.visible, "the room's stand is there")
	for id in [&"t4_sweep_stand", &"t4_throw_music"]:
		t.play_move(id)
		await _run(t, 0.2)
		check(not stand.visible, "%s hides the room's stand" % id)
		await _run(t, float(t.meta["moves"][String(id)]["seconds"]) + 0.5)
		check_eq(t.current_move(), Terence.HUB, "%s is over" % id)
		var left: Array = t._props.filter(func(r): return r["move"] == id)
		check_eq(left.size(), 1, "%s leaves its prop in the room" % id)
		if left.is_empty():
			continue
		var prop: Node3D = left[0]["node"]
		check(prop.get_parent() == t.get_parent(), "%s prop now belongs to the world" % id)
		check(not stand.visible, "the stand stays hidden while %s's prop lies there" % id)
		await _run(t, Terence.PROP_LIFETIME)
		check(not is_instance_valid(prop) or not prop.is_inside_tree(), "%s prop is cleared after %.0f s" % [id, Terence.PROP_LIFETIME])
		check(stand.visible, "the room's stand is back after %s" % id)
	# a thrown chair stays put while Terence walks off, and goes when he throws again
	t.play_move(&"t5_chair_throw")
	await _run(t, float(t.meta["moves"][String(&"t5_chair_throw")]["seconds"]) + 0.5)
	var chairs: Array = t._props.filter(func(r): return r["move"] == &"t5_chair_throw")
	check_eq(chairs.size(), 1, "the chair stays after the hub")
	if chairs.is_empty():
		return
	var chair: Node3D = chairs[0]["node"]
	var landed := chair.global_transform
	check(landed.origin.distance_to(t.home.origin) > 0.5, "the chair landed away from him (%.2f m)" % landed.origin.distance_to(t.home.origin))
	t.play_move(&"t3_loom")
	await _run(t, 2.0)
	check(chair.global_transform.is_equal_approx(landed), "the chair does not follow him")
	t.stop()
	await _run_until(t, func(): return not t.is_travelling(), 20.0)
	t.play_move(&"t5_chair_throw")
	await _run(t, 0.1)
	check(not is_instance_valid(chair) or not chair.is_inside_tree(), "throwing again clears the old chair")
	# restarting the throw after the chair landed, before the move is over, also swaps it
	await _run(t, _land(t, &"t5_chair_throw") + 0.05)
	var first: Array = t._props.filter(func(r): return r["landed"])
	check_eq(first.size(), 1, "the chair has landed")
	t.play_move(&"t5_chair_throw")
	await _run(t, 0.05)
	check(first.is_empty() or not is_instance_valid(first[0]["node"]), "a restart clears the landed chair")
	check_eq(t._attachments["PropR"].get_child_count(), 1, "and a new chair is in his hand")


func _land(t: Terence, id: StringName) -> float:
	return t._land_time(id) - 0.1


func test_band_crossfades() -> void:
	var a := _member("band_trumpet_2", "trumpet_2")
	var cut := _member("band_trumpet_2", "trumpet_2")
	var stay := _member("band_trumpet_2", "trumpet_2")
	for n in [a, cut, stay]:
		n.set_band_state(&"playing")
		_steps(n, 0.25)
	check_eq(a.current_state(), &"raise", "half way through raising")
	var p0 := _pose(a.skeleton)
	a.set_band_state(&"lowering")
	cut.set_band_state(&"lowering")
	cut.player.play(&"lower", 0.0)
	_check_crossfade("raise interrupted by lower", a, cut, stay, p0, BandMember.XFADE)
	# a one-shot reaction goes back to rest by itself, blended
	var m := _member("band_trombone_3", "trombone_3")
	_steps(m, 0.5)
	m.react(&"look_at_drummer")
	_check_smooth_return("look_at_drummer", m, &"look_at_drummer", &"rest", 4.0)
	# freeze holds, then blends back into play_loop when released
	m.set_band_state(&"playing")
	_steps(m, 1.5)
	m.react(&"freeze")
	_steps(m, 3.0)
	check_eq(m.current_state(), &"freeze", "freeze holds until released")
	m.set_band_state(&"playing")
	_steps(m, 0.5)
	check_eq(m.current_state(), &"play_loop", "released into play_loop")


## Steps without awaiting a frame (poses only, no attachments needed).
func _steps(node, seconds: float, dt: float = 1.0 / 30.0) -> void:
	var t := 0.0
	while t < seconds:
		node.step(dt)
		t += dt


func _instrument_center(n: Node3D) -> Vector3:
	var sum := Vector3.ZERO
	var count := 0
	for mi in n.find_children("*", "MeshInstance3D", true, false):
		sum += (mi as MeshInstance3D).global_transform * (mi as MeshInstance3D).get_aabb().get_center()
		count += 1
	return sum / max(count, 1)
