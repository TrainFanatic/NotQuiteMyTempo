extends SceneTree
## Godot --headless --path fletcher/godot --script tests/run_tests.gd   (exits 1 on failure)

const RATE := 24000
var failures := 0


class FakeTerence extends Node3D:
	signal move_started(id: StringName)
	signal move_finished(id: StringName)
	var home := Transform3D.IDENTITY
	var travelling := false
	func is_travelling() -> bool:
		return travelling
	func current_move() -> StringName:
		return &"t0_stare_drummer"
	func has_move(id: StringName) -> bool:
		return true


class FakeDirector:
	var msgs := []
	var times := []  # Time.get_ticks_usec() / 1e6 of each message
	func handle_message(m: Dictionary) -> bool:
		msgs.append(m)
		times.append(Time.get_ticks_usec() / 1e6)
		return true


func _initialize() -> void:
	await process_frame
	await _test_cue_waits_for_its_chunk()
	await _test_other_utterance_and_missing_chunk()
	_test_bark_loads_from_disk()
	await _test_link_routes_and_dedupes()
	await _test_queue_priority_and_waiting()
	await _test_queue_drops_stale()
	await _test_queued_loop_does_not_strand_a_one_shot()
	_test_same_move_and_raw_ids()
	await _test_count_in_defers_to_a_cued_finger()
	await _test_after_s_fires_at_the_end()
	await _test_bark_at_s_and_after_s()
	await _test_bark_negative_at_s_leads()
	await _test_cutoff_fist_gets_the_longer_lead()
	await _test_ducks_sfx_while_speaking()
	await _test_countoff_on_t0()
	await _test_conduct_fallback_while_playing()
	await _test_finger_waits_for_home()
	await _test_stop_waits_for_the_fist()
	await _test_hold_defers_instead_of_dropping()
	print("FAILURES: %d" % failures if failures else "ALL PASSED")
	quit(1 if failures else 0)


func check(ok: bool, what: String) -> void:
	print(("ok   " if ok else "FAIL ") + what)
	if not ok:
		failures += 1


func tone_chunk(uid: String, index: int, seconds: float) -> Dictionary:
	var n := int(seconds * RATE)
	var b := PackedByteArray()
	b.resize(2 * n)
	for i in n:
		b.encode_s16(2 * i, int(8000.0 * sin(TAU * 220.0 * i / RATE)))
	return {"utterance_id": uid, "index": index, "pcm_b64": Marshalls.raw_to_base64(b), "final": false}


func new_voice() -> FletcherVoice:
	var v := FletcherVoice.new()
	root.add_child(v)
	return v


func _test_cue_waits_for_its_chunk() -> void:
	var v := new_voice()
	var t := {}
	v.speaking.connect(func(_u): t["speak"] = Time.get_ticks_msec())
	v.gesture_cue.connect(func(g, a, _l, _u): t[g] = Time.get_ticks_msec(); t[g + "_action"] = a)
	v.finished.connect(func(_u): t["done"] = Time.get_ticks_msec())
	# The cue arrives BEFORE its audio, as it can from Fletcher.
	v.handle_message("fletcher.state", {"action": "point", "gesture": "slam_stand", "utterance_id": "u1", "at_index": 4})
	v.handle_message("fletcher.speech", {"utterance_id": "u1", "text": "Not my tempo.", "stream": true, "sample_rate": RATE})
	for i in 10:
		v.handle_message("fletcher.audio", tone_chunk("u1", i, 0.1))
	v.handle_message("fletcher.audio", {"utterance_id": "u1", "index": 10, "pcm_b64": "", "final": true})
	var start := Time.get_ticks_msec()
	while not t.has("done") and Time.get_ticks_msec() - start < 3000:
		await process_frame
	check(t.has("slam_stand"), "gesture fired")
	if t.has("slam_stand") and t.has("speak"):
		var dt: int = t["slam_stand"] - t["speak"]
		check(dt >= 330 and dt <= 520, "gesture fires when chunk 4 plays (0.4 s in): %d ms" % dt)
		check(t["slam_stand_action"] == "point", "gesture keeps its contract action")
	check(t.has("done") and t["done"] - t["speak"] >= 900, "line plays out (~1 s) then finishes")
	v.queue_free()


func _test_other_utterance_and_missing_chunk() -> void:
	var v := new_voice()
	var fired := []
	v.gesture_cue.connect(func(g, _a, _l, _u): fired.append(g))
	v.handle_message("fletcher.state", {"action": "point", "gesture": "stale", "utterance_id": "old", "at_index": 0})
	v.handle_message("fletcher.state", {"action": "glare", "gesture": "late", "utterance_id": "u2", "at_index": 9})
	v.handle_message("fletcher.speech", {"utterance_id": "u2", "stream": true})
	v.handle_message("fletcher.audio", tone_chunk("old", 0, 0.2))  # wrong utterance: ignored
	v.handle_message("fletcher.audio", tone_chunk("u2", 0, 0.3))
	v.handle_message("fletcher.audio", {"utterance_id": "u2", "index": 1, "pcm_b64": "", "final": true})
	for i in 40:
		await process_frame
	check(fired.is_empty(), "no cue for another utterance or for a chunk that never came: %s" % [fired])
	# A new line interrupts: the old audio must not leak into it.
	v.handle_message("fletcher.speech", {"utterance_id": "a", "stream": true})
	v.handle_message("fletcher.audio", tone_chunk("a", 0, 1.0))
	for i in 20:
		await process_frame
	v.handle_message("fletcher.speech", {"utterance_id": "b", "stream": true})
	v.handle_message("fletcher.audio", tone_chunk("b", 0, 0.2))
	for i in 20:
		await process_frame
	check(v.played_frames() <= 0.2 * RATE, "an interrupting line drops the old one's audio")
	v.queue_free()


func _test_bark_loads_from_disk() -> void:
	var v := new_voice()
	v.barks_dir = ProjectSettings.globalize_path("res://").path_join("../barks")
	var said := []
	v.speaking.connect(func(u): said.append(u))
	v.handle_message("fletcher.speech", {"utterance_id": "b1", "text": "Stop!", "clip_id": "stop_1"})
	check(said == ["b1"], "bark stop_1 loads from fletcher/barks and plays")
	v.queue_free()


const MOVES := "res://tests/fixtures/terence_v004_moves.json"  # copy of Adam's assets/terence one


func now() -> float:
	return Time.get_ticks_usec() / 1e6


func new_link(v: FletcherVoice, d: FakeDirector, hub: FletcherHub = null) -> FletcherLink:
	var link := FletcherLink.new()
	link.moves_json = MOVES
	link.hub = hub
	link.voice = v
	link.director = d
	if hub:
		root.add_child(hub)
	root.add_child(link)
	return link


## [[move, seconds since t0]] for every play_move the link issues.
func record(link: FletcherLink, t0: float) -> Array:
	var log := []
	link.move_requested.connect(func(m): log.append([m, now() - t0]))
	return log


func wait_until(cond: Callable, timeout_s: float) -> void:
	var start := now()
	while not cond.call() and now() - start < timeout_s:
		await process_frame


func _test_link_routes_and_dedupes() -> void:
	var hub := FletcherHub.new()
	hub.url = "ws://127.0.0.1:1"  # nothing there; it just retries
	var v := new_voice()
	var d := FakeDirector.new()
	var link := new_link(v, d, hub)
	var send := func(type, data): hub.message_received.emit(type, data, {})
	send.call("session.state", {"state": "playing", "bpm": 240, "bar": 1})
	for i in 3:
		send.call("fletcher.state", {"mood": "irritated", "anger": 0.5 + 0.1 * i, "action": "glare"})
	send.call("fletcher.state", {"mood": "furious", "anger": 1.0, "action": "fist_cutoff"})
	var plays := d.msgs.filter(func(m): return m["type"] == "play_move").map(func(m): return m["id"])
	check(plays == ["t0_conduct", "t2_judging_stare", "t1_fist_cutoff"], "actions map to moves, repeats dropped: %s" % [plays])
	check(d.msgs.any(func(m): return m == {"type": "set_tempo", "bpm": 240.0}), "session bpm sets the tempo")
	check(not d.msgs.any(func(m): return m.get("reaction") == "freeze"), "band not frozen before the fist frame")
	var lowered := func(): return d.msgs.any(func(m): return m.get("state") == "lowering")
	var fist_t := now()
	var cut := link.event_frame("t1_fist_cutoff", "cutoff") / 24.0 * 0.5  # v004: frame 14 = 0.29 s at 240 BPM
	await create_timer(cut + 0.08).timeout
	check(d.msgs.any(func(m): return m.get("reaction") == "freeze"), "band freezes on the fist frame (%.3f s)" % cut)
	check(not lowered.call(), "and stays frozen through the hold")
	var rel := link.hold_release("t1_fist_cutoff") / 24.0 * 0.5  # frame 50 = 1.04 s
	await wait_until(lowered, 2.0)
	check(absf(now() - fist_t - rel) < 0.08, "band lowers at the fist's release (%.3f s): %.3f" % [rel, now() - fist_t])
	send.call("fletcher.state", {"action": "lean_in", "gesture": "throw_chair", "utterance_id": "u3", "at_index": 0})
	send.call("fletcher.speech", {"utterance_id": "u3", "stream": true})
	send.call("fletcher.audio", tone_chunk("u3", 0, 0.2))
	send.call("fletcher.audio", {"utterance_id": "u3", "index": 1, "pcm_b64": "", "final": true})
	# the cutoff (priority 2) is still holding: the chair throw waits for its release
	await wait_until(func(): return d.msgs.any(func(m): return m.get("id") == "t5_chair_throw"), 1.5)
	check(d.msgs.any(func(m): return m.get("id") == "t5_chair_throw"), "gesture cue reaches the director as its move")
	for n in [link, v, hub]:
		n.queue_free()


func _test_queue_priority_and_waiting() -> void:
	var d := FakeDirector.new()
	var link := new_link(null, d)
	link.bpm = 240.0  # one-shots at half their authored length
	var t0 := now()
	var log := record(link, t0)
	link.request("t2_judging_stare", 1)
	link.request("ap_nod", 0)  # a loop is interruptible even by an ambient cue
	link.request("t2_point_player", 1)  # the nod is priority 0: preempted
	link.request("t1_flick", 1)  # waits for the point (0.75 s at 240)
	link.request("t1_fist_cutoff", 2)  # must play: preempts the point at once
	var ids := log.map(func(e): return e[0])
	check(ids == ["t2_judging_stare", "ap_nod", "t2_point_player", "t1_fist_cutoff"],
		"loops and lower priorities give way, equal priority waits: %s" % [ids])
	check(link.queued_moves() == ["t1_flick"], "flick queued behind the cutoff: %s" % [link.queued_moves()])
	await wait_until(func(): return log.size() >= 5, 2.0)
	var rel := 50.0 / 24.0 * 120.0 / 240.0  # v004 t1_fist_cutoff release frame 50 (of 72)
	check(log.size() >= 5 and log[4][0] == "t1_flick" and absf(log[4][1] - log[3][1] - rel) < 0.08,
		"flick starts at the cutoff's release, not its end (%.3f s at 240 BPM): %s" % [rel, log.slice(3)])
	link.queue_free()


func _test_queue_drops_stale() -> void:
	var d := FakeDirector.new()
	var link := new_link(null, d)
	var dropped := []
	link.move_dropped.connect(func(m, late): dropped.append([m, late]))
	var log := record(link, now())
	link.request("t5_chair_throw", 2)  # 2.5 s at 120 BPM
	link.request("t1_flick", 1)        # anchor now: 2.5 s late by the time the chair lands
	link.request("t1_clap_stop", 2)    # equal priority waits too; must play, so never dropped, and goes first
	await wait_until(func(): return log.size() >= 2 and not dropped.is_empty(), 5.0)
	var ids := log.map(func(e): return e[0])
	check(ids == ["t5_chair_throw", "t1_clap_stop"], "priority 2 waits for the one-shot, then plays: %s" % [log])
	check(dropped.size() == 1 and dropped[0][0] == "t1_flick" and dropped[0][1] > link.max_late_s,
		"a priority-1 cue more than max_late_s late is dropped: %s" % [dropped])
	link.queue_free()


## hub/full_e2e.py: at the restart the band's "playing" queues conduct behind the finger,
## then a bark's point cue queues behind it. The finger ends: the point plays (conduct is a
## loop and would never finish to let it through), then he falls back to conducting.
func _test_queued_loop_does_not_strand_a_one_shot() -> void:
	var d := FakeDirector.new()
	var link := new_link(null, d)
	link.bpm = 240.0
	link.band_playing = true
	var dropped := []
	link.move_dropped.connect(func(m, late): dropped.append([m, late]))
	var log := record(link, now())
	link.request("t1_flick", 2)          # stands in for the count-off finger
	link.request("t0_conduct", 1)        # the restart's action change: a loop
	link.request("t2_point_player", 1)   # the bark's cue
	await wait_until(func(): return log.size() >= 3, 3.0)
	var ids := log.map(func(e): return e[0])
	check(ids.slice(0, 3) == ["t1_flick", "t2_point_player", "t0_conduct"] and dropped.is_empty(),
		"a queued loop gives way to the one-shot behind it, then comes back: %s dropped %s" % [ids, dropped])
	link.queue_free()


func _test_same_move_and_raw_ids() -> void:
	var d := FakeDirector.new()
	var link := new_link(null, d)
	var log := record(link, now())
	link._on_action("fist_cutoff")
	link._on_cue({"gesture": "t1_fist_cutoff", "action": "fist_cutoff", "priority": 2, "due_t": now()})
	check(log.map(func(e): return e[0]) == ["t1_fist_cutoff"], "a cue for the move already playing doesn't restart it: %s" % [log])
	check(link.resolve("t2_score_point", "glare") == "t2_score_point" and link.resolve("slam_stand", "glare") == "t1_fist_slam" \
		and link.resolve("no_such_move", "glare") == "t2_judging_stare" and link.resolve("walk", "nod") == "ap_nod",
		"gesture resolves: GESTURE_MOVES, raw move id, else the action (walk is a utility, not a move)")
	link.queue_free()


func _test_count_in_defers_to_a_cued_finger() -> void:
	var d := FakeDirector.new()
	var hub := FletcherHub.new()
	hub.url = "ws://127.0.0.1:1"
	var link := new_link(null, d, hub)
	var log := record(link, now())
	var send := func(type, data): hub.message_received.emit(type, data, {})
	# no cue: the count_in path counts off on its own, 5 beats before t0
	send.call("session.state", {"state": "count_in", "bpm": 240, "t0": now() + 1.25 + 0.2})
	await wait_until(func(): return not log.is_empty(), 1.0)
	check(log.size() == 1 and log[0][0] == "t0_countoff_finger" and absf(log[0][1] - 0.2) < 0.05,
		"count_in alone starts the finger 5 beats early: %s" % [log])
	await create_timer(1.3).timeout
	# Fletcher's own after_s finger cue came first: count_in must not start a second one
	link._on_cue({"gesture": "t0_countoff_finger", "action": "conduct", "priority": 2, "due_t": now()})
	send.call("session.state", {"state": "count_in", "bpm": 240, "t0": now() + 1.25 + 0.1})
	await create_timer(1.6).timeout
	var fingers := log.filter(func(e): return e[0] == "t0_countoff_finger")
	check(fingers.size() == 2, "count_in skips its finger when Fletcher cued one: %s" % [log])
	link.queue_free()
	hub.queue_free()


func _test_after_s_fires_at_the_end() -> void:
	var v := new_voice()
	var d := FakeDirector.new()
	var link := new_link(v, d)
	link.bpm = 240.0
	var t := {}
	v.finished.connect(func(u): t["end_" + u] = now())
	var log := record(link, 0.0)
	var cue := func(data): link._on_message("fletcher.state", data, {})
	var send := func(type, data): link._on_message(type, data, {})
	cue.call({"action": "glare", "gesture": "t1_wave_off", "utterance_id": "u5", "after_s": 0.0})
	cue.call({"action": "glare", "gesture": "ap_nod", "utterance_id": "u5", "after_s": 0.2})
	cue.call({"action": "glare", "gesture": "t1_flick", "utterance_id": "u5", "at_s": 0.1})  # 0.375 s, over before the end
	send.call("fletcher.speech", {"utterance_id": "u5", "stream": true})
	for i in 6:
		send.call("fletcher.audio", tone_chunk("u5", i, 0.1))
	send.call("fletcher.audio", {"utterance_id": "u5", "index": 6, "pcm_b64": "", "final": true})
	await wait_until(func(): return log.size() >= 4, 3.0)
	var ids := log.map(func(e): return e[0])
	check(ids.slice(0, 3) == ["t1_flick", "t1_wave_off", "ap_nod"], "at_s mid-line, then the after_s cues in order: %s" % [ids])
	if t.has("end_u5") and log.size() >= 3:
		check(absf(log[1][1] - t["end_u5"]) < 0.08, "after_s 0 fires as the line ends: %.3f s after" % (log[1][1] - t["end_u5"]))
		# the nod is due 0.2 s after the end but the wave-off lasts 0.625 s at 240 BPM: it waits
		check(absf(log[2][1] - log[1][1] - 30.0 / 24.0 * 0.5) < 0.08, "after_s 0.2 waits for the wave-off: %.3f s after it" % (log[2][1] - log[1][1]))
	check(ids.size() >= 4 and ids[3] == "t2_judging_stare", "then he settles into the action's loop (glare): %s" % [ids])
	link.queue_free()
	v.queue_free()


func _test_bark_at_s_and_after_s() -> void:
	var v := new_voice()
	v.barks_dir = ProjectSettings.globalize_path("res://").path_join("../barks")
	var d := FakeDirector.new()
	var link := new_link(v, d)
	var t := {}
	v.speaking.connect(func(u): t["speak"] = now())
	v.finished.connect(func(u): t["end"] = now())
	var log := record(link, 0.0)
	var send := func(type, data): link._on_message(type, data, {})
	send.call("fletcher.state", {"action": "point", "gesture": "point", "utterance_id": "b2", "at_s": 0.3})
	send.call("fletcher.state", {"action": "glare", "gesture": "ap_nod", "utterance_id": "b2", "after_s": 0.0, "priority": 2})
	send.call("fletcher.speech", {"utterance_id": "b2", "text": "Stop!", "clip_id": "stop_1"})
	var clip_len: float = v._bark("stop_1").get_length()
	await wait_until(func(): return log.size() >= 2, clip_len + 2.0)
	check(log.size() >= 1 and log[0][0] == "t2_point_player" and t.has("speak") and absf(log[0][1] - t["speak"] - 0.3) < 0.08,
		"bark at_s 0.3 starts the point 0.3 s into the bark: %s" % ["%.3f" % (log[0][1] - t["speak"]) if log.size() and t.has("speak") else log])
	check(t.has("end") and absf(t["end"] - t["speak"] - clip_len) < 0.12, "bark finishes after its clip (%.2f s)" % clip_len)
	check(log.size() >= 2 and log[1][0] == "ap_nod" and absf(log[1][1] - t.get("end", 0.0)) < 0.08,
		"bark after_s 0 (priority 2) preempts the point at the bark's end: %s" % [log])
	link.queue_free()
	v.queue_free()


## A cue with a negative at_s starts its move now and holds the bark back by -at_s (capped).
func _test_bark_negative_at_s_leads() -> void:
	for want in [[-0.2, 0.2], [-0.6, 0.5]]:  # a priority-2 cutoff fist: capped at cutoff_lead_max_s
		var v := new_voice()
		v.barks_dir = ProjectSettings.globalize_path("res://").path_join("../barks")
		var d := FakeDirector.new()
		var link := new_link(v, d)
		var t := {}
		v.speaking.connect(func(u): t["speak"] = now())
		var t0 := now()
		var log := record(link, t0)
		var send := func(type, data): link._on_message(type, data, {})
		send.call("fletcher.state", {"action": "fist_cutoff", "gesture": "t1_fist_cutoff", "utterance_id": "b9", "at_s": want[0], "priority": 2})
		send.call("fletcher.speech", {"utterance_id": "b9", "text": "Stop!", "clip_id": "stop_1"})
		await wait_until(func(): return t.has("speak") and log.size() >= 1, 2.0)
		var fist: float = log[0][1] if log.size() else -1.0
		var heard: float = t.get("speak", 0.0) - t0
		check(log.size() >= 1 and log[0][0] == "t1_fist_cutoff" and fist < 0.05 and absf(heard - want[1]) < 0.04,
			"at_s %.1f: fist starts at once (%.3f), the bark is held %.1f s (%.3f)" % [want[0], fist, want[1], heard])
		check(v.is_speaking(), "a held bark counts as speaking")
		link.queue_free()
		v.queue_free()


## Fletcher's count-off cue carries t0 (laptop clock): finger at t0 - 5 beats, word onsets on
## beats -4..-1, and the bridge's count_in (no t0) does not add a second finger.
func _test_countoff_on_t0() -> void:
	var v := new_voice()
	v.countoff_dir = ProjectSettings.globalize_path("res://").path_join("../countoff")
	var d := FakeDirector.new()
	var hub := FletcherHub.new()
	hub.url = "ws://127.0.0.1:1"
	var link := new_link(v, d, hub)
	var log := record(link, 0.0)
	var words := []
	v.countoff_word.connect(func(c): words.append([c, now()]))
	var onsets = JSON.parse_string(FileAccess.get_file_as_string(v.countoff_dir.path_join("manifest.json")))
	var send := func(type, data): hub.message_received.emit(type, data, {})
	var t0 := now() + 1.25 + 0.3  # 240 BPM: beat 0.25 s, finger 5 beats = 1.25 s before t0
	var clips := ["co_five_sharp", "co_six_sharp", "co_seven_sharp", "co_eight_shout"]
	send.call("fletcher.state", {"action": "conduct", "gesture": "t0_countoff_finger", "utterance_id": "u7",
		"priority": 2, "t0": t0, "bpm": 240, "clips": clips})
	await wait_until(func(): return words.size() >= 2, 2.0)
	send.call("session.state", {"state": "count_in", "bpm": 240})  # the bridge, at the restart ("five")
	await wait_until(func(): return words.size() >= 4, 2.0)
	await create_timer(0.3).timeout
	var fingers := log.filter(func(e): return e[0] == "t0_countoff_finger")
	check(fingers.size() == 1 and absf(fingers[0][1] - (t0 - 1.25)) < 0.03,
		"finger starts 5 beats before t0: %s" % ["%.3f" % (fingers[0][1] - (t0 - 1.25)) if fingers.size() else "none"])
	var offs := []
	for k in words.size():
		offs.append(words[k][1] + float(onsets[words[k][0]]["onset_s"]) - (t0 - (4 - k) * 0.25))
	check(words.map(func(w): return w[0]) == clips and offs.all(func(o): return absf(o) < 0.03),
		"word onsets on beats -4..-1 before t0: %s" % [offs.map(func(o): return "%.3f" % o)])
	link.queue_free()
	hub.queue_free()
	v.queue_free()


## While the band plays, a finished one-shot goes back to conducting, not to the action's stare.
func _test_conduct_fallback_while_playing() -> void:
	var d := FakeDirector.new()
	var hub := FletcherHub.new()
	hub.url = "ws://127.0.0.1:1"
	var link := new_link(null, d, hub)
	var log := record(link, now())
	hub.message_received.emit("session.state", {"state": "playing", "bpm": 240, "bar": 1}, {})
	link._on_cue({"gesture": "t2_point_player", "action": "point", "priority": 1, "due_t": now()})
	await wait_until(func(): return log.size() >= 3, 2.0)
	check(log.map(func(e): return e[0]) == ["t0_conduct", "t2_point_player", "t0_conduct"],
		"after the point he conducts again: %s" % [log])
	link.queue_free()
	hub.queue_free()


## The count-off finger never makes him walk: away from home it waits until he is back.
func _test_finger_waits_for_home() -> void:
	var d := FakeDirector.new()
	var ter := FakeTerence.new()
	root.add_child(ter)
	ter.global_position = Vector3(2, 0, 2)
	ter.travelling = true
	var link := FletcherLink.new()
	link.moves_json = MOVES
	link.director = d
	link.terence = ter
	root.add_child(link)
	var log := record(link, now())
	link._finger(now())
	await create_timer(0.2).timeout
	check(log.is_empty(), "no finger while he walks home: %s" % [log])
	ter.travelling = false
	ter.global_position = Vector3.ZERO
	await wait_until(func(): return not log.is_empty(), 1.0)
	check(log.size() == 1 and log[0][0] == "t0_countoff_finger", "the finger once he is home: %s" % [log])
	link.queue_free()
	ter.queue_free()


## main.py sends the fist (anchored to the Stop bark), then session.command stop, then the bark:
## the band keeps playing until the fist's cutoff frame, freezes, and lowers at its release.
func _test_stop_waits_for_the_fist() -> void:
	var v := new_voice()
	v.barks_dir = ProjectSettings.globalize_path("res://").path_join("../barks")
	var d := FakeDirector.new()
	var hub := FletcherHub.new()
	hub.url = "ws://127.0.0.1:1"
	var link := new_link(v, d, hub)
	var send := func(type, data): hub.message_received.emit(type, data, {})
	send.call("session.state", {"state": "playing", "bpm": 120, "bar": 1})
	d.msgs.clear()
	d.times.clear()
	var t0 := now()
	var log := record(link, t0)
	send.call("fletcher.state", {"action": "fist_cutoff", "gesture": "t1_fist_cutoff", "utterance_id": "s1", "at_s": -0.25, "priority": 2})
	send.call("session.command", {"action": "stop"})
	send.call("session.state", {"state": "stopped", "bpm": 120})
	send.call("fletcher.speech", {"utterance_id": "s1", "text": "Stop!", "clip_id": "stop_1"})
	var band := func():
		var out := []
		for i in d.msgs.size():
			var m: Dictionary = d.msgs[i]
			if m.has("reaction") or m.get("type") == "set_band_state":
				out.append([m.get("reaction", m.get("state")), snappedf(d.times[i] - t0, 0.01)])
		return out
	check(band.call().is_empty(), "the stop command doesn't lower the band before the fist lands: %s" % [band.call()])
	var cut := 14.0 / 24.0
	var rel := 50.0 / 24.0
	await wait_until(func(): return d.msgs.any(func(m): return m.get("state") == "lowering"), 3.5)
	var ev: Array = band.call()
	check(log.size() >= 1 and log[0][0] == "t1_fist_cutoff" and log[0][1] < 0.05, "fist starts at once: %s" % [log])
	check(ev.size() == 2 and ev[0][0] == "freeze" and absf(ev[0][1] - cut) < 0.06 and ev[1][0] == "lowering" \
		and absf(ev[1][1] - rel) < 0.06, "freeze on the cutoff frame (%.2f s), lowering at the release (%.2f s): %s" % [cut, rel, ev])
	link.queue_free()
	hub.queue_free()
	v.queue_free()


## A cue that arrives during a hold waits for the release and is not stale for the hold's
## length; one arriving before a non-held one-shot ends still goes stale as before.
func _test_hold_defers_instead_of_dropping() -> void:
	var d := FakeDirector.new()
	var link := new_link(null, d)
	link.bpm = 94.0  # release frame 50 = 2.66 s: longer than max_late_s
	var dropped := []
	link.move_dropped.connect(func(m, late): dropped.append(m))
	var t0 := now()
	var log := record(link, t0)
	link.request("t1_fist_cutoff", 2)
	await create_timer(0.3).timeout
	link.request("t2_point_player", 1)  # the tirade opener, cued just after the Stop bark
	var rel := 50.0 / 24.0 * 120.0 / 94.0
	await wait_until(func(): return log.size() >= 2, rel + 1.0)
	check(dropped.is_empty() and log.size() >= 2 and log[1][0] == "t2_point_player" and absf(log[1][1] - rel) < 0.08,
		"the opener plays at the release (%.2f s at 94 BPM), not dropped: %s %s" % [rel, log, dropped])
	link.queue_free()


## The cutoff fist (priority 2) may hold "Stop!" up to cutoff_lead_max_s; other bark cues keep
## bark_lead_max_s.
func _test_cutoff_fist_gets_the_longer_lead() -> void:
	var v := new_voice()
	var cue := func(uid, prio): v.handle_message("fletcher.state", {"action": "fist_cutoff", "gesture": "t1_fist_cutoff",
		"utterance_id": uid, "at_s": -0.45, "priority": prio})
	cue.call("c1", 2)
	cue.call("c2", 1)
	v.handle_message("fletcher.state", {"action": "point", "gesture": "t2_point_player", "utterance_id": "c3", "at_s": -0.7, "priority": 2})
	check(absf(v.bark_lead("c1") - 0.45) < 1e-6 and absf(v.bark_lead("c2") - 0.3) < 1e-6 and absf(v.bark_lead("c3") - 0.3) < 1e-6,
		"cutoff fist p2 held 0.45 (cap 0.5), p1 fist and other moves capped at 0.3: %.2f %.2f %.2f" % [v.bark_lead("c1"), v.bark_lead("c2"), v.bark_lead("c3")])
	v.queue_free()


## While a bark plays, the SFX bus is turned down (sidechain-style, from the Voice bus meter);
## afterwards it returns to its own volume. Without the buses it does nothing.
func _test_ducks_sfx_while_speaking() -> void:
	AudioServer.add_bus(1)
	AudioServer.set_bus_name(1, &"TestSFX")
	AudioServer.set_bus_volume_db(1, -3.0)
	AudioServer.add_bus(2)
	AudioServer.set_bus_name(2, &"TestVoice")
	var cam := Camera3D.new()  # the listener: a 3D player is silent without one
	root.add_child(cam)
	cam.position = Vector3(0, 0, 1)
	cam.current = true
	var v := new_voice()
	v.barks_dir = ProjectSettings.globalize_path("res://").path_join("../barks")
	v.duck_bus = &"TestSFX"
	for p in v.find_children("*", "AudioStreamPlayer3D", false, false):
		(p as AudioStreamPlayer3D).bus = &"TestVoice"
	var low := 0.0
	v.handle_message("fletcher.speech", {"utterance_id": "d1", "text": "Stop!", "clip_id": "stop_1"})
	var t0 := now()
	while now() - t0 < 1.0:
		low = minf(low, AudioServer.get_bus_volume_db(1) + 3.0)
		await process_frame
	await create_timer(2.0).timeout
	var back := AudioServer.get_bus_volume_db(1)
	check(low < -6.0 and absf(back + 3.0) < 0.3, "SFX bus ducks under the bark (%.1f dB) and comes back to -3 dB (%.2f)" % [low, back])
	v.queue_free()
	cam.queue_free()
	await process_frame
	AudioServer.remove_bus(2)
	AudioServer.remove_bus(1)
