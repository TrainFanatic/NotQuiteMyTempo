extends TestCase
## Cinematic cut-ins: the shot table resolves from the moves metadata, cinematic moves play
## their shots in order on the animation clock (so tempo scales them), the XR panel renders
## only while a shot is on and stays put, the desktop cut switches cameras and back, and
## other moves do nothing.

const CinematicDirector := preload("res://scripts/cinematic/cinematic_director.gd")
const ShotTable := preload("res://scripts/cinematic/shot_table.gd")
const ShotFramer := preload("res://scripts/cinematic/shot_framer.gd")
const DT := 1.0 / 240.0
## The moves the request names for default coverage.
const COVERAGE := [&"t1_flick", &"t1_wave_off", &"t1_fist_cutoff", &"t1_clap_stop", &"t1_fist_slam",
	&"t2_judging_stare", &"t2_point_player", &"t3_loom", &"t3_whisper", &"t4_shout", &"t4_charge",
	&"t5_rhythm_slaps", &"t5_chair_throw", &"ap_grin"]


## Terence in the room, a camera at the player's head (current), and a director over them.
func _rig(panel_mode: bool) -> Dictionary:
	var room: Node3D = load(Rehearsal.ROOM_GLB).instantiate()
	mount(room)
	var spot := room.find_child("spot_terence", true, false) as Node3D
	var drummer := room.find_child("spot_drummer", true, false) as Node3D
	var head_pos := drummer.global_position + Vector3.UP * Rehearsal.PLAYER_HEAD_HEIGHT
	var t := Terence.new()
	mount(t)
	t.set_process(false)
	t.setup(Rehearsal.character_at(spot), head_pos, room)
	var head := Camera3D.new()
	mount(head)
	head.look_at_from_position(head_pos, spot.global_position + Vector3.UP * 1.6, Vector3.UP)
	head.make_current()
	var cd := CinematicDirector.new()
	mount(cd)
	cd.set_process(false)
	check(cd.setup(t, head, panel_mode, Environment.new()), "director sets up")
	var rec := {"clock": 0.0, "shots": [], "ended": []}
	cd.shot_started.connect(func(move: StringName, i: int, framing: StringName) -> void:
		rec["shots"].append({"move": move, "index": i, "framing": framing, "frame": cd.sequence_frame(), "clock": rec["clock"]}))
	cd.sequence_ended.connect(func(move: StringName) -> void:
		rec["ended"].append({"move": move, "clock": rec["clock"]}))
	return {"t": t, "cd": cd, "head": head, "rec": rec}


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
	check_eq(fc[1]["from"], int(_frames(t, &"t1_fist_cutoff")["cutoff"]) + 5, "the cut follows the cutoff frame")
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
		var cut: float = int(m["cutoff"]) + 5.0
		check_near(shots[1]["frame"], cut, 24.0 * DT + 1e-3, "cut to the face at cutoff+5 frames")
		check_near(shots[1]["clock"], cut / 24.0, 2.0 * DT, "at that animation time")
	check_eq(rec["ended"].size(), 1, "one sequence")
	if rec["ended"].size() == 1:
		# the last shot holds 14 frames into the idle stare after the move
		check_near(rec["ended"][0]["clock"], (float(m["frames"]) + 14.0) / 24.0, 3.0 * DT, "ends 14 frames after the move")
	check(not r["cd"].is_active(), "idle again")


func test_cut_frames_follow_the_json() -> void:
	var r := _rig(false)
	var t: Terence = r["t"]
	var rec: Dictionary = r["rec"]
	# a re-authored fist cutoff (as the more violent version will be) moves the cuts with it
	t.meta["moves"]["t1_fist_cutoff"]["cutoff"] = 16
	t.play_move(&"t1_fist_cutoff")
	_step(r, 1.0)
	check_eq(rec["shots"].size(), 2, "two shots")
	if rec["shots"].size() == 2:
		check_near(rec["shots"][1]["clock"], 21.0 / 24.0, 2.0 * DT, "the face cut moved to frame 21")
	print("(the next error is expected: a move whose key timing went missing)")
	t.meta["moves"]["t1_fist_cutoff"].erase("cutoff")
	rec["shots"].clear()
	_step(r, 1.0)
	t.play_move(&"t1_fist_cutoff")
	_step(r, 0.2)
	check(rec["shots"].is_empty() and not r["cd"].is_active(), "no shots without the cutoff timing")


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
			var cut: float = (int(m["cutoff"]) + 5.0) / 24.0 * 120.0 / bpm
			check_near(rec["shots"][1]["clock"], cut, 2.0 * DT, "face cut at %d BPM" % bpm)
		check_eq(rec["ended"].size(), 1, "sequence over at %d BPM" % bpm)
		if rec["ended"].size() == 1:
			check_near(rec["ended"][0]["clock"], (float(m["frames"]) + 14.0) / 24.0 * 120.0 / bpm, 3.0 * DT, "ends on time at %d BPM" % bpm)
		for c in tree.root.get_children():
			c.queue_free()
		await tree.process_frame


func test_panel_renders_only_during_a_shot() -> void:
	var r := _rig(true)
	var t: Terence = r["t"]
	var cd: CinematicDirector = r["cd"]
	var head: Camera3D = r["head"]
	var vp: SubViewport = cd.viewport
	check_eq(vp.size, CinematicDirector.PANEL_RES, "low-res viewport")
	check_eq(vp.render_target_update_mode, SubViewport.UPDATE_DISABLED, "viewport off while idle")
	check(not cd.panel.visible, "panel hidden while idle")
	check(cd.cam.get_viewport() == vp and cd.cam.current, "the cinematic camera films into the panel's viewport")
	check(cd.cam.cull_mask & cd.panel.layers == 0, "the cinematic camera does not film the panel")
	check(tree.root.get_camera_3d() == head, "the player's camera is untouched")
	t.play_move(&"t1_fist_cutoff")
	_step(r, DT)
	check_eq(vp.render_target_update_mode, SubViewport.UPDATE_ALWAYS, "viewport renders during the shot")
	check(cd.panel.visible, "panel up at the cut")
	var placed: Transform3D = cd.panel.global_transform
	var eye := head.global_position
	var to_panel := placed.origin - eye
	check_near(Vector2(to_panel.x, to_panel.z).length(), CinematicDirector.PANEL_DIST, 0.01, "panel 1.3 m out")
	check_near(to_panel.y, -CinematicDirector.PANEL_DROP, 0.01, "a little below the eyes")
	check(placed.basis.z.dot((eye - placed.origin).normalized()) > 0.99, "panel faces the player")
	_check_clear_of_terence(placed, eye, cd.framer.face_point(), "at home")
	# the player looks around: the panel stays where it was put
	head.rotate_y(0.6)
	head.global_position += Vector3(0.1, 0.0, 0.05)
	_step(r, 0.3)
	check(cd.panel.global_transform.is_equal_approx(placed), "panel holds still in the room (not head-locked)")
	check(tree.root.get_camera_3d() == head, "the player's camera stays current")
	check(_step_until(r, func(): return not cd.is_active(), 3.0), "sequence ends")
	check_eq(vp.render_target_update_mode, SubViewport.UPDATE_DISABLED, "viewport off again at the cut out")
	check(cd.panel.visible, "panel fading out")
	_step(r, CinematicDirector.FADE_OUT + 0.02)
	check(not cd.panel.visible, "panel gone after the fade")


func test_panel_clear_of_terence_up_close() -> void:
	var r := _rig(true)
	var t: Terence = r["t"]
	var cd: CinematicDirector = r["cd"]
	var head: Camera3D = r["head"]
	t.play_move(&"t3_whisper")
	check(_step_until(r, func(): return cd.is_active(), 10.0), "he walks over and the whisper cuts in")
	check(t.global_position.distance_to(Vector3(head.global_position.x, t.global_position.y, head.global_position.z)) < 0.8, "he is close")
	_check_clear_of_terence(cd.panel.global_transform, head.global_position, cd.framer.face_point(), "up close")
	# looking at the throne (down) still puts the panel ahead, level
	var down := Transform3D(Basis(Vector3.RIGHT, -1.2) * head.global_transform.basis, head.global_position)
	var p := CinematicDirector.place_panel(down, cd.framer.face_point())
	check_near(p.origin.y, head.global_position.y - CinematicDirector.PANEL_DROP, 1e-3, "panel at the same height when looking down")


func _check_clear_of_terence(panel_xf: Transform3D, eye: Vector3, face: Vector3, where: String) -> void:
	var a := ShotFramer._flat(panel_xf.origin - eye)
	var b := ShotFramer._flat(face - eye)
	var sep := rad_to_deg(a.angle_to(b))
	var dist := Vector2(face.x - eye.x, face.z - eye.z).length()
	var need := rad_to_deg(atan(CinematicDirector.PANEL_WIDTH / 2.0 / CinematicDirector.PANEL_DIST) + atan(CinematicDirector.TERENCE_HALF_WIDTH / dist))
	check(sep >= need, "panel clear of Terence %s (%.1f deg apart, need %.1f)" % [where, sep, need])


func test_desktop_cuts_camera_and_back() -> void:
	var r := _rig(false)
	var t: Terence = r["t"]
	var cd: CinematicDirector = r["cd"]
	var head: Camera3D = r["head"]
	check(cd.viewport == null and cd.panel == null, "no panel on the desktop")
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
	# a fist insert frames the fist where it closes
	t.play_move(&"t1_fist_cutoff")
	_step(r, float(_frames(t, &"t1_fist_cutoff")["cutoff"]) / 24.0)
	var hand: Vector3 = cd.framer.bone_global("RightHand").origin
	var off := rad_to_deg((-cd.cam.global_transform.basis.z).angle_to(hand - cd.cam.global_position))
	check(off < cd.cam.fov / 4.0, "the insert has the fist near the middle at the cutoff (%.1f deg off)" % off)
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
	for id in [&"t0_check_watch", &"t2_score_point", &"t0_countoff_finger"]:
		check(not ShotTable.SHOTS.has(id), "%s is not cinematic" % id)
		t.play_move(id)
		_step(r, 0.5)
		check(not cd.is_active(), "%s: no sequence" % id)
		check_eq(cd.viewport.render_target_update_mode, SubViewport.UPDATE_DISABLED, "%s: viewport stays off" % id)
		check(not cd.panel.visible, "%s: no panel" % id)
	# opted out in the table
	cd.table[&"t1_flick"]["enabled"] = false
	t.play_move(&"t1_flick")
	_step(r, 0.5)
	check(rec["shots"].is_empty(), "an opted-out move cuts nothing")
	check(not cd.panel.visible, "and shows no panel")


func test_rehearsal_toggle() -> void:
	var on := Rehearsal.new()
	on.run_demo = false
	on.sfx_enabled = false
	mount(on)
	check(on.cinematic != null and not on.cinematic.panel_mode, "cinematics on by default, desktop cut off-headset")
	check(on.rig.camera.current, "the player camera starts current")
	var off := Rehearsal.new()
	off.run_demo = false
	off.sfx_enabled = false
	off.cinematics_enabled = false
	mount(off)
	check(off.cinematic == null, "cinematics_enabled = false builds no director")
