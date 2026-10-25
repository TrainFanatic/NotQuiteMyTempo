class_name BackingPlayer
extends Node
## The band's backing track: songs/<id>/backing.ogg (every non-drum part of the song, sample 0
## = song time 0 = the downbeat of bar 1), on the Band bus, started so that a chosen song time
## comes out of the speaker at a chosen moment of this device's clock (Time.get_ticks_usec()/1e6,
## the clock FletcherHub.to_local_time() maps laptop times onto). The whole audio chain, and
## how t0 ties the clocks together: quest_app/AUDIO.md.
##
##   var backing := BackingPlayer.new()
##   add_child(backing)
##   backing.load("user://songs/<id>")                         # backing.ogg (+ click.ogg if there)
##   backing.play_at(hub.to_local_time(t0), song_time_at_t0)   # on session.state count_in
##   director.set_song_position(backing.song_time())           # every frame: the band's beat lock
##   backing.stop(0.15)                                        # session.state stopped (the fist)
##   backing.seek(song_time_at_t0, hub.to_local_time(t0))      # the restart's count_in
##
## Timing model (Godot's own): play() takes effect at the next mix, which is
## AudioServer.get_time_to_next_mix() away, and what is mixed then is heard MIX_DELAY_FRAMES
## + output_latency() later. So the player starts the stream at the mix whose output is
## closest to the target, at the position that makes song_t land exactly on local_t: started
## early (by up to preroll_s) it plays a little of what comes before; started late (local_t
## already past) it skips ahead. Either way the timeline is exact; only the very first note of the song, which
## has nothing before it, can land up to half a frame early.
##
## song_time() is the playback clock: anchored to the wall clock at the start (smooth, no
## mix-sized steps) and pulled slowly toward the stream's real position, so it follows the
## audio clock's drift without inheriting its jitter. Before the start it counts up to the
## target (negative during a count-in from bar 1); after stop() it holds.

signal started(song_time: float, heard_t: float)  ## the stream started; song_time is heard at heard_t
signal finished  ## the stream played to its end

enum State { IDLE, SCHEDULED, PLAYING, STOPPED, FINISHED }

const BUS := &"Band"
## Godot mixes every playback this many output frames late (AudioServer's 64-frame lookahead,
## kept for click-free stops, plus the resampler's history). Measured: 68 frames at 48 kHz for
## both 48 and 44.1 kHz streams (tests/test_backing_player.gd, CoreAudio).
const MIX_DELAY_FRAMES := 68

@export var bus := BUS
@export var gain_db := 0.0
@export var click_enabled := false:  ## the optional click.ogg, on the same timeline
	set(v):
		click_enabled = v
		if _click_player:
			_click_player.volume_db = _click_db()
@export var click_db := -6.0
## Added to AudioServer.get_output_latency(): what a one-time calibration measured beyond what
## the driver reports (the Quest's Android driver reports little or nothing; AUDIO.md).
@export var extra_latency_s := 0.0
## How much music before the target may play when the start can't land on a mix exactly
## (a restart from bar N plays at most this much of bar N-1).
@export var preroll_s := 0.05
## Clock smoothing: the time constant with which song_time() follows the audio clock's drift,
## and the error beyond which it jumps instead (a paused app, a stalled driver).
@export var clock_tau_s := 1.0
@export var clock_resync_s := 0.08
const CLOCK_SETTLE_S := 0.1   # first mixes after a start: not used
const CLOCK_BASELINE_S := 0.3 # then this long: the position's constant lead over what is heard
const CLOCK_MEDIAN := 5       # readings that race a mix are off by a mix period: median of 5
const CLOCK_RESYNC_S := 0.3   # off by more than clock_resync_s this long: jump

## Ducking under Fletcher: while the Voice bus sounds, the Band bus goes down by up to
## duck_max_db (FletcherVoice does the same to the SFX bus, with a deeper cap). 0 turns it off.
@export var duck_source_bus := &"Voice"
@export var duck_threshold_db := -42.0
@export var duck_ratio := 5.0
@export var duck_max_db := 6.0
@export var duck_attack_s := 0.02
@export var duck_release_s := 0.6

var state := State.IDLE
var song_dir := ""
var duck_db := 0.0  ## the current duck (<= 0 dB), on top of the Band bus's own volume

var _player: AudioStreamPlayer
var _click_player: AudioStreamPlayer
var _target_local := 0.0  # SCHEDULED: when _target_song must be heard
var _target_song := 0.0
var _anchor_local := 0.0  # PLAYING: song time _anchor_song is heard at _anchor_local
var _anchor_song := 0.0
var _held := 0.0          # STOPPED / FINISHED / IDLE: song_time()
var _lead_samples: Array = []  # (measured - predicted) while the baseline is taken
var _lead := NAN               # the position's constant lead over the heard sample
var _off_since := INF         # since when the clock has been off by > clock_resync_s
var _errs: Array = []          # the last CLOCK_MEDIAN clock errors
var _frame_dt := 1.0 / 72.0
var _fade: Tween
var _duck_base := NAN


func _ready() -> void:
	_player = _make_player("Backing")
	_click_player = _make_player("Click")
	_click_player.volume_db = _click_db()
	if AudioServer.get_bus_index(bus) < 0:
		push_warning("BackingPlayer: no '%s' audio bus, playing on Master" % bus)


func _make_player(n: String) -> AudioStreamPlayer:
	var p := AudioStreamPlayer.new()
	p.name = n
	p.bus = bus if AudioServer.get_bus_index(bus) >= 0 else &"Master"
	add_child(p)
	return p


## Loads <song_dir>/backing.ogg and, if present, click.ogg (user:// downloads or res://).
## False, with a warning, when there is no backing; play_at() is then a no-op.
func load(dir: String) -> bool:
	_halt()
	state = State.IDLE
	_held = 0.0
	song_dir = dir
	_player.stream = _ogg(dir.path_join("backing.ogg"))
	_click_player.stream = _ogg(dir.path_join("click.ogg"))
	if _player.stream == null:
		push_warning("BackingPlayer: no backing.ogg in %s" % dir)
		return false
	return true


func is_loaded() -> bool:
	return _player != null and _player.stream != null


## Seconds of backing (0 when none is loaded).
func length() -> float:
	return _player.stream.get_length() if is_loaded() else 0.0


func is_playing() -> bool:
	return state == State.PLAYING


## Song time `song_t` will come out of the speaker at `local_t` (this device's seconds). If
## local_t is already past, it starts at once, seeked forward to where the song is by now.
func play_at(local_t: float, song_t: float) -> void:
	if not is_loaded():
		return
	_halt()
	_target_local = local_t
	_target_song = song_t
	state = State.SCHEDULED
	_try_start()


## Jump to song time `song_t`, heard at `local_t` (default: as soon as possible).
func seek(song_t: float, local_t: float = -1.0) -> void:
	play_at(_now() if local_t < 0.0 else local_t, song_t)


## Stops the band, fading out over fade_s. song_time() holds where it was.
func stop(fade_s: float = 0.0) -> void:
	if state == State.SCHEDULED or state == State.PLAYING:
		_held = song_time()
		state = State.STOPPED
	if not _player.playing:
		return
	if fade_s <= 0.0 or not is_inside_tree():
		_halt()
		return
	_fade = create_tween()
	for p in [_player, _click_player]:
		_fade.parallel().tween_property(p, "volume_db", -60.0, fade_s)
	_fade.tween_callback(_halt)


## The song time heard right now: the band's beat lock (SceneDirector.set_song_position).
func song_time() -> float:
	match state:
		State.SCHEDULED:
			return _target_song - (_target_local - _now())
		State.PLAYING:
			return _anchor_song + (_now() - _anchor_local)
	return _held


## What the driver reports plus the calibrated extra (AUDIO.md, "Calibration").
func output_latency() -> float:
	return AudioServer.get_output_latency() + extra_latency_s


## When what play() starts now is first heard: the next mix, Godot's mix delay, the output.
func _heard_if_started_now(now: float) -> float:
	return now + maxf(AudioServer.get_time_to_next_mix(), 0.0) \
		+ MIX_DELAY_FRAMES / AudioServer.get_mix_rate() + output_latency()


func _process(delta: float) -> void:
	_frame_dt = lerpf(_frame_dt, clampf(delta, 0.001, 0.1), 0.1)
	_duck(delta)
	match state:
		State.SCHEDULED:
			_try_start()
		State.PLAYING:
			if not _player.playing:
				_held = length()
				state = State.FINISHED
				finished.emit()
			else:
				_track_clock(delta)


## Starts the stream if this frame's mix is the best one for the target (see the top).
func _try_start() -> void:
	var heard := _heard_if_started_now(_now())
	var early := _target_local - heard  # > 0: what is mixed next is heard before the target
	if early > 0.0 and early > minf(_target_song, preroll_s) and early > 0.5 * _frame_dt:
		return  # a later frame does better
	var from := maxf(0.0, _target_song - early)
	if from >= length():
		_held = length()
		state = State.FINISHED
		finished.emit()
		return
	for p in [_player, _click_player]:
		if p.stream:
			p.volume_db = gain_db if p == _player else _click_db()
			p.play(from)
	_anchor_local = heard
	_anchor_song = from
	_lead = NAN
	_lead_samples.clear()
	_off_since = INF
	_errs.clear()
	state = State.PLAYING
	started.emit(from, heard)


## Pulls the wall-clock anchor toward the stream's position, so song_time() follows the audio
## clock's drift. get_playback_position() is where decoding has got to: one mix period ahead of
## the next mix's start, plus Godot's internal buffering, which depends on the stream and mix
## rates. So the first CLOCK_BASELINE_S after a start measure that lead (the start itself is
## exact), and from then on only changes of it move the clock.
func _track_clock(delta: float) -> void:
	var now := _now()
	if now < _anchor_local + CLOCK_SETTLE_S:
		return
	var measured := _player.get_playback_position() - maxf(AudioServer.get_time_to_next_mix(), 0.0)
	var dev := measured - (_anchor_song + (now - _anchor_local))
	if is_nan(_lead):
		_lead_samples.append(dev)
		if now >= _anchor_local + CLOCK_SETTLE_S + CLOCK_BASELINE_S and _lead_samples.size() >= 5:
			_lead_samples.sort()
			_lead = _lead_samples[_lead_samples.size() / 2]
		return
	var err := dev - _lead
	if absf(err) > clock_resync_s:
		_off_since = minf(_off_since, now)
		if now - _off_since >= CLOCK_RESYNC_S:  # the audio really jumped (a pause, a stall)
			_anchor_song += err
			_off_since = INF
			_errs.clear()
		return
	_off_since = INF
	_errs.append(err)
	if _errs.size() > CLOCK_MEDIAN:
		_errs.pop_front()
	var sorted := _errs.duplicate()
	sorted.sort()
	_anchor_song += sorted[sorted.size() / 2] * (1.0 - exp(-delta / maxf(clock_tau_s, 0.01)))


func _halt() -> void:
	if _fade and _fade.is_valid():
		_fade.kill()
	_fade = null
	if _player:
		_player.stop()
		_click_player.stop()


func _click_db() -> float:
	return click_db if click_enabled else -80.0


func _duck(delta: float) -> void:
	var bi := AudioServer.get_bus_index(bus)
	var vi := AudioServer.get_bus_index(duck_source_bus)
	if bi < 0 or vi < 0 or bi == vi:
		return
	if is_nan(_duck_base):
		_duck_base = AudioServer.get_bus_volume_db(bi)
	var peak := maxf(AudioServer.get_bus_peak_volume_left_db(vi, 0), AudioServer.get_bus_peak_volume_right_db(vi, 0))
	var target := -clampf((peak - duck_threshold_db) * (1.0 - 1.0 / duck_ratio), 0.0, duck_max_db)
	var tau := duck_attack_s if target < duck_db else duck_release_s
	duck_db += (target - duck_db) * (1.0 - exp(-delta / maxf(tau, 0.001)))
	AudioServer.set_bus_volume_db(bi, _duck_base + duck_db)


func _exit_tree() -> void:
	var bi := AudioServer.get_bus_index(bus)
	if bi >= 0 and not is_nan(_duck_base):
		AudioServer.set_bus_volume_db(bi, _duck_base)


static func _now() -> float:
	return Time.get_ticks_usec() / 1e6


static func _ogg(path: String) -> AudioStream:
	if ResourceLoader.exists(path):
		return ResourceLoader.load(path)  # imported (res:// in exported builds)
	if FileAccess.file_exists(path):
		var s := AudioStreamOggVorbis.load_from_file(path)  # downloaded (user://songs/<id>/)
		if s:
			s.loop = false
		return s
	return null
