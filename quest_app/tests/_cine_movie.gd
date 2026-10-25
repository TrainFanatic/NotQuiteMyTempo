extends Node
## Throwaway: records cinematic moves for a visual check. Panel mode with `-- --panel`.
const CinematicDirector := preload("res://scripts/cinematic/cinematic_director.gd")
const MOVES := [&"t1_fist_cutoff", &"t1_flick", &"t1_wave_off", &"t1_clap_stop", &"t1_fist_slam",
	&"t2_point_player", &"t4_shout", &"t4_charge", &"t5_chair_throw", &"ap_grin", &"t2_judging_stare",
	&"t3_whisper", &"t5_rhythm_slaps", &"t3_loom"]
var r: Rehearsal
var log_file: FileAccess

func _ready() -> void:
	var panel := "--panel" in OS.get_cmdline_user_args()
	r = Rehearsal.new()
	r.run_demo = false
	r.sfx_enabled = false
	r.cinematics_enabled = not panel
	add_child(r)
	var cd: CinematicDirector = r.cinematic
	if panel:
		cd = CinematicDirector.new()
		r.add_child(cd)
		cd.setup(r.terence, r.rig.camera, true, (r.get_node("WorldEnvironment") as WorldEnvironment).environment)
	cd.shot_started.connect(func(m, i, f): print("SHOT frame %d %s %d %s" % [Engine.get_frames_drawn(), m, i, f]))
	cd.sequence_ended.connect(func(m): print("END frame %d %s" % [Engine.get_frames_drawn(), m]))
	_run()

func _wait(s: float) -> void:
	await get_tree().create_timer(s).timeout

func _run() -> void:
	await _wait(0.5)
	for id in MOVES:
		r.terence.play_move(id)
		while r.terence.is_travelling():
			await get_tree().process_frame
		var m: Dictionary = r.terence.meta["moves"][String(id)]
		await _wait(float(m["seconds"]) + 1.0)
		if m["loop"]:
			r.terence.stop()
			await _wait(0.5)
		while r.terence.is_travelling():
			await get_tree().process_frame
	print("MOVIE DONE")
	get_tree().quit()
