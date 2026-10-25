extends "res://scripts/game/backing_player.gd"
## Latency calibration (laptop side: `python -m nqmt.calibrate`, CONTRACT.md "calibrate.*").
## On `calibrate.start {id, times}` it plays one short clap per time (laptop clock) on the Band
## bus, scheduled exactly like the backing track: one generated clap train started with
## BackingPlayer.play_at, so clap k is heard at to_local_time(times[k]) with the same output
## latency compensation (extra_latency_s, the Settings latency) as the song. The laptop hears
## them back through the normal mic path, and the delay it measures is what a drummer playing
## along to the band will look like, which the bridge then subtracts.
## GameSession makes one (setup) and passes it the hub and the backing player.

const RATE := 48000
const PRE_S := 0.1         # silence before the first clap (play_at's preroll never clips it)
const CLAP_S := 0.06

var hub: Node              # FletcherHub
var backing_ref: Node      # the song's BackingPlayer: its extra_latency_s is used here too
var last_id := ""


func _ready() -> void:
	duck_source_bus = &""  # never touch the Band bus volume (the backing player ducks it)
	super()
	if hub:
		hub.message_received.connect(_on_message)


func _on_message(type: String, data: Dictionary, _env: Dictionary) -> void:
	if type == "calibrate.start":
		start(data)


## Schedules the claps; answers `calibrate.ack` with what was scheduled (or the error).
func start(data: Dictionary) -> void:
	var times: Array = data.get("times", [])
	var id := str(data.get("id", ""))
	if times.is_empty():
		_ack(id, 0, "no times")
		return
	if backing_ref and "extra_latency_s" in backing_ref:
		extra_latency_s = backing_ref.extra_latency_s
	gain_db = float(data.get("gain_db", 0.0))
	var t_first := float(times[0])
	var rel: Array = []
	for t in times:
		rel.append(PRE_S + float(t) - t_first)
	_player.stream = clap_train(rel)
	_click_player.stream = null
	state = State.IDLE
	last_id = id
	var local_t: float = hub.to_local_time(t_first) if hub else _now() + 1.0
	play_at(local_t, PRE_S)
	_ack(id, times.size(), "", local_t - _now())


func _ack(id: String, n: int, error: String, lead_s := 0.0) -> void:
	if hub == null:
		return
	var d := {"id": id, "n": n, "lead_s": snappedf(lead_s, 0.001),
		"output_latency_s": snappedf(output_latency(), 0.0001),
		"clock_offset": hub.clock_offset if hub.clock_offset != INF else null}
	if error != "":
		d["error"] = error
	hub.send("calibrate.ack", d)


## A mono 48 kHz 16-bit stream with a clap (noise burst, ~8 ms decay) starting at each time
## in `at` (s from sample 0), plus CLAP_S of tail.
static func clap_train(at: Array) -> AudioStreamWAV:
	var last := 0.0
	for a in at:
		last = maxf(last, float(a))
	var n := int((last + CLAP_S + 0.2) * RATE)
	var buf := PackedFloat32Array()
	buf.resize(n)
	var rng := RandomNumberGenerator.new()
	rng.seed = 5678
	var clap_n := int(CLAP_S * RATE)
	for a in at:
		var s0 := int(round(float(a) * RATE))
		for i in clap_n:
			if s0 + i < n:
				buf[s0 + i] += rng.randf_range(-1.0, 1.0) * exp(-float(i) / (0.008 * RATE))
	var bytes := PackedByteArray()
	bytes.resize(n * 2)
	for i in n:
		bytes.encode_s16(i * 2, int(clampf(buf[i] * 0.9, -1.0, 1.0) * 32767.0))
	var w := AudioStreamWAV.new()
	w.format = AudioStreamWAV.FORMAT_16_BITS
	w.mix_rate = RATE
	w.stereo = false
	w.data = bytes
	return w
