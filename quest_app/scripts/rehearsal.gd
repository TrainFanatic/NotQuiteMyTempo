class_name Rehearsal
extends Node3D
## Main scene: builds the band room from the imported assets at runtime.
##   room (res://assets/room) with its seat_/spot_ markers
##   16 band members at their seats, instruments on their Instrument bones
##   Terence at spot_terence
##   lights, environment, player rig (XR or desktop preview)
##   SceneDirector (the game-facing API) and, optionally, a demo timeline via FakeInjector

## Marker -> (member glb, instrument glb). Same table as the asset repo's band_anims.SEATING.
const SEATING := {
	"seat_sax_1": ["band_tenor_sax_2", "tenor_sax_2"], "seat_sax_2": ["band_alto_sax_2", "alto_sax_2"],
	"seat_sax_3": ["band_alto_sax_1", "alto_sax_1"], "seat_sax_4": ["band_tenor_sax_1", "tenor_sax_1"],
	"seat_sax_5": ["band_baritone_sax_1", "baritone_sax_1"],
	"seat_trombone_1": ["band_trombone_1", "trombone_1"], "seat_trombone_2": ["band_trombone_2", "trombone_2"],
	"seat_trombone_3": ["band_trombone_3", "trombone_3"], "seat_trombone_4": ["band_trombone_4", "trombone_4"],
	"seat_trumpet_1": ["band_trumpet_1", "trumpet_1"], "seat_trumpet_2": ["band_trumpet_2", "trumpet_2"],
	"seat_trumpet_3": ["band_trumpet_3", "trumpet_3"], "seat_trumpet_4": ["band_trumpet_4", "trumpet_4"],
	"seat_piano_1": ["band_piano_1", ""], "seat_guitar_1": ["band_guitar_1", "guitar_1"],
	"seat_bass_1": ["band_bass_1", "upright_bass_1"],
}
const ROOM_GLB := "res://assets/room/room_v005.glb"
const PLAYER_HEAD_HEIGHT := 1.15
const DEMO := "res://scenes/demo_timeline.json"
const SfxLibrary := preload("res://scripts/sfx/sfx_library.gd")
const SfxVoicePool := preload("res://scripts/sfx/sfx_voice_pool.gd")
const SfxCuePlayer := preload("res://scripts/sfx/sfx_cue_player.gd")
const CinematicDirector := preload("res://scripts/cinematic/cinematic_director.gd")   # cinematics

## Plays scenes/demo_timeline.json on start (count-off, band in, a cutoff, a stare...).
@export var run_demo := true
## Plays every animation in turn with a floating label (scripts/showcase.gd). Takes over
## from run_demo. Off by default in code; scenes/rehearsal.tscn turns it on.
@export var run_showcase := false
## Animation-cued sound effects (scripts/sfx). On, the assets in res://assets/sfx must exist
## (tools/sync_assets.sh); a missing one is an error. Tests that need no sound turn it off.
@export var sfx_enabled := true
## Film-style cut-ins on Terence's big moves (scripts/cinematic): a cinema panel in XR, a
## camera cut on the desktop. Per-move opt-outs are in scripts/cinematic/shot_table.gd.
@export var cinematics_enabled := true

var room: Node3D
var band: Array = []
var terence: Terence
var director: SceneDirector
var rig: PlayerRig
var injector: FakeInjector
var sfx_library: SfxLibrary
var sfx_pool: SfxVoicePool
var cinematic: CinematicDirector   # cinematics


var showcase: Showcase


func _ready() -> void:
	build()
	if run_showcase:
		showcase = Showcase.new()
		showcase.name = "Showcase"
		add_child(showcase)
		showcase.setup(director)
		showcase.start()
	elif run_demo:
		injector = FakeInjector.new()
		injector.name = "FakeInjector"
		add_child(injector)
		if not injector.load_json(director, DEMO):
			push_error("Rehearsal: demo timeline failed to load")


func build() -> void:
	var scene: PackedScene = load(ROOM_GLB)
	if scene == null:
		push_error("Rehearsal: missing %s (run tools/sync_assets.sh)" % ROOM_GLB)
		return
	room = scene.instantiate()
	room.name = "Room"
	add_child(room)
	for marker_name in SEATING:
		var marker := marker_node(marker_name)
		if marker == null:
			return
		var m := BandMember.new()
		add_child(m)
		m.setup(SEATING[marker_name][0], SEATING[marker_name][1])
		m.global_transform = character_at(marker)
		band.append(m)
	var spot := marker_node("spot_terence")
	var drummer := marker_node("spot_drummer")
	if spot == null or drummer == null:
		return
	var head := drummer.global_position + Vector3.UP * PLAYER_HEAD_HEIGHT
	terence = Terence.new()
	add_child(terence)
	terence.setup(character_at(spot), head, room)
	director = SceneDirector.new()
	director.name = "SceneDirector"
	add_child(director)
	director.setup(terence, band)
	_add_lights()
	rig = PlayerRig.new()
	add_child(rig)
	rig.setup(drummer.global_transform, spot.global_position + Vector3.UP * 1.6)
	if rig.xr_mode:
		_add_kit_window(drummer)
	# --- Cinematics: close-ups of Terence on the moves in the shot table ---
	if cinematics_enabled:
		cinematic = CinematicDirector.new()
		cinematic.name = "CinematicDirector"
		add_child(cinematic)
		var world_env := get_node("WorldEnvironment") as WorldEnvironment
		if not cinematic.setup(terence, rig.camera, rig.xr_mode, world_env.environment):
			push_error("Rehearsal: the cinematic director failed to set up")
			return
	# --- end Cinematics ---
	# --- SFX: one shared voice pool, a cue player on every band member and Terence ---
	if sfx_enabled:
		sfx_library = SfxLibrary.load_dir()
		if sfx_library == null:
			push_error("Rehearsal: sfx_enabled but the sound effects did not load (run tools/sync_assets.sh)")
			return
		sfx_pool = SfxVoicePool.new()
		add_child(sfx_pool)
		if not sfx_pool.setup():
			return
		if not SfxCuePlayer.attach_all(band, terence, sfx_library, sfx_pool):
			push_error("Rehearsal: sound cues failed to attach")
			return
	# --- end SFX ---


func marker_node(marker_name: String) -> Node3D:
	var n := room.find_child(marker_name, true, false) as Node3D
	if n == null:
		push_error("Rehearsal: room has no marker %s" % marker_name)
	return n


## Characters face +Z (Blender -Y); markers look down -Z (Blender +Y): turn 180 degrees.
static func character_at(marker: Node3D) -> Transform3D:
	var xf := marker.global_transform
	return Transform3D(xf.basis * Basis(Vector3.UP, PI), xf.origin)


## XR only: a box around the drummer's spot drawn with alpha 0, so the real kit shows
## through the virtual room via passthrough. Size and offset are in the drummer's frame
## (forward = toward Terence).
const KIT_WINDOW_SIZE := Vector3(1.9, 1.05, 1.6)      # width, height (kept below seated eye height), depth
const KIT_WINDOW_FORWARD := 0.35                     # centre this far in front of the throne


func _add_kit_window(drummer: Node3D) -> void:
	var mat := ShaderMaterial.new()
	mat.shader = load("res://shaders/passthrough_window.gdshader")
	var box := BoxMesh.new()
	box.size = KIT_WINDOW_SIZE
	var mi := MeshInstance3D.new()
	mi.name = "KitWindow"
	mi.mesh = box
	mi.material_override = mat
	mi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	add_child(mi)
	var xf := drummer.global_transform
	var fwd := -xf.basis.z.normalized()
	mi.global_transform = Transform3D(xf.basis, xf.origin + fwd * KIT_WINDOW_FORWARD + Vector3.UP * (KIT_WINDOW_SIZE.y / 2.0 - 0.01))


func _add_lights() -> void:
	var env := Environment.new()
	env.background_mode = Environment.BG_COLOR
	# in XR the clear colour must be transparent so passthrough shows wherever nothing is drawn
	env.background_color = Color(0, 0, 0, 0) if PlayerRig.xr_requested() else Color(0.02, 0.015, 0.01)
	env.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
	env.ambient_light_color = Color(0.55, 0.38, 0.24)
	env.ambient_light_energy = 0.35
	env.tonemap_mode = Environment.TONE_MAPPER_FILMIC
	# glow is an extra full-screen pass: too expensive on the Quest, keep it for the desktop preview
	env.glow_enabled = not PlayerRig.xr_requested()
	env.glow_intensity = 0.6
	var we := WorldEnvironment.new()
	we.name = "WorldEnvironment"
	we.environment = env
	add_child(we)
	# warm wash from the coffers over the band and the bandleader
	var key := DirectionalLight3D.new()
	key.name = "CofferWash"
	key.light_color = Color(1.0, 0.72, 0.45)
	key.light_energy = 0.35
	key.rotation_degrees = Vector3(-70, 20, 0)
	add_child(key)
	# a warm pool at each wall sconce (the room's sconce_* meshes)
	for s in room.find_children("sconce_*", "", true, false):
		var l := OmniLight3D.new()
		l.light_color = Color(1.0, 0.6, 0.3)
		l.light_energy = 0.8
		l.omni_range = 2.5
		# the shade sits 0.25 m out from the wall along the sconce's local +Z (Blender -Y)
		var out_dir := (s as Node3D).global_transform.basis.z.normalized()
		l.position = (s as Node3D).global_position + Vector3(0, 0.05, 0) + out_dir * 0.3
		add_child(l)


func _process(_delta: float) -> void:
	if injector != null and director != null:
		director.set_song_position(injector.time)
