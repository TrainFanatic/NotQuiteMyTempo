extends TestCase
## Latency calibration's headset side (scripts/game/calibration_player.gd): calibrate.start
## {times} (laptop clock) -> one clap per time on the Band bus, heard at to_local_time(t), and a
## calibrate.ack. Heard times are measured with test_backing_player.gd's OutputProbe. Plain
## headless (the Dummy driver, not real time: 93 ms mixes, ~4 % slow) only checks the claps
## land roughly; on a real driver (CoreAudio, input off) they landed within 0.1 ms (2026-10-24).

const Probe := preload("res://tests/test_backing_player.gd")
const CalibrationPlayer := preload("res://scripts/game/calibration_player.gd")


## FletcherHub's surface that the player uses: message_received, send, to_local_time.
class FakeHub:
	extends Node
	signal message_received(type: String, data: Dictionary, envelope: Dictionary)
	var clock_offset := 100.0   # device clock = laptop clock + 100 s
	var sent: Array = []

	func send(type: String, data: Dictionary) -> bool:
		sent.append([type, data])
		return true

	func to_local_time(t: float) -> float:
		return t + clock_offset


func _now() -> float:
	return Time.get_ticks_usec() / 1e6


func _frames_for(seconds: float) -> void:
	var end := _now() + seconds
	while _now() < end:
		await tree.process_frame


func test_clap_train_has_a_clap_at_each_time() -> void:
	var w: AudioStreamWAV = CalibrationPlayer.clap_train([0.1, 0.6])
	check_eq(w.mix_rate, 48000, "48 kHz")
	var peak := func(s: float) -> int:   # loudest sample in the 2 ms from s
		var m := 0
		for i in 96:
			m = maxi(m, absi(w.data.decode_s16((int(s * 48000) + i) * 2)))
		return m
	check(peak.call(0.1) > 10000, "loud at the first clap")
	check(peak.call(0.6) > 10000, "loud at the second")
	check_eq(peak.call(0.4), 0, "silent between")
	check_eq(peak.call(0.098), 0, "silent just before")


func test_claps_heard_at_the_scheduled_times() -> void:
	var probe = Probe.OutputProbe.new()
	mount(probe)
	var hub := FakeHub.new()
	mount(hub)
	var cal = CalibrationPlayer.new()
	cal.hub = hub
	mount(cal)
	await _frames_for(0.3)
	var laptop_now := _now() - hub.clock_offset
	var times := [laptop_now + 0.5, laptop_now + 1.0, laptop_now + 1.5]
	hub.message_received.emit("calibrate.start", {"id": "x1", "times": times}, {})
	check_eq(hub.sent.size(), 1, "one ack")
	if hub.sent.size() == 1:
		check_eq(hub.sent[0][0], "calibrate.ack", "ack type")
		check_eq(hub.sent[0][1]["n"], 3, "ack counts the claps")
		check_eq(hub.sent[0][1]["id"], "x1", "ack echoes the id")
	await _frames_for(2.2)
	var tol := 0.25 if AudioServer.get_driver_name() == "Dummy" else 0.004
	for i in times.size():
		var want: float = hub.to_local_time(times[i])
		var heard: float = probe.heard_onset(want - 0.2, 0.05)
		print("  [calibration] clap %d: %+.2f ms" % [i, (heard - want) * 1000.0])
		check_near(heard, want, tol, "clap %d heard on time" % i)
	cal.queue_free()
	probe.queue_free()
	hub.queue_free()
	await tree.process_frame


func test_no_times_is_refused() -> void:
	var hub := FakeHub.new()
	mount(hub)
	var cal = CalibrationPlayer.new()
	cal.hub = hub
	mount(cal)
	hub.message_received.emit("calibrate.start", {"id": "x2"}, {})
	check(hub.sent.size() == 1 and hub.sent[0][1].has("error"), "error ack")
	cal.queue_free()
	hub.queue_free()
	await tree.process_frame
