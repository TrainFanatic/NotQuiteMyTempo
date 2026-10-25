extends Node3D
## A flat world-space panel you can point at: a SubViewport of Controls drawn onto a quad,
## sheet-music paper in the room's PS2 look (low resolution, nearest filtering). The game
## menu and the small "back to menu" card are both one of these.
## Pointing: hover_ray(origin, dir) from any pointer (the head's gaze in the headset, the
## mouse on the desktop), then either step(delta) dwells on the hovered item (dwell_s, a ring
## fills round the cursor) or click() picks it at once (a hand pinch, a mouse click).
## Items are Controls registered with add_button(); activated(id, arg) fires when one is picked.
## Loaded with preload() by its users, so no global class cache entry is needed.

signal activated(id: String, arg: Variant)

const PAPER := Color(0.93, 0.89, 0.78)
const PAPER_HOVER := Color(0.99, 0.93, 0.70)
const INK := Color(0.13, 0.11, 0.09)
const INK_SOFT := Color(0.40, 0.35, 0.29)
const RED := Color(0.62, 0.09, 0.06)        # Fletcher's red pencil
const RE_ARM_S := 0.6                       # after a pick, the dwell waits this long before counting again
## ...and until the pointer has moved this far (pixels, about 2 degrees of head turn) from
## where the pick happened: the next page's item that lands under a resting gaze (CANCEL
## where PLAY was) is never picked by the gaze just staying put.
const RE_ARM_PX := 24.0

## Seconds of steady gaze that pick an item.
@export var dwell_s := 1.5
## Size of the paper in metres.
@export var size_m := Vector2(0.8, 0.56)
## Its resolution: low on purpose (the room's textures are 128 to 256 px, nearest filtered).
@export var pixels := Vector2i(512, 358)

var viewport: SubViewport
var quad: MeshInstance3D
var root: Control           # the page, fills the viewport
var content: VBoxContainer  # what pages build into
var hovered := ""           # id of the item under the pointer, "" for none
var cursor := Vector2(-1, -1)   # pointer on the paper in pixels, (-1, -1) when off it
var dwell := 0.0            # seconds the pointer has rested on `hovered`
var _items := {}            # id -> {control, arg}
var _overlay: Control
var _re_arm := 0.0
var _pick_at := Vector2(-1, -1)  # where the last pick happened, until the pointer leaves it
var _styles := {}


func build() -> void:
	viewport = SubViewport.new()
	viewport.name = "Viewport"
	viewport.size = pixels
	viewport.disable_3d = true
	viewport.transparent_bg = false
	viewport.gui_disable_input = true          # input comes from hover_ray / click, not events
	viewport.render_target_update_mode = SubViewport.UPDATE_ALWAYS
	add_child(viewport)
	root = PanelContainer.new()
	root.name = "Page"
	root.theme = _theme()
	root.size = Vector2(pixels)
	root.add_theme_stylebox_override("panel", _box(PAPER, Color(0, 0, 0, 0), 0, 14))
	viewport.add_child(root)
	content = VBoxContainer.new()
	content.add_theme_constant_override("separation", 6)
	root.add_child(content)
	_overlay = Control.new()
	_overlay.name = "Cursor"
	_overlay.mouse_filter = Control.MOUSE_FILTER_IGNORE
	_overlay.size = Vector2(pixels)
	_overlay.draw.connect(_draw_cursor)
	viewport.add_child(_overlay)
	quad = MeshInstance3D.new()
	quad.name = "Paper"
	var qm := QuadMesh.new()
	qm.size = size_m
	quad.mesh = qm
	var mat := StandardMaterial3D.new()
	mat.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED   # lit by its own lamp: always readable
	mat.albedo_texture = viewport.get_texture()
	mat.albedo_color = Color(0.92, 0.88, 0.80)
	mat.texture_filter = BaseMaterial3D.TEXTURE_FILTER_NEAREST
	quad.material_override = mat
	quad.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	add_child(quad)


func _theme() -> Theme:
	var t := Theme.new()
	t.default_font_size = 20
	t.set_color("font_color", "Label", INK)
	_styles["idle"] = _box(Color(0, 0, 0, 0), INK_SOFT, 2, 6)
	_styles["hover"] = _box(PAPER_HOVER, RED, 3, 6)
	_styles["selected"] = _box(Color(0.86, 0.80, 0.66), INK, 2, 6)
	return t


static func _box(bg: Color, border: Color, width: int, margin: int) -> StyleBoxFlat:
	var b := StyleBoxFlat.new()
	b.bg_color = bg
	b.border_color = border
	b.set_border_width_all(width)
	b.set_content_margin_all(margin)
	b.anti_aliasing = false
	return b


## ---- building pages ------------------------------------------------------

func clear() -> void:
	for c in content.get_children():
		content.remove_child(c)
		c.queue_free()
	_items.clear()
	hovered = ""
	dwell = 0.0


func label(text: String, size := 20, color := INK, parent: Control = null) -> Label:
	var l := Label.new()
	l.text = text
	l.add_theme_font_size_override("font_size", size)
	l.add_theme_color_override("font_color", color)
	(parent if parent != null else content).add_child(l)
	return l


func row(parent: Control = null) -> HBoxContainer:
	var h := HBoxContainer.new()
	h.add_theme_constant_override("separation", 8)
	(parent if parent != null else content).add_child(h)
	return h


## A pickable item: a framed label. `selected` draws it pressed (the chosen song, the mic).
func add_button(text: String, id: String, arg: Variant = null, parent: Control = null,
		expand := false, selected := false) -> PanelContainer:
	var p := PanelContainer.new()
	p.name = "Item_" + id.validate_node_name()
	p.add_theme_stylebox_override("panel", _styles["selected" if selected else "idle"])
	if expand:
		p.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	var l := Label.new()
	l.text = text
	l.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER if not expand else HORIZONTAL_ALIGNMENT_LEFT
	if expand:   # long titles end in "..." instead of pushing the page off the paper
		l.clip_text = true
		l.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
		l.custom_minimum_size = Vector2(40, 0)
	p.add_child(l)
	(parent if parent != null else content).add_child(p)
	_items[id] = {"control": p, "arg": arg, "selected": selected}
	return p


func has_item(id: String) -> bool:
	return _items.has(id)


func item_ids() -> Array:
	return _items.keys()


func item_text(id: String) -> String:
	if not _items.has(id):
		return ""
	return (_items[id]["control"].get_child(0) as Label).text


## The item's rectangle on the paper, in pixels (valid once the page has been laid out).
func item_rect(id: String) -> Rect2:
	return (_items[id]["control"] as Control).get_global_rect() if _items.has(id) else Rect2()


## The world position of an item's centre (tests, and the movie's scripted gaze).
func item_world(id: String) -> Vector3:
	var c := item_rect(id).get_center()
	var uv := c / Vector2(pixels)
	return global_transform * Vector3((uv.x - 0.5) * size_m.x, (0.5 - uv.y) * size_m.y, 0.0)


## ---- pointing ------------------------------------------------------------

## Where a ray meets the paper, in pixels, or (-1, -1). Only from the front.
func ray_pixel(origin: Vector3, dir: Vector3) -> Vector2:
	if not is_visible_in_tree():
		return Vector2(-1, -1)
	var inv := global_transform.affine_inverse()
	var o := inv * origin
	var d := inv.basis * dir
	if d.z >= -1e-6 or o.z <= 0.0:
		return Vector2(-1, -1)
	var p := o + d * (-o.z / d.z)
	var uv := Vector2(p.x / size_m.x + 0.5, 0.5 - p.y / size_m.y)
	if uv.x < 0.0 or uv.x > 1.0 or uv.y < 0.0 or uv.y > 1.0:
		return Vector2(-1, -1)
	return uv * Vector2(pixels)


## Distance along the ray to the paper, or INF (to pick the nearest of several panels).
func ray_distance(origin: Vector3, dir: Vector3) -> float:
	if ray_pixel(origin, dir).x < 0.0:
		return INF
	var n := global_transform.basis.z.normalized()
	return (global_position - origin).dot(n) / dir.normalized().dot(n)


func hover_ray(origin: Vector3, dir: Vector3) -> bool:
	hover_pixel(ray_pixel(origin, dir))
	return cursor.x >= 0.0


func hover_pixel(px: Vector2) -> void:
	cursor = px
	var id := ""
	if px.x >= 0.0:
		for k in _items:
			if item_rect(k).has_point(px):
				id = k
				break
	if id != hovered:
		_set_style(hovered, false)
		hovered = id
		dwell = 0.0
		_set_style(hovered, true)
	_overlay.queue_redraw()


func unhover() -> void:
	hover_pixel(Vector2(-1, -1))


## Dwell: called every frame while gaze picking is on.
func step(delta: float) -> void:
	_re_arm = maxf(0.0, _re_arm - delta)
	if _pick_at.x >= 0.0 and (cursor.x < 0.0 or cursor.distance_to(_pick_at) > RE_ARM_PX):
		_pick_at = Vector2(-1, -1)
	if hovered == "" or _re_arm > 0.0 or _pick_at.x >= 0.0:
		dwell = 0.0
	else:
		dwell += delta
		if dwell >= dwell_s:
			_pick(hovered)
	_overlay.queue_redraw()


## Pick the hovered item now (pinch, mouse click). False when nothing is under the pointer.
func click() -> bool:
	if hovered == "":
		return false
	_pick(hovered)
	return true


## Pick an item by id, as if pointed at (the laptop console's path goes through hub messages,
## this is for tests and scripted runs).
func pick(id: String) -> bool:
	if not _items.has(id):
		push_error("GamePanel: no item %s on this page (has %s)" % [id, _items.keys()])
		return false
	_pick(id)
	return true


func _pick(id: String) -> void:
	var arg = _items[id]["arg"]
	dwell = 0.0
	_re_arm = RE_ARM_S
	_pick_at = cursor
	activated.emit(id, arg)


func _set_style(id: String, hover: bool) -> void:
	if id == "" or not _items.has(id):
		return
	var it: Dictionary = _items[id]
	var key := "hover" if hover else ("selected" if it["selected"] else "idle")
	(it["control"] as PanelContainer).add_theme_stylebox_override("panel", _styles[key])


func _draw_cursor() -> void:
	if cursor.x < 0.0:
		return
	_overlay.draw_circle(cursor, 3.0, RED)
	if hovered != "" and dwell > 0.0:
		var f := clampf(dwell / dwell_s, 0.0, 1.0)
		_overlay.draw_arc(cursor, 13.0, 0.0, TAU, 32, Color(INK_SOFT, 0.35), 2.0)
		_overlay.draw_arc(cursor, 13.0, -PI / 2.0, -PI / 2.0 + TAU * f, 32, RED, 4.0)
