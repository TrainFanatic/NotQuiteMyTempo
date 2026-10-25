extends TestCase
## MicStreamer (scripts/game/mic_streamer.gd): the Quest side of mic.pcm. Headless has no
## microphone, so captured frames are pushed in by hand through push_frames(), the same call
## _process makes with what AudioEffectCapture returns.


class FakeHub:
	extends RefCounted
	var open := true
	var clock_offset := 123.25
	var frames: Array = []

	func send(type: String, data: Dictionary) -> bool:
		if open:
			frames.append([type, data])
		return open


func _streamer(hub: FakeHub, rate := 44100) -> MicStreamer:
	var m := MicStreamer.new()
	m.hub = hub
	mount(m)
	m.sample_rate = rate  # whatever the headless mixer said; the Quest's is 44.1 kHz
	return m


func _block(start: int, n: int) -> PackedVector2Array:
	var b := PackedVector2Array()
	for i in n:
		var v := float((start + i) % 1000) / 1000.0 - 0.5
		b.append(Vector2(v, v * 0.5))  # mono = mean = 0.75 v
	return b


func _decode(b64: String) -> PackedInt32Array:
	var raw := Marshalls.base64_to_raw(b64)
	var out := PackedInt32Array()
	for i in raw.size() / 2:
		out.append(raw.decode_s16(i * 2))
	return out


func test_frames_are_50ms_contiguous_mono_int16_with_clock() -> void:
	var hub := FakeHub.new()
	var m := _streamer(hub)
	var fed := 0
	var t := 10.0
	for n in [100, 3000, 1, 2204, 5000, 999, 4096]:
		t += float(n) / 44100.0 + 0.003  # each pull comes a little after its mix
		m.push_frames(_block(fed, n), t)
		fed += n
	check_eq(hub.frames.size(), fed / 2205, "one frame per complete 2205 samples")
	var next := 0
	for k in hub.frames.size():
		var f: Array = hub.frames[k]
		var d: Dictionary = f[1]
		check_eq(f[0], "mic.pcm", "type")
		check_eq(d["seq"], k + 1, "seq")
		check_eq(d["rate"], 44100, "rate")
		check_eq(d["channels"], 1, "mono")
		check_eq(d["first_sample"], next, "first_sample is contiguous")
		check_eq(d["clock_offset"], 123.25, "the hub's clock offset rides along")
		var pcm := _decode(d["pcm_b64"])
		check_eq(pcm.size(), 2205, "50 ms at 44.1 kHz")
		var i := 1234 % 2205
		var want := int(round((float((next + i) % 1000) / 1000.0 - 0.5) * 0.75 * 32767.0))
		check_eq(pcm[i], want, "sample = mean of the channels, 16-bit")
		next += pcm.size()
	# local_t is linear in first_sample (one epoch); the epoch is the earliest-looking pull:
	# the first push, at 10.0 + 100/44100 + 0.003 with 100 samples captured
	var first: Dictionary = hub.frames[0][1]
	var last: Dictionary = hub.frames[-1][1]
	check_near(first["local_t"], 10.003, 1e-4, "local_t of sample 0")
	check_near(last["local_t"] - first["local_t"], float(last["first_sample"]) / 44100.0, 1e-4,
		"local_t advances with the sample count")
	m.queue_free()
	await tree.process_frame


func test_discarded_capture_frames_become_a_gap_and_hub_loss_a_seq_gap() -> void:
	var hub := FakeHub.new()
	var m := _streamer(hub, 48000)
	m.push_frames(_block(0, 3000), 1.0)  # one 2400 frame sent, 600 pending
	m.note_discarded(500)                # pending 600 goes out short, then the index jumps
	hub.open = false
	m.push_frames(_block(0, 2400), 1.1)  # lost: the hub is down
	hub.open = true
	m.push_frames(_block(0, 2400), 1.2)
	var firsts := []
	var seqs := []
	for f in hub.frames:
		firsts.append(f[1]["first_sample"])
		seqs.append(f[1]["seq"])
	check_eq(firsts, [0, 2400, 5900], "the discard skips 500 samples; the lost frame took 3500..5900")
	check_eq(seqs, [1, 2, 4], "seq counts the lost frame")
	check_eq(_decode(hub.frames[1][1]["pcm_b64"]).size(), 600, "the partial frame before the gap")
	check_eq(m.lost_frames, 1, "lost frames counted")
	check_eq(m.discarded_samples, 500, "discarded samples counted")
	m.queue_free()
	await tree.process_frame


func test_runtime_bus_is_muted_has_capture_and_goes_away() -> void:
	var m := _streamer(FakeHub.new())
	var idx := AudioServer.get_bus_index(&"QuestMicStream")
	check(idx >= 0, "bus made")
	check(AudioServer.is_bus_mute(idx), "bus muted: the mic is never heard")
	check(AudioServer.get_bus_effect(idx, 0) is AudioEffectCapture, "capture effect")
	m.queue_free()
	await tree.process_frame
	check_eq(AudioServer.get_bus_index(&"QuestMicStream"), -1, "bus removed with the node")
