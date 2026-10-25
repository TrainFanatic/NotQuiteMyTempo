extends SceneTree
## Integration test against Adam's REAL Terence. Not run in this folder: tests/run_integration.sh
## copies his quest_app to a scratch dir, adds scripts/fletcher/*.gd and this file, imports it,
## and runs:  Godot --headless --path <copy> --script tests/fletcher_integration.gd -- <barks dir> [sweep]
## Builds his Rehearsal scene, wires FletcherHub/Voice/Link to its director, feeds scripted hub
## messages (fletcher.state cues, fletcher.speech/audio with a tone) and asserts on Terence's own
## move_started timestamps (line timing jitters ~0.1 s: the headless dummy audio driver mixes ~93 ms at a time). With "sweep" it also plays every move once and reports oddities.

const RATE := 24000
var failures := 0
var starts := []    # [[id, t]] from terence.move_started
var finishes := []  # [[id, t]] from terence.move_finished
var dropped := []
var errors := []
var r
var terence
var hub: FletcherHub
var voice: FletcherVoice
var link: FletcherLink


class ErrLog extends Logger:
	var sink: Array
	var mutex := Mutex.new()
	func _log_error(function: String, file: String, line: int, code: String, rationale: String,
			_editor_notify: bool, error_type: int, _bt: Array[ScriptBacktrace]) -> void:
		if error_type == ERROR_TYPE_WARNING:
			return
		mutex.lock()
		sink.append("%s (%s:%d %s)" % [rationale if rationale != "" else code, file.get_file(), line, function])
		mutex.unlock()


func now() -> float:
	return Time.get_ticks_usec() / 1e6


func check(ok: bool, what: String) -> void:
	print(("ok   " if ok else "FAIL ") + what)
	if not ok:
		failures += 1


func _initialize() -> void:
	var args := OS.get_cmdline_user_args()
	var barks_dir: String = args[0] if args.size() > 0 else ""
	var sweep := args.has("sweep")
	var lg := ErrLog.new()
	lg.sink = errors
	OS.add_logger(lg)
	Engine.max_fps = 72  # the Quest's rate; headless would otherwise spin
	await process_frame
	r = load("res://scripts/rehearsal.gd").new()
	r.run_demo = false
	r.run_showcase = false
	r.intro_enabled = false  # Terence on his spot from the start (his entrance walks him in)
	root.add_child(r)
	await process_frame
	terence = r.terence
	check(terence != null and not terence.moves.is_empty(), "Adam's rehearsal builds, Terence has %d moves" % (terence.moves.size() if terence else 0))
	if terence == null:
		quit(1)
		return
	print("build errors: %s" % [errors])
	errors.clear()
	terence.move_started.connect(func(id): starts.append([String(id), now()]))
	terence.move_finished.connect(func(id): finishes.append([String(id), now()]))
	hub = FletcherHub.new()
	hub.url = "ws://127.0.0.1:1"  # we emit message_received ourselves
	root.add_child(hub)
	voice = FletcherVoice.new()
	voice.barks_dir = barks_dir
	terence.add_child(voice)
	link = FletcherLink.new()
	link.hub = hub
	link.voice = voice
	link.director = r.director
	link.move_dropped.connect(func(m, late): dropped.append([m, late]))
	root.add_child(link)
	check(link.terence == terence, "link found Terence through the director and uses his signals")
	await _phase_countoff()
	await _phase_bark()
	await _phase_line_queue()
	await _phase_close_up()
	await _phase_cutoff_sequence()
	if sweep:
		await _sweep()
	print("errors during the scripted phases: %s" % [errors])
	print("FAILURES: %d" % failures if failures else "ALL PASSED")
	quit(1 if failures else 0)


func send(type: String, data: Dictionary) -> void:
	hub.message_received.emit(type, data, {"type": type, "t": now()})


func tone(uid: String, seconds: float, chunk_s := 0.1) -> void:
	var n := int(chunk_s * RATE)
	var b := PackedByteArray()
	b.resize(2 * n)
	for i in n:
		b.encode_s16(2 * i, int(8000.0 * sin(TAU * 220.0 * i / RATE)))
	var b64 := Marshalls.raw_to_base64(b)
	var chunks := int(round(seconds / chunk_s))
	for i in chunks:
		send("fletcher.audio", {"utterance_id": uid, "index": i, "pcm_b64": b64, "final": false})
	send("fletcher.audio", {"utterance_id": uid, "index": chunks, "pcm_b64": "", "final": true})


func wait(seconds: float) -> void:
	await create_timer(seconds).timeout


func wait_until(cond: Callable, timeout_s: float) -> bool:
	var t := now()
	while not cond.call():
		if now() - t > timeout_s:
			return false
		await process_frame
	return true


## First move_started of `id` at or after t (relative), or -1.
func started_at(id: String, since: float) -> float:
	for e in starts:
		if e[0] == id and e[1] >= since:
			return e[1] - since
	return -1.0


func finished_at(id: String, since: float) -> float:
	for e in finishes:
		if e[0] == id and e[1] >= since:
			return e[1] - since
	return -1.0


func order_since(since: float) -> Array:
	return starts.filter(func(e): return e[1] >= since and e[0] not in ["t0_stare_drummer", "walk"]) \
		.map(func(e): return "%s@%.2f" % [e[0], e[1] - since])


func settle() -> void:
	# back home, idle, nothing queued
	await wait_until(func(): return not terence.is_travelling() and String(terence.current_move()) == "t0_stare_drummer" \
		and link.queued_moves().is_empty(), 15.0)
	await wait(0.3)


func near(x: float, want: float, tol := 0.07) -> bool:
	return x >= 0.0 and absf(x - want) <= tol


func _phase_countoff() -> void:
	print("-- count-off at 120 BPM: t0 in 3 s")
	var p := now()
	var t0 := p + 3.0
	send("session.state", {"state": "count_in", "bpm": 120, "t0": t0})
	await wait_until(func(): return now() >= t0, 4.0)
	send("session.state", {"state": "playing", "bpm": 120, "bar": 1})
	await wait(0.6)
	var f := started_at("t0_countoff_finger", p)
	check(near(f, 0.5), "t0_countoff_finger starts 5 beats before t0 (want 0.50 s): %.3f" % f)
	var c := started_at("t0_conduct", p)
	check(c >= 2.95 and c <= 3.2, "t0_conduct starts at t0 once the count-off ends (want ~3.0 s): %.3f" % c)
	send("session.state", {"state": "stopped", "bpm": 120})
	send("fletcher.state", {"mood": "cold", "anger": 0.2, "action": "idle", "look_at": "drummer"})
	await settle()


func _phase_bark() -> void:
	print("-- bark no_1 at 180 BPM: at_s 0.2 fist slam, after_s 0 point (waits for the slam's release)")
	send("session.state", {"state": "stopped", "bpm": 180})
	await wait(0.1)
	var p := now()
	send("fletcher.state", {"action": "glare", "gesture": "slam_stand", "utterance_id": "b1", "at_s": 0.2})
	send("fletcher.state", {"action": "point", "gesture": "t2_point_player", "utterance_id": "b1", "after_s": 0.0})
	send("fletcher.speech", {"utterance_id": "b1", "text": "No.", "clip_id": "no_1"})
	await wait(2.5)
	var slam := started_at("t1_fist_slam", p)
	var point := started_at("t2_point_player", p)
	var rel := link.hold_release("t1_fist_slam") / 24.0 * 120.0 / 180.0  # v004: frame 26 of 38
	var fin := finished_at("t1_fist_slam", p)
	print("   order: %s" % [order_since(p)])
	check(near(slam, 0.2), "fist slam 0.2 s into the bark: %.3f" % slam)
	check(point > 0.88 and near(point - slam, rel, 0.1), "point waits for the slam's release (%.3f s in), then starts (bark ends 0.88 s): %.3f" % [rel, point])
	check(near(fin, point, 0.05), "the slam holds until the point takes over: %.3f" % fin)
	await settle()


func _phase_line_queue() -> void:
	print("-- 2 s line at 120 BPM: chair throw @chunk 0, flick at_s 0.1 (goes stale), fist cutoff p2 at_s 1.0 (preempts), nod after_s 0.3 (waits for the fist's release), then glare loop")
	send("session.state", {"state": "stopped", "bpm": 120})
	await wait(0.1)
	var ends := {}
	var on_end := func(u): ends[u] = now()
	voice.finished.connect(on_end)
	var p := now()
	send("fletcher.state", {"action": "walk_toward", "gesture": "throw_chair", "utterance_id": "u2", "at_index": 0})
	send("fletcher.state", {"action": "glare", "gesture": "t1_flick", "utterance_id": "u2", "at_s": 0.1})
	send("fletcher.state", {"action": "fist_cutoff", "gesture": "t1_fist_cutoff", "utterance_id": "u2", "at_s": 1.0, "priority": 2})
	send("fletcher.state", {"action": "glare", "gesture": "ap_nod", "utterance_id": "u2", "after_s": 0.3})
	send("fletcher.speech", {"utterance_id": "u2", "text": "...", "stream": true, "sample_rate": RATE})
	tone("u2", 2.0)
	await wait(5.5)
	voice.finished.disconnect(on_end)
	print("   order: %s  dropped: %s" % [order_since(p), dropped])
	var chair := started_at("t5_chair_throw", p)
	var fist := started_at("t1_fist_cutoff", p)
	var nod := started_at("ap_nod", p)
	var stare := started_at("t2_judging_stare", p)
	var e: float = ends.get("u2", -1.0) - p
	check(chair >= 0.0 and chair < 0.25, "chair throw on chunk 0 (prebuffer 0.15 s): %.3f" % chair)
	check(near(fist - chair, 1.0, 0.12), "priority-2 fist cutoff preempts the chair 1.0 s in: %.3f" % (fist - chair))
	check(started_at("t1_flick", p) < 0.0 and dropped.any(func(d): return d[0] == "t1_flick"), "the flick went stale behind them and was dropped")
	var rel := link.hold_release("t1_fist_cutoff") / 24.0  # frame 50 = 2.08 s at 120 BPM
	check(nod > e + 0.3 and near(nod - fist, rel, 0.1), "nod due 0.3 s after the line (end %.3f) waits for the fist's release (%.2f s in): %.3f" % [e, rel, nod - fist])
	check(stare > nod + 0.9, "then the glare loop (fallback) after the nod: %.3f" % stare)
	send("fletcher.state", {"action": "idle"})
	await settle()


func _phase_close_up() -> void:
	print("-- close-up (closeups on): slow_clap -> t3_clap_in_face @chunk 0 walks first; nod after_s 0 must not start mid-walk/mid-clap")
	link.closeups = true
	var p := now()
	send("fletcher.state", {"action": "glare", "gesture": "slow_clap", "utterance_id": "u3", "at_index": 0})
	send("fletcher.state", {"action": "nod", "gesture": "ap_nod", "utterance_id": "u3", "after_s": 0.0})
	send("fletcher.speech", {"utterance_id": "u3", "text": "...", "stream": true, "sample_rate": RATE})
	tone("u3", 1.0)
	await wait(0.4)
	check(terence.is_travelling() and String(terence.queued_move()) == "t3_clap_in_face", "he walks to the player first, the clap waiting on arrival")
	await wait_until(func(): return started_at("t3_clap_in_face", p) >= 0.0, 10.0)
	var clap := started_at("t3_clap_in_face", p)
	await wait(4.0)
	var clap_end := finished_at("t3_clap_in_face", p)
	var nod := started_at("ap_nod", p)
	print("   order: %s  dropped: %s" % [order_since(p), dropped])
	check(clap > 0.5, "the clap starts on arrival, %.2f s after the cue" % clap)
	check(near(clap_end - clap, 2.5, 0.1), "clap lasts 60 frames at 120 BPM (2.5 s): %.3f" % (clap_end - clap))
	check(nod < 0.0 or nod >= clap_end - 0.02, "nod not started during the walk or the clap (%.3f; dropped if stale)" % nod)
	await settle()
	link.closeups = false


## Fletcher's real cutoff sequence (main.py): band playing; fist action + the Stop bark with a
## t1_fist_cutoff at_s 0 cue (p2); plain lean_in p0; a line with an at_index cue; after_s 0
## stare (p1) and after_s 0.3 count-off finger (p2); then the bridge's count_in for the restart.
func _phase_cutoff_sequence() -> void:
	print("-- cutoff sequence at 120 BPM")
	send("session.state", {"state": "playing", "bpm": 120, "bar": 1})
	await wait(1.0)
	var ends := {}
	var on_end := func(u): ends[u] = now()
	voice.finished.connect(on_end)
	var p := now()
	send("fletcher.state", {"mood": "furious", "anger": 1.0, "action": "fist_cutoff", "look_at": "drummer"})
	send("fletcher.state", {"action": "fist_cutoff", "gesture": "t1_fist_cutoff", "utterance_id": "stop", "at_s": 0.0, "priority": 2})
	send("session.command", {"action": "stop"})
	send("fletcher.speech", {"utterance_id": "stop", "text": "Stop!", "clip_id": "stop_1"})
	send("fletcher.state", {"mood": "furious", "anger": 1.0, "action": "lean_in", "priority": 0})
	var cut := link.event_frame("t1_fist_cutoff", "cutoff") / 24.0
	var rel := link.hold_release("t1_fist_cutoff") / 24.0
	# sample the band's states (all members) around the fist's cutoff and release frames
	var band := {}
	var marks := {"before": cut - 0.1, "frozen": cut + 0.1, "held": rel - 0.1, "after": rel + 0.2}
	for key in marks:
		create_timer(marks[key]).timeout.connect(func(): band[key] = \
			r.director.members.values().map(func(m): return String(m.current_state())))
	await wait_until(func(): return ends.has("stop"), 3.0)
	send("fletcher.state", {"action": "point", "gesture": "point", "utterance_id": "t1", "at_index": 0})
	send("fletcher.state", {"action": "glare", "gesture": "t2_judging_stare", "utterance_id": "t1", "after_s": 0.0})
	send("fletcher.state", {"action": "conduct", "gesture": "t0_countoff_finger", "utterance_id": "t1", "after_s": 0.3, "priority": 2})
	send("fletcher.speech", {"utterance_id": "t1", "text": "...", "stream": true, "sample_rate": RATE})
	tone("t1", 2.0)
	await wait_until(func(): return ends.has("t1"), 5.0)
	# the bridge restarts: count_in with t0 = when the cued finger's "five" lands
	send("session.state", {"state": "count_in", "bpm": 120, "t0": ends["t1"] + 0.3 + 2.5})
	await wait(4.0)
	voice.finished.disconnect(on_end)
	print("   order: %s  dropped: %s" % [order_since(p), dropped])
	var fists := starts.filter(func(e): return e[0] == "t1_fist_cutoff" and e[1] >= p)
	var fingers := starts.filter(func(e): return e[0] == "t0_countoff_finger" and e[1] >= p)
	var all_in := func(key, ok): return band.has(key) and not band[key].is_empty() and band[key].all(func(s): return s in ok)
	check(all_in.call("before", ["play_loop"]), "the stop command doesn't lower the band before the fist lands: %s" % [band.get("before")])
	check(all_in.call("frozen", ["freeze"]), "band freezes on the fist's cutoff frame (%.2f s): %s" % [cut, band.get("frozen")])
	check(all_in.call("held", ["freeze"]), "and stays frozen through the hold: %s" % [band.get("held")])
	check(all_in.call("after", ["lower", "rest"]), "then lowers at the release (%.2f s): %s" % [rel, band.get("after")])
	check(fists.size() == 1 and fists[0][1] - p < 0.1, "one fist, on the Stop bark (the cue doesn't restart the action's fist): %d" % fists.size())
	var opener := started_at("t2_point_player", p)
	check(fists.size() == 1 and near(opener - (fists[0][1] - p), rel, 0.1), "the line's opener (point @chunk 0) plays at the fist's release (%.2f s): %.3f" % [rel, opener])
	check(fingers.size() == 1 and near(fingers[0][1] - ends["t1"], 0.3), "one count-off finger, 0.3 s after the line (count_in skipped its own): %s" % [fingers.map(func(e): return e[1] - ends["t1"])])
	check(errors.is_empty(), "no errors from Adam's scene: %s" % [errors])


## Every move once at 150 BPM through the link: start delay (walk), duration vs moves.json, errors.
func _sweep() -> void:
	print("-- sweep: every move at 150 BPM")
	send("session.state", {"state": "stopped", "bpm": 150})
	await wait(0.2)
	var odd := []
	for id in terence.moves:
		var m := String(id)
		if m == "walk":
			continue
		errors.clear()
		var p := now()
		link.request(m, 2)
		var ok := await wait_until(func(): return started_at(m, p) >= 0.0, 12.0)
		var s := started_at(m, p)
		var line := "%-20s start %+.2f" % [m, s]
		if not ok:
			odd.append("%s never started" % m)
		elif link.is_loop(m):
			await wait(0.8)
			line += "  loop"
		else:
			var want := link.move_seconds(m)
			await wait_until(func(): return finished_at(m, p) >= 0.0, want + 3.0)
			var dur := finished_at(m, p) - s
			line += "  %.2f s (json %.2f)" % [dur, want]
			if absf(dur - want) > 0.1:
				odd.append("%s lasts %.2f s, json says %.2f" % [m, dur, want])
		if not errors.is_empty():
			odd.append("%s errors: %s" % [m, errors.duplicate()])
		print("   " + line + ("  ERR %d" % errors.size() if errors.size() else ""))
		link._on_action("idle")
		link._last_action = ""
		await settle()
	print("sweep oddities: %s" % [odd if odd.size() else "none"])
