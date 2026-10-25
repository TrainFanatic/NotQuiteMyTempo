extends SceneTree
## Connects to a running hub and reports what Fletcher sent and what the voice did.
##   Godot --headless --path fletcher/godot --script tests/live_client.gd -- ws://localhost:8798 40

var log := {"speech": 0, "barks": 0, "lines": 0, "chunks": 0, "gestures": [], "moves": [], "subtitles": []}


class Recorder:
	var msgs := []
	func handle_message(m: Dictionary) -> bool:
		msgs.append(m)
		return true


func _initialize() -> void:
	var args := OS.get_cmdline_user_args()
	var hub := FletcherHub.new()
	hub.url = args[0] if args.size() > 0 else "ws://localhost:8765"
	var seconds := float(args[1]) if args.size() > 1 else 30.0
	var voice := FletcherVoice.new()
	voice.barks_dir = ProjectSettings.globalize_path("res://").path_join("../barks")
	var link := FletcherLink.new()
	link.hub = hub
	link.voice = voice
	link.director = Recorder.new()
	root.add_child(hub)
	root.add_child(voice)
	root.add_child(link)
	hub.message_received.connect(func(type, d, _e):
		if type == "fletcher.speech":
			log["speech"] += 1
			log["barks" if d.has("clip_id") else "lines"] += 1
		elif type == "fletcher.audio" and d.get("pcm_b64", "") != "":
			log["chunks"] += 1)
	voice.gesture_cue.connect(func(g, _a, _l, _u): log["gestures"].append(g))
	voice.subtitle.connect(func(s): log["subtitles"].append(s))
	link.move_requested.connect(func(m): log["moves"].append(m))
	await create_timer(seconds).timeout
	print("LIVE ", JSON.stringify(log))
	quit(0)
