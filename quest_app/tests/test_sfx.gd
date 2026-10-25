extends TestCase
## Sound effects: the library, cue firing against animation time, band table lookup,
## anchors, the voice pool and its cap. Characters are stand-ins (FakeCharacter) whose
## FakeBlender emits AnimBlender's advanced/started signals from positions the test sets,
## so every interval edge case can be driven exactly. Fixtures: tests/fixtures/sfx
## (python3 tests/fixtures/make_sfx_fixtures.py).

const SfxLibrary := preload("res://scripts/sfx/sfx_library.gd")
const SfxVoicePool := preload("res://scripts/sfx/sfx_voice_pool.gd")
const SfxCuePlayer := preload("res://scripts/sfx/sfx_cue_player.gd")
const FIXTURES := "res://tests/fixtures/sfx"
const LOOP_LEN := 2.0   # loop_a, looping
const SHOT_LEN := 1.0   # shot_b, one-shot


## Emits what AnimBlender emits; the test decides the positions.
class FakeBlender:
	extends RefCounted
	signal advanced(anim: StringName, from_pos: float, to_pos: float, wrapped: bool)
	signal started(anim: StringName)
	var player: AnimationPlayer
	var current: StringName = &""
	var pos := 0.0

	func start(anim: StringName) -> void:
		current = anim
		pos = 0.0
		started.emit(anim)

	## Moves the current animation on by `delta`, wrapping loops as AnimBlender does.
	func step(delta: float) -> void:
		var length := player.get_animation(current).length
		var looping := player.get_animation(current).loop_mode != Animation.LOOP_NONE
		var from := pos
		var to := from + delta
		var wrapped := false
		if to >= length:
			if looping:
				to = fmod(to, length)
				wrapped = true
			else:
				to = length
		pos = to
		if wrapped or to > from:
			advanced.emit(current, from, to, wrapped)


class FakeCharacter:
	extends Node3D
	var blender: FakeBlender
	var skeleton: Skeleton3D
	var props := {}

	func prop_anchor(bone: String) -> Node3D:
		return props.get(bone)


class FakeBandCharacter:
	extends Node3D
	var blender: FakeBlender
	var skeleton: Skeleton3D
	var member_name := ""


func _library() -> SfxLibrary:
	var lib: SfxLibrary = SfxLibrary.load_dir(FIXTURES)
	check(lib != null, "fixture library loads")
	return lib


func _json(file: String) -> Dictionary:
	return JSON.parse_string(FileAccess.get_file_as_string(FIXTURES.path_join(file)))


func _pool(voices: int = 24) -> SfxVoicePool:
	var p := SfxVoicePool.new()
	mount(p)
	p.set_process(false)
	check(p.setup(voices), "pool sets up")
	return p


func _rig(c: Node3D) -> void:
	var skel := Skeleton3D.new()
	skel.add_bone("Hips")
	skel.set_bone_rest(0, Transform3D(Basis(), Vector3(0, 1.0, 0)))
	skel.add_bone("RightHand")
	skel.set_bone_parent(1, 0)
	skel.set_bone_rest(1, Transform3D(Basis(), Vector3(0.3, 0.4, 0.2)))
	skel.reset_bone_poses()
	c.add_child(skel)
	var ap := AnimationPlayer.new()
	var lib := AnimationLibrary.new()
	var a := Animation.new()
	a.length = LOOP_LEN
	a.loop_mode = Animation.LOOP_LINEAR
	lib.add_animation(&"loop_a", a)
	var b := Animation.new()
	b.length = SHOT_LEN
	lib.add_animation(&"shot_b", b)
	ap.add_animation_library(&"", lib)
	c.add_child(ap)
	var bl := FakeBlender.new()
	bl.player = ap
	c.blender = bl
	c.skeleton = skel


func _terence() -> FakeCharacter:
	var c := FakeCharacter.new()
	c.name = "FakeTerence"
	mount(c)
	_rig(c)
	return c


func _band(member: String) -> FakeBandCharacter:
	var c := FakeBandCharacter.new()
	c.name = member
	c.member_name = member
	mount(c)
	_rig(c)
	return c


func _cue_player(c: Node3D, table: Dictionary, lib: SfxLibrary, pool: SfxVoicePool) -> SfxCuePlayer:
	var p := SfxCuePlayer.new()
	c.add_child(p)
	p.rng.seed = 1234
	check(p.setup(c, table, lib, pool), "cue player sets up")
	return p


func _times(p: SfxCuePlayer) -> Array:
	return p.fired.map(func(f): return snappedf(f["time"], 0.001))


func _steps(bl: FakeBlender, total: float, dt: float) -> void:
	var t := 0.0
	while t < total - 1e-9:
		var d := minf(dt, total - t)
		bl.step(d)
		t += d


func test_sfx_library_loads() -> void:
	var lib := _library()
	if lib == null:
		return
	check_eq(lib.streams.size(), 2, "two sounds")
	check_eq(lib.fps, 24.0, "cue fps")
	var tick: AudioStreamWAV = lib.streams[&"tick"]
	check_eq(tick.mix_rate, 22050, "22,050 Hz")
	check_near(tick.get_length(), 0.05, 0.002, "tick length")
	check_eq(lib.terence_table()["loop_a"].size(), 3, "Terence loop_a cues")


func test_sfx_fire_once_per_loop_pass() -> void:
	var lib := _library()
	var c := _terence()
	var p := _cue_player(c, lib.terence_table(), lib, _pool())
	c.blender.start(&"loop_a")
	check_eq(_times(p), [0.0], "frame 0 fires on start")
	# three full passes at an uneven step, ending just before the 4th pass reaches 0.5 s
	_steps(c.blender, 3 * LOOP_LEN + 0.3, 1.0 / 30.0 + 0.0007)
	check_eq(_times(p), [0.0, 0.5, 1.5, 0.0, 0.5, 1.5, 0.0, 0.5, 1.5, 0.0], "each cue once per pass, wraps fire frame 0")


func test_sfx_fast_steps_skip_no_cue() -> void:
	var lib := _library()
	var c := _terence()
	var p := _cue_player(c, lib.terence_table(), lib, _pool())
	c.blender.start(&"loop_a")
	c.blender.step(1.6)   # past 0.5 and 1.5 in one step
	check_eq(_times(p), [0.0, 0.5, 1.5], "one step over two cues fires both")
	c.blender.step(1.1)   # 1.6 -> wraps -> 0.7: frame 0 and 0.5 of the next pass
	check_eq(_times(p), [0.0, 0.5, 1.5, 0.0, 0.5], "a wrapping step fires the end of one pass and the start of the next")
	c.blender.step(0.8)   # 0.7 -> 1.5 exactly: the cue on the boundary fires once
	c.blender.step(0.1)
	check_eq(_times(p), [0.0, 0.5, 1.5, 0.0, 0.5, 1.5], "a cue on a step boundary fires once")


func test_sfx_restart_and_one_shot_end() -> void:
	var lib := _library()
	var c := _terence()
	var p := _cue_player(c, lib.terence_table(), lib, _pool())
	c.blender.start(&"loop_a")
	c.blender.step(0.7)
	c.blender.start(&"loop_a")   # restart mid pass
	c.blender.step(0.6)
	check_eq(_times(p), [0.0, 0.5, 0.0, 0.5], "a restart fires frame 0 and the pass again")
	c.props["PropR"] = Node3D.new()
	c.add_child(c.props["PropR"])
	p.fired.clear()
	c.blender.start(&"shot_b")
	_steps(c.blender, 1.5, 0.1)   # runs into the end and holds
	check_eq(_times(p), [0.25, 0.5, 1.0], "one-shot cues fire once, including the last frame")


func test_sfx_only_dominant_animation_fires() -> void:
	var lib := _library()
	var c := _terence()
	c.props["PropR"] = Node3D.new()
	c.add_child(c.props["PropR"])
	var p := _cue_player(c, lib.terence_table(), lib, _pool())
	c.blender.start(&"loop_a")
	c.blender.step(0.4)
	c.blender.start(&"shot_b")   # crossfade: loop_a fades out from 0.4 s
	c.blender.advanced.emit(&"loop_a", 0.4, 0.6, false)
	check_eq(_times(p), [0.0], "the fading animation's 0.5 s cue does not fire")
	c.blender.step(0.3)
	check_eq(_times(p), [0.0, 0.25], "the dominant animation's cue fires")


func test_sfx_backwards_step_is_an_error() -> void:
	var lib := _library()
	var c := _terence()
	var p := _cue_player(c, lib.terence_table(), lib, _pool())
	c.blender.start(&"loop_a")
	p.fired.clear()
	c.blender.advanced.emit(&"loop_a", 1.0, 0.2, false)   # expected ERROR
	check_eq(p.fired.size(), 0, "a step back without a wrap fires nothing")


func test_sfx_band_lookup() -> void:
	var lib := _library()
	check_eq(SfxLibrary.kind_of("band_tenor_sax_1"), "tenor_sax", "kind of a two-word section")
	check_eq(SfxLibrary.kind_of("band_trombone_2"), "trombone", "kind of a trombone")
	var t2 := lib.band_table("band_trombone_2")
	check_eq(t2["loop_a"][0]["frame"], 18, "member table first")
	check_eq(t2["shot_b"][0]["frame"], 12, "default for actions the member table lacks")
	check_eq(lib.band_sources("band_trombone_2"), {"loop_a": "band_trombone_2", "shot_b": "default"}, "sources for trombone 2")
	var t1 := lib.band_table("band_trombone_1")
	check_eq(t1["loop_a"].size(), 1, "kind table replaces the default list, no merge")
	check_eq(t1["loop_a"][0]["frame"], 6, "kind table second")
	check_eq(lib.band_table("band_trumpet_1")["loop_a"][0]["frame"], 0, "default last")
	check(lib.check_band_keys(["band_trombone_1", "band_trombone_2"]), "band keys match the roster")
	check(not lib.check_band_keys(["band_trumpet_1"]), "a band key matching nobody is an error")   # expected ERROR
	var m := _band("band_trombone_1")
	var p := _cue_player(m, lib.band_table("band_trombone_1"), lib, _pool())
	m.blender.start(&"loop_a")
	m.blender.step(0.3)
	check_eq(_times(p), [0.25], "trombone 1 plays the trombone loop_a cue")


func test_sfx_attach_all() -> void:
	var lib := _library()
	var band := [_band("band_trombone_1"), _band("band_trombone_2"), _band("band_trumpet_1")]
	var t := _terence()
	check(SfxCuePlayer.attach_all(band, t, lib, _pool()), "attaches to everyone")
	for n in band + [t]:
		check(n.has_node("SfxCues"), "%s has a cue player" % n.name)


func test_sfx_attach_terence_only() -> void:
	var lib := _library()
	var band := [_band("band_trombone_1")]
	var t := _terence()
	check(SfxCuePlayer.attach_terence(t, lib, _pool()), "attaches to Terence")
	check(t.has_node("SfxCues"), "Terence has a cue player")
	check(not band[0].has_node("SfxCues"), "the band stays silent")


func test_sfx_library_errors() -> void:
	check(SfxLibrary.load_dir("res://tests/fixtures/no_such_sfx") == null, "missing manifest")   # expected ERROR
	var manifest := _json("sfx_manifest.json")
	var cues := _json("sfx_cues.json")
	var m := manifest.duplicate(true)
	m["sounds"]["gone"] = {"file": "gone.wav", "seconds": 0.1}
	check(SfxLibrary.from_data(m, cues, FIXTURES) == null, "missing WAV")   # expected ERROR
	m = manifest.duplicate(true)
	m["sounds"]["tick"]["seconds"] = 0.5
	check(SfxLibrary.from_data(m, cues, FIXTURES) == null, "length disagrees with manifest")   # expected ERROR
	var c := cues.duplicate(true)
	c["terence"]["loop_a"][0]["sfx"] = "kazoo"
	check(SfxLibrary.from_data(manifest, c, FIXTURES) == null, "unknown sound in a Terence cue")   # expected ERROR
	c = cues.duplicate(true)
	c["band"]["trombone"]["loop_a"][0]["sfx"] = "kazoo"
	check(SfxLibrary.from_data(manifest, c, FIXTURES) == null, "unknown sound in a band cue")   # expected ERROR
	c = cues.duplicate(true)
	c["terence"]["shot_b"][1]["anchor"] = "world:1,2"
	check(SfxLibrary.from_data(manifest, c, FIXTURES) == null, "malformed world anchor")   # expected ERROR
	c = cues.duplicate(true)
	c["terence"]["loop_a"][0].erase("gain_db")
	check(SfxLibrary.from_data(manifest, c, FIXTURES) == null, "cue missing a key")   # expected ERROR
	c = cues.duplicate(true)
	c["terence"]["loop_a"][0]["frame"] = 2.5
	check(SfxLibrary.from_data(manifest, c, FIXTURES) == null, "fractional frame")   # expected ERROR
	c = cues.duplicate(true)
	c["version"] = 2
	check(SfxLibrary.from_data(manifest, c, FIXTURES) == null, "unknown version")   # expected ERROR


func _bad_table(anchor: String, action: String = "loop_a", frame: int = 0) -> Dictionary:
	return {action: [{"frame": frame, "sfx": "tick", "anchor": anchor, "gain_db": 0.0, "pitch_jitter": 0.0}]}


func test_sfx_cue_player_errors() -> void:
	var lib := _library()
	var pool := _pool()
	var t := _terence()
	var b := _band("band_trumpet_1")
	var cases := [
		[t, _bad_table("LeftPinky"), "unknown bone anchor"],
		[b, _bad_table("PropR"), "prop anchor on a character without props"],
		[t, _bad_table("Hips", "dance"), "unknown animation"],
		[t, _bad_table("Hips", "shot_b", 30), "cue past the animation's end"],
	]
	for case in cases:
		var p := SfxCuePlayer.new()
		case[0].add_child(p)
		check(not p.setup(case[0], case[1], lib, pool), case[2])   # expected ERROR
	var plain := Node3D.new()
	plain.name = "NoBlender"
	mount(plain)
	var p2 := SfxCuePlayer.new()
	plain.add_child(p2)
	check(not p2.setup(plain, {}, lib, pool), "a character without a blender")   # expected ERROR
	# a prop anchor with no prop present at trigger time
	var p3 := _cue_player(t, lib.terence_table(), lib, pool)
	t.blender.start(&"shot_b")
	t.blender.step(0.3)   # expected ERROR
	check_eq(_times(p3), [], "nothing plays without the prop")


func test_sfx_placement() -> void:
	var lib := _library()
	var pool := _pool()
	var c := _terence()
	c.global_transform = Transform3D(Basis(Vector3.UP, PI / 2), Vector3(2, 0, -1))
	var prop := Node3D.new()   # in the room, not under him: a thrown prop
	mount(prop)
	prop.global_position = Vector3(1, 1.5, 0)
	c.props["PropR"] = prop
	var p := _cue_player(c, lib.terence_table(), lib, pool)
	c.blender.start(&"loop_a")
	var hand: Vector3 = c.skeleton.global_transform * c.skeleton.get_bone_global_pose(1).origin
	var v: AudioStreamPlayer3D = p.fired[0]["voice"]
	check(v.global_position.distance_to(hand) < 1e-4, "voice at the RightHand bone (%s vs %s)" % [v.global_position, hand])
	check(v.global_position.distance_to(Vector3(2.2, 1.4, -1.3)) < 1e-4, "bone position includes the character's transform")
	check_eq(v.bus, &"SFX", "on the SFX bus")
	var start_xf := c.global_transform
	c.blender.start(&"shot_b")
	c.global_position += Vector3(0, 0, 3)   # he moves after the move started
	c.blender.step(0.3)
	v = p.fired[-1]["voice"]
	check(v.global_position.distance_to(Vector3(1, 1.5, 0)) < 1e-4, "voice at the prop")
	prop.global_position = Vector3(-1, 2, 4)   # the prop flies
	pool.step(0.02)
	check(v.global_position.distance_to(Vector3(-1, 2, 4)) < 1e-4, "voice follows the flying prop")
	c.blender.step(0.25)
	v = p.fired[-1]["voice"]
	var want: Vector3 = start_xf * Vector3(0, 0.5, -1)   # Blender (0, 1, 0.5) is Godot (0, 0.5, -1)
	check(v.global_position.distance_to(want) < 1e-4, "world anchor in the frame captured at the move's start (%s vs %s)" % [v.global_position, want])


func test_sfx_world_anchor_blender_axes() -> void:
	# the cue file writes world: points in Blender axes (Z up, facing -Y)
	check(SfxLibrary.world_local("world:-1.2,1.5,0").is_equal_approx(Vector3(-1.2, 0, -1.5)), "door_slam point: 1.5 m behind him")
	check(SfxLibrary.world_local("world:0,-1,0").is_equal_approx(Vector3(0, 0, 1)), "Blender -Y (his front) is Godot +Z")
	check(SfxLibrary.world_local("world:0,0,2").is_equal_approx(Vector3(0, 2, 0)), "Blender Z up is Godot Y up")
	check(SfxLibrary.world_local("world: 0.5 , 0 , 0").is_equal_approx(Vector3(0.5, 0, 0)), "x is unchanged, spaces allowed")


func test_sfx_gain_and_pitch() -> void:
	var lib := _library()
	var pitches := []
	for run in 2:
		var c := _terence()
		var p := _cue_player(c, lib.terence_table(), lib, _pool())
		c.blender.start(&"loop_a")
		c.blender.step(0.6)
		var f: Dictionary = p.fired[1]   # the 0.5 s clap: -3 dB, 5% jitter
		check_eq((f["voice"] as AudioStreamPlayer3D).volume_db, -3.0, "gain_db applied")
		check(f["pitch"] >= 0.95 and f["pitch"] <= 1.05 and f["pitch"] != 1.0, "pitch jittered within 5%% (%s)" % f["pitch"])
		check_near((f["voice"] as AudioStreamPlayer3D).pitch_scale, f["pitch"], 1e-6, "pitch_scale applied")
		check_eq(p.fired[0]["pitch"], 1.0, "no jitter, no pitch change")
		pitches.append(f["pitch"])
	check_eq(pitches[0], pitches[1], "seeded jitter repeats")


func test_sfx_voice_cap() -> void:
	var lib := _library()
	var pool := _pool(3)
	var clap: AudioStream = lib.streams[&"clap"]
	var used := []
	for i in 5:
		used.append(pool.play(clap, Vector3(i, 0, 0), 0.0, 1.0))
	check_eq(pool.voices.size(), 3, "three voices exist")
	check_eq(pool.active_count(), 3, "never more than the cap")
	check_eq(used[3], used[0], "the 4th sound takes the oldest voice")
	check_eq(used[4], used[1], "the 5th takes the next oldest")
	check(used[3].global_position.is_equal_approx(Vector3(3, 0, 0)), "the stolen voice moved to the new sound")
	pool.step(0.2)
	check_eq(pool.active_count(), 0, "voices free once their sound is over")
	check_eq(pool.play(clap, Vector3.ZERO, 0.0, 1.0), used[0], "a free voice is reused first")
	var big := _pool()
	check_eq(big.voices.size(), 24, "default cap of 24 voices")
	var v := big.voices[0]
	check(v.unit_size >= 2.0 and v.unit_size <= 3.0 and v.max_distance == 20.0, "room-sized attenuation")
	check_eq(v.doppler_tracking, AudioStreamPlayer3D.DOPPLER_TRACKING_DISABLED, "no doppler")


func test_sfx_bus_layout() -> void:
	var i := AudioServer.get_bus_index(&"SFX")
	check(i > 0, "SFX bus exists")
	if i <= 0:
		return
	check_eq(AudioServer.get_bus_send(i), &"Master", "SFX sends to Master")
	# no reverb: its comb filters made every hit gurgle on the Quest
	check_eq(AudioServer.get_bus_effect_count(i), 0, "SFX is dry")
	check(AudioServer.get_bus_volume_db(i) < 0.0, "SFX leaves headroom")
