"""Sound-effect cue tables for Terence and the band, derived from the motion in the exported glbs.

Run with Blender (no arguments):
    /Applications/Blender.app/Contents/MacOS/Blender --background --python gen_sfx_cues.py

It imports terence/terence_v004.glb, the 16 band/band_<kind>_<n>.glb members and the prop glbs,
samples every frame of every action and detects the events that make a sound:

    feet       plants (a foot coming back to the floor after lifting), pivots (a planted foot turning)
    hands      claps (closest approach of both palms), slaps (right palm to the player's head),
               desk contacts (hand impacts on the music stand), taps (fingertip strokes on the
               watch or the score), strike swings (speed peaks that stop dead)
    props      pickups, releases, arrivals, landings, cowbell stick hits, flight whooshes
    body       big chest accelerations (cloth), heavy hip drops (body thud)
    band       instrument handling in raise / lower, the trombone slide, the spit valve shakes,
               page flips, chair or bench creaks on startles and big seated weight shifts

It writes sfx/sfx_cues.json (the contract the Godot project reads) and sfx/CUES.md (a review
sheet), checks its own output and spot-checks the detections against the key timings in
terence/terence_v004_moves.json and ANIMATIONS.md. Any failed check raises.

Frames are authored frames at 24 fps. Loop cues are strictly below the loop length. A `world:`
anchor is a point in the character's local frame in the same convention as the moves json
targets (Blender axes: the character faces -Y, Z is up).
"""

import json
import math
from collections import Counter
from pathlib import Path

import bpy
import numpy as np
from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree

ROOT = Path(__file__).resolve().parent
OUT_JSON = ROOT / "sfx" / "sfx_cues.json"
OUT_MD = ROOT / "sfx" / "CUES.md"
TERENCE_GLB = ROOT / "terence" / "terence_v004.glb"
TERENCE_META = ROOT / "terence" / "terence_v004_moves.json"
FPS = 24
LOG = "[sfx_cues]"

SFX = """foot_step_1 foot_step_2 foot_step_3 foot_step_4 foot_stomp foot_scuff clap_1 clap_2 clap_3 clap_big
slap_1 slap_2 slap_3 fist_slam_stand whoosh_small whoosh_big whoosh_spin cloth_rustle_1 cloth_rustle_2 body_thud
stand_rattle stand_crash paper_grab paper_flutter paper_land page_flip chair_grab chair_impact drum_grab drum_impact
cymbal_grab cymbal_impact cowbell_hit_1 cowbell_hit_2 watch_tap finger_snap chair_creak instrument_handle sax_keys
trumpet_valves trombone_slide spit_valve guitar_handle bass_handle piano_bench_creak door_slam""".split()
SFX_SET = set(SFX)
GAIN_RANGE = (-18.0, 3.0)
JITTER_RANGE = (0.0, 0.2)

# Which prop rides which prop bone in which move (PROP_OF in build_terence_v004.py). The motion
# check below verifies that the prop bones move only in these moves.
PROP_OF = {"t5_chair_throw": {"PropR": "chair_folded"}, "t5_drum_throw": {"PropR": "spare_drum"},
           "t5_cymbal_throw": {"PropR": "cymbal"}, "t5_cowbell": {"PropR": "cowbell_stick", "PropL": "cowbell"},
           "t4_sweep_stand": {"PropR": "music_stand_knockable"}, "t4_throw_music": {"PropR": "sheet_music_pages"}}
THROWN = ("chair_folded", "spare_drum", "cymbal", "sheet_music_pages")
# Sounds of each prop's events: (sfx, gain dB, pitch jitter)
PROP_SFX = {
    "chair_folded": {"grab": ("chair_grab", -4.0, 0.05), "arrive": ("chair_impact", 0.0, 0.04), "land": ("chair_impact", -5.0, 0.08)},
    "spare_drum": {"grab": ("drum_grab", -4.0, 0.05), "arrive": ("drum_impact", 1.0, 0.04)},
    "cymbal": {"grab": ("cymbal_grab", -4.0, 0.04), "arrive": ("cymbal_impact", 0.0, 0.03)},
    "sheet_music_pages": {"grab": ("paper_grab", -6.0, 0.06), "release": ("paper_flutter", -4.0, 0.06),
                          "land": ("paper_land", -6.0, 0.06)},
    "music_stand_knockable": {"hit": ("stand_rattle", 0.0, 0.04), "land": ("stand_crash", 2.0, 0.03)},
}
# Close-up moves: Godot places Terence 0.70 m in front of the player, whose head is NEAR_HEAD
# (ANIMATIONS.md, conventions). Slaps are only looked for in these.
CLOSE_UP_PREFIX = ("t3_",)
CLOSE_UP = ("t5_rhythm_slaps", "t5_cowbell")

BAND_KINDS = ("alto_sax", "tenor_sax", "baritone_sax", "trumpet", "trombone", "piano", "guitar", "bass")
BAND_ACTIONS = ("rest", "raise", "play_loop", "lower", "freeze", "startle", "look_down", "look_at_drummer",
                "flip_page", "arms_rest")
BAND_LOOPS = ("rest", "play_loop", "arms_rest")          # loop flags, as in ANIMATIONS.md / gen_animations_md.py
SAXES = ("alto_sax", "tenor_sax", "baritone_sax")
# Band key timings quoted in ANIMATIONS.md, used only for the spot checks
BAND_DOC = {"raise_meets_mouth": 13, "flip_pinch": 10, "spit_shakes": (16, 22, 28), "startle_peak": 4}

TRACK = ("Hips", "Spine", "Chest", "UpperChest", "Head", "LeftFoot", "RightFoot", "LeftToes", "RightToes",
         "LeftHand", "RightHand", "LeftLowerArm", "RightLowerArm", "LeftMiddleProximal", "RightMiddleProximal",
         "PropR", "PropL", "Instrument", "InstrumentSlide")
TAILS = ("RightIndexDistal", "RightThumbDistal", "RightMiddleDistal", "LeftThumbDistal", "LeftMiddleDistal")

# minimum gap (frames) between two cues of the same sound family on the same anchor
GAP = {"foot_step": 5, "foot_stomp": 6, "foot_scuff": 6, "clap": 5, "slap": 8, "whoosh": 6, "cloth_rustle": 30,
       "chair_creak": 24, "piano_bench_creak": 24, "cowbell_hit": 6, "trombone_slide": 4, "spit_valve": 4}
GAP_DEFAULT = 4
VARIANTS = {"foot_step": ["foot_step_1", "foot_step_2", "foot_step_3", "foot_step_4"],
            "clap": ["clap_1", "clap_2", "clap_3"], "slap": ["slap_1", "slap_2", "slap_3"],
            "cowbell_hit": ["cowbell_hit_1", "cowbell_hit_2"], "cloth_rustle": ["cloth_rustle_1", "cloth_rustle_2"]}


def family(sfx):
    for fam, names in VARIANTS.items():
        if sfx == fam or sfx in names:
            return fam
    if sfx.startswith("whoosh_"):
        return "whoosh"
    return sfx


# ---------------------------------------------------------------------------
# Sampling
# ---------------------------------------------------------------------------
class Clip:
    """Every frame of one action: bone matrices (armature space = the character's local frame).
    Loops drop their last frame (it repeats frame 0, checked) and are analysed circularly."""

    def __init__(self, name, last, loop, mats, tails):
        self.name, self.last, self.loop = name, last, loop
        self.F = last if loop else last + 1           # analysed frames
        self.M = {b: m[:self.F] for b, m in mats.items()}
        self.T = {b: t[:self.F] for b, t in tails.items()}

    def pos(self, b):
        return self.M[b][:, :3, 3]

    def prev(self, f):
        return (f - 1) % self.F if self.loop else f - 1

    def nxt(self, f):
        return (f + 1) % self.F if self.loop else f + 1

    def has(self, f):
        return self.loop or 0 <= f < self.F

    def wrap(self, f):
        return f % self.F if self.loop else f

    def dist(self, a, b):
        d = abs(a - b)
        return min(d, self.F - d) if self.loop else d

    def speed(self, p):
        """speed[f] = distance moved from frame f-1 to f (m/frame); 0 at a one-shot's first frame."""
        v = np.linalg.norm(p - np.roll(p, 1, axis=0), axis=1)
        if not self.loop:
            v[0] = 0.0
        return v


def import_rig(path):
    bpy.ops.wm.read_homefile(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=str(path))
    rigs = [o for o in bpy.data.objects if o.type == "ARMATURE"]
    if len(rigs) != 1:
        raise RuntimeError(f"{path.name}: expected one armature, got {[o.name for o in rigs]}")
    rig = rigs[0]
    ad = rig.animation_data
    if ad is None:
        raise RuntimeError(f"{path.name}: armature has no animation data")
    for t in list(ad.nla_tracks):        # the importer stacks every action as an NLA strip; sample them one at a time
        ad.nla_tracks.remove(t)
    return rig


def sample_glb(path, loops):
    """{action: Clip} plus the set of bone names. loops: {action: bool}."""
    rig = import_rig(path)
    bones = {b.name for b in rig.data.bones}
    sc = bpy.context.scene
    if sc.render.fps != FPS:
        raise RuntimeError(f"{path.name}: scene fps {sc.render.fps}, expected {FPS}")
    clips = {}
    for act in bpy.data.actions:
        if act.name not in loops:
            raise RuntimeError(f"{path.name}: unexpected action {act.name}")
        rig.animation_data.action = act
        if not act.slots:
            raise RuntimeError(f"{path.name}: action {act.name} has no slot")
        rig.animation_data.action_slot = act.slots[0]
        f0, f1 = act.frame_range
        if abs(f0) > 1e-6 or abs(f1 - round(f1)) > 1e-6:
            raise RuntimeError(f"{path.name}: {act.name} frame range {tuple(act.frame_range)} is not 0..integer")
        last = int(round(f1))
        track = [b for b in TRACK if b in bones]
        mats = {b: np.zeros((last + 1, 4, 4)) for b in track}
        tails = {b: np.zeros((last + 1, 3)) for b in TAILS if b in bones}
        for f in range(last + 1):
            sc.frame_set(f)
            for b in track:
                mats[b][f] = np.array(rig.pose.bones[b].matrix)
            for b in tails:
                tails[b][f] = tuple(rig.pose.bones[b].tail)
        loop = loops[act.name]
        if loop:
            worst = max(float(np.abs(m[0] - m[last]).max()) for m in mats.values())
            if worst > 2e-3:
                raise RuntimeError(f"{path.name}: loop {act.name} is not seamless (frame 0 vs {last}: {worst:.4f})")
        clips[act.name] = Clip(act.name, last, loop, mats, tails)
    missing = set(loops) - set(clips)
    if missing:
        raise RuntimeError(f"{path.name}: missing actions {sorted(missing)}")
    return clips, bones


def load_props():
    """Per prop: centre (bbox centre of its meshes), grip_r points, triangle mesh, in the prop's own
    frame (= the prop bone's frame). The cowbell stick is reported in its own frame, as it rides PropR."""
    out = {}
    for name in ("chair_folded", "spare_drum", "cymbal", "cowbell", "sheet_music_pages", "music_stand_knockable"):
        bpy.ops.wm.read_homefile(use_empty=True)
        bpy.ops.import_scene.gltf(filepath=str(ROOT / "props" / f"{name}.glb"))
        objs = [o for o in bpy.data.objects if not o.name.startswith("Icosphere")]
        roots = [o for o in objs if o.parent is None]
        if len(roots) != 1:
            raise RuntimeError(f"{name}.glb: expected one root, got {[o.name for o in roots]}")
        parts = {name: [roots[0]] + [o for o in objs if o is not roots[0] and not _under(o, "cowbell_stick")]}
        if name == "cowbell":
            stick = [o for o in objs if o.name.split(".")[0] == "cowbell_stick"]
            if len(stick) != 1:
                raise RuntimeError("cowbell.glb: expected one cowbell_stick")
            parts["cowbell_stick"] = [o for o in objs if _under(o, "cowbell_stick")]
            frames = {name: roots[0], "cowbell_stick": stick[0]}
        else:
            frames = {name: roots[0]}
        for key, group in parts.items():
            inv = frames[key].matrix_world.inverted()
            verts, tris, grips, part = [], [], [], {}
            for o in group:
                if o.type == "MESH":
                    M = inv @ o.matrix_world
                    base = len(verts)
                    verts += [tuple(M @ v.co) for v in o.data.vertices]
                    part[o.name.split(".")[0]] = np.array(verts[base:])
                    o.data.calc_loop_triangles()
                    tris += [tuple(base + i for i in t.vertices) for t in o.data.loop_triangles]
                elif o.type == "EMPTY" and o.name.split(".")[0] == "grip_r":
                    grips.append(tuple((inv @ o.matrix_world).translation))
            if not verts:
                raise RuntimeError(f"{name}.glb: no mesh for {key}")
            V = np.array(verts)
            out[key] = dict(centre=(V.min(0) + V.max(0)) / 2, verts=V, grips=np.array(grips), parts=part,
                            bvh=BVHTree.FromPolygons([Vector(v) for v in verts], tris))
    return out


def _under(o, name):
    while o is not None:
        if o.name.split(".")[0] == name:
            return True
        o = o.parent
    return False


def xform(M, p):
    return M[:3, :3] @ p + M[:3, 3]


def runs(mask, loop):
    """Lists of consecutive True indices, joined across the seam for loops."""
    n = len(mask)
    if not mask.any():
        return []
    if loop and mask.all():
        return [list(range(n))]
    order = [(int(np.argmin(mask)) + i) % n for i in range(n)] if loop else list(range(n))
    out, cur = [], []
    for i in order:
        if mask[i]:
            cur.append(i)
        elif cur:
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return out


def ev(frame, sfx, anchor, gain, jitter, why, strength=1.0):
    return dict(frame=int(frame), sfx=sfx, anchor=anchor, gain_db=float(gain), pitch_jitter=float(jitter),
                why=why, strength=float(strength))


# ---------------------------------------------------------------------------
# Terence detectors
# ---------------------------------------------------------------------------
GROUND_EPS = 0.004       # a foot this far above the floor is lifted
SHUFFLE_LIFT = 0.03      # plants lifted less than this are shuffles (scuffs)
STRIDE = 0.45            # a plant this far from its liftoff is a walking stride


def foot_events(c, ground):
    hz = c.pos("Hips")[:, 2]
    hxy = c.pos("Hips")[:, :2]
    plants, pivots = [], []

    def fall(k):               # hip drop (cm) from frame k-1 to k
        if not c.has(k) or not c.has(k - 1) or (not c.loop and k - 1 < 0):
            return 0.0
        return (hz[c.wrap(k - 1)] - hz[c.wrap(k)]) * 100

    def drop_cm(f):            # fastest hip drop (cm/frame) into frames f-1..f+1
        return max(fall(k) for k in (f - 1, f, f + 1))

    def jolt(f):               # a heel strike that lands hard: the hips drop >= 2 cm in one frame out of a steady gait
        return any(fall(k) >= 2.0 and fall(k - 1) <= 0.6 for k in (f - 1, f, f + 1))

    for side in ("Left", "Right"):
        p = c.pos(f"{side}Foot")
        z = p[:, 2]
        up = z > ground + GROUND_EPS
        for f in range(c.F):
            if up[f] or (not c.loop and f == 0) or not up[c.prev(f)]:
                continue
            k, swing = c.prev(f), []
            while up[k] and len(swing) < c.F:
                swing.append(k)
                if not c.loop and k == 0:
                    break
                k = c.prev(k)
            lift = float(z[swing].max() - ground)
            dist = float(np.linalg.norm(p[f, :2] - p[k, :2]))
            a = c.wrap(f - 2) if c.loop else max(f - 2, 0)
            span = 2 if c.loop else max(f - a, 1)
            hv = float(np.linalg.norm(hxy[f] - hxy[a])) / span * 100          # hips speed into the plant, cm/f
            e = c.wrap(f + 1) if c.has(f + 1) else f
            hvz = (hz[e] - hz[a]) * 100 / 3                                   # hips rising (+) or sinking (-), cm/f
            d = drop_cm(f)
            if lift < SHUFFLE_LIFT:
                kind = "scuff"
            elif dist >= STRIDE:
                kind = "stomp" if jolt(f) else "step"
            else:
                kind = "stomp" if d >= 2.0 or (hv >= 2.5 and hvz <= 0.2) else "step"
            plants.append(dict(frame=f, side=side, kind=kind, lift=lift, dist=dist, drop=d, hv=hv))
        # pivots: the planted foot turning on the floor
        y = c.M[f"{side}Foot"][:, :3, 1]
        yaw = np.degrees(np.unwrap(np.arctan2(y[:, 0], -y[:, 1])))
        rate = yaw - np.roll(yaw, 1)
        rate = (rate + 180) % 360 - 180
        grounded = (~up) & np.roll(~up, 1)
        if not c.loop:
            rate[0], grounded[0] = 0.0, False
        rate = np.where(grounded, rate, 0.0)
        for run in runs(np.abs(rate) > 1.0, c.loop):
            pivots.append(dict(run=run, side=side, rate=rate, turn=float(rate[run].sum())))

    out = []
    # both feet planting together (a stance change) sound once, from the bigger step
    plants.sort(key=lambda q: (q["frame"], q["side"]))
    keep = []
    for q in plants:
        twin = next((k for k in keep if k["kind"] == q["kind"] and k["side"] != q["side"]
                     and c.dist(k["frame"], q["frame"]) <= 1), None)
        if twin is None:
            keep.append(q)
        elif q["lift"] * q["dist"] > twin["lift"] * twin["dist"]:
            keep[keep.index(twin)] = q
    loud = [q for q in keep if q["kind"] != "scuff"]
    for q in keep:
        anchor = f"{q['side']}Foot"
        if q["kind"] == "scuff":
            if any(c.dist(q["frame"], s["frame"]) <= 2 for s in loud):
                continue
            out.append(ev(q["frame"], "foot_scuff", anchor, -14.0, 0.06, f"shuffle lift {q['lift'] * 100:.1f} cm",
                          q["lift"] * q["dist"]))
        elif q["kind"] == "stomp":
            g = -2.0 + min(max(q["drop"] - 2.0, 0.0), 2.0)
            out.append(ev(q["frame"], "foot_stomp", anchor, g, 0.04,
                          f"plant, step {q['dist']:.2f} m, hips drop {q['drop']:.1f} cm/f, hips {q['hv']:.1f} cm/f",
                          q["lift"] * q["dist"]))
        else:
            g = (-6.0 if q["dist"] >= STRIDE else -10.0) + 1.5 * min(q["drop"], 2.0)
            out.append(ev(q["frame"], "foot_step", anchor, g, 0.06,
                          f"plant, step {q['dist']:.2f} m, lift {q['lift'] * 100:.1f} cm", q["lift"] * q["dist"]))
    # a pivot scuffs at its fastest turning frame clear of the plants (a turn on a plant frame is
    # heard as the plant), and one turn sounds once even when both feet pivot through it
    scuffs = []
    for q in pivots:
        if abs(q["turn"]) < 15.0:
            continue
        clear = [i for i in q["run"] if not any(c.dist(i, s["frame"]) <= (3 if s["side"] == q["side"] else 2)
                                                for s in plants if s["kind"] != "scuff" or s["side"] == q["side"])]
        if not clear:
            continue
        f = max(clear, key=lambda i: abs(q["rate"][i]))
        if abs(q["rate"][f]) >= 6.0:
            scuffs.append(ev(f, "foot_scuff", f"{q['side']}Foot", -12.0, 0.06,
                             f"pivot {q['turn']:+.0f} deg, {abs(q['rate'][f]):.0f} deg/f", abs(q["turn"])))
    for e in sorted(scuffs, key=lambda e: -e["strength"]):
        if all(c.dist(e["frame"], k["frame"]) >= 10 for k in out if k["why"].startswith("pivot")):
            out.append(e)
    return out, plants


def clap_events(c):
    d = np.linalg.norm(c.pos("LeftMiddleProximal") - c.pos("RightMiddleProximal"), axis=1)
    out = []
    for f in range(c.F):
        if not c.loop and f == 0:
            continue
        close = d[c.prev(f)] - d[f]
        if d[f] < 0.04 and close >= 0.05:
            big = close >= 0.15
            out.append(ev(f, "clap_big" if big else "clap", "RightHand", 2.0 if big else 0.0, 0.04,
                          f"palms close {close * 100:.0f} cm into frame, gap {d[f] * 100:.1f} cm", close))
    return out


def slap_events(c, near_head):
    d = np.linalg.norm(c.pos("RightMiddleProximal") - near_head, axis=1)
    out = []
    for f in range(c.F):
        if not c.has(f - 1) or not c.has(f + 1) or (not c.loop and f == 0):
            continue
        a = d[c.prev(f)] - d[f]
        if d[f] < 0.12 and d[f] <= d[c.wrap(f + 1)] and a >= 0.08:
            out.append(ev(f, "slap", "RightHand", 1.0, 0.03, f"palm {d[f] * 100:.0f} cm from the head centre, "
                          f"closing {a * 100:.0f} cm/f", a))
    return out


def desk_distance(c, stand_pose, props):
    """Per frame: shortest distance from the right palm / index tip to the music stand mesh."""
    bvh = props["music_stand_knockable"]["bvh"]
    knocked = PROP_OF.get(c.name, {}).get("PropR") == "music_stand_knockable"
    pts = (c.pos("RightMiddleProximal"), c.T["RightIndexDistal"])
    out = np.zeros(c.F)
    for f in range(c.F):
        M = c.M["PropR"][f] if knocked else stand_pose
        inv = np.linalg.inv(M)
        out[f] = min(bvh.find_nearest(Vector(xform(inv, p[f])))[3] for p in pts)
    return out, knocked


def desk_events(c, stand_pose, props):
    """Hand impacts on the music stand (fist slam, or the strike that knocks it over) and fingertip
    taps on the score."""
    dd, knocked = desk_distance(c, stand_pose, props)
    palm = c.pos("RightMiddleProximal")
    v = c.speed(palm)
    hz = c.pos("Hips")[:, 2]
    tipz = c.T["RightIndexDistal"][:, 2]
    out, info = [], {}
    for f in (range(c.F) if c.loop else range(1, c.F - 1)):
        n_ = c.wrap(f + 1)
        if v[f] >= 0.15 and v[n_] <= 0.35 * v[f] and dd[f] <= 0.06:
            if knocked:
                info["hit"] = f
            else:
                out.append(ev(f, "fist_slam_stand", "RightHand", 1.0, 0.03,
                              f"hand stops dead on the stand ({v[f] * 100:.0f} -> {v[n_] * 100:.0f} cm/f)", v[f]))
                out.append(ev(c.wrap(f + 1), "stand_rattle", "RightHand", -8.0, 0.05, "stand shakes under the slam"))
                drop = (hz[c.prev(f)] - hz[f]) * 100
                if drop >= 2.5:
                    hp = c.pos("Hips")[f, :2]
                    foot = min(("Left", "Right"), key=lambda s: np.linalg.norm(c.pos(f"{s}Foot")[f, :2] - hp))
                    out.append(ev(f, "foot_stomp", f"{foot}Foot", -4.0, 0.04, f"weight slams down {drop:.1f} cm/f"))
    # fingertip taps on the score: a pointing finger's downward stroke that bottoms out on the desk
    slams = [e["frame"] for e in out if e["sfx"] == "fist_slam_stand"]
    for f in range(3, c.F - 1):
        if knocked:
            break
        drop = tipz[f - 3:f].max() - tipz[f]
        pointing = tipz[f] <= palm[f, 2] - 0.03
        if (tipz[f] <= tipz[f - 1] and tipz[f] < tipz[f + 1] and drop >= 0.03 and dd[f] <= 0.06 and pointing
                and all(abs(f - k) > 3 for k in slams)):
            out.append(ev(f, "watch_tap", "RightHand", -8.0, 0.05, f"fingertip taps the score (stroke {drop * 100:.0f} cm)"))
    return out, info


def watch_events(c):
    """Fingertip taps on the left wrist: a downward stroke of the right index tip that bottoms out
    close to the left forearm."""
    tip = c.T["RightIndexDistal"]
    a, b = c.pos("LeftLowerArm"), c.pos("LeftHand")
    out = []
    for f in range(3, c.F - 1):
        seg = b[f] - a[f]
        t = np.clip(np.dot(tip[f] - a[f], seg) / np.dot(seg, seg), 0, 1)
        d = np.linalg.norm(tip[f] - (a[f] + seg * t))
        z = tip[:, 2]
        drop = z[f - 3:f].max() - z[f]
        if z[f] <= z[f - 1] and z[f] < z[f + 1] and drop >= 0.03 and d <= 0.09 and b[f, 2] >= 1.2:
            out.append(ev(f, "watch_tap", "RightHand", -3.0, 0.04, f"fingertip taps the wrist ({d * 100:.0f} cm from the forearm)"))
    return out


def snap_events(c):
    """A finger snap: thumb and middle fingertips pressed together, then flying apart."""
    out = []
    for side in ("Right", "Left"):
        s = np.linalg.norm(c.T[f"{side}ThumbDistal"] - c.T[f"{side}MiddleDistal"], axis=1)
        for f in range(1, c.F):
            if s[f - 1] < 0.025 and s[f] - s[f - 1] > 0.03:
                out.append(ev(f, "finger_snap", f"{side}Hand", -2.0, 0.04, "thumb and middle finger snap apart"))
    return out


def strike_whooshes(c):
    """Swings that arrive fast and stop dead: speed peak >= 0.27 m/frame whose next frame drops below half."""
    out = []
    for side in ("Right", "Left"):
        v = c.speed(c.pos(f"{side}MiddleProximal"))
        for f in range(1, c.F):
            n_ = c.wrap(f + 1) if c.has(f + 1) else None
            nv = v[n_] if n_ is not None else 0.0
            if v[f] >= 0.27 and v[f] >= v[c.prev(f)] and v[f] >= nv and nv <= 0.5 * v[f]:
                big = v[f] >= 0.55
                out.append(ev(c.wrap(f - 1), "whoosh_big" if big else "whoosh_small", f"{side}Hand", -4.0 if big else -8.0,
                              0.06, f"strike swing {v[f] * FPS:.1f} m/s, stops dead", v[f]))
    return out


def prop_events(c, props, targets):
    """Pickups, releases, arrivals, landings and flight whooshes of the props of this move, plus
    cowbell stick hits."""
    out, info = [], {}
    DH = np.array(targets["DRUMMER_HEAD"])
    for bone, prop in PROP_OF.get(c.name, {}).items():
        M = c.M[bone]
        if prop == "cowbell_stick":
            continue
        if prop == "cowbell":
            stick, bell = props["cowbell_stick"], props["cowbell"]
            tip_local = stick["verts"][np.argmax(stick["verts"][:, 2])]
            Ms = c.M["PropR"]
            d = np.zeros(c.F)
            for f in range(c.F):
                tip = xform(Ms[f], tip_local)
                d[f] = bell["bvh"].find_nearest(Vector(xform(np.linalg.inv(M[f]), tip)))[3]
            hits = [f for f in range(2, c.F - 1) if d[f] < 0.015 and d[f] <= d[f - 1] and d[f] < d[f + 1]
                    and d[f - 2] - d[f] >= 0.02]
            info["hits"] = hits
            for f in hits:
                out.append(ev(f, "cowbell_hit", "PropL", -2.0, 0.0, f"stick tip {d[f] * 100:.1f} cm from the bell"))
            continue
        geo = props[prop]
        centre = np.array([xform(M[f], geo["centre"]) for f in range(c.F)])
        org = M[:, :3, 3]
        v = c.speed(centre)
        ang = np.zeros(c.F)
        for f in range(1, c.F):
            R = M[f - 1][:3, :3].T @ M[f][:3, :3]
            ang[f] = math.degrees(math.acos(max(-1.0, min(1.0, (np.trace(R) - 1) / 2))))
        rot0 = [math.degrees(math.acos(max(-1.0, min(1.0, (np.trace(M[0][:3, :3].T @ M[f][:3, :3]) - 1) / 2))))
                for f in range(c.F)]
        moved = [f for f in range(c.F) if np.linalg.norm(org[f] - org[0]) > 0.005 or rot0[f] > 1.0]
        if not moved:
            raise RuntimeError(f"{c.name}: {prop} on {bone} never moves")
        start = moved[0] - 1
        snd = PROP_SFX[prop]
        info["start"] = start
        if prop in THROWN:
            info["pickup"] = start
            sfx, g, j = snd["grab"]
            out.append(ev(start, sfx, bone, g, j, "prop leaves its rest pose next frame"))
            palm = c.pos("RightMiddleProximal")
            gd = np.array([min(np.linalg.norm(xform(M[f], gp) - palm[f]) for gp in geo["grips"]) for f in range(c.F)])
            rel = next((f - 1 for f in range(start + 1, c.F) if gd[f] > 0.10), None)
            if rel is None:
                raise RuntimeError(f"{c.name}: {prop} never leaves the hand")
            info["release"] = rel
            end = c.F - 1
            arrive = None
            for f in range(rel + 1, c.F):
                dh = np.linalg.norm(centre[f] - DH)
                if f + 1 < c.F and dh < 0.35 and dh <= np.linalg.norm(centre[f - 1] - DH) and dh <= np.linalg.norm(centre[f + 1] - DH):
                    arrive = f
                    break
                if f + 1 < c.F and v[f] >= 0.01 and v[f + 1] < 0.005:
                    arrive = f
                    break
            low = lowest(M, geo["verts"])
            land = next((f for f in range(rel + 1, c.F) if low[f] <= FLOOR_TOUCH), None)
            stop = next((x for x in (arrive, land) if x is not None), end)
            fl = slice(rel + 1, stop + 1)
            spin, fs = float(np.median(ang[fl])), float(np.median(v[fl]))
            kind = "whoosh_spin" if spin >= 40 else ("whoosh_big" if fs >= 0.25 else "whoosh_small")
            g = {"whoosh_spin": -2.0, "whoosh_big": -2.0, "whoosh_small": -6.0}[kind]
            out.append(ev(rel, kind, bone, g, 0.05, f"release, flies at {fs * FPS:.1f} m/s, spins {spin * FPS:.0f} deg/s",
                          10.0))
            if "release" in snd:
                sfx, g, j = snd["release"]
                out.append(ev(rel, sfx, bone, g, j, "pages leave the hand"))
            if arrive is not None and "arrive" in snd:
                info["arrive"] = arrive
                sfx, g, j = snd["arrive"]
                out.append(ev(arrive, sfx, bone, g, j, f"arrives {np.linalg.norm(centre[arrive] - DH):.2f} m from the "
                              "drummer's head" if np.linalg.norm(centre[arrive] - DH) < 0.35 else "flight stops"))
            if land is not None and "land" in snd:
                info["land"] = land
                sfx, g, j = snd["land"]
                out.append(ev(land, sfx, bone, g, j, f"lowest point reaches the floor ({low[land] * 100:.1f} cm)"))
        elif prop == "music_stand_knockable":
            low = lowest(M, geo["parts"]["stand_desk"])
            land = next((f for f in range(start + 1, c.F) if low[f] <= FLOOR_TOUCH), None)
            info["land"] = land
            if land is not None:
                sfx, g, j = snd["land"]
                out.append(ev(land, sfx, bone, g, j, f"desk edge reaches the floor ({low[land] * 100:.1f} cm)"))
    return out, info


FLOOR_TOUCH = 0.02        # a prop's lowest point this close to the floor touches it


def lowest(M, verts):
    """Per frame: the lowest point of a prop's vertices (m)."""
    return np.array([float((M[f][:3, :3] @ verts.T)[2].min() + M[f][2, 3]) for f in range(len(M))])


def body_events(c):
    """Cloth on big chest accelerations (sparingly) and a thud on heavy hip drops."""
    out = []
    ch = c.pos("UpperChest")
    acc = np.linalg.norm(np.roll(ch, -1, 0) - 2 * ch + np.roll(ch, 1, 0), axis=1) * 100
    if not c.loop:
        acc[0] = acc[-1] = 0.0
    peaks = [f for f in range(c.F) if acc[f] >= 5.0 and acc[f] >= acc[c.wrap(f - 1) if c.has(f - 1) else f]
             and acc[f] >= acc[c.wrap(f + 1) if c.has(f + 1) else f]]
    chosen = []
    for f in sorted(peaks, key=lambda f: -acc[f]):
        if len(chosen) < 2 and all(c.dist(f, k) >= GAP["cloth_rustle"] for k in chosen):
            chosen.append(f)
    for f in chosen:
        out.append(ev(f, "cloth_rustle", "UpperChest", -12.0, 0.08, f"chest acceleration {acc[f]:.1f} cm/f^2", acc[f]))
    hz = c.pos("Hips")[:, 2]
    vz = (hz - np.roll(hz, 1)) * 100
    if not c.loop:
        vz[0] = 0.0
    for run in runs(vz < -0.2, c.loop):
        total = -vz[run].sum()
        if total >= 10.0 and total / len(run) >= 1.5:
            bottom = run[-1]
            out.append(ev(bottom, "body_thud", "Hips", -4.0, 0.05, f"hips drop {total:.0f} cm and stop"))
    return out


def door_events(c, targets):
    door = np.array(targets["DOOR"])
    hxy = c.pos("Hips")[:, :2]
    if c.loop or np.linalg.norm(hxy[-1] - door[:2]) > 1.0 or np.linalg.norm(hxy[0] - door[:2]) < 1.0:
        return [], None
    v = c.speed(c.pos("Hips"))
    arrive = next(f for f in range(c.F) if all(v[k] < 0.002 for k in range(f + 1, c.F)))
    w = ",".join(f"{x:g}" for x in targets["DOOR"])
    return [ev(arrive, "door_slam", f"world:{w}", 0.0, 0.03, f"walks to {np.linalg.norm(hxy[-1] - door[:2]):.2f} m "
               "from the door and stops")], arrive


def terence_cues(clips, meta, props, targets):
    ground = min(float(cl.pos(f"{s}Foot")[:, 2].min()) for cl in clips.values() for s in ("Left", "Right"))
    stand_pose = clips["t4_sweep_stand"].M["PropR"][0]       # the stand in front of him, as the knock-over starts
    near_head = np.array(targets["NEAR_HEAD"])
    table, found = {}, {}
    for name, c in clips.items():
        events, plants = foot_events(c, ground)
        events += clap_events(c)
        if name.startswith(CLOSE_UP_PREFIX) or name in CLOSE_UP:
            events += slap_events(c, near_head)
        close_up = name.startswith(CLOSE_UP_PREFIX) or name in CLOSE_UP
        de, dinfo = ([], {}) if close_up else desk_events(c, stand_pose, props)   # close up, the stand is not in front of him
        events += de
        events += watch_events(c)
        events += snap_events(c)
        pe, pinfo = prop_events(c, props, targets)
        if "hit" in dinfo:
            sfx, g, j = PROP_SFX["music_stand_knockable"]["hit"]
            pe.append(ev(dinfo["hit"], sfx, "PropR", g, j, "the strike lands on the stand"))
            pinfo["hit"] = dinfo["hit"]
        events += pe
        releases = [e["frame"] for e in pe if e["sfx"].startswith("whoosh_")]
        events += [w for w in strike_whooshes(c) if all(c.dist(w["frame"], r) > 4 for r in releases)]
        for b in body_events(c):
            busy = sum(1 for e in events if c.dist(e["frame"], b["frame"]) <= 1)
            if b["sfx"] != "cloth_rustle" or busy < 2:
                events.append(b)
        de2, arrive = door_events(c, targets)
        events += de2
        if arrive is not None:
            pinfo["door_arrive"] = arrive
        table[name] = finish(events, c)
        found[name] = dict(plants=plants, props=pinfo)
    return table, found


# ---------------------------------------------------------------------------
# Band detectors
# ---------------------------------------------------------------------------
MOVE_EPS = 0.003          # m/frame: the instrument (or a hand) is moving
PAGE_CORNER = (-0.20, -0.50, 0.12)         # top corner of the right page above the desk (flip_keys in band_anims.py)
PAGE_DESK_Z = {True: 1.02, False: 1.20}    # desk height of the room's stands, seated / standing (the bassist's)
CREAK_CHEST = 0.002       # m/frame of chest speed that loads the chair


def main_run(v, eps, where):
    rs = runs(v >= eps, False)
    if not rs:
        raise RuntimeError(f"{where}: nothing moves faster than {eps} m/frame")
    r = max(rs, key=len)
    return r[0] - 1, r[-1]          # onset (last still frame), settle (last moving frame)


def band_member_cues(member, kind, clips, rest_hips_z):
    seated = rest_hips_z < 0.8
    has_inst = kind not in ("piano",)
    table = {}
    for name, c in clips.items():
        events = []
        chest_v = c.speed(c.pos("Chest"))
        inst_v = c.speed(c.pos("Instrument"))
        creak = "piano_bench_creak" if kind == "piano" else "chair_creak"
        if name in ("raise", "lower"):
            if kind in SAXES or kind in ("trumpet", "trombone"):
                on, settle = main_run(inst_v, MOVE_EPS, f"{member} {name}")
                touch = {"trumpet": "trumpet_valves", "trombone": "instrument_handle"}.get(kind, "sax_keys")
                if name == "raise":
                    events.append(ev(on, "instrument_handle", "Instrument", -12.0, 0.06, "instrument starts to lift"))
                    events.append(ev(settle, touch, "Instrument", -10.0, 0.06, "instrument settles at the mouth"))
                else:
                    events.append(ev(on, touch, "Instrument", -12.0, 0.06, "instrument comes off the lips"))
                    events.append(ev(settle, "instrument_handle", "Instrument", -10.0, 0.06, "instrument settles at rest"))
            elif kind in ("guitar", "bass"):
                hands = {s: c.speed(c.pos(f"{s}MiddleProximal")) for s in ("Left", "Right")}
                side = max(hands, key=lambda s: hands[s].sum())
                on, settle = main_run(hands[side], 0.005, f"{member} {name}")
                snd = f"{kind}_handle"
                if name == "raise":
                    events.append(ev(settle, snd, f"{side}Hand", -10.0, 0.06, "playing hand settles on the strings"))
                else:
                    events.append(ev(on, snd, f"{side}Hand", -10.0, 0.06, "playing hand leaves the strings"))
            elif kind == "piano":
                f = int(np.argmax(chest_v))
                events.append(ev(f, "piano_bench_creak", "Hips", -14.0, 0.06,
                                 f"weight shift {chest_v[f] * 1000:.1f} mm/f on the bench"))
        if name == "startle":
            f = int(np.argmax(chest_v))
            if seated:
                events.append(ev(f, creak, "Hips", -8.0, 0.06, f"jolt {chest_v[f] * 1000:.1f} mm/f loads the seat"))
            elif has_inst:
                events.append(ev(f, f"{kind}_handle" if kind in ("guitar", "bass") else "instrument_handle",
                                 "Instrument", -12.0, 0.06, "jolt knocks the instrument"))
        elif seated and name not in ("play_loop", "freeze", "rest", "arms_rest") and not (kind == "piano" and name in ("raise", "lower")):
            # big seated weight shifts
            pk = [f for f in range(1, c.F) if chest_v[f] >= CREAK_CHEST and chest_v[f] >= chest_v[f - 1]
                  and (f + 1 >= c.F or chest_v[f] >= chest_v[f + 1])]
            if pk:
                f = max(pk, key=lambda f: chest_v[f])
                events.append(ev(f, creak, "Hips", -14.0, 0.07, f"seated weight shift {chest_v[f] * 1000:.1f} mm/f"))
        if name == "rest" and seated:
            # one rare creak per loop, only for the players whose slow weight shift is large
            ch = c.pos("Chest")
            span = float(np.linalg.norm(ch.max(0) - ch.min(0)))
            if span >= REST_CREAK_SPAN:
                f = int(np.argmax(chest_v))
                events.append(ev(f, creak, "Hips", -16.0, 0.08, f"slow weight shift spans {span * 1000:.0f} mm"))
        if name == "flip_page":
            hands = {s: c.speed(c.pos(f"{s}MiddleProximal")) for s in ("Left", "Right")}
            side = max(hands, key=lambda s: hands[s].sum())
            corner = np.array(PAGE_CORNER) + np.array((0, 0, PAGE_DESK_Z[seated]))
            d = np.minimum(np.linalg.norm(c.pos(f"{side}Hand") - corner, axis=1),
                           np.linalg.norm(c.pos(f"{side}MiddleProximal") - corner, axis=1))     # wrist or palm
            pinch = int(np.argmin(d))
            if d[pinch] > 0.06:
                raise RuntimeError(f"{member} flip_page: the {side} hand comes no closer than {d[pinch]:.2f} m to the page")
            events.append(ev(pinch, "page_flip", f"{side}Hand", -10.0, 0.08,
                             f"{side.lower()} hand pinches the page corner ({d[pinch] * 100:.1f} cm) and turns it"))
        if name == "spit_valve":
            on, settle = main_run(inst_v, MOVE_EPS, f"{member} {name}")
            events.append(ev(on, "instrument_handle", "Instrument", -12.0, 0.06, "trombone tips slide-down"))
            iz = c.pos("Instrument")[:, 2]
            shakes = [f for f in range(2, c.F - 2) if iz[f] < iz[f - 1] and iz[f] <= iz[f + 1]
                      and max(iz[f - 2:f + 3]) - iz[f] >= 0.01]
            for k, f in enumerate(shakes):
                events.append(ev(f, "spit_valve", "Instrument", -6.0 - 4.0 * k, 0.06, f"shake {k + 1}"))
            last = max(range(c.F), key=lambda f: f if inst_v[f] >= MOVE_EPS else -1)
            events.append(ev(last, "instrument_handle", "Instrument", -12.0, 0.06, "trombone back at rest"))
        if "InstrumentSlide" in c.M:
            Mi, Ms = c.M["Instrument"], c.M["InstrumentSlide"]
            s = np.array([(np.linalg.inv(Mi[f]) @ Ms[f])[1, 3] for f in range(c.F)])
            sv = np.abs(s - np.roll(s, 1))
            if not c.loop:
                sv[0] = 0.0
            for run in runs(sv >= 0.01, c.loop):
                dist = float(sv[run].sum())
                if dist >= 0.05:
                    f = c.wrap(run[0] - 1)
                    events.append(ev(f, "trombone_slide", "InstrumentSlide", -14.0 + 6.0 * min(dist / 0.35, 1.0), 0.05,
                                     f"slide travels {dist * 100:.0f} cm", dist))
        table[name] = finish(events, c)
    return table


GENERIC_BAND = ("instrument_handle", "chair_creak", "page_flip")
ACTION_ORDER = BAND_ACTIONS + ("spit_valve",)
REST_CREAK_SPAN = 0.0      # set from the spread of the band's rest weight shifts in band_cues()


def band_cues(members, report):
    global REST_CREAK_SPAN
    spans = {m: float(np.linalg.norm(cl["rest"].pos("Chest").max(0) - cl["rest"].pos("Chest").min(0)))
             for m, (kind, cl, bones) in members.items()}
    # the rare rest creak goes to the third of the band with the largest slow weight shift
    REST_CREAK_SPAN = sorted(spans.values())[-max(1, len(spans) // 3)]
    report.append(f"rest weight-shift spans (mm): " + ", ".join(f"{m} {s * 1000:.1f}" for m, s in sorted(spans.items())))
    per_member = {}
    for m, (kind, clips, bones) in members.items():
        hz = float(clips["rest"].pos("Hips")[0, 2])
        per_member[m] = band_member_cues(m, kind, clips, hz)
    # compress: kind tables carry the most common cue list per action, members only their differences
    band = {"default": {}}
    key = lambda cues: json.dumps([{k: v for k, v in c.items() if k != "why"} for c in cues], sort_keys=True)
    # the default (a fallback for a kind without a table) keeps only the kind-neutral sounds
    for a in BAND_ACTIONS:
        lists = [key([c for c in per_member[m][a] if c["sfx"] in GENERIC_BAND]) for m in members]
        band["default"][a] = json.loads(Counter(lists).most_common(1)[0][0])
    for kind in BAND_KINDS:
        ms = [m for m in members if members[m][0] == kind]
        if not ms:
            raise RuntimeError(f"no band member of kind {kind}")
        band[kind] = {}
        for a in sorted(per_member[ms[0]], key=ACTION_ORDER.index):
            counts = Counter(key(per_member[m][a]) for m in ms)
            best = max(counts.values())
            pick = next(key(per_member[m][a]) for m in ms if counts[key(per_member[m][a])] == best)
            band[kind][a] = json.loads(pick)
        for m in ms:
            diff = {a: per_member[m][a] for a in band[kind] if key(per_member[m][a]) != key(band[kind][a])}
            if diff:
                band[m] = diff
    return band, per_member


# ---------------------------------------------------------------------------
# Finishing: gaps, variants, output form
# ---------------------------------------------------------------------------
def finish(events, c):
    for e in events:
        e["frame"] = c.wrap(e["frame"])
        if not 0 <= e["frame"] < c.F:
            raise RuntimeError(f"{c.name}: event {e['sfx']} at frame {e['frame']} outside 0..{c.F - 1}")
    # minimum gap per family and anchor: keep the stronger cue
    kept = []
    for e in sorted(events, key=lambda e: (-e["strength"], e["frame"])):
        fam = family(e["sfx"])
        gap = GAP.get(fam, GAP_DEFAULT)
        if any(family(k["sfx"]) == fam and k["anchor"] == e["anchor"] and c.dist(k["frame"], e["frame"]) < gap for k in kept):
            continue
        kept.append(e)
    kept.sort(key=lambda e: (e["frame"], e["sfx"]))
    # alternating variants in time order
    counters = Counter()
    for e in kept:
        fam = family(e["sfx"])
        if fam in VARIANTS and e["sfx"] == fam:
            names = VARIANTS[fam]
            e["sfx"] = names[counters[fam] % len(names)]
            counters[fam] += 1
    return [dict(frame=e["frame"], sfx=e["sfx"], anchor=e["anchor"], gain_db=round(e["gain_db"], 1) + 0.0,
                 pitch_jitter=round(e["pitch_jitter"], 3), why=e["why"]) for e in kept]


CUE_KEYS = ("frame", "sfx", "anchor", "gain_db", "pitch_jitter")


def strip(table):
    return {a: [{k: cue[k] for k in CUE_KEYS} for cue in cues] for a, cues in table.items()}


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------
def check_cues(where, cues, length, loop, bones):
    for cue in cues:
        if set(cue) != {"frame", "sfx", "anchor", "gain_db", "pitch_jitter"}:
            raise RuntimeError(f"{where}: bad cue keys {sorted(cue)}")
        if cue["sfx"] not in SFX_SET:
            raise RuntimeError(f"{where}: unknown sfx {cue['sfx']}")
        f = cue["frame"]
        if not isinstance(f, int) or f < 0 or (f >= length if loop else f > length):
            raise RuntimeError(f"{where}: frame {f} out of range for length {length} (loop {loop})")
        a = cue["anchor"]
        if a.startswith("world:"):
            xyz = a[len("world:"):].split(",")
            if len(xyz) != 3:
                raise RuntimeError(f"{where}: bad world anchor {a}")
            [float(x) for x in xyz]
        elif a not in ("PropR", "PropL") and a not in bones:
            raise RuntimeError(f"{where}: anchor {a} is not a bone")
        elif a in ("PropR", "PropL") and a not in bones:
            raise RuntimeError(f"{where}: anchor {a} but the rig has no {a} bone")
        if not GAIN_RANGE[0] <= cue["gain_db"] <= GAIN_RANGE[1]:
            raise RuntimeError(f"{where}: gain {cue['gain_db']} outside {GAIN_RANGE}")
        if not JITTER_RANGE[0] <= cue["pitch_jitter"] <= JITTER_RANGE[1]:
            raise RuntimeError(f"{where}: pitch_jitter {cue['pitch_jitter']} outside {JITTER_RANGE}")


def self_check(doc, meta, terence_bones, members):
    if doc["fps"] != FPS or doc["version"] != 1:
        raise RuntimeError("bad header")
    moves = meta["moves"]
    if set(doc["terence"]) != set(moves):
        raise RuntimeError(f"terence actions mismatch: {sorted(set(doc['terence']) ^ set(moves))}")
    for a, cues in doc["terence"].items():
        check_cues(f"terence/{a}", cues, moves[a]["frames"], moves[a]["loop"], terence_bones)
    band = doc["band"]
    for kind in BAND_KINDS:
        if kind not in band:
            raise RuntimeError(f"band table for {kind} missing")
    for m, (kind, clips, bones) in members.items():
        for a, c in clips.items():
            # resolution order: member, kind, default
            for table in (m, kind, "default"):
                if table in band and a in band[table]:
                    cues = band[table][a]
                    break
            else:
                raise RuntimeError(f"band {m}/{a} has no cue entry")
            if a not in band[kind]:
                raise RuntimeError(f"band kind {kind} lacks an entry for {a}")
            check_cues(f"band/{m}/{a}", cues, c.last, c.loop, bones)
    for table, acts in band.items():
        if table not in ("default",) + BAND_KINDS and table not in members:
            raise RuntimeError(f"band table {table} is neither default, a kind nor a member")
        users = [m for m, (k, cl, b) in members.items() if table in ("default", k, m)]
        for a, cues in acts.items():
            for m in users:
                if a in members[m][1]:
                    check_cues(f"band/{table}/{a} for {m}", cues, members[m][1][a].last, members[m][1][a].loop, members[m][2])
    for a in BAND_ACTIONS:
        if a not in band["default"]:
            raise RuntimeError(f"band default lacks {a}")


def spot_check(terence, found, meta, band_member, members, report):
    """Detections against the key timings in the moves json and ANIMATIONS.md."""
    moves = meta["moves"]
    fails = []

    def near(action, sfx_prefix, frames, tol, label):
        cues = [c for c in terence[action] if c["sfx"].startswith(sfx_prefix)]
        got = [c["frame"] for c in cues]
        for f in frames:
            d = min((abs(g - f) for g in got), default=None)
            ok = d is not None and d <= tol
            report.append(f"  {'ok ' if ok else 'BAD'} {action} {label} {f}: nearest {sfx_prefix}* at "
                          f"{min(got, key=lambda g: abs(g - f)) if got else 'none'}")
            if not ok:
                fails.append(f"{action} {label} {f}")
        if len(got) != len(frames):
            report.append(f"  BAD {action}: {len(got)} {sfx_prefix}* cues for {len(frames)} {label}")
            fails.append(f"{action} {label} count")

    def info_near(action, key, ref, tol, label):
        got = found[action]["props"].get(key)
        ok = got is not None and abs(got - ref) <= tol
        report.append(f"  {'ok ' if ok else 'BAD'} {action} {label} {ref}: detected {got}")
        if not ok:
            fails.append(f"{action} {label}")

    report.append("spot checks against terence_v004_moves.json:")
    near("t0_clap_off", "clap", moves["t0_clap_off"]["claps"], 1, "claps")
    near("t3_clap_in_face", "clap", moves["t3_clap_in_face"]["claps"], 1, "claps")
    near("t1_clap_stop", "clap", [moves["t1_clap_stop"]["clap"]], 1, "clap")
    near("t5_rhythm_slaps", "slap", moves["t5_rhythm_slaps"]["impacts"], 2, "impacts")
    near("t1_fist_slam", "fist_slam_stand", [moves["t1_fist_slam"]["slam"]], 1, "slam")
    near("t2_impatient_watch", "watch_tap", moves["t2_impatient_watch"]["taps"], 1, "taps")
    near("t2_score_point", "watch_tap", moves["t2_score_point"]["taps"], 1, "taps")
    near("t5_cowbell", "cowbell_hit", moves["t5_cowbell"]["hits"], 1, "hits")
    near("t4_charge", "foot_stomp", moves["t4_charge"]["stomps"], 1, "stomps")
    near("t4_storm_out", "door_slam", [moves["t4_storm_out"]["arrives"]], 1, "arrives")
    for a in ("t5_chair_throw", "t5_drum_throw", "t5_cymbal_throw"):
        info_near(a, "pickup", moves[a]["pickup"], 1, "pickup")
        info_near(a, "release", moves[a]["release"], 1, "release")
        info_near(a, "arrive", moves[a]["arrives"], 1, "arrives")
    info_near("t5_chair_throw", "land", moves["t5_chair_throw"]["lands"], 1, "lands")
    near("t5_chair_throw", "chair_impact", [moves["t5_chair_throw"]["arrives"], moves["t5_chair_throw"]["lands"]], 1,
         "arrives/lands")
    near("t5_drum_throw", "drum_impact", [moves["t5_drum_throw"]["arrives"]], 1, "arrives")
    near("t5_cymbal_throw", "cymbal_impact", [moves["t5_cymbal_throw"]["arrives"]], 1, "arrives")
    info_near("t4_throw_music", "pickup", moves["t4_throw_music"]["grab"], 1, "grab")
    info_near("t4_throw_music", "release", moves["t4_throw_music"]["release"], 1, "release")
    # the json's lands is where the pages' path ends; the lowest corner touches the floor a few frames
    # earlier and slides to rest, and the sound goes on the touchdown
    info_near("t4_throw_music", "land", moves["t4_throw_music"]["lands"], 3, "lands (touchdown, then slide)")
    info_near("t4_sweep_stand", "hit", moves["t4_sweep_stand"]["hit"], 1, "hit")
    info_near("t4_sweep_stand", "land", moves["t4_sweep_stand"]["lands"], 1, "lands")
    # walking: one plant per foot per stride cycle, alternating feet
    for a in ("walk", "t0_pace", "t3_circle_kit"):
        steps = [c for c in terence[a] if c["sfx"].startswith("foot_step")]
        sides = Counter(c["anchor"] for c in steps)
        order = [c["anchor"] for c in sorted(steps, key=lambda c: c["frame"])]
        alt = all(order[i] != order[i + 1] for i in range(len(order) - 1)) and (len(order) % 2 == 0)
        exp = 2 if a == "walk" else None
        ok = alt and sides["LeftFoot"] == sides["RightFoot"] and (exp is None or len(steps) == exp)
        report.append(f"  {'ok ' if ok else 'BAD'} {a}: {len(steps)} footsteps ({dict(sides)}), alternating {alt}")
        if not ok:
            fails.append(f"{a} footsteps")
    report.append("spot checks against ANIMATIONS.md (band):")
    for m, (kind, clips, bones) in members.items():
        t = band_member[m]
        if kind in SAXES or kind in ("trumpet", "trombone"):
            settle = max(c["frame"] for c in t["raise"] if c["anchor"] == "Instrument")
            ok = abs(settle - BAND_DOC["raise_meets_mouth"]) <= 1
            if not ok:
                fails.append(f"{m} raise settle {settle}")
            report.append(f"  {'ok ' if ok else 'BAD'} {m} raise: instrument settles at {settle} (meets the mouth at 13)")
        flips = [c["frame"] for c in t["flip_page"] if c["sfx"] == "page_flip"]
        ok = len(flips) == 1 and abs(flips[0] - BAND_DOC["flip_pinch"]) <= 1
        if not ok:
            fails.append(f"{m} flip_page {flips}")
        st = [c["frame"] for c in t["startle"]]
        ok2 = len(st) == 1 and st[0] <= BAND_DOC["startle_peak"]
        if not ok2:
            fails.append(f"{m} startle {st}")
        report.append(f"  {'ok ' if ok and ok2 else 'BAD'} {m}: page_flip at {flips} (pinch at 10), startle cue at {st} (jolt peaks at 4)")
        if kind == "trombone":
            shakes = [c["frame"] for c in t["spit_valve"] if c["sfx"] == "spit_valve"]
            ok = len(shakes) == 3 and all(abs(a - b) <= 1 for a, b in zip(shakes, BAND_DOC["spit_shakes"]))
            slides = [c["frame"] for c in t["play_loop"] if c["sfx"] == "trombone_slide"]
            ok_s = len(slides) == 4 and all(min(abs(f - b), 48 - abs(f - b)) <= 2 for f in slides for b in
                                            [min((0, 12, 24, 36, 48), key=lambda b: abs(f - b))])
            if not (ok and ok_s):
                fails.append(f"{m} spit {shakes} slides {slides}")
            report.append(f"  {'ok ' if ok and ok_s else 'BAD'} {m}: spit shakes at {shakes} (16/22/28), "
                          f"play_loop slide cues at {slides} (one per beat change)")
    if fails:
        raise RuntimeError(f"spot checks failed: {fails}")


# ---------------------------------------------------------------------------
# Review sheet
# ---------------------------------------------------------------------------
def fmt_cue(c):
    return f"{c['frame']} `{c['sfx']}` {c['anchor']} {c['gain_db']:+.0f} dB"


def write_md(doc, terence_why, band_why, meta, members, report_counts):
    L = ["# SFX cues", "",
         "Generated by `gen_sfx_cues.py` from the motion in the exported glbs (do not edit by hand). "
         "The contract is `sfx_cues.json`. Frames are authored frames at 24 fps; loop cues are below the loop length. "
         "Gains are dB, jitter is the random pitch spread. A `world:x,y,z` anchor is in the character's local frame, "
         "in the moves json convention (Blender axes, the character faces -Y, Z up; in Godot that is (x, z, -y)).", "",
         "## Counts", "", "| Character | Category | Cues |", "|---|---|---|"]
    for (who, cat), n in sorted(report_counts.items()):
        L.append(f"| {who} | {cat} | {n} |")
    L += ["", "## Terence", "", "| Action | Frames | Loop | Cues (frame, sound, anchor, gain) | Detected from |", "|---|---|---|---|---|"]
    for a in meta["moves"]:
        cues = terence_why[a]
        mv = meta["moves"][a]
        body = "<br>".join(fmt_cue(c) for c in cues) or "none"
        why = "<br>".join(c["why"] for c in cues) or ""
        L.append(f"| `{a}` | {mv['frames']} | {'yes' if mv['loop'] else 'no'} | {body} | {why} |")
    L += ["", "## Band", "",
          "Resolution order is member, then kind, then default; a more specific table replaces the generic one for that action. "
          "Kind tables are complete. Member tables hold only the actions where that player's motion differs "
          "(the trombones' slide patterns, the heavier rest weight shifts).", ""]
    for table, acts in doc["band"].items():
        L += [f"### {table}", "", "| Action | Cues |", "|---|---|"]
        for a, cues in acts.items():
            L.append(f"| `{a}` | {'<br>'.join(fmt_cue(c) for c in cues) or 'none'} |")
        L.append("")
    L += ["### Why (first member of each kind)", "", "| Member | Action | Detected from |", "|---|---|---|"]
    seen = set()
    for m, (kind, clips, bones) in members.items():
        if kind in seen:
            continue
        seen.add(kind)
        for a, cues in band_why[m].items():
            if cues:
                why = "<br>".join(f"{c['frame']} `{c['sfx']}`, {c['why']}" for c in cues)
                L.append(f"| {m} | `{a}` | {why} |")
    OUT_MD.write_text("\n".join(L) + "\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
CATEGORY = {
    "footsteps": ("foot_",), "hand contacts": ("clap", "slap", "fist_slam", "watch_tap", "finger_snap"),
    "whooshes": ("whoosh_",), "cloth / body": ("cloth_rustle", "body_thud"),
    "props / room": ("stand_", "paper_", "chair_grab", "chair_impact", "drum_", "cymbal_", "cowbell_", "door_slam"),
    "instrument handling": ("instrument_handle", "sax_keys", "trumpet_valves", "trombone_slide", "spit_valve",
                            "guitar_handle", "bass_handle"),
    "page flips": ("page_flip",), "creaks": ("chair_creak", "piano_bench_creak"),
}


def category(sfx):
    hits = [cat for cat, prefixes in CATEGORY.items() if sfx.startswith(prefixes)]
    if len(hits) != 1:
        raise RuntimeError(f"{sfx}: {len(hits)} categories")
    return hits[0]


def main():
    meta = json.loads(TERENCE_META.read_text())
    if meta["fps"] != FPS:
        raise RuntimeError(f"moves json fps {meta['fps']}")
    targets = meta["targets"]
    props = load_props()
    tclips, tbones = sample_glb(TERENCE_GLB, {a: m["loop"] for a, m in meta["moves"].items()})
    for a, c in tclips.items():
        if c.last != meta["moves"][a]["frames"]:
            raise RuntimeError(f"terence {a}: glb has {c.last} frames, json {meta['moves'][a]['frames']}")
        for bone in ("PropR", "PropL"):
            p = c.pos(bone)
            moving = float(np.linalg.norm(p - p[0], axis=1).max()) > 1e-4
            if moving != (bone in PROP_OF.get(a, {})) and not (a == "t5_cowbell" and bone == "PropL"):
                raise RuntimeError(f"terence {a}: {bone} moving={moving} disagrees with PROP_OF")
    report = []
    terence_why, found = terence_cues(tclips, meta, props, targets)

    members = {}
    for path in sorted((ROOT / "band").glob("band_*.glb")):
        m = path.stem
        kind = next((k for k in sorted(BAND_KINDS, key=len, reverse=True) if m.startswith(f"band_{k}_")), None)
        if kind is None:
            raise RuntimeError(f"{path.name}: unknown band kind")
        loops = {a: a in BAND_LOOPS for a in BAND_ACTIONS + (("spit_valve",) if kind == "trombone" else ())}
        clips, bones = sample_glb(path, loops)
        members[m] = (kind, clips, bones)
    if len(members) != 16:
        raise RuntimeError(f"expected 16 band members, found {len(members)}")
    band_why, band_member = band_cues(members, report)

    doc = {"fps": FPS, "version": 1, "terence": strip({a: terence_why[a] for a in meta["moves"]}),
           "band": {t: strip(acts) for t, acts in band_why.items()}}
    self_check(doc, meta, tbones, members)

    # per-action summary of what was detected
    print(f"{LOG} Terence, per action:")
    counts = Counter()
    for a in meta["moves"]:
        cues = terence_why[a]
        for c in cues:
            counts[("terence", category(c["sfx"]))] += 1
        print(f"{LOG}   {a:20s} " + (", ".join(f"{c['frame']} {c['sfx']}@{c['anchor']}" for c in cues) or "none"))
    print(f"{LOG} band, per member (resolved):")
    for m, (kind, clips, bones) in members.items():
        for a in clips:
            for c in band_member[m][a]:
                counts[("band (all 16 members, resolved)", category(c["sfx"]))] += 1
        print(f"{LOG}   {m}: " + "; ".join(f"{a} " + ",".join(f"{c['frame']} {c['sfx']}" for c in band_member[m][a])
                                         for a in clips if band_member[m][a]))
    spot_check(terence_why, found, meta, band_member, members, report)
    for line in report:
        print(f"{LOG} {line}")
    print(f"{LOG} counts: " + ", ".join(f"{w}/{c} {n}" for (w, c), n in sorted(counts.items())))

    OUT_JSON.parent.mkdir(exist_ok=True)
    OUT_JSON.write_text(json.dumps(doc, indent=1) + "\n")
    write_md(doc, terence_why, band_member, meta, members, counts)
    print(f"{LOG} wrote {OUT_JSON.relative_to(ROOT)} and {OUT_MD.relative_to(ROOT)}: "
          f"{sum(len(v) for v in doc['terence'].values())} Terence cues, {len(doc['band'])} band tables")


if __name__ == "__main__":
    main()
