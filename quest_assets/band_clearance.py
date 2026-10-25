"""Clearance check for the band animations. Used by build_band.py for every member.

Samples every action of a posed band rig (every STEP frames plus each action's last frame)
and measures how deep things sink into each other, in metres:

    inst>PART     instrument points (mesh vertices, edge midpoints, face centres) inside a body part
    PART>inst     deformed body vertices inside the instrument mesh
    palm>body     palm vertices inside the torso, thighs, shins, head or the other palm
    fingers>body  finger vertices inside the same
    forearm>body  forearm vertices inside the torso, thighs or head
    body>seat     body vertices and edge midpoints inside the seat (backrest, stool, bench, keys alike)
    seat>body     points on the seat's surface inside a body part (catches limbs passing between rings)
    inst>seat     instrument points inside the seat (backrest, stool, bench, keys alike)

Body parts are convex hulls of each bone's rest-pose vertices (the vertices whose strongest
weight is that bone), carried by the bone's pose deformation. Instruments are tested as solids
through the nearest face normal of their mesh. Everything is in the character frame (faces -Y,
origin at the chair centre on the floor); the rig must sit at the world origin, unrotated.
"""

import bmesh
import bpy
import numpy as np
from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree

import lib_ps2 as ps2

STEP = 2
# Room chair (build_room.build_chair), turned into the character frame: the seat box is
# 0.46 x 0.44 x 0.07 under the origin, the backrest leans back behind the player.
SEAT_BOX = (Vector((0, 0, ps2.SEAT_TOP - 0.035)), Vector((0.23, 0.22, 0.035)), Matrix.Identity(3))
BACK_BOX = (Vector((0, 0.21, 0.76)), Vector((0.22, 0.03, 0.16)), Matrix.Rotation(np.radians(-8.0), 3, "X"))
STOOL_BOX = (Vector((0, 0, 0.74)), Vector((0.18, 0.18, 0.02)), Matrix.Identity(3))   # bassist's stool seat
# Piano bench (build_room.build_bench) and the keyboard 0.62 m ahead (build_grand_piano), character frame
BENCH_BOX = (Vector((0, 0, 0.48)), Vector((0.40, 0.17, 0.03)), Matrix.Identity(3))
KEYS_BOX = (Vector((0, -0.50, 0.70)), Vector((0.71, 0.12, 0.03)), Matrix.Identity(3))
FURNITURE = {"chair": (("seat", SEAT_BOX), ("backrest", BACK_BOX)), "stool": (("stool", STOOL_BOX),),
             "piano": (("bench", BENCH_BOX), ("keys", KEYS_BOX))}
HULL_WEIGHT = 0.25          # a vertex joins a bone's hull at this weight, so neighbouring hulls overlap
HIP_BLEND = 0.12            # seat points this close to a hip joint are left to the vertex test (the
                            # hulls there are larger than the blended, seated mesh)
REPORT_CM = 1.0             # per-member lines list checks deeper than this
# check -> max metres; the build fails above these. The stool checks are left out on purpose:
# the room's bass stool stands where the standing bassist stands (see DECISIONS.md).
LIMITS = {
    **{f"inst>{p}": 0.02 for p in ("thigh", "shin", "torso", "head", "upperarm", "forearm")},
    **{f"{p}>inst": 0.02 for p in ("thigh", "shin", "torso", "head", "upperarm", "forearm", "palm", "fingers")},
    "palm>body": 0.02, "fingers>body": 0.02, "forearm>body": 0.025,
    **{f"{a}>{b}": 0.01 for n in ("seat", "backrest", "bench", "keys") for a, b in (("body", n), (n, "body"))},
    **{f"inst>{n}": 0.01 for n in ("seat", "backrest", "bench", "keys")},
}
WIND_MOUTH_EXEMPT = 0.06    # instrument points this close to a wind's mouthpiece tip may touch the face

PARTS = {   # check name -> bones whose rest vertices make one hull each
    "thigh": ("LeftUpperLeg", "RightUpperLeg"),
    "shin": ("LeftLowerLeg", "RightLowerLeg"),
    "torso": ("Hips", "Spine", "Chest", "UpperChest"),
    "head": ("Neck", "Head"),
    "upperarm": ("LeftUpperArm", "RightUpperArm"),
    "forearm": ("LeftLowerArm", "RightLowerArm"),
    "palm": ("LeftHand", "RightHand"),
}


def _finger(name):
    return any(k in name for k in ("Thumb", "Index", "Middle", "Ring", "Little"))


def read_shape(path):
    """Instrument glb -> {"body": (verts, polys), "slide": (verts, polys) or absent} in the root frame."""
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=str(path))
    new = [o for o in bpy.data.objects if o not in before]
    roots = [o for o in new if o.parent is None and o.type == "MESH"]
    if len(roots) != 1:
        raise RuntimeError(f"{path}: expected one mesh root, got {[o.name for o in roots]}")
    inv = roots[0].matrix_world.inverted()
    out = {}
    for o in new:
        if o.type != "MESH":
            continue
        key = "slide" if o.name.split(".")[0].endswith("_slide") else "body"
        if key in out:
            raise RuntimeError(f"{path}: two {key} meshes")
        m = inv @ o.matrix_world
        out[key] = ([m @ v.co for v in o.data.vertices], [tuple(p.vertices) for p in o.data.polygons])
    for o in new:
        bpy.data.objects.remove(o, do_unlink=True)
    if "body" not in out:
        raise RuntimeError(f"{path}: no instrument mesh")
    return out


def _samples(verts, polys):
    """Vertices, edge midpoints and face centres, so big flat faces are covered too."""
    pts = [Vector(v) for v in verts]
    edges = set()
    for p in polys:
        pts.append(sum((Vector(verts[i]) for i in p), Vector()) / len(p))
        for a, b in zip(p, p[1:] + p[:1]):
            edges.add((min(a, b), max(a, b)))
    pts += [(Vector(verts[a]) + Vector(verts[b])) / 2 for a, b in edges]
    return np.array([tuple(p) for p in pts])


def _hull(points):
    """Convex hull planes (normals, offsets) of an (N,3) array; inside means all n.p - d < 0."""
    ext = points.max(axis=0) - points.min(axis=0)
    if ext.min() < 0.01:
        raise RuntimeError(f"flat hull, extent {ext}")
    bm = bmesh.new()
    for p in points:
        bm.verts.new(p)
    bmesh.ops.convex_hull(bm, input=bm.verts[:])   # only the hull's faces are used below
    centre = Vector(points.mean(axis=0))
    ns, ds = [], []
    for f in bm.faces:
        f.normal_update()
        n = f.normal.copy()
        c = f.calc_center_median()
        if n.length < 1e-9:
            continue
        if n.dot(c - centre) < 0:
            n = -n
        ns.append(tuple(n))
        ds.append(n.dot(c))
    bm.free()
    if len(ns) < 4:
        raise RuntimeError("degenerate hull")
    return np.array(ns), np.array(ds)


def _depth_in_hull(P, hull):
    """Penetration depth of each point (0 outside)."""
    n, d = hull
    s = P @ n.T - d
    return np.clip(-s.max(axis=1), 0.0, None)


def _depth_in_box(P, box):
    c, half, rot = box
    R = np.array(rot)
    L = (P - np.array(c)) @ R          # row vectors: local = R^T (p - c)
    m = np.array(half) - np.abs(L)
    return np.clip(m.min(axis=1), 0.0, None)


RAYS = [Vector(v) for v in ((1, 0.013, 0.007), (-1, 0.011, -0.009), (0.008, 1, 0.012), (-0.006, -1, 0.01),
                            (0.012, -0.007, 1), (0.009, 0.006, -1))]


def _inside(bvh, p):
    """Ray parity in six directions, majority vote (the instrument meshes have open tube ends)."""
    odd = 0
    for d in RAYS:
        o, hits = p.copy(), 0
        while hits < 40:
            loc = bvh.ray_cast(o, d)[0]
            if loc is None:
                break
            hits += 1
            o = loc + d * 1e-5
        odd += hits % 2
    return odd >= 4


def _box_points(box, step=0.02):
    """Grid of points over a furniture box's surface, character frame."""
    c, half, rot = box
    h = np.array(half)
    axes = [np.linspace(-h[i], h[i], max(2, int(2 * h[i] / step) + 1)) for i in range(3)]
    g = np.stack(np.meshgrid(*axes, indexing="ij"), -1).reshape(-1, 3)
    on = np.any(np.isclose(np.abs(g), h), axis=1)
    return g[on] @ np.array(rot).T + np.array(c)


def _xform(M, P):
    A = np.array(M)
    return P @ A[:3, :3].T + A[:3, 3]


class Clearance:
    """Clearance check for one built member (rig + skinned mesh) and its instrument shape."""

    def __init__(self, rig, mesh_ob, shape, wind, furniture):
        if rig.matrix_world != Matrix() or mesh_ob.matrix_world != Matrix():
            raise RuntimeError(f"{rig.name}: clearance needs the rig at the origin, unrotated")
        self.rig, self.mesh = rig, mesh_ob
        self.wind = wind
        self.furniture_names = [n for n, _ in FURNITURE[furniture]]
        self.furniture = [b for _, b in FURNITURE[furniture]]
        names = [g.name for g in mesh_ob.vertex_groups]
        dom, member = [], {}
        for v in mesh_ob.data.vertices:
            if not v.groups:
                raise RuntimeError(f"{mesh_ob.name}: unweighted vertex {v.index}")
            g = max(v.groups, key=lambda g: g.weight)
            dom.append(names[g.group])
            for gg in v.groups:
                if gg.weight >= HULL_WEIGHT:
                    member.setdefault(names[gg.group], []).append(v.index)
        self.dom = np.array(dom)
        rest = np.array([tuple(v.co) for v in mesh_ob.data.vertices])
        self.hulls = {}
        for part, bones in PARTS.items():
            for b in bones:
                sel = np.array(member.get(b, []), dtype=int)
                if len(sel) < 8:
                    raise RuntimeError(f"{mesh_ob.name}: only {len(sel)} vertices on {b}")
                self.hulls[b] = (part, _hull(rest[sel]))
        side = np.array(["Left" if d.startswith("Left") else ("Right" if d.startswith("Right") else "") for d in dom])
        self.side = side
        self.sel = {
            "palm": np.isin(self.dom, ("LeftHand", "RightHand")),
            "fingers": np.array([_finger(d) for d in dom]),
            "forearm": np.isin(self.dom, PARTS["forearm"]),
        }
        for part in ("thigh", "shin", "torso", "head", "upperarm"):
            self.sel[part] = np.isin(self.dom, PARTS[part])
        self.feet = np.array(["Foot" in d or "Toes" in d for d in dom])
        ed = np.array([tuple(e.vertices) for e in mesh_ob.data.edges])
        self.edges = ed[~(self.feet[ed[:, 0]] | self.feet[ed[:, 1]])]
        self.furniture_pts = [_box_points(b) for b in self.furniture]
        self.shape = None
        if shape is not None:
            self.shape = {}
            for key, (verts, polys) in shape.items():
                self.shape[key] = (_samples(verts, polys), BVHTree.FromPolygons([tuple(v) for v in verts], polys))
            lo = np.min([s[0].min(axis=0) for s in self.shape.values()], axis=0)
            hi = np.max([s[0].max(axis=0) for s in self.shape.values()], axis=0)
            self.box = (lo - 0.02, hi + 0.02)

    # ------------------------------------------------------------------
    def _deformed(self):
        deps = bpy.context.evaluated_depsgraph_get()
        ev = self.mesh.evaluated_get(deps)
        me = ev.to_mesh()
        co = np.empty(len(me.vertices) * 3)
        me.vertices.foreach_get("co", co)
        ev.to_mesh_clear()
        co = co.reshape(-1, 3)
        if len(co) != len(self.dom):
            raise RuntimeError(f"{self.mesh.name}: evaluated mesh has {len(co)} vertices, rest {len(self.dom)}")
        return co

    def seat_gap(self, under_hips=False):
        """Lowest body point over the seat (or stool, bench) minus its top: < 0 means sunk in.
        under_hips limits it to the strip under the hip joints."""
        c, half, _ = self.furniture[0]
        ed = self.edges
        V = self._deformed()
        V = np.concatenate([V[~self.feet], (V[ed[:, 0]] + V[ed[:, 1]]) / 2])
        over = np.all(np.abs(V[:, :2] - np.array(c)[:2]) < np.array(half)[:2], axis=1)
        if under_hips:
            hip_y = 0.5 * (self.rig.pose.bones["LeftUpperLeg"].head.y + self.rig.pose.bones["RightUpperLeg"].head.y)
            over &= np.abs(V[:, 1] - hip_y) < 0.07
        if not over.any():
            raise RuntimeError(f"{self.rig.name}: nothing over the seat")
        return float(V[over, 2].min() - (c.z + half.z))

    def thigh_top(self, sd, xy):
        """Top of the deformed thigh of side sd around (x, y) (the loft rings are ~0.18 m apart)."""
        V = self._deformed()
        sel = (self.dom == f"{sd}UpperLeg") | (self.dom == f"{sd}LowerLeg")
        near = sel & (np.abs(V[:, 0] - xy[0]) < 0.07) & (np.abs(V[:, 1] - xy[1]) < 0.10)
        if near.sum() < 2:
            raise RuntimeError(f"{self.rig.name}: no {sd} thigh vertices near {xy}")
        return float(V[near, 2].max())

    def _part_depths(self, P, parts, skip_side=None):
        """Max depth per part of points P against the hulls of the given parts."""
        out = {}
        for bone, (part, hull) in self.hulls.items():
            if part not in parts:
                continue
            if skip_side and bone.startswith(skip_side):
                continue
            pb = self.rig.pose.bones[bone]
            D = (pb.matrix @ pb.bone.matrix_local.inverted()).inverted()
            dep = _depth_in_hull(_xform(D, P), hull)
            if dep.size and float(dep.max()) > out.get(part, 0.0):
                out[part] = float(dep.max())
                self.where[part] = bone
        return out

    def _in_instrument(self, P, key):
        """Depth of points P (character frame) inside instrument piece key (0 outside)."""
        pts, bvh = self.shape[key]
        bone = "Instrument" if key == "body" else "InstrumentSlide"
        M = self.rig.pose.bones[bone].matrix
        L = _xform(M.inverted(), P)
        lo, hi = self.box
        near = np.all((L > lo) & (L < hi), axis=1)
        dep = np.zeros(len(P))
        for i in np.nonzero(near)[0]:
            p = Vector(L[i])
            if _inside(bvh, p):
                dep[i] = bvh.find_nearest(p)[3]
        return dep

    def measure(self):
        """All checks at the current pose -> {check: depth}."""
        V = self._deformed()
        res = {}
        self.where = {}
        self.explain = {}     # check -> the body bone or instrument piece it was measured against

        def put(k, v, where=""):
            if v > res.get(k, -1.0):
                res[k] = v
                self.explain[k] = where

        # hands and forearms against the body
        for sd, other in (("Left", "Right"), ("Right", "Left")):
            mine = self.side == sd
            for grp, label in (("palm", "palm>body"), ("fingers", "fingers>body"), ("forearm", "forearm>body")):
                P = V[self.sel[grp] & mine]
                parts = ("torso", "thigh", "shin", "head") + (("palm",) if grp != "forearm" else ())
                d = self._part_depths(P, parts, skip_side=sd)
                for part, v in d.items():
                    put(label, v, f"{sd} into {self.where[part]}")
                put(label, 0.0)
        # body against the furniture both ways (feet may sit under the chair edge on the floor)
        body = np.concatenate([V[~self.feet], (V[self.edges[:, 0]] + V[self.edges[:, 1]]) / 2])
        for nm, box, pts in zip(self.furniture_names, self.furniture, self.furniture_pts):
            dep = _depth_in_box(body, box)
            put(f"body>{nm}", float(dep.max()) if dep.size else 0.0)
            hips = [np.array(self.rig.pose.bones[f"{sd}UpperLeg"].head) for sd in ("Left", "Right")]
            far = np.min([np.linalg.norm(pts - h, axis=1) for h in hips], axis=0) > HIP_BLEND
            d = self._part_depths(pts[far], ("thigh", "shin", "torso", "upperarm", "forearm", "palm", "head"))
            put(f"{nm}>body", max(d.values(), default=0.0))
        if self.shape is None:
            return res
        # instrument against the body and furniture
        for key, (pts, _) in self.shape.items():
            bone = "Instrument" if key == "body" else "InstrumentSlide"
            M = self.rig.pose.bones[bone].matrix
            P = _xform(M, pts)
            if self.wind:
                Pk = P[np.linalg.norm(pts, axis=1) > WIND_MOUTH_EXEMPT]
            else:
                Pk = P
            for part, d in self._part_depths(Pk, ("thigh", "shin", "torso", "upperarm", "forearm")).items():
                put(f"inst>{part}", d, f"{key} into {self.where[part]}")
            for part, d in self._part_depths(Pk if self.wind else P, ("head",)).items():
                put("inst>head", d, f"{key} into {self.where[part]}")
            for nm, box in zip(self.furniture_names, self.furniture):
                dep = _depth_in_box(P, box)
                put(f"inst>{nm}", float(dep.max()) if dep.size else 0.0)
            # body vertices inside the instrument
            for part in ("thigh", "shin", "torso", "head", "upperarm", "forearm", "palm", "fingers"):
                sel = self.sel[part]
                P2 = V[sel]
                if self.wind and part == "head":
                    tip = np.array(self.rig.pose.bones["Instrument"].matrix.translation)
                    P2 = P2[np.linalg.norm(P2 - tip, axis=1) > WIND_MOUTH_EXEMPT]
                dep = self._in_instrument(P2, key)
                if dep.size:
                    k = int(dep.argmax())
                    put(f"{part}>inst", float(dep[k]), f"{self.side[self.sel[part]][k] if part not in ('torso', 'head') else ''} into {key}")
                else:
                    put(f"{part}>inst", 0.0)
        return res

    def sample_actions(self, actions):
        """{check: (depth, action, frame)} worst over every action, sampled every STEP frames."""
        rig = self.rig
        if rig.animation_data is None:
            rig.animation_data_create()
        sc = bpy.context.scene
        worst = {}
        self.per_action = {}
        for act in actions:
            rig.animation_data.action = act
            if rig.animation_data.action_slot is None and act.slots:
                rig.animation_data.action_slot = act.slots[0]
            f0, f1 = (int(round(x)) for x in act.frame_range)
            frames = sorted(set(range(f0, f1 + 1, STEP)) | {f1})
            for f in frames:
                sc.frame_set(f)
                for k, v in self.measure().items():
                    if k not in worst or v > worst[k][0]:
                        worst[k] = (v, act.name, f)
                    pa = self.per_action.setdefault(act.name, {})
                    if k not in pa or v > pa[k][0]:
                        pa[k] = (v, f, self.explain.get(k, ""))
        rig.animation_data.action = None
        ps2.reset_pose(rig)
        return worst


def over_limits(rows):
    """[(member, worst)] -> descriptions of every check deeper than its LIMITS entry."""
    out = []
    for name, worst in rows:
        for k, (d, act, fr) in worst.items():
            if k in LIMITS and d > LIMITS[k]:
                out.append(f"{name} {k} {d * 100:.1f} cm > {LIMITS[k] * 100:.1f} ({act}@{fr})")
    return out
