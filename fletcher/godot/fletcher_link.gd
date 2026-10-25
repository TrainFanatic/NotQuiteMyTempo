class_name FletcherLink
extends Node
## Glue between the hub and Adam's SceneDirector: routes fletcher.* messages
## to FletcherVoice and turns Fletcher's actions, timed gesture cues and the
## session state into director.handle_message() calls. Owned by the Fletcher
## module; the move tables are a starting point for Adam to tune.
##
##   var link := FletcherLink.new()
##   link.hub = $FletcherHub; link.voice = $Terence/Head/FletcherVoice; link.director = director
##   add_child(link)
##
## Move queue (one Terence move at a time):
## - A due request starts now if Terence is idle, in a looping move, or in a
##   one-shot of lower priority. Otherwise it waits for that one-shot to finish,
##   and is dropped if by then it is more than max_late_s past its anchor (unless
##   priority 2). Priorities: 0 ambient, 1 normal, 2 must play.
## - With the real Terence (director.terence, or set `terence`) "started" and
##   "finished" are his move_started/move_finished signals, so a close-up move is
##   not playing while he still walks to the player. Without him (tests, a fake
##   director) one-shots last their moves.json length, scaled by the tempo.
## - A request for the move already playing (or already queued) counts as done.
## - Holds (terence_v004): a one-shot with hold_start/release events (t1_fist_cutoff, t1_fist_slam,
##   t1_flick, t1_wave_off, t1_clap_stop) holds its pose from hold_start to release. Requests
##   arriving before the release wait for it and count as due then (not stale); from the
##   release frame on, the move gives way to whatever is queued, as if it had ended.
## - When a line ends and nothing is queued or pending, he falls back to the
##   current action's looping move (glare -> judging stare, ...); while the band
##   plays, always back to conducting.
## - No walking by default: gesture names resolve to moves he plays where he stands
##   (set `closeups` for the walking t3_* versions). Raw move ids from Fletcher are
##   played as sent; Fletcher only sends close-ups when its own `closeups` is on.
## - Cutoff: the band freezes on the fist's "cutoff" frame and stays frozen through the hold,
##   lowering at its "release" frame. A session stop that arrives while that fist is coming
##   (playing, queued, or waiting in the voice for the Stop bark) leaves the band to it.
## - Key frames all come from moves.json at runtime (fist "cutoff"/"release", the finger's
##   "finger_up"); nothing here hard-codes a frame number.
## - Count-off: a fletcher.state cue with `t0` (laptop clock) + `bpm` schedules the
##   finger move at t0 - 5 beats and the count-off words (voice.play_countoff) so their
##   onsets sit on beats -4..-1. The finger never makes him walk: if he is away from
##   home it waits until he is back.

signal move_requested(id: String)  ## every play_move this node issues (handy for tests/logs)
signal move_dropped(id: String, late_s: float)  ## a queued request that went stale

@export var hub: FletcherHub
@export var voice: FletcherVoice
@export var moves_json := "res://assets/terence/terence_v004_moves.json"
@export var max_late_s := 1.5     ## a queued cue later than this past its anchor is dropped (priority < 2)
@export var start_timeout_s := 12.0  ## a requested move that never starts (no route...) is given up
@export var closeups := false  ## gesture names may resolve to close-ups that walk him to the drummer
var director: Object  ## SceneDirector (anything with handle_message(Dictionary) -> bool)
var terence: Object   ## Adam's Terence; taken from director.terence when not set

## fletcher.state action (contract ACTIONS) -> Terence move. lean_in walks him
## to the kit, so while the band plays it falls back to a stare.
const ACTION_MOVES := {
	"conduct": "t0_conduct", "glare": "t2_judging_stare", "lean_in": "t2_judging_stare",
	"point": "t2_point_player", "fist_cutoff": "t1_fist_cutoff", "walk_toward": "t4_charge",
	"nod": "ap_nod", "slow_clap": "t1_clap_stop",
}
## Gesture cue from a tirade (fletcher/tirade.py GESTURES) -> Terence move.
## A gesture that is not listed here but is a Terence move id is played as is.
## All of these play where he stands (same table as fletcher/cues.py after STATIONARY).
const GESTURE_MOVES := {
	"slam_stand": "t1_fist_slam", "throw_music": "t4_throw_music", "kick_stand": "t4_sweep_stand",
	"throw_chair": "t5_chair_throw", "storm_over": "t4_charge", "in_face": "t2_point_player",
	"point": "t2_point_player", "beat_time": "t0_clap_off", "grab_cymbal": "t5_cymbal_throw",
	"turn_away": "t1_wave_off", "spin_back": "t2_point_player", "throw_hands_up": "t4_shout",
	"slow_clap": "t0_clap_off",
}
## With `closeups`: the walking versions (he goes to the drummer first, ~4 s at 94 BPM).
const CLOSEUP_GESTURE_MOVES := {"in_face": "t3_loom", "beat_time": "t5_rhythm_slaps", "slow_clap": "t3_clap_in_face"}
const HUB_MOVE := "t0_stare_drummer"
## Used only when moves.json can't be read: the looping moves of terence_v004.
const DEFAULT_LOOPS := ["t0_conduct", "t0_pace", "t0_lean_listen", "t0_watch_other", "t0_stare_drummer",
	"t0_chair_rest", "t2_judging_stare", "t2_hunt_sections", "t3_loom", "t3_crouch", "t3_circle_kit"]
const AUTHORED_BPM := 120.0
const FIST := "t1_fist_cutoff"
const COUNTOFF := "t0_countoff_finger"
const STOP_GRACE_S := 1.0  ## a deferred session stop lowers the band anyway if no fist starts by then

var bpm := 120.0
var band_playing := false
var meta := {}   ## moves.json: {fps, bpm, moves: {id: {frames, loop, ...}}}
var _last_action := ""
## The move we asked for and treat as Terence's: {move, priority, loop, phase, t, ends}.
## phase: "requested" (sent; he may be walking there) or "playing".
var _cur := {}
var _queue := []  # [{move, priority, due_t, seq}]
var _seq := 0
var _cue_finger_t := -INF  # when a fletcher cue last asked for t0_countoff_finger
var _timers := []  # [[at (this device's seconds), Callable]], checked every frame
var _countoff_at := -INF  # when Fletcher's t0-scheduled finger starts (this device's seconds)
var _finger_wait := -1.0  # a finger due at this time waits for him to get home
var _stop_pending := false  # a session stop is waiting for the coming fist to freeze the band
var _frozen := false  # frozen on the fist; lowers at its release unless the band restarts first


func _ready() -> void:
	_load_meta()
	if terence == null and director != null:
		var t = director.get("terence")
		if t is Object and t.has_signal("move_started"):
			terence = t
	if terence:
		terence.move_started.connect(_on_terence_started)
		terence.move_finished.connect(_on_terence_finished)
	if hub:
		hub.message_received.connect(_on_message)
	if voice:
		voice.cue_due.connect(_on_cue)
		voice.finished.connect(_on_voice_finished)


func _load_meta() -> void:
	var text := FileAccess.get_file_as_string(moves_json)
	var parsed = JSON.parse_string(text) if text != "" else null
	if typeof(parsed) == TYPE_DICTIONARY and typeof(parsed.get("moves")) == TYPE_DICTIONARY:
		meta = parsed
	else:
		push_warning("[FletcherLink] cannot read %s; durations and frames fall back to defaults" % moves_json)
		meta = {"fps": 24, "bpm": AUTHORED_BPM, "moves": {}}


## ---- move facts ---------------------------------------------------------

func has_move(id: String) -> bool:
	if terence and terence.has_method("has_move"):
		return terence.has_move(StringName(id))
	return meta["moves"].has(id) and not meta["moves"][id].get("utility", false)


func is_loop(id: String) -> bool:
	if not meta["moves"].has(id):
		return id in DEFAULT_LOOPS
	return bool(meta["moves"][id].get("loop", false))


## Real seconds from the move's start to authored frame `frame` at the current tempo.
func frame_seconds(id: String, frame: float) -> float:
	var rate := bpm / float(meta.get("bpm", AUTHORED_BPM))
	if terence and terence.has_method("tempo_policy") and String(terence.tempo_policy(StringName(id))) == "free":
		rate = 1.0
	return frame / float(meta.get("fps", 24)) / rate


func move_seconds(id: String) -> float:
	return frame_seconds(id, float(meta["moves"].get(id, {}).get("frames", 24)))


## Authored frame of `event` in move `id` (the first one if it is a list), else `fallback`.
func event_frame(id: String, event: String, fallback: float = -1.0) -> float:
	var v = meta["moves"].get(id, {}).get(event)
	if typeof(v) == TYPE_ARRAY:
		v = v[0] if not v.is_empty() else null
	return float(v) if typeof(v) in [TYPE_INT, TYPE_FLOAT] else fallback


## Frame a held pose ends (its "release"), or -1 if the move has no hold. Only moves with a
## "hold_start" have one: for the throws (t5_*, t4_throw_music) "release" is letting go of a prop.
func hold_release(id: String) -> float:
	return event_frame(id, "release") if event_frame(id, "hold_start") >= 0.0 else -1.0


## Beats from the finger move's start to t0: finger 1 ("five") goes up on beat -4, so the
## lead is 4 beats plus the first finger_up frame in beats (12 frames = 1 beat: 5 beats).
func countoff_lead_beats() -> float:
	var fps := float(meta.get("fps", 24))
	var authored := float(meta.get("bpm", AUTHORED_BPM))
	return 4.0 + event_frame(COUNTOFF, "finger_up", fps * 60.0 / authored) / fps * authored / 60.0


## The cue spec's resolution: GESTURE_MOVES, else a Terence move id, else the action's move.
func resolve(gesture: String, action: String) -> String:
	if closeups and CLOSEUP_GESTURE_MOVES.has(gesture):
		return CLOSEUP_GESTURE_MOVES[gesture]
	if GESTURE_MOVES.has(gesture):
		return GESTURE_MOVES[gesture]
	if gesture != "" and has_move(gesture):
		return gesture
	return _action_move(action)


func _action_move(action: String) -> String:
	return ACTION_MOVES.get(action, "")


## What Terence is doing now, as far as the queue is concerned.
func current_move() -> String:
	if not _cur.is_empty():
		return _cur["move"]
	if terence:
		return String(terence.current_move())
	return HUB_MOVE


func queued_moves() -> Array:
	return _queue.map(func(r): return r["move"])


## ---- the queue ----------------------------------------------------------

## Ask for `move` whose anchor was at due_t (this device's seconds; default now).
func request(move: String, priority: int = 1, due_t: float = -1.0) -> void:
	if move == "":
		return
	if due_t < 0.0:
		due_t = _now()
	if not _cur.is_empty() and _cur["move"] == move:
		# already on (e.g. the action's fist, then the stop bark's fist cue): that cue is
		# satisfied; restarting would jerk him back to frame 0
		_cur["priority"] = maxi(_cur["priority"], priority)
		return
	if _queue.any(func(r): return r["move"] == move):
		return
	if _can_start(priority):
		_start(move, priority)
	else:
		if _cur.has("yields"):  # held pose: due when it releases, not stale for the hold
			due_t = maxf(due_t, _cur["yields"])
		_seq += 1
		_queue.append({"move": move, "priority": priority, "due_t": due_t, "seq": _seq})
		_queue.sort_custom(func(a, b): return a["priority"] > b["priority"] \
			or (a["priority"] == b["priority"] and a["seq"] < b["seq"]))


func _can_start(priority: int) -> bool:
	return _cur.is_empty() or _cur["loop"] or _cur["priority"] < priority \
		or _now() >= _cur.get("yields", INF)


func _start(move: String, priority: int) -> void:
	_cur = {"move": move, "priority": priority, "loop": is_loop(move), "phase": "requested",
			"t": _now(), "ends": INF}
	move_requested.emit(move)
	if not _send({"type": "play_move", "id": move}):
		_cur = {}
		_pump()
		return
	if terence == null:
		_on_playing()


func _on_playing() -> void:
	_cur["phase"] = "playing"
	_cur["t"] = _now()
	var move: String = _cur["move"]
	if not _cur["loop"]:
		_cur["ends"] = _cur["t"] + move_seconds(move)
		var release := hold_release(move)
		if release >= 0.0:
			_cur["yields"] = _cur["t"] + frame_seconds(move, release)
			_at(_cur["yields"], _release_if.bind(_cur))
	if move == FIST:
		_cutoff_band()


## The held move reached its release: whatever waited behind it starts now.
func _release_if(cur: Dictionary) -> void:
	if is_same(cur, _cur) and not _queue.is_empty():
		_pump()


func _on_terence_started(id: StringName) -> void:
	if not _cur.is_empty() and _cur["phase"] == "requested" and String(id) == _cur["move"]:
		_on_playing()


func _on_terence_finished(id: StringName) -> void:
	if not _cur.is_empty() and _cur["phase"] == "playing" and String(id) == _cur["move"]:
		# Deferred: Terence emits this from inside his _sync_current, and a play_move from
		# there makes him report the new move as started, finished and started again.
		_finish_if.call_deferred(_cur)


func _finish_if(cur: Dictionary) -> void:
	if is_same(cur, _cur):
		_finish()


func _finish() -> void:
	_cur = {}
	_pump()


## Start the best queued request that is still fresh; else maybe fall back.
func _pump() -> void:
	var now := _now()
	while not _queue.is_empty():
		var r: Dictionary = _queue.pop_front()
		var late: float = now - r["due_t"]
		if r["priority"] < 2 and late > max_late_s:
			move_dropped.emit(r["move"], late)
			continue
		if is_loop(r["move"]) and _queue.any(func(q): return not is_loop(q["move"]) \
				and (q["priority"] >= 2 or now - q["due_t"] <= max_late_s)):
			# a loop (an action change, e.g. conduct at the restart) queued ahead of a fresh
			# one-shot: starting it would strand the one-shot, since a loop never finishes.
			# Play the one-shot; _fallback returns to the action's loop after it.
			continue
		_start(r["move"], r["priority"])
		return
	_fallback()


## After a line, with nothing left to do, settle into the current action's loop.
func _fallback() -> void:
	if not _cur.is_empty() or not _queue.is_empty():
		return
	if voice and (voice.is_speaking() or voice.has_pending_cues()):
		return
	var move := "t0_conduct" if band_playing else _action_move(_last_action)
	if move != "" and is_loop(move) and move != current_move():
		_start(move, 0)


func _on_voice_finished(_uid: String) -> void:
	if _cur.is_empty() or _cur["loop"]:
		_fallback.call_deferred()  # let after_s 0 cues of this utterance arrive first


## Run `f` at `at` (this device's seconds). Not a SceneTree timer: those count frame
## deltas, so one created in a long frame (a hitch, a scene load) fires early.
func _at(at: float, f: Callable) -> void:
	_timers.append([at, f])


func _process(_delta: float) -> void:
	var now := _now()
	if not _timers.is_empty():
		var due := _timers.filter(func(e): return now >= e[0])
		_timers = _timers.filter(func(e): return now < e[0])
		for e in due:
			e[1].call()
	if _finger_wait >= 0.0 and _is_home():
		var at := _finger_wait
		_finger_wait = -1.0
		request(COUNTOFF, 2, at)
	if _cur.is_empty():
		return
	if _cur["phase"] == "requested" and now - _cur["t"] > start_timeout_s:
		push_warning("[FletcherLink] %s never started; giving up on it" % _cur["move"])
		_finish()
	elif _cur["phase"] == "playing" and terence == null and now >= _cur["ends"]:
		_finish()


## ---- messages -----------------------------------------------------------

func _on_message(type: String, data: Dictionary, _env: Dictionary) -> void:
	if type.begins_with("fletcher.") and voice:
		voice.handle_message(type, data)
	match type:
		"fletcher.state":
			if data.has("gesture") and data.has("t0"):
				_on_countoff_cue(data)
			elif not data.has("gesture"):  # gestures wait for their anchor (voice)
				_on_action(str(data.get("action", "")), _prio(data, -1))
		"session.state":
			_on_session(data)
		"session.command":
			if data.get("action") == "stop":
				_stop_band()


static func _prio(d: Dictionary, fallback: int) -> int:
	var p = d.get("priority")
	return clampi(int(p), 0, 2) if typeof(p) in [TYPE_INT, TYPE_FLOAT] else fallback


func _on_action(action: String, priority: int = -1) -> void:
	if action == _last_action:
		return  # Fletcher repeats his action as anger moves; replaying would restart the move
	_last_action = action
	if action == "idle" or action == "":
		_queue = _queue.filter(func(r): return r["priority"] >= 2)
		if _cur.is_empty() or _cur["loop"]:
			_cur = {}
			_send({"type": "stop_move"})  # a one-shot returns to the idle stare by itself
		return
	if priority < 0:
		priority = 2 if action == "fist_cutoff" else 1
	request(_action_move(action), priority)


func _on_cue(c: Dictionary) -> void:
	var move := resolve(str(c.get("gesture", "")), str(c.get("action", "")))
	if move == "":
		return
	_last_action = str(c.get("action", _last_action))
	if move == COUNTOFF:
		_cue_finger_t = _now()
		_finger(float(c.get("due_t", _now())))
		return
	request(move, int(c.get("priority", 1)), float(c.get("due_t", _now())))


## Fletcher's count-off: one clock. t0 (laptop monotonic) is the restart bar's downbeat; the
## finger move starts 5 beats before it (finger k up on beat k-5), the words on beats -4..-1.
func _on_countoff_cue(d: Dictionary) -> void:
	var b := float(d.get("bpm", bpm)) if typeof(d.get("bpm")) in [TYPE_INT, TYPE_FLOAT] else bpm
	var t0 := float(d["t0"])
	if hub:
		t0 = hub.to_local_time(t0)
	_last_action = str(d.get("action", _last_action))
	var at := t0 - countoff_lead_beats() * 60.0 / b
	_countoff_at = at
	_at(at, _scheduled_finger.bind(at))
	if voice and voice.has_method("play_countoff"):
		var clips = d.get("clips", [])
		voice.play_countoff(t0, b, clips if typeof(clips) == TYPE_ARRAY else [])


func _scheduled_finger(at: float) -> void:
	_cue_finger_t = _now()
	_finger(at)


## The count-off finger, due at `at`. It is played where he stands: away from home (after a
## close-up) it waits until he is back instead of making him walk.
func _finger(at: float) -> void:
	if _is_home():
		request(COUNTOFF, 2, at)
	else:
		_finger_wait = at


func _is_home() -> bool:
	if terence == null:
		return true
	if terence.has_method("is_travelling") and terence.is_travelling():
		return false
	var home = terence.get("home")
	return not (home is Transform3D) or terence.global_position.distance_to(home.origin) < 0.05


func _on_session(d: Dictionary) -> void:
	if d.has("bpm") and float(d["bpm"]) >= 40.0:
		bpm = float(d["bpm"])
		_send({"type": "set_tempo", "bpm": bpm})
	match str(d.get("state", "")):
		"count_in":
			# t0 = downbeat after "eight" (laptop clock). Start the finger move 5 beats early.
			var at := _now()
			if d.has("t0") and hub:
				at = hub.to_local_time(float(d["t0"])) - countoff_lead_beats() * 60.0 / bpm
			_at(at, _count_in_finger.bind(at))
		"playing":
			if not band_playing:
				_band("playing")
				_last_action = ""
				_on_action("conduct")
		"stopped", "idle":
			if band_playing:
				_stop_band()


## The count_in finger, unless Fletcher already cued one (his after_s cue on the tirade,
## timed to the restart): pending in the voice, queued, playing, or started just now.
func _count_in_finger(at: float) -> void:
	if current_move() == COUNTOFF or COUNTOFF in queued_moves() \
			or (voice and voice.has_pending_gesture(COUNTOFF)) \
			or _now() - _cue_finger_t < move_seconds(COUNTOFF) + 1.0 \
			or absf(at - _countoff_at) < move_seconds(COUNTOFF) or _finger_wait >= 0.0:
		return
	_finger(at)


## The band stops (session stop / stopped). If the cutoff fist is on its way, the fist does it
## on its cutoff frame instead of now (the stop command goes out with the fist, ~0.6 s early).
func _stop_band() -> void:
	var fist_coming := current_move() == FIST or FIST in queued_moves() \
		or (voice != null and voice.has_pending_gesture(FIST))
	if not fist_coming:
		_band("lowering")
		return
	if _stop_pending:
		return
	_stop_pending = true
	_at(_now() + STOP_GRACE_S, func():
		if _stop_pending and _cur.get("move", "") != FIST:
			_stop_pending = false
			_band("lowering"))


## Freeze the band on the fist's cutoff frame, not on message arrival, and hold it frozen
## until the fist's release. Only a playing band can freeze (Adam's BandMember rejects it from
## rest, e.g. a fist cue mid-tirade).
func _cutoff_band() -> void:
	var cut := frame_seconds(FIST, maxf(0.0, event_frame(FIST, "cutoff", 0.0)))
	var release := hold_release(FIST)
	var hold := frame_seconds(FIST, release) - cut if release >= 0.0 else 0.0
	_at(_now() + cut, func():
		var stop := _stop_pending
		_stop_pending = false
		if band_playing:
			_send({"type": "react", "target": "all", "reaction": "freeze"})
			band_playing = false
			_frozen = true
			_at(_now() + maxf(0.0, hold), func():
				if _frozen:
					_band("lowering"))
		elif stop:
			_band("lowering"))


func _band(state: String) -> void:
	band_playing = state == "playing" or state == "raising"
	_frozen = false
	_stop_pending = false
	_send({"type": "set_band_state", "target": "all", "state": state})


func _send(msg: Dictionary) -> bool:
	if director:
		return bool(director.handle_message(msg))
	return true


static func _now() -> float:
	return Time.get_ticks_usec() / 1e6
