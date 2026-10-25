"""Terence v002: finger rig + prop bones + every move ID as an action (task B2 of OVERNIGHT.md).

Run (after prep_terence_textures.py, build_room.py and build_props.py):
    /Applications/Blender.app/Contents/MacOS/Blender --background --python build_terence_v002.py

v001 (terence/terence.glb) is left untouched. v002 rebuilds the same body and photo head on the
shared lib_ps2 humanoid, which adds Godot SkeletonProfileHumanoid finger bones, and two
extra root bones, PropR and PropL, that carry whatever prop a move uses (chair, cowbell,
cowbell stick, spare drum, cymbal, music stand, sheet music). In Godot, parent the prop's
glb under a BoneAttachment3D on PropR/PropL with an identity transform for the duration of
the move; at frame 0 the prop bone sits where the prop is expected to be.

Frames: Terence's node at spot_terence, facing -Y (the band). Targets:
    DRUMMER_HEAD  the player's head at spot_drummer, 1.15 m high (from the room markers)
    NEAR_HEAD     for close-up moves (t3_*, t5_rhythm_slaps, t5_cowbell) Godot first moves
                  Terence's node to stand NEAR_DIST in front of the player, facing them
Godot re-aims live; the defaults just make the authored motion land somewhere sensible.
No facial shape keys exist (the face is a photo texture), so faces are skipped.

Outputs: terence/terence_v002.glb, terence_v002.blend, terence_v002_sheet.png,
         terence/terence_v002_moves.json (length, loop, key timings per action, for ANIMATIONS.md)
"""

import json
import math
import sys
from pathlib import Path

import bpy
import numpy as np
from mathutils import Euler, Matrix, Vector

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib_ps2 as ps2  # noqa: E402

R = ps2.ROOT
OUT = R / "terence"
TEX_DIR = OUT / "textures"
VERSION = "v002"
FPS = 24
BEAT = 12
NEAR_DIST = 0.70
TRI_TARGET = 2300
Z = ps2.Z

# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------
FM = json.loads((TEX_DIR / "face_map.json").read_text())
S = FM["metres_per_px"]


def photo_uv(co):
    px = FM["center_x"] + co.x / S
    py = FM["top_y"] + (ps2.HEAD_TOP - co.z) / S
    x0, y0, x1, y1 = FM["crop"]
    return (px - x0) / (x1 - x0) * FM["photo_w"] / FM["tex_size"], 1.0 - (py - y0) / (y1 - y0)


def strip_uv(co):
    a = (math.atan2(co.y - ps2.HEAD_CENTER_Y, co.x) / (2 * math.pi)) % 1.0
    u0 = FM["photo_w"] / FM["tex_size"]
    return u0 + 0.06 + a * (1.0 - u0 - 0.12), min(max((co.z - 1.44) / (ps2.HEAD_TOP - 1.44), 0.0), 1.0)


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


def build_model(col):
    lib = ps2.MatLib(TEX_DIR, prefix="terence2_")
    textures(lib)
    P = dict(mats=dict(head="head", skin="skin", top="shirt", pants="pants", shoes="shoes", belt="belt"),
             sleeve="short", face_uv=photo_uv, back_uv=strip_uv,
             neck_front=False,   # the photo's neck is streaked with jaw shadow; plain skin reads cleaner
             back_threshold=-0.35, ears_front=False)   # sideways faces take plain skin, not a smeared photo
    b = ps2.Builder(lib)
    ps2.build_humanoid(b, P)
    # belt band: stretch the whole belt texture over its height so the buckle reads in one piece
    belt_i = b.mats.index("belt")
    zs = [r[0] for r in ps2.TORSO_RINGS if r[4] == "belt"]
    top_z = zs[0]
    bot_z = next(r[0] for r in ps2.TORSO_RINGS if r[0] < top_z)
    for f in b.bm.faces:
        if f.material_index == belt_i:
            for loop in f.loops:
                loop[b.uv].uv = (loop[b.uv].uv.x, (loop.vert.co.z - bot_z) / (top_z - bot_z))
            b.explicit.add(f)
    mesh = b.build("terence_mesh", col, smooth=True)
    bones = ps2.humanoid_bones(1.0)
    bones.update(ps2.prop_bones(["PropR", "PropL"]))
    rig = ps2.make_armature("terence_rig", bones, col)
    ps2.skin_mesh(mesh, rig, b.weight_list)
    tris = ps2.tri_count(mesh)
    if tris > TRI_TARGET:
        raise RuntimeError(f"terence: {tris} triangles > {TRI_TARGET}")
    return rig, mesh, tris


# ---------------------------------------------------------------------------
# Scene data: room targets and prop grips
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
    t = {
        "DRUMMER_HEAD": Vector((loc["spot_drummer"].x, loc["spot_drummer"].y, 1.15)),
        "NEAR_HEAD": Vector((0.0, -NEAR_DIST, 1.15)),
        "STOOL": Vector((loc["stool_terence"].x, loc["stool_terence"].y, 0.0)),
        "DOOR": Vector((loc["door_main"].x, loc["door_main"].y, 0.0)),
        "SAXES": loc["seat_sax_3"] + Vector((0, 0, 1.1)),
        "TROMBONES": loc["seat_trombone_2"] + Vector((0.4, 0, 1.3)),
        "TRUMPETS": loc["seat_trumpet_3"] + Vector((0, 0, 1.5)),
        "STAND": Vector((0.0, -0.5, 0.0)),
    }
    return t


def read_prop(path):
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=str(path))
    new = [o for o in bpy.data.objects if o not in before]
    root = next(o for o in new if o.parent is None)
    inv = root.matrix_world.inverted()
    out = {}
    for o in new:
        key = o.name.split(".")[0]
        if key not in out:
            out[key] = inv @ o.matrix_world
    for o in new:
        bpy.data.objects.remove(o, do_unlink=True)
    return out


# ---------------------------------------------------------------------------
# Choreography
# ---------------------------------------------------------------------------
def Rm(x=0, y=0, z=0):
    return Euler((math.radians(x), math.radians(y), math.radians(z)), "XYZ").to_matrix().to_4x4()


def Tm(v):
    return Matrix.Translation(Vector(v))


def lerp(a, b, t):
    return a + (b - a) * t


def arc(p0, p1, t, apex):
    return lerp(Vector(p0), Vector(p1), t) + Vector((0, 0, 4 * apex * t * (1 - t)))


# Right-arm presets in rest space (standing at the origin): wrist, hand direction, shape, pole.
# The left arm mirrors x. Presets ride the posed UpperChest, so they lean and turn with him.
PRESETS = {
    "down": ((-0.23, -0.03, 0.82), (0.05, -0.15, -1), "relaxed", (-0.3, 0.6, -1)),
    "hip": ((-0.21, 0.00, 1.00), (1, -0.3, -0.4), "relaxed", (-1, 0.5, 0)),
    "chest": ((-0.10, -0.30, 1.22), (0.15, -0.25, 1), "pinch", (-0.6, 0.4, -1)),
    "cross": ((0.10, -0.22, 1.22), (1, 0.2, 0.1), "relaxed", (-0.8, -0.3, -1)),
    "behind": ((-0.08, 0.16, 0.98), (1, 0, -0.3), "relaxed", (-0.5, 0.8, -0.4)),
    "up": ((-0.15, -0.25, 1.75), (0, 0, 1), "open", (-1, 0.3, -0.3)),
    "finger_up": ((-0.16, -0.36, 1.48), (0, -0.1, 1), "point", (-1, 0.3, -0.5)),
    "finger_beat": ((-0.16, -0.40, 1.40), (0, -0.4, 1), "point", (-1, 0.3, -0.5)),
    "fist_high": ((-0.18, -0.35, 1.62), (0, -0.1, 1), "fist", (-1, 0.3, -0.4)),
    "open_high": ((-0.20, -0.40, 1.52), (0, -0.2, 1), "open", (-1, 0.3, -0.4)),
    "clap": ((-0.025, -0.40, 1.27), (0.25, -0.4, 1), "open", (-1, 0.2, -0.6)),
    "clap_open": ((-0.17, -0.38, 1.27), (0.6, -0.3, 1), "open", (-1, 0.2, -0.6)),
    "watch": ((0.02, -0.33, 1.30), (1, -0.1, 0.1), "relaxed", (-0.8, 0.2, -1)),   # mirrored for the left
    "watch_tap": ((0.03, -0.31, 1.37), (0.5, 0, -1), "point", (-0.8, 0.3, -0.6)),
    "c1": ((-0.16, -0.42, 1.08), (0, -0.6, 0.6), "pinch", (-1, 0.4, -0.6)),
    "c2": ((0.02, -0.42, 1.20), (0.3, -0.6, 0.6), "pinch", (-1, 0.4, -0.6)),
    "c3": ((-0.36, -0.38, 1.20), (-0.3, -0.6, 0.6), "pinch", (-1, 0.4, -0.6)),
    "c4": ((-0.16, -0.38, 1.42), (0, -0.4, 1), "pinch", (-1, 0.4, -0.6)),
    "rebound": ((-0.14, -0.42, 1.22), (0, -0.6, 0.8), "pinch", (-1, 0.4, -0.6)),
    "palms_down": ((-0.24, -0.40, 1.08), (0.2, -1, 0), "open", (-1, 0.4, -0.4)),
    "flick_a": ((-0.14, -0.36, 1.30), (0, -0.5, 1), "open", (-1, 0.3, -0.6)),
    "flick_b": ((-0.26, -0.40, 1.36), (-1, -0.5, 0.5), "open", (-1, 0.3, -0.6)),
    "wave_out": ((-0.46, -0.22, 1.10), (-1, -0.2, -0.2), "open", (-1, 0.3, -0.2)),
    "fist_out": ((-0.36, -0.30, 1.10), (-0.5, -1, 0), "fist", (-1, 0.5, -0.5)),
    "beckon": ((-0.10, -0.46, 1.20), (0, -1, 0.2), "point", (-1, 0.4, -0.8)),
    "beckon_curl": ((-0.10, -0.44, 1.22), (0, -1, 0.3), "fist", (-1, 0.4, -0.8)),
    "pump_fwd": ((-0.20, -0.30, 1.05), (0, -1, 0.3), "fist", (-0.6, 0.8, -0.6)),
    "pump_back": ((-0.22, 0.10, 0.92), (0, 0.3, -1), "fist", (-0.6, 0.8, -0.6)),
    "thighs": ((-0.12, -0.24, 0.70), (0, -1, -0.5), "relaxed", (-0.5, 0.3, -1)),
}


def mirror(v):
    return Vector((-v[0], v[1], v[2]))


class Director:
    def __init__(self, rig, targets, props):
        self.rig = rig
        self.t = targets
        self.props = props
        self.prop_world = {}

    # pose dict -> ops
    def P(self, **d):
        return [("fn", lambda rig, d=d: self._apply(d))]

    def _apply(self, d):
        rig = self.rig
        self.prop_world = {}
        hips = Vector(d.get("hips", (0, 0, 0)))
        yaw = d.get("yaw", 0.0)
        ps2.move_hips(rig, hips)
        if yaw:
            ps2.turn(rig, "Hips", Z, yaw)
        ry = Matrix.Rotation(math.radians(yaw), 3, "Z")
        side_axis = ry @ ps2.X
        fwd_axis = ry @ Vector((0, -1, 0))
        lean = d.get("lean", 0.0)
        if lean:
            ps2.turn(rig, "Spine", side_axis, lean * 0.4)
            ps2.turn(rig, "Chest", side_axis, lean * 0.35)
            ps2.turn(rig, "UpperChest", side_axis, lean * 0.25)
        if d.get("tilt"):
            ps2.turn(rig, "Chest", fwd_axis, d["tilt"])
        if d.get("twist"):
            ps2.turn(rig, "UpperChest", Z, d["twist"])
        # legs: feet planted (default: under the hips at rest stance), knees forward
        feet = d.get("feet")
        for i, (sd, sx) in enumerate((("Left", 1), ("Right", -1))):
            if feet:
                ankle = Vector(feet[i])
            else:
                spread = d.get("spread", 0.0)
                ankle = Vector((hips.x, hips.y, 0)) + ry @ Vector((sx * (0.095 + spread), 0, 0)) + Vector((0, 0, 0.09))
            pole = ry @ Vector((sx * 0.25, -1, 0.1))
            ps2.ik_leg(rig, sd, ankle, pole)
            ps2.aim(rig, f"{sd}Foot", d.get("foot_dir", ry @ Vector((sx * 0.1, -1, -0.32))))
        # props
        for bone in ("PropR", "PropL"):
            if bone in d:
                name, M = d[bone]
                ps2.set_bone_matrix(rig, bone, M)
                self.prop_world[name] = Matrix(M)
        # arms
        for sd, key in (("Left", "L"), ("Right", "R")):
            self._arm(sd, d.get(key, ("preset", "down")))
        # head
        lk = d.get("look")
        if lk:
            if lk[0] == "at":
                self._look_at(Vector(lk[1]), yaw + d.get("twist", 0.0), lean, lk[2] if len(lk) > 2 else 0.0)
            else:
                ps2.look(rig, *lk)

    def _look_at(self, point, facing, lean, roll):
        head = self.rig.pose.bones["Head"].head
        v = point - head
        world_yaw = math.degrees(math.atan2(v.x, -v.y))
        rel = (world_yaw - facing + 180) % 360 - 180
        pitch = math.degrees(math.atan2(v.z, max(v.xy.length, 1e-4))) + lean
        # head and neck only: turning the chest here would drag the already-placed hands
        ps2.look(self.rig, max(-100, min(100, rel)), max(-60, min(45, pitch)), roll, 0.0)

    def _arm(self, sd, spec):
        rig = self.rig
        sx = 1 if sd == "Left" else -1
        kind = spec[0]
        up = rig.pose.bones[f"{sd}UpperArm"]
        lo = rig.pose.bones[f"{sd}LowerArm"]
        reach = (up.length + lo.length)
        if kind == "preset":
            wrist, hdir, shape, pole = PRESETS[spec[1]]
            off = Vector(spec[2]) if len(spec) > 2 else Vector()
            wrist, hdir, pole = Vector(wrist), Vector(hdir), Vector(pole)
            if sd == "Left":
                wrist, hdir, pole = mirror(wrist), mirror(hdir), mirror(pole)
                off = mirror(off)
            uc = rig.pose.bones["UpperChest"]
            Mc = uc.matrix @ uc.bone.matrix_local.inverted()
            target = Mc @ (wrist + off)
            hdir = Mc.to_3x3() @ hdir
            pole = Mc.to_3x3() @ pole
            if len(spec) > 3:
                shape = spec[3]
        elif kind == "at":
            target = Vector(spec[1])
            shape = spec[2] if len(spec) > 2 else "relaxed"
            hdir = Vector(spec[3]) if len(spec) > 3 and spec[3] else (target - up.head)
            pole = Vector(spec[4]) if len(spec) > 4 and spec[4] else Vector((sx * 0.8, 0.4, -1))
        elif kind == "point":
            v = (Vector(spec[1]) - up.head).normalized()
            target = up.head + v * reach * 0.93
            hdir, shape = v, spec[2] if len(spec) > 2 else "point"
            pole = Vector((sx * 0.4, 0.3, -1))
        elif kind == "grip":
            M = self.prop_world[spec[1]]
            g = M @ self.props[spec[1]][spec[2]].translation
            target = g + (up.head - g).normalized() * 0.06
            hdir = g - target
            shape = spec[3] if len(spec) > 3 else "grip"
            pole = Vector((sx * 0.9, 0.4, -0.8))
        else:
            raise ValueError(spec)
        ps2.ik_arm(rig, sd, target, pole)
        ps2.aim(rig, f"{sd}Hand", hdir)
        ps2.hand_shape(rig, sd, shape)


# ---------------------------------------------------------------------------
# Walking
# ---------------------------------------------------------------------------
def walk_keys(D, path, start, frames, step=0.55, every=3, look=None, arms="swing", extra=None, end_yaw=None):
    """Keys for walking along a polyline of local (x, y) points. Footfalls alternate left/right
    every `step` metres, each foot planted 0.10 m to its side of the path; the hips stay
    centred between the feet and drop just enough for the legs to reach; the swing foot
    lifts 8 cm; arms counter-swing (or hold a preset). Closed paths loop seamlessly."""
    pts = [Vector((p[0], p[1], 0)) for p in path]
    closed = (pts[0] - pts[-1]).length < 1e-6
    seglen = [(pts[i + 1] - pts[i]).length for i in range(len(pts) - 1)]
    total = sum(seglen)
    n = max(2, int(round(total / step / 2)) * 2)
    step = total / n
    leg = 0.83
    drop = leg - math.sqrt(max(leg * leg - (step / 2) ** 2, 0.0)) + 0.01

    def at(dist):
        dist = dist % total if closed else max(0.0, min(total, dist))
        for i, L in enumerate(seglen):
            if dist <= L or i == len(seglen) - 1:
                t = dist / L if L else 0
                return lerp(pts[i], pts[i + 1], min(t, 1.0)), (pts[i + 1] - pts[i]).normalized()
            dist -= L

    def yaw_of(dirv):
        return math.degrees(math.atan2(dirv.x, -dirv.y))

    def left_of(dv):
        return Vector((-dv.y, dv.x, 0))

    plants = []
    for i in range(n + 1):
        p, dv = at(step * i)
        sgn = 1 if i % 2 == 0 else -1          # even footfalls are the left foot
        plants.append(p + left_of(dv) * (0.10 * sgn) + Vector((0, 0, 0.09)))
    p0, dv0 = at(0.0)
    start_right = p0 - left_of(dv0) * 0.10 + Vector((0, 0, 0.09))

    def plant(i):
        if i < 0:
            return plants[n + i] if closed else start_right
        return plants[i]

    keys = []
    for f in range(0, frames + 1, every):
        u = f / frames
        j = min(int(u * n), n - 1)
        fr = u * n - j
        hips_dist = (j + fr - 0.5) * step
        p, dv = at(hips_dist)
        heading = (at(hips_dist + 0.15)[0] - at(hips_dist - 0.15)[0])
        yaw = yaw_of(heading.normalized() if heading.length > 1e-6 else dv)
        if end_yaw is not None and u > 0.9:
            yaw = lerp(yaw, end_yaw, (u - 0.9) / 0.1)
        lift = 0.08 * math.sin(math.pi * fr)
        moving_left = (j + 1) % 2 == 0
        swing_pos = lerp(plant(j - 1), plant(j + 1), fr) + Vector((0, 0, lift))
        support = plant(j)
        feet = [swing_pos, support] if moving_left else [support, swing_pos]
        swing = math.sin(math.pi * (j + fr))
        d = dict(hips=(p.x, p.y, -drop - 0.012 * math.sin(math.pi * fr)), yaw=yaw, feet=feet, lean=4)
        if arms == "swing":
            d["L"] = ("preset", "down", (0, 0.10 * swing, 0))
            d["R"] = ("preset", "down", (0, -0.10 * swing, 0))
        elif arms == "pump":
            d["L"] = ("preset", "pump_fwd" if swing > 0 else "pump_back")
            d["R"] = ("preset", "pump_back" if swing > 0 else "pump_fwd")
            d["lean"] = 14
        else:
            d["L"] = ("preset", arms)
            d["R"] = ("preset", arms)
        if look is not None:
            d["look"] = look(u) if callable(look) else look
        if extra:
            d.update(extra(u))
        keys.append((start + f, D.P(**d)))
    return keys


# ---------------------------------------------------------------------------
# Moves
# ---------------------------------------------------------------------------
def moves(D):
    t = D.t
    DH, NH = t["DRUMMER_HEAD"], t["NEAR_HEAD"]
    idle = dict(L=("preset", "down"), R=("preset", "down"))
    hips_hands = dict(L=("preset", "hip"), R=("preset", "hip"))
    M = {}      # name -> (keys, loop, timings)

    def add(name, keys, loop=False, **timing):
        M[name] = (keys, loop, timing)

    P = D.P
    # ---- tier 0 ----
    k = [(0, P(**idle)), (8, P(R=("preset", "finger_up"), L=("preset", "hip"), look=("at", t["SAXES"])))]
    for i, f in enumerate(range(12, 49, 6)):
        k.append((f, P(R=("preset", "finger_up" if i % 2 == 0 else "finger_beat"), L=("preset", "hip"), look=("at", t["SAXES"]))))
    k.append((60, P(**idle)))
    add("t0_countoff_finger", k, downbeats=[18, 30, 42], finger_up=[12, 24, 36, 48])

    k = [(0, P(**hips_hands))]
    for b in range(4):
        f = b * BEAT
        k += [(f + 2, P(L=("preset", "hip"), R=("preset", "chest", (0, 0, -0.04)), look=(0, -9))),
              (f + 8, P(L=("preset", "hip"), R=("preset", "chest"), look=(0, 0)))]
    k.append((48, P(**hips_hands)))
    add("t0_countoff_verbal", k, beats=[2, 14, 26, 38])

    k = [(0, P(**idle))]
    for b in range(4):
        f = 12 + b * BEAT
        k += [(f - 6, P(L=("preset", "clap_open"), R=("preset", "clap_open"), look=("at", t["SAXES"]))),
              (f, P(L=("preset", "clap"), R=("preset", "clap"), look=("at", t["SAXES"])))]
    k.append((60, P(**idle)))
    add("t0_clap_off", k, claps=[12, 24, 36, 48])

    seq = ["c1", "rebound", "c2", "c2", "c3", "c3", "c4", "c4", "c1"]
    k = []
    for i, name in enumerate(seq):
        f = i * 6
        bob = -2 if name == "c1" else 0
        k.append((f, P(R=("preset", name), L=("preset", "hip"), lean=4 + bob, look=("at", t["SAXES"]))))
    add("t0_conduct", k, loop=True, downbeats=[0, 48], beat_frames=BEAT)

    pace = [(0, 0), (1.2, 0), (1.4, 0.15), (1.2, 0.3), (0, 0.3), (-0.2, 0.15), (0, 0)]
    add("t0_pace", walk_keys(D, pace, 0, 96, arms="behind", look=lambda u: ("at", DH)), loop=True)

    k = []
    for f, sway in ((0, 0), (36, 1), (72, 0)):
        k.append((f, P(lean=10 + sway * 2, L=("preset", "hip"), R=("preset", "hip"), look=(15 + sway * 4, -6, 12))))
    add("t0_lean_listen", k, loop=True)

    k = [(0, P(**idle)), (10, P(R=("point", t["TRUMPETS"], "open"), L=("preset", "hip"), look=("at", t["TRUMPETS"]))),
         (24, P(R=("point", t["TRUMPETS"] + Vector((0, 0, 0.2)), "open"), L=("preset", "hip"), look=("at", t["TRUMPETS"]))),
         (36, P(**idle))]
    add("t0_cue_section", k, cue=10)

    k = []
    for f, dz in ((0, 0), (36, 0.08), (72, 0)):
        k.append((f, P(L=("preset", "cross"), R=("preset", "cross"), look=("at", t["SAXES"] + Vector((0.2 * dz, 0, dz))))))
    add("t0_watch_other", k, loop=True)

    k = [(f, P(**hips_hands, look=("at", DH + Vector((0, 0, 0.02 * i))))) for i, f in enumerate((0, 36, 72))]
    k[-1] = (72, P(**hips_hands, look=("at", DH)))
    add("t0_stare_drummer", k, loop=True)

    wrist_pt = Vector((-0.02, -0.33, 1.30))
    k = [(0, P(**idle)), (10, P(L=("preset", "watch"), R=("preset", "down"), look=("at", wrist_pt))),
         (34, P(L=("preset", "watch"), R=("preset", "down"), look=("at", wrist_pt))), (48, P(**idle))]
    add("t0_check_watch", k, look_at_watch=[10, 34])

    st = t["STOOL"]
    perch = dict(hips=(st.x, st.y + 0.03, 0.82 - 0.95), lean=-3,
                 feet=[st + Vector((0.16, -0.30, 0.09)), st + Vector((-0.06, -0.16, 0.27))],
                 L=("preset", "thighs"), R=("preset", "thighs"))
    k = [(0, P(**perch, look=("at", t["SAXES"]))), (36, P(**perch, look=("at", t["SAXES"] + Vector((0.4, 0, 0))))),
         (72, P(**perch, look=("at", t["SAXES"])))]
    add("t0_chair_rest", k, loop=True, note="hips sit on stool_terence")

    # ---- tier 1 ----
    add("t1_flick", [(0, P(**idle)), (6, P(R=("preset", "flick_a"), look=("at", DH))),
                     (10, P(R=("preset", "flick_b"), look=("at", DH))), (18, P(**idle, look=("at", DH)))], flick=10)
    add("t1_wave_off", [(0, P(**idle)), (8, P(R=("preset", "chest", (0, 0, 0), "open"), look=(-10, 0))),
                        (16, P(R=("preset", "wave_out"), look=(-22, -4))), (22, P(R=("preset", "chest", (0, 0, 0), "open"), look=(-22, -4))),
                        (30, P(**idle, look=(-10, 0)))], wave=16)
    add("t1_fist_cutoff", [(0, P(R=("preset", "c4"), L=("preset", "hip"))), (6, P(R=("preset", "open_high"), L=("preset", "hip"), look=("at", t["SAXES"]))),
                           (10, P(R=("preset", "fist_high"), L=("preset", "hip"), look=("at", t["SAXES"]))),
                           (24, P(R=("preset", "fist_high"), L=("preset", "hip"), look=("at", DH))), (30, P(**idle, look=("at", DH)))], cutoff=10)
    add("t1_clap_stop", [(0, P(**idle)), (6, P(L=("preset", "clap_open"), R=("preset", "clap_open"))),
                         (9, P(L=("preset", "clap"), R=("preset", "clap"), lean=4)),
                         (14, P(L=("preset", "clap", (0.03, 0, 0)), R=("preset", "clap", (-0.03, 0, 0)), look=("at", DH))),
                         (24, P(**idle, look=("at", DH)))], clap=9)
    desk = Vector((0.0, -0.46, 1.14))
    add("t1_fist_slam", [(0, P(**idle)), (8, P(R=("preset", "up", (0, -0.05, 0), "fist"), lean=-3, look=("at", desk))),
                         (12, P(R=("at", desk, "fist", (0, -0.3, -1)), lean=12, look=("at", desk))),
                         (20, P(R=("at", desk + Vector((0, 0, 0.02)), "fist", (0, -0.3, -1)), lean=10, look=("at", DH))),
                         (30, P(**idle, look=("at", DH)))], slam=12)

    # ---- tier 2 ----
    stare = dict(L=("preset", "cross"), R=("preset", "cross"), lean=-3)
    add("t2_judging_stare", [(0, P(**stare, look=("at", DH, 8))), (36, P(**stare, look=("at", DH + Vector((0, 0, 0.03)), 10))),
                             (72, P(**stare, look=("at", DH, 8)))], loop=True)
    add("t2_score_point", [(0, P(**idle)), (10, P(R=("point", desk), L=("preset", "hip"), lean=8, look=("at", desk))),
                           (16, P(R=("point", desk + Vector((0, 0, -0.03))), L=("preset", "hip"), lean=8, look=("at", desk))),
                           (20, P(R=("point", desk), L=("preset", "hip"), lean=8, look=("at", desk))),
                           (24, P(R=("point", desk + Vector((0, 0, -0.03))), L=("preset", "hip"), lean=8, look=("at", DH))),
                           (36, P(**idle, look=("at", DH)))], taps=[16, 24])
    add("t2_point_player", [(0, P(**idle, look=("at", DH))), (8, P(R=("point", DH), L=("preset", "hip"), look=("at", DH))),
                            (28, P(R=("point", DH + Vector((0, 0, 0.03))), L=("preset", "hip"), look=("at", DH))),
                            (36, P(**idle, look=("at", DH)))], point=8)
    hunt = [("SAXES", 0, -10, 0.0), ("SAXES", 24, -10, 0.0), ("TROMBONES", 32, 0, 0.12), ("TROMBONES", 56, 0, 0.12),
            ("TRUMPETS", 64, 8, -0.12), ("TRUMPETS", 88, 8, -0.12), ("SAXES", 96, -10, 0.0)]
    add("t2_hunt_sections", [(f, P(hips=(dx, 0, 0), yaw=yw, L=("preset", "behind"), R=("preset", "behind"), lean=6,
                                   look=("at", t[sec]))) for sec, f, yw, dx in hunt], loop=True)
    add("t2_forced_calm", [(0, P(**idle)), (12, P(L=("preset", "palms_down"), R=("preset", "palms_down"), lean=-2, look=(0, 6))),
                           (24, P(L=("preset", "palms_down", (0, 0, -0.12)), R=("preset", "palms_down", (0, 0, -0.12)), lean=3, look=(0, -10))),
                           (36, P(L=("preset", "palms_down", (0, 0, -0.12)), R=("preset", "palms_down", (0, 0, -0.12)), lean=2, look=("at", DH))),
                           (48, P(**hips_hands, look=("at", DH)))])
    away = Vector((2.6, 1.2, 1.2))
    add("t2_switch_drummer", [(0, P(**idle, look=("at", DH))), (8, P(R=("point", DH), L=("preset", "hip"), look=("at", DH))),
                              (18, P(R=("point", DH), L=("preset", "hip"), look=("at", DH))),
                              (26, P(R=("point", away, "open"), L=("preset", "hip"), twist=20, look=("at", DH))),
                              (40, P(**idle, look=("at", DH)))], point=8, sweep=26)
    tap = dict(L=("preset", "watch"), look=("at", wrist_pt))
    add("t2_impatient_watch", [(0, P(**idle)), (8, P(R=("preset", "down"), **tap)), (14, P(R=("preset", "watch_tap"), **tap)),
                               (20, P(R=("preset", "watch_tap", (0, 0, 0.06)), **tap)), (26, P(R=("preset", "watch_tap"), **tap)),
                               (32, P(R=("preset", "down"), L=("preset", "watch"), look=("at", DH))), (48, P(**idle, look=("at", DH)))], taps=[14, 26])

    # ---- tier 3 (node moved to NEAR_DIST in front of the player) ----
    loom = dict(lean=22, L=("preset", "behind"), R=("preset", "behind"))
    add("t3_loom", [(0, P(**loom, look=("at", NH))), (36, P(**loom, hips=(0, 0, -0.01), look=("at", NH))),
                    (72, P(**loom, look=("at", NH)))], loop=True)
    ear = NH + Vector((-0.09, 0.02, 0.02))
    shoulder = NH + Vector((-0.20, 0.04, -0.26))
    add("t3_whisper", [(0, P(**idle, look=("at", NH))),
                       (16, P(hips=(0, -0.10, -0.06), lean=34, twist=-12, R=("at", shoulder, "relaxed", (0, -0.3, -1)), L=("preset", "behind"), look=("at", ear))),
                       (56, P(hips=(0, -0.12, -0.07), lean=36, twist=-14, R=("at", shoulder, "relaxed", (0, -0.3, -1)), L=("preset", "behind"), look=("at", ear))),
                       (72, P(**idle, look=("at", NH)))], close=16)
    crouch = dict(hips=(0, 0.06, -0.46), spread=0.08, lean=14,
                  L=("at", Vector((0.17, -0.32, 0.50)), "relaxed", (0, -1, -0.4)), R=("at", Vector((-0.17, -0.32, 0.50)), "relaxed", (0, -1, -0.4)))
    add("t3_crouch", [(0, P(**crouch, look=("at", NH))), (36, P(**crouch, look=("at", NH + Vector((0, 0, 0.02))))),
                      (72, P(**crouch, look=("at", NH)))], loop=True)
    radius = 1.3
    c = Vector((0, -radius, 0))
    circle = [(c.x + radius * math.sin(a), c.y + radius * math.cos(a)) for a in np.linspace(0, 2 * math.pi, 17)]
    add("t3_circle_kit", walk_keys(D, circle, 0, 144, step=0.5, arms="behind", look=lambda u: ("at", c + Vector((0, 0, 1.15)))),
        loop=True, note=f"player head assumed {radius} m in front of the start")
    clap_at = NH + Vector((0, 0.17, 0.02))
    k = [(0, P(**idle, look=("at", NH)))]
    for b in range(4):
        f = 12 + b * BEAT
        k += [(f - 6, P(lean=16, L=("at", clap_at + Vector((0.16, 0.02, 0)), "open", (-0.5, -1, 0.4)),
                        R=("at", clap_at + Vector((-0.16, 0.02, 0)), "open", (0.5, -1, 0.4)), look=("at", NH))),
              (f, P(lean=18, L=("at", clap_at + Vector((0.025, 0, 0)), "open", (-0.3, -1, 0.6)),
                    R=("at", clap_at + Vector((-0.025, 0, 0)), "open", (0.3, -1, 0.6)), look=("at", NH)))]
    k.append((60, P(**idle, look=("at", NH))))
    add("t3_clap_in_face", k, claps=[12, 24, 36, 48])
    warm = dict(R=("at", shoulder, "relaxed", (0, -0.3, -1)), L=("preset", "chest", (0, 0, -0.05), "relaxed"), lean=8)
    add("t3_fake_warmth", [(0, P(**idle, look=("at", NH))), (16, P(**warm, look=("at", NH, -8))),
                           (36, P(**warm, look=("at", NH + Vector((0, 0, -0.03)), -10))), (56, P(**warm, look=("at", NH, -8))),
                           (72, P(**idle, look=("at", NH)))], hand_on_shoulder=[16, 56])

    # ---- tier 4 ----
    shout = dict(L=("preset", "fist_out"), R=("preset", "fist_out"))
    add("t4_shout", [(0, P(**idle, look=("at", DH))), (6, P(**shout, lean=14, look=("at", DH + Vector((0, 0, 0.2))))),
                     (12, P(**shout, lean=20, look=("at", DH + Vector((0, 0, 0.25))))), (24, P(**shout, lean=15, look=("at", DH + Vector((0, 0, 0.2))))),
                     (36, P(**shout, lean=20, look=("at", DH + Vector((0, 0, 0.25))))), (48, P(**idle, look=("at", DH)))], accents=[12, 36])
    dirv = Vector((DH.x, DH.y, 0)).normalized()
    add("t4_charge", walk_keys(D, [(0, 0), (dirv.x * 1.6, dirv.y * 1.6)], 0, 30, step=0.8, every=2, arms="pump",
                               look=("at", DH)), stop=30, distance_m=1.6)
    stand0 = Tm(t["STAND"]) @ Rm(z=180)
    piv = stand0 @ D.props["music_stand_knockable"]["pivot_tip"].translation

    def stand_at(ang):
        return Tm(piv) @ Matrix.Rotation(math.radians(ang), 4, Vector((1, 0, 0))) @ Tm(-piv) @ stand0

    desk_edge = lambda ang: stand_at(ang) @ D.props["music_stand_knockable"]["grip_l"].translation
    add("t4_sweep_stand", [(0, P(**idle, PropR=("music_stand_knockable", stand_at(0)), look=("at", DH))),
                           (8, P(R=("at", desk_edge(0) + Vector((-0.05, 0.05, 0.05)), "open"), lean=10, PropR=("music_stand_knockable", stand_at(0)), look=("at", DH))),
                           (12, P(R=("at", desk_edge(0) + Vector((0.05, -0.05, 0.0)), "open"), lean=16, twist=18, PropR=("music_stand_knockable", stand_at(25)), look=("at", DH))),
                           (20, P(R=("preset", "wave_out", (0.2, -0.1, -0.1)), lean=12, twist=22, PropR=("music_stand_knockable", stand_at(90)), look=("at", DH))),
                           (30, P(**idle, PropR=("music_stand_knockable", stand_at(90)), look=("at", DH)))], hit=12, lands=20)
    pages0 = Tm((0, -0.47, 1.12)) @ Rm(x=-65)
    pages_grip = lambda Mp: Mp @ D.props["sheet_music_pages"]["grip_r"].translation
    land = Vector((0.4, -2.2, 0.05))

    def pages_at(tt):
        return Tm(arc((0.05, -0.62, 1.35), land, tt, 0.4)) @ Rm(x=-65 + 160 * tt, z=40 * tt)

    k = [(0, P(**idle, PropR=("sheet_music_pages", pages0))),
         (8, P(R=("grip", "sheet_music_pages", "grip_r", "pinch"), lean=10, PropR=("sheet_music_pages", pages0), look=("at", pages0.translation))),
         (14, P(R=("grip", "sheet_music_pages", "grip_r", "pinch"), lean=4, PropR=("sheet_music_pages", Tm((-0.15, -0.30, 1.40)) @ Rm(x=-40)), look=("at", DH))),
         (18, P(R=("preset", "open_high", (0, -0.15, -0.1)), lean=12, PropR=("sheet_music_pages", pages_at(0.0)), look=("at", DH)))]
    for f in (22, 26, 30, 34):
        k.append((f, P(R=("preset", "down"), lean=6, PropR=("sheet_music_pages", pages_at((f - 18) / 16)), look=("at", DH))))
    add("t4_throw_music", k, release=18, lands=34)
    door = t["DOOR"]
    door_yaw = math.degrees(math.atan2(door.x, -door.y))
    door_pt = door + Vector((0, 0, 1.2))
    add("t4_door_point", [(0, P(**idle, look=("at", DH))), (12, P(R=("point", door_pt), L=("preset", "hip"), twist=door_yaw * 0.35, look=("at", door_pt))),
                          (30, P(R=("point", door_pt), L=("preset", "hip"), twist=door_yaw * 0.35, look=("at", DH))),
                          (40, P(R=("point", door_pt), L=("preset", "hip"), twist=door_yaw * 0.35, look=("at", DH))),
                          (48, P(**idle, look=("at", DH)))], point=12)
    stop = door - door.normalized() * 0.6
    add("t4_storm_out", walk_keys(D, [(0, 0), (stop.x * 0.5, stop.y * 0.5 - 0.3), (stop.x, stop.y)], 0, 72, step=0.65,
                                  look=lambda u: ("at", door_pt)), arrives=72)

    # ---- tier 5 ----
    chair0 = Tm((-0.62, 0.05, 0.0))
    chair_lift = Tm((-0.30, -0.25, 0.55)) @ Rm(x=-10)
    chair_over = Tm((-0.05, 0.25, 1.15)) @ Rm(x=-60)
    rel = Vector((-0.05, -0.55, 1.20))

    def chair_fly(tt):
        return Tm(arc(rel, DH + Vector((0, 0, -0.5)), tt, 0.5)) @ Rm(x=-60 - 300 * tt, z=90 * tt)

    grip_both = dict(L=("grip", "chair", "grip_l"), R=("grip", "chair", "grip_r"))
    k = [(0, P(**idle, PropR=("chair", chair0), look=("at", DH))),
         (10, P(hips=(-0.15, 0, -0.18), lean=30, **grip_both, PropR=("chair", chair0), look=("at", chair0.translation))),
         (20, P(lean=6, **grip_both, PropR=("chair", chair_lift), look=("at", DH))),
         (30, P(lean=-10, **grip_both, PropR=("chair", chair_over), look=("at", DH))),
         (36, P(lean=18, L=("preset", "open_high", (0, -0.2, -0.1)), R=("preset", "open_high", (0, -0.2, -0.1)), PropR=("chair", chair_fly(0.0)), look=("at", DH)))]
    for f in (40, 44, 48, 52):
        k.append((f, P(lean=12, **idle, PropR=("chair", chair_fly((f - 36) / 16)), look=("at", DH))))
    k.append((60, P(lean=4, **idle, PropR=("chair", chair_fly(1.0)), look=("at", DH))))
    add("t5_chair_throw", k, pickup=10, release=36, arrives=52)

    cheek = NH + Vector((-0.08, 0.07, 0.0))
    windup = NH + Vector((-0.48, -0.10, 0.12))
    k = [(0, P(**idle, look=("at", NH)))]
    impacts = [18, 42, 66, 84]
    for imp in impacts:
        k += [(imp - 6, P(lean=12, twist=14, R=("at", windup, "open", (0.4, 0.3, 0.6)), look=("at", NH))),
              (imp, P(lean=16, twist=-10, R=("at", cheek, "open", (1, 0.2, 0.3)), look=("at", NH))),
              (imp + 4, P(lean=14, twist=-16, R=("at", cheek + Vector((0.18, -0.04, -0.04)), "open", (1, 0, 0)), look=("at", NH)))]
    k.append((96, P(**idle, look=("at", NH))))
    add("t5_rhythm_slaps", k, impacts=impacts)

    bell = Tm(NH + Vector((0.28, 0.12, 0.05))) @ Rm(z=-30)
    stick_g = D.props["cowbell_stick"]["grip_r"].translation      # relative to the stick object
    hit = bell @ Vector((0.0, -0.03, 0.035))
    zdir = Vector((0.55, -0.15, -0.82)).normalized()

    def stick(lift):
        # stick's local +Z runs grip -> tip; the tip is 0.32 m beyond the grip
        tip = hit + Vector((0, 0, lift))
        rotm = zdir.to_track_quat("Z", "Y").to_matrix().to_4x4()
        origin = tip - zdir * 0.20
        return Tm(origin) @ rotm

    k = [(0, P(R=("preset", "down"), L=("grip", "cowbell", "grip_l", "fist"), PropL=("cowbell", bell), PropR=("cowbell_stick", stick(0.25)), look=("at", NH)))]
    hits = [12, 24, 36, 48, 60]
    for h in hits:
        for f, lift in ((h - 6, 0.22), (h, 0.0)):
            k.append((f, P(lean=10, L=("grip", "cowbell", "grip_l", "fist"), R=("grip", "cowbell_stick", "grip_r", "fist"),
                           PropL=("cowbell", bell), PropR=("cowbell_stick", stick(lift)), look=("at", NH))))
    k.append((72, P(lean=6, L=("grip", "cowbell", "grip_l", "fist"), R=("grip", "cowbell_stick", "grip_r", "fist"),
                    PropL=("cowbell", bell), PropR=("cowbell_stick", stick(0.25)), look=("at", NH))))
    add("t5_cowbell", k, hits=hits, note="cowbell on PropL, its stick (reparented) on PropR")

    drum0 = Tm((-0.45, -0.30, 0.0))
    drum_chest = Tm((0.0, -0.38, 1.05)) @ Rm(x=70)
    drum_over = Tm((0.0, -0.05, 1.72)) @ Rm(x=20)
    drel = Vector((0.0, -0.55, 1.65))

    def drum_fly(tt):
        return Tm(arc(drel, DH + Vector((0, 0.4, -0.55)), tt, 0.35)) @ Rm(x=20 + 400 * tt)

    grip_drum = dict(L=("grip", "spare_drum", "grip_l"), R=("grip", "spare_drum", "grip_r"))
    k = [(0, P(**idle, PropR=("spare_drum", drum0), look=("at", DH))),
         (10, P(hips=(-0.12, -0.05, -0.30), lean=34, spread=0.05, **grip_drum, PropR=("spare_drum", drum0), look=("at", drum0.translation))),
         (22, P(lean=4, **grip_drum, PropR=("spare_drum", drum_chest), look=("at", DH))),
         (30, P(lean=-8, **grip_drum, PropR=("spare_drum", drum_over), look=("at", DH))),
         (36, P(lean=18, L=("preset", "open_high", (0, -0.25, -0.1)), R=("preset", "open_high", (0, -0.25, -0.1)), PropR=("spare_drum", drum_fly(0.0)), look=("at", DH)))]
    for f in (40, 44, 48, 52):
        k.append((f, P(lean=10, **idle, PropR=("spare_drum", drum_fly((f - 36) / 16)), look=("at", DH))))
    k.append((60, P(**idle, PropR=("spare_drum", drum_fly(1.0)), look=("at", DH))))
    add("t5_drum_throw", k, pickup=10, release=36, arrives=52)

    cym0 = Tm((-0.45, -0.32, 0.95))
    cym_wind = Tm((0.35, -0.12, 1.20)) @ Rm(y=-70)
    crel = Vector((-0.20, -0.52, 1.30))

    def cym_fly(tt):
        return Tm(arc(crel, DH, tt, 0.15)) @ Rm(x=-10, z=900 * tt)

    k = [(0, P(**idle, PropR=("cymbal", cym0), look=("at", DH))),
         (8, P(lean=10, R=("grip", "cymbal", "grip_r", "pinch"), PropR=("cymbal", cym0), look=("at", cym0.translation))),
         (16, P(lean=4, twist=35, R=("grip", "cymbal", "grip_r", "pinch"), PropR=("cymbal", cym_wind), look=("at", DH))),
         (22, P(lean=12, twist=-25, R=("point", DH, "open"), PropR=("cymbal", cym_fly(0.0)), look=("at", DH)))]
    for f in (26, 30, 34):
        k.append((f, P(lean=8, twist=-20, R=("preset", "down"), PropR=("cymbal", cym_fly((f - 22) / 14)), look=("at", DH))))
    k.append((48, P(**idle, PropR=("cymbal", cym_fly(1.0)), look=("at", DH))))
    add("t5_cymbal_throw", k, pickup=8, release=22, arrives=36)

    # ---- approval ----
    add("ap_nod", [(0, P(**hips_hands, look=("at", DH))), (6, P(**hips_hands, look=("at", DH + Vector((0, 0, -0.35))))),
                   (12, P(**hips_hands, look=("at", DH))), (18, P(**hips_hands, look=("at", DH + Vector((0, 0, -0.3))))),
                   (24, P(**hips_hands, look=("at", DH)))], nods=[6, 18])
    add("ap_grin", [(0, P(**hips_hands, look=("at", DH))), (12, P(**hips_hands, lean=-3, look=("at", DH, 7))),
                    (24, P(**hips_hands, lean=-2, look=("at", DH + Vector((0, 0, -0.15)), 6))), (36, P(**hips_hands, look=("at", DH, 4)))])
    add("ap_quiet_wave", [(0, P(**idle, look=("at", DH))), (8, P(R=("preset", "chest", (0, 0, 0), "open"), look=("at", DH))),
                          (12, P(R=("preset", "chest", (0.06, 0, 0), "open"), look=("at", DH))), (16, P(R=("preset", "chest", (-0.06, 0, 0), "open"), look=("at", DH))),
                          (20, P(R=("preset", "chest", (0.06, 0, 0), "open"), look=("at", DH))), (36, P(**idle, look=("at", DH)))])
    add("ap_beckon", [(0, P(**idle, look=("at", DH))), (8, P(R=("preset", "beckon"), look=("at", DH))), (14, P(R=("preset", "beckon_curl"), look=("at", DH))),
                      (20, P(R=("preset", "beckon"), look=("at", DH))), (26, P(R=("preset", "beckon_curl"), look=("at", DH))),
                      (36, P(**idle, look=("at", DH)))], curls=[14, 26])
    add("ap_awe", [(0, P(**idle, look=("at", DH))), (24, P(**idle, lean=-4, look=("at", DH + Vector((0, 0, 0.1))))),
                   (48, P(**idle, lean=-5, look=("at", DH + Vector((0, 0, 0.12))))), (56, P(**idle, lean=-4, look=("at", DH + Vector((0, 0, -0.1))))),
                   (72, P(**idle, lean=-3, look=("at", DH))), ], nod=56)
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

PROP_FILES = {"chair": "chair", "cowbell": "cowbell", "spare_drum": "spare_drum", "cymbal": "cymbal",
              "sheet_music_pages": "sheet_music_pages", "music_stand_knockable": "music_stand_knockable"}

SHEET = [("t0_conduct", 0), ("t0_countoff_finger", 12), ("t1_fist_cutoff", 12), ("t2_point_player", 20),
         ("t3_crouch", 0), ("t4_shout", 12), ("t5_chair_throw", 30), ("t5_rhythm_slaps", 18)]


def main():
    sc = ps2.reset_scene()
    col = ps2.new_collection("terence")
    prev = ps2.new_collection("preview_only")
    rig, mesh, tris = build_model(col)
    targets = room_targets()
    props = {}
    for key, fname in PROP_FILES.items():
        data = read_prop(R / "props" / f"{fname}.glb")
        if key == "cowbell":
            stick_m = data["cowbell_stick"]
            props["cowbell_stick"] = {"grip_r": stick_m.inverted() @ data["grip_r"]}
            props["cowbell"] = {"grip_l": data["grip_l"]}
        else:
            props[key] = data
    if set(PROP_FILES) - set(props):
        raise RuntimeError("missing prop data")
    D = Director(rig, targets, props)
    table = moves(D)
    missing = [m for m in ORDER if m not in table]
    extra = [m for m in table if m not in ORDER]
    if missing or extra:
        raise RuntimeError(f"move table mismatch: missing {missing}, extra {extra}")
    meta = {}
    for name in ORDER:
        keys, loop, timing = table[name]
        ps2.make_action(rig, name, keys, fps=FPS, loop=loop)
        meta[name] = dict(frames=keys[-1][0], seconds=round(keys[-1][0] / FPS, 3), loop=loop, **timing)
        print(f"[terence_v002] {name}: {keys[-1][0]} frames")
    ps2.export_glb(OUT / f"terence_{VERSION}.glb", [rig, mesh], animations=True)
    (OUT / f"terence_{VERSION}_moves.json").write_text(json.dumps(dict(
        fps=FPS, bpm=120, targets={k: [round(c, 3) for c in v] for k, v in targets.items()},
        triangles=tris, moves=meta), indent=2))

    # contact sheet: 8 key poses with the props they use
    prop_objs = {}
    for key, fname in PROP_FILES.items():
        before = set(bpy.data.objects)
        bpy.ops.import_scene.gltf(filepath=str(R / "props" / f"{fname}.glb"))
        new = [o for o in bpy.data.objects if o not in before]
        for o in new:
            if o.name.startswith("Icosphere"):
                o.hide_render = True
        prop_objs[key] = next(o for o in new if o.parent is None)
        if key == "cowbell":
            prop_objs["cowbell_stick"] = next(o for o in new if o.name.startswith("cowbell_stick"))
    for o in prop_objs.values():
        for x in _tree(o):
            x.hide_render = True
    ps2.setup_render(256, 256, samples=16)
    ps2.neutral_lights(prev, center=(0, -0.5, 1.0), power=250)
    for ld in [o for o in prev.objects if o.type == "LIGHT"]:
        ld.data.size = 20
    shots, labels = [], []
    for name, frame in SHEET:
        def setup(name=name, frame=frame):
            rig.animation_data.action = bpy.data.actions[name]
            sc.frame_set(frame)
            used = table[name][0][0][1]
            for o in prop_objs.values():
                for x in _tree(o):
                    x.hide_render = True
            for bone in ("PropR", "PropL"):
                M = rig.pose.bones[bone].matrix
                if M.translation.length > 1e-4:
                    keyname = next((p for p in prop_objs if True and _prop_for(name, bone) == p), None)
                    if keyname:
                        prop_objs[keyname].matrix_world = rig.matrix_world @ M
                        for x in _tree(prop_objs[keyname]):
                            x.hide_render = x.type == "EMPTY" or x.name.startswith("Icosphere")
                        if keyname == "cowbell":   # its stick is shown separately on PropR
                            prop_objs["cowbell_stick"].hide_render = False
            bpy.context.view_layer.update()
        cam = ps2.add_camera(f"preview_cam_{name}", prev, (1.15, -2.1, 1.35), (0.0, -0.3, 0.95), lens=30)
        shots.append((cam, setup))
        labels.append(name)
    frames = ps2.render_frames(shots)
    ps2.compose_sheet(frames, OUT / f"terence_{VERSION}_sheet.png", cols=4, size=(1024, 512), labels=labels)
    rig.animation_data.action = bpy.data.actions["t0_conduct"]
    sc.frame_set(0)
    bpy.ops.wm.save_as_mainfile(filepath=str(OUT / f"terence_{VERSION}.blend"))
    print(f"[terence_v002] done: {tris} tris, {len(ORDER)} actions")


def _tree(o):
    out = [o]
    for c in o.children:
        out.extend(_tree(c))
    return out


def _prop_for(move, bone):
    table = {"t5_chair_throw": {"PropR": "chair"}, "t5_drum_throw": {"PropR": "spare_drum"},
             "t5_cymbal_throw": {"PropR": "cymbal"}, "t5_cowbell": {"PropR": "cowbell_stick", "PropL": "cowbell"},
             "t4_sweep_stand": {"PropR": "music_stand_knockable"}, "t4_throw_music": {"PropR": "sheet_music_pages"}}
    return table.get(move, {}).get(bone)


main()
