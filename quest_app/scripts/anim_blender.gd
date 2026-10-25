extends RefCounted
## Crossfades between the animations of one AnimationPlayer, advanced manually by its owner.
## AnimationPlayer keeps every animation that is still fading out in its blend list, so a
## switch in the middle of a fade blends on from the mixed pose instead of popping. That is
## why the characters use the player directly rather than an AnimationTree state machine,
## whose transitions only fade from one previous state.
## Loaded with preload() by its users, so no global class cache entry is needed.

## Emitted from advance() for the current (dominant) animation: animation time moved from
## from_pos to to_pos (seconds, authored time). wrapped is true if a loop wrapped within the step
## (then from_pos > to_pos; listeners treat it as (from_pos, length] + [0, to_pos]).
signal advanced(anim: StringName, from_pos: float, to_pos: float, wrapped: bool)
## Emitted from play() when an animation starts (or restarts) at time 0.
signal started(anim: StringName)

var player: AnimationPlayer
## The animation most recently started (the one fading in, or fully in).
var current: StringName = &""
## Counts play() calls, so a restart of the same animation can be told from no change.
var plays := 0


func _init(anim_player: AnimationPlayer) -> void:
	player = anim_player
	player.callback_mode_process = AnimationMixer.ANIMATION_CALLBACK_MODE_PROCESS_MANUAL
	player.active = true


## Starts `anim` from its beginning, fading in over `xfade` seconds of animation time.
## Restarting the animation that is already current also crossfades: the player keeps a
## fading copy at the old position while the new one starts at 0.
## `at` > 0 enters a looping animation part way (a beat-locked loop joining the song mid
## bar); then `started` is not emitted, and the first `advanced` reports time from `at`.
func play(anim: StringName, xfade: float, at: float = 0.0) -> bool:
	if not player.has_animation(anim):
		push_error("AnimBlender: %s has no animation %s" % [player.get_path(), anim])
		return false
	if at < 0.0 or at >= player.get_animation(anim).length:
		push_error("AnimBlender: %s cannot start at %.3f s (length %.3f s)" % [anim, at, player.get_animation(anim).length])
		return false
	if at > 0.0 and not is_looping(anim):
		push_error("AnimBlender: only looping animations start part way (%s at %.3f s)" % [anim, at])
		return false
	player.play(anim, xfade)
	if anim == current or at > 0.0:
		player.seek(at, false)
	current = anim
	plays += 1
	if at == 0.0:
		started.emit(anim)
	return true


## Advances by `delta` seconds of animation time (the owner has applied tempo already).
func advance(delta: float) -> void:
	var moving := player.is_playing() and delta > 0.0
	var from_pos := player.current_animation_position if moving else 0.0
	player.advance(delta)
	if not moving:
		return
	var total := length()
	var to_pos := player.current_animation_position if player.is_playing() else total
	var wrapped := is_looping(current) and (to_pos < from_pos or delta >= total)
	if wrapped or to_pos > from_pos:
		advanced.emit(current, from_pos, to_pos, wrapped)


## True once a non-looping current animation has played to its end (its last pose holds).
func finished() -> bool:
	return current != &"" and not player.is_playing()


## Seconds into the current animation.
func position() -> float:
	return player.current_animation_position if player.is_playing() else length()


func length() -> float:
	return player.get_animation(current).length


func is_looping(anim: StringName) -> bool:
	return player.get_animation(anim).loop_mode != Animation.LOOP_NONE
