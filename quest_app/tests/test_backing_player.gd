extends TestCase
## The backing track (scripts/game/backing_player.gd): play_at() lands a song time on a wall-
## clock moment, seek(), stop(), the click on the same timeline, the playback clock, ducking
## under the Voice bus. Real frames and real mixing (headless: Godot's Dummy driver, which
## mixes 4096-frame blocks, ~93 ms; on a desktop with `--audio-driver CoreAudio` 512 frames).
##
## How "heard at" is measured: an OutputProbe puts an AudioEffectCapture on the Band bus and,
## every frame, notes when the last mix began (now - get_time_since_last_mix()) and how many
## frames the capture had been pushed by then. That ties every captured frame to the moment it
## was mixed; + output latency is when it is heard (Godot's model; the acoustic part is in
## AUDIO.md). A marker's "heard" time is its first captured frame above MARK_LEVEL.
## Fixtures: tests/fixtures/backing (make_backing_fixtures.py): tone bursts at known times.

const DIR := "res://tests/fixtures/backing"
const MARKS := [1.0, 2.0, 3.0]         # burst onsets in backing.ogg (song s)
const CLICKS := [0.5, 1.5, 2.5, 3.5]   # ... and in click.ogg
const MARK_LEVEL := 0.1                # bursts peak at 0.5
const TOL := 0.004                     # a few ms, on a real audio driver
## The Dummy driver (plain headless) sleeps between its 93 ms mixes, so its audio runs ~4 %
## slower than the wall clock and its mix times wobble +-3 ms: there the checks are coarse (a
## second of play is 40 ms of drift, which song_time() follows with clock_tau_s). The precise
## run is on a real driver, muted: tests/measure_backing.gd (see its header).
const TOL_DUMMY := 0.1


## Captures what the Band bus mixes and when.
class OutputProbe:
	extends Node
	var bus_idx := -1
	var cap: AudioEffectCapture
	var keepalive: AudioStreamPlayer
	var frames := PackedVector2Array()
	var marks: Array = []   # [frame index of a mix's first frame, local time that mix began]
	var rate := 48000.0

	func _ready() -> void:
		rate = AudioServer.get_mix_rate()
		bus_idx = AudioServer.get_bus_index(&"Band")
		cap = AudioEffectCapture.new()
		cap.buffer_length = 8.0
		AudioServer.add_bus_effect(bus_idx, cap)
		# a constant -90 dB DC keeps the bus mixing (and the capture fed) while nothing plays
		var w := AudioStreamWAV.new()
		w.format = AudioStreamWAV.FORMAT_16_BITS
		w.mix_rate = int(rate)
		var d := PackedByteArray()
		d.resize(2 * 4800)
		for i in 4800:
			d.encode_s16(2 * i, 1)
		w.data = d
		w.loop_mode = AudioStreamWAV.LOOP_FORWARD
		w.loop_end = 4800
		keepalive = AudioStreamPlayer.new()
		keepalive.stream = w
		keepalive.bus = &"Band"
		add_child(keepalive)
		keepalive.play()
		if AudioServer.get_driver_name() != "Dummy":
			AudioServer.set_bus_mute(0, true)  # measure without making noise

	func _process(_d: float) -> void:
		var since := AudioServer.get_time_since_last_mix()
		var period := since + AudioServer.get_time_to_next_mix()  # = that mix's frames / rate
		var pushed := cap.get_pushed_frames()
		var now := Time.get_ticks_usec() / 1e6
		if pushed > 0 and (marks.is_empty() or marks[-1][2] != pushed):
			marks.append([pushed - roundi(period * rate), now - since, pushed])
		var n := cap.get_frames_available()
		if n > 0:
			frames.append_array(cap.get_buffer(n))

	func _exit_tree() -> void:
		AudioServer.set_bus_mute(0, false)
		for i in AudioServer.get_bus_effect_count(bus_idx):
			if AudioServer.get_bus_effect(bus_idx, i) == cap:
				AudioServer.remove_bus_effect(bus_idx, i)
				return

	## Local time frame f was mixed (median over the nearby mix marks).
	func mixed_at(f: int) -> float:
		var offs := []
		for m in marks:
			if absi(m[0] - f) < int(rate):
				offs.append(m[1] - m[0] / rate)
		if offs.is_empty():
			return NAN
		offs.sort()
		return offs[offs.size() / 2] + f / rate

	## Heard time of the first onset above `level` heard at or after local time `after`, or NAN.
	func heard_onset(after: float, level: float = MARK_LEVEL) -> float:
		var f := 0
		while f < frames.size():
			if absf(frames[f].x) > level:
				var t := mixed_at(f) + AudioServer.get_output_latency()
				if t >= after:
					return t
				f += int(0.05 * rate)  # past this burst
			else:
				f += 1
		return NAN


func _now() -> float:
	return Time.get_ticks_usec() / 1e6


func _tol() -> float:
	return TOL_DUMMY if AudioServer.get_driver_name() == "Dummy" else TOL


var _live: Array = []


## A fresh probe + player (the previous test's are freed first: one capture on the bus at a time).
func _rig() -> Array:
	for n in _live:
		if is_instance_valid(n):
			n.queue_free()
	_live.clear()
	await tree.process_frame
	var probe := OutputProbe.new()
	mount(probe)
	var bp := BackingPlayer.new()
	mount(bp)
	check(bp.load(DIR), "loads the fixture backing")
	_live = [bp, probe]
	await _frames_for(0.3)  # the probe gets its first mixes
	return [bp, probe]


func _frames_for(seconds: float) -> void:
	var end := _now() + seconds
	while _now() < end:
		await tree.process_frame


func _report(what: String, err: float) -> void:
	print("  [backing] %s: %+.2f ms" % [what, err * 1000.0])


func test_band_bus_layout() -> void:
	var i := AudioServer.get_bus_index(&"Band")
	check(i > 0, "Band bus exists")
	check_eq(AudioServer.get_bus_send(i), &"Master", "Band sends to Master")
	check(AudioServer.get_bus_index(&"SFX") > 0 and AudioServer.get_bus_index(&"Voice") > 0, "SFX and Voice still there")


func test_play_at_lands_on_time() -> void:
	var r: Array = await _rig()
	var bp: BackingPlayer = r[0]
	var probe: OutputProbe = r[1]
	var t := _now() + 0.5
	bp.play_at(t, 0.8)   # song 1.0 heard at t + 0.2, song 2.0 at t + 1.2
	check_eq(bp.state, BackingPlayer.State.SCHEDULED, "waits for its mix")
	await _frames_for(2.0)
	check_eq(bp.state, BackingPlayer.State.PLAYING, "playing")
	for m in [1.0, 2.0]:
		var e: float = probe.heard_onset(t - 0.1) if m == 1.0 else probe.heard_onset(t + 0.6)
		e -= t + (m - 0.8)
		_report("play_at, song %.1f s" % m, e)
		check_near(e, 0.0, _tol(), "song %.1f heard on time" % m)


func test_play_at_from_the_top() -> void:
	var r: Array = await _rig()
	var bp: BackingPlayer = r[0]
	var probe: OutputProbe = r[1]
	var t := _now() + 0.3
	bp.play_at(t, 0.0)
	await _frames_for(1.5)
	var e := probe.heard_onset(t) - (t + 1.0)
	_report("play_at from song 0, song 1.0", e)
	# from the very top the start may be early by up to half a frame (no audio before 0)
	check_near(e, 0.0, _tol(), "song 1.0 heard on time")


func test_play_at_in_the_past_starts_seeked() -> void:
	var r: Array = await _rig()
	var bp: BackingPlayer = r[0]
	var probe: OutputProbe = r[1]
	var t := _now() - 0.3
	bp.play_at(t, 0.5)   # song 1.0 belongs at t + 0.5 = now + 0.2
	check_eq(bp.state, BackingPlayer.State.PLAYING, "starts at once")
	await _frames_for(0.8)
	var e := probe.heard_onset(_now() - 1.0) - (t + 0.5)
	_report("play_at 0.3 s late, song 1.0", e)
	check_near(e, 0.0, _tol(), "skipped ahead to where the song is by now")


func test_seek_and_click() -> void:
	var r: Array = await _rig()
	var bp: BackingPlayer = r[0]
	var probe: OutputProbe = r[1]
	bp.click_enabled = true
	var t := _now() + 0.3
	bp.play_at(t, 1.2)    # click 1.5 at t + 0.3, mark 2.0 at t + 0.8
	await _frames_for(1.0)
	var e := probe.heard_onset(t) - (t + 0.3)
	_report("click 1.5 on the backing timeline", e)
	check_near(e, 0.0, _tol(), "click on the same timeline")
	var t2 := _now() + 0.25
	bp.seek(2.8, t2)      # mark 3.0 at t2 + 0.2 (nothing between 2.8 and 3.0)
	await _frames_for(0.8)
	e = probe.heard_onset(t2 - 0.05) - (t2 + 0.2)
	_report("seek to 2.8, song 3.0", e)
	check_near(e, 0.0, _tol(), "seek lands on time")


## The clock the band locks to: no mix-sized steps, and it agrees with what is heard.
func test_song_time_is_smooth_and_right() -> void:
	var r: Array = await _rig()
	var bp: BackingPlayer = r[0]
	var probe: OutputProbe = r[1]
	var t := _now() + 0.2
	bp.play_at(t, 0.6)
	check_near(bp.song_time(), 0.4, 0.01, "counts up to the target before the start")
	var samples := []  # [now, song_time, naive]
	var end := t + 3.0
	while _now() < end:
		await tree.process_frame
		if bp.is_playing():
			var p: AudioStreamPlayer = bp.get_node("Backing")
			var naive := p.get_playback_position() + AudioServer.get_time_since_last_mix() - AudioServer.get_output_latency()
			samples.append([_now(), bp.song_time(), naive])
	check(samples.size() > 20, "sampled while playing")
	var worst := 0.0
	var worst_naive := 0.0
	for i in range(1, samples.size()):
		var dt: float = samples[i][0] - samples[i - 1][0]
		worst = maxf(worst, absf(samples[i][1] - samples[i - 1][1] - dt))
		worst_naive = maxf(worst_naive, absf(samples[i][2] - samples[i - 1][2] - dt))
	print("  [backing] song_time step jitter: worst %.2f ms (raw position+since_last_mix: %.2f ms)" % [worst * 1000.0, worst_naive * 1000.0])
	check(worst < 0.002, "song_time has no steps (worst %.2f ms)" % (worst * 1000.0))
	# when mark 2.0 / 3.0 is heard, song_time() reads 2.0 / 3.0
	for m in [2.0, 3.0]:
		var heard := probe.heard_onset(t + (m - 0.6) - 0.3)
		var st := NAN
		for i in range(1, samples.size()):
			if samples[i][0] >= heard:
				var a: Array = samples[i - 1]
				var b: Array = samples[i]
				st = lerpf(a[1], b[1], (heard - a[0]) / maxf(b[0] - a[0], 1e-6))
				break
		_report("song_time() when song %.1f is heard (minus %.1f)" % [m, m], st - m)
		check_near(st, m, _tol(), "song_time agrees with the audio at %.1f" % m)


func test_stop_fades_and_holds() -> void:
	var r: Array = await _rig()
	var bp: BackingPlayer = r[0]
	var probe: OutputProbe = r[1]
	bp.play_at(_now(), 0.9)
	await _frames_for(0.3)
	bp.stop(0.1)
	check_eq(bp.state, BackingPlayer.State.STOPPED, "stopped")
	var held := bp.song_time()
	await _frames_for(1.4)  # marks at 2.0 must not sound
	check_eq(bp.song_time(), held, "song_time holds after stop")
	check(not (bp.get_node("Backing") as AudioStreamPlayer).playing, "player stopped after the fade")
	check(is_nan(probe.heard_onset(_now() - 0.6)), "nothing after the fade")


func test_plays_to_the_end() -> void:
	var r: Array = await _rig()
	var bp: BackingPlayer = r[0]
	var ended := [false]
	bp.finished.connect(func(): ended[0] = true)
	bp.play_at(_now(), 3.5)
	await _frames_for(1.0)
	check(ended[0], "finished emitted")
	check_eq(bp.state, BackingPlayer.State.FINISHED, "finished")
	bp.play_at(_now(), 9.0)
	check_eq(bp.state, BackingPlayer.State.FINISHED, "past the end: nothing to play")


func test_loads_a_download() -> void:
	await _rig()
	DirAccess.make_dir_recursive_absolute("user://test_songs/fixture")
	for f in ["backing.ogg", "click.ogg"]:
		var b := FileAccess.get_file_as_bytes(DIR.path_join(f))
		if b.is_empty():  # an exported build keeps only the imported copy; nothing to test then
			return
		var out := FileAccess.open("user://test_songs/fixture".path_join(f), FileAccess.WRITE)
		out.store_buffer(b)
		out.close()
	var bp := BackingPlayer.new()
	mount(bp)
	check(bp.load("user://test_songs/fixture"), "loads from user://")
	check_near(bp.length(), 4.0, 0.01, "4 s long")
	check(not (bp.get_node("Backing").stream as AudioStreamOggVorbis).loop, "does not loop")
	check(not bp.load("user://test_songs/none"), "a missing song is false")
	bp.play_at(_now(), 0.0)
	check_eq(bp.state, BackingPlayer.State.IDLE, "nothing loaded: play_at does nothing")
	for f in ["backing.ogg", "click.ogg"]:
		DirAccess.remove_absolute("user://test_songs/fixture".path_join(f))


func test_ducks_under_voice() -> void:
	await _rig()  # frees the last test's player: only one may duck the bus
	for n in _live:
		n.queue_free()
	await tree.process_frame
	var bp := BackingPlayer.new()
	mount(bp)
	var band := AudioServer.get_bus_index(&"Band")
	var base := AudioServer.get_bus_volume_db(band)
	var w := AudioStreamWAV.new()  # a loud steady tone on the Voice bus
	w.format = AudioStreamWAV.FORMAT_16_BITS
	w.mix_rate = 22050
	var d := PackedByteArray()
	d.resize(2 * 2205)
	for i in 2205:
		d.encode_s16(2 * i, int(12000 * sin(TAU * 441.0 * i / 22050.0)))
	w.data = d
	w.loop_mode = AudioStreamWAV.LOOP_FORWARD
	w.loop_end = 2205
	AudioServer.set_bus_mute(0, AudioServer.get_driver_name() != "Dummy")  # no noise on a real driver
	var v := AudioStreamPlayer.new()
	v.stream = w
	v.bus = &"Voice"
	mount(v)
	v.play()
	await _frames_for(0.6)
	var ducked := AudioServer.get_bus_volume_db(band) - base
	print("  [backing] Band under a loud voice: %.1f dB" % ducked)
	check(ducked < -4.0 and ducked >= -bp.duck_max_db - 0.01, "Band ducked, at most duck_max_db (%.1f dB)" % ducked)
	v.stop()
	await _frames_for(2.5)
	check_near(AudioServer.get_bus_volume_db(band), base, 0.3, "Band back to its own volume")
	bp.queue_free()
	await tree.process_frame
	check_near(AudioServer.get_bus_volume_db(band), base, 0.001, "volume restored on exit")
	AudioServer.set_bus_mute(0, false)
