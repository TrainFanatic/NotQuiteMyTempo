extends Node
## Fletcher, live from the team hub (Rehearsal.fletcher_enabled, or `-- --fletcher [url]`).
## Fletcher is the bandleader's mind and voice, running on the laptop (fletcher/ in the repo);
## the hub (hub/server.py) relays his speech, audio and body cues over a WebSocket.
##   FletcherHub    WebSocket client, reconnects every second, so no server is fine
##   FletcherVoice  barks, streamed lines and timed gesture cues, on a BoneAttachment3D on
##                  Terence's Head bone, every player on the Voice bus
##   FletcherLink   Fletcher's actions and cues -> SceneDirector, one move at a time
## Those three are generated copies (fletcher/godot/sync_to_quest_app.sh); this file is not.
## Holding: while the intro runs (setup with hold = true) the link does not exist yet. The
## voice still hears the hub, so Fletcher can talk, but his body cues have nobody to fire
## into and are dropped, and Terence is left to the entrance. release() adds the link and
## replays the latest session.state and plain fletcher.state, so tempo, band and mood are
## current. Loaded with preload() by its users, so no global class cache entry is needed.
## `-- --fletcher-log` prints Terence's moves and the voice's events, one line each with the
## laptop clock (hub `t`), for hub/full_e2e.py: `[fletcher-log] <laptop s> <event> <args>`.
## `-- --fletcher-quit-on-close` quits the app when the hub closes the connection (the test's
## clean shutdown: Godot has no SIGINT/SIGTERM handler, a signal just kills it).

const HEAD_BONE := "Head"
const BUS := &"Voice"
const ARG := "--fletcher"
const LOG_ARG := "--fletcher-log"
const QUIT_ARG := "--fletcher-quit-on-close"

var hub: FletcherHub
var voice: FletcherVoice
var link: FletcherLink
var head: BoneAttachment3D
## type -> [data, envelope]: the newest session.state and plain fletcher.state while held.
var _held := {}


## Whether the command line asks for Fletcher: `-- --fletcher`, `-- --fletcher ws://host:8765`
## or `-- --fletcher=ws://host:8765`.
static func requested() -> bool:
	for a in OS.get_cmdline_user_args():
		if a == ARG or a.begins_with(ARG + "="):
			return true
	return false


## The hub URL from the command line, or "" (FletcherHub's own default).
static func requested_url() -> String:
	var args := OS.get_cmdline_user_args()
	for i in args.size():
		if args[i].begins_with(ARG + "="):
			return args[i].substr(ARG.length() + 1)
		if args[i] == ARG and i + 1 < args.size() and not args[i + 1].begins_with("--"):
			return args[i + 1]
	return ""


## Builds the hub, the voice on Terence's head and (unless `hold`) the link. `url` "" keeps
## FletcherHub's default. False, with an error, when something is missing.
func setup(terence: Terence, director: SceneDirector, url: String, hold: bool) -> bool:
	name = "Fletcher"
	if terence == null or terence.skeleton == null or director == null:
		push_error("Fletcher: the scene did not build (no Terence or director)")
		return false
	if AudioServer.get_bus_index(BUS) < 0:
		push_error("Fletcher: no '%s' audio bus (res://default_bus_layout.tres)" % BUS)
		return false
	if terence.skeleton.find_bone(HEAD_BONE) < 0:
		push_error("Fletcher: Terence's skeleton has no %s bone" % HEAD_BONE)
		return false
	head = BoneAttachment3D.new()
	head.name = "FletcherHead"
	head.bone_name = HEAD_BONE
	terence.skeleton.add_child(head)
	hub = DemoHub.new() if DemoHub.requested() else FletcherHub.new()   # judge demo: a recorded session
	hub.name = "FletcherHub"
	if url != "":
		hub.url = url
	add_child(hub)
	voice = FletcherVoice.new()
	voice.name = "FletcherVoice"
	head.add_child(voice)   # its _ready makes the players
	# its own players (stream, bark, count-off words): never the SFX pool's voices
	for p in voice.find_children("*", "AudioStreamPlayer3D", false, false):
		(p as AudioStreamPlayer3D).bus = BUS
	link = FletcherLink.new()
	link.name = "FletcherLink"
	link.voice = voice
	link.director = director
	link.terence = terence
	link.moves_json = Terence.MOVES_JSON
	if hold:
		hub.message_received.connect(_on_held_message)
	else:
		_add_link()
	if LOG_ARG in OS.get_cmdline_user_args():
		_start_log(terence)
	if QUIT_ARG in OS.get_cmdline_user_args():
		hub.disconnected.connect(func(): print("[FletcherSetup] hub closed, quitting (%s)" % QUIT_ARG); get_tree().quit())
	return true


func is_held() -> bool:
	return link != null and not link.is_inside_tree()


## The intro is over: Fletcher may move Terence from now on.
func release() -> void:
	if hub is DemoHub:
		hub.start()
	if not is_held():
		return
	hub.message_received.disconnect(_on_held_message)
	_add_link()
	for type in ["session.state", "fletcher.state"]:
		if _held.has(type):
			hub.message_received.emit(type, _held[type][0], _held[type][1])
	_held.clear()


func _add_link() -> void:
	link.hub = hub
	add_child(link)


func _on_held_message(type: String, data: Dictionary, envelope: Dictionary) -> void:
	if type.begins_with("fletcher."):
		voice.handle_message(type, data)
	if type == "session.state" or (type == "fletcher.state" and not data.has("gesture")):
		_held[type] = [data, envelope]


## ---- `-- --fletcher-log` ---------------------------------------------------

func _start_log(terence: Terence) -> void:
	terence.move_started.connect(func(id): _log("move_started", str(id)))
	terence.move_finished.connect(func(id): _log("move_finished", str(id)))
	voice.speaking.connect(func(uid): _log("speaking", uid))
	voice.finished.connect(func(uid): _log("finished", uid))
	voice.subtitle.connect(func(text): _log("subtitle", text))
	voice.countoff_word.connect(func(clip): _log("countoff_word", clip))
	link.move_requested.connect(func(id): _log("move_requested", id))
	link.move_dropped.connect(func(id, late): _log("move_dropped", "%s %.3f" % [id, late]))
	hub.connected.connect(func(): _log("hub_connected", hub.url))
	hub.message_received.connect(func(type, data, _env):
		if type == "session.state" or type == "session.command":
			_log("hub_" + type, str(data.get("state", data.get("action", "")))))


## One line on stdout: laptop seconds (local clock minus the hub clock offset; "nan" before
## the first hub frame), the event and its argument.
func _log(event: String, arg: String) -> void:
	var local := Time.get_ticks_usec() / 1e6
	var laptop := "nan" if hub.clock_offset == INF else "%.4f" % (local - hub.clock_offset)
	print("[fletcher-log] %s %s %s" % [laptop, event, arg.replace("\n", " ")])
