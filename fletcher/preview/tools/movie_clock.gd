class_name MovieClock
## Time source for the Fletcher scripts in this movie copy. Under --write-movie every frame is
## exactly 1/fps of simulated time, so the wall clock (which the real scripts use) would drift
## from what is rendered; here it is frames / fps. Outside a movie it is the wall clock.
static var fps := 30.0

static func now() -> float:
	if Engine.get_write_movie_path() != "":
		return Engine.get_process_frames() / fps
	return Time.get_ticks_usec() / 1e6
