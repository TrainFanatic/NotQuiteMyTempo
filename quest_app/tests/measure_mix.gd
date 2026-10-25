extends SceneTree
## The game's mix levels, measured in the real scene: the seated camera, Terence's voice from
## his head bone (FletcherVoice, 3D), a song's backing.ogg on the Band bus (BackingPlayer, with
## its ducking under the Voice bus). Captures the Voice and Band buses (after the 3D
## attenuation and the bus volume) and prints RMS / peak in dBFS: the backing alone, then the
## backing ducked under a run of barks, and the voice over it. Numbers: AUDIO.md, "Levels".
## From quest_app/ (makes no sound in --headless):
##   Godot --headless --path . --script tests/measure_mix.gd -- song=../songs/bond_demo
## Exits 1 if the voice is not at least 6 dB over the ducked backing.

const FletcherSetup := preload("res://scripts/fletcher/fletcher_setup.gd")
const BARKS := ["no_1", "rushing_2", "stop_2", "no_2", "stop_1"]


func _init() -> void:
	_run.call_deferred()


func _arg(k: String, d: String) -> String:
	for a in OS.get_cmdline_user_args():
		if a.begins_with(k + "="):
			return a.substr(k.length() + 1)
	return d


func _capture(bus: StringName) -> AudioEffectCapture:
	var cap := AudioEffectCapture.new()
	cap.buffer_length = 10.0
	AudioServer.add_bus_effect(AudioServer.get_bus_index(bus), cap)
	return cap


static func _db(sumsq: float, n: int) -> float:
	return -INF if n == 0 or sumsq <= 0.0 else 10.0 * log(sumsq / n) / log(10.0)


## Drains a capture over `seconds`; returns [rms dB, peak dB].
func _measure(cap: AudioEffectCapture, seconds: float) -> Array:
	cap.clear_buffer()
	var sumsq := 0.0
	var n := 0
	var peak := 0.0
	var end := Time.get_ticks_msec() + int(seconds * 1000)
	while Time.get_ticks_msec() < end:
		await process_frame
		var k := cap.get_frames_available()
		if k > 0:
			for f in cap.get_buffer(k):
				sumsq += f.x * f.x + f.y * f.y
				peak = maxf(peak, maxf(absf(f.x), absf(f.y)))
				n += 2
	return [_db(sumsq, n), -INF if peak <= 0.0 else 20.0 * log(peak) / log(10.0)]


## What a capture holds right now (the last `seconds` of a bus): [rms dB, peak dB].
func _measure_since(cap: AudioEffectCapture) -> Array:
	var sumsq := 0.0
	var n := 0
	var peak := 0.0
	var k := cap.get_frames_available()
	if k > 0:
		for f in cap.get_buffer(k):
			sumsq += f.x * f.x + f.y * f.y
			peak = maxf(peak, maxf(absf(f.x), absf(f.y)))
			n += 2
	return [_db(sumsq, n), -INF if peak <= 0.0 else 20.0 * log(peak) / log(10.0)]


func _run() -> void:
	var song_dir := ProjectSettings.globalize_path(_arg("song", "res://../songs/bond_demo"))
	var R = load("res://scripts/rehearsal.gd")
	var r = R.new()
	r.sfx_enabled = false
	r.cinematics_enabled = false
	r.intro_enabled = false
	r.game_mode = true
	root.add_child(r)
	for i in 5:
		await process_frame
	var fl := FletcherSetup.new()
	r.add_child(fl)
	if not fl.setup(r.terence, r.director, "ws://127.0.0.1:1", false):
		quit(1)
		return
	await process_frame
	var cam: Camera3D = root.get_viewport().get_camera_3d()
	var dist := cam.global_position.distance_to(fl.head.global_position) if cam else NAN
	var backing: BackingPlayer = r.game.backing
	if not backing.load(song_dir):
		push_error("measure_mix: no backing.ogg in %s" % song_dir)
		quit(1)
		return
	# Bus effects run BEFORE the bus fader, so a capture sees the Band bus pre-volume and
	# pre-duck: both are added back from AudioServer / BackingPlayer.duck_db.
	var bi := AudioServer.get_bus_index(&"Band")
	var band_db := AudioServer.get_bus_volume_db(bi)
	var band := _capture(&"Band")
	var voice := _capture(&"Voice")
	backing.play_at(Time.get_ticks_usec() / 1e6 + 0.2, float(_arg("from", "30")))
	await _measure(band, 1.0)
	var alone: Array = await _measure(band, 4.0)
	var vpow := 0.0
	var vpeak := -INF
	var bpow := 0.0
	var ducks := []
	for clip in BARKS:
		fl.voice.handle_message("fletcher.speech", {"clip_id": clip, "utterance_id": clip})
		await process_frame
		voice.clear_buffer()
		var b: Array = await _measure(band, 0.5)
		var v: Array = await _measure_since(voice)
		ducks.append(backing.duck_db)
		vpow += pow(10.0, v[0] / 10.0)
		vpeak = maxf(vpeak, v[1])
		bpow += pow(10.0, (b[0] + backing.duck_db) / 10.0)
		await _measure(band, 0.8)  # let the duck release
	var v_rms := 10.0 * log(vpow / BARKS.size()) / log(10.0)
	var b_rms := 10.0 * log(bpow / BARKS.size()) / log(10.0) + band_db
	var b_alone: float = alone[0] + band_db
	print("mix: camera -> Terence's head %.2f m; voice unit_size %.1f (3D gain %+.1f dB); Band bus %.1f dB"
		% [dist, fl.voice.unit_size, minf(3.0, 20.0 * log(fl.voice.unit_size / dist) / log(10.0)), band_db])
	print("mix: backing alone   rms %.1f dBFS  peak %.1f (at Master)" % [b_alone, alone[1] + band_db])
	print("mix: backing ducked  rms %.1f dBFS  (duck %s dB)" % [b_rms, ", ".join(ducks.map(func(d): return "%.1f" % d))])
	print("mix: voice (barks)   rms %.1f dBFS  peak %.1f" % [v_rms, vpeak])
	print("mix: voice over ducked backing %+.1f dB, over the backing alone %+.1f dB" % [v_rms - b_rms, v_rms - b_alone])
	var ok := v_rms - b_rms >= 6.0
	backing.stop(0.0)
	r.queue_free()
	await process_frame
	quit(0 if ok else 1)
