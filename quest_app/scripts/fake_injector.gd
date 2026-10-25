class_name FakeInjector
extends Node
## Feeds timed messages into a SceneDirector, standing in for the real drum input / AI /
## voice systems. Load a timeline as an Array of {"at": seconds, ...message} or from a JSON
## file holding that array. Messages fire in order as time passes (or via advance()).

signal finished

var director: SceneDirector
var timeline: Array = []
var time := 0.0
var _next := 0
var failures: Array = []


func setup(target: SceneDirector, messages: Array) -> void:
	director = target
	timeline = messages.duplicate(true)
	timeline.sort_custom(func(a, b): return float(a["at"]) < float(b["at"]))
	time = 0.0
	_next = 0
	failures.clear()


func load_json(target: SceneDirector, path: String) -> bool:
	var text := FileAccess.get_file_as_string(path)
	if text.is_empty():
		push_error("FakeInjector: cannot read %s" % path)
		return false
	var data = JSON.parse_string(text)
	if not data is Array:
		push_error("FakeInjector: %s must hold a JSON array of messages" % path)
		return false
	setup(target, data)
	return true


func advance(delta: float) -> void:
	time += delta
	while _next < timeline.size() and float(timeline[_next]["at"]) <= time:
		var msg: Dictionary = timeline[_next].duplicate()
		msg.erase("at")
		if not director.handle_message(msg):
			failures.append(msg)
		_next += 1
		if _next == timeline.size():
			finished.emit()


func is_done() -> bool:
	return _next >= timeline.size()


func _process(delta: float) -> void:
	if director != null:
		advance(delta)
