"""Terence v003: a bigger, more imposing Terence with forceful, fluid choreography.

Run (after prep_terence_textures.py, build_room.py and build_props.py):
    /Applications/Blender.app/Contents/MacOS/Blender --background --python build_terence_v003.py

v001 (terence/terence.glb) and v002 (terence_v002*) are left untouched. Changes from v002:
  * Body. He stands HEIGHT (1.93 m) tall. Shoulders, chest, arms, hands and head are broader
    than the shared humanoid, then the whole figure is scaled so the head top lands on HEIGHT.
    The photo face UVs are computed in the unscaled head frame, so the face still lines up.
  * Motion. Moves are keyed on every frame from a pose curve, not from a few held poses. Each
    channel (hips, lean, twist, side bend, shoulders, feet, wrist targets, finger curls, gaze)
    runs through a monotone cubic, so motion flows through keys without overshooting them.
    Keys tagged "hit" arrive at speed (strikes, slams, claps), and a small breathing and weight
    sway layer keeps holds alive. Tier 4 and 5 moves use wind-ups, steps into the strike, whole
    body follow-through and recoil.
  * Clearance. Every action is sampled on every frame after it is built. Hands and forearms are
    measured against capsules for his torso, neck, head and legs, and props against the same
    capsules (the stand sweep also against his hand). The build stops if anything goes deeper
    than CLEAR_MIN.
  * t5_chair_throw uses props/chair_folded.glb (folded flat) thrown backhand like a disc, spinning
    about its short axis. `walk` is a utility loop (walk in place, root at the origin).

Prop convention is unchanged from v002: PropR / PropL are root bones whose pose is the prop's
root transform; at frame 0 a prop bone sits where the prop should already be.
Targets: DRUMMER_HEAD (the player's head at spot_drummer, 1.15 m, from the room markers) for
aimed moves, NEAR_HEAD = (0, -0.70, 1.15) for close-up moves (Godot first places Terence's node
NEAR_DIST in front of the player, facing them).

Outputs: terence/terence_v003.glb, terence_v003.blend, terence_v003_sheet.png,
         terence/terence_v003_moves.json
"""

import json
import math
import sys
from pathlib import Path

import bpy
import numpy as np
from mathutils import Euler, Matrix, Quaternion, Vector

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib_ps2 as ps2  # noqa: E402

R = ps2.ROOT
OUT = R / "terence"
TEX_DIR = OUT / "textures"
VERSION = "v003"
LOG = f"[terence_{VERSION}]"
FPS = 24
BEAT = 12
NEAR_DIST = 0.70
TRI_TARGET = 2300
HEIGHT = 1.93
CLEAR_MIN = -0.015        # deepest allowed overlap (m) between capsules: resting contact, not passing through
Z = ps2.Z
X = ps2.X

# body proportions relative to the shared 1.80 m humanoid, before the overall scale
BODY = dict(shoulder=1.12, chest=1.08, waist=1.03, hip=1.02, depth=1.06, arm=1.08, leg=1.05, neck=1.08, head=1.05)
HS = BODY["head"]
HAND_SCALE = 1.12          # hands grow about the wrist
ARM_OUT = 0.018            # arms sit this much further out, to go with the broader shoulders
HEAD_TOP_C = ps2.CHIN_Z + (ps2.HEAD_TOP - ps2.CHIN_Z) * HS
SC = HEIGHT / HEAD_TOP_C   # overall scale: rest-space numbers below are canonical and get multiplied by SC

# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------
FM = json.loads((TEX_DIR / "face_map.json").read_text())
S_PX = FM["metres_per_px"]


def head_canon(co):
    """Undo the head scale (about the chin) so UVs see the head the photo was measured on."""
    if co.z <= ps2.CHIN_Z:
        return Vector((co.x, co.y, co.z))
    return Vector((co.x / HS, co.y, ps2.CHIN_Z + (co.z - ps2.CHIN_Z) / HS))


def photo_uv(co):
    c = head_canon(co)
    px = FM["center_x"] + c.x / S_PX
    py = FM["top_y"] + (ps2.HEAD_TOP - c.z) / S_PX
    x0, y0, x1, y1 = FM["crop"]
    return (px - x0) / (x1 - x0) * FM["photo_w"] / FM["tex_size"], 1.0 - (py - y0) / (y1 - y0)


def strip_uv(co):
    c = head_canon(co)
    a = (math.atan2(c.y - ps2.HEAD_CENTER_Y, c.x) / (2 * math.pi)) % 1.0
    u0 = FM["photo_w"] / FM["tex_size"]
    return u0 + 0.06 + a * (1.0 - u0 - 0.12), min(max((c.z - 1.44) / (ps2.HEAD_TOP - 1.44), 0.0), 1.0)


def textures(lib):
    n = 128
    x, y = ps2.grid(n, n)
    img = bpy.data.images.load(str(TEX_DIR / "terence_head_v002.png"))
    lib.add_image("head", img, uv="fit", rough=0.6)
    lib.add("skin", ps2.noise(n, FM["skin_srgb_v002"], 0.05, 5), uv=0.5, rough=0.6)
    shirt = ps2.noise(n, (0.050, 0.047, 0.050), 0.08, 1)
    shirt *= (1.0 + 0.18 * np.sin(x / n * 2 * math.pi * 5 + np.sin(y / 9.0)) * np.exp(-((y - n * 0.3) / (n * 0.35)) ** 2))[..., None]
    lib.add("shirt", shirt, uv=0.6, rough=0.95)
    pants = ps2.noise(n, (0.058, 0.056, 0.062), 0.06, 2)
    pants *= (1.0 + 0.5 * np.exp(-((x - n * 0.75) / 2.0) ** 2))[..., None]
    lib.add("pants", pants, uv=0.6, rough=0.85)
    belt = ps2.noise(n, (0.045, 0.035, 0.030), 0.05, 3)
    buckle = (np.abs(x - n * 0.75) < 7) & (y > 14) & (y < n - 14)
    belt[buckle] = (0.62, 0.62, 0.60)
    belt[buckle & (np.abs(x - n * 0.75) < 4) & (y > 26) & (y < n - 26)] = (0.05, 0.04, 0.035)
    lib.add("belt", belt, uv="fit", rough=0.5)
    shoe = ps2.noise(n, (0.035, 0.032, 0.032), 0.05, 4)
    shoe *= (1.0 + 0.9 * np.exp(-((x - n * 0.25) / 8.0) ** 2))[..., None]
    shoe[y < 10] = (0.02, 0.018, 0.018)
    lib.add("shoes", shoe, uv=0.3, rough=0.35)


ARM_CHAIN = ("UpperArm", "LowerArm", "Hand", "Thumb", "Index", "Middle", "Ring", "Little")
HAND_CHAIN = ("Hand", "Thumb", "Index", "Middle", "Ring", "Little")


def wrist_c(sgn):
    """Canonical wrist position after the arm shift (before SC)."""
    return Vector((sgn * (0.715 + ARM_OUT), 0.0, ps2.ARM_Z))


def v003_bones():
    bones = ps2.humanoid_bones(1.0)
    out = {}
    for name, (h, t, p) in bones.items():
        h, t = Vector(h), Vector(t)
        sgn = 1 if name.startswith("Left") else -1
        part = name.replace("Left", "").replace("Right", "")
        if name.startswith(("Left", "Right")) and part.startswith(ARM_CHAIN):
            h.x += sgn * ARM_OUT
            t.x += sgn * ARM_OUT
            if part.startswith(HAND_CHAIN):
                w = wrist_c(sgn)
                h = w + (h - w) * HAND_SCALE
                t = w + (t - w) * HAND_SCALE
        elif part == "Shoulder":
            t.x += sgn * ARM_OUT
        out[name] = (tuple(h * SC), tuple(t * SC), p)
    return out


def build_model(col):
    lib = ps2.MatLib(TEX_DIR, prefix="terence3_")
    textures(lib)
    P = dict(mats=dict(head="head", skin="skin", top="shirt", pants="pants", shoes="shoes", belt="belt"),
             sleeve="short", face_uv=photo_uv, back_uv=strip_uv, neck_front=False,
             back_threshold=-0.35, ears_front=False, **BODY)
    b = ps2.Builder(lib)
    info = ps2.build_humanoid(b, P)
    belt_i = b.mats.index("belt")
    zs = [r[0] for r in ps2.TORSO_RINGS if r[4] == "belt"]
    top_z = zs[0]
    bot_z = next(r[0] for r in ps2.TORSO_RINGS if r[0] < top_z)
    for f in b.bm.faces:
        if f.material_index == belt_i:
            for loop in f.loops:
                loop[b.uv].uv = (loop[b.uv].uv.x, (loop.vert.co.z - bot_z) / (top_z - bot_z))
            b.explicit.add(f)
    # broader frame: arms out, bigger hands about the wrist (weights were set on creation, so they hold)
    arm_verts = {v for f in b.tags["arms"] | b.tags["hands"] for v in f.verts}
    hand_verts = {v for f in b.tags["hands"] for v in f.verts}
    for v in arm_verts:
        v.co.x += math.copysign(ARM_OUT, v.co.x)
    for v in hand_verts:
        w = wrist_c(1 if v.co.x > 0 else -1)
        v.co = w + (v.co - w) * HAND_SCALE
    b.transform(Matrix.Scale(SC, 4))
    mesh = b.build("terence_mesh", col, smooth=True)
    top = max(v.co.z for v in mesh.data.vertices)
    bottom = min(v.co.z for v in mesh.data.vertices)
    if abs(top - HEIGHT) > 0.005 or abs(bottom) > 0.002:
        raise RuntimeError(f"terence height {top:.3f} (want {HEIGHT}), lowest point {bottom:.3f}")
    if abs(info["top_z"] - HEAD_TOP_C) > 1e-6:
        raise RuntimeError("head scale bookkeeping is off")
    bones = v003_bones()
    bones.update(ps2.prop_bones(["PropR", "PropL"]))
    rig = ps2.make_armature("terence_rig", bones, col)
    ps2.skin_mesh(mesh, rig, b.weight_list)
    tris = ps2.tri_count(mesh)
    if tris > TRI_TARGET:
        raise RuntimeError(f"terence: {tris} triangles > {TRI_TARGET}")
    return rig, mesh, tris, top


# ---------------------------------------------------------------------------
# Scene data: room targets and props
# ---------------------------------------------------------------------------
def room_targets():
    names = ("spot_terence", "spot_drummer", "stool_terence", "door_main", "seat_sax_3", "seat_trombone_2",
             "seat_trumpet_3", "seat_trumpet_2", "seat_sax_1", "seat_bass_1")
    with bpy.data.libraries.load(str(R / "room/room_v003.blend"), link=False) as (src, dst):
        missing = [n for n in names if n not in src.objects]
        if missing:
            raise RuntimeError(f"room lacks {missing}")
        dst.objects = list(names)
    world = {o.name: o.location.copy() for o in dst.objects}
    spot_yaw = dst.objects[0].rotation_euler.z
    for o in dst.objects:
        bpy.data.objects.remove(o, do_unlink=True)
    to_local = Matrix.Rotation(-(spot_yaw + math.pi), 3, "Z")
    loc = {n: to_local @ (p - world["spot_terence"]) for n, p in world.items()}
    return {
        "DRUMMER_HEAD": Vector((loc["spot_drummer"].x, loc["spot_drummer"].y, 1.15)),
        "NEAR_HEAD": Vector((0.0, -NEAR_DIST, 1.15)),
        "STOOL": Vector((loc["stool_terence"].x, loc["stool_terence"].y, 0.0)),
        "DOOR": Vector((loc["door_main"].x, loc["door_main"].y, 0.0)),
        "SAXES": loc["seat_sax_3"] + Vector((0, 0, 1.1)),
        "TROMBONES": loc["seat_trombone_2"] + Vector((0.4, 0, 1.3)),
        "TRUMPETS": loc["seat_trumpet_3"] + Vector((0, 0, 1.5)),
        "STAND": Vector((0.0, -0.5, 0.0)),
    }


PROP_FILES = {"chair_folded": "chair_folded", "cowbell": "cowbell", "spare_drum": "spare_drum", "cymbal": "cymbal",
              "sheet_music_pages": "sheet_music_pages", "music_stand_knockable": "music_stand_knockable"}


def _sample_tris(tris, spacing=0.03):
    pts = []
    for a, b, c in tris:
        n = max(1, int(math.ceil(max((b - a).length, (c - a).length, (c - b).length) / spacing)))
        for i in range(n + 1):
            for j in range(n + 1 - i):
                u, v = i / n, j / n
                pts.append(a + (b - a) * u + (c - a) * v)
    return np.array([tuple(p) for p in pts])


def read_prop(path):
    """Empties/objects of a prop glb relative to its root, plus surface point clouds of its meshes."""
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=str(path))
    new = [o for o in bpy.data.objects if o not in before]
    roots = [o for o in new if o.parent is None and not o.name.startswith("Icosphere")]
    if len(roots) != 1:
        raise RuntimeError(f"{path.name}: expected one root, got {[o.name for o in roots]}")
    root = roots[0]
    inv = root.matrix_world.inverted()
    mats, meshes = {}, {}
    for o in new:
        if o.name.startswith("Icosphere"):
            continue
        key = o.name.split(".")[0]
        if key not in mats:
            mats[key] = inv @ o.matrix_world
        if o.type == "MESH":
            me = o.data
            me.calc_loop_triangles()
            M = inv @ o.matrix_world
            meshes[key] = [tuple(M @ me.vertices[i].co for i in t.vertices) for t in me.loop_triangles]
    for o in new:
        bpy.data.objects.remove(o, do_unlink=True)
    return mats, meshes


def load_props():
    """name -> {"grips": {obj: Matrix rel. to the prop bone}, "cloud": Nx3 points rel. to the prop bone}."""
    props = {}
    for key, fname in PROP_FILES.items():
        mats, meshes = read_prop(R / "props" / f"{fname}.glb")
        if key == "cowbell":
            stick_m = mats["cowbell_stick"]
            sinv = stick_m.inverted()
            props["cowbell_stick"] = {"grips": {"grip_r": sinv @ mats["grip_r"]},
                                      "cloud": _sample_tris([tuple(sinv @ p for p in t) for t in meshes["cowbell_stick"]])}
            props["cowbell"] = {"grips": {"grip_l": mats["grip_l"]}, "cloud": _sample_tris(meshes["cowbell"])}
        else:
            props[key] = {"grips": mats, "cloud": _sample_tris([t for m in meshes.values() for t in m])}
    return props


# ---------------------------------------------------------------------------
# Small math
# ---------------------------------------------------------------------------
def Rm(x=0, y=0, z=0):
    return Euler((math.radians(x), math.radians(y), math.radians(z)), "XYZ").to_matrix().to_4x4()


def Tm(v):
    return Matrix.Translation(Vector(v))


def V(*a):
    return Vector(a if len(a) == 3 else a[0])


def lerp(a, b, t):
    return a + (b - a) * t


def smooth(t):
    t = min(max(t, 0.0), 1.0)
    return t * t * (3 - 2 * t)


def yaw_rot(deg):
    return Matrix.Rotation(math.radians(deg), 3, "Z")


def heading(deg):
    """Unit horizontal direction for a yaw (0 = -Y, + toward his left)."""
    a = math.radians(deg)
    return Vector((math.sin(a), -math.cos(a), 0.0))


def yaw_of(v):
    return math.degrees(math.atan2(v.x, -v.y))


# ---------------------------------------------------------------------------
# Curves: monotone cubic Hermite through keys. "hit" keys arrive at speed and stop dead,
# "snap" keys leave at speed. Loops wrap tangents around the period.
# ---------------------------------------------------------------------------
HIT_K = 2.4


def _slope(t0, v0, t1, v1):
    return (v1 - v0) / (t1 - t0)


def _mono(dl, dr, hl, hr):
    """Fritsch-Butland tangent from the slopes either side, zero where the curve turns."""
    w1, w2 = 2 * hr + hl, hr + 2 * hl
    with np.errstate(divide="ignore", invalid="ignore"):
        m = (w1 + w2) / (w1 / dl + w2 / dr)
    return np.where(dl * dr > 0, m, 0.0)


class Curve:
    """ts: key frames, get(i) -> np.array value at key i (may be lazy), modes: per-key ease."""

    def __init__(self, ts, get, modes, period=None):
        self.ts, self.get, self.modes, self.period = ts, get, modes, period
        self.n = len(ts)
        self._cache = {}

    def _key(self, i):
        n, P = self.n, self.period
        if 0 <= i < n:
            return self.ts[i], self._val(i)
        if P is None:
            return None
        if i < 0:
            return self.ts[i + n - 1] - P, self._val(i + n - 1)
        return self.ts[i - n + 1] + P, self._val(i - n + 1)

    def _val(self, i):
        if i not in self._cache:
            self._cache[i] = np.asarray(self.get(i), float)
        return self._cache[i]

    def _tan(self, i):
        t, v = self._key(i)
        prev, nxt = self._key(i - 1), self._key(i + 1)
        mode = self.modes[i]
        dl = _slope(prev[0], prev[1], t, v) if prev else None
        dr = _slope(t, v, nxt[0], nxt[1]) if nxt else None
        if dl is None or dr is None:
            base = np.zeros_like(v)
        else:
            base = _mono(dl, dr, t - prev[0], nxt[0] - t)
        m_in = m_out = base
        if mode == "hit":
            m_in = HIT_K * dl if dl is not None else base
            m_out = np.zeros_like(v)
        elif mode == "snap":
            m_in = np.zeros_like(v)
            m_out = HIT_K * dr if dr is not None else base
        elif mode == "hold":
            m_in = m_out = np.zeros_like(v)
        return m_in, m_out

    def seg(self, f):
        ts = self.ts
        if f <= ts[0]:
            return 0, 0.0
        if f >= ts[-1]:
            return self.n - 2, 1.0
        for i in range(self.n - 1):
            if ts[i] <= f <= ts[i + 1]:
                return i, (f - ts[i]) / (ts[i + 1] - ts[i])
        raise ValueError(f)

    def __call__(self, f):
        self._cache = {}
        i, s = self.seg(f)
        t0, v0 = self._key(i)
        t1, v1 = self._key(i + 1)
        h = t1 - t0
        _, m0 = self._tan(i)
        m1, _ = self._tan(i + 1)
        s2, s3 = s * s, s * s * s
        return ((2 * s3 - 3 * s2 + 1) * v0 + (s3 - 2 * s2 + s) * h * m0 +
                (-2 * s3 + 3 * s2) * v1 + (s3 - s2) * h * m1)


# ---------------------------------------------------------------------------
# Pose specs
# ---------------------------------------------------------------------------
def sc(v):
    return Vector(v) * SC


# Right-arm presets in canonical rest space (standing at the origin): wrist, hand direction, shape,
# elbow pole. The left arm mirrors x. Presets ride the posed UpperChest, so they lean and turn with him.
PRESETS = {
    "down": ((-0.285, -0.03, 0.83), (-0.12, -0.15, -1), "hang", (-0.3, 0.6, -1)),
    "hip": ((-0.255, 0.02, 1.00), (0.25, -1, -0.35), "relaxed", (-1, 0.5, 0)),
    "chest": ((-0.10, -0.32, 1.22), (0.15, -0.25, 1), "pinch", (-0.6, 0.4, -1)),
    "cross": ((0.10, -0.25, 1.22), (1, 0.2, 0.1), "relaxed", (-0.8, -0.3, -1)),
    "behind": ((-0.09, 0.20, 0.98), (1, 0, -0.3), "relaxed", (-0.5, 0.8, -0.4)),
    "up": ((-0.17, -0.30, 1.78), (0, 0, 1), "open", (-1, 0.3, -0.3)),
    "finger_up": ((-0.17, -0.38, 1.48), (0, -0.1, 1), "point", (-1, 0.3, -0.5)),
    "finger_beat": ((-0.17, -0.42, 1.40), (0, -0.4, 1), "point", (-1, 0.3, -0.5)),
    "fist_high": ((-0.19, -0.37, 1.62), (0, -0.1, 1), "fist", (-1, 0.3, -0.4)),
    "open_high": ((-0.21, -0.42, 1.52), (0, -0.2, 1), "open", (-1, 0.3, -0.4)),
    "clap": ((-0.03, -0.42, 1.27), (0.25, -0.4, 1), "open", (-1, 0.2, -0.6)),
    "clap_open": ((-0.19, -0.40, 1.27), (0.6, -0.3, 1), "open", (-1, 0.2, -0.6)),
    "watch": ((0.03, -0.35, 1.30), (1, -0.1, 0.1), "relaxed", (-0.8, 0.2, -1)),   # mirrored for the left
    "watch_tap": ((0.04, -0.33, 1.37), (0.5, 0, -1), "point", (-0.8, 0.3, -0.6)),
    "c1": ((-0.17, -0.44, 1.08), (0, -0.6, 0.6), "pinch", (-1, 0.4, -0.6)),
    "c2": ((0.02, -0.44, 1.20), (0.3, -0.6, 0.6), "pinch", (-1, 0.4, -0.6)),
    "c3": ((-0.38, -0.40, 1.20), (-0.3, -0.6, 0.6), "pinch", (-1, 0.4, -0.6)),
    "c4": ((-0.17, -0.40, 1.42), (0, -0.4, 1), "pinch", (-1, 0.4, -0.6)),
    "rebound": ((-0.15, -0.44, 1.22), (0, -0.6, 0.8), "pinch", (-1, 0.4, -0.6)),
    "palms_down": ((-0.25, -0.42, 1.08), (0.2, -1, 0), "open", (-1, 0.4, -0.4)),
    "flick_a": ((-0.15, -0.38, 1.30), (0, -0.5, 1), "open", (-1, 0.3, -0.6)),
    "flick_b": ((-0.28, -0.42, 1.36), (-1, -0.5, 0.5), "open", (-1, 0.3, -0.6)),
    "wave_out": ((-0.48, -0.24, 1.10), (-1, -0.2, -0.2), "open", (-1, 0.3, -0.2)),
    "fist_out": ((-0.36, -0.32, 1.08), (-0.5, -1, 0), "fist", (-1, 0.5, -0.5)),
    "fist_cock": ((-0.30, -0.10, 1.16), (0, -0.4, 1), "fist", (-1, 0.6, -0.4)),
    "rage": ((-0.30, -0.36, 1.36), (0.3, -0.4, 1), "fist", (-1, 0.2, -0.3)),
    "beckon": ((-0.10, -0.48, 1.20), (0, -1, 0.2), "point", (-1, 0.4, -0.8)),
    "beckon_curl": ((-0.10, -0.46, 1.22), (0, -1, 0.3), "fist", (-1, 0.4, -0.8)),
    "pump_fwd": ((-0.21, -0.32, 1.05), (0, -1, 0.3), "fist", (-0.6, 0.8, -0.6)),
    "pump_back": ((-0.25, 0.12, 0.93), (0, 0.3, -1), "fist", (-0.6, 0.8, -0.6)),
    "thighs": ((-0.15, -0.26, 0.76), (0, -1, -0.25), "relaxed", (-0.7, 0.3, -1)),
    "balance": ((-0.46, -0.06, 1.08), (-1, -0.3, -0.3), "open", (-1, 0.4, -0.3)),
}

SHAPES = {"open": (0, 0, 0, 0, 0), "relaxed": (0.25, 0.3, 0.35, 0.4, 0.2), "hang": (0.12, 0.15, 0.2, 0.25, 0.1), "grip": (0.6, 0.65, 0.7, 0.7, 0.5),
          "fist": (1, 1, 1, 1, 0.9), "point": (0, 1, 1, 1, 0.8), "pinch": (0.55, 0.5, 0.5, 0.45, 0.7),
          "claw": (0.45, 0.5, 0.55, 0.6, 0.3)}


def mirror(v):
    return Vector((-v[0], v[1], v[2]))


def pr(name, off=(0, 0, 0), shape=None):
    return ("preset", name, off, shape)


def at(p, shape="relaxed", hdir=None, pole=None, contact=False):
    """World wrist target. contact=True: the hand must actually get there (checked for reach)."""
    return ("at", Vector(p), shape, hdir, pole, contact)


def on_bone(bone, off, shape="relaxed", hdir=(0, -1, -0.4), pole=None, along=0.0):
    """Wrist on a posed bone (head, or `along` the way to its tail) plus a world offset: hands on his own legs."""
    return ("bone", bone, Vector(off), shape, hdir, pole, along)


def pt(p, shape="point"):
    return ("point", Vector(p), shape)


def grip(prop, g="grip_r", shape="grip", pole=None):
    """Hand on a prop's grip empty. pole: elbow direction (default out to his side and down)."""
    return ("grip", prop, g, shape, pole)


BODY_CH = dict(hips=(0, 0, 0), yaw=0.0, lean=0.0, hpitch=0.0, side=0.0, twist=0.0, hroll=0.0, shl=0.0, shr=0.0,
               hunch=0.0, jut=0.0, nod=0.0, shake=0.0, life=1.0)
STANCE_CH = dict(base=(0, 0), byaw=0.0, spread=0.0, stepl=(0, 0), stepr=(0, 0), toes=0.0)
IDLE = dict(L=pr("down"), R=pr("down"), look=(0.0, 0.0, 0.0))


class Seq:
    """Keys that inherit the previous key's channels: s.k(frame, ease, **changes)."""

    def __init__(self, **base):
        self.cur = {**BODY_CH, **STANCE_CH, **IDLE, **base}
        self.keys = []

    def k(self, f, ease=None, **ch):
        unknown = set(ch) - set(self.cur) - {"feet"}
        if unknown:
            raise KeyError(f"unknown pose channels {unknown}")
        self.cur = {**self.cur, **ch}
        if self.keys and f <= self.keys[-1][0]:
            raise ValueError(f"keys out of order at {f}")
        self.keys.append((f, dict(self.cur), ease))
        return self


class Move:
    def __init__(self, length, loop=False, seq=None, fn=None, props=None, timing=None):
        if (seq is None) == (fn is None):
            raise ValueError("a move needs exactly one of seq or fn")
        if seq is not None and seq.keys[-1][0] != length:
            raise ValueError(f"last key {seq.keys[-1][0]} != length {length}")
        self.length, self.loop, self.seq, self.fn = length, loop, seq, fn
        self.props = props or {}
        self.timing = timing or {}


# ---------------------------------------------------------------------------
# Director: turns a pose (channels + arm/look specs) into bone transforms
# ---------------------------------------------------------------------------
REST_FOOT = None
ANKLE_Z = 0.09 * SC
LEG_LEN = None


def stance_feet(d):
    """Ankle positions [L, R] for a pose dict: explicit 'feet', or the stance channels."""
    if "feet" in d and d["feet"] is not None:
        return [Vector(d["feet"][0]), Vector(d["feet"][1])], [d.get("byaw", 0.0)] * 2
    base = Vector((d["base"][0], d["base"][1], 0.0))
    ry = yaw_rot(d["byaw"])
    out = []
    for sx, step in ((1, d["stepl"]), (-1, d["stepr"])):
        p = base + ry @ Vector((sx * (ps2.HIP_X * SC + d["spread"]), 0, 0)) + Vector((step[0], step[1], ANKLE_Z))
        out.append(p)
    return out, [d["byaw"] + d["toes"], d["byaw"] - d["toes"]]          # toes out turn the knees out with them


class Director:
    def __init__(self, rig, targets, props):
        self.rig = rig
        self.t = targets
        self.props = props
        self.prop_world = {}
        self.max_drop = 0.0
        self.short = []           # (move, frame, side, metres short) for contact/grip targets out of reach
        self.move_name = ""
        self.frame = 0
        pb = rig.pose.bones
        global REST_FOOT, LEG_LEN
        lf = rig.data.bones["LeftFoot"]
        REST_FOOT = (lf.tail_local - lf.head_local).normalized()
        LEG_LEN = rig.data.bones["LeftUpperLeg"].length + rig.data.bones["LeftLowerLeg"].length
        self.arm_reach = pb["LeftUpperArm"].length + pb["LeftLowerArm"].length

    # ---- body ----
    def apply_body(self, b, feet, fyaw, lift=(0.0, 0.0)):
        rig = self.rig
        ps2.reset_pose(rig)
        hips = Vector(b["hips"])
        yaw = b["yaw"]
        ps2.move_hips(rig, hips)
        ry = yaw_rot(yaw)
        hp = rig.pose.bones["Hips"]
        Rh = (Matrix.Rotation(math.radians(yaw), 4, Z) @ Matrix.Rotation(math.radians(b["hroll"]), 4, Vector((0, -1, 0)))
              @ Matrix.Rotation(math.radians(b["hpitch"]), 4, X))          # pelvis tips forward: stooping from the hips
        hp.matrix = Tm(hp.head) @ Rh @ Tm(-hp.head) @ hp.matrix
        bpy.context.view_layer.update()
        # legs can reach? drop the hips just enough (a planted foot never floats)
        drop = 0.0
        for i, sd in enumerate(("Left", "Right")):
            hj = rig.pose.bones[f"{sd}UpperLeg"].head
            a = feet[i]
            horiz = (a - hj).xy.length
            L = LEG_LEN * 0.995
            if (a - hj).length > L:
                need = (hj.z - a.z) - math.sqrt(max(L * L - horiz * horiz, 0.0))
                drop = max(drop, need)
        if drop > 0:
            if drop > 0.12:
                raise RuntimeError(f"{self.move_name} f{self.frame}: feet {drop:.2f} m out of reach")
            self.max_drop = max(self.max_drop, drop)
            ps2.move_hips(rig, hips - Vector((0, 0, drop)))
        # spine: lean (forward), side bend (+ toward his left), twist (+ toward his left)
        for bone, kl, kt, ks in (("Spine", 0.40, 0.25, 0.40), ("Chest", 0.35, 0.35, 0.35), ("UpperChest", 0.25, 0.40, 0.25)):
            pb = rig.pose.bones[bone]
            M = pb.matrix
            up, sx, fw = M.col[1].xyz.normalized(), M.col[0].xyz.normalized(), M.col[2].xyz.normalized()
            lean = b["lean"] * kl
            if bone == "UpperChest":
                lean -= b["breath"] * 1.4
            Rb = (Matrix.Rotation(math.radians(b["twist"] * kt), 4, up) @ Matrix.Rotation(math.radians(lean), 4, sx)
                  @ Matrix.Rotation(math.radians(-b["side"] * ks), 4, fw))
            pb.matrix = Tm(pb.head) @ Rb @ Tm(-pb.head) @ M
            bpy.context.view_layer.update()
        # shoulders: raise (shl/shr, plus breath) and hunch forward
        uc = rig.pose.bones["UpperChest"].matrix
        cfw, cup = uc.col[2].xyz.normalized(), uc.col[1].xyz.normalized()
        for sd, raise_, sgn in (("Left", b["shl"], 1), ("Right", b["shr"], -1)):
            pb = rig.pose.bones[f"{sd}Shoulder"]
            r = raise_ + b["breath"] * 1.2
            Rs = Matrix.Rotation(math.radians(sgn * r), 4, cfw) @ Matrix.Rotation(math.radians(-sgn * b["hunch"]), 4, cup)
            pb.matrix = Tm(pb.head) @ Rs @ Tm(-pb.head) @ pb.matrix
        bpy.context.view_layer.update()
        # legs
        for i, (sd, sx) in enumerate((("Left", 1), ("Right", -1))):
            fy = yaw_rot(fyaw[i])
            pole = fy @ Vector((sx * 0.25, -1, 0.1))
            ps2.ik_leg(rig, sd, feet[i], pole)
            fd = fy @ REST_FOOT
            if lift[i] > 0:        # toes drop a little while the foot swings
                fd = (fd + Vector((0, 0, -lift[i] * 2.0))).normalized()
            ps2.aim(rig, f"{sd}Foot", fd)

    # ---- arms ----
    def resolve_arm(self, sd, spec, grip_time=None):
        """Wrist target, hand direction, elbow pole and finger curls for an arm spec, under the
        current body. A grip resolves against the prop at grip_time (the end of its hold), so
        an arm blending away from a released prop never chases it."""
        rig = self.rig
        sx = 1 if sd == "Left" else -1
        kind = spec[0]
        if kind == "mix":
            k = spec[3]
            return (1 - k) * self.resolve_arm(sd, spec[1], grip_time) + k * self.resolve_arm(sd, spec[2], grip_time)
        up = rig.pose.bones[f"{sd}UpperArm"]
        contact = False
        if kind == "preset":
            wrist, hdir, shape, pole = PRESETS[spec[1]]
            off = Vector(spec[2]) if len(spec) > 2 else Vector()
            wrist, hdir, pole = sc(wrist), Vector(hdir), Vector(pole)
            off = off * SC
            if sd == "Left":
                wrist, hdir, pole, off = mirror(wrist), mirror(hdir), mirror(pole), mirror(off)
            ucb = rig.pose.bones["UpperChest"]
            Mc = ucb.matrix @ ucb.bone.matrix_local.inverted()
            target = Mc @ (wrist + off)
            hdir = Mc.to_3x3() @ hdir
            pole = Mc.to_3x3() @ pole
            if len(spec) > 3 and spec[3]:
                shape = spec[3]
        elif kind == "at":
            target = Vector(spec[1])
            shape = spec[2]
            hdir = Vector(spec[3]) if spec[3] is not None else (target - up.head)
            pole = Vector(spec[4]) if spec[4] is not None else Vector((sx * 0.8, 0.4, -1))
            contact = spec[5]
        elif kind == "bone":
            bpb = rig.pose.bones[spec[1]]
            target = bpb.head.lerp(bpb.tail, spec[6]) + Vector(spec[2])
            shape, hdir = spec[3], Vector(spec[4])
            pole = Vector(spec[5]) if spec[5] is not None else Vector((sx * 0.8, 0.2, 0.6))
            contact = True
        elif kind == "point":
            v = (Vector(spec[1]) - up.head).normalized()
            target = up.head + v * self.arm_reach * 0.93
            hdir, shape = v, spec[2]
            pole = Vector((sx * 0.4, 0.3, -1))
        elif kind == "grip":
            name, g = spec[1], spec[2]
            if name not in self.prop_world:
                raise RuntimeError(f"{self.move_name} f{self.frame}: grip on {name}, which is not posed")
            Mp = self.prop_world[name] if grip_time is None or grip_time >= self.frame else self.prop_fn[name](grip_time)
            gp = Mp @ self.props[name]["grips"][g].translation
            target = gp + (up.head - gp).normalized() * (0.068 * SC * HAND_SCALE)
            hdir = gp - target
            shape = spec[3]
            pole = Vector(spec[4]) if spec[4] is not None else Vector((sx * 0.9, 0.4, -0.8))
            contact = True
        else:
            raise ValueError(spec)
        self.last_contact = (contact, kind)
        curls = SHAPES[shape] if isinstance(shape, str) else shape
        return np.array(tuple(target) + tuple(hdir.normalized()) + tuple(pole.normalized()) + tuple(curls))

    def apply_arm(self, sd, a):
        rig = self.rig
        target, hdir, pole, curls = Vector(a[0:3]), Vector(a[3:6]), Vector(a[6:9]), a[9:14]
        ps2.ik_arm(rig, sd, target, pole)
        ps2.aim(rig, f"{sd}Hand", hdir)
        pbs = rig.pose.bones
        for fname, c in zip(ps2.FINGER_NAMES, curls[:4]):
            for part, k in (("Proximal", 1.0), ("Intermediate", 1.1), ("Distal", 0.8)):
                pbs[f"{sd}{fname}{part}"].rotation_quaternion = Quaternion((1, 0, 0), math.radians(-80 * c * k))
        for part in ("Proximal", "Distal"):
            pbs[f"{sd}Thumb{part}"].rotation_quaternion = Quaternion((0, 0, 1), math.radians((50 if sd == "Left" else -50) * curls[4]))

    # ---- head ----
    def resolve_look(self, spec):
        if spec[0] != "at":
            return np.array([spec[0], spec[1], spec[2] if len(spec) > 2 else 0.0])
        rig = self.rig
        point = Vector(spec[1])
        roll = spec[2] if len(spec) > 2 else 0.0
        head = rig.pose.bones["Head"].head
        uc = rig.pose.bones["UpperChest"].matrix
        fw, up = uc.col[2].xyz, uc.col[1].xyz
        fh = Vector((fw.x, fw.y, 0)).normalized()
        facing = yaw_of(fh)
        v = point - head
        rel = (yaw_of(v) - facing + 180) % 360 - 180
        chest_pitch = math.degrees(math.atan2(up.dot(fh), up.z))
        pitch = math.degrees(math.atan2(v.z, max(v.xy.length, 1e-4))) + chest_pitch
        return np.array([max(-100, min(100, rel)), max(-60, min(45, pitch)), roll])

    def apply_look(self, ang, jut, nod):
        rig = self.rig
        ps2.look(rig, ang[0], ang[1] + nod, ang[2], 0.0)
        if jut:
            for bone, k in (("Neck", 1.0), ("Head", -1.15)):
                pb = rig.pose.bones[bone]
                sx = (pb.matrix.to_3x3() @ X).normalized()
                ps2.turn(rig, bone, sx, jut * k)

    # ---- props ----
    def check_reach(self, sd, a, kind):
        up = self.rig.pose.bones[f"{sd}UpperArm"]
        short = (Vector(a[0:3]) - up.head).length - self.arm_reach * 0.999
        if short > 0.015:
            self.short.append((self.move_name, self.frame, sd, round(short, 3), kind))

    def pose_props(self, move, f):
        self.prop_world = {}
        self.prop_fn = {name: fn for name, fn in move.props.values()}
        for bone, (name, fn) in move.props.items():
            M = Matrix(fn(f))
            ps2.set_bone_matrix(self.rig, bone, M)
            self.prop_world[name] = M


def is_contact(spec):
    return spec[0] in ("grip", "bone") or (spec[0] == "at" and spec[5])


def life_overlay(move, f):
    """Breathing and weight sway: periodic over a loop, faded in and out on one-shots."""
    N = move.length
    if move.loop:
        pb = N / max(1, round(N / 66))
        ps_ = N / max(1, round(N / 132))
        env = 1.0
    else:
        pb, ps_ = 66.0, 132.0
        env = smooth(f / 10.0) * smooth((N - f) / 10.0)
    br = 0.5 + 0.5 * math.sin(2 * math.pi * f / pb)
    sw = math.sin(2 * math.pi * f / ps_)
    return env * br, env * sw


def _shake(f, amp, seed):
    return Vector((math.sin(f * 2.3 + seed), math.sin(f * 2.9 + seed * 1.7), math.sin(f * 3.7 + seed * 0.6))) * amp


def evaluate(D, move, f):
    """Pose the rig for frame f of a move."""
    D.frame = f
    if move.fn is not None:
        d = {**BODY_CH, **STANCE_CH, **IDLE, **move.fn(f)}
        body = {k: (np.array(d[k], float) if isinstance(d[k], tuple) else float(d[k])) for k in BODY_CH}
        feet, fyaw = stance_feet(d)
        lift = d.get("lift", (0.0, 0.0))
        arm_get = {sd: (lambda spec=d[key], sd=sd: D.resolve_arm(sd, spec)) for sd, key in (("Left", "L"), ("Right", "R"))}
        look_get = lambda spec=d["look"]: D.resolve_look(spec)
        arm_val = lambda sd: arm_get[sd]()
        look_val = look_get
        contact_now = {sd: is_contact(d[key]) for sd, key in (("Left", "L"), ("Right", "R"))}
    else:
        keys = move.seq.keys
        ts = [k[0] for k in keys]
        modes = [k[2] for k in keys]
        period = move.length if move.loop else None
        body = {}
        for ch in BODY_CH:
            vals = [np.atleast_1d(np.array(k[1][ch], float)) for k in keys]
            c = Curve(ts, lambda i, vals=vals: vals[i], modes, period)
            v = c(f)
            body[ch] = v if len(v) > 1 else float(v[0])
        resolved = [stance_feet(k[1]) for k in keys]
        fvals = [np.array(tuple(r[0][0]) + tuple(r[0][1]) + tuple(r[1])) for r in resolved]
        fc = Curve(ts, lambda i: fvals[i], modes, period)
        fv = fc(f)
        feet = [Vector(fv[0:3]), Vector(fv[3:6])]
        fyaw = [fv[6], fv[7]]
        i, s = fc.seg(f)
        j = (i + 1)
        lift = [0.0, 0.0]
        for side in (0, 1):
            p0, p1 = Vector(fvals[i][side * 3:side * 3 + 3]), Vector(fvals[j][side * 3:side * 3 + 3])
            dist = (p1 - p0).length
            if dist > 0.03:
                h = min(0.10, 0.35 * dist) * math.sin(math.pi * s)
                feet[side] = feet[side] + Vector((0, 0, h))
                lift[side] = h
        curves = {}
        contact_now = {}
        for sd, key in (("Left", "L"), ("Right", "R")):
            specs = [k[1][key] for k in keys]
            run_end = []
            for i_, sp in enumerate(specs):       # last frame of the run of grips on the same prop
                j_ = i_
                while sp[0] == "grip" and j_ + 1 < len(specs) and specs[j_ + 1][0] == "grip" and specs[j_ + 1][1] == sp[1]:
                    j_ += 1
                run_end.append(ts[j_] if sp[0] == "grip" else None)
            curves[sd] = Curve(ts, lambda i, specs=specs, run_end=run_end, sd=sd: D.resolve_arm(sd, specs[i], run_end[i]), modes, period)
            contact_now[sd] = any(t_ == f and is_contact(specs[i_]) for i_, t_ in enumerate(ts))
        lc = Curve(ts, lambda i: D.resolve_look(keys[i][1]["look"]), modes, period)
        arm_val = lambda sd: curves[sd](f)
        look_val = lambda: lc(f)
    br, sw = life_overlay(move, f)
    life = body["life"]
    body["breath"] = br * life
    body["hips"] = np.array(body["hips"]) + np.array((0.010 * sw * life, 0, 0))
    body["hroll"] = body["hroll"] + 1.0 * sw * life
    body["side"] = body["side"] - 0.8 * sw * life
    D.apply_body(body, feet, fyaw, lift)
    D.pose_props(move, f)
    for n, sd in enumerate(("Left", "Right")):
        a = arm_val(sd)
        if body["shake"]:
            a = a.copy()
            a[0:3] += np.array(_shake(f, body["shake"], 1.3 + n * 2.1))
        if contact_now[sd]:
            D.check_reach(sd, a, "contact")
        D.apply_arm(sd, a)
    bpy.context.view_layer.update()
    D.apply_look(look_val(), body["jut"], body["nod"])
    bpy.context.view_layer.update()


# ---------------------------------------------------------------------------
# Prop tracks
# ---------------------------------------------------------------------------
class Track:
    """Prop transform keys: (frame, location, euler degrees XYZ, ease) or (frame, Matrix, ease).
    Monotone cubic on location and euler angles; matrices get eulers compatible with the previous
    key, so a track never spins the long way round."""

    def __init__(self, keys, pivot=None):
        """pivot: a point in the prop's frame (a grip) whose world path is interpolated instead of the origin's."""
        self.pivot = Vector(pivot) if pivot is not None else Vector()
        rows, prev = [], None
        for k in keys:
            if isinstance(k[1], Matrix):
                e = k[1].to_euler("XYZ", prev) if prev is not None else k[1].to_euler("XYZ")
                rows.append((k[0], k[1] @ self.pivot, tuple(math.degrees(a) for a in e), k[2] if len(k) > 2 else None))
                prev = e
            else:
                rows.append((k[0], k[1], k[2], k[3] if len(k) > 3 else None))
                prev = Euler(tuple(math.radians(a) for a in k[2]), "XYZ")
        self.keys = rows
        ts = [k[0] for k in rows]
        vals = [np.array(tuple(k[1]) + tuple(k[2]), float) for k in rows]
        keys = rows
        self.c = Curve(ts, lambda i: vals[i], [k[3] if len(k) > 3 else None for k in keys])

    def __call__(self, f):
        v = self.c(f)
        return Tm(v[0:3]) @ Rm(*v[3:6]) @ Tm(-self.pivot)


# ---------------------------------------------------------------------------
# Walking
# ---------------------------------------------------------------------------
def walk_fn(path, frames, step=0.58, look=None, arms="swing", extra=None, end_yaw=None, stomp=0.0, lean=4.0,
            swing_amp=0.12):
    """Pose function for walking along a polyline of local (x, y) points over `frames`. Footfalls
    alternate left/right every `step` metres, each foot planted 0.11 m to its side of the path; the
    hips stay centred between the planted feet and drop just enough for the legs to reach; the swing
    foot eases forward and lifts; pelvis and chest counter-rotate; arms counter-swing (or hold a
    preset, or pump). stomp > 0 makes each heel strike land hard. Closed paths loop seamlessly."""
    pts = [Vector((p[0], p[1], 0)) for p in path]
    closed = (pts[0] - pts[-1]).length < 1e-6
    seglen = [(pts[i + 1] - pts[i]).length for i in range(len(pts) - 1)]
    total = sum(seglen)
    n = max(2, int(round(total / step / 2)) * 2)
    step = total / n
    half = 0.11 * SC
    drop = LEG_LEN - math.sqrt(max(LEG_LEN ** 2 - (step / 2) ** 2, 0.0)) + 0.012

    def at_(dist):
        dist = dist % total if closed else max(0.0, min(total, dist))
        for i, L in enumerate(seglen):
            if dist <= L or i == len(seglen) - 1:
                t = dist / L if L else 0
                return lerp(pts[i], pts[i + 1], min(t, 1.0)), (pts[i + 1] - pts[i]).normalized()
            dist -= L

    def left_of(dv):
        return Vector((-dv.y, dv.x, 0))

    plants = []
    for i in range(n + 1):
        p, dv = at_(step * i)
        sgn = 1 if i % 2 == 0 else -1          # even footfalls are the left foot
        plants.append(p + left_of(dv) * (half * sgn) + Vector((0, 0, ANKLE_Z)))
    p0, dv0 = at_(0.0)
    start_right = p0 - left_of(dv0) * half + Vector((0, 0, ANKLE_Z))

    def plant(i):
        if i < 0:
            return plants[n + i] if closed else start_right
        return plants[i]

    def fn(f):
        u = min(max(f / frames, 0.0), 1.0)
        j = min(int(u * n), n - 1)
        fr = u * n - j
        hips_dist = (j + fr - 0.5) * step
        p, dv = at_(hips_dist)
        hd = at_(hips_dist + 0.15)[0] - at_(hips_dist - 0.15)[0]
        yaw = yaw_of(hd.normalized() if hd.length > 1e-6 else dv)
        if end_yaw is not None and u > 0.85:
            yaw = lerp(yaw, end_yaw, smooth((u - 0.85) / 0.15))
        sm = smooth(fr)
        lift = 0.085 * SC * math.sin(math.pi * fr) ** 1.2
        moving_left = (j + 1) % 2 == 0
        swing_pos = lerp(plant(j - 1), plant(j + 1), sm) + Vector((0, 0, lift))
        support = plant(j)
        feet = [swing_pos, support] if moving_left else [support, swing_pos]
        lifts = (lift, 0.0) if moving_left else (0.0, lift)
        phase = math.pi * (j + fr)
        swing = math.cos(phase)                      # how far forward the left foot is (-1 back .. 1 front)
        bob = 0.016 * math.sin(math.pi * fr) - drop
        if stomp:
            bob -= stomp * math.exp(-((fr - 0.04) / 0.08) ** 2)
        over = math.sin(math.pi * fr) * (-1 if moving_left else 1)       # weight over the support foot
        side_shift = left_of(dv) * (0.018 * over)
        d = dict(hips=(p.x + side_shift.x, p.y + side_shift.y, bob), yaw=yaw - 5.0 * swing, twist=9.0 * swing,
                 hroll=2.5 * over, feet=feet, lift=lifts, lean=lean, byaw=yaw, life=0.0)
        if arms == "swing":
            d["L"] = pr("down", (0, swing_amp * swing, 0.02 * abs(swing)))
            d["R"] = pr("down", (0, -swing_amp * swing, 0.02 * abs(swing)))
        elif arms == "pump":
            k = 0.5 + 0.5 * swing
            d["L"] = ("mix", pr("pump_back"), pr("pump_fwd"), k)
            d["R"] = ("mix", pr("pump_fwd"), pr("pump_back"), k)
        else:
            d["L"] = pr(arms)
            d["R"] = pr(arms)
        d["look"] = (look(u) if callable(look) else look) if look is not None else (0.0, -6.0 * math.sin(phase * 2) * 0.2, 0.0)
        d["nod"] = 1.5 * math.cos(2 * phase)          # small head bob against the hips
        if extra:
            d.update(extra(u))
        return d

    fn.n_steps = n
    fn.step = step
    return fn


def walk_in_place_fn(frames=24, stride=0.72):
    """Walk in place: the hips stay over the origin, the support foot slides back under him at
    walking speed (2 * stride / cycle), so Godot moving the node at that speed plants the feet."""
    half = 0.11 * SC
    drop = LEG_LEN - math.sqrt(max(LEG_LEN ** 2 - (stride / 2) ** 2, 0.0)) + 0.012

    def foot(phase):
        """phase 0..1 for one foot: stance 0..0.55 (front to back), swing 0.55..1 (back to front)."""
        st = 0.55
        if phase < st:
            y = lerp(-stride / 2, stride / 2, phase / st)       # forward is -Y
            return y, 0.0
        s = (phase - st) / (1 - st)
        y = lerp(stride / 2, -stride / 2, smooth(s))
        return y, 0.09 * SC * math.sin(math.pi * s) ** 1.2

    def fn(f):
        ph = (f % frames) / frames
        yl, ll = foot(ph)
        yr, lr = foot((ph + 0.5) % 1.0)
        swing = -yl / (stride / 2)                      # how far forward the left foot is (-1 back .. 1 front)
        bob = -drop + 0.022 * (0.5 - 0.5 * math.cos(4 * math.pi * ph + 0.6))
        feet = [Vector((half, yl, ANKLE_Z + ll)), Vector((-half, yr, ANKLE_Z + lr))]
        over = math.sin(2 * math.pi * ph)               # + while the left foot carries him
        d = dict(hips=(0.018 * over, 0.0, bob), yaw=-5.0 * swing, twist=9.0 * swing,
                 hroll=2.0 * over, feet=feet, lift=(ll, lr), lean=5.0, life=0.0,
                 L=pr("down", (0, 0.13 * swing, 0.03 * abs(swing))), R=pr("down", (0, -0.13 * swing, 0.03 * abs(swing))),
                 look=(0.0, 0.0, 0.0), nod=1.8 * math.cos(4 * math.pi * ph + 0.6))
        return d

    fn.speed = 2 * stride / (frames / FPS)
    return fn


# ---------------------------------------------------------------------------
# Moves
# ---------------------------------------------------------------------------
def moves(D):
    t = D.t
    DH, NH = t["DRUMMER_HEAD"], t["NEAR_HEAD"]
    SAX, TRP = t["SAXES"], t["TRUMPETS"]
    M = {}

    def add(name, length, seq=None, fn=None, loop=False, props=None, **timing):
        M[name] = Move(length, loop, seq, fn, props, timing)

    hips_hands = dict(L=pr("hip"), R=pr("hip"))

    # ================= tier 0, rehearsal: controlled, fluid, alive =================
    s = Seq(look=("at", SAX)).k(0)
    s.k(5, R=pr("chest", (0, 0.02, -0.04), "point"), L=pr("hip"), lean=0.5)
    s.k(10, R=pr("finger_up", (0, 0, 0.035)), lean=-1.5, shr=1.5)
    s.k(12, R=pr("finger_up"), lean=-1)
    for fb in (18, 30, 42):
        s.k(fb - 3, R=pr("finger_up", (0, 0.01, 0.025)), nod=2.0, lean=-1.5, hips=(0, 0, 0.004))
        s.k(fb, "hit", R=pr("finger_beat", (0, -0.01, -0.035)), nod=-6.0, lean=3.0, hips=(0, 0, -0.012), shr=0)
        s.k(fb + 2, R=pr("finger_beat", (0, 0, -0.015)), nod=-3.5, lean=2.0)
        s.k(fb + 6, R=pr("finger_up"), nod=0.0, lean=-0.5, hips=(0, 0, 0))
    s.k(54, R=pr("chest", (0, 0.02, -0.12), "relaxed"), lean=0.5)
    s.k(60, R=pr("down"), L=pr("down"), lean=0)
    add("t0_countoff_finger", 60, s, downbeats=[18, 30, 42], finger_up=[12, 24, 36, 48])

    s = Seq(L=pr("hip"), R=pr("chest", (0, 0, 0.02))).k(0)
    for b in (2, 14, 26, 38):
        s.k(b, "hit", R=pr("chest", (0, -0.01, -0.06)), nod=-7.0, lean=3.0, shr=1.5)
        s.k(b + 4, R=pr("chest", (0, 0, -0.03)), nod=-2.0, lean=1.5, shr=0)
        if b < 38:
            s.k(b + 9, R=pr("chest", (0, 0.01, 0.02)), nod=1.5, lean=-0.5)
    s.k(48, R=pr("hip"), nod=0.0, lean=0)
    add("t0_countoff_verbal", 48, s, beats=[2, 14, 26, 38])

    s = Seq(look=("at", SAX)).k(0)
    s.k(6, L=pr("clap_open", (0, 0.02, 0)), R=pr("clap_open", (0, 0.02, 0)), lean=-1, shl=1.5, shr=1.5)
    for c in (12, 24, 36, 48):
        s.k(c, "hit", L=pr("clap"), R=pr("clap"), lean=3.0, nod=-5.0, shl=0, shr=0)
        s.k(c + 2, L=pr("clap", (0.012, 0, 0.005)), R=pr("clap", (-0.012, 0, 0.005)), lean=2.5, nod=-3.0)
        if c < 48:
            s.k(c + 7, L=pr("clap_open"), R=pr("clap_open"), lean=-0.5, nod=1.0, shl=1, shr=1)
    s.k(53, L=pr("clap_open", (0.02, 0.05, -0.15)), R=pr("clap_open", (-0.02, 0.05, -0.15)), lean=1)
    s.k(60, L=pr("down"), R=pr("down"), lean=0, nod=0)
    add("t0_clap_off", 60, s, claps=[12, 24, 36, 48])

    # conducting: ictus (hit) on each beat, rebound, travel along a curved 4/4 pattern
    s = Seq(L=pr("hip"), look=("at", SAX))
    patt = [(0, "c1", (0, 0, 0), "hit", 4.0, -3.0, 0.0), (3, "rebound", (0, 0, 0.04), None, 2.5, 0.0, 0.004),
            (7, "c2", (-0.06, 0, 0.07), None, 2.0, 0.0, 0.0), (12, "c2", (0, 0, 0), "hit", 3.0, -2.0, -0.004),
            (15, "c2", (0.01, 0, 0.05), None, 2.0, 0.0, 0.002), (19, "c3", (0.10, 0, 0.06), None, 2.0, 0, 0),
            (24, "c3", (0, 0, 0), "hit", 3.0, -2.0, -0.004), (27, "c3", (0.01, 0, 0.05), None, 2.0, 0, 0.002),
            (31, "c4", (-0.08, 0, -0.04), None, 2.0, 0, 0), (36, "c4", (0, 0, 0), "hit", 2.5, -1.5, 0.0),
            (40, "c4", (0, 0, 0.04), None, 1.0, 1.0, 0.004), (44, "c1", (0, 0, 0.12), None, 1.5, 0.5, 0.0),
            (48, "c1", (0, 0, 0), "hit", 4.0, -3.0, 0.0)]
    for f, name, off, e, ln, nd, hz in patt:
        s.k(f, e, R=pr(name, off), lean=ln, nod=nd, hips=(0.01 * math.sin(f / 48 * 2 * math.pi), 0, hz - 0.006),
            twist=4.0 * math.sin(f / 48 * 2 * math.pi + 1.0))
    add("t0_conduct", 48, s, loop=True, downbeats=[0, 48], beat_frames=BEAT)

    pace = [(0, 0), (1.2, 0), (1.4, 0.15), (1.2, 0.3), (0, 0.3), (-0.2, 0.15), (0, 0)]
    add("t0_pace", 96, fn=walk_fn(pace, 96, arms="behind", look=lambda u: ("at", DH)), loop=True)

    s = Seq(**hips_hands)
    for f, ln, sd, rl, yw in ((0, 10, -2, 12, 15), (18, 12, 1, 15, 18), (36, 13, 2.5, 16, 20), (54, 11, -1, 13, 16), (72, 10, -2, 12, 15)):
        s.k(f, lean=ln, side=sd, hips=(0.012 * math.sin(f / 72 * 2 * math.pi), 0.01, -0.008), look=(yw, -6.0, rl))
    add("t0_lean_listen", 72, s, loop=True)

    s = Seq(look=("at", TRP)).k(0)
    s.k(6, R=pr("chest", (0, 0.04, 0.05), "relaxed"), twist=-6, lean=-2, shr=2)
    s.k(10, "hit", R=pt(TRP, "open"), twist=8, lean=4, hips=(0, -0.02, 0), shr=0)
    s.k(13, R=pt(TRP + V(0, 0, 0.30), "open"), twist=10, lean=4.5)
    s.k(24, R=pt(TRP + V(0, 0, 0.18), "open"), twist=6, lean=3)
    s.k(36, R=pr("down"), twist=0, lean=0, hips=(0, 0, 0))
    add("t0_cue_section", 36, s, cue=10)

    s = Seq(L=pr("cross"), R=pr("cross"))
    for f, dz, sd in ((0, 0, -1.5), (24, 0.05, 1.0), (48, 0.09, 2.0), (72, 0, -1.5)):
        s.k(f, look=("at", SAX + V(0.25 * dz, 0, dz)), side=sd, lean=-1 + dz * 10)
    add("t0_watch_other", 72, s, loop=True)

    s = Seq(**hips_hands)
    for f, dz, rl, sd in ((0, 0, 0, 0), (24, 0.02, 2, 1.5), (48, -0.01, -1, -1.0), (72, 0, 0, 0)):
        s.k(f, look=("at", DH + V(0, 0, dz), rl), side=sd, lean=-1.5)
    add("t0_stare_drummer", 72, s, loop=True)

    wrist_pt = V(-0.03, -0.36, 1.36)
    s = Seq().k(0)
    s.k(10, L=pr("watch"), shl=2, look=("at", wrist_pt), lean=3)
    s.k(22, L=pr("watch", (0, -0.01, 0.01)), look=("at", wrist_pt + V(0.01, 0, 0.01)), lean=3.5)
    s.k(34, L=pr("watch"), look=("at", wrist_pt), shl=1)
    s.k(48, L=pr("down"), look=(0.0, 0.0, 0.0), lean=0, shl=0)
    add("t0_check_watch", 48, s, look_at_watch=[10, 34])

    st = t["STOOL"]
    perch = dict(hips=(st.x, st.y + 0.03, 0.81 - 0.95 * SC), lean=-3,
                 feet=[st + V(0.17, -0.34, ANKLE_Z), st + V(-0.06, -0.17, 0.28)],
                 L=on_bone("LeftUpperLeg", (0.01, 0.0, 0.16), hdir=(0, -1, -0.2), pole=(0.9, 0.3, 0.2), along=0.55),
                 R=on_bone("RightUpperLeg", (0.0, 0.0, 0.13), hdir=(0, -1, -0.2), pole=(-0.9, 0.3, 0.2), along=0.6))
    s = Seq(**perch)
    for f, dx, sd in ((0, 0, 0), (36, 0.4, 2), (72, 0, 0)):
        s.k(f, look=("at", SAX + V(dx, 0, 0)), side=sd)
    add("t0_chair_rest", 72, s, loop=True, note="hips sit on stool_terence")

    # ================= tier 1, stops: crisp =================
    s = Seq(look=("at", DH)).k(0)
    s.k(5, R=pr("flick_a", (0, 0.03, -0.03)), shr=2, lean=-1)
    s.k(8, R=pr("flick_a", (0.03, 0.02, 0.04)), twist=5)
    s.k(10, "hit", R=pr("flick_b"), twist=-8, lean=3, nod=-3)
    s.k(12, R=pr("flick_b", (-0.05, -0.02, -0.03)), twist=-10, lean=3, shr=0)
    s.k(18, R=pr("down"), twist=0, lean=0, nod=0)
    add("t1_flick", 18, s, flick=10)

    s = Seq().k(0, look=(-6.0, 0.0, 0.0))
    s.k(8, R=pr("chest", (0.03, 0, 0.02), "open"), twist=8, look=(-10.0, 0.0, 0.0))
    s.k(13, R=pr("chest", (0.07, 0.02, 0.04), "open"), twist=12)
    s.k(16, "hit", R=pr("wave_out"), twist=-14, lean=2, look=(-22.0, -4.0, 0.0))
    s.k(19, R=pr("wave_out", (-0.06, 0.02, -0.03)), twist=-18)
    s.k(24, R=pr("chest", (-0.06, 0.02, -0.12), "open"), twist=-8)
    s.k(30, R=pr("down"), twist=0, lean=0, look=(-10.0, 0.0, 0.0))
    add("t1_wave_off", 30, s, wave=16)

    s = Seq(L=pr("hip"), R=pr("c4"), look=("at", SAX)).k(0)
    s.k(4, R=pr("open_high", (0, 0.04, 0.05)), shr=2)
    s.k(7, R=pr("open_high", (0, 0.03, 0.09)), shr=4, lean=-2)
    s.k(10, "hit", R=pr("fist_high", (0, -0.03, -0.05)), lean=3, nod=-4, hips=(0, 0, -0.015), shr=0)
    s.k(13, R=pr("fist_high", (0, -0.02, -0.07)), lean=2.5)
    s.k(18, R=pr("fist_high", (0, 0, -0.04)), lean=1, nod=-1, look=("at", DH))
    s.k(24, R=pr("fist_high", (0, 0, -0.05)), hips=(0, 0, 0))
    s.k(30, R=pr("down"), L=pr("down"), lean=0, nod=0)
    add("t1_fist_cutoff", 30, s, cutoff=10)

    s = Seq().k(0)
    s.k(6, L=pr("clap_open", (0.03, 0.05, 0.02)), R=pr("clap_open", (-0.03, 0.05, 0.02)), shl=2.5, shr=2.5, lean=-2)
    s.k(9, "hit", L=pr("clap"), R=pr("clap"), lean=5, nod=-5, shl=0, shr=0, hips=(0, -0.01, -0.012))
    s.k(11, L=pr("clap", (0.01, 0, 0)), R=pr("clap", (-0.01, 0, 0)), lean=4.5)
    s.k(15, L=pr("clap", (0.04, 0, 0)), R=pr("clap", (-0.04, 0, 0)), look=("at", DH), lean=3, nod=-2)
    s.k(24, L=pr("down"), R=pr("down"), lean=0, nod=0, hips=(0, 0, 0))
    add("t1_clap_stop", 24, s, clap=9)

    desk = V(0.0, -0.48, 1.14)
    s = Seq(look=("at", DH)).k(0)
    s.k(6, R=pr("up", (0.02, 0.06, -0.02), "fist"), lean=-5, side=4, shr=6, hips=(0, 0.01, 0.008), look=("at", desk))
    s.k(9, R=pr("up", (0.02, 0.10, 0.03), "fist"), lean=-7, side=5)
    s.k(12, "hit", R=at(desk, "fist", (0, -0.3, -1), (-0.8, 0.5, -0.6), True), lean=15, side=-2, shr=-2, nod=-8, hips=(0, -0.03, -0.045))
    s.k(14, R=at(desk + V(0, 0, 0.045), "fist", (0, -0.3, -1), (-0.8, 0.5, -0.6)), lean=13, nod=-5)
    s.k(18, R=at(desk + V(0, 0, 0.01), "fist", (0, -0.3, -1), (-0.8, 0.5, -0.6)), lean=11, side=0, shr=0, look=("at", DH))
    s.k(30, R=pr("down"), lean=0, nod=0, hips=(0, 0, 0))
    add("t1_fist_slam", 30, s, slam=12)

    # ================= tier 2, pressure =================
    stare = dict(L=pr("cross"), R=pr("cross"), lean=-3)
    s = Seq(**stare)
    for f, dz, rl, sd in ((0, 0, 8, 0), (24, 0.03, 10, 1.5), (48, 0.01, 11, -1.0), (72, 0, 8, 0)):
        s.k(f, look=("at", DH + V(0, 0, dz), rl), side=sd)
    add("t2_judging_stare", 72, s, loop=True)

    s = Seq(L=pr("hip")).k(0, look=("at", DH))
    s.k(6, R=pr("chest", (0, 0.02, 0), "point"), look=("at", desk), lean=4)
    s.k(10, R=pt(desk + V(0, 0, 0.06)), lean=8)
    s.k(13, R=pt(desk + V(0, 0, 0.035)), lean=8)
    s.k(16, "hit", R=pt(desk + V(0, 0, -0.035)), lean=9.5, nod=-4)
    s.k(19, R=pt(desk + V(0, 0, 0.035)), lean=8.5, nod=-1)
    s.k(24, "hit", R=pt(desk + V(0, 0, -0.035)), lean=9.5, nod=-3, look=("at", DH))
    s.k(27, R=pt(desk + V(0, 0, 0.0)), lean=8.5, nod=0)
    s.k(36, R=pr("down"), L=pr("down"), lean=0)
    add("t2_score_point", 36, s, taps=[16, 24])

    s = Seq(look=("at", DH)).k(0)
    s.k(5, R=pr("chest", (0, 0.05, 0.02), "point"), L=pr("hip"), twist=-5, lean=-2, shr=2)
    s.k(8, "hit", R=pt(DH), twist=10, lean=5, hips=(0, -0.02, 0), shr=0)
    s.k(11, R=pt(DH + V(0, 0, 0.05)), twist=11, lean=5.5)
    s.k(18, R=pt(DH + V(0, 0, 0.02)), twist=9, lean=4.5)
    s.k(28, R=pt(DH + V(0, 0, 0.03)), twist=9, lean=4)
    s.k(31, R=pr("flick_b", (0.0, -0.06, -0.16), "relaxed"), twist=5, lean=2.5)
    s.k(36, R=pr("down"), L=pr("down"), twist=0, lean=0, hips=(0, 0, 0))
    add("t2_point_player", 36, s, point=8)

    s = Seq(L=pr("behind"), R=pr("behind"), lean=6)
    hunt = [(0, "SAXES", -10, 0.0, None), (20, "SAXES", -10, 0.0, None), (26, "TROMBONES", -4, 0.06, None), (32, "TROMBONES", 0, 0.12, "hold"),
            (52, "TROMBONES", 0, 0.12, None), (58, "TRUMPETS", 5, 0.0, None), (64, "TRUMPETS", 8, -0.12, "hold"),
            (84, "TRUMPETS", 8, -0.12, None), (90, "SAXES", -4, -0.05, None), (96, "SAXES", -10, 0.0, None)]
    for f, sec, yw, dx, e in hunt:
        s.k(f, e, hips=(dx, 0, -0.006), yaw=yw, look=("at", t[sec]), side=dx * 15)
    add("t2_hunt_sections", 96, s, loop=True)

    s = Seq().k(0)
    s.k(12, L=pr("palms_down", (0, 0, 0.03)), R=pr("palms_down", (0, 0, 0.03)), lean=-2, look=(0.0, 6.0, 0.0), shl=3, shr=3)
    s.k(24, L=pr("palms_down", (0, 0, -0.12)), R=pr("palms_down", (0, 0, -0.12)), lean=3, look=(0.0, -10.0, 0.0), shl=-1, shr=-1)
    s.k(36, L=pr("palms_down", (0, 0, -0.13)), R=pr("palms_down", (0, 0, -0.13)), lean=2, look=("at", DH), shl=0, shr=0)
    s.k(48, L=pr("hip"), R=pr("hip"), lean=0)
    add("t2_forced_calm", 48, s)

    away = V(2.6, 1.2, 1.2)
    s = Seq(look=("at", DH)).k(0)
    s.k(5, R=pr("chest", (0, 0.05, 0.02), "point"), L=pr("hip"), twist=-4)
    s.k(8, "hit", R=pt(DH), twist=8, lean=4)
    s.k(18, R=pt(DH + V(0, 0, 0.02)), twist=8, lean=4)
    s.k(22, R=pt(DH + V(0, 0, 0.06)), L=pr("chest", (0, 0.03, 0), "open"), twist=2)
    s.k(26, "hit", R=pr("chest", (-0.05, 0, -0.1), "relaxed"), L=pt(away, "open"), twist=22, lean=1)
    s.k(29, L=pt(away + V(0.3, 0.3, 0.1), "open"), twist=25)
    s.k(40, R=pr("down"), L=pr("down"), twist=0, lean=0)
    add("t2_switch_drummer", 40, s, point=8, sweep=26)

    s = Seq().k(0)
    s.k(8, L=pr("watch"), look=("at", wrist_pt), shl=2, lean=3)
    s.k(12, R=pr("watch_tap", (0, 0, 0.06)))
    s.k(14, "hit", R=pr("watch_tap"), nod=-3)
    s.k(20, R=pr("watch_tap", (0, 0, 0.07)), nod=0)
    s.k(26, "hit", R=pr("watch_tap"), nod=-3)
    s.k(29, R=pr("watch_tap", (0, 0, 0.04)), nod=0)
    s.k(34, R=pr("down"), L=pr("watch", (0, 0, -0.03)), look=("at", DH), lean=2)
    s.k(48, L=pr("down"), lean=0, shl=0)
    add("t2_impatient_watch", 48, s, taps=[14, 26])

    # ================= tier 3, close up =================
    s = Seq(L=pr("behind"), R=pr("behind"))
    for f, ln, jt, hy in ((0, 21, 3, 0.02), (24, 25, 6, 0.035), (48, 26, 7, 0.04), (72, 21, 3, 0.02)):
        s.k(f, lean=ln, jut=jt, hips=(0, hy, -0.012), look=("at", NH), hunch=4)
    add("t3_loom", 72, s, loop=True)

    ear = NH + V(-0.10, 0.03, 0.03)
    shoulder = NH + V(-0.20, 0.03, -0.26)
    s = Seq().k(0, look=("at", NH))
    s.k(8, R=pr("chest", (0, -0.05, -0.05), "relaxed"), lean=14, hips=(0, -0.04, -0.03), twist=-6)
    s.k(16, R=at(shoulder, "relaxed", (0, -0.3, -1), None, True), L=pr("behind"), lean=44, twist=-16, side=-4, hips=(-0.03, -0.19, -0.13),
        stepr=(-0.03, -0.24), look=("at", ear), jut=5)
    s.k(36, lean=45, twist=-17, hips=(-0.03, -0.20, -0.135), look=("at", ear + V(0, 0, 0.01)))
    s.k(56, lean=45, twist=-16, hips=(-0.03, -0.20, -0.135), look=("at", ear))
    s.k(64, R=pr("chest", (0, -0.03, -0.06), "relaxed"), L=pr("down"), lean=12, twist=-4, hips=(0, -0.03, -0.02), stepr=(0, 0), jut=0, look=("at", NH))
    s.k(72, R=pr("down"), lean=0, twist=0, hips=(0, 0, 0))
    add("t3_whisper", 72, s, close=16)

    crouch = dict(hips=(0, 0.10, -0.50), spread=0.09, lean=26,
                  L=on_bone("LeftLowerLeg", (0.02, 0.07, 0.15), hdir=(0, -1, -0.15)), R=on_bone("RightLowerLeg", (-0.02, 0.07, 0.15), hdir=(0, -1, -0.15)))
    s = Seq(**crouch)
    for f, dz, ln in ((0, 0, 26), (36, 0.025, 29), (72, 0, 26)):
        s.k(f, look=("at", NH + V(0, 0, dz)), lean=ln, jut=ln - 22)
    add("t3_crouch", 72, s, loop=True)

    radius = 1.3
    c = V(0, -radius, 0)
    circle = [(c.x + radius * math.sin(a), c.y + radius * math.cos(a)) for a in np.linspace(0, 2 * math.pi, 17)]
    add("t3_circle_kit", 144, fn=walk_fn(circle, 144, step=0.52, arms="behind", look=lambda u: ("at", c + V(0, 0, 1.15)), lean=8),
        loop=True, note=f"player head assumed {radius} m in front of the start")

    clap_at = NH + V(0, 0.20, 0.03)
    s = Seq().k(0, look=("at", NH))
    for b in range(4):
        f = 12 + b * BEAT
        s.k(f - 6, lean=14, hips=(0, -0.02, -0.02), shl=2.5, shr=2.5, jut=2,
            L=at(clap_at + V(0.20, 0.03, 0.02), "open", (-0.5, -1, 0.4)), R=at(clap_at + V(-0.20, 0.03, 0.02), "open", (0.5, -1, 0.4)))
        s.k(f, "hit", lean=19, hips=(0, -0.04, -0.03), shl=0, shr=0, jut=6, nod=-3,
            L=at(clap_at + V(0.03, 0, 0), "open", (-0.3, -1, 0.6), None, True), R=at(clap_at + V(-0.03, 0, 0), "open", (0.3, -1, 0.6), None, True))
        s.k(f + 2, lean=18, nod=-1,
            L=at(clap_at + V(0.045, 0.005, 0.005), "open", (-0.3, -1, 0.6)), R=at(clap_at + V(-0.045, 0.005, 0.005), "open", (0.3, -1, 0.6)))
    s.k(60, L=pr("down"), R=pr("down"), lean=0, hips=(0, 0, 0), jut=0, nod=0)
    add("t3_clap_in_face", 60, s, claps=[12, 24, 36, 48])

    s = Seq().k(0, look=("at", NH))
    s.k(10, R=pr("chest", (-0.04, -0.08, 0.0), "relaxed"), L=pr("chest", (0, 0, -0.06), "relaxed"), lean=14, hips=(0, -0.08, -0.04), stepr=(-0.02, -0.14))
    s.k(16, R=at(shoulder, "relaxed", (0, -0.3, -1), None, True), lean=38, side=-6, twist=-8, look=("at", NH, -8), hips=(-0.04, -0.18, -0.13), stepr=(-0.04, -0.26))
    s.k(26, lean=39, look=("at", NH + V(0, 0, -0.02), -10))
    s.k(36, R=at(shoulder + V(0, 0, -0.01), "relaxed", (0, -0.3, -1), None, True), lean=39, look=("at", NH + V(0, 0, -0.03), -10))
    s.k(46, R=at(shoulder, "relaxed", (0, -0.3, -1), None, True), lean=38.5, look=("at", NH, -9))
    s.k(56, lean=38, look=("at", NH, -8))
    s.k(64, R=pr("chest", (-0.04, -0.05, -0.04), "relaxed"), lean=12, side=0, twist=0, hips=(0, -0.05, -0.03), stepr=(0, 0))
    s.k(72, R=pr("down"), L=pr("down"), lean=0, hips=(0, 0, 0), look=("at", NH))
    add("t3_fake_warmth", 72, s, hand_on_shoulder=[16, 56])

    # ================= tier 4, rage: wind-up, lunge, strike, follow-through, recoil =================
    s = Seq(look=("at", DH)).k(0)
    s.k(5, L=pr("fist_cock"), R=pr("fist_cock"), lean=-7, shl=6, shr=6, hips=(0, 0.03, 0.01), jut=-2, look=("at", DH + V(0, 0, 0.2)))
    s.k(10, L=pr("rage", (0, 0.06, 0.04)), R=pr("rage", (0, 0.06, 0.04)), lean=12, hips=(0, -0.12, -0.05),
        stepl=(0.0, -0.32), shl=2, shr=2)
    s.k(12, "hit", L=pr("rage", (0, -0.05, -0.02)), R=pr("rage", (0, -0.05, -0.02)), lean=24, hips=(0, -0.18, -0.09),
        jut=12, nod=-4, shl=-2, shr=-2, hunch=8, shake=0.012, look=("at", DH + V(0, 0, 0.25)))
    s.k(15, L=pr("rage", (0, -0.02, 0.0)), R=pr("rage", (0, -0.02, 0.0)), lean=21, hips=(0, -0.16, -0.08), jut=10, nod=-1)
    s.k(24, lean=19, hips=(0, -0.15, -0.075), jut=8, shake=0.01)
    s.k(31, L=pr("fist_cock", (0, -0.05, 0)), R=pr("fist_cock", (0, -0.05, 0)), lean=12, hips=(0, -0.13, -0.065), jut=2, shl=5, shr=5, nod=2, shake=0.004)
    s.k(36, "hit", L=pr("rage", (0, -0.06, -0.03)), R=pr("rage", (0, -0.06, -0.03)), lean=28, hips=(0, -0.20, -0.10),
        jut=13, nod=-5, shl=-2, shr=-2, hunch=10, shake=0.014)
    s.k(39, L=pr("rage", (0, -0.03, -0.01)), R=pr("rage", (0, -0.03, -0.01)), lean=25, hips=(0, -0.19, -0.095), jut=11, nod=-2)
    s.k(43, L=pr("hip", (0, -0.04, 0.03), "fist"), R=pr("hip", (0, -0.04, 0.03), "fist"), lean=10, hips=(0, -0.06, -0.03),
        stepl=(0, 0), jut=3, shake=0.0, hunch=3, shl=0, shr=0, nod=0)
    s.k(48, L=pr("down"), R=pr("down"), lean=0, hips=(0, 0, 0), jut=0, hunch=0, look=("at", DH))
    add("t4_shout", 48, s, accents=[12, 36])

    dirv = V(DH.x, DH.y, 0).normalized()
    charge_dist = 1.6
    cw = walk_fn([(0, 0), (dirv.x * charge_dist, dirv.y * charge_dist)], 26, step=0.8, arms="pump", look=("at", DH), stomp=0.035, lean=16)

    def charge(f):
        if f <= 26:
            d = cw(f)
            d["jut"] = 6.0
            d["hunch"] = 6.0
            return d
        d = cw(26)
        k = (f - 26) / 4.0                       # the stop: weight slams forward and recoils
        jolt = math.sin(math.pi * k)
        hp = Vector(d["hips"])
        d["hips"] = (hp.x + dirv.x * 0.06 * jolt, hp.y + dirv.y * 0.06 * jolt, hp.z - 0.04 * jolt)
        d["lean"] = 16 + 10 * jolt - 4 * k
        d["jut"] = 6.0 + 6 * jolt
        d["hunch"] = 6.0
        d["L"] = ("mix", d["L"], pr("fist_out"), smooth(k))
        d["R"] = ("mix", d["R"], pr("fist_out"), smooth(k))
        return d
    add("t4_charge", 30, fn=charge, stop=30, distance_m=charge_dist,
        stomps=[round(26 * (i + 1) / cw.n_steps) for i in range(cw.n_steps)])

    stand0 = Tm(t["STAND"]) @ Rm(z=180)
    sprop = D.props["music_stand_knockable"]["grips"]
    piv = stand0 @ sprop["pivot_tip"].translation

    def stand_at(ang):
        return Tm(piv) @ Matrix.Rotation(math.radians(ang), 4, X) @ Tm(-piv) @ stand0

    hit_f, land_f = 12, 21

    def stand_fn(f):
        if f <= hit_f:
            return stand_at(0)
        u = min((f - hit_f) / (land_f - hit_f), 1.0)
        ang = 92 * u ** 1.6                              # topples away, accelerating
        if f > land_f:                                    # small bounce on the floor
            ang = 92 - 6 * math.sin(math.pi * min((f - land_f) / 5, 1.0)) * math.exp(-(f - land_f) / 6)
        return stand_at(ang)

    desk_top = stand_at(0) @ V(0.0, 0.0, 1.02)            # desk centre
    desk_n = (stand_at(0).to_3x3() @ (Matrix.Rotation(math.radians(-25), 3, X) @ V(0, -1, 0))).normalized()  # faces him
    desk_up = (stand_at(0).to_3x3() @ (Matrix.Rotation(math.radians(-25), 3, X) @ V(0, 0, 1))).normalized()
    strike = desk_top + desk_up * 0.17 + V(-0.10, 0, 0.04) + desk_n * 0.08
    s = Seq(look=("at", DH)).k(0)
    s.k(5, R=pr("up", (-0.22, 0.10, -0.10), "open"), twist=-30, lean=-4, side=-5, shr=7, hips=(-0.03, 0.03, 0.0),
        stepr=(-0.04, 0.05), look=("at", desk_top))
    s.k(9, R=pr("up", (-0.30, 0.16, -0.06), "open"), twist=-38, lean=-6, side=-7)
    s.k(12, "hit", R=at(strike, "open", (0.6, -0.8, -0.3), (-0.7, 0.3, -0.6), True), twist=10, lean=20, side=2, shr=-2,
        hips=(0.02, -0.10, -0.06), stepl=(0.02, -0.22), stepr=(-0.04, 0.05), jut=6)
    s.k(15, R=at(strike + V(0.42, 0.02, -0.12), "open", (1, -0.4, -0.3), (-0.4, 0.6, -0.6)), twist=30, lean=24, side=6, hips=(0.05, -0.12, -0.07))
    s.k(19, R=pr("wave_out", (0.42, -0.26, -0.1), "open"), twist=34, lean=20, side=4)
    s.k(24, R=pr("down", (-0.02, -0.05, 0.06)), twist=12, lean=8, side=0, hips=(0.02, -0.05, -0.03), stepl=(0, 0), stepr=(0, 0), jut=2, look=("at", DH))
    s.k(30, R=pr("down"), twist=0, lean=0, hips=(0, 0, 0), jut=0, shr=0)
    add("t4_sweep_stand", 30, s, props={"PropR": ("music_stand_knockable", stand_fn)}, hit=hit_f, lands=land_f)

    pprop = D.props["sheet_music_pages"]["grips"]
    pages0 = Tm((0, -0.47, 1.12)) @ Rm(x=-65)
    g_local = pprop["grip_r"].translation
    land = V(0.4, -2.2, 0.05)
    rel_f, pland_f = 18, 34
    grab_f = 8
    # pages follow the hand: keyed so the grip stays in the right hand from the grab to the release
    def pg(gp, e):          # pages placed so their grip_r lands on gp
        return Tm(gp) @ Rm(*e) @ Tm(-g_local)

    ptrack = Track([(0, pages0), (grab_f, pages0), (11, pg(V(-0.18, -0.44, 1.30), (-50, 0, -160))),
                    (14, pg(V(-0.40, -0.28, 1.48), (-30, 20, -185))), (rel_f, pg(V(-0.08, -0.66, 1.24), (-70, -20, -150)), "hit")],
                   pivot=g_local)
    rel_g = ptrack(rel_f) @ g_local

    def pages_fn(f):
        if f <= rel_f:
            return ptrack(f)
        u = min((f - rel_f) / (pland_f - rel_f), 1.0)
        e = 1 - (1 - u) ** 2.2                       # fast off the hand, paper drag slows it
        p = lerp(rel_g, land, e) + V(0, 0, 0.35 * math.sin(math.pi * min(e * 1.15, 1.0)) * (1 - u * 0.3))
        r = Rm(x=-70 + 180 * e + 12 * math.sin((f - rel_f) * 0.9), y=-20 + 25 * math.sin((f - rel_f) * 0.6), z=-150 - 70 * e)
        return Tm(p) @ r @ Tm(-g_local)

    s = Seq(look=("at", pages0.translation)).k(0)
    s.k(grab_f, R=grip("sheet_music_pages", "grip_r", "pinch"), lean=14, hips=(0, -0.04, -0.03), shr=2)
    s.k(11, lean=6, twist=-10, hips=(0, 0.0, -0.01))
    s.k(14, twist=-26, lean=-2, side=-4, shr=6, L=pr("balance", (0, 0, 0)), look=("at", DH), hips=(-0.02, 0.03, 0.0))
    s.k(rel_f, "hit", R=grip("sheet_music_pages", "grip_r", "open"), twist=18, lean=20, side=3, hips=(0.03, -0.12, -0.06),
        stepl=(0.02, -0.25), shr=-2, jut=8, L=pr("down", (0, 0.05, 0.05)))
    s.k(21, R=pr("wave_out", (0.50, -0.15, -0.20), "open"), twist=28, lean=24, side=5, hips=(0.04, -0.13, -0.07))
    s.k(26, R=pr("down", (-0.03, -0.09, 0.07)), twist=10, lean=10, side=1, hips=(0.01, -0.06, -0.03), jut=3)
    s.k(34, R=pr("down"), L=pr("down"), twist=0, lean=0, side=0, hips=(0, 0, 0), stepl=(0, 0), jut=0, shr=0)
    add("t4_throw_music", 34, s, props={"PropR": ("sheet_music_pages", pages_fn)}, grab=grab_f, release=rel_f, lands=pland_f)

    door = t["DOOR"]
    door_yaw = yaw_of(door)
    door_pt = door + V(0, 0, 1.2)
    tw = max(-45, min(45, door_yaw * 0.35))
    s = Seq(look=("at", DH)).k(0)
    s.k(7, R=pr("chest", (0.0, -0.03, 0.04), "point"), L=pr("hip"), twist=-tw * 0.4, lean=-3, shr=4, look=("at", door_pt))
    s.k(12, "hit", R=pt(door_pt), twist=tw, lean=6, shr=-1, jut=4)
    s.k(15, R=pt(door_pt + V(0, 0, 0.12)), twist=tw * 1.08, lean=6.5)
    s.k(21, R=pt(door_pt + V(0, 0, 0.04)), lean=5, look=("at", DH))
    s.k(24, "hit", R=pt(door_pt + V(0, 0, -0.06)), lean=7, nod=-3, jut=6)
    s.k(30, R=pt(door_pt + V(0, 0, 0.03)), lean=5.5, nod=0)
    s.k(36, "hit", R=pt(door_pt + V(0, 0, -0.06)), lean=7, nod=-3)
    s.k(40, R=pt(door_pt), lean=5, nod=0, jut=3)
    s.k(48, R=pr("down"), L=pr("down"), twist=0, lean=0, jut=0, shr=0)
    add("t4_door_point", 48, s, point=12, jabs=[24, 36])

    stop = door - door.normalized() * 0.6
    add("t4_storm_out", 72, fn=walk_fn([(0, 0), (stop.x * 0.5, stop.y * 0.5 - 0.3), (stop.x, stop.y)], 72, step=0.70,
                                       look=lambda u: ("at", door_pt), stomp=0.02, lean=9, swing_amp=0.16), arrives=72)

    # ================= tier 5, violence =================
    # --- the folded chair, slung flat like a discus at the player's head ---
    # He grabs it by its top bar, swings it out flat at arm's length to his right and back (coiled,
    # weight on the right foot), then unwinds: hips, chest and arm whip round counter-clockwise, the
    # left foot steps toward the player, and the slab leaves the hand when the hand's path points at
    # the player's head. The slab always extends outward from the hand, away from his body.
    cprop = D.props["chair_folded"]["grips"]
    cg_r = cprop["grip_r"].translation
    com_local = V(0, 0, 0.425)
    yaw_p = yaw_of(DH)
    chair0 = Tm((-0.56, -0.08, 0.0)) @ Rm(z=90)

    def flat(arm_deg, grip_pt, bank=0.0):
        """Chair lying flat (thickness vertical), slab extending outward along arm_deg from grip_r at grip_pt."""
        zc = -heading(arm_deg)                    # local +Z (feet -> grip edge) points back toward the hand
        yc = Vector((0, 0, 1))
        xc = yc.cross(zc).normalized()
        yc = zc.cross(xc).normalized()
        Rot = Matrix((xc, yc, zc)).transposed().to_4x4()
        Rot = Matrix.Rotation(math.radians(bank), 4, zc) @ Rot
        return Tm(grip_pt) @ Rot @ Tm(-cg_r)

    def hand_pt(arm_deg, r, z, c=(0, 0)):
        return V(c[0], c[1], z) + heading(arm_deg) * r

    pick_f, rel_c, arr_c = 10, 28, 44
    grip0 = chair0 @ cg_r
    rel_arm = yaw_p - 90 - 8                      # sweep is counter-clockwise: hand velocity = arm heading + 90
    cposes = [(0, chair0, None), (pick_f, chair0, None),
              (15, flat(-95, hand_pt(-95, 0.50, 1.02, (-0.05, 0.02))), None),
              (20, flat(-122, hand_pt(-122, 0.56, 1.12, (-0.07, 0.04))), None),
              (24, flat(-134, hand_pt(-134, 0.56, 1.14, (-0.08, 0.05))), "hold"),
              (26, flat(-100, hand_pt(-100, 0.62, 1.18, (-0.03, -0.04))), None),
              (rel_c, flat(rel_arm, hand_pt(rel_arm, 0.64, 1.22, (0.04, -0.12))), None)]
    ctrack = Track([(f, M, e) for f, M, e in cposes], pivot=cg_r)
    rel_M = ctrack(rel_c)
    rel_com = rel_M @ com_local
    spins = 2.0
    dirf = (DH - rel_com)
    flight_frames = arr_c - rel_c
    vel = dirf / flight_frames                               # metres per frame
    spin_rate = -360.0 * spins / flight_frames               # clockwise from above, as a right-hander's discus
    g = 9.81 / FPS ** 2
    # past the head it keeps flying and drops to the floor, then skids to a stop
    a_, b_, c_ = 0.5 * g, -vel.z, -(DH.z - 0.03)
    t_land = (-b_ + math.sqrt(b_ * b_ - 4 * a_ * c_)) / (2 * a_)
    land_c = arr_c + int(math.ceil(t_land))
    p_land = DH + vel * t_land - V(0, 0, 0.5 * g * t_land * t_land)
    p_land.z = 0.03
    vh = V(vel.x, vel.y, 0)
    Rr = rel_M.to_3x3().to_4x4()

    def chair_fn(f):
        if f <= rel_c:
            return ctrack(f)
        tt = f - rel_c
        if tt <= flight_frames:
            u = tt / flight_frames
            p = rel_com + dirf * u + V(0, 0, 0.10 * 4 * u * (1 - u))
            ang = spin_rate * tt
        else:
            e = tt - flight_frames
            if e <= t_land:
                p = DH + vel * e - V(0, 0, 0.5 * g * e * e)
                ang = spin_rate * tt
            else:
                k = 4.0 * (1 - math.exp(-(e - t_land) / 4.0))     # skid: decays to a stop
                p = p_land + vh * k
                ang = spin_rate * (flight_frames + t_land + k)
        return Tm(p) @ Matrix.Rotation(math.radians(ang), 4, Z) @ Rr @ Tm(-com_local)

    arm_pole = (0, 0.3, -1)
    to_p = heading(yaw_p) * 0.30
    s = Seq(look=("at", DH)).k(0)
    s.k(6, R=pr("down", (-0.10, -0.06, 0.10), "claw"), lean=14, side=-10, twist=-14, hips=(-0.04, 0, -0.07), look=("at", grip0))
    s.k(pick_f, R=grip("chair_folded", "grip_r", "grip"), lean=18, side=-12, twist=-18, hips=(-0.05, 0, -0.09))
    s.k(15, R=grip("chair_folded", "grip_r", "grip", arm_pole), lean=4, side=2, twist=-38, yaw=-10, byaw=-8, hips=(-0.05, 0.02, -0.05),
        L=pr("balance", (-0.04, -0.12, 0.08)), look=("at", DH))
    s.k(20, lean=2, side=4, twist=-52, yaw=-18, byaw=-12, hips=(-0.07, 0.04, -0.07), shr=3, L=pr("balance", (-0.06, -0.18, 0.10)))
    s.k(24, "hold", lean=3, side=5, twist=-60, yaw=-22, hips=(-0.08, 0.05, -0.09), shr=4)
    s.k(26, lean=6, side=0, twist=-30, yaw=2, byaw=10, hips=(-0.03, -0.04, -0.09), stepl=(to_p.x * 0.6, to_p.y * 0.6), shr=1,
        L=pr("balance", (0.0, 0.05, 0.0)), jut=3)
    s.k(rel_c, "hit", R=grip("chair_folded", "grip_r", "grip", arm_pole), lean=11, side=-4, twist=6, yaw=26, byaw=22,
        hips=(0.04, -0.12, -0.09), stepl=(to_p.x, to_p.y), jut=7, shr=-2, L=pr("down", (0.0, 0.12, 0.10)))
    s.k(rel_c + 1, R=at(hand_pt(yaw_p - 75, 0.62, 1.22, (0.05, -0.13)), "open", heading(yaw_p - 40), (-0.2, 0.3, -1)))
    s.k(32, R=at(hand_pt(yaw_p - 30, 0.60, 1.20, (0.07, -0.15)), "open", heading(yaw_p), (0.2, 0.3, -1)), lean=16, side=-6, twist=26,
        yaw=34, hips=(0.07, -0.16, -0.10), jut=8)
    s.k(37, R=pr("cross", (0.08, -0.18, 0.0), "open"), lean=17, side=-5, twist=34, yaw=38, byaw=26, hips=(0.08, -0.18, -0.11), jut=6)
    s.k(43, R=pr("down", (-0.04, -0.16, 0.14), "relaxed"), lean=12, side=-2, twist=20, yaw=32, hips=(0.06, -0.15, -0.08), jut=4)
    s.k(50, R=pr("down"), L=pr("down"), lean=6, side=0, twist=6, yaw=18, byaw=12, hips=(0.03, -0.07, -0.04), jut=2, shr=0)
    s.k(56, lean=2, twist=0, yaw=4, byaw=0, hips=(0, -0.01, -0.01), stepl=(0, 0), jut=0)
    s.k(60, lean=0, yaw=0, hips=(0, 0, 0))
    add("t5_chair_throw", 60, s, props={"PropR": ("chair_folded", chair_fn)}, pickup=pick_f, release=rel_c,
        arrives=arr_c, lands=land_c, spins=spins, note="props/chair_folded.glb on PropR, slung flat like a discus, spinning about its short axis")

    # --- slaps at the player's cheek: wind wide, strike in two frames, sweep off and away from the face ---
    cheek = NH + V(-0.085, 0.075, 0.0)
    windup = NH + V(-0.50, 0.12, 0.16)
    away_pt = NH + V(-0.02, 0.22, 0.16)
    s = Seq().k(0, look=("at", NH))
    impacts = [18, 42, 66, 84]
    for n_, imp in enumerate(impacts):
        w = 7 if n_ < 3 else 6
        s.k(imp - w, R=at(windup + V(0, 0, 0.02 * n_), "open", (0.3, 0.4, 0.7), (0.3, 0.6, -0.4)), twist=-30, lean=18, side=-8, shr=6,
            hips=(-0.04, -0.06, -0.07), stepl=(0.02, -0.22), L=pr("balance", (0.25, -0.05, 0.05)), jut=0, nod=0)
        s.k(imp - 2, R=at(windup + V(0.10, -0.06, -0.01), "open", (0.7, 0.2, 0.5), (0.3, 0.6, -0.4)), twist=-24, lean=22)
        s.k(imp, "hit", R=at(cheek, "open", (1, 0.2, 0.25), (0.2, 0.6, -0.5), True), twist=10, lean=33, side=3, shr=-2,
            hips=(0.02, -0.16, -0.12), jut=5, nod=-3)
        s.k(imp + 3, R=at(away_pt, "open", (0.8, 0.3, 0.5), (-1, 0.1, 0.6)), twist=18, lean=27, side=5, hips=(0.03, -0.14, -0.10))
        s.k(imp + 7, R=pr("chest", (-0.08, -0.06, -0.06), "open"), twist=6, lean=20, side=1, shr=0, hips=(0.01, -0.09, -0.07), jut=2, nod=0)
    s.k(96, R=pr("down"), L=pr("down"), twist=0, lean=0, side=0, hips=(0, 0, 0), stepl=(0, 0), jut=0)
    add("t5_rhythm_slaps", 96, s, impacts=impacts)

    # --- cowbell rung beside the player's head, every beat, with the whole body ---
    bell = Tm(NH + V(0.40, 0.12, -0.04)) @ Rm(z=-30)
    hitp = bell @ V(0.0, -0.03, 0.035)
    zdir = V(0.78, -0.22, -0.58).normalized()
    rotm = zdir.to_track_quat("Z", "Y").to_matrix().to_4x4()
    hits = [12, 24, 36, 48, 60]

    def stick_at(lift):
        # stick's local +Z runs grip -> tip; the tip is 0.20 m beyond the stick origin
        tip = hitp + V(-0.12, 0.04, 1.0) * lift
        return Tm(tip - zdir * 0.20) @ rotm

    stick_keys = [(0, 0.22)]
    for h in hits:
        stick_keys += [(h - 5, 0.26), (h - 2, 0.21), (h, 0.0), (h + 2, 0.04)]
    stick_keys.append((72, 0.22))
    sk_curve = Curve([k[0] for k in stick_keys], lambda i: np.array([stick_keys[i][1]]), [("hit" if k[1] == 0.0 else None) for k in stick_keys])

    def stick_fn(f):
        return stick_at(max(float(sk_curve(f)[0]), 0.0))

    s = Seq(L=grip("cowbell", "grip_l", "fist"), R=grip("cowbell_stick", "grip_r", "fist", (-1, 0.2, -0.3)), lean=13,
            hips=(0, -0.13, -0.06), stepl=(0.0, -0.20), look=("at", NH)).k(0)
    for h in hits:
        s.k(h - 5, lean=12, twist=-10, shr=5, hips=(0, -0.12, -0.06), nod=1, jut=2)
        s.k(h, "hit", lean=18, twist=6, shr=-2, hips=(0, -0.15, -0.09), nod=-4, jut=6)
        s.k(h + 3, lean=16, twist=3, shr=0, hips=(0, -0.14, -0.08), nod=-1)
    s.k(72, lean=12, twist=0, hips=(0, -0.13, -0.06), nod=0, jut=0)
    add("t5_cowbell", 72, s, props={"PropL": ("cowbell", lambda f: bell), "PropR": ("cowbell_stick", stick_fn)},
        hits=hits, note="cowbell on PropL, its stick (reparented) on PropR")

    # --- spare drum: hauled up overhead, stepped into, hurled down at the kit ---
    drum0 = Tm((0.0, -0.60, 0.0))
    d_rel_f, d_arr_f = 34, 48
    dtrack = Track([(0, drum0.translation, (0, 0, 0)), (10, drum0.translation, (0, 0, 0)),
                    (14, V(0.0, -0.58, 0.42), (25, 0, 0)), (20, V(0.0, -0.52, 1.10), (70, 0, 0)),
                    (26, V(0.0, -0.36, 1.74), (35, 0, 0)), (30, V(0.0, -0.24, 1.86), (15, 0, 0), "hold"),
                    (32, V(0.0, -0.40, 1.80), (35, 0, 0)), (d_rel_f, V(0.0, -0.66, 1.64), (60, 0, 0), "hit")])
    drel = dtrack(d_rel_f).translation
    dtarget = DH + V(0, 0.4, -0.55)

    def drum_fn(f):
        if f <= d_rel_f:
            return dtrack(f)
        u = min((f - d_rel_f) / (d_arr_f - d_rel_f), 1.0)
        p = lerp(drel, dtarget, u) + V(0, 0, 0.30 * 4 * u * (1 - u))
        return Tm(p) @ Rm(x=60 + 520 * u, z=25 * u)

    grip_drum = dict(L=grip("spare_drum", "grip_l"), R=grip("spare_drum", "grip_r"))
    s = Seq(look=("at", DH)).k(0)
    s.k(5, lean=30, hips=(-0.0, 0.06, -0.22), spread=0.05, toes=10, side=0, L=at((0.17, -0.50, 0.64), "open", (0, -0.3, -1), (0.3, 0.5, 0.6)),
        R=at((-0.17, -0.50, 0.64), "open", (0, -0.3, -1), (-0.3, 0.5, 0.6)),
        look=("at", drum0.translation))
    s.k(8, lean=48, hpitch=40, hips=(0.0, 0.10, -0.36), spread=0.13, toes=28,
        L=at((0.18, -0.60, 0.36), "open", (0, -0.2, -1), (0.3, 0.5, 0.6)), R=at((-0.18, -0.60, 0.36), "open", (0, -0.2, -1), (-0.3, 0.5, 0.6)))
    s.k(10, lean=56, hpitch=48, hips=(0.0, 0.12, -0.42), spread=0.16, toes=35, side=0,
        L=grip("spare_drum", "grip_l", "grip", (0.3, 0.5, 0.6)), R=grip("spare_drum", "grip_r", "grip", (-0.3, 0.5, 0.6)))
    s.k(14, **grip_drum, lean=40, hpitch=28, hips=(0.0, 0.06, -0.30), toes=15)
    s.k(20, lean=8, hpitch=4, hips=(0, 0, -0.06), spread=0.03, toes=0, side=0, look=("at", DH))
    s.k(26, lean=-8, hpitch=-2, hips=(0, 0.04, 0.0), shl=6, shr=6)
    s.k(30, "hold", lean=-13, hips=(0, 0.07, 0.01), shl=8, shr=8, jut=-2)
    s.k(32, lean=-4, hips=(0, -0.04, -0.03), shl=6, shr=6, jut=0)
    s.k(d_rel_f, "hit", lean=24, hpitch=8, hips=(0, -0.16, -0.09), stepl=(0.03, -0.32), shl=-2, shr=-2, jut=10, hunch=8)
    s.k(d_rel_f + 1, L=pr("open_high", (0, -0.28, -0.25)), R=pr("open_high", (0, -0.28, -0.25)))
    s.k(40, L=pr("down", (0, -0.15, 0.25), "open"), R=pr("down", (0, -0.15, 0.25), "open"), lean=30, hips=(0, -0.18, -0.11), jut=8, hunch=10)
    s.k(48, L=pr("down"), R=pr("down"), lean=14, hips=(0, -0.10, -0.06), jut=4, hunch=4, shl=0, shr=0)
    s.k(56, lean=3, hpitch=0, hips=(0, -0.01, -0.01), stepl=(0, 0), jut=0, hunch=0)
    s.k(60, lean=0, hips=(0, 0, 0))
    add("t5_drum_throw", 60, s, props={"PropR": ("spare_drum", drum_fn)}, pickup=10, release=d_rel_f, arrives=d_arr_f)

    # --- cymbal: snatched up, cocked across the body, sliced backhand at the player's head ---
    cym0 = Tm((-0.46, -0.34, 0.95))
    y_rel, y_arr = 21, 33
    ytrack = Track([(0, cym0.translation, (0, 0, 0)), (8, cym0.translation, (0, 0, 0)),
                    (13, V(0.02, -0.52, 1.22), (0, -40, 40)), (17, V(0.30, -0.48, 1.25), (0, -70, 90), "hold"),
                    (y_rel, V(-0.12, -0.58, 1.28), (-8, -10, 160), "hit")])
    yrel = ytrack(y_rel).translation

    def cym_fn(f):
        if f <= y_rel:
            return ytrack(f)
        u = min((f - y_rel) / (y_arr - y_rel), 1.0)
        p = lerp(yrel, DH, u) + V(0, 0, 0.12 * 4 * u * (1 - u))
        return Tm(p) @ Rm(x=-8 * (1 - u), y=-10 * (1 - u), z=160 - 1440 * u)

    s = Seq(look=("at", DH)).k(0)
    s.k(8, R=grip("cymbal", "grip_r", "pinch"), lean=12, side=-4, twist=-8, hips=(-0.02, 0, -0.03), look=("at", cym0.translation))
    s.k(13, R=grip("cymbal", "grip_r", "pinch", (-0.2, -1, 0.3)), lean=6, twist=20, look=("at", DH))
    s.k(17, "hold", lean=8, twist=44, yaw=10, side=4, hips=(0.05, 0.03, -0.05), shr=5, hunch=7, L=pr("balance", (0, 0.1, 0)))
    s.k(y_rel, "hit", R=grip("cymbal", "grip_r", "open", (-0.6, -0.3, 0.6)), lean=14, twist=-14, yaw=30, side=-3, hips=(-0.04, -0.15, -0.07),
        stepr=(0.10, -0.30), shr=-2, hunch=0, jut=8, L=pr("down", (0, 0.12, 0.08)))
    s.k(y_rel + 1, R=pt(DH, "open"))
    s.k(27, R=pr("wave_out", (0, -0.12, 0), "open"), lean=16, twist=-30, side=-5, hips=(-0.06, -0.19, -0.08), jut=6)
    s.k(36, R=pr("down", (0, -0.04, 0.03)), lean=8, twist=-8, yaw=20, side=0, hips=(-0.02, -0.1, -0.04), jut=2, shr=0)
    s.k(44, R=pr("down"), L=pr("down"), lean=2, twist=0, yaw=4, hips=(0, -0.01, 0), stepr=(0, 0), jut=0)
    s.k(48, lean=0, yaw=0, hips=(0, 0, 0))
    add("t5_cymbal_throw", 48, s, props={"PropR": ("cymbal", cym_fn)}, pickup=8, release=y_rel, arrives=y_arr)

    # ================= approval: quiet =================
    s = Seq(**hips_hands, look=("at", DH)).k(0)
    for nf in (6, 18):
        s.k(nf - 3, nod=2.0)
        s.k(nf, nod=-14.0, lean=1.5)
        s.k(nf + 3, nod=-4.0, lean=0.5)
    s.k(24, nod=0.0, lean=0)
    add("ap_nod", 24, s, nods=[6, 18])

    s = Seq(**hips_hands, look=("at", DH)).k(0)
    s.k(12, lean=-3, look=("at", DH, 7), shl=1, shr=1)
    s.k(24, lean=-2, look=("at", DH + V(0, 0, -0.15), 6))
    s.k(36, lean=0, look=("at", DH, 4), shl=0, shr=0)
    add("ap_grin", 36, s)

    s = Seq(look=("at", DH)).k(0)
    s.k(8, R=pr("chest", (0, 0, 0), "open"))
    for f, dx in ((12, 0.06), (16, -0.06), (20, 0.06)):
        s.k(f, R=pr("chest", (dx, 0, 0), "open"))
    s.k(26, R=pr("chest", (0, 0.02, -0.10), "open"))
    s.k(36, R=pr("down"))
    add("ap_quiet_wave", 36, s)

    s = Seq(look=("at", DH)).k(0)
    s.k(8, R=pr("beckon"), lean=2)
    s.k(14, R=pr("beckon_curl"), nod=-2)
    s.k(20, R=pr("beckon"), nod=0)
    s.k(26, R=pr("beckon_curl"), nod=-2)
    s.k(36, R=pr("down"), lean=0, nod=0)
    add("ap_beckon", 36, s, curls=[14, 26])

    s = Seq(look=("at", DH)).k(0)
    s.k(24, lean=-4, look=("at", DH + V(0, 0, 0.10)))
    s.k(48, lean=-5, look=("at", DH + V(0, 0, 0.12)))
    s.k(52, lean=-4.5, nod=3)
    s.k(56, lean=-4, nod=-8, look=("at", DH + V(0, 0, -0.1)))
    s.k(62, nod=-2)
    s.k(72, lean=-3, nod=0, look=("at", DH))
    add("ap_awe", 72, s, nod=56)

    # ================= utility =================
    wf = walk_in_place_fn(24)
    add("walk", 24, fn=wf, loop=True, utility=True, speed_mps=round(wf.speed, 3),
        note="walk in place, root at the origin; move the node forward (-Y local) at speed_mps")
    return M


ORDER = ["t0_countoff_finger", "t0_countoff_verbal", "t0_clap_off", "t0_conduct", "t0_pace", "t0_lean_listen",
         "t0_cue_section", "t0_watch_other", "t0_stare_drummer", "t0_check_watch", "t0_chair_rest",
         "t1_flick", "t1_wave_off", "t1_fist_cutoff", "t1_clap_stop", "t1_fist_slam",
         "t2_judging_stare", "t2_score_point", "t2_point_player", "t2_hunt_sections", "t2_forced_calm",
         "t2_switch_drummer", "t2_impatient_watch",
         "t3_loom", "t3_whisper", "t3_crouch", "t3_circle_kit", "t3_clap_in_face", "t3_fake_warmth",
         "t4_shout", "t4_charge", "t4_sweep_stand", "t4_throw_music", "t4_door_point", "t4_storm_out",
         "t5_chair_throw", "t5_rhythm_slaps", "t5_cowbell", "t5_drum_throw", "t5_cymbal_throw",
         "ap_nod", "ap_grin", "ap_quiet_wave", "ap_beckon", "ap_awe"]
UTILITY = ["walk"]


# ---------------------------------------------------------------------------
# Keying
# ---------------------------------------------------------------------------
def make_action(D, name, move):
    rig = D.rig
    scene = bpy.context.scene
    scene.render.fps = FPS
    if rig.animation_data is None:
        rig.animation_data_create()
    act = bpy.data.actions.new(name)
    act.use_fake_user = True
    rig.animation_data.action = act
    D.move_name = name
    prev = {}
    for f in range(0, move.length + 1):
        scene.frame_set(f)
        evaluate(D, move, f)
        for pb in rig.pose.bones:
            q = pb.matrix_basis.to_quaternion()
            if pb.name in prev and prev[pb.name].dot(q) < 0:
                q.negate()                       # keep quaternions on one hemisphere: no flips between keys
            prev[pb.name] = q.copy()
            pb.rotation_quaternion = q
            pb.keyframe_insert("rotation_quaternion", frame=f)
            if pb.name in ps2.LOC_BONES:
                pb.location = pb.matrix_basis.to_translation()
                pb.keyframe_insert("location", frame=f)
    for fc in ps2._fcurves(act):
        for kp in fc.keyframe_points:
            kp.interpolation = "LINEAR"
        if move.loop:
            fc.modifiers.new("CYCLES")
    act["loop"] = bool(move.loop)
    act.frame_range = (0, move.length)
    rig.animation_data.action = None
    ps2.reset_pose(rig)
    return act


# ---------------------------------------------------------------------------
# Clearance check: capsules for torso, neck, head and legs; hands, forearms and props measured against them
# ---------------------------------------------------------------------------
def _ring_at(z):
    rings = ps2.TORSO_RINGS
    reg_scale = {"upper": BODY["shoulder"], "shoulder": BODY["shoulder"], "chest": BODY["chest"],
                 "waist": BODY["waist"], "belt": BODY["waist"], "hip": BODY["hip"]}
    for i in range(len(rings) - 1):
        z0, z1 = rings[i][0], rings[i + 1][0]
        if z0 >= z >= z1:
            u = (z0 - z) / (z0 - z1)
            a, b = rings[i], rings[i + 1]
            rx = lerp(a[1] * reg_scale[a[4]], b[1] * reg_scale[b[4]], u)
            ry = lerp(a[2], b[2], u) * BODY["depth"]
            cy = lerp(a[3], b[3], u)
            return rx, ry, cy
    raise ValueError(z)


def body_capsules():
    """[(bone, part, a_rest, b_rest, radius)] in rest space (already scaled)."""
    caps = []
    for bone, z0, z1 in (("Hips", 0.91, 1.05), ("Spine", 1.05, 1.18), ("Chest", 1.18, 1.30), ("UpperChest", 1.30, 1.43)):
        zs = np.linspace(z0, z1, 5)
        rx = float(np.mean([_ring_at(z)[0] for z in zs]))
        ry = float(np.mean([_ring_at(z)[1] for z in zs]))
        cy = float(np.mean([_ring_at(z)[2] for z in zs]))
        off = max(rx - ry, 0.0)
        for sx in (-1, 1):
            caps.append((bone, "torso", sc((sx * off, cy, z0)), sc((sx * off, cy, z1)), ry * SC))
    hz = lambda z: ps2.CHIN_Z + (z - ps2.CHIN_Z) * HS
    caps.append(("Head", "head", sc((0, -0.015, hz(1.61))), sc((0, -0.015, hz(1.73))), 0.080 * HS * SC))
    caps.append(("Neck", "neck", sc((0, 0.004, 1.47)), sc((0, 0.0, 1.56)), 0.060 * BODY["neck"] * SC))
    for sd, sx in (("Left", 1), ("Right", -1)):
        caps.append((f"{sd}UpperLeg", "leg", sc((sx * ps2.HIP_X, 0, 0.86)), sc((sx * ps2.HIP_X, 0, 0.52)), 0.080 * BODY["leg"] * SC))
        caps.append((f"{sd}LowerLeg", "leg", sc((sx * ps2.HIP_X, 0, 0.48)), sc((sx * ps2.HIP_X, 0, 0.14)), 0.060 * BODY["leg"] * SC))
    return caps


def seg_dist(P, A, B):
    """Distances from points P (N,3) to segment AB."""
    ab = B - A
    t = np.clip(((P - A) @ ab) / max(ab @ ab, 1e-12), 0.0, 1.0)
    return np.linalg.norm(P - (A + t[:, None] * ab), axis=1)


def seg_seg(a0, a1, b0, b1, n=9):
    """Approximate distance between two segments (sampled along the first)."""
    P = np.linspace(a0, a1, n)
    d = seg_dist(P, b0, b1)
    return float(d.min())


class Clearance:
    def __init__(self, rig, props):
        self.rig = rig
        self.props = props
        self.caps = body_capsules()
        self.results = {}
        self.geo = {}

    def _deform(self, bone):
        pb = self.rig.pose.bones[bone]
        return pb.matrix @ pb.bone.matrix_local.inverted()

    def body(self):
        out = []
        cache = {}
        for bone, part, a, b, r in self.caps:
            if bone not in cache:
                cache[bone] = self._deform(bone)
            M = cache[bone]
            out.append((f"{part}:{bone}", np.array(M @ a), np.array(M @ b), r))
        return out

    def items(self):
        pbs = self.rig.pose.bones
        out = []
        for sd in ("Left", "Right"):
            lo, hd = pbs[f"{sd}LowerArm"], pbs[f"{sd}Hand"]
            mid_t = pbs[f"{sd}MiddleDistal"].tail
            out.append((f"{sd}Forearm", np.array(lo.head), np.array(lo.tail), 0.038 * BODY["arm"] * SC))
            out.append((f"{sd}Palm", np.array(hd.head), np.array(hd.tail), 0.026 * HAND_SCALE * SC))
            out.append((f"{sd}Fingers", np.array(pbs[f"{sd}MiddleProximal"].head), np.array(mid_t), 0.015 * HAND_SCALE * SC))
        return out

    def measure(self, move_name, move, f, hand_props=()):
        body = self.body()
        worst = []
        for iname, a0, a1, ir in self.items():
            for bname, b0, b1, br in body:
                d = seg_seg(a0, a1, b0, b1) - ir - br
                worst.append((d, iname, bname, (a0, a1, ir, b0, b1, br)))
        rig = self.rig
        for bone, (pname, _) in move.props.items():
            Mp = rig.pose.bones[bone].matrix
            cloud = self.props[pname]["cloud"]
            Mn = np.array(Mp)
            P = cloud @ Mn[:3, :3].T + Mn[:3, 3]
            for bname, b0, b1, br in body:
                d = float(seg_dist(P, b0, b1).min()) - br
                worst.append((d, pname, bname, None))
            if pname in hand_props:
                for iname, a0, a1, ir in self.items():
                    d = float(seg_dist(P, a0, a1).min()) - ir
                    worst.append((d, iname, pname, None))
        w = min(worst, key=lambda x: x[0])
        return w

    def run(self, D, name, move, hand_props=()):
        sc_ = bpy.context.scene
        rig = self.rig
        rig.animation_data.action = bpy.data.actions[name]
        best = (9.0, "", "", -1)
        for f in range(0, move.length + 1):
            sc_.frame_set(f)
            d, a, b, geo = self.measure(name, move, f, hand_props)
            if d < best[0]:
                best = (d, a, b, f)
                self.geo[name] = geo
        rig.animation_data.action = None
        self.results[name] = best
        return best


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
SHEET = [("t0_conduct", 6, "front"), ("t1_fist_slam", 12, "side"), ("t3_loom", 36, "loom"), ("t4_shout", 36, "side"),
         ("t4_sweep_stand", 14, "front"), ("t5_chair_throw", 25, "throw"), ("t5_chair_throw", 36, "throw"), ("t5_rhythm_slaps", 42, "loom")]
CAMS = {"front": ((1.25, -2.4, 1.45), (0.0, -0.3, 1.0), 30), "side": ((2.3, -1.3, 1.45), (0.0, -0.35, 1.05), 30), "loom": ((1.9, -0.55, 1.45), (0.0, -0.45, 1.25), 30),
        "throw": ((-2.4, -2.6, 2.3), (0.4, -0.9, 1.0), 22)}
PROP_OF = {"t5_chair_throw": {"PropR": "chair_folded"}, "t5_drum_throw": {"PropR": "spare_drum"},
           "t5_cymbal_throw": {"PropR": "cymbal"}, "t5_cowbell": {"PropR": "cowbell_stick", "PropL": "cowbell"},
           "t4_sweep_stand": {"PropR": "music_stand_knockable"}, "t4_throw_music": {"PropR": "sheet_music_pages"}}


def main():
    sc_ = ps2.reset_scene()
    col = ps2.new_collection("terence")
    prev = ps2.new_collection("preview_only")
    rig, mesh, tris, height = build_model(col)
    targets = room_targets()
    props = load_props()
    D = Director(rig, targets, props)
    table = moves(D)
    names = ORDER + UTILITY
    missing = [m for m in names if m not in table]
    extra = [m for m in table if m not in names]
    if missing or extra:
        raise RuntimeError(f"move table mismatch: missing {missing}, extra {extra}")
    for name, mv in table.items():
        if PROP_OF.get(name, {}) != {b: p for b, (p, _) in mv.props.items()}:
            raise RuntimeError(f"{name}: props {mv.props.keys()} disagree with PROP_OF")
    meta = {}
    for name in names:
        mv = table[name]
        make_action(D, name, mv)
        meta[name] = dict(frames=mv.length, seconds=round(mv.length / FPS, 3), loop=mv.loop, **mv.timing)
        print(f"{LOG} {name}: {mv.length} frames")
    if D.short:
        worst = max(D.short, key=lambda s: s[3])
        print(f"{LOG} contact targets out of reach: {len(D.short)} frames, worst {worst}")
        for s_ in D.short:
            print(f"{LOG}   short {s_}")
        bad = [s for s in D.short if s[3] > 0.03]
        if bad:
            raise RuntimeError(f"grips out of reach: {bad[:6]}")
    print(f"{LOG} max automatic hip drop to keep feet planted: {D.max_drop:.3f} m")

    # clearance check over every frame of every action
    chk = Clearance(rig, props)
    lines = []
    for name in names:
        hp = ("music_stand_knockable",) if name == "t4_sweep_stand" else ()
        d, a, b, f = chk.run(D, name, table[name], hand_props=hp)
        lines.append(f"{name:22s} {d:+.3f} m  ({a} vs {b} @ f{f})")
    print(f"{LOG} clearance (most negative = deepest overlap):")
    for ln in sorted(lines, key=lambda s: float(s.split()[1])):
        print(f"{LOG}   {ln}")
    worst_name = min(chk.results, key=lambda n: chk.results[n][0])
    worst = chk.results[worst_name]
    clear_summary = dict(worst_m=round(worst[0], 4), move=worst_name, frame=worst[3], pair=f"{worst[1]} vs {worst[2]}",
                         per_move={n: round(v[0], 4) for n, v in chk.results.items()})
    (OUT / f"terence_{VERSION}_clearance.json").write_text(json.dumps(clear_summary, indent=2))
    for n_, g_ in chk.geo.items():
        if g_ is not None and chk.results[n_][0] < CLEAR_MIN:
            print(f"{LOG}   geo {n_}: item {np.round(g_[0], 3)} -> {np.round(g_[1], 3)} r{g_[2]:.3f}; body {np.round(g_[3], 3)} -> {np.round(g_[4], 3)} r{g_[5]:.3f}")
    fails = {n: v for n, v in chk.results.items() if v[0] < CLEAR_MIN}
    if fails:
        raise RuntimeError(f"clearance below {CLEAR_MIN} m in {len(fails)} moves: " +
                           ", ".join(f"{n} {v[0]:+.3f} ({v[1]} vs {v[2]} f{v[3]})" for n, v in fails.items()))

    ps2.export_glb(OUT / f"terence_{VERSION}.glb", [rig, mesh], animations=True)
    (OUT / f"terence_{VERSION}_moves.json").write_text(json.dumps(dict(
        fps=FPS, bpm=120, height_m=round(height, 3), targets={k: [round(c, 3) for c in v] for k, v in targets.items()},
        triangles=tris, moves=meta), indent=2))

    # contact sheet
    prop_objs = {}
    for key, fname in PROP_FILES.items():
        before = set(bpy.data.objects)
        bpy.ops.import_scene.gltf(filepath=str(R / "props" / f"{fname}.glb"))
        new = [o for o in bpy.data.objects if o not in before]
        for o in new:
            o.hide_render = True
        prop_objs[key] = next(o for o in new if o.parent is None and not o.name.startswith("Icosphere"))
        if key == "cowbell":
            prop_objs["cowbell_stick"] = next(o for o in new if o.name.startswith("cowbell_stick"))
    bpy.ops.mesh.primitive_uv_sphere_add(radius=0.10, location=targets["NEAR_HEAD"], segments=12, ring_count=8)
    player = bpy.context.active_object
    player.name = "preview_player_head"
    for c_ in list(player.users_collection):
        c_.objects.unlink(player)
    prev.objects.link(player)
    ps2.setup_render(256, 256, samples=16)
    ps2.neutral_lights(prev, center=(0, -0.5, 1.0), power=250)
    for ld in [o for o in prev.objects if o.type == "LIGHT"]:
        ld.data.size = 20
    shots, labels = [], []
    for i, (name, frame, camk) in enumerate(SHEET):
        def setup(name=name, frame=frame, camk=camk):
            rig.animation_data.action = bpy.data.actions[name]
            sc_.frame_set(frame + 1)           # a new action at an unchanged frame number is not re-evaluated
            sc_.frame_set(frame)
            for o in prop_objs.values():
                for x in _tree(o):
                    x.hide_render = True
            for bone, key in PROP_OF.get(name, {}).items():
                prop_objs[key].matrix_world = rig.matrix_world @ rig.pose.bones[bone].matrix
                for x in _tree(prop_objs[key]):
                    x.hide_render = x.type == "EMPTY" or x.name.startswith("Icosphere")
                if key == "cowbell":
                    prop_objs["cowbell_stick"].hide_render = False
            player.hide_render = camk != "loom"
            bpy.context.view_layer.update()
        loc, tgt, lens = CAMS[camk]
        cam = ps2.add_camera(f"preview_cam_{i}_{name}", prev, loc, tgt, lens=lens)
        shots.append((cam, setup))
        labels.append(f"{name} {frame}")
    frames = ps2.render_frames(shots)
    ps2.compose_sheet(frames, OUT / f"terence_{VERSION}_sheet.png", cols=4, size=(1024, 512), labels=labels)
    rig.animation_data.action = bpy.data.actions["t0_conduct"]
    sc_.frame_set(0)
    bpy.ops.wm.save_as_mainfile(filepath=str(OUT / f"terence_{VERSION}.blend"))
    print(f"{LOG} done: {tris} tris, height {height:.3f} m, {len(names)} actions, worst clearance "
          f"{worst[0]:+.3f} m ({worst_name} f{worst[3]} {worst[1]} vs {worst[2]})")


def _tree(o):
    out = [o]
    for c in o.children:
        out.extend(_tree(c))
    return out


main()
