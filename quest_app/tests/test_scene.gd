extends TestCase
## Rehearsal scene assembly and C4 director API through the fake injector.


func _scene() -> Rehearsal:
	var r := Rehearsal.new()
	r.run_demo = false
	r.sfx_enabled = false
	mount(r)
	return r


func _run(r: Rehearsal, seconds: float, dt: float = 1.0 / 30.0, inj: FakeInjector = null) -> void:
	var t := 0.0
	while t < seconds:
		if inj:
			inj.advance(dt)
		for m in r.band:
			m.step(dt)
		r.terence.step(dt)
		t += dt


func test_scene_assembles() -> void:
	var r := _scene()
	check_eq(r.band.size(), 16, "16 band members")
	var with_inst := r.band.filter(func(m): return m.instrument != null)
	check_eq(with_inst.size(), 15, "everyone but the pianist holds an instrument")
	for marker_name in Rehearsal.SEATING:
		var marker := r.marker_node(marker_name)
		var m: BandMember = r.band.filter(func(b): return b.member_name == Rehearsal.SEATING[marker_name][0])[0]
		check(m.global_position.distance_to(marker.global_position) < 1e-3, "%s at %s" % [m.member_name, marker_name])
		check(m.global_transform.basis.z.dot(-marker.global_transform.basis.z) > 0.99, "%s faces the marker's forward" % m.member_name)
	check(r.terence.global_position.distance_to(r.marker_node("spot_terence").global_position) < 1e-3, "Terence on his spot")
	check(r.rig.camera != null and r.rig.camera.current, "desktop camera active")


func test_director_messages() -> void:
	var r := _scene()
	var d := r.director
	var inj := FakeInjector.new()
	mount(inj)
	inj.setup(d, [
		{"at": 0.1, "type": "play_move", "id": "t0_countoff_finger"},
		{"at": 0.2, "type": "set_band_state", "target": "trumpet", "state": "playing"},
		{"at": 0.2, "type": "set_band_state", "target": "saxes", "state": "playing"},
		{"at": 0.3, "type": "set_tempo", "bpm": 180},
		{"at": 2.0, "type": "react", "target": "trombone", "reaction": "look_at_drummer"},
		{"at": 2.1, "type": "set_band_state", "target": "nobody", "state": "playing"},
		{"at": 2.2, "type": "dance"},
	])
	_run(r, 2.6, 1.0 / 30.0, inj)
	check(inj.is_done(), "timeline finished")
	check_eq(inj.failures.size(), 2, "bad target and unknown type are rejected")
	check_eq(d.members["band_trumpet_3"].current_state(), &"play_loop", "trumpets playing")
	check_eq(d.members["band_baritone_sax_1"].current_state(), &"play_loop", "saxes playing")
	check_eq(d.members["band_guitar_1"].current_state(), &"rest", "guitar untouched")
	check_eq(d.members["band_trombone_1"].current_state(), &"look_at_drummer", "trombones look at the drummer")
	check_eq(d.members["band_trumpet_1"].bpm, 180.0, "tempo reaches the band")
	check_eq(r.terence.bpm, 180.0, "tempo reaches Terence")


func test_song_drives_band() -> void:
	var r := _scene()
	var d := r.director
	check(d.load_song("res://tests/fixtures/type1_tracks.mid", {"Trumpets": ["trumpet"], "Trombones": ["trombone"]}), "song loads")
	check_eq(d.bpm, 100.0, "tempo from the file")
	var dt := 1.0 / 30.0
	var t := 0.0
	var trombone_states := {}
	while t < 7.0:
		d.set_song_position(t)
		for m in r.band:
			m.step(dt)
		trombone_states[snappedf(t, 0.5)] = d.members["band_trombone_1"].current_state()
		t += dt
	check_eq(trombone_states[1.0], &"play_loop", "trombones up for phrase 1")
	check_eq(trombone_states[3.0], &"rest", "trombones down in the rest")
	check_eq(trombone_states[5.5], &"play_loop", "trombones up for phrase 2")
	check_eq(d.members["band_trumpet_1"].current_state(), &"rest", "trumpets done by the end")
	check(not d.load_song("res://tests/fixtures/type1_tracks.mid", {"Saxes": ["saxes"]}), "missing track name is an error")
