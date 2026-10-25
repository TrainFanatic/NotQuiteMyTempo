extends TestCase
## Cinematic cut-ins: the shot table resolves from the moves metadata, cinematic moves play
## their shots in order on the animation clock (so tempo scales them), the XR cut moves the
## XR origin so the tracked head lands on each shot (and back, exactly), with the kit window
## hidden meanwhile, the desktop cut switches cameras and back, no floating panel or
## SubViewport exists in either mode, and other moves do nothing.

const CinematicDirector := preload("res://scripts/cinematic/cinematic_director.gd")
const ShotTable := preload("res://scripts/cinematic/shot_table.gd")
const ShotFramer := preload("res://scripts/cinematic/shot_framer.gd")
const DT := 1.0 / 240.0
## The stand-in XR head's tracked pose in the origin's frame: off the throne's centre line,
## turned 0.3 rad left and pitched 0.35 rad down.
const HEAD_LOCAL := Transform3D(Basis(Vector3.UP, 0.3) * Basis(Vector3.RIGHT, -0.35), Vector3(0.15, 1.18, 0.1))
## The moves the request names for default coverage.
const COVERAGE := [&"t1_flick", &"t1_wave_off", &"t1_fist_cutoff", &"t1_clap_stop", &"t1_fist_slam",
	&"t2_judging_stare", &"t2_point_player", &"t3_loom", &"t3_whisper", &"t4_shout", &"t4_charge",
	&"t5_rhythm_slaps", &"t5_chair_throw", &"ap_grin"]


## Terence in the room, a camera at the player's head (current), and a director over them.
## XR uses a stand-in rig: an XROrigin3D at the throne with the head under it, tracked off
## centre (leaning, turned and looking down a little, as a seated drummer would), and a
## stand-in KitWindow.
func _rig(xr: bool) -> Dictionary:
	var room: Node3D = load(Rehearsal.ROOM_GLB).instantiate()
	mount(room)
	var spot := room.find_child("spot_terence", true, false) as Node3D
	var drummer := room.find_child("spot_drummer", true, false) as Node3D
	var head_pos := drummer.global_position + Vector3.UP * Rehearsal.PLAYER_HEAD_HEIGHT
	var t := Terence.new()
	mount(t)
	t.set_process(false)
	t.setup(Rehearsal.character_at(spot), head_pos, room)
	# the director goes in before the rig, so a teardown with a shot still on frees it (and
	# it puts the view back) while the rig is still there
	var cd := CinematicDirector.new()
	mount(cd)
	cd.set_process(false)
	var head := Camera3D.new()
	var origin: XROrigin3D = null
	var kit: MeshInstance3D = null
	if xr:
		origin = XROrigin3D.new()
		mount(origin)
		origin.global_transform = drummer.global_transform
		origin.add_child(head)
		head.transform = HEAD_LOCAL
		kit = MeshInstance3D.new()
		kit.name = "KitWindow"
		mount(kit)
	else:
		mount(head)
		head.look_at_from_position(head_pos, spot.global_position + Vector3.UP * 1.6, Vector3.UP)
	head.make_current()
	check(cd.setup(t, head, xr, origin, kit), "director sets up")
	var rec := {"clock": 0.0, "shots": [], "ended": []}
	cd.shot_started.connect(func(move: StringName, i: int, framing: StringName) -> void:
		rec["shots"].append({"move": move, "index": i, "framing": framing, "frame": cd.sequence_frame(), "clock": rec["clock"]}))
	cd.sequence_ended.connect(func(move: StringName) -> void:
		rec["ended"].append({"move": move, "clock": rec["clock"]}))
	return {"t": t, "cd": cd, "head": head, "origin": origin, "kit": kit, "rec": rec}


func _step(r: Dictionary, seconds: float) -> void:
	var t: Terence = r["t"]
	var cd: CinematicDirector = r["cd"]
	var s := 0.0
	while s < seconds:
		r["rec"]["clock"] += DT
		t.step(DT)
		cd.update(DT)
		s += DT


## Steps until `cond` holds (or `limit` seconds); true if it did.
func _step_until(r: Dictionary, cond: Callable, limit: float) -> bool:
	var s := 0.0
	while not cond.call() and s < limit:
		_step(r, DT)
		s += DT
	return cond.call()


func _frames(t: Terence, move: StringName) -> Dictionary:
	return t.meta["moves"][String(move)]


func test_table_covers_and_resolves() -> void:
	var r := _rig(false)
	var t: Terence = r["t"]
	check(ShotTable.check(ShotTable.SHOTS, t.meta, Terence.CLOSE_MOVES), "the whole table resolves")
	for id in COVERAGE:
		check(ShotTable.SHOTS.has(id) and ShotTable.SHOTS[id]["enabled"], "%s is covered" % id)
	for id in ShotTable.SHOTS:
		for s in ShotTable.resolve(ShotTable.SHOTS, id, t.meta):
			var hold: float = (s["to"] - s["from"]) / ShotTable.FPS
			check(hold >= ShotTable.MIN_HOLD - 1e-6 and hold <= ShotTable.MAX_HOLD + 1e-6,
				"%s %s holds %.2f s" % [id, s["framing"], hold])
	# the fist cutoff opens on the fist and cuts to his face at its JSON key timing
	var fc := ShotTable.resolve(ShotTable.SHOTS, &"t1_fist_cutoff", t.meta)
	check_eq(fc[0]["framing"], &"fist_insert", "fist cutoff opens on the fist")
	check_eq(fc[0]["accent"], int(_frames(t, &"t1_fist_cutoff")["cutoff"]) - 2, "the zoom snaps on the cutoff")
	check_eq(fc[1]["framing"], &"close_face", "then his face")
	check_eq(fc[1]["from"], int(_frames(t, &"t1_fist_cutoff")["hold_start"]) + 10, "the cut follows the hold frame")
	var slaps := ShotTable.resolve(ShotTable.SHOTS, &"t5_rhythm_slaps", t.meta)
	var impacts: Array = _frames(t, &"t5_rhythm_slaps")["impacts"]
	check_eq(slaps.map(func(s): return s["from"]), [0, int(impacts[0]), int(impacts[1]), int(impacts[2])], "slap cuts land on the impacts")
	# frame specs
	var mm := {"frames": 30, "cutoff": 10, "impacts": [18, 42]}
	check_eq(ShotTable.resolve_frame("cutoff+5", mm, &"x"), 15, "key plus offset")
	check_eq(ShotTable.resolve_frame("impacts[1]-3", mm, &"x"), 39, "list element minus offset")
	check_eq(ShotTable.resolve_frame("end+12", mm, &"x"), 42, "end plus offset")
	check_eq(ShotTable.resolve_frame(7, mm, &"x"), 7, "plain frame")
	print("(the next errors are expected: bad frame specs and tables)")
	check_eq(ShotTable.resolve_frame("release", mm, &"x"), -1, "a missing key is an error, not a guess")
	check_eq(ShotTable.resolve_frame("impacts[5]", mm, &"x"), -1, "an index out of range is an error")
	check_eq(ShotTable.resolve_frame("cutoff++1", mm, &"x"), -1, "a malformed spec is an error")
	var bad := ShotTable.SHOTS.duplicate(true)
	bad[&"t1_flick"]["shots"][0]["framing"] = &"dutch_tilt"
	check(not ShotTable.check(bad, t.meta, Terence.CLOSE_MOVES), "an unknown framing fails the table")
	bad = ShotTable.SHOTS.duplicate(true)
	bad[&"t3_whisper"]["shots"][-1]["to"] = "end+10"
	check(not ShotTable.check(bad, t.meta, Terence.CLOSE_MOVES), "a close-up one-shot cannot run into the walk home")
	bad = ShotTable.SHOTS.duplicate(true)
	bad[&"t1_wave_off"]["shots"][1]["from"] = "wave+6"
	check(not ShotTable.check(bad, t.meta, Terence.CLOSE_MOVES), "a gap between shots fails the table")


func test_fist_cutoff_plays_shots_in_order() -> void:
	var r := _rig(false)
	var t: Terence = r["t"]
	var rec: Dictionary = r["rec"]
	var m := _frames(t, &"t1_fist_cutoff")
	t.play_move(&"t1_fist_cutoff")
	_step(r, (float(m["frames"]) + 30.0) / 24.0)
	var shots: Array = rec["shots"]
	check_eq(shots.map(func(s): return s["framing"]), [&"fist_insert", &"close_face"], "fist insert, then face")
	if shots.size() == 2:
		check_near(shots[0]["clock"], 0.0, 2.0 * DT, "the cut in comes with the move")
		var cut: float = int(m["hold_start"]) + 10.0
		check_near(shots[1]["frame"], cut, 24.0 * DT + 1e-3, "cut to the face at hold_start+10 frames")
		check_near(shots[1]["clock"], cut / 24.0, 2.0 * DT, "at that animation time")
	check_eq(rec["ended"].size(), 1, "one sequence")
	if rec["ended"].size() == 1:
		# the last shot holds 6 frames into the idle stare after the move
		check_near(rec["ended"][0]["clock"], (float(m["frames"]) + 6.0) / 24.0, 3.0 * DT, "ends 6 frames after the move")
	check(not r["cd"].is_active(), "idle again")


func test_cut_frames_follow_the_json() -> void:
	var r := _rig(false)
	var t: Terence = r["t"]
	var rec: Dictionary = r["rec"]
	# a re-authored fist cutoff (as the more violent version will be) moves the cuts with it
	t.meta["moves"]["t1_fist_cutoff"]["hold_start"] = 22
	t.play_move(&"t1_fist_cutoff")
	_step(r, 1.5)
	check_eq(rec["shots"].size(), 2, "two shots")
	if rec["shots"].size() == 2:
		check_near(rec["shots"][1]["clock"], 32.0 / 24.0, 2.0 * DT, "the face cut moved to frame 32")
	print("(the next error is expected: a move whose key timing went missing)")
	t.meta["moves"]["t1_fist_cutoff"].erase("hold_start")
	rec["shots"].clear()
	_step(r, 1.0)
	t.play_move(&"t1_fist_cutoff")
	_step(r, 0.2)
	check(rec["shots"].is_empty() and not r["cd"].is_active(), "no shots without the hold timing")


func test_shot_timing_scales_with_tempo() -> void:
	for bpm in [60.0, 240.0]:
		var r := _rig(false)
		var t: Terence = r["t"]
		var rec: Dictionary = r["rec"]
		t.set_tempo(bpm)
		var m := _frames(t, &"t1_fist_cutoff")
		t.play_move(&"t1_fist_cutoff")
		_step(r, (float(m["frames"]) + 30.0) / 24.0 * 120.0 / bpm)
		check_eq(rec["shots"].size(), 2, "two shots at %d BPM" % bpm)
		if rec["shots"].size() == 2:
			var cut: float = (int(m["hold_start"]) + 10.0) / 24.0 * 120.0 / bpm
			check_near(rec["shots"][1]["clock"], cut, 2.0 * DT, "face cut at %d BPM" % bpm)
		check_eq(rec["ended"].size(), 1, "sequence over at %d BPM" % bpm)
		if rec["ended"].size() == 1:
			check_near(rec["ended"][0]["clock"], (float(m["frames"]) + 6.0) / 24.0 * 120.0 / bpm, 3.0 * DT, "ends on time at %d BPM" % bpm)
		for c in tree.root.get_children():
			c.queue_free()
		await tree.process_frame


func _angle_diff(a: float, b: float) -> float:
	return absf(wrapf(a - b, -PI, PI))


## The head, as tracked in the origin's frame.
func _head_local(r: Dictionary) -> Transform3D:
	return (r["origin"] as Node3D).global_transform.affine_inverse() * (r["head"] as Node3D).global_transform


## The tracked head sits exactly on the shot: its position, and the shot's heading.
func _check_on_shot(r: Dictionary, what: String) -> void:
	var cd: CinematicDirector = r["cd"]
	var head: Node3D = r["head"]
	var shot: Transform3D = cd.shot_transform
	check(head.global_position.distance_to(shot.origin) < 1e-4,
		"%s: the head lands on the shot camera (%.5f m off)" % [what, head.global_position.distance_to(shot.origin)])
	var dh := _angle_diff(CinematicDirector.heading(head.global_basis), CinematicDirector.heading(shot.basis))
	check(dh < 1e-4, "%s: the head heads the way the shot looks (%.5f rad off)" % [what, dh])
	check((r["origin"] as Node3D).global_basis.y.is_equal_approx(Vector3.UP), "%s: the origin stays upright" % what)


func test_xr_cut_moves_the_origin() -> void:
	var r := _rig(true)
	var t: Terence = r["t"]
	var cd: CinematicDirector = r["cd"]
	var head: Camera3D = r["head"]
	var origin: XROrigin3D = r["origin"]
	var rest: Transform3D = origin.transform
	var rest_global: Transform3D = origin.global_transform
	check(cd.cam == null and cd.overlay == null, "no cinematic camera or overlay in XR")
	t.play_move(&"t4_shout")
	_step(r, DT)
	check(cd.is_active(), "the shout cuts in")
	check(tree.root.get_camera_3d() == head, "the player's own camera stays current")
	check(not origin.transform.is_equal_approx(rest), "the origin moved")
	_check_on_shot(r, "at the cut")
	check(_head_local(r).is_equal_approx(HEAD_LOCAL), "head tracking is untouched (the offset and pitch stay)")
	check_near((-head.global_basis.z).y, (-(rest_global * HEAD_LOCAL).basis.z).y, 1e-4, "the head keeps its own pitch")
	var face: Vector3 = cd.framer.face_point()
	check(head.global_position.distance_to(face) < 1.2, "the view is up close to his face")
	var to_face := face - head.global_position
	var dh := _angle_diff(atan2(-to_face.x, -to_face.z), CinematicDirector.heading(head.global_basis))
	check(dh < deg_to_rad(15.0), "and heads toward it (%.1f deg off)" % rad_to_deg(dh))
	# the framer still aims from the throne, not from where the cut put the head
	check(cd.player_eye().distance_to(rest_global * HEAD_LOCAL.origin) < 1e-4, "the shots are framed from the player's real seat")
	# the player moves their head: the view moves with it on top of the shot
	var nudge := Vector3(0.05, -0.02, 0.03)
	head.transform = Transform3D(Basis(Vector3.UP, 0.2) * HEAD_LOCAL.basis, HEAD_LOCAL.origin + nudge)
	_step(r, DT)
	var moved := head.global_position - cd.shot_transform.origin
	check(moved.distance_to(origin.global_basis * nudge) < 1e-4, "a head move moves the view by as much (%s)" % moved)
	check_near(_angle_diff(CinematicDirector.heading(head.global_basis), CinematicDirector.heading(cd.shot_transform.basis)), 0.2, 1e-3,
		"a head turn turns the view")


func test_xr_origin_follows_tracking_shots() -> void:
	var r := _rig(true)
	var t: Terence = r["t"]
	var cd: CinematicDirector = r["cd"]
	var origin: XROrigin3D = r["origin"]
	var rec: Dictionary = r["rec"]
	# the wave-off opens on a push-in on the low-angle medium
	check_eq(ShotTable.SHOTS[&"t1_wave_off"]["shots"][0]["camera"], &"push_in", "the wave-off opens on a push-in")
	t.play_move(&"t1_wave_off")
	_step(r, DT)
	var first := origin.global_position
	var steps := 0
	while cd.is_active() and cd.shot_index() == 0 and steps < 60:
		_step(r, 4.0 * DT)
		steps += 1
		_check_on_shot(r, "push-in step %d" % steps)
	check(origin.global_position.distance_to(first) > 0.05, "the origin moved with the dolly (%.3f m)" % origin.global_position.distance_to(first))
	check(rec["shots"].size() >= 2, "it cut to the next shot")
	if rec["shots"].size() >= 2:
		_check_on_shot(r, "after the cut to the close-up")
		check(_head_local(r).is_equal_approx(HEAD_LOCAL), "head tracking still untouched after the second cut")


func test_xr_restores_the_view_and_kit_window() -> void:
	var r := _rig(true)
	var t: Terence = r["t"]
	var cd: CinematicDirector = r["cd"]
	var head: Camera3D = r["head"]
	var origin: XROrigin3D = r["origin"]
	var kit: MeshInstance3D = r["kit"]
	var rest: Transform3D = origin.transform
	check(kit.visible, "kit window shows while idle")
	check(cd.blink != null and cd.blink.get_parent() == head, "the blink is on the head (head-locked)")
	check(not cd.blink.visible, "no blink while idle")
	t.play_move(&"t1_fist_cutoff")
	_step(r, DT)
	check(cd.is_active() and not kit.visible, "kit window hidden during the close-up")
	check(cd.blink.visible, "a blink covers the cut")
	_step(r, CinematicDirector.BLINK + 2.0 * DT)
	check(not cd.blink.visible, "the blink is over in %.0f ms" % (CinematicDirector.BLINK * 1000.0))
	check(_step_until(r, func(): return not cd.is_active(), 6.0), "sequence ends")
	check(origin.transform == rest, "the origin is back exactly at the end")
	check(kit.visible, "kit window back at the end")
	check(cd.blink.visible, "a blink covers the cut back")
	_step(r, CinematicDirector.BLINK + 2.0 * DT)
	# interrupted mid-sequence: straight back
	t.play_move(&"t4_shout")
	_step(r, 0.3)
	check(cd.is_active() and not kit.visible and origin.transform != rest, "the shout cuts in")
	t.play_move(&"t0_check_watch")
	_step(r, DT)
	check(not cd.is_active(), "interrupted sequence ends")
	check(origin.transform == rest, "the origin is back exactly on interruption")
	check(kit.visible, "kit window back on interruption")
	check(_head_local(r).is_equal_approx(HEAD_LOCAL), "head tracking untouched throughout")


func test_no_floating_panel() -> void:
	for xr in [true, false]:
		var r := _rig(xr)
		var t: Terence = r["t"]
		var cd: CinematicDirector = r["cd"]
		t.play_move(&"t1_fist_cutoff")
		_step(r, 0.5)
		check(cd.is_active(), "a shot is on (xr %s)" % xr)
		check(tree.root.find_children("*", "SubViewport", true, false).is_empty(), "no SubViewport (xr %s)" % xr)
		check(tree.root.find_child("CinemaPanel", true, false) == null, "no cinema panel (xr %s)" % xr)
		check(cd.find_children("*", "MeshInstance3D", true, false).is_empty(), "the director places no meshes in the room (xr %s)" % xr)
		check(not ResourceLoader.exists("res://shaders/cinematic_panel.gdshader"), "the panel shader is gone")
		for c in tree.root.get_children():
			c.queue_free()
		await tree.process_frame


func test_xr_setup_fails_loudly() -> void:
	var r := _rig(false)
	var t: Terence = r["t"]
	var head: Camera3D = r["head"]
	print("(the next errors are expected: XR set-ups missing a node)")
	var origin := XROrigin3D.new()
	mount(origin)
	var kit := Node3D.new()
	mount(kit)
	var cd := CinematicDirector.new()
	mount(cd)
	check(not cd.setup(t, head, true, null, kit), "XR without an origin fails")
	check(not cd.setup(t, head, true, origin, kit), "XR with the head outside the origin fails")
	var head2 := Camera3D.new()
	origin.add_child(head2)
	check(not cd.setup(t, head2, true, origin, null), "XR without the kit window fails")
	check(not cd.setup(t, head, false, origin, null), "the desktop with an XR origin fails")
	check(cd.setup(t, head2, true, origin, kit), "a complete XR rig sets up")


func test_desktop_cuts_camera_and_back() -> void:
	var r := _rig(false)
	var t: Terence = r["t"]
	var cd: CinematicDirector = r["cd"]
	var head: Camera3D = r["head"]
	check(tree.root.get_camera_3d() == head, "player camera before")
	t.play_move(&"t4_shout")
	_step(r, DT)
	check(tree.root.get_camera_3d() == cd.cam, "cut to the cinematic camera")
	check(cd.overlay.visible, "grade and letterbox on")
	# the face is in frame and close
	var face: Vector3 = cd.framer.face_point()
	var cam_fwd: Vector3 = -cd.cam.global_transform.basis.z
	check(rad_to_deg(cam_fwd.angle_to(face - cd.cam.global_position)) < 8.0, "the camera looks at his face")
	check(cd.cam.global_position.distance_to(face) < 1.2, "from close up")
	check(_step_until(r, func(): return not cd.is_active(), 4.0), "shout sequence ends")
	check(tree.root.get_camera_3d() == head, "cut back to the player camera")
	check(not cd.overlay.visible, "overlay off")
	# a fist insert frames the fist once it is held up after the slam
	t.play_move(&"t1_fist_cutoff")
	_step(r, float(_frames(t, &"t1_fist_cutoff")["hold_start"]) / 24.0)
	var hand: Vector3 = cd.framer.bone_global("RightHand").origin
	var off := rad_to_deg((-cd.cam.global_transform.basis.z).angle_to(hand - cd.cam.global_position))
	check(off < cd.cam.fov / 4.0, "the insert has the held fist near the middle (%.1f deg off)" % off)
	check(cd.cam.global_position.distance_to(hand) < 0.8, "close on the fist")
	# another move takes over mid-sequence: cut back at once
	t.play_move(&"t0_check_watch")
	_step(r, DT)
	check(not cd.is_active(), "interrupted sequence ends")
	check(tree.root.get_camera_3d() == head, "and cuts back")


func test_other_moves_do_nothing() -> void:
	var r := _rig(true)
	var t: Terence = r["t"]
	var cd: CinematicDirector = r["cd"]
	var rec: Dictionary = r["rec"]
	var origin: XROrigin3D = r["origin"]
	var kit: MeshInstance3D = r["kit"]
	var rest: Transform3D = origin.transform
	for id in [&"t0_check_watch", &"t2_score_point", &"t0_countoff_finger"]:
		check(not ShotTable.SHOTS.has(id), "%s is not cinematic" % id)
		t.play_move(id)
		_step(r, 0.5)
		check(not cd.is_active(), "%s: no sequence" % id)
		check(origin.transform == rest and kit.visible, "%s: the view and kit window stay" % id)
	# opted out in the table
	cd.table[&"t1_flick"]["enabled"] = false
	t.play_move(&"t1_flick")
	_step(r, 0.5)
	check(rec["shots"].is_empty(), "an opted-out move cuts nothing")
	check(origin.transform == rest and kit.visible and not cd.blink.visible, "and moves nothing")


func test_rehearsal_toggle() -> void:
	var default := Rehearsal.new()
	default.run_demo = false
	default.sfx_enabled = false
	default.intro_enabled = false
	mount(default)
	check(default.cinematic == null, "no camera cuts by default")
	var on := Rehearsal.new()
	on.run_demo = false
	on.sfx_enabled = false
	on.cinematics_enabled = true
	mount(on)
	check(on.cinematic != null and not on.cinematic.xr_mode, "cinematics when enabled, desktop cut off-headset")
	check(on.rig.camera.current, "the player camera starts current")
	var off := Rehearsal.new()
	off.run_demo = false
	off.sfx_enabled = false
	off.cinematics_enabled = false
	mount(off)
	check(off.cinematic == null, "cinematics_enabled = false builds no director")
