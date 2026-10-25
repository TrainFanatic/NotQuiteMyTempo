extends Node
## Song files from the laptop's library server, cached in user://songs/<id>/:
##   backing.ogg       the band without the drums (BackingPlayer plays it)
##   arrangement.mid   the source MIDI (SceneDirector.load_song: who plays when)
##   band_tracks.json  MIDI track name -> band sections, for load_song
##   drums.drums.txt + drums.tempo.json  the drum part and its bars, for the chart on the
##                     music stand (scripts/game/drum_chart.gd); when song.json counts `notes`
## fetch(song, base_url) downloads what the song has (song.json's `backing`/`arrangement`
## flags; a 404 just means "not there") one file at a time over HTTP, each into a .part file
## renamed when complete, then writes the song's song.json beside them. A song is cached
## while that song.json's `imported_at` matches the library's, so a re-import downloads again.
## Loaded with preload() by its users.

signal prepared(song_id: String, dir: String)
signal failed(song_id: String, why: String)

## The cache (tests point it elsewhere).
static var root := "user://songs/"
const META := "song.json"
const TIMEOUT_S := 30.0

var _http: HTTPRequest
var _job := {}          # {song, base, files: [left], missing: []}
var _queue: Array = []  # [[song, base_url]] waiting behind _job


static func valid_id(id: String) -> bool:
	var re := RegEx.create_from_string("^[A-Za-z0-9][A-Za-z0-9_.-]*$")
	return re.search(id) != null and not id.contains("..")


static func song_dir(id: String) -> String:
	return root + id + "/"


static func files_for(song: Dictionary) -> Array:
	var f := []
	if song.get("backing", true) != false:
		f.append("backing.ogg")
	if song.get("arrangement", true) != false:
		f.append_array(["arrangement.mid", "band_tracks.json"])
	if typeof(song.get("notes")) in [TYPE_INT, TYPE_FLOAT]:   # the library's songs all have a sheet
		f.append_array(["drums.drums.txt", "drums.tempo.json"])
	return f


## True when everything the song lists is in the cache for this import of it.
static func is_cached(song: Dictionary) -> bool:
	var id := str(song.get("id", ""))
	if not valid_id(id):
		return false
	var dir := song_dir(id)
	var meta = JSON.parse_string(FileAccess.get_file_as_string(dir + META)) if FileAccess.file_exists(dir + META) else null
	if not meta is Dictionary or str(meta.get("imported_at", "")) != str(song.get("imported_at", "")):
		return false
	var missing: Array = meta.get("_missing", [])
	for f in files_for(song):
		if not f in missing and not FileAccess.file_exists(dir + f):
			return false
	return true


func is_busy(id: String = "") -> bool:
	if id == "":
		return not _job.is_empty()
	return (not _job.is_empty() and _job["song"]["id"] == id) or _queue.any(func(q): return q[0]["id"] == id)


## Download the song unless it is cached; `prepared` either way (at once when cached).
func fetch(song: Dictionary, base_url: String) -> void:
	var id := str(song.get("id", ""))
	if not valid_id(id):
		failed.emit(id, "bad song id %s" % id)
		return
	if is_cached(song):
		prepared.emit(id, song_dir(id))
		return
	if is_busy(id):
		return
	if base_url == "":
		failed.emit(id, "no library base_url yet")
		return
	_queue.append([song, base_url])
	if _job.is_empty():
		_next_job()


func _next_job() -> void:
	if _queue.is_empty():
		_job = {}
		return
	var q: Array = _queue.pop_front()
	var song: Dictionary = q[0]
	var dir := song_dir(str(song["id"]))
	DirAccess.make_dir_recursive_absolute(dir)
	_job = {"song": song, "base": str(q[1]), "files": files_for(song), "missing": []}
	_next_file()


func _next_file() -> void:
	var id := str(_job["song"]["id"])
	var dir := song_dir(id)
	if _job["files"].is_empty():
		var meta: Dictionary = _job["song"].duplicate(true)
		meta["_missing"] = _job["missing"]
		var f := FileAccess.open(dir + META, FileAccess.WRITE)
		f.store_string(JSON.stringify(meta, "  "))
		f.close()
		_job = {}
		prepared.emit(id, dir)
		_next_job()
		return
	var file: String = _job["files"][0]
	if _http == null:
		_http = HTTPRequest.new()
		_http.name = "HTTP"
		_http.timeout = TIMEOUT_S
		_http.request_completed.connect(_on_completed)
		add_child(_http)
	_http.download_file = dir + file + ".part"
	var url := "%s/%s/%s" % [str(_job["base"]).trim_suffix("/"), id.uri_encode(), file]
	var err := _http.request(url)
	if err != OK:
		_fail("cannot request %s (error %d)" % [url, err])


func _on_completed(result: int, code: int, _headers: PackedStringArray, _body: PackedByteArray) -> void:
	if _job.is_empty():
		return
	var id := str(_job["song"]["id"])
	var file: String = _job["files"].pop_front()
	var part := song_dir(id) + file + ".part"
	if result == HTTPRequest.RESULT_SUCCESS and code == 200:
		DirAccess.remove_absolute(song_dir(id) + file)
		if DirAccess.rename_absolute(part, song_dir(id) + file) != OK:
			_fail("cannot move %s into place" % part)
			return
	elif result == HTTPRequest.RESULT_SUCCESS and code == 404:
		DirAccess.remove_absolute(part)
		_job["missing"].append(file)
	else:
		DirAccess.remove_absolute(part)
		_fail("%s: HTTP result %d, status %d" % [file, result, code])
		return
	_next_file()


func _fail(why: String) -> void:
	var id := str(_job["song"]["id"])
	push_warning("[SongCache] %s: %s" % [id, why])
	_job = {}
	failed.emit(id, why)
	_next_job()
