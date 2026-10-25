extends RefCounted
## Tempo rules shared by the band and Terence. Every animation is authored at 120 BPM and
## 24 fps; each one has a tempo policy:
##   SYNCED  plays at bpm / 120 (the default for every animation)
##   LOCKED  synced, and while a song is loaded its loop is phase-locked to the song's beat
##   FREE    plays in real time whatever the tempo
## Crossfades are authored in seconds at 120 BPM and scale with the tempo like the moves they
## join, clamped to [MIN_FADE, MAX_FADE] real seconds: at 400 BPM a plain scaled 0.25 s fade
## would be 75 ms (a pop), at 40 BPM 0.75 s (mush). The clamp keeps every fade readable.
## Loaded with preload() by its users, so no global class cache entry is needed.

const AUTHORED_BPM := 120.0
const MIN_BPM := 40.0
const MAX_BPM := 400.0
const SYNCED := &"synced"
const LOCKED := &"locked"
const FREE := &"free"
const POLICIES := [SYNCED, LOCKED, FREE]
## Authored seconds per beat (0.5 s at 120 BPM).
const AUTHORED_BEAT := 60.0 / AUTHORED_BPM
const MIN_FADE := 0.12        # real seconds
const MAX_FADE := 0.45        # real seconds
## Beat-lock: the loop's phase error closes with this time constant (real seconds), and the
## playback rate never strays more than LOCK_MAX_DEV from the tempo's rate while it does.
const LOCK_TAU := 0.5
const LOCK_MAX_DEV := 0.25


## True when `bpm` is inside [MIN_BPM, MAX_BPM]; otherwise reports who rejected it.
static func check_bpm(bpm: float, who: String) -> bool:
	if is_nan(bpm) or bpm < MIN_BPM or bpm > MAX_BPM:
		push_error("%s: tempo %s BPM is outside %d..%d" % [who, bpm, MIN_BPM, MAX_BPM])
		return false
	return true


## Checks a {animation: policy} override table against the animations that exist.
static func check_policy_table(table: Dictionary, player: AnimationPlayer, who: String) -> bool:
	for anim in table:
		if not player.has_animation(anim):
			push_error("%s: tempo policy names unknown animation %s" % [who, anim])
			return false
		var policy: StringName = table[anim]
		if not policy in POLICIES:
			push_error("%s: tempo policy %s for %s is not one of %s" % [who, policy, anim, POLICIES])
			return false
		if policy == LOCKED and not check_lockable(player.get_animation(anim), anim, who):
			return false
	return true


## A beat-locked animation must loop and last a whole number of beats.
static func check_lockable(anim: Animation, anim_name: StringName, who: String) -> bool:
	if anim.loop_mode == Animation.LOOP_NONE:
		push_error("%s: beat-locked %s does not loop" % [who, anim_name])
		return false
	var beats := anim.length / AUTHORED_BEAT
	if beats < 1.0 or abs(beats - roundf(beats)) > 0.01:
		push_error("%s: beat-locked %s lasts %.3f beats, not a whole number" % [who, anim_name, beats])
		return false
	return true


## Playback rate for a policy at `bpm`.
static func rate(policy: StringName, bpm: float) -> float:
	return 1.0 if policy == FREE else bpm / AUTHORED_BPM


## Real fade length for a fade authored as `authored` seconds at 120 BPM, joining moves
## played at `bpm` (FREE moves keep the authored length).
static func fade_seconds(authored: float, policy: StringName, bpm: float) -> float:
	if policy == FREE:
		return authored
	return clampf(authored * AUTHORED_BPM / bpm, MIN_FADE, MAX_FADE)


## Where a beat-locked loop of `length` authored seconds should be at song beat `beat`.
static func locked_position(beat: float, length: float) -> float:
	return fposmod(beat * AUTHORED_BEAT, length)


## Animation time to advance a beat-locked loop this step: the tempo's step plus a gentle
## correction toward `target` (where the loop should be at the end of the step). The
## correction is proportional to the phase error and bounded, so the loop never jumps and
## never runs backwards.
static func locked_delta(pos: float, length: float, target: float, delta: float, anim_rate: float) -> float:
	var nominal := delta * anim_rate
	var err := fposmod(target - (pos + nominal) + length * 0.5, length) - length * 0.5
	var corr := err * (1.0 - exp(-delta / LOCK_TAU))
	var bound := nominal * LOCK_MAX_DEV
	return nominal + clampf(corr, -bound, bound)
