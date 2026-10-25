"""Shared helpers for the PS2-style asset scripts. Import inside Blender:

    import sys; sys.path.insert(0, str(Path(__file__).resolve().parent)); import lib_ps2 as ps2

Contents
    textures   grid/noise/brushed/wood/plaid/stripes arrays, save_image, material
    Builder    bmesh builder: box, prism, cylinder, quad, loft (explicit UVs) + projected UVs
    humanoid   build_humanoid(): lofted low-poly body with finger hands, humanoid weights,
               humanoid_bones(): Godot SkeletonProfileHumanoid names incl. fingers
    rig        make_armature, skin_mesh
    animation  reset_pose, aim, turn, apply_pose, make_action
    output     export_glb, add_camera, render_frames, compose_sheet (with a tiny label font)
    room       append_room

Conventions: Z up, metres, characters face -Y with their left on +X, T-pose rest.
"""

import math
import shutil
import tempfile
from pathlib import Path

import bmesh
import bpy
import numpy as np
from mathutils import Euler, Matrix, Vector

ROOT = Path(__file__).resolve().parent
X, Y, Z = Vector((1, 0, 0)), Vector((0, 1, 0)), Vector((0, 0, 1))
SUPER = 2.5            # default superellipse exponent for cross sections


# ---------------------------------------------------------------------------
# Scene
# ---------------------------------------------------------------------------
def reset_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    return bpy.context.scene


def new_collection(name, parent=None):
    col = bpy.data.collections.new(name)
    (parent or bpy.context.scene.collection).children.link(col)
    return col


# ---------------------------------------------------------------------------
# Textures (sRGB float arrays, row 0 = bottom of the image)
# ---------------------------------------------------------------------------
def grid(w, h):
    y, x = np.mgrid[0:h, 0:w].astype(float)
    return x, y


def solid(w, h, color):
    return np.ones((h, w, 3)) * np.array(color, float)


def noise(n, base, amount, seed, clump=2, h=None):
    h = h or n
    rng = np.random.default_rng(seed)
    small = rng.uniform(-amount, amount, (h // clump, n // clump))
    f = 1.0 + np.kron(small, np.ones((clump, clump)))
    return np.array(base, float)[None, None, :] * f[..., None]


def brushed(n, base, seed, h=None):
    h = h or n
    rng = np.random.default_rng(seed)
    f = 1.0 + rng.uniform(-0.06, 0.06, (h, 1)) + rng.uniform(-0.02, 0.02, (h, n))
    return np.array(base, float)[None, None, :] * f[..., None]


def wood(w, h, base, seed, plank_px=32, vertical=True, seams=True, joint_px=0):
    rng = np.random.default_rng(seed)
    x, y = grid(w, h)
    a, b = (x, y) if vertical else (y, x)
    plank = (a // plank_px).astype(int)
    pv = rng.uniform(-0.12, 0.12, plank.max() + 1)[plank]
    grain = 0.08 * np.sin((a + 2.5 * np.sin(b / 11.0 + plank * 1.7)) * 1.3 + plank * 2.1)
    f = 1.0 + pv + grain + rng.uniform(-0.03, 0.03, (h, w))
    if seams:
        f[(a % plank_px) == 0] *= 0.5
    if joint_px:
        offs = rng.integers(0, joint_px, plank.max() + 1)[plank]
        f[((b + offs) % joint_px) == 0] *= 0.5
    return np.array(base, float)[None, None, :] * f[..., None]


def plaid(n, base, line_a, line_b, seed, period=32):
    rng = np.random.default_rng(seed)
    x, y = grid(n, n)
    arr = np.ones((n, n, 3)) * np.array(base, float)
    for coord in (x, y):
        band = (coord % period) < period * 0.28
        arr[band] = arr[band] * 0.55 + np.array(line_a) * 0.45
        thin = (coord % period).astype(int) == int(period * 0.62)
        arr[thin] = arr[thin] * 0.4 + np.array(line_b) * 0.6
    arr *= (1.0 + rng.uniform(-0.04, 0.04, (n, n)))[..., None]
    return arr


def stripes(n, base, line, seed, period=12, width=4, vertical=False):
    x, y = grid(n, n)
    c = x if vertical else y
    arr = noise(n, base, 0.04, seed)
    arr[(c % period) < width] = arr[(c % period) < width] * 0.3 + np.array(line) * 0.7
    return arr


def save_image(name, arr, tex_dir):
    """Write an sRGB array as PNG, pack it, and return the Blender image."""
    tex_dir = Path(tex_dir)
    tex_dir.mkdir(parents=True, exist_ok=True)
    h, w, _ = arr.shape
    rgba = np.concatenate([np.clip(arr, 0, 1), np.ones((h, w, 1))], axis=2).astype(np.float32)
    img = bpy.data.images.new(name, w, h, alpha=False)
    img.pixels.foreach_set(rgba.ravel())
    img.filepath_raw = str(tex_dir / f"{name}.png")
    img.file_format = "PNG"
    img.save()
    img.pack()
    return img


def material(name, img, rough=0.7, metal=0.0, emit=0.0):
    """Principled BSDF fed only by a nearest-filtered image texture."""
    m = bpy.data.materials.new(name)
    nt = m.node_tree
    bsdf = nt.nodes["Principled BSDF"]
    t = nt.nodes.new("ShaderNodeTexImage")
    t.image = img
    t.interpolation = "Closest"
    t.location = (-350, 200)
    nt.links.new(t.outputs["Color"], bsdf.inputs["Base Color"])
    bsdf.inputs["Roughness"].default_value = rough
    bsdf.inputs["Metallic"].default_value = metal
    if emit > 0:
        nt.links.new(t.outputs["Color"], bsdf.inputs["Emission Color"])
        bsdf.inputs["Emission Strength"].default_value = emit
    return m


class MatLib:
    """Named materials with a UV mode each: tile size in metres, (u, v) tile sizes, or 'fit'."""

    def __init__(self, tex_dir, prefix=""):
        self.tex_dir = Path(tex_dir)
        self.prefix = prefix
        self.mats = {}
        self.uv = {}

    def add(self, name, arr, uv=0.5, rough=0.7, metal=0.0, emit=0.0):
        if name in self.mats:
            raise KeyError(f"material {name} defined twice")
        img = save_image(f"tex_{self.prefix}{name}", arr, self.tex_dir)
        self.mats[name] = material(f"{self.prefix}{name}", img, rough, metal, emit)
        self.uv[name] = uv
        return self.mats[name]

    def add_image(self, name, img, uv="fit", rough=0.7):
        img.pack()
        self.mats[name] = material(f"{self.prefix}{name}", img, rough)
        self.uv[name] = uv
        return self.mats[name]


# ---------------------------------------------------------------------------
# Mesh builder
# ---------------------------------------------------------------------------
def ring_points(center, u_axis, v_axis, ru, rv, n, phase=0.0, sup=SUPER):
    pts = []
    for j in range(n):
        a = 2 * math.pi * j / n + phase
        c, s = math.cos(a), math.sin(a)
        cu = math.copysign(abs(c) ** (2 / sup), c)
        sv = math.copysign(abs(s) ** (2 / sup), s)
        pts.append(Vector(center) + Vector(u_axis) * (ru * cu) + Vector(v_axis) * (rv * sv))
    return pts


class Builder:
    def __init__(self, matlib):
        self.lib = matlib
        self.bm = bmesh.new()
        self.uv = self.bm.loops.layers.uv.new("UVMap")
        self.mats = []
        self.weights = {}      # BMVert -> {bone: w}
        self.explicit = set()  # faces whose UVs were set by hand
        self.tags = {}         # tag -> set of faces

    def _mi(self, name):
        if name not in self.lib.mats:
            raise KeyError(f"unknown material {name}")
        if name not in self.mats:
            self.mats.append(name)
        return self.mats.index(name)

    def _faces_of(self, verts):
        return {f for v in verts for f in v.link_faces}

    def _weigh(self, verts, weight):
        if weight is None:
            return
        for v in verts:
            self.weights[v] = weight(v.co) if callable(weight) else dict(weight)

    def _tag(self, tag, faces):
        if tag:
            self.tags.setdefault(tag, set()).update(faces)

    def box(self, center, size, mat, top=None, bottom=None, front=None, rot=(0, 0, 0),
            weight=None, tag=None):
        """Box rotated (XYZ radians) about its centre. front = faces facing local -Y."""
        r = Euler(rot).to_matrix()
        c = Vector(center)
        verts = bmesh.ops.create_cube(self.bm, size=1.0)["verts"]
        local_front = r @ Vector((0, -1, 0))
        for v in verts:
            v.co = c + r @ Vector((v.co.x * size[0], v.co.y * size[1], v.co.z * size[2]))
        faces = self._faces_of(verts)
        for f in faces:
            f.normal_update()
            m = mat
            if top and f.normal.z > 0.9:
                m = top
            elif bottom and f.normal.z < -0.9:
                m = bottom
            elif front and f.normal.dot(local_front) > 0.9:
                m = front
            f.material_index = self._mi(m)
        self._weigh(verts, weight)
        self._tag(tag, faces)
        return faces

    def cylinder(self, p0, p1, r0, mat, segs=8, r1=None, caps=True, weight=None, tag=None, cap_mat=None):
        """Tube from p0 to p1 with radius r0 -> r1."""
        p0, p1 = Vector(p0), Vector(p1)
        t = (p1 - p0).normalized()
        ref = Z if abs(t.z) < 0.9 else X
        u = t.cross(ref).normalized()
        v = t.cross(u).normalized()
        rings = [ring_points(p0, u, v, r0, r0, segs, sup=2), ring_points(p1, u, v, r1 or r0, r1 or r0, segs, sup=2)]
        cm = cap_mat or mat
        return self.loft(rings, [mat], weight, u, v, cap_start=cm if caps else None,
                         cap_end=cm if caps else None, tile_v=max((p1 - p0).length, 0.05), tag=tag)

    def prism(self, pts, z0, z1, mat, matrix=Matrix(), weight=None, tag=None, top=None):
        """Extrude a CCW 2D outline from z0 to z1 (then transform by matrix)."""
        bot = [self.bm.verts.new(matrix @ Vector((x, y, z0))) for x, y in pts]
        topv = [self.bm.verts.new(matrix @ Vector((x, y, z1))) for x, y in pts]
        faces = [self.bm.faces.new(topv), self.bm.faces.new(list(reversed(bot)))]
        n = len(pts)
        for i in range(n):
            j = (i + 1) % n
            faces.append(self.bm.faces.new((bot[i], bot[j], topv[j], topv[i])))
        for k, f in enumerate(faces):
            f.material_index = self._mi(top if (top and k == 0) else mat)
        self._weigh(bot + topv, weight)
        self._tag(tag, faces)
        return faces

    def quad(self, pts, mat, inward, weight=None, tag=None, uvs=None):
        vs = [self.bm.verts.new(p) for p in pts]
        f = self.bm.faces.new(vs)
        f.normal_update()
        if f.normal.dot(Vector(inward)) < 0:
            f.normal_flip()
            if uvs:
                uvs = list(reversed(uvs))
        f.material_index = self._mi(mat)
        if uvs:
            for loop, uv in zip(f.loops, uvs):
                loop[self.uv].uv = uv
            self.explicit.add(f)
        self._weigh(vs, weight)
        self._tag(tag, [f])
        return f

    def loft(self, rings, mats, weight, u_axis, v_axis, cap_start=None, cap_end=None,
             tile_v=0.5, tag=None, u_scale=1.0, flip=None):
        """Skin consecutive rings (equal point counts) with quads and cylindrical UVs.
        mats: one material per band (or a single name). Returns (faces, verts)."""
        n = len(rings[0])
        if isinstance(mats, str):
            mats = [mats] * (len(rings) - 1)
        verts = [[self.bm.verts.new(p) for p in ring] for ring in rings]
        self._weigh([v for r in verts for v in r], weight)
        t = (sum(rings[-1], Vector()) - sum(rings[0], Vector())) / n
        if flip is None:
            flip = Vector(u_axis).cross(Vector(v_axis)).dot(t) < 0
        vacc = [0.0]
        for i in range(1, len(rings)):
            c0 = sum(rings[i - 1], Vector()) / n
            c1 = sum(rings[i], Vector()) / n
            vacc.append(vacc[-1] + (c1 - c0).length + 1e-4)
        faces = []
        for i in range(len(rings) - 1):
            for j in range(n):
                k = (j + 1) % n
                quad = [verts[i][j], verts[i][k], verts[i + 1][k], verts[i + 1][j]]
                uvs = [(j / n * u_scale, vacc[i] / tile_v), ((j + 1) / n * u_scale, vacc[i] / tile_v),
                       ((j + 1) / n * u_scale, vacc[i + 1] / tile_v), (j / n * u_scale, vacc[i + 1] / tile_v)]
                if flip:
                    quad.reverse()
                    uvs.reverse()
                f = self.bm.faces.new(quad)
                f.material_index = self._mi(mats[i])
                for loop, uv in zip(f.loops, uvs):
                    loop[self.uv].uv = uv
                faces.append(f)
        for ring, cap_mat, at_end in ((verts[0], cap_start, False), (verts[-1], cap_end, True)):
            if cap_mat is None:
                continue
            order = list(ring) if (at_end != flip) else list(reversed(ring))
            f = self.bm.faces.new(order)
            f.material_index = self._mi(cap_mat)
            c = sum((v.co for v in order), Vector()) / len(order)
            span = max((v.co - c).length for v in order) * 2 or 1.0
            for loop in f.loops:
                d = loop.vert.co - c
                loop[self.uv].uv = (0.5 + d.dot(Vector(u_axis)) / span, 0.5 + d.dot(Vector(v_axis)) / span)
            faces.append(f)
        self.explicit.update(faces)
        self._tag(tag, faces)
        return faces, verts

    def tube(self, pts, radii, mats, segs=8, cap_start=None, cap_end=None, weight=None, tag=None, tile_v=0.3):
        """Bent tube through pts (radius per point), rings kept perpendicular to the path by
        parallel transport so bends don't twist. mats: one name or one per segment."""
        pts = [Vector(p) for p in pts]
        tangents = []
        for i in range(len(pts)):
            a = pts[max(i - 1, 0)]
            c = pts[min(i + 1, len(pts) - 1)]
            tangents.append((c - a).normalized())
        t0 = tangents[0]
        ref = Z if abs(t0.z) < 0.9 else X
        u = t0.cross(ref).normalized()
        rings, frames = [], []
        for i, (p, t) in enumerate(zip(pts, tangents)):
            if i:
                u = (tangents[i - 1].rotation_difference(t) @ u).normalized()
            v = t.cross(u).normalized()
            rings.append(ring_points(p, u, v, radii[i], radii[i], segs, sup=2))
            frames.append((u, v))
        # u x v == t at every ring, so winding is outward without the loft's global check
        return self.loft(rings, mats, weight, frames[0][0], frames[0][1], cap_start=cap_start,
                         cap_end=cap_end, tile_v=tile_v, tag=tag, flip=False)

    def fan(self, ring, apex, mat, outward, weight=None, tag=None):
        av = self.bm.verts.new(apex)
        self._weigh([av], weight)
        faces = []
        for i in range(len(ring)):
            f = self.bm.faces.new((ring[i], ring[(i + 1) % len(ring)], av))
            f.normal_update()
            if f.normal.dot(Vector(outward)) < 0:
                f.normal_flip()
            f.material_index = self._mi(mat)
            faces.append(f)
        self._tag(tag, faces)
        return faces, av

    def transform(self, matrix):
        bmesh.ops.transform(self.bm, matrix=matrix, verts=list(self.bm.verts))

    def _project_uvs(self):
        up = Z
        for f in self.bm.faces:
            if f in self.explicit:
                continue
            mode = self.lib.uv[self.mats[f.material_index]]
            n = f.normal
            if abs(n.z) > 0.7:
                ua, va = X, Y
            else:
                fwd = Vector((-n.x, -n.y, 0.0)).normalized()
                ua, va = fwd.cross(up), up
            pts = [(l.vert.co.dot(ua), l.vert.co.dot(va)) for l in f.loops]
            if mode == "fit":
                us, vs = [p[0] for p in pts], [p[1] for p in pts]
                u0, u1, v0, v1 = min(us), max(us), min(vs), max(vs)
                du, dv = (u1 - u0) or 1.0, (v1 - v0) or 1.0
                for l, (u, v) in zip(f.loops, pts):
                    l[self.uv].uv = ((u - u0) / du, (v - v0) / dv)
            else:
                tu, tv = mode if isinstance(mode, tuple) else (mode, mode)
                for l, (u, v) in zip(f.loops, pts):
                    l[self.uv].uv = (u / tu, v / tv)

    def remap_uvs(self, faces, fn):
        """fn(co, face) -> (u, v) for every loop of the given faces."""
        for f in faces:
            for loop in f.loops:
                loop[self.uv].uv = fn(loop.vert.co, f)
            self.explicit.add(f)

    def build(self, name, collection, loc=(0, 0, 0), rot_z=0.0, smooth=False):
        bm = self.bm
        bm.normal_update()
        self._project_uvs()
        me = bpy.data.meshes.new(name)
        self.weight_list = [self.weights.get(v) for v in bm.verts]
        bm.to_mesh(me)
        bm.free()
        for m in self.mats:
            me.materials.append(self.lib.mats[m])
        if smooth:
            for p in me.polygons:
                p.use_smooth = True
        ob = bpy.data.objects.new(name, me)
        ob.location = loc
        ob.rotation_euler = (0, 0, rot_z)
        collection.objects.link(ob)
        return ob


def tri_count(obj):
    return sum(len(p.vertices) - 2 for p in obj.data.polygons)


def empty(name, collection, loc, rot=(0, 0, 0), size=0.05, parent=None):
    ob = bpy.data.objects.new(name, None)
    ob.empty_display_type = "ARROWS"
    ob.empty_display_size = size
    ob.location = loc
    ob.rotation_euler = rot
    collection.objects.link(ob)
    if parent:
        ob.parent = parent
    return ob


# ---------------------------------------------------------------------------
# Weights
# ---------------------------------------------------------------------------
def smoothstep(e0, e1, x):
    t = min(max((x - e0) / (e1 - e0), 0.0), 1.0)
    return t * t * (3 - 2 * t)


def chain(c, bones, bounds, bw):
    """Blend weights along a coordinate: bones[k] owns c between bounds[k-1] and bounds[k]."""
    ts = [1.0] + [smoothstep(b - bw, b + bw, c) for b in bounds] + [0.0]
    w = {}
    for k, bone in enumerate(bones):
        val = ts[k] - ts[k + 1]
        if val > 1e-4:
            w[bone] = w.get(bone, 0.0) + val
    return w


def side(x):
    return "Left" if x >= 0 else "Right"


# ---------------------------------------------------------------------------
# Humanoid body (canonical 1.80 m, scaled at the end)
# ---------------------------------------------------------------------------
CANON_H = 1.80
ARM_Z = 1.405
HIP_X = 0.095
SHOE_X = 0.100

# Head profile rings: (z, half width, front depth, back depth), measured from the Terence still.
HEAD_RINGS = [
    (1.7934, 0.0332, 0.026, 0.032), (1.7851, 0.0540, 0.046, 0.056), (1.7685, 0.0689, 0.068, 0.078),
    (1.7519, 0.0772, 0.082, 0.092), (1.7353, 0.0830, 0.090, 0.100), (1.7145, 0.0813, 0.096, 0.104),
    (1.6921, 0.0764, 0.090, 0.100), (1.6689, 0.0755, 0.091, 0.095), (1.6481, 0.0747, 0.090, 0.085),
    (1.6274, 0.0714, 0.092, 0.072), (1.6066, 0.0681, 0.090, 0.055), (1.5859, 0.0598, 0.086, 0.040),
    (1.5693, 0.0515, 0.080, 0.030), (1.5568, 0.0374, 0.072, 0.020), (1.5485, 0.0208, 0.060, 0.010),
]
HEAD_TOP = 1.80
HEAD_CENTER_Y = -0.012
CHIN_Z = 1.548
# forward push on the front-centre column per ring index: brow, nose, under-nose, lips, chin
FACE_PUSH = {5: 0.004, 7: 0.010, 8: 0.022, 9: 0.006, 10: 0.005, 12: 0.006, 13: 0.004}
EYE_RECESS = {6: 0.005}
EAR_Z = (1.620, 1.690)
EAR_X = (0.071, 0.096)

NECK_RINGS = [(1.585, 0.056, 0.056, 0.006), (1.535, 0.059, 0.060, 0.010),
              (1.495, 0.062, 0.063, 0.012), (1.455, 0.065, 0.066, 0.012)]
TORSO_RINGS = [  # (z, rx, ry, cy, region)  region: collar/upper/mid/waist/belt/hip
    (1.490, 0.066, 0.068, 0.010, "upper"), (1.472, 0.100, 0.084, 0.008, "upper"),
    (1.455, 0.155, 0.094, 0.004, "shoulder"), (1.420, 0.198, 0.104, 0.000, "shoulder"),
    (1.370, 0.205, 0.112, -0.006, "chest"), (1.290, 0.190, 0.118, -0.010, "chest"),
    (1.190, 0.176, 0.108, -0.006, "waist"), (1.090, 0.166, 0.102, -0.002, "waist"),
    (1.045, 0.166, 0.102, 0.000, "belt"), (0.998, 0.168, 0.103, 0.000, "hip"),
    (0.930, 0.180, 0.110, 0.004, "hip"), (0.895, 0.140, 0.085, 0.000, "hip"),
]
SLEEVE_SHORT = [(0.13, 0.075, 0.080), (0.20, 0.072, 0.075), (0.28, 0.066, 0.066),
                (0.355, 0.063, 0.063), (0.350, 0.046, 0.046)]
ARM_SKIN = [(0.33, 0.050, 0.050), (0.40, 0.047, 0.048), (0.46, 0.042, 0.044),
            (0.52, 0.049, 0.048), (0.60, 0.043, 0.040), (0.70, 0.031, 0.026)]
SLEEVE_LONG = [(0.13, 0.075, 0.080), (0.20, 0.072, 0.075), (0.30, 0.062, 0.063), (0.46, 0.052, 0.054),
               (0.55, 0.056, 0.055), (0.66, 0.047, 0.044), (0.700, 0.043, 0.038), (0.695, 0.030, 0.026)]
PALM = [(0.705, 0.031, 0.022), (0.745, 0.045, 0.023), (0.790, 0.046, 0.019)]
FINGERS = {  # name: (y offset, length, radius)
    "Index": (-0.030, 0.085, 0.0095), "Middle": (-0.010, 0.092, 0.0098),
    "Ring": (0.010, 0.085, 0.0094), "Little": (0.029, 0.068, 0.0082),
}
FINGER_SPLIT = (0.45, 0.30, 0.25)
FINGER_X0 = 0.782
THUMB = [((0.718, -0.024, ARM_Z - 0.006), 0.012), ((0.745, -0.050, ARM_Z - 0.008), 0.011),
         ((0.768, -0.068, ARM_Z - 0.009), 0.0095), ((0.790, -0.082, ARM_Z - 0.010), 0.008)]
LEG_RINGS = [(0.930, 0.090, 0.095), (0.800, 0.085, 0.088), (0.620, 0.072, 0.075),
             (0.500, 0.063, 0.066), (0.380, 0.061, 0.063), (0.220, 0.059, 0.061),
             (0.100, 0.061, 0.063), (0.105, 0.040, 0.042)]
SHOE_RINGS = [(0.060, 0.040, 0.045), (0.020, 0.045, 0.055), (-0.050, 0.048, 0.050),
              (-0.120, 0.047, 0.040), (-0.180, 0.040, 0.030), (-0.210, 0.025, 0.020)]

FINGER_NAMES = ("Index", "Middle", "Ring", "Little")


def humanoid_bones(scale=1.0):
    """Godot SkeletonProfileHumanoid names (no Root/Eyes/Jaw). name -> (head, tail, parent)."""
    left = {
        "LeftShoulder": ((0.03, 0, 1.42), (0.17, 0, ARM_Z), "UpperChest"),
        "LeftUpperArm": ((0.17, 0, ARM_Z), (0.46, 0, ARM_Z), "LeftShoulder"),
        "LeftLowerArm": ((0.46, 0, ARM_Z), (0.715, 0, ARM_Z), "LeftUpperArm"),
        "LeftHand": ((0.715, 0, ARM_Z), (FINGER_X0, -0.010, ARM_Z), "LeftLowerArm"),
        "LeftUpperLeg": ((HIP_X, 0, 0.92), (HIP_X, 0, 0.50), "Hips"),
        "LeftLowerLeg": ((HIP_X, 0, 0.50), (HIP_X, 0, 0.09), "LeftUpperLeg"),
        "LeftFoot": ((HIP_X, 0, 0.09), (SHOE_X, -0.12, 0.025), "LeftLowerLeg"),
        "LeftToes": ((SHOE_X, -0.12, 0.025), (SHOE_X, -0.21, 0.025), "LeftFoot"),
    }
    names = ("Metacarpal", "Proximal", "Distal")
    for i in range(3):
        parent = "LeftHand" if i == 0 else f"LeftThumb{names[i - 1]}"
        left[f"LeftThumb{names[i]}"] = (THUMB[i][0], THUMB[i + 1][0], parent)
    seg = ("Proximal", "Intermediate", "Distal")
    for fname, (fy, length, _) in FINGERS.items():
        x = FINGER_X0
        for i, part in enumerate(seg):
            x1 = x + length * FINGER_SPLIT[i]
            parent = "LeftHand" if i == 0 else f"Left{fname}{seg[i - 1]}"
            left[f"Left{fname}{part}"] = ((x, fy, ARM_Z - 0.006), (x1, fy, ARM_Z - 0.006), parent)
            x = x1
    bones = {
        "Hips": ((0, 0, 0.95), (0, 0, 1.05), None),
        "Spine": ((0, 0, 1.05), (0, 0, 1.18), "Hips"),
        "Chest": ((0, 0, 1.18), (0, 0, 1.30), "Spine"),
        "UpperChest": ((0, 0, 1.30), (0, 0.005, 1.46), "Chest"),
        "Neck": ((0, 0.005, 1.46), (0, 0.0, 1.57), "UpperChest"),
        "Head": ((0, 0.0, 1.57), (0, 0.0, HEAD_TOP), "Neck"),
    }
    for name, (h, t, p) in left.items():
        bones[name] = (h, t, p)
        mirror = lambda v: (-v[0], v[1], v[2])
        bones[name.replace("Left", "Right")] = (mirror(h), mirror(t), p.replace("Left", "Right") if p else p)
    return {n: (tuple(c * scale for c in h), tuple(c * scale for c in t), p) for n, (h, t, p) in bones.items()}


def head_point(rings, z, angle, sup=SUPER, grow=0.0, center_y=HEAD_CENTER_Y):
    """Point on the head surface at height z and angle (0 = +X, 270 deg = face front)."""
    zs = [r[0] for r in rings]
    z = min(max(z, zs[-1]), zs[0])
    for i in range(len(rings) - 1):
        if zs[i] >= z >= zs[i + 1]:
            t = (zs[i] - z) / ((zs[i] - zs[i + 1]) or 1.0)
            hw, f, b = [rings[i][k] + (rings[i + 1][k] - rings[i][k]) * t for k in (1, 2, 3)]
            break
    c, s = math.cos(angle), math.sin(angle)
    cu = math.copysign(abs(c) ** (2 / sup), c)
    sv = math.copysign(abs(s) ** (2 / sup), s)
    depth = b if s > 0 else f
    return Vector(((hw + grow) * cu, center_y + (depth + grow) * sv, z))


def default_face_uv(co):
    """Generic band face texture: front planar into u 0..0.75, v from chin to scalp top."""
    return 0.375 + co.x / 0.19 * 0.75, (co.z - 1.53) / (HEAD_TOP - 1.53)


def default_back_uv(co):
    a = (math.atan2(co.y - HEAD_CENTER_Y, co.x) / (2 * math.pi)) % 1.0
    return 0.78 + a * 0.19, min(max((co.z - 1.44) / (HEAD_TOP - 1.44), 0.0), 1.0)


def build_humanoid(b, P):
    """Loft a humanoid into Builder b. P keys (all optional except materials):
    mats: dict role -> material name for head, skin, top, pants, shoes, belt (optional)
    shoulder, chest, waist, hip, depth, belly, arm, leg, neck, head (scale factors, default 1)
    jaw (scale of lower-face width), sleeve 'short'|'long', head_rings (override),
    face_uv / back_uv callables, back_threshold (normal.y above which back_uv is used),
    face_push / eye_recess overrides, height (final metres)."""
    g = lambda k, d=1.0: P.get(k, d)
    m = P["mats"]
    rings_src = P.get("head_rings", HEAD_RINGS)
    hs, jaw = g("head"), g("jaw")
    # scale the head about the chin so the neck joint stays put
    head_rings = []
    for z, hw, f, bk in rings_src:
        k = jaw if z < 1.62 else 1.0
        head_rings.append((CHIN_Z + (z - CHIN_Z) * hs, hw * hs * k, f * hs, bk * hs))
    top_z = CHIN_Z + (HEAD_TOP - CHIN_Z) * hs
    head_w = {"Head": 1.0}

    # head
    nseg = 16
    front_j = int(round(nseg * 0.75))
    push = P.get("face_push", FACE_PUSH)
    recess = P.get("eye_recess", EYE_RECESS)
    rings = []
    for i, (z, hw, f, bk) in enumerate(head_rings):
        pts = []
        for j in range(nseg):
            a = 2 * math.pi * j / nseg
            p = head_point(head_rings, z, a)
            d = abs(j - front_j)
            if i in push and d <= 1:
                p.y -= push[i] * hs * (1.0 if d == 0 else 0.45)
            if i in recess and d == 1:
                p.y += recess[i] * hs
            pts.append(p)
        rings.append(pts)
    head_faces, head_verts = b.loft(rings, m["head"], head_w, X, Y, cap_end=m["head"], tag="head")
    fan_faces, _ = b.fan(head_verts[0], (0, HEAD_CENTER_Y + 0.004, top_z), m["head"], Z, head_w, tag="head")
    # ears
    for sgn in (1, -1):
        z_lo = CHIN_Z + (EAR_Z[0] - CHIN_Z) * hs
        z_hi = CHIN_Z + (EAR_Z[1] - CHIN_Z) * hs
        x_in, x_out = EAR_X[0] * hs, EAR_X[1] * hs
        er = []
        for t, flare, ds in ((0.0, 0.45, 0.6), (0.3, 0.85, 0.9), (0.62, 1.0, 1.0), (0.88, 0.8, 0.8), (1.0, 0.35, 0.5)):
            z = z_lo + (z_hi - z_lo) * t
            cx = sgn * (x_in + (x_out - x_in) * flare * 0.5)
            er.append(ring_points((cx, 0.012, z), X, Y, (x_out - x_in) * flare * 0.5 + 0.002, 0.015 * ds, 6))
        b.loft(er, m["head"], head_w, X, Y, cap_start=m["head"], cap_end=m["head"], tag="ears")
    # neck
    nk = g("neck")
    neck = [ring_points((0, cy, z), X, Y, rx * nk, ry * nk, 12) for z, rx, ry, cy in NECK_RINGS]
    b.loft(neck, m["head"], lambda co: chain(co.z, ["UpperChest", "Neck", "Head"], [1.47, 1.56], 0.02),
           X, Y, tag="neck")

    # torso
    region_scale = {"upper": g("shoulder"), "shoulder": g("shoulder"), "chest": g("chest"),
                    "waist": g("waist"), "belt": g("waist"), "hip": g("hip")}
    region_mat = {"upper": m["top"], "shoulder": m["top"], "chest": m["top"], "waist": m["top"],
                  "belt": m.get("belt", m["top"] if P.get("untucked") else m["pants"]), "hip": m["pants"]}
    if P.get("untucked"):
        region_mat["hip"] = m["top"]
    trings, tmats = [], []
    for k, (z, rx, ry, cy, reg) in enumerate(TORSO_RINGS):
        sx = region_scale[reg] if reg not in ("upper",) or k > 0 else 1.0
        belly = g("belly", 0.0) if reg in ("waist", "belt") else 0.0
        trings.append(ring_points((0, cy - belly * 0.5, z), X, Y, rx * sx, (ry + belly * 0.5) * g("depth"), 12))
        tmats.append(region_mat[reg])
    tmats = tmats[:-1]  # one material per band: band k takes ring k's region

    def torso_w(co):
        w = chain(co.z, ["Hips", "Spine", "Chest", "UpperChest"], [1.05, 1.18, 1.30], 0.04)
        s = smoothstep(0.09, 0.19, abs(co.x)) * smoothstep(1.30, 1.40, co.z) * 0.7
        if s > 0:
            w = {k: v * (1 - s) for k, v in w.items()}
            w[side(co.x) + "Shoulder"] = s
        leg = smoothstep(0.96, 0.87, co.z) * smoothstep(0.02, 0.08, abs(co.x)) * 0.6
        if leg > 0:
            w = {k: v * (1 - leg) for k, v in w.items()}
            w[side(co.x) + "UpperLeg"] = leg
        return w

    b.loft(trings, tmats, torso_w, X, Y, cap_end=m["pants"], tile_v=0.6, tag="torso")

    # arms
    ak = g("arm")
    for sgn in (1, -1):
        sd = side(sgn)

        def arm_w(co, sd=sd):
            return chain(abs(co.x), [sd + "Shoulder", sd + "UpperArm", sd + "LowerArm", sd + "Hand"],
                         [0.17, 0.46, 0.715], 0.035)

        sx = g("shoulder")
        if P.get("sleeve", "short") == "long":
            sl = [ring_points((sgn * x * (sx if x < 0.2 else 1.0), 0, ARM_Z), Y, Z, ry * ak, rz * ak, 8) for x, ry, rz in SLEEVE_LONG]
            b.loft(sl, m["top"], arm_w, Y, Z, tile_v=0.4, tag="arms")
        else:
            sl = [ring_points((sgn * x * (sx if x < 0.2 else 1.0), 0, ARM_Z), Y, Z, ry * ak, rz * ak, 8) for x, ry, rz in SLEEVE_SHORT]
            b.loft(sl, m["top"], arm_w, Y, Z, tile_v=0.4, tag="arms")
            ar = [ring_points((sgn * x, 0, ARM_Z), Y, Z, ry * ak, rz * ak, 8) for x, ry, rz in ARM_SKIN]
            b.loft(ar, m["skin"], arm_w, Y, Z, tile_v=0.4, tag="arms")
        # palm
        palm = [ring_points((sgn * x, 0, ARM_Z - 0.004), Y, Z, ry * ak, rz * ak, 8) for x, ry, rz in PALM]
        b.loft(palm, m["skin"], {sd + "Hand": 1.0}, Y, Z, cap_end=m["skin"], tile_v=0.2, tag="hands")
        # fingers: 4-sided tubes in three segments
        seg = ("Proximal", "Intermediate", "Distal")
        for fname, (fy, length, r) in FINGERS.items():
            xs = [FINGER_X0]
            for frac in FINGER_SPLIT:
                xs.append(xs[-1] + length * frac)
            rr = [r, r * 0.95, r * 0.88, r * 0.75]
            fr = [ring_points((sgn * x, fy, ARM_Z - 0.006), Y, Z, rk * ak, rk * ak * 0.85, 4, phase=math.pi / 4, sup=2)
                  for x, rk in zip(xs, rr)]
            bounds = xs[1:3]

            def fw(co, fname=fname, sd=sd, bounds=bounds):
                return chain(abs(co.x), [f"{sd}{fname}{s}" for s in seg], bounds, 0.006)

            b.loft(fr, m["skin"], fw, Y, Z, cap_end=m["skin"], tile_v=0.1, tag="hands")
        th = [ring_points((sgn * p[0], p[1], p[2]), X, Z, r * ak, r * ak * 0.85, 4, phase=math.pi / 4, sup=2)
              for p, r in THUMB]

        def tw(co, sd=sd):
            return chain(-co.y, [sd + "ThumbMetacarpal", sd + "ThumbProximal", sd + "ThumbDistal"],
                         [-THUMB[1][0][1], -THUMB[2][0][1]], 0.006)

        b.loft(th, m["skin"], tw, Vector((sgn, 0, 0)), Z, cap_end=m["skin"], tile_v=0.1, tag="hands")

    # legs and shoes
    lk = g("leg")
    for sgn in (1, -1):
        sd = side(sgn)

        def leg_w(co, sd=sd):
            return chain(-co.z, ["Hips", sd + "UpperLeg", sd + "LowerLeg", sd + "Foot"], [-0.90, -0.50, -0.10], 0.04)

        lr = [ring_points((sgn * HIP_X, 0.0, z), X, Y, rx * lk, ry * lk, 8) for z, rx, ry in LEG_RINGS]
        b.loft(lr, m["pants"], leg_w, X, Y, cap_end=m["pants"], tile_v=0.6, tag="legs")
        sr = [ring_points((sgn * SHOE_X, y, h), X, Z, w, h, 8) for y, w, h in SHOE_RINGS]
        b.loft(sr, m["shoes"], lambda co, sd=sd: chain(-co.y, [sd + "Foot", sd + "Toes"], [0.12], 0.02),
               X, Z, cap_start=m["shoes"], cap_end=m["shoes"], tile_v=0.3, tag="shoes")

    # head UVs: front planar into the face texture, back into its strip
    face_uv = P.get("face_uv", default_face_uv)
    back_uv = P.get("back_uv", default_back_uv)
    thr = P.get("back_threshold", 0.05)
    b.bm.normal_update()
    tagged = b.tags["head"] | b.tags["ears"] | b.tags["neck"]

    neck_front = P.get("neck_front", False)   # True: neck front samples the face texture (photo heads)

    def head_uv(co, f):
        if f in b.tags["neck"] and not neck_front:
            return back_uv(co)                    # plain skin from the lower strip
        ear_front = P.get("ears_front", True) and f in b.tags["ears"]
        use_front = ear_front or f.normal.y < thr or f.calc_center_median().z < 1.50
        return face_uv(co) if use_front else back_uv(co)

    b.remap_uvs(tagged, head_uv)
    return dict(top_z=top_z, head_rings=head_rings)


def hair_cap(b, mat, head_rings, front_z, back_z, side_z=None, thick=0.008, top_z=HEAD_TOP,
             segs=16, steps=4, bump=0.0, seed=0, weight=None, tag="hair"):
    """Mesh hair: a shell over the scalp whose lower edge runs from front_z (forehead)
    to back_z (nape). bump > 0 roughens it (curly hair)."""
    rng = np.random.default_rng(seed)
    side_z = side_z if side_z is not None else (front_z + back_z) / 2
    rings = []
    for k in range(steps + 1):
        t = k / steps
        pts = []
        for j in range(segs):
            a = 2 * math.pi * j / segs
            s = math.sin(a)                        # -1 front, +1 back
            edge = side_z + (back_z - side_z) * max(s, 0) + (front_z - side_z) * max(-s, 0)
            z = edge + (top_z - 0.012 - edge) * (t ** 0.75)
            g = thick + (bump * rng.uniform(0.3, 1.0) if bump else 0.0)
            pts.append(head_point(head_rings, z, a, grow=g))
        rings.append(pts)
    faces, verts = b.loft(rings, mat, weight or {"Head": 1.0}, X, Y, tile_v=0.15, tag=tag)
    b.fan(verts[-1], (0, HEAD_CENTER_Y + 0.004, top_z + thick + bump * 0.6), mat, Z, weight or {"Head": 1.0}, tag=tag)
    return faces


# ---------------------------------------------------------------------------
# Rig
# ---------------------------------------------------------------------------
def make_armature(name, bones, collection):
    data = bpy.data.armatures.new(name + "_skeleton")
    rig = bpy.data.objects.new(name, data)
    collection.objects.link(rig)
    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode="EDIT")
    for bname, (h, t, _) in bones.items():
        eb = data.edit_bones.new(bname)
        eb.head, eb.tail = h, t
    for bname, (_, _, p) in bones.items():
        if p:
            eb = data.edit_bones[bname]
            eb.parent = data.edit_bones[p]
            eb.use_connect = (eb.head - eb.parent.tail).length < 1e-5
    bpy.ops.object.mode_set(mode="OBJECT")
    for pb in rig.pose.bones:
        pb.rotation_mode = "QUATERNION"
    return rig


def skin_mesh(ob, rig, weight_list, max_influences=4):
    names = {b.name for b in rig.data.bones}
    for n in names:
        ob.vertex_groups.new(name=n)
    for i, w in enumerate(weight_list):
        if not w:
            raise RuntimeError(f"{ob.name}: vertex {i} has no weights")
        top = sorted(w.items(), key=lambda kv: -kv[1])[:max_influences]
        s = sum(v for _, v in top)
        for bname, val in top:
            if bname not in names:
                raise KeyError(f"{ob.name}: weight on unknown bone {bname}")
            ob.vertex_groups[bname].add([i], val / s, "REPLACE")
    ob.parent = rig
    mod = ob.modifiers.new("skin", "ARMATURE")
    mod.object = rig


def prop_bones(names, parent=None):
    """Extra non-humanoid root bones at the origin pointing +Y, so their rest frame equals the
    armature frame: an object attached to one with an identity offset lands where the bone is."""
    out = {}
    for n in names:
        p = parent if n != "Instrument" else None
        if n == "InstrumentSlide":
            p = "Instrument"
        out[n] = ((0, 0, 0), (0, 0.1, 0), p)
    return out


def scale_bones(bones, s):
    return {n: (tuple(c * s for c in h), tuple(c * s for c in t), p) for n, (h, t, p) in bones.items()}


# ---------------------------------------------------------------------------
# Posing and actions (armature space == world axes; keep rigs unrotated while posing)
# ---------------------------------------------------------------------------
def reset_pose(rig):
    for pb in rig.pose.bones:
        pb.matrix_basis = Matrix()
    bpy.context.view_layer.update()


def aim(rig, bone, direction):
    pb = rig.pose.bones[bone]
    cur = (pb.tail - pb.head).normalized()
    q = cur.rotation_difference(Vector(direction).normalized())
    pb.matrix = Matrix.Translation(pb.head) @ q.to_matrix().to_4x4() @ Matrix.Translation(-pb.head) @ pb.matrix
    bpy.context.view_layer.update()


def turn(rig, bone, axis, degrees):
    """Rotate a bone (and its children) about a world axis through its head."""
    pb = rig.pose.bones[bone]
    r = Matrix.Rotation(math.radians(degrees), 4, Vector(axis).normalized())
    pb.matrix = Matrix.Translation(pb.head) @ r @ Matrix.Translation(-pb.head) @ pb.matrix
    bpy.context.view_layer.update()


def move_hips(rig, offset, bone="Hips"):
    pb = rig.pose.bones[bone]
    m = pb.matrix.copy()
    m.translation = rig.data.bones[bone].head_local + Vector(offset)
    pb.matrix = m
    bpy.context.view_layer.update()


def apply_pose(rig, ops):
    """ops: list of ('hips', (dx,dy,dz)) | ('aim', bone, dir) | ('turn', bone, axis, deg)
    | ('curl', side, amount 0..1) | ('pose', other_ops). Applied in order after a reset."""
    reset_pose(rig)
    _apply_ops(rig, ops)


def _apply_ops(rig, ops):
    for op in ops:
        kind = op[0]
        if kind == "hips":
            move_hips(rig, op[1])
        elif kind == "aim":
            aim(rig, op[1], op[2])
        elif kind == "turn":
            turn(rig, op[1], op[2], op[3])
        elif kind == "curl":
            curl_fingers(rig, op[1], op[2], thumb=op[3] if len(op) > 3 else op[2])
        elif kind == "pose":
            _apply_ops(rig, op[1])
        elif kind == "ik":
            ik_arm(rig, op[1], op[2], op[3])
        elif kind == "prop":
            set_bone_matrix(rig, op[1], op[2])
        elif kind == "look":
            look(rig, *op[1:])
        elif kind == "fn":
            op[1](rig)
        else:
            raise ValueError(f"unknown pose op {op}")


def set_bone_matrix(rig, bone, matrix):
    """Pose a bone to an armature-space matrix (used for the Instrument/Prop bones)."""
    rig.pose.bones[bone].matrix = Matrix(matrix)
    bpy.context.view_layer.update()


def ik_arm(rig, sd, target, pole):
    """Two-bone IK: put the wrist (LowerArm tail) on target with the elbow toward pole."""
    ik_two(rig, f"{sd}UpperArm", f"{sd}LowerArm", target, pole)


def ik_leg(rig, sd, ankle, pole=None):
    """Two-bone IK for a leg: ankle (LowerLeg tail) on target, knee toward pole (default forward)."""
    ik_two(rig, f"{sd}UpperLeg", f"{sd}LowerLeg", ankle, pole or Vector((0, -1, 0.2)))


def ik_two(rig, upper, lower, target, pole):
    up = rig.pose.bones[upper]
    lo = rig.pose.bones[lower]
    S = up.head.copy()
    T = Vector(target)
    l1 = up.length
    l2 = lo.length
    d_vec = T - S
    d = min(max(d_vec.length, 1e-4), (l1 + l2) * 0.999)
    axis = d_vec.normalized()
    cos_a = max(-1.0, min(1.0, (l1 * l1 + d * d - l2 * l2) / (2 * l1 * d)))
    P = Vector(pole)
    perp = P - axis * P.dot(axis)
    if perp.length < 1e-6:
        perp = Z - axis * Z.dot(axis)
    perp.normalize()
    elbow = S + axis * (l1 * cos_a) + perp * (l1 * math.sqrt(max(0.0, 1 - cos_a * cos_a)))
    aim(rig, upper, elbow - S)
    aim(rig, lower, S + axis * d - rig.pose.bones[lower].head)


def look(rig, yaw=0.0, pitch=0.0, roll=0.0, spine_share=0.0):
    """Turn the head: yaw (deg, + = toward the character's left), pitch (+ = up).
    spine_share moves part of the yaw into the UpperChest."""
    if spine_share:
        turn(rig, "UpperChest", Z, yaw * spine_share)
        yaw *= (1 - spine_share)
    for bone, k in (("Neck", 0.45), ("Head", 0.55)):
        pb = rig.pose.bones[bone]
        turn(rig, bone, Z, yaw * k)
        side_axis = (pb.matrix.to_3x3() @ Vector((1, 0, 0))).normalized()
        turn(rig, bone, side_axis, -pitch * k)   # +X rotation tips the face down, so negate
        if roll:
            fwd = (pb.matrix.to_3x3() @ Vector((0, 0, 1))).normalized()
            turn(rig, bone, fwd, roll * k)


def curl_finger(rig, sd, fname, amount):
    """Bend one finger (Index/Middle/Ring/Little) 0 = straight, 1 = into the palm."""
    for part, k in (("Proximal", 1.0), ("Intermediate", 1.1), ("Distal", 0.8)):
        pb = rig.pose.bones[f"{sd}{fname}{part}"]
        axis = (pb.matrix.to_3x3() @ Vector((1, 0, 0))).normalized()
        turn(rig, pb.name, axis, -80 * amount * k)


def hand_shape(rig, sd, shape):
    """Named hand shapes: open, relaxed, grip, fist, point, pinch."""
    curls = {"open": (0, 0, 0, 0, 0), "relaxed": (0.25, 0.3, 0.35, 0.4, 0.2), "grip": (0.6, 0.65, 0.7, 0.7, 0.5),
             "fist": (1, 1, 1, 1, 0.9), "point": (0, 1, 1, 1, 0.8), "pinch": (0.55, 0.5, 0.5, 0.45, 0.7)}[shape]
    for fname, c in zip(FINGER_NAMES, curls[:4]):
        curl_finger(rig, sd, fname, c)
    for part in ("Proximal", "Distal"):
        pb = rig.pose.bones[f"{sd}Thumb{part}"]
        axis = (pb.matrix.to_3x3() @ Vector((0, 0, 1))).normalized()
        turn(rig, pb.name, axis, (50 if sd == "Left" else -50) * curls[4])


def curl_fingers(rig, sd, amount, thumb=None):
    """Bend each finger joint about the hand's local side axis (0 = flat, 1 = fist)."""
    thumb = amount if thumb is None else thumb
    for fname in FINGER_NAMES:
        for part, k in (("Proximal", 1.0), ("Intermediate", 1.1), ("Distal", 0.8)):
            pb = rig.pose.bones[f"{sd}{fname}{part}"]
            axis = (pb.matrix.to_3x3() @ Vector((1, 0, 0))).normalized()
            turn(rig, pb.name, axis, -80 * amount * k)
    for part in ("Proximal", "Distal"):
        pb = rig.pose.bones[f"{sd}Thumb{part}"]
        axis = (pb.matrix.to_3x3() @ Vector((0, 0, 1))).normalized()
        turn(rig, pb.name, axis, (50 if sd == "Left" else -50) * thumb)


LOC_BONES = ("Hips", "Instrument", "InstrumentSlide", "PropR", "PropL")


def make_action(rig, name, keys, fps=24, loop=False, interp="BEZIER"):
    """keys: list of (frame, ops). Keys every pose bone's rotation, plus location for the
    Hips and any prop/instrument bones (LOC_BONES)."""
    scene = bpy.context.scene
    scene.render.fps = fps
    if rig.animation_data is None:
        rig.animation_data_create()
    act = bpy.data.actions.new(name)
    act.use_fake_user = True
    rig.animation_data.action = act
    for frame, ops in keys:
        scene.frame_set(frame)
        apply_pose(rig, ops)
        for pb in rig.pose.bones:
            pb.keyframe_insert("rotation_quaternion", frame=frame)
            if pb.name in LOC_BONES:
                pb.keyframe_insert("location", frame=frame)
    for fc in _fcurves(act):
        for kp in fc.keyframe_points:
            kp.interpolation = interp
        if loop:
            fc.modifiers.new("CYCLES")
    act["loop"] = bool(loop)
    act.frame_range = (keys[0][0], keys[-1][0])
    rig.animation_data.action = None
    reset_pose(rig)
    return act


def _fcurves(act):
    if hasattr(act, "fcurves") and len(getattr(act, "fcurves", [])):
        return list(act.fcurves)
    out = []
    for layer in getattr(act, "layers", []):
        for strip in layer.strips:
            for bag in strip.channelbags:
                out.extend(bag.fcurves)
    return out


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------
def export_glb(path, objects, animations=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.object.select_all(action="DESELECT")
    for ob in objects:
        ob.select_set(True)
    kw = dict(filepath=str(path), export_format="GLB", use_selection=True, export_cameras=False,
              export_lights=False, export_yup=True, export_apply=False)
    if animations:
        kw.update(export_animations=True, export_animation_mode="ACTIONS", export_force_sampling=True)
    else:
        kw.update(export_animations=False)
    bpy.ops.export_scene.gltf(**kw)
    bpy.ops.object.select_all(action="DESELECT")


def add_camera(name, collection, loc, target, lens=35.0, ortho=None):
    cd = bpy.data.cameras.new(name)
    cd.lens = lens
    cd.clip_start = 0.02
    if ortho:
        cd.type = "ORTHO"
        cd.ortho_scale = ortho
    co = bpy.data.objects.new(name, cd)
    co.location = loc
    co.rotation_euler = (Vector(target) - Vector(loc)).to_track_quat("-Z", "Y").to_euler()
    collection.objects.link(co)
    return co


def setup_render(w, h, samples=16):
    sc = bpy.context.scene
    sc.render.engine = "BLENDER_EEVEE"
    sc.eevee.taa_render_samples = samples
    sc.eevee.shadow_pool_size = "1024"
    sc.view_settings.view_transform = "Standard"
    sc.render.resolution_x = w
    sc.render.resolution_y = h
    sc.render.resolution_percentage = 100
    sc.render.image_settings.file_format = "PNG"
    return sc


def neutral_lights(collection, center=(0, 0, 1.0), power=600):
    """Simple 3-point studio light for asset turnarounds (preview only)."""
    c = Vector(center)
    for name, off, p in (("key", (-2.5, -3.5, 3.0), 1.0), ("fill", (3.0, -2.0, 1.5), 0.5), ("rim", (0.5, 3.0, 3.0), 0.8)):
        ld = bpy.data.lights.new(f"preview_{name}", "AREA")
        ld.energy = power * p
        ld.size = 3.0
        lo = bpy.data.objects.new(ld.name, ld)
        lo.location = c + Vector(off)
        lo.rotation_euler = (c - lo.location).to_track_quat("-Z", "Y").to_euler()
        collection.objects.link(lo)
    w = bpy.data.worlds.new("preview_world")
    w.node_tree.nodes["Background"].inputs["Color"].default_value = (0.05, 0.05, 0.055, 1)
    bpy.context.scene.world = w


def render_frames(shots):
    """shots: list of (camera, setup_fn or None). Returns list of HxWx4 float arrays."""
    sc = bpy.context.scene
    tmp = Path(tempfile.mkdtemp(prefix="ps2_render_"))
    frames = []
    for i, (cam, setup) in enumerate(shots):
        if setup:
            setup()
        sc.camera = cam
        path = tmp / f"shot_{i}.png"
        sc.render.filepath = str(path)
        bpy.ops.render.render(write_still=True)
        img = bpy.data.images.load(str(path))
        w, h = img.size
        px = np.empty(w * h * 4, np.float32)
        img.pixels.foreach_get(px)
        frames.append(px.reshape(h, w, 4))
        bpy.data.images.remove(img)
    shutil.rmtree(tmp)
    return frames


# 5x7 label font (A-Z, 0-9, _ - . space)
_FONT = {
    "A": "01110100011000111111100011000110001", "B": "11110100011000111110100011000111110",
    "C": "01110100011000010000100001000101110", "D": "11110100011000110001100011000111110",
    "E": "11111100001000011110100001000011111", "F": "11111100001000011110100001000010000",
    "G": "01110100011000010111100011000101111", "H": "10001100011000111111100011000110001",
    "I": "01110001000010000100001000010001110", "J": "00111000100001000010000101001001100",
    "K": "10001100101010011000101001001010001", "L": "10000100001000010000100001000011111",
    "M": "10001110111010110101100011000110001", "N": "10001100011100110101100111000110001",
    "O": "01110100011000110001100011000101110", "P": "11110100011000111110100001000010000",
    "Q": "01110100011000110001101011001001101", "R": "11110100011000111110101001001010001",
    "S": "01111100001000001110000010000111110", "T": "11111001000010000100001000010000100",
    "U": "10001100011000110001100011000101110", "V": "10001100011000110001100010101000100",
    "W": "10001100011000110101101011010101010", "X": "10001100010101000100010101000110001",
    "Y": "10001100010101000100001000010000100", "Z": "11111000010001000100010001000011111",
    "0": "01110100011001110101110011000101110", "1": "00100011000010000100001000010001110",
    "2": "01110100010000100010001000100011111", "3": "11111000100010000010000011000101110",
    "4": "00010001100101010010111110001000010", "5": "11111100001111000001000011000101110",
    "6": "00110010001000011110100011000101110", "7": "11111000010001000100010000100001000",
    "8": "01110100011000101110100011000101110", "9": "01110100011000101111000010001001100",
    "_": "00000000000000000000000000000011111", "-": "00000000000000011111000000000000000",
    ".": "00000000000000000000000000110001100", " ": "0" * 35,
}


def draw_label(arr, text, x, y, scale=2, color=(1.0, 0.85, 0.4)):
    """Draw text into an HxWx4 float array; (x, y) is the top-left in image rows from the top."""
    h = arr.shape[0]
    cx = x
    for ch in text.upper():
        bits = _FONT.get(ch, _FONT[" "])
        for r in range(7):
            for c in range(5):
                if bits[r * 5 + c] == "1":
                    py = h - 1 - (y + r * scale)
                    px = cx + c * scale
                    if 0 <= py - scale + 1 and py < h and px + scale <= arr.shape[1]:
                        arr[py - scale + 1:py + 1, px:px + scale, :3] = color
        cx += 6 * scale


def compose_sheet(frames, out_path, cols, labels=None, size=(1024, 512), bg=0.05):
    """Tile frames into one sheet of at most `size`, with optional labels per tile."""
    W, H = size
    rows = math.ceil(len(frames) / cols)
    tw, th = W // cols, H // rows
    sheet = np.ones((H, W, 4), np.float32) * np.array((bg, bg, bg, 1.0), np.float32)
    for i, fr in enumerate(frames):
        r, c = divmod(i, cols)
        fh, fw, _ = fr.shape
        ys = (np.arange(th) * fh / th).astype(int)
        xs = (np.arange(tw) * fw / tw).astype(int)
        tile = fr[ys][:, xs]
        y0 = H - (r + 1) * th
        sheet[y0:y0 + th, c * tw:(c + 1) * tw] = tile
        if labels:
            draw_label(sheet, labels[i], c * tw + 4, r * th + 4, scale=1 if tw <= 256 else 2)
    img = bpy.data.images.new(Path(out_path).stem, W, H, alpha=False)
    img.pixels.foreach_set(sheet.ravel())
    img.filepath_raw = str(out_path)
    img.file_format = "PNG"
    img.save()
    bpy.data.images.remove(img)


# ---------------------------------------------------------------------------
# Room
# ---------------------------------------------------------------------------
def append_room(room_blend, parent_collection, include_preview=True):
    """Append the room (and its preview lights + world) from a room blend file."""
    names = ["room"] + (["preview_only"] if include_preview else [])
    with bpy.data.libraries.load(str(room_blend), link=False) as (src, dst):
        missing = [n for n in names if n not in src.collections]
        if missing:
            raise RuntimeError(f"{room_blend} lacks collections {missing}")
        dst.collections = names
        dst.worlds = list(src.worlds)
    for col in dst.collections:
        col.name = "room_" + col.name
        parent_collection.children.link(col)
    if include_preview:
        if not dst.worlds:
            raise RuntimeError("room world missing")
        bpy.context.scene.world = dst.worlds[0]
    return dst.collections


# ---------------------------------------------------------------------------
# Shared poses (character local frame: faces -Y, left is +X). Ops for apply_pose().
# ---------------------------------------------------------------------------
SEAT_TOP = 0.46        # top of the band chairs' cushion


def seated_ops(rig, lap_hands=True):
    """Sitting on a SEAT_TOP chair whose seat centre is under the rig origin."""
    hips = rig.data.bones["Hips"].head_local
    shin = (rig.data.bones["LeftLowerLeg"].head_local - rig.data.bones["LeftLowerLeg"].tail_local).length
    ankle_h = rig.data.bones["LeftFoot"].head_local.z
    target_z = max(ankle_h + shin + 0.02, SEAT_TOP + 0.06)
    ops = [("hips", (0, 0.06, target_z - hips.z)),
           ("turn", "Spine", X, -4)]
    for sd, sx in (("Left", 1), ("Right", -1)):
        ops += [("aim", f"{sd}UpperLeg", (sx * 0.08, -1, -0.03)),
                ("aim", f"{sd}LowerLeg", (sx * 0.02, -0.08, -1)),
                ("aim", f"{sd}Foot", (0, -1, -0.35))]
        if lap_hands:
            ops += [("aim", f"{sd}UpperArm", (sx * 0.12, -0.20, -1)),
                    ("aim", f"{sd}LowerArm", (-sx * 0.15, -1, -0.30)),
                    ("aim", f"{sd}Hand", (-sx * 0.10, -1, -0.45)),
                    ("curl", sd, 0.25)]
    return ops
