extends RefCounted
## Plans a walking route on the floor around the band and the furniture.
## Obstacles live in the XZ plane:
##   a circle around every seat_* marker (a seated player and their instrument),
##   the footprint box of every floor-standing piece listed in BOX_PREFIXES,
## and the walker keeps BODY_RADIUS from all of them (boxes get rounded corners, as a body
## sliding round them would). plan() runs A* over a visibility graph whose nodes sit just
## outside that clearance (around circles and box corners), so every leg of the route is a
## straight line that keeps the clearance. Loaded with preload() by its users.

## Half-width of a person walking, plus a little air.
const BODY_RADIUS := 0.3
## A seated player with their instrument, measured from the seat marker.
const SEAT_RADIUS := 0.55
## Floor-level room meshes that block walking (the band's chairs sit inside the seat circles,
## but spare chairs do not). Risers are left out: their tiers lie under the seat circles and
## their mesh bounds would also cover the drummer's corner.
const BOX_PREFIXES := ["chair_", "stand_", "stool_", "piano_", "guitar_amp", "bandleader_table",
	"instrument_case_", "percussion_closet", "folder_shelf"]
const CIRCLE_SIDES := 10
const CORNER_STEPS := 3       # graph nodes around each box corner's rounded quarter
const EPS := 0.02

var circles: Array = []           # [Vector2 centre, float grown radius]
var boxes: Array[Rect2] = []      # footprints; the walker keeps BODY_RADIUS from them
var bounds := Rect2()             # walkable floor inside the walls
var _free_nodes: Array[Vector2] = []
var _reach: Array[Rect2] = []     # per obstacle: the area a segment must touch to be blocked
var _edges := {}                  # Vector2i(node, node) -> blocked


## Builds a planner from the room glb's markers and meshes. Returns null (after push_error)
## when the room lacks seats or its shell.
static func from_room(room: Node3D) -> RefCounted:
	var planner: RefCounted = load("res://scripts/walk_planner.gd").new()
	var shell := room.find_child("room_shell", true, false) as Node3D
	if shell == null:
		push_error("WalkPlanner: room has no room_shell mesh to bound the floor")
		return null
	var shell_box := _footprint(shell)
	planner.bounds = shell_box.grow(-(BODY_RADIUS + 0.1))
	var seats := room.find_children("seat_*", "Node3D", true, false)
	if seats.is_empty():
		push_error("WalkPlanner: room has no seat_* markers")
		return null
	for s in seats:
		var p: Vector3 = (s as Node3D).global_position
		planner.circles.append([Vector2(p.x, p.z), SEAT_RADIUS + BODY_RADIUS])
	for n in room.find_children("*", "Node3D", true, false):
		var nm := String(n.name)
		if nm.begins_with("seat_") or not BOX_PREFIXES.any(func(pre): return nm.begins_with(pre)):
			continue
		var fp := _footprint(n)
		if fp.size == Vector2.ZERO:
			continue   # an empty marker, not furniture
		planner.boxes.append(fp)
	return planner


## XZ footprint of every MeshInstance3D at or under `n`, as a Rect2 (x, z).
static func _footprint(n: Node) -> Rect2:
	var out := Rect2()
	var first := true
	var meshes := n.find_children("*", "MeshInstance3D", true, false)
	if n is MeshInstance3D:
		meshes.append(n)
	for mi in meshes:
		var ab: AABB = (mi as MeshInstance3D).global_transform * (mi as MeshInstance3D).get_aabb()
		var r := Rect2(ab.position.x, ab.position.z, ab.size.x, ab.size.z)
		out = r if first else out.merge(r)
		first = false
	return out


## Waypoints from `from` to `to` (both included), at `from`'s height. Empty after push_error
## when no route exists. An obstacle that already contains an endpoint is ignored for the
## legs touching that endpoint, so the walker can step out of (or into) a tight spot.
func plan(from: Vector3, to: Vector3) -> PackedVector3Array:
	var a := Vector2(from.x, from.z)
	var b := Vector2(to.x, to.z)
	if not bounds.has_point(a) or not bounds.has_point(b):
		push_error("WalkPlanner: %s -> %s leaves the room" % [from, to])
		return PackedVector3Array()
	if _free_nodes.is_empty():
		_build_graph()
	var free: Array[Vector2] = [a, b]
	free.append_array(_free_nodes)
	var skip_a := _inside(a)
	var skip_b := _inside(b)
	# A* from node 0 to node 1
	var n := free.size()
	var g_cost := {0: 0.0}
	var came := {}
	var open := {0: true}
	var closed := {}
	while not open.is_empty():
		var cur := -1
		var best := INF
		for k in open:
			var f: float = g_cost[k] + free[k].distance_to(b)
			if f < best:
				best = f
				cur = k
		if cur == 1:
			var out := PackedVector3Array()
			var k := 1
			while true:
				out.insert(0, Vector3(free[k].x, from.y, free[k].y))
				if k == 0:
					break
				k = came[k]
			return out
		open.erase(cur)
		closed[cur] = true
		for nxt in n:
			if closed.has(nxt) or nxt == cur:
				continue
			var skip: Array = []
			if cur == 0 or nxt == 0:
				skip.append_array(skip_a)
			if cur == 1 or nxt == 1:
				skip.append_array(skip_b)
			if _edge_blocked(cur, nxt, free, skip):
				continue
			var cost: float = g_cost[cur] + free[cur].distance_to(free[nxt])
			if not g_cost.has(nxt) or cost < g_cost[nxt]:
				g_cost[nxt] = cost
				came[nxt] = cur
				open[nxt] = true
	push_error("WalkPlanner: no route from %s to %s" % [from, to])
	return PackedVector3Array()


## Graph nodes just outside every obstacle's clearance, kept where the floor is free.
## The obstacles never move, so this and the edges between these nodes are cached.
func _build_graph() -> void:
	var nodes: Array[Vector2] = []
	for c in circles:
		var r: float = (c[1] + EPS) / cos(PI / CIRCLE_SIDES)
		for i in CIRCLE_SIDES:
			var ang := TAU * i / CIRCLE_SIDES
			nodes.append(c[0] + Vector2(cos(ang), sin(ang)) * r)
	var corner_r := (BODY_RADIUS + EPS) / cos(PI / 4.0 / (CORNER_STEPS - 1))
	for box in boxes:
		var corners := [box.position, Vector2(box.end.x, box.position.y), box.end, Vector2(box.position.x, box.end.y)]
		for ci in 4:
			var start_ang := PI + ci * PI / 2.0   # outward quarter of this corner
			for k in CORNER_STEPS:
				var ang := start_ang + (PI / 2.0) * k / (CORNER_STEPS - 1)
				nodes.append(corners[ci] + Vector2(cos(ang), sin(ang)) * corner_r)
	for p in nodes:
		if bounds.has_point(p) and _inside(p).is_empty():
			_free_nodes.append(p)
	for c in circles:
		_reach.append(Rect2(c[0] - Vector2.ONE * c[1], Vector2.ONE * 2.0 * c[1]))
	for box in boxes:
		_reach.append(box.grow(BODY_RADIUS))


## Edges between two cached nodes (index >= 2 in plan's list) are looked up once.
func _edge_blocked(i: int, j: int, nodes: Array[Vector2], skip: Array) -> bool:
	if i < 2 or j < 2:
		return _blocked(nodes[i], nodes[j], skip)
	var key := Vector2i(min(i, j), max(i, j))
	if not _edges.has(key):
		_edges[key] = _blocked(nodes[i], nodes[j], skip)
	return _edges[key]


## Obstacle ids containing `p`: circles are 0..n-1, boxes follow.
func _inside(p: Vector2) -> Array:
	var out: Array = []
	for i in circles.size():
		if p.distance_to(circles[i][0]) < circles[i][1]:
			out.append(i)
	for i in boxes.size():
		if _point_box_dist(p, boxes[i]) < BODY_RADIUS:
			out.append(circles.size() + i)
	return out


func _blocked(p: Vector2, q: Vector2, skip: Array) -> bool:
	var seg := Rect2(p, Vector2.ZERO).expand(q)
	for i in circles.size():
		if i in skip or not seg.intersects(_reach[i], true):
			continue
		var c: Vector2 = circles[i][0]
		if Geometry2D.get_closest_point_to_segment(c, p, q).distance_to(c) < circles[i][1] - 1e-4:
			return true
	for i in boxes.size():
		if circles.size() + i in skip or not seg.intersects(_reach[circles.size() + i], true):
			continue
		if _segment_box_dist(p, q, boxes[i]) < BODY_RADIUS - 1e-4:
			return true
	return false


static func _point_box_dist(p: Vector2, r: Rect2) -> float:
	var c := Vector2(clamp(p.x, r.position.x, r.end.x), clamp(p.y, r.position.y, r.end.y))
	return p.distance_to(c)


static func _segment_box_dist(p: Vector2, q: Vector2, r: Rect2) -> float:
	if _segment_hits_box(p, q, r):
		return 0.0
	var d: float = min(_point_box_dist(p, r), _point_box_dist(q, r))
	for c in [r.position, Vector2(r.end.x, r.position.y), r.end, Vector2(r.position.x, r.end.y)]:
		d = min(d, Geometry2D.get_closest_point_to_segment(c, p, q).distance_to(c))
	return d


## Liang-Barsky: does the segment p-q pass through the inside of `r`?
static func _segment_hits_box(p: Vector2, q: Vector2, r: Rect2) -> bool:
	var d := q - p
	var t0 := 0.0
	var t1 := 1.0
	for axis in 2:
		var lo: float = r.position[axis]
		var hi: float = r.end[axis]
		if abs(d[axis]) < 1e-9:
			if p[axis] <= lo or p[axis] >= hi:
				return false
			continue
		var ta: float = (lo - p[axis]) / d[axis]
		var tb: float = (hi - p[axis]) / d[axis]
		if ta > tb:
			var tmp := ta
			ta = tb
			tb = tmp
		t0 = max(t0, ta)
		t1 = min(t1, tb)
		if t0 >= t1:
			return false
	return true
