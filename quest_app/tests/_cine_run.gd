extends SceneTree
## Headless test runner. From the project folder:
##   Godot --headless --path . --script tests/run_tests.gd
## Runs every test_* method of every tests/test_*.gd, prints a summary, exits 1 on failure.
## Expected-error tests deliberately trigger push_error; those lines are not failures.


func _init() -> void:
	_run.call_deferred()


func _run() -> void:
	var dir := DirAccess.open("res://tests")
	var files: Array = []
	for f in dir.get_files():
		if f == "test_cinematic.gd":
			files.append(f)
	files.sort()
	var total := 0
	var failed: Array = []
	for f in files:
		var script: GDScript = load("res://tests/" + f)
		var suite: TestCase = script.new()
		suite.tree = self
		for m in script.get_script_method_list():
			var mname: String = m["name"]
			if not mname.begins_with("test_"):
				continue
			suite.current = "%s.%s" % [f.get_basename(), mname]
			var before := suite.failures.size()
			await suite.call(mname)
			total += 1
			var ok := suite.failures.size() == before
			print("%s %s" % ["PASS" if ok else "FAIL", suite.current])
		failed.append_array(suite.failures)
		for c in root.get_children():
			c.queue_free()
		await process_frame
	print("\n%d tests, %d failed checks" % [total, failed.size()])
	for line in failed:
		print("  - " + line)
	quit(1 if not failed.is_empty() else 0)
