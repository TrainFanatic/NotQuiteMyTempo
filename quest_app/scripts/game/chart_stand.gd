extends Node3D
## The drum chart on the music stand during a song: the same stand and the same sheet of paper
## as the menu (GameMenu), which is hidden while a song runs. A GamePanel (low-res SubViewport,
## nearest filtered) drawn with a DrumChart (scripts/game/drum_chart.gd):
##   header   the song, the bar (of the song's bars), and Fletcher's restart bar
##   page     four bars in two systems, one line per lane (CR RD HH SN TM BD; cymbals as x,
##            snare and toms as squares, kick as dots). Each slot keeps its bar until the
##            playhead has passed it (DrumChart.slots), so the page never jumps under the eye
##   now      the current bar is shaded and a red playhead follows the song time
##   restart  the bar Fletcher sends the band back to is boxed in red pencil
## GameSession feeds it: set_chart(), set_time() every frame from the backing's clock.
## Loaded with preload() by its users.

const GamePanel := preload("res://scripts/game/game_panel.gd")
const DrumChart := preload("res://scripts/game/drum_chart.gd")
const SLOTS := 4                         # bars on the page
const PER_ROW := 2                       # bars per system
const LABEL_W := 24.0                    # px, the lane names down the left
const HEADER_H := 22.0
const PAD := 6.0                         # px from a bar line to the first and last note
const NOW_SHADE := Color(0.99, 0.93, 0.70)

var panel: GamePanel
var view: Control
var chart: DrumChart                     # null: no chart (the text says why)
var title := ""
var current := 1                         # bar under the playhead
var playhead := -1.0                     # 0..1 through `current`, < 0 hides it
var restart_bar := 0                     # Fletcher's restart bar, 0 for none
var note := ""                           # shown instead of the page when there is no chart


## `like`: the GameMenu whose stand and paper this one shares (built and placed).
func build(like: Node3D) -> void:
	name = "ChartStand"
	panel = GamePanel.new()
	panel.name = "Panel"
	panel.size_m = like.panel.size_m
	panel.pixels = like.panel.pixels
	add_child(panel)
	panel.build()
	view = Control.new()
	view.name = "Chart"
	view.size_flags_vertical = Control.SIZE_EXPAND_FILL
	view.draw.connect(_draw_chart)
	panel.content.add_child(view)
	var stand := like.get_node("Stand").duplicate() as Node3D
	add_child(stand)
	place_like(like)


## Same spot as the menu: one stand, one sheet.
func place_like(like: Node3D) -> void:
	global_transform = like.global_transform
	panel.transform = like.panel.transform
	var mine := get_node_or_null("Stand") as Node3D
	var theirs := like.get_node("Stand") as Node3D
	for c in theirs.get_children():
		var m := mine.get_node(NodePath(c.name)) as Node3D
		m.transform = (c as Node3D).transform


func set_chart(c: DrumChart, song_title: String, why_none := "") -> void:
	chart = c
	title = song_title
	note = why_none if c == null else ""
	current = 1
	playhead = -1.0
	view.queue_redraw()


## The song time now; `running` false keeps the playhead hidden (count-in, after a cutoff).
func set_time(t: float, running: bool) -> void:
	if chart == null:
		return
	current = chart.bar_at(t)
	playhead = chart.fraction(t) if running else -1.0
	view.queue_redraw()


## The bar on the page before the song time reaches it (a count-in to `bar`).
func show_bar(bar: int) -> void:
	current = maxi(1, bar)
	playhead = -1.0
	view.queue_redraw()


func set_restart_bar(bar: int) -> void:
	restart_bar = bar
	view.queue_redraw()


## Where bar `slot` (0..SLOTS-1) is drawn in the chart Control: Rect2 in its pixels.
func slot_rect(slot: int, area: Vector2) -> Rect2:
	var rows := ceili(SLOTS / float(PER_ROW))
	var row_h := (area.y - HEADER_H) / rows
	var w := (area.x - LABEL_W) / PER_ROW
	return Rect2(LABEL_W + (slot % PER_ROW) * w, HEADER_H + (slot / PER_ROW) * row_h, w, row_h)


## The playhead's x in the chart Control, or -1 when hidden (tests).
func playhead_x() -> float:
	if chart == null or playhead < 0.0:
		return -1.0
	var r := slot_rect(DrumChart.slots(current, SLOTS).find(current), view.size)
	return r.position.x + PAD + (r.size.x - PAD * 2.0) * playhead


func _draw_chart() -> void:
	var font := ThemeDB.fallback_font
	var w := view.size.x
	var head := title
	if chart != null:
		head += "   bar %d/%d" % [current, chart.bar_count()]
	view.draw_string(font, Vector2(0, 16), head, HORIZONTAL_ALIGNMENT_LEFT, w - 120, 17, GamePanel.INK)
	if restart_bar > 0:
		view.draw_string(font, Vector2(w - 118, 16), "restart: bar %d" % restart_bar,
			HORIZONTAL_ALIGNMENT_RIGHT, 118, 15, GamePanel.RED)
	if chart == null:
		view.draw_string(font, Vector2(0, HEADER_H + 30), note, HORIZONTAL_ALIGNMENT_LEFT, w, 17, GamePanel.INK_SOFT)
		return
	var bars := DrumChart.slots(current, SLOTS)
	for i in bars.size():
		_draw_bar(font, bars[i], slot_rect(i, view.size), i % PER_ROW == 0)


func _draw_bar(font: Font, bar: int, r: Rect2, first_in_row: bool) -> void:
	var lanes := DrumChart.LANES.size()
	var top := r.position.y + 14.0       # room for the bar number
	var lane_h := (r.size.y - 18.0) / lanes
	var y_of := func(lane: int) -> float: return top + lane_h * (lane + 0.5)
	if bar > chart.bar_count():
		return   # past the end: blank paper
	if bar == current:
		view.draw_rect(Rect2(r.position.x, top, r.size.x, lane_h * lanes), NOW_SHADE)
	if first_in_row:
		for l in lanes:
			view.draw_string(font, Vector2(0, y_of.call(l) + 5), DrumChart.LABELS[l],
				HORIZONTAL_ALIGNMENT_LEFT, LABEL_W, 12, GamePanel.INK_SOFT)
	for l in lanes:   # the staff: one ruled line per lane
		var y: float = y_of.call(l)
		view.draw_line(Vector2(r.position.x, y), Vector2(r.end.x, y), Color(GamePanel.INK_SOFT, 0.45), 1.0)
	var beats := chart.beats(bar)
	for b in range(1, beats):   # beat ticks
		var x := r.position.x + r.size.x * b / beats
		view.draw_line(Vector2(x, top + 2), Vector2(x, top + lane_h * lanes - 2), Color(GamePanel.INK_SOFT, 0.3), 1.0)
	view.draw_line(Vector2(r.end.x - 1, top), Vector2(r.end.x - 1, top + lane_h * lanes), GamePanel.INK, 2.0)
	if first_in_row:
		view.draw_line(Vector2(r.position.x, top), Vector2(r.position.x, top + lane_h * lanes), GamePanel.INK, 2.0)
	view.draw_string(font, Vector2(r.position.x + 3, r.position.y + 12), str(bar),
		HORIZONTAL_ALIGNMENT_LEFT, -1, 12, GamePanel.INK)
	for h in chart.hits_in(bar):
		var x: float = r.position.x + PAD + (r.size.x - PAD * 2.0) * h[0]
		var y: float = y_of.call(h[1])
		var s := minf(4.0, lane_h * 0.32)
		match int(h[1]):
			0, 1, 2:   # cymbals: x
				view.draw_line(Vector2(x - s, y - s), Vector2(x + s, y + s), GamePanel.INK, 2.0)
				view.draw_line(Vector2(x - s, y + s), Vector2(x + s, y - s), GamePanel.INK, 2.0)
			5:         # kick: a dot
				view.draw_circle(Vector2(x, y), s + 0.5, GamePanel.INK)
			_:         # snare, toms: a square head
				view.draw_rect(Rect2(x - s, y - s, s * 2.0, s * 2.0), GamePanel.INK)
	if bar == restart_bar:   # Fletcher's red pencil round the bar he restarts from
		view.draw_rect(Rect2(r.position.x + 1, top - 13, r.size.x - 3, lane_h * lanes + 14), GamePanel.RED, false, 2.0)
		view.draw_string(font, Vector2(r.position.x + 22, r.position.y + 12), "from here",
			HORIZONTAL_ALIGNMENT_LEFT, -1, 12, GamePanel.RED)
	if bar == current and playhead >= 0.0:
		var px := r.position.x + PAD + (r.size.x - PAD * 2.0) * playhead
		view.draw_line(Vector2(px, top - 2), Vector2(px, top + lane_h * lanes + 2), GamePanel.RED, 2.0)
