extends Node3D
## The in-song HUD, kept out of the way: two small world-space labels in the drummer's frame
## (nothing is stuck to the head).
##   status, up and to the left over the saxes: the song, the bar, and a timing hint from
##           the referee's judgment.note (a running mean of error_ms: RUSHING / DRAGGING)
##   subtitle, low in front, under Terence's line of sight: what Fletcher just said
## Loaded with preload() by its users.

const INK := Color(1.0, 0.86, 0.55)       # the showcase label's warm yellow, dimmer
const RED := Color(1.0, 0.42, 0.32)
const TREND_MS := 12.0                    # |mean error| beyond this is a trend (bridge `good` is 35)
const TREND_ALPHA := 0.25                 # weight of each new hit in the running mean
const TREND_HOLD_S := 4.0                 # the hint fades this long after the last hit
const SUBTITLE_HOLD_S := 4.0
## Positions in the drummer's frame (x right, f forward, y up from the floor), metres.
const STATUS_AT := {"x": -0.62, "f": 1.7, "y": 1.5}
const SUBTITLE_AT := {"x": 0.0, "f": 2.3, "y": 0.86}

var status: Label3D
var subtitle: Label3D
var title := ""
var bar := 0
var bars := 0
var trend_ms := NAN          # running mean of error_ms (+ = late), NAN before the first hit
var _trend_age := INF
var _subtitle_age := INF


func build() -> void:
	name = "GameHud"
	status = _label("Status", 28, INK)
	subtitle = _label("Subtitle", 34, Color(1, 1, 1))
	subtitle.pixel_size = 0.0024
	subtitle.width = 900.0
	subtitle.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	_refresh()


func _label(n: String, size: int, color: Color) -> Label3D:
	var l := Label3D.new()
	l.name = n
	l.font_size = size
	l.outline_size = 8
	l.outline_modulate = Color(0, 0, 0, 0.8)
	l.modulate = color
	l.pixel_size = 0.0016
	l.no_depth_test = true
	l.fixed_size = false
	l.texture_filter = BaseMaterial3D.TEXTURE_FILTER_NEAREST
	add_child(l)
	return l


func place(drummer: Transform3D, eye_height: float) -> void:
	var eye := drummer.origin + Vector3.UP * eye_height
	for pair in [[status, STATUS_AT], [subtitle, SUBTITLE_AT]]:
		var at: Dictionary = pair[1]
		var p: Vector3 = drummer.origin + drummer.basis.x.normalized() * at.x \
			- drummer.basis.z.normalized() * at.f + Vector3.UP * at.y
		var l: Label3D = pair[0]
		l.global_position = p
		l.look_at(p + (p - eye).normalized(), Vector3.UP)   # Label3D shows its +Z face: face the eye


func start(song_title: String, total_bars: int) -> void:
	title = song_title
	bars = total_bars
	bar = 0
	trend_ms = NAN
	_trend_age = INF
	_subtitle_age = INF
	subtitle.text = ""
	visible = true
	_refresh()


func set_bar(b: int) -> void:
	if b != bar:
		bar = b
		_refresh()


## A judgment.note: hits (error_ms not null) move the running mean.
func note(d: Dictionary) -> void:
	var e = d.get("error_ms")
	if not typeof(e) in [TYPE_INT, TYPE_FLOAT]:
		return
	trend_ms = float(e) if is_nan(trend_ms) else lerpf(trend_ms, float(e), TREND_ALPHA)
	_trend_age = 0.0
	_refresh()


## "rushing", "dragging" or "" (in time, or nothing heard lately).
func trend() -> String:
	if is_nan(trend_ms) or _trend_age > TREND_HOLD_S:
		return ""
	if trend_ms <= -TREND_MS:
		return "rushing"
	if trend_ms >= TREND_MS:
		return "dragging"
	return ""


func say(text: String) -> void:
	subtitle.text = text
	_subtitle_age = 0.0


func _refresh() -> void:
	var line := title
	if bar > 0:
		line += "   bar %d" % bar + (" / %d" % bars if bars > 0 else "")
	var t := trend()
	if t == "rushing":
		line += "\n<< RUSHING  %+.0f ms" % trend_ms
	elif t == "dragging":
		line += "\nDRAGGING >>  %+.0f ms" % trend_ms
	status.text = line
	status.modulate = RED if t != "" else INK


func _process(delta: float) -> void:
	if not visible:
		return
	var had := trend()
	_trend_age += delta
	if trend() != had:
		_refresh()
	_subtitle_age += delta
	if _subtitle_age > SUBTITLE_HOLD_S and subtitle.text != "":
		subtitle.text = ""
