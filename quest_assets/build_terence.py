"""Build Terence Fletcher as a PS2-era character.

Run (after `python3 prep_terence_textures.py` has made the head texture):
    /Applications/Blender.app/Contents/MacOS/Blender --background --python build_terence.py

Style target: refs/terence/user_refs/ps2_target_style.webp. A hand-built low-poly body
lofted from cross-section rings, a photo face projected onto the front of the head,
black crew-neck tee, black slacks with belt, black shoes, mitten hands with a thumb.

Outputs (all in terence/):
    terence.glb              skinned mesh + skeleton, rest pose (T-pose), no lights/cameras
    terence_<VERSION>.blend  incl. the room and preview lights/cameras for look-dev
    terence_<VERSION>_sheet.png  face close-up | gesture pose | drummer's view | T-pose

Conventions: Blender Z up, character faces -Y (glTF +Z), his left is +X.
Bones use Godot's SkeletonProfileHumanoid names so the BoneMap auto-maps for retargeting.
"""

import json
import math
import shutil
import tempfile
from pathlib import Path

import bmesh
import bpy
import numpy as np
from mathutils import Matrix, Vector

ROOT = Path(__file__).resolve().parent
OUT_DIR = ROOT / "terence"
TEX_DIR = OUT_DIR / "textures"
ROOM_BLEND = ROOT / "room/room_v003.blend"
VERSION = "v001"

HEIGHT = 1.80          # top of scalp
TRI_BUDGET = 4000
TEX = 128

# ---------------------------------------------------------------------------
# Proportions (metres). Rings are (position along the part, radius u, radius v, offset).
# ---------------------------------------------------------------------------
HEAD_SEGMENTS = 16
BODY_SEGMENTS = 12
LIMB_SEGMENTS = 8
SUPER = 2.5            # superellipse exponent for cross-sections (2 = ellipse)

# Head rings in photo rows: (py, half width px, front depth m, back depth m).
# Widths measured from the levelled face still; depths from a typical skull profile.
HEAD_RINGS = [
    (30, 40, 0.026, 0.032), (40, 65, 0.046, 0.056), (60, 83, 0.068, 0.078),
    (80, 93, 0.082, 0.092), (100, 100, 0.090, 0.100), (125, 98, 0.096, 0.104),
    (152, 92, 0.090, 0.100), (180, 91, 0.091, 0.095), (205, 90, 0.090, 0.085),
    (230, 86, 0.092, 0.072), (255, 82, 0.090, 0.055), (280, 72, 0.086, 0.040),
    (300, 62, 0.080, 0.030), (315, 45, 0.072, 0.020), (325, 25, 0.060, 0.010),
]
HEAD_CENTER_Y = -0.012
# Extra forward push on the front-centre column (and half on its neighbours): nose, brow, lips, chin.
FACE_PUSH = {125: 0.004, 180: 0.010, 205: 0.022, 230: 0.006, 255: 0.005, 300: 0.006, 315: 0.004}
EYE_RECESS = {152: 0.005}
# Ears from the photo: rows 148..228, sticking out 86..116 px from the centre line.
EAR_ROWS = (148, 228)
EAR_PX = (86, 116)
EAR_Y, EAR_DEPTH = 0.012, 0.015

NECK_RINGS = [  # (z, rx, ry, cy)
    (1.585, 0.056, 0.056, 0.006), (1.535, 0.059, 0.060, 0.010),
    (1.495, 0.062, 0.063, 0.012), (1.455, 0.065, 0.066, 0.012),
]
TORSO_RINGS = [  # (z, rx, ry, cy, material)
    (1.490, 0.066, 0.068, 0.010, "shirt"),   # crew collar
    (1.472, 0.100, 0.084, 0.008, "shirt"),
    (1.455, 0.155, 0.094, 0.004, "shirt"),   # trapezius slope
    (1.420, 0.198, 0.104, 0.000, "shirt"),
    (1.370, 0.205, 0.112, -0.006, "shirt"),
    (1.290, 0.190, 0.118, -0.010, "shirt"),  # chest
    (1.190, 0.176, 0.108, -0.006, "shirt"),
    (1.090, 0.166, 0.102, -0.002, "shirt"),
    (1.045, 0.166, 0.102, 0.000, "belt"),
    (0.998, 0.168, 0.103, 0.000, "pants"),
    (0.930, 0.180, 0.110, 0.004, "pants"),   # hips
    (0.895, 0.140, 0.085, 0.000, "pants"),
]
ARM_Z = 1.405
SLEEVE_RINGS = [(0.13, 0.075, 0.080), (0.20, 0.072, 0.075), (0.28, 0.066, 0.066),
                (0.355, 0.063, 0.063), (0.350, 0.046, 0.046)]  # last ring turns the hem in
ARM_RINGS = [(0.33, 0.050, 0.050), (0.40, 0.047, 0.048), (0.46, 0.042, 0.044),
             (0.52, 0.049, 0.048), (0.60, 0.043, 0.040), (0.70, 0.031, 0.026)]  # thick forearms, per refs
HAND_RINGS = [(0.700, 0.033, 0.024), (0.745, 0.047, 0.026), (0.800, 0.049, 0.022),
              (0.860, 0.044, 0.016), (0.895, 0.033, 0.011)]
THUMB = [((0.740, -0.030, 1.398), 0.014), ((0.772, -0.058, 1.395), 0.012),
         ((0.797, -0.077, 1.392), 0.009)]
HIP_X = 0.095
LEG_RINGS = [(0.930, 0.090, 0.095), (0.800, 0.085, 0.088), (0.620, 0.072, 0.075),
             (0.500, 0.063, 0.066), (0.380, 0.061, 0.063), (0.220, 0.059, 0.061),
             (0.100, 0.061, 0.063), (0.105, 0.040, 0.042)]  # last ring turns the hem in
SHOE_RINGS = [(0.060, 0.040, 0.045), (0.020, 0.045, 0.055), (-0.050, 0.048, 0.050),
              (-0.120, 0.047, 0.040), (-0.180, 0.040, 0.030), (-0.210, 0.025, 0.020)]
SHOE_X = 0.100

# Skeleton: name -> (head, tail, parent). Left side; right side is mirrored.
BONES = {
    "Hips": ((0, 0, 0.95), (0, 0, 1.05), None),
    "Spine": ((0, 0, 1.05), (0, 0, 1.18), "Hips"),
    "Chest": ((0, 0, 1.18), (0, 0, 1.30), "Spine"),
    "UpperChest": ((0, 0, 1.30), (0, 0.005, 1.46), "Chest"),
    "Neck": ((0, 0.005, 1.46), (0, 0.0, 1.57), "UpperChest"),
    "Head": ((0, 0.0, 1.57), (0, 0.0, HEIGHT), "Neck"),
    "LeftShoulder": ((0.03, 0, 1.42), (0.17, 0, ARM_Z), "UpperChest"),
    "LeftUpperArm": ((0.17, 0, ARM_Z), (0.46, 0, ARM_Z), "LeftShoulder"),
    "LeftLowerArm": ((0.46, 0, ARM_Z), (0.715, 0, ARM_Z), "LeftUpperArm"),
    "LeftHand": ((0.715, 0, ARM_Z), (0.90, 0, ARM_Z), "LeftLowerArm"),
    "LeftUpperLeg": ((HIP_X, 0, 0.92), (HIP_X, 0, 0.50), "Hips"),
    "LeftLowerLeg": ((HIP_X, 0, 0.50), (HIP_X, 0, 0.09), "LeftUpperLeg"),
    "LeftFoot": ((HIP_X, 0, 0.09), (SHOE_X, -0.12, 0.025), "LeftLowerLeg"),
    "LeftToes": ((SHOE_X, -0.12, 0.025), (SHOE_X, -0.21, 0.025), "LeftFoot"),
}

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
face_map_path = TEX_DIR / "face_map.json"
head_png = TEX_DIR / "terence_head.png"
for p in (face_map_path, head_png, ROOM_BLEND):
    if not p.exists():
        raise FileNotFoundError(f"{p} missing (run prep_terence_textures.py / build_room.py first)")
FM = json.loads(face_map_path.read_text())
S = FM["metres_per_px"]
SKIN = FM["skin_srgb"]

bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
ASSET_COL = bpy.data.collections.new("terence")
PREVIEW_COL = bpy.data.collections.new("preview_only")
scene.collection.children.link(ASSET_COL)
scene.collection.children.link(PREVIEW_COL)


def py_to_z(py):
    return HEIGHT - (py - FM["top_y"]) * S


def photo_uv(x, z):
    """Front planar projection of a model point onto the head texture."""
    px = FM["center_x"] + x / S
    py = FM["top_y"] + (HEIGHT - z) / S
    x0, y0, x1, y1 = FM["crop"]
    u = (px - x0) / (x1 - x0) * FM["photo_w"] / FM["tex_size"]
    v = 1.0 - (py - y0) / (y1 - y0)
    return u, v


def strip_uv(x, y, z):
    """Back/top of head: cylindrical wrap into the plain scalp strip."""
    a = (math.atan2(y - HEAD_CENTER_Y, x) / (2 * math.pi)) % 1.0
    u0 = FM["photo_w"] / FM["tex_size"]
    u = u0 + 0.06 + a * (1.0 - u0 - 0.12)
    v = min(max((z - 1.44) / (HEIGHT - 1.44), 0.0), 1.0)
    return u, v


# ---------------------------------------------------------------------------
# Textures (sRGB, row 0 = bottom)
# ---------------------------------------------------------------------------
def grid(w, h):
    y, x = np.mgrid[0:h, 0:w].astype(float)
    return x, y


def noise(n, base, amount, seed, clump=2):
    rng = np.random.default_rng(seed)
    small = rng.uniform(-amount, amount, (n // clump, n // clump))
    return np.array(base)[None, None, :] * (1.0 + np.kron(small, np.ones((clump, clump))))[..., None]


def tex_shirt(n):
    x, y = grid(n, n)
    arr = noise(n, (0.050, 0.047, 0.050), 0.08, 1)
    folds = 1.0 + 0.18 * np.sin(x / n * 2 * math.pi * 5 + np.sin(y / 9.0)) * np.exp(-((y - n * 0.3) / (n * 0.35)) ** 2)
    return arr * folds[..., None]


def tex_pants(n):
    x, y = grid(n, n)
    arr = noise(n, (0.058, 0.056, 0.062), 0.06, 2)
    crease = np.exp(-((x - n * 0.75) / 2.0) ** 2) * 0.5
    arr *= (1.0 + crease)[..., None]
    return arr


def tex_belt(n):
    x, y = grid(n, n)
    arr = noise(n, (0.045, 0.035, 0.030), 0.05, 3)
    arr[(y < 6) | (y >= n - 6)] *= 1.6
    buckle = (np.abs(x - n * 0.75) < 7) & (y > 14) & (y < n - 14)
    arr[buckle] = (0.62, 0.62, 0.60)
    arr[buckle & (np.abs(x - n * 0.75) < 4) & (y > 26) & (y < n - 26)] = (0.05, 0.04, 0.035)
    return arr


def tex_shoe(n):
    x, y = grid(n, n)
    arr = noise(n, (0.035, 0.032, 0.032), 0.05, 4)
    arr *= (1.0 + 0.9 * np.exp(-((x - n * 0.25) / 8.0) ** 2))[..., None]  # toe-top sheen
    arr[y < 10] = (0.02, 0.018, 0.018)  # sole edge
    return arr


def tex_skin(n):
    x, y = grid(n, n)
    arr = noise(n, SKIN, 0.05, 5)
    # warmer, darker underside of the forearm/hand (u around 0.75 faces down in T-pose)
    arr *= (1.0 - 0.10 * np.cos((x / n) * 2 * math.pi - math.pi / 2))[..., None]
    return arr


def make_image(name, arr):
    h, w, _ = arr.shape
    rgba = np.concatenate([np.clip(arr, 0, 1), np.ones((h, w, 1))], axis=2).astype(np.float32)
    img = bpy.data.images.new(name, w, h, alpha=False)
    img.pixels.foreach_set(rgba.ravel())
    img.filepath_raw = str(TEX_DIR / f"{name}.png")
    img.file_format = "PNG"
    img.save()
    img.pack()
    return img


head_img = bpy.data.images.load(str(head_png))
head_img.pack()
IMAGES = {
    "head": head_img,
    "skin": make_image("tex_terence_skin", tex_skin(TEX)),
    "shirt": make_image("tex_terence_shirt", tex_shirt(TEX)),
    "pants": make_image("tex_terence_pants", tex_pants(TEX)),
    "belt": make_image("tex_terence_belt", tex_belt(TEX)),
    "shoe": make_image("tex_terence_shoe", tex_shoe(TEX)),
}
ROUGH = {"head": 0.6, "skin": 0.6, "shirt": 0.95, "pants": 0.85, "belt": 0.5, "shoe": 0.35}
MATS = {}
for name, img in IMAGES.items():
    m = bpy.data.materials.new(f"terence_{name}")
    nt = m.node_tree
    bsdf = nt.nodes["Principled BSDF"]
    t = nt.nodes.new("ShaderNodeTexImage")
    t.image = img
    t.interpolation = "Closest"
    nt.links.new(t.outputs["Color"], bsdf.inputs["Base Color"])
    bsdf.inputs["Roughness"].default_value = ROUGH[name]
    MATS[name] = m
MAT_ORDER = list(MATS)

# ---------------------------------------------------------------------------
# Lofted mesh building
# ---------------------------------------------------------------------------
bm = bmesh.new()
uv_layer = bm.loops.layers.uv.new("UVMap")
vertex_weights = {}   # BMVert -> {bone: weight}
head_faces = set()    # faces that get the photo/strip head mapping


def ring_points(center, u_axis, v_axis, ru, rv, n, phase=0.0):
    pts = []
    for j in range(n):
        a = 2 * math.pi * j / n + phase
        c, s = math.cos(a), math.sin(a)
        cu = math.copysign(abs(c) ** (2 / SUPER), c)
        sv = math.copysign(abs(s) ** (2 / SUPER), s)
        pts.append(Vector(center) + u_axis * (ru * cu) + v_axis * (rv * sv))
    return pts


def loft(rings, mats, weight_fn, u_axis, v_axis, cap_start=None, cap_end=None, tile_v=0.5,
         head=False):
    """rings: list of point lists (same length). mats: material per band."""
    n = len(rings[0])
    verts = [[bm.verts.new(p) for p in ring] for ring in rings]
    for ring in verts:
        for v in ring:
            vertex_weights[v] = weight_fn(v.co)
    t = (sum(rings[-1], Vector()) - sum(rings[0], Vector())) / n
    flip = u_axis.cross(v_axis).dot(t) < 0
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
            uvs = [(j / n, vacc[i] / tile_v), ((j + 1) / n, vacc[i] / tile_v),
                   ((j + 1) / n, vacc[i + 1] / tile_v), (j / n, vacc[i + 1] / tile_v)]
            if flip:
                quad.reverse()
                uvs.reverse()
            f = bm.faces.new(quad)
            f.material_index = MAT_ORDER.index(mats[i])
            for loop, uv in zip(f.loops, uvs):
                loop[uv_layer].uv = uv
            faces.append(f)
    for ring, cap_mat, at_end in ((verts[0], cap_start, False), (verts[-1], cap_end, True)):
        if cap_mat is None:
            continue
        order = list(ring) if (at_end != flip) else list(reversed(ring))
        f = bm.faces.new(order)
        f.material_index = MAT_ORDER.index(cap_mat)
        c = sum((v.co for v in order), Vector()) / len(order)
        for loop in f.loops:
            d = loop.vert.co - c
            loop[uv_layer].uv = (0.5 + d.dot(u_axis) * 2, 0.5 + d.dot(v_axis) * 2)
        faces.append(f)
    if head:
        head_faces.update(faces)
    return faces, verts


def smoothstep(e0, e1, x):
    t = min(max((x - e0) / (e1 - e0), 0.0), 1.0)
    return t * t * (3 - 2 * t)


def chain(c, bones, bounds, bw):
    ts = [1.0] + [smoothstep(b - bw, b + bw, c) for b in bounds] + [0.0]
    w = {}
    for k, bone in enumerate(bones):
        val = ts[k] - ts[k + 1]
        if val > 1e-4:
            w[bone] = w.get(bone, 0.0) + val
    return w


def side_name(x):
    return "Left" if x >= 0 else "Right"


X, Y, Z = Vector((1, 0, 0)), Vector((0, 1, 0)), Vector((0, 0, 1))

# --- head --------------------------------------------------------------------
front_j = int(round(HEAD_SEGMENTS * 0.75))  # angle 270 deg = -Y = face front
head_rings = []
for py, hw_px, f_d, b_d in HEAD_RINGS:
    z = py_to_z(py)
    hw = hw_px * S
    pts = []
    for j in range(HEAD_SEGMENTS):
        a = 2 * math.pi * j / HEAD_SEGMENTS
        c, s = math.cos(a), math.sin(a)
        cu = math.copysign(abs(c) ** (2 / SUPER), c)
        sv = math.copysign(abs(s) ** (2 / SUPER), s)
        depth = b_d if s > 0 else f_d
        y = HEAD_CENTER_Y + depth * sv
        d = abs(j - front_j)
        if py in FACE_PUSH and d <= 1:
            y -= FACE_PUSH[py] * (1.0 if d == 0 else 0.45)
        if py in EYE_RECESS and d == 1:
            y += EYE_RECESS[py]
        pts.append(Vector((hw * cu, y, z)))
    head_rings.append(pts)
apex = Vector((0, HEAD_CENTER_Y + 0.004, HEIGHT))
# rings run scalp -> chin: the chin gets a flat cap, the scalp a fan to one apex vertex
_, head_verts = loft(head_rings, ["head"] * (len(head_rings) - 1), lambda co: {"Head": 1.0},
                     X, Y, cap_end="head", head=True)
top_ring = head_verts[0]
apex_v = bm.verts.new(apex)
vertex_weights[apex_v] = {"Head": 1.0}
for i in range(len(top_ring)):
    a, b = top_ring[i], top_ring[(i + 1) % len(top_ring)]
    f = bm.faces.new((a, b, apex_v))
    f.normal_update()
    if f.normal.z < 0:
        f.normal_flip()
    f.material_index = MAT_ORDER.index("head")
    head_faces.add(f)
# --- ears --------------------------------------------------------------------
for sgn in (1, -1):
    z_lo, z_hi = py_to_z(EAR_ROWS[1]), py_to_z(EAR_ROWS[0])
    x_in, x_out = EAR_PX[0] * S, EAR_PX[1] * S
    rings = []
    # lobe, lower bowl, widest, upper rim, top: outer edge flares most at the top third
    for t, flare, dscale in ((0.0, 0.45, 0.6), (0.3, 0.85, 0.9), (0.62, 1.0, 1.0), (0.88, 0.8, 0.8), (1.0, 0.35, 0.5)):
        z = z_lo + (z_hi - z_lo) * t
        cx = sgn * (x_in + (x_out - x_in) * flare * 0.5)
        rx = (x_out - x_in) * flare * 0.5 + 0.002
        rings.append(ring_points((cx, EAR_Y, z), X, Y, rx, EAR_DEPTH * dscale, 6))
    loft(rings, ["head"] * (len(rings) - 1), lambda co: {"Head": 1.0}, X, Y, cap_start="head",
         cap_end="head", head=True)

# --- neck --------------------------------------------------------------------
neck_rings = [ring_points((0, cy, z), X, Y, rx, ry, BODY_SEGMENTS) for z, rx, ry, cy in NECK_RINGS]
loft(neck_rings, ["head"] * 3,
     lambda co: chain(co.z, ["UpperChest", "Neck", "Head"], [1.47, 1.56], 0.02), X, Y, head=True)


# --- torso -------------------------------------------------------------------
def torso_weights(co):
    w = chain(co.z, ["Hips", "Spine", "Chest", "UpperChest"], [1.05, 1.18, 1.30], 0.04)
    s = smoothstep(0.09, 0.19, abs(co.x)) * smoothstep(1.30, 1.40, co.z) * 0.7
    if s > 0:
        w = {k: v * (1 - s) for k, v in w.items()}
        w[side_name(co.x) + "Shoulder"] = s
    leg = smoothstep(0.96, 0.87, co.z) * smoothstep(0.02, 0.08, abs(co.x)) * 0.6
    if leg > 0:
        w = {k: v * (1 - leg) for k, v in w.items()}
        w[side_name(co.x) + "UpperLeg"] = leg
    return w


torso_rings = [ring_points((0, cy, z), X, Y, rx, ry, BODY_SEGMENTS) for z, rx, ry, cy, _ in TORSO_RINGS]
loft(torso_rings, [TORSO_RINGS[i][4] for i in range(len(TORSO_RINGS) - 1)], torso_weights, X, Y,
     cap_end="pants", tile_v=0.6)

# --- arms ----------------------------------------------------------------------
for sgn in (1, -1):
    side = side_name(sgn)
    ax = Vector((sgn, 0, 0))
    u_axis, v_axis = Y, Z

    def arm_w(co, side=side):
        return chain(abs(co.x), [side + "Shoulder", side + "UpperArm", side + "LowerArm", side + "Hand"],
                     [0.17, 0.46, 0.715], 0.035)

    sleeve = [ring_points((sgn * x, 0, ARM_Z), u_axis, v_axis, ry, rz, LIMB_SEGMENTS) for x, ry, rz in SLEEVE_RINGS]
    loft(sleeve, ["shirt"] * (len(sleeve) - 1), arm_w, u_axis, v_axis, tile_v=0.4)
    arm = [ring_points((sgn * x, 0, ARM_Z), u_axis, v_axis, ry, rz, LIMB_SEGMENTS) for x, ry, rz in ARM_RINGS]
    loft(arm, ["skin"] * (len(arm) - 1), arm_w, u_axis, v_axis, tile_v=0.4)
    hand = [ring_points((sgn * x, 0, ARM_Z - 0.004), u_axis, v_axis, ry, rz, LIMB_SEGMENTS) for x, ry, rz in HAND_RINGS]
    loft(hand, ["skin"] * (len(hand) - 1), lambda co, side=side: {side + "Hand": 1.0}, u_axis, v_axis,
         cap_end="skin", tile_v=0.2)
    thumb = []
    for (x, y, z), r in THUMB:
        thumb.append(ring_points((sgn * x, y, z), Vector((sgn, 0, 0)), Z, r, r * 0.8, 6))
    loft(thumb, ["skin"] * (len(thumb) - 1), lambda co, side=side: {side + "Hand": 1.0},
         Vector((sgn, 0, 0)), Z, cap_end="skin", tile_v=0.2)

# --- legs and shoes ----------------------------------------------------------
for sgn in (1, -1):
    side = side_name(sgn)

    def leg_w(co, side=side):
        return chain(-co.z, ["Hips", side + "UpperLeg", side + "LowerLeg", side + "Foot"],
                     [-0.90, -0.50, -0.10], 0.04)

    leg = [ring_points((sgn * HIP_X, 0.0, z), X, Y, rx, ry, LIMB_SEGMENTS) for z, rx, ry in LEG_RINGS]
    loft(leg, ["pants"] * (len(leg) - 1), leg_w, X, Y, cap_end="pants", tile_v=0.6)
    shoe = [ring_points((sgn * SHOE_X, y, h), X, Z, w, h, LIMB_SEGMENTS) for y, w, h in SHOE_RINGS]
    loft(shoe, ["shoe"] * (len(shoe) - 1),
         lambda co, side=side: chain(-co.y, [side + "Foot", side + "Toes"], [0.12], 0.02),
         X, Z, cap_start="shoe", cap_end="shoe", tile_v=0.3)

# --- head UVs: photo on the front, scalp strip elsewhere ----------------------
bm.normal_update()
for f in head_faces:
    use_photo = f.normal.y < 0.05 or f.calc_center_median().z < 1.50
    for loop in f.loops:
        co = loop.vert.co
        loop[uv_layer].uv = photo_uv(co.x, co.z) if use_photo else strip_uv(co.x, co.y, co.z)

# Belt band: stretch the whole texture over its height so the buckle reads in one piece.
belt_zs = [r[0] for r in TORSO_RINGS if r[4] == "belt"]
belt_top, belt_bot = belt_zs[0], next(r[0] for r in TORSO_RINGS if r[0] < belt_zs[0])
belt_index = MAT_ORDER.index("belt")
for f in bm.faces:
    if f.material_index == belt_index:
        for loop in f.loops:
            loop[uv_layer].uv = (loop[uv_layer].uv.x, (loop.vert.co.z - belt_bot) / (belt_top - belt_bot))

mesh = bpy.data.meshes.new("terence_mesh")
bm.to_mesh(mesh)
vert_list = list(bm.verts)
weights_by_index = [vertex_weights[v] for v in vert_list]
bm.free()
for m in MAT_ORDER:
    mesh.materials.append(MATS[m])
for p in mesh.polygons:
    p.use_smooth = True
body = bpy.data.objects.new("terence_mesh", mesh)
ASSET_COL.objects.link(body)

tris = sum(len(p.vertices) - 2 for p in mesh.polygons)
print(f"[build_terence] triangles: {tris}")
if tris >= TRI_BUDGET:
    raise RuntimeError(f"triangle budget exceeded: {tris} >= {TRI_BUDGET}")

# ---------------------------------------------------------------------------
# Skeleton and skin weights
# ---------------------------------------------------------------------------
arm_data = bpy.data.armatures.new("terence_skeleton")
rig = bpy.data.objects.new("terence_rig", arm_data)
ASSET_COL.objects.link(rig)
bpy.context.view_layer.objects.active = rig
bpy.ops.object.mode_set(mode="EDIT")
all_bones = {}
for name, (h, t, parent) in BONES.items():
    for sgn, prefix_from, prefix_to in ((1, "Left", "Left"), (-1, "Left", "Right")):
        if not name.startswith("Left") and sgn == -1:
            continue
        bname = name.replace(prefix_from, prefix_to)
        pname = parent.replace(prefix_from, prefix_to) if parent else None
        eb = arm_data.edit_bones.new(bname)
        eb.head = (h[0] * sgn, h[1], h[2])
        eb.tail = (t[0] * sgn, t[1], t[2])
        all_bones[bname] = pname
for bname, pname in all_bones.items():
    if pname:
        arm_data.edit_bones[bname].parent = arm_data.edit_bones[pname]
        if (arm_data.edit_bones[bname].head - arm_data.edit_bones[pname].tail).length < 1e-4:
            arm_data.edit_bones[bname].use_connect = True
bpy.ops.object.mode_set(mode="OBJECT")

for bname in all_bones:
    body.vertex_groups.new(name=bname)
for i, w in enumerate(weights_by_index):
    total = sum(w.values())
    if total <= 0:
        raise RuntimeError(f"vertex {i} has no weights")
    top = sorted(w.items(), key=lambda kv: -kv[1])[:4]
    s = sum(v for _, v in top)
    for bname, val in top:
        if bname not in all_bones:
            raise KeyError(f"weight references unknown bone {bname}")
        body.vertex_groups[bname].add([i], val / s, "REPLACE")
body.parent = rig
mod = body.modifiers.new("skin", "ARMATURE")
mod.object = rig

# ---------------------------------------------------------------------------
# Export glb (rest pose)
# ---------------------------------------------------------------------------
OUT_DIR.mkdir(exist_ok=True)
bpy.ops.object.select_all(action="DESELECT")
rig.select_set(True)
body.select_set(True)
bpy.ops.export_scene.gltf(
    filepath=str(OUT_DIR / "terence.glb"),
    export_format="GLB",
    use_selection=True,
    export_cameras=False,
    export_lights=False,
    export_yup=True,
    export_skins=True,
    export_animations=False,
)
bpy.ops.object.select_all(action="DESELECT")

# ---------------------------------------------------------------------------
# Look-dev: bring in the room, place him on his spot, pose, render
# ---------------------------------------------------------------------------
with bpy.data.libraries.load(str(ROOM_BLEND), link=False) as (src, dst):
    dst.collections = [c for c in src.collections if c in ("room", "preview_only")]
    dst.worlds = list(src.worlds)
for col in dst.collections:
    if col is None:
        raise RuntimeError("room collections failed to load")
    col.name = "lookdev_" + col.name
    PREVIEW_COL.children.link(col)
if not dst.worlds:
    raise RuntimeError("room world failed to load")
scene.world = dst.worlds[0]
spot = bpy.data.objects["spot_terence"]
drummer = bpy.data.objects["spot_drummer"]
rig.location = spot.location
rig.rotation_euler = (0, 0, spot.rotation_euler.z + math.pi)  # he faces -Y, the spot's +Y faces the band
bpy.context.view_layer.update()

key = bpy.data.lights.new("preview_key_terence", "AREA")
key.energy = 25.0
key.size = 1.0
key.color = (1.0, 0.72, 0.45)
key_ob = bpy.data.objects.new(key.name, key)
PREVIEW_COL.objects.link(key_ob)
fwd = rig.matrix_world.to_3x3() @ Vector((0, -1, 0))
key_ob.location = rig.location + fwd * 1.3 + Vector((0, 0, 2.5))
key_ob.rotation_euler = ((rig.location + Vector((0, 0, 1.6))) - key_ob.location).to_track_quat("-Z", "Y").to_euler()

scene.render.engine = "BLENDER_EEVEE"
scene.eevee.taa_render_samples = 32
scene.eevee.use_raytracing = True
scene.eevee.shadow_pool_size = "1024"
scene.view_settings.view_transform = "Standard"
scene.render.resolution_x = 540
scene.render.resolution_y = 720
scene.render.image_settings.file_format = "PNG"


def world_pt(local):
    return rig.matrix_world @ Vector(local)


def aim(bone_name, direction):
    """Rotate a pose bone so it points along an armature-space direction."""
    pb = rig.pose.bones[bone_name]
    cur = (pb.tail - pb.head).normalized()
    q = cur.rotation_difference(Vector(direction).normalized())
    pb.matrix = Matrix.Translation(pb.head) @ q.to_matrix().to_4x4() @ Matrix.Translation(-pb.head) @ pb.matrix
    bpy.context.view_layer.update()


def reset_pose():
    for pb in rig.pose.bones:
        pb.matrix_basis = Matrix()
    bpy.context.view_layer.update()


POSES = {
    "relaxed": [("LeftUpperArm", (0.18, 0.02, -1)), ("LeftLowerArm", (0.12, -0.15, -1)), ("LeftHand", (0.08, -0.1, -1)),
                ("RightUpperArm", (-0.18, 0.02, -1)), ("RightLowerArm", (-0.12, -0.15, -1)), ("RightHand", (-0.08, -0.1, -1))],
    "gesture": [("RightUpperArm", (-0.30, -0.25, -1)), ("RightLowerArm", (0.50, -0.75, 0.30)), ("RightHand", (0.35, -0.75, 0.35)),
                ("LeftUpperArm", (0.55, 0.35, -0.75)), ("LeftLowerArm", (-0.45, -0.25, -0.60)), ("LeftHand", (-0.70, -0.40, -0.20))],
}


def apply_pose(name):
    reset_pose()
    for bone, d in POSES[name]:
        aim(bone, d)


def add_cam(name, loc, target, lens):
    cd = bpy.data.cameras.new(name)
    cd.lens = lens
    cd.clip_start = 0.05
    co = bpy.data.objects.new(name, cd)
    co.location = loc
    co.rotation_euler = (Vector(target) - Vector(loc)).to_track_quat("-Z", "Y").to_euler()
    PREVIEW_COL.objects.link(co)
    return co


hidden_for_close = [bpy.data.objects[n] for n in ("stand_terence", "stool_terence")]
shots = [
    ("face", "gesture", add_cam("preview_cam_face", world_pt((0.05, -0.75, 1.66)), world_pt((0, 0, 1.64)), 50), True),
    ("gesture", "gesture", add_cam("preview_cam_gesture", world_pt((0.9, -2.0, 1.35)), world_pt((0, 0, 1.0)), 40), True),
    ("drummer", "relaxed", add_cam("preview_cam_drummer", (drummer.location.x, drummer.location.y, 1.2),
                                    world_pt((0, 0, 1.25)), 30), False),
    ("tpose", None, add_cam("preview_cam_tpose", world_pt((0, -1.45, 0.95)), world_pt((0, 0, 0.92)), 22), True),
]
tmp = Path(tempfile.mkdtemp(prefix="terence_preview_"))
frames = []
for name, pose, cam, hide in shots:
    reset_pose() if pose is None else apply_pose(pose)
    for ob in hidden_for_close:
        ob.hide_render = hide
    scene.camera = cam
    path = tmp / f"{name}.png"
    scene.render.filepath = str(path)
    bpy.ops.render.render(write_still=True)
    img = bpy.data.images.load(str(path))
    w, h = img.size
    px = np.empty(w * h * 4, np.float32)
    img.pixels.foreach_get(px)
    frames.append(px.reshape(h, w, 4))
    bpy.data.images.remove(img)
shutil.rmtree(tmp)

gap = np.ones((frames[0].shape[0], 6, 4), np.float32) * np.array((0.05, 0.05, 0.05, 1.0), np.float32)
parts = []
for i, fr in enumerate(frames):
    if i:
        parts.append(gap)
    parts.append(fr)
sheet_px = np.concatenate(parts, axis=1)
sh, sw, _ = sheet_px.shape
sheet = bpy.data.images.new("terence_sheet", sw, sh, alpha=False)
sheet.pixels.foreach_set(sheet_px.ravel())
sheet.filepath_raw = str(OUT_DIR / f"terence_{VERSION}_sheet.png")
sheet.file_format = "PNG"
sheet.save()
bpy.data.images.remove(sheet)

for ob in hidden_for_close:
    ob.hide_render = False
apply_pose("gesture")
scene.camera = shots[1][2]
bpy.ops.wm.save_as_mainfile(filepath=str(OUT_DIR / f"terence_{VERSION}.blend"))
print(f"[build_terence] done: {OUT_DIR}")
