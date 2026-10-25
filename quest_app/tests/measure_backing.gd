extends SceneTree
## The backing-track timing tests (tests/test_backing_player.gd) on a REAL audio driver, where
## the timing is meaningful (plain headless uses the Dummy driver, which isn't real time).
## Master is muted while it runs, so it makes no sound. From quest_app/:
##   Godot --headless --audio-driver CoreAudio --path . --script tests/measure_backing.gd
## (Linux: PulseAudio or ALSA; Windows: WASAPI.) Prints each measured offset; exits 1 on failure.


func _init() -> void:
	_run.call_deferred()


func _run() -> void:
	var script: GDScript = load("res://tests/test_backing_player.gd")
	var suite: TestCase = script.new()
	suite.tree = self
	print("driver %s, mix rate %d, reported output latency %.1f ms" % [AudioServer.get_driver_name(),
		AudioServer.get_mix_rate(), AudioServer.get_output_latency() * 1000.0])
	var total := 0
	for m in script.get_script_method_list():
		var mname: String = m["name"]
		if not mname.begins_with("test_"):
			continue
		suite.current = "test_backing_player." + mname
		var before := suite.failures.size()
		await suite.call(mname)
		total += 1
		print("%s %s" % ["PASS" if suite.failures.size() == before else "FAIL", suite.current])
	for c in root.get_children():
		c.queue_free()
	await process_frame
	print("\n%d tests, %d failed checks" % [total, suite.failures.size()])
	for line in suite.failures:
		print("  - " + line)
	quit(1 if not suite.failures.is_empty() else 0)
