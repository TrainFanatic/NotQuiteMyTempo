class_name Showcase
extends Node
## Plays every animation in turn with a floating label naming it, so you can review them all
## in the headset or the desktop preview:
##   1. the band cycle: raise, play_loop, lower
##   2. each band reaction (freeze from playing; the rest from resting; spit_valve on trombones)
##   3. every Terence move in the order of terence_v003_moves.json (45 moves, then the walk cycle)
## Then it starts over. Looping moves are shown for LOOP_HOLD seconds.

const LOOP_HOLD := 4.0
const GAP := 0.6
const BAND_REACTIONS := [&"startle", &"look_down", &"look_at_drummer", &"flip_page", &"arms_rest"]

var director: SceneDirector
var label: Label3D
var running := false


func setup(target: SceneDirector) -> void:
	director = target
	label = Label3D.new()
	label.name = "ShowcaseLabel"
	label.billboard = BaseMaterial3D.BILLBOARD_ENABLED
	label.no_depth_test = true
	label.fixed_size = false
	label.pixel_size = 0.0035
	label.font_size = 64
	label.outline_size = 12
	label.modulate = Color(1.0, 0.85, 0.4)
	director.terence.add_child(label)
	label.position = Vector3(0, 2.15, 0)


func start() -> void:
	running = true
	_run()


func _show(text: String) -> void:
	label.text = text
	print("Showcase: ", text)


func _wait(seconds: float) -> void:
	await get_tree().create_timer(seconds).timeout


func _run() -> void:
	while running:
		var t: Terence = director.terence
		# --- band cycle ---
		director.stop_move()
		_show("BAND: raise -> play_loop")
		director.set_band_state("all", &"playing")
		await _wait(0.4)
		t.play_move(&"t0_conduct")
		await _wait(5.0)
		_show("BAND: freeze (from playing)")
		director.react("all", &"freeze")
		await _wait(2.0)
		_show("BAND: lower -> rest")
		director.set_band_state("all", &"lowering")
		t.stop()
		await _wait(2.0)
		for r in BAND_REACTIONS:
			_show("BAND: %s" % r)
			director.react("all", r)
			await _wait(2.4)
			if r == &"arms_rest":   # loops until told otherwise
				director.set_band_state("all", &"resting")
				await _wait(0.6)
		_show("BAND: spit_valve (trombones)")
		director.react("trombone", &"spit_valve")
		await _wait(2.6)
		# --- Terence ---
		var i := 0
		for id in t.moves:
			i += 1
			var m: Dictionary = t.meta["moves"][String(id)]
			_show("TERENCE %d/%d: %s" % [i, t.moves.size(), id])
			t.play_move(id)
			while t.is_travelling():   # close-up moves start once he has walked over
				await get_tree().process_frame
			if m["loop"]:
				await _wait(LOOP_HOLD)
				t.stop()
				await _wait(GAP)
			else:
				await _wait(float(m["seconds"]) + GAP)
		_show("SHOWCASE: starting over")
		await _wait(2.0)
