"""Band animations (task B1 of OVERNIGHT.md, smoothed and de-clipped in v004). Used by build_band.py.

Each member gets, keyframed by script at 24 fps (120 BPM -> 12 frames per beat):
    rest, raise, play_loop, lower                      instrument cycle (play_loop: 4 beats, loops)
    freeze, startle, look_down, look_at_drummer,       reactions
    flip_page, arms_rest, spit_valve (trombones only)

Instruments are carried by the extra root bone "Instrument" (and "InstrumentSlide" for the
trombone's slide, moving only along the instrument's local -Y). In Godot, put the
instrument .glb under a BoneAttachment3D on "Instrument" (see ANIMATIONS.md for the offset)
and the slide under one on "InstrumentSlide". The hands are solved with two-bone IK onto the
instrument's grip_l / grip_r empties, read from instruments/<kind>_1.glb.

Motion: raise and lower are keyed every 2 frames along an eased path with an anticipation
and a settle, the instrument travelling on an arc that keeps it off the legs, and the arms
and torso staggered against it. Reactions ease in and settle. rest and arms_rest breathe
and shift their weight. build_band.py measures every action with band_clearance.py.
"""

import math

import bpy
from mathutils import Euler, Matrix, Quaternion, Vector

import lib_ps2 as ps2

FPS = 24
BEAT = 12
LOOP_BEATS = 4
PLAY_LEN = BEAT * LOOP_BEATS
RAISE_LEN = 16             # raise and lower (12 before v004)
REST_LEN = 96              # rest and arms_rest loops: one breath, 4 s (24 before v004)
KEY_STEP = 2               # key spacing for the shaped actions

INSTRUMENT_OF = {"alto_sax": "alto_sax", "tenor_sax": "tenor_sax", "baritone_sax": "baritone_sax",
                 "trumpet": "trumpet", "trombone": "trombone", "guitar": "guitar", "bass": "upright_bass",
                 "piano": None}

# Instrument placement. "mouth" frames are relative to the player's mouth, "root" to the
# character origin (feet / chair centre). Rotations in degrees, applied X, then Y, then Z.
PLAY = {
    "alto_sax": ("mouth", (0, 0, 0), (-20, 20, 0)),
    "tenor_sax": ("mouth", (0, 0, 0), (-21, 28, 0)),
    "baritone_sax": ("mouth", (0, 0, 0), (-17, 34, 0)),
    "trumpet": ("mouth", (0, 0, 0), (-8, 0, 0)),
    "trombone": ("mouth", (0, 0, 0), (-8, -30, -14)),
    "guitar": ("lap", (0.08, 0.15, 0.20), (0, -12, 0)),
    "upright_bass": ("root", (0.16, -0.46, 0.0), (-10, 0, 0)),
}
# At rest: (frame, loc, rot). Frames: "root" = character origin (the guitar and bass move with
# the hips, see root_shift), "chest" = carried by the UpperChest (a sax on its neck strap),
# authored in the standing T-pose frame, "lap" = from the top of the right thigh under the
# right lap hand. "arms_rest" is where the instrument sits while both hands are on the lap.
REST = {
    "alto_sax": ("chest", (0.0, -0.15, 1.50), (-14, 24, 0)),
    "tenor_sax": ("chest", (0.0, -0.15, 1.46), (-10, 28, 0)),
    "baritone_sax": ("chest", (0.0, -0.15, 1.50), (-6, 34, 0)),
    "trumpet": ("lap", (0.0, 0.28, 0.097), (3, 0, 0)),
    "trombone": ("root", (0.40, 0.01, 0.83), (90, 0, -30)),
    "guitar": PLAY["guitar"],
    "upright_bass": PLAY["upright_bass"],
}
ARMS_REST = dict(REST)
ARMS_REST.update({   # hands off, the sax hangs a little further out on its strap, clear of the forearms
    "alto_sax": ("chest", (-0.02, -0.20, 1.54), (-14, 24, 0)),
    "tenor_sax": ("chest", (-0.02, -0.20, 1.50), (-10, 28, 0)),
    "baritone_sax": ("chest", (-0.02, -0.20, 1.54), (-6, 34, 0)),
})
ARMS_REST_HOLD = {"trumpet": {"Left": None, "Right": "grip_r"}, "guitar": {"Left": None, "Right": "rest_r"}}   # hands that stay on the instrument
ROOT_SHIFT = {}            # extra offset (x s) for "root" instruments, rest and play alike
# Path of the instrument during raise / lower: extra offset (character frame, metres) at the
# middle of the move, so it swings forward and up past the knees instead of through them.
ARC = {
    "alto_sax": (0, -0.10, 0.06), "tenor_sax": (0, -0.12, 0.06), "baritone_sax": (0, -0.10, 0.05),
    "trumpet": (0, -0.12, 0.08), "trombone": (0.10, -0.15, 0.10), "guitar": (0, -0.04, 0.03),
    "upright_bass": (0, 0, 0),
}
# Fraction of the raise travel by which the instrument has finished turning (1 = with the travel).
ROT_LEAD = {}
ROT_PIVOT = {"trombone": "grip_l"}   # turns about the hand that keeps hold of it
# Control point (instrument frame) a travelling hand curves through instead of the default lift
MIX_VIA = {("upright_bass", "Right"): (-0.5, -0.15, 0.95)}
# Frames (raise, then lower) over which a hand that changes hold travels between the lap and the instrument
HAND_TIMING = {"Left": (3, RAISE_LEN - 3), "Right": (1, RAISE_LEN - 5), ("trombone", "Right"): (7, RAISE_LEN - 1)}
HAND_TIMING_LOWER = {"Left": (2, RAISE_LEN - 5), "Right": (4, RAISE_LEN - 3), ("trombone", "Right"): (0, 7)}
# Which grip each hand holds: None = resting on the lap (or hanging, standing)
HANDS = {
    "alto_sax": ({"Left": "grip_l", "Right": "grip_r"}, {"Left": "grip_l", "Right": "grip_r"}),
    "tenor_sax": ({"Left": "grip_l", "Right": "grip_r"}, {"Left": "grip_l", "Right": "grip_r"}),
    "baritone_sax": ({"Left": "grip_l", "Right": "grip_r"}, {"Left": "grip_l", "Right": "grip_r"}),
    "trumpet": ({"Left": None, "Right": "grip_r"}, {"Left": "grip_l", "Right": "grip_r"}),
    "trombone": ({"Left": "grip_l", "Right": None}, {"Left": "grip_l", "Right": "grip_r"}),
    "guitar": ({"Left": "grip_l", "Right": "rest_r"}, {"Left": "grip_l", "Right": "strum"}),
    "upright_bass": ({"Left": "grip_l", "Right": None}, {"Left": "grip_l", "Right": "pluck"}),
}
# Holds that place the wrist itself (instrument frame): (wrist, hand direction). The guitar's
# strumming hand comes over the top edge of the lower bout, the bassist's plucking hand round
# the right edge of the body.
WRIST_HOLDS = {
    ("guitar", "rest_r"): ((-0.13, -0.075, 0.23), (0.35, -0.15, -1.0)),
    ("guitar", "strum"): ((-0.11, -0.08, 0.23), (0.45, -0.25, -1.0)),
    ("upright_bass", "pluck"): ((-0.24, -0.17, 1.00), (1.0, -0.6, -0.2)),
}
# Where the palm sits relative to a grip, in the instrument's frame (metres), so the hand
# wraps the tube instead of sinking into it. Missing entries mean no offset.
PALM_OFFSET = {
    **{(sax, "grip_l"): (0, -0.012, 0) for sax in ("alto_sax", "tenor_sax", "baritone_sax")},
    **{(sax, "grip_r"): (0, 0.015, 0) for sax in ("alto_sax", "tenor_sax", "baritone_sax")},
    ("guitar", "grip_l"): (0, 0.025, -0.015),
}
WRIST_BACK = 0.085         # wrist distance behind a grip, so the palm stops at the tube and the fingers wrap it
# Elbow pole per instrument and hand (character frame direction); default is out, back and down.
POLES = {
    **{(sax, "Left", "grip_l"): (1.0, -1.0, -0.5) for sax in ("alto_sax", "tenor_sax", "baritone_sax")},
    **{(sax, "Right", "grip_r"): (-1.0, 0.0, -0.6) for sax in ("alto_sax", "tenor_sax", "baritone_sax")},
    ("guitar", "Right", "strum"): (-1.0, 0.6, 0.0), ("guitar", "Right", "rest_r"): (-1.0, 0.6, 0.0),
}
# share of look_at_drummer's turn taken by the torso (the sax hangs off the chest, keep it small)
TWIST = {"alto_sax": 0.2, "tenor_sax": 0.2, "baritone_sax": 0.2}
# the hand that turns the page (the right one unless it holds the instrument at rest)
FLIP_HAND = {"trumpet": "Left"}
FLIP_LEFT_X = {"baritone_sax": -0.06}   # where the page is let go (x), per instrument
FLIP_FRONT = (-0.15, -0.60, 0.0)   # on the way back the hand passes here, in front of the desk
# where that hand passes on its way to the page, clear of the instrument (character frame, x s)
FLIP_AROUND = {"Right": (-0.32, -0.14, -0.08), "Left": (0.30, -0.30, -0.02), ("guitar", "Right"): (-0.18, -0.32, 0.10)}
SEATING = {  # room seat marker -> (band member, instrument glb or None)
    "seat_sax_1": ("band_tenor_sax_2", "tenor_sax_2"), "seat_sax_2": ("band_alto_sax_2", "alto_sax_2"),
    "seat_sax_3": ("band_alto_sax_1", "alto_sax_1"), "seat_sax_4": ("band_tenor_sax_1", "tenor_sax_1"),
    "seat_sax_5": ("band_baritone_sax_1", "baritone_sax_1"),
    **{f"seat_trombone_{i}": (f"band_trombone_{i}", f"trombone_{i}") for i in range(1, 5)},
    **{f"seat_trumpet_{i}": (f"band_trumpet_{i}", f"trumpet_{i}") for i in range(1, 5)},
    "seat_piano_1": ("band_piano_1", None), "seat_guitar_1": ("band_guitar_1", "guitar_1"),
    "seat_bass_1": ("band_bass_1", "upright_bass_1"),
}
SPIT = ((0.30, -0.38, 1.10), (58, 0, -12))  # trombone held slide-down for spit_valve (root frame)
SLIDE_PATTERN = [0.0, 0.22, 0.08, 0.35]   # metres per beat in play_loop
BASS_STEPS = [0, 1, 2, 1]                  # left hand position on the neck per beat (v003's 0,1,2 broke the loop)
PIANO_KEYS = (0.0, -0.40, 0.865)           # wrist line over the keys, character frame
SEAT = {   # per kind: seat top z, hip joint y from the seat centre (+ = back), shin lean forward (deg),
           # heel lift (m), pelvis roll back onto the seat (deg, the spine compensates)
    "default": dict(top=ps2.SEAT_TOP, back=-0.05, shin=3.0, heel=0.04, pelvis=-20.0),   # front half of the chair
    "piano": dict(top=0.51, back=-0.05, shin=3.0, heel=0.04, pelvis=-20.0),   # the piano bench is 0.51 m, 0.34 m deep
}
LAP_Y = {"default": -0.30, "piano": -0.20}   # lap hands, forward of the hip joint (x s)
OLD_HIP_Y = 0.06           # hip y the v003 "root" placements (guitar, bass) were authored against
SEAT_PRESS = 0.004         # how far the seat cushion is allowed to give under the player


def rot_matrix(rot):
    return Euler([math.radians(a) for a in rot], "XYZ").to_matrix().to_4x4()


def read_grips(path):
    """Import an instrument glb, return {grip name: local position} (slide grips at closed)."""
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=str(path))
    new = [o for o in bpy.data.objects if o not in before]
    roots = [o for o in new if o.parent is None and o.type == "MESH"]
    if len(roots) != 1:
        raise RuntimeError(f"{path}: expected one mesh root, got {[o.name for o in roots]}")
    inv = roots[0].matrix_world.inverted()
    grips = {o.name.split(".")[0]: (inv @ o.matrix_world).translation.copy() for o in new if o.type == "EMPTY" and o.name.startswith("grip_")}
    for o in new:
        bpy.data.objects.remove(o, do_unlink=True)
    if set(grips) != {"grip_l", "grip_r"}:
        raise RuntimeError(f"{path}: grips {sorted(grips)}")
    return grips


# --- easing ---------------------------------------------------------------------
def clamp01(x):
    return max(0.0, min(1.0, x))


def smooth(x):
    """Smoothstep 0..1 (ease in and out)."""
    x = clamp01(x)
    return x * x * (3 - 2 * x)


def smoother(x):
    x = clamp01(x)
    return x * x * x * (x * (6 * x - 15) + 10)


def window(f, f0, f1, ease=smooth):
    """Eased 0..1 between frames f0 and f1."""
    return ease((f - f0) / (f1 - f0))


def settle(f, f0, f1, over=0.08):
    """0..1 between f0 and f1 that overshoots by `over` around 70% and settles to exactly 1."""
    x = clamp01((f - f0) / (f1 - f0))
    return smoother(x) + over * math.sin(math.pi * smooth(x)) * smooth(x)


def pulse(f, f0, peak, f1):
    """0 -> 1 at peak -> 0, eased both ways."""
    if f <= peak:
        return window(f, f0, peak)
    return 1.0 - window(f, peak, f1)


def scale_ops(ops, w):
    """Scale the angle of turn / look ops by w (they are linear in degrees)."""
    out = []
    for op in ops:
        if op[0] == "turn":
            out.append(("turn", op[1], op[2], op[3] * w))
        elif op[0] == "look":
            vals = list(op[1:])
            for i in range(min(3, len(vals))):
                vals[i] = vals[i] * w
            out.append(("look", *vals))
        else:
            raise ValueError(f"cannot scale op {op}")
    return out


def blend_matrix(A, B, t, t_rot=None):
    """Location lerp (t), rotation slerp (t_rot, default t) between two rigid matrices."""
    la, ra = A.translation, A.to_quaternion()
    lb, rb = B.translation, B.to_quaternion()
    q = ra.slerp(rb, t if t_rot is None else t_rot)
    return Matrix.Translation(la.lerp(lb, t)) @ q.to_matrix().to_4x4()


class Player:
    """Builds the animation set for one band member."""

    def __init__(self, rig, kind, grips, drummer_local, name=""):
        self.rig = rig
        # per-player feel so a section doesn't move in lockstep (deterministic from the name)
        h = sum((i + 1) * ord(c) for i, c in enumerate(name)) or 1
        self.phase = (h % 4) * 0.125          # beats; loop stays seamless (all motion is 1, 2 or 4 beat periodic)
        self.bob = 1.5 + (h % 5) * 0.5        # degrees of forward bob per beat
        self.sway = (h % 3) * 1.6             # degrees of side sway over two beats
        self.lift = 2.0 + (h % 4) * 1.0       # degrees the bell lifts on 2 and 4
        self.slide = SLIDE_PATTERN[h % 4:] + SLIDE_PATTERN[:h % 4]
        self.breath_phase = (h % 7) / 7.0     # where in the breath each player's rest loop starts
        self.weight = 0.6 + (h % 3) * 0.3     # degrees of slow weight shift at rest
        self.kind = kind
        self.inst = INSTRUMENT_OF[kind]
        self.grips = grips.get(self.inst) if self.inst else None
        self.standing = kind == "bass"        # the bassist stands (as in the film)
        self.wind = self.inst is not None and PLAY[self.inst][0] == "mouth"
        self.drummer_local = drummer_local
        self.s = rig.data.bones["Head"].tail_local.z / ps2.HEAD_TOP
        seat = SEAT.get(kind, SEAT["default"])
        self.seat_top, self.seat_back, self.shin_lean, self.heel = seat["top"], seat["back"], seat["shin"], seat["heel"]
        self.hip_z = self.seat_top + 0.10 * self.s     # refined by fit_seat()
        self.pelvis = seat["pelvis"]
        self.lap_z = {"Left": None, "Right": None}     # thigh top under each lap hand, set by fit_seat()
        self.fitted = False

    # --- seat ---------------------------------------------------------------
    def fit_seat(self, clearance):
        """Set the hip height so the seat gives by SEAT_PRESS, and read the thigh tops for the lap hands.
        The standing bassist has no seat."""
        if self.standing:
            self.fitted = True
            return
        def gap_at(z):
            self.hip_z = z
            ps2.apply_pose(self.rig, self.base())
            return clearance.seat_gap() + SEAT_PRESS
        legs = self.leg_lengths()
        reach = legs[2] + legs[0] * 0.98 + legs[1] * math.cos(math.radians(self.shin_lean))
        lo, hi = self.seat_top, min(self.seat_top + 0.25 * self.s, reach)
        if not gap_at(lo) < 0 < gap_at(hi):
            raise RuntimeError(f"{self.rig.name}: seat fit has no solution between {lo:.3f} and {hi:.3f}")
        for _ in range(20):    # bisection, the gap grows with the hip height
            mid = 0.5 * (lo + hi)
            if gap_at(mid) < 0:
                lo = mid
            else:
                hi = mid
        gap_at(hi)
        for sd in ("Left", "Right"):
            self.lap_z[sd] = clearance.thigh_top(sd, self.lap_xy(sd))
        self.fitted = True
        ps2.reset_pose(self.rig)

    def leg_lengths(self):
        """(thigh, shin, seated ankle height)."""
        b = self.rig.data.bones
        return ((b["LeftUpperLeg"].head_local - b["LeftUpperLeg"].tail_local).length,
                (b["LeftLowerLeg"].head_local - b["LeftLowerLeg"].tail_local).length,
                b["LeftFoot"].head_local.z + self.heel * self.s)

    def seat_ops(self):
        rig, s = self.rig, self.s
        hips = rig.data.bones["Hips"].head_local
        # the pelvis rolls back onto the seat (lowers the seat of the trousers), the spine stays upright
        ops = [("hips", (0, self.seat_back, self.hip_z - hips.z)), ("turn", "Hips", ps2.X, self.pelvis),
               ("turn", "Spine", ps2.X, -4 - self.pelvis)]
        thigh = (rig.data.bones["LeftUpperLeg"].head_local - rig.data.bones["LeftUpperLeg"].tail_local).length
        shin = (rig.data.bones["LeftLowerLeg"].head_local - rig.data.bones["LeftLowerLeg"].tail_local).length
        foot = rig.data.bones["LeftFoot"]
        ankle_z = foot.head_local.z + self.heel * s
        flen = foot.length
        drop_toe = ankle_z - foot.tail_local.z        # toes stay on the floor while the heel lifts
        foot_dir = Vector((0, -math.sqrt(flen * flen - drop_toe * drop_toe), -drop_toe)).normalized()

        def legs(rig):
            for sd, sx in (("Left", 1), ("Right", -1)):
                H = rig.pose.bones[f"{sd}UpperLeg"].head
                drop = H.z - ankle_z
                b = math.radians(self.shin_lean)
                sa = (drop - shin * math.cos(b)) / thigh
                if not -1.0 < sa < 1.0:
                    raise RuntimeError(f"{rig.name}: legs cannot reach the floor from hip height {H.z:.3f}")
                fwd = thigh * math.sqrt(1 - sa * sa) + shin * math.sin(b)
                ankle = Vector((sx * 0.115 * s, H.y - fwd, ankle_z))
                ps2.ik_leg(rig, sd, ankle, pole=Vector((sx * 0.08, -1, 0.5)))
                ps2.aim(rig, f"{sd}Foot", (sx * 0.06, foot_dir.y, foot_dir.z))
        return ops + [("fn", legs)]

    # --- building blocks ---------------------------------------------------
    def base(self, breath=None, breath_w=1.0):
        """Seated (or standing) pose plus, when breath is a frame number, the breathing and weight
        shift (scaled by breath_w)."""
        ops = [("turn", "Spine", ps2.X, -3)] if self.standing else self.seat_ops()
        if breath is not None:
            ops += scale_ops(self.breath_ops(breath), breath_w)
        return ops

    def breath(self, f):
        """0..1 inhale at frame f of the rest cycle."""
        return 0.5 - 0.5 * math.cos(2 * math.pi * (f / REST_LEN + self.breath_phase))

    def breath_ops(self, f):
        b = self.breath(f)
        ph = 2 * math.pi * (f / REST_LEN + self.breath_phase)
        shift = self.weight * math.sin(ph + 1.3) + 0.3 * self.weight * math.sin(2 * ph)
        return [("turn", "Spine", ps2.Y, shift), ("turn", "UpperChest", ps2.X, -1.6 * b),
                ("turn", "LeftShoulder", ps2.Y, -2.0 * b), ("turn", "RightShoulder", ps2.Y, 2.0 * b),
                ("turn", "Neck", ps2.X, 1.2 * b)]

    def mouth(self):
        pb = self.rig.pose.bones["Head"]
        rest_mouth = Vector((0, -0.112 * self.s, pb.bone.head_local.z + 0.036 * self.s))
        return pb.matrix @ pb.bone.matrix_local.inverted() @ rest_mouth

    def placement(self, frame, loc, rot, breath=0.0):
        """Instrument matrix for a (frame, loc, rot) entry of PLAY / REST / ARMS_REST."""
        M = Matrix.Translation(Vector(loc) * self.s) @ rot_matrix(rot)
        if frame == "mouth":
            return Matrix.Translation(self.mouth()) @ M
        if frame == "chest":
            pb = self.rig.pose.bones["UpperChest"]
            return pb.matrix @ pb.bone.matrix_local.inverted() @ M
        if frame == "lap":
            x, y = self.lap_xy("Right")
            return Matrix.Translation(Vector((x, y, self.lap_z["Right"] + 0.003 * breath))) @ M
        if frame == "root":
            return Matrix.Translation(self.root_shift() + Vector((0, 0, 0.004 * breath))) @ M
        raise ValueError(f"unknown instrument frame {frame}")

    def rest_matrix(self, table=None, breath=0.0):
        return self.placement(*(table or REST)[self.inst], breath=breath)

    def play_matrix(self, wobble=(0, 0, 0), lift=0.0):
        frame, loc, rot = PLAY[self.inst]
        rot = (rot[0] + wobble[0], rot[1] + wobble[1], rot[2] + wobble[2])
        return Matrix.Translation((0, 0, lift)) @ self.placement(frame, loc, rot)

    def root_shift(self):
        """Carry root-framed instruments with the hips (they were placed for a hip y of OLD_HIP_Y)."""
        hip_y = OLD_HIP_Y if self.standing else self.seat_back
        return Vector((0, hip_y - OLD_HIP_Y, 0)) + Vector(ROOT_SHIFT.get(self.inst, (0, 0, 0))) * self.s

    def inst_matrix(self, mode, wobble=(0, 0, 0), lift=0.0):
        return self.rest_matrix() if mode == "rest" else self.play_matrix(wobble, lift)

    def lap_xy(self, sd):
        """Where a lap hand rests, along the thigh: (x, y) in the character frame, fixed by the seat."""
        sx = 1 if sd == "Left" else -1
        return (sx * 0.13 * self.s, self.seat_back + LAP_Y.get(self.kind, LAP_Y["default"]) * self.s)

    def lap(self, sd):
        x, y = self.lap_xy(sd)
        if self.standing:   # hanging at the side
            sx = 1 if sd == "Left" else -1
            return self.rig.pose.bones["Hips"].head + Vector((sx * 0.28, -0.04, -0.20)) * self.s
        if self.lap_z[sd] is None:
            raise RuntimeError(f"{self.rig.name}: lap hands need fit_seat() first")
        return Vector((x, y, self.lap_z[sd] + 0.045 * self.s))

    def lap_dir(self, sd):
        sx = 1 if sd == "Left" else -1
        if self.standing:
            return Vector((sx * 0.05, -0.1, -1))
        return Vector((-sx * 0.25, -1, -0.45))

    def pole(self, sd, grip):
        sx = 1 if sd == "Left" else -1
        p = POLES.get((self.inst, sd, grip))
        if p:
            return Vector(p)
        if grip is None and self.standing:
            return Vector((sx * 1.0, 0.1, -0.3))
        return Vector((sx * 0.8, 0.35, -1.0))

    def reach(self, sd, grip_pos, pole=None, curl=0.55, hand_dir=None, wrist_back=0.06):
        """IK the wrist to just behind grip_pos, point the hand through it, curl the fingers."""
        sx = 1 if sd == "Left" else -1
        shoulder = self.rig.pose.bones[f"{sd}UpperArm"].head
        to_shoulder = (shoulder - grip_pos).normalized()
        wrist = grip_pos + to_shoulder * wrist_back * self.s
        ps2.ik_arm(self.rig, sd, wrist, pole or Vector((sx * 0.8, 0.35, -1.0)))
        pb = self.rig.pose.bones[f"{sd}Hand"]
        ps2.aim(self.rig, f"{sd}Hand", hand_dir or (grip_pos - pb.head))
        ps2.curl_fingers(self.rig, sd, curl)

    def hold(self, sd, g, M, slide=0.0):
        """(target, hand direction or None, wrist distance behind the target) for hand sd holding g
        of the instrument at M. g None = the lap; a WRIST_HOLDS name places the wrist itself."""
        if g is None:
            return self.lap(sd), self.lap_dir(sd), 0.0
        wh = WRIST_HOLDS.get((self.inst, g))
        if wh is not None:
            return M @ Vector(wh[0]), (M.to_3x3() @ Vector(wh[1])).normalized(), 0.0
        local = self.grips[g].copy()
        if self.inst == "trombone" and g == "grip_r":
            local.y -= slide
        local += Vector(PALM_OFFSET.get((self.inst, g), (0, 0, 0)))
        return M @ local, None, WRIST_BACK

    def hands(self, assign, M, slide=0.0, offsets=None, mix=None, relaxed=False):
        """Solve both arms. mix: {side: (other hold, w)} blends a hand toward another hold
        (w = 1 is fully there), used while a hand travels between the lap and the instrument."""
        offsets = offsets or {}
        mix = mix or {}
        for sd in ("Left", "Right"):
            g = assign[sd]
            shoulder = self.rig.pose.bones[f"{sd}UpperArm"].head
            pos, hdir, back = self.hold(sd, g, M, slide)
            hdir = hdir if hdir is not None else (pos - shoulder).normalized()
            pole = self.pole(sd, g)
            on_grip = 0.0 if g is None else 1.0
            if sd in mix:
                g2, w = mix[sd]
                via = MIX_VIA.get((self.inst, sd))
                pos2, hdir2, back2 = self.hold(sd, g2, M, slide)
                hdir2 = hdir2 if hdir2 is not None else (pos2 - shoulder).normalized()
                if via is not None:     # round the instrument's edge
                    c = M @ Vector(via)
                    pos = pos.lerp(c, w).lerp(c.lerp(pos2, w), w)
                else:
                    pos = pos.lerp(pos2, w) + Vector((0, -0.03, 0.12)) * self.s * math.sin(math.pi * w)   # arcs up and over
                hdir = hdir.lerp(hdir2, w)
                if via is not None:     # fingers lead round the edge
                    hdir = (hdir + (M.to_3x3() @ Vector((-0.4, -1.0, 0.0))).normalized() * 1.5 * math.sin(math.pi * w)).normalized()
                back = back + (back2 - back) * w
                pole = pole.lerp(self.pole(sd, g2), w)
                on_grip = on_grip * (1 - w) + (0.0 if g2 is None else 1.0) * w
            pos = pos + offsets.get(sd, Vector())
            curl = 0.3 + (0.05 if relaxed else 0.25) * on_grip
            self.reach(sd, pos, pole=pole, curl=curl, hand_dir=hdir, wrist_back=back)

    def set_instrument(self, M, slide=0.0):
        ps2.set_bone_matrix(self.rig, "Instrument", M)
        if "InstrumentSlide" in self.rig.pose.bones:
            ps2.set_bone_matrix(self.rig, "InstrumentSlide", M @ Matrix.Translation((0, -slide, 0)))

    # --- poses ---------------------------------------------------------------
    def piano_rest_hands(self, offsets=None):
        for sd in ("Left", "Right"):
            self.reach(sd, self.lap(sd) + (offsets or {}).get(sd, Vector()), pole=self.pole(sd, None),
                       curl=0.3, hand_dir=self.lap_dir(sd), wrist_back=0.0)

    def pose_rest(self, extra=(), hand_offsets=None, both_lap=False, breath=0.0, fn_after=None):
        """Rest pose at frame `breath` of the breathing cycle (None = no breathing)."""
        b = self.breath(breath) if breath is not None else 0.0

        def fn(rig):
            if self.kind == "piano":
                self.piano_rest_hands(hand_offsets)
            else:
                M = self.rest_matrix(ARMS_REST if both_lap else REST, b)
                self.set_instrument(M)
                if both_lap:
                    assign = ARMS_REST_HOLD.get(self.inst, {"Left": None, "Right": None})
                else:
                    assign = HANDS[self.inst][0]
                self.hands(assign, M, offsets=hand_offsets, relaxed=both_lap)
            if fn_after:
                fn_after(rig)
        return self.base(breath) + list(extra) + [("fn", fn)]

    def play_torso(self, beat, w=1.0):
        ph = beat + self.phase
        bob = self.bob * math.sin(2 * math.pi * ph)
        sway = self.sway * math.sin(math.pi * ph)
        return [("turn", "Spine", ps2.X, -bob * w), ("turn", "Chest", ps2.Y, sway * w)]

    def play_parts(self, beat):
        """(instrument matrix, slide, hand offsets) for the playing pose at `beat` (current torso)."""
        ph = beat + self.phase
        wob = (self.lift * math.sin(math.pi * ph), 0, 0) if PLAY[self.inst][0] == "mouth" else (0, 0, 0)
        slide = 0.0
        if self.inst == "trombone":
            slide = self.slide[int(beat + 1e-6) % LOOP_BEATS]
        M = self.play_matrix(wobble=wob)
        offsets = {}
        if self.inst == "guitar":
            offsets["Right"] = Vector((0, 0, 0.04 * math.cos(math.pi * beat * 2)))
        if self.inst == "upright_bass":
            offsets["Right"] = Vector((0.03 * math.cos(math.pi * beat * 2), -0.01, 0))
            step = BASS_STEPS[int(beat + 1e-6) % LOOP_BEATS]      # up and down the neck, 4-beat periodic
            offsets["Left"] = M.to_3x3() @ Vector((0, 0, 0.035 * step))
        return M, slide, offsets

    def piano_play_hands(self, beat, w=1.0):
        for sd, sx in (("Left", 1), ("Right", -1)):
            shift = 0.06 * math.sin(math.pi * beat / 2 + (0 if sx > 0 else 1.3))
            bounce = 0.02 * abs(math.sin(math.pi * beat))
            keys = Vector((sx * 0.20 + shift, PIANO_KEYS[1], PIANO_KEYS[2] + bounce)) * self.s
            target = self.lap(sd).lerp(keys, w) + Vector((0, 0.03, 0.13)) * self.s * math.sin(math.pi * w)
            pole = self.pole(sd, None).lerp(Vector((sx * 1.0, 0.4, -0.5)), w)
            hdir = self.lap_dir(sd).lerp(Vector((0, -1, -0.5)), w)
            self.reach(sd, target, pole=pole, curl=0.3 + 0.05 * w, hand_dir=hdir, wrist_back=0.0)

    def pose_play(self, beat=0.0, extra=()):
        def fn(rig):
            if self.kind == "piano":
                self.piano_play_hands(beat)
                return
            M, slide, offsets = self.play_parts(beat)
            self.set_instrument(M, slide)
            self.hands(HANDS[self.inst][1], M, slide=slide, offsets=offsets)
        return self.base() + self.play_torso(beat) + list(extra) + [("fn", fn)]

    def pose_between(self, t, torso_w, hand_w, arc_w, breath_f=None, extra=()):
        """Between rest (t=0) and the playing pose at beat 0 (t=1). torso_w blends the playing
        torso, hand_w {side: w} moves hands that change grip, arc_w scales the ARC offset."""
        b = self.breath(breath_f) * (1 - t) if breath_f is not None else 0.0

        def fn(rig):
            if self.kind == "piano":
                self.piano_play_hands(0.0, w=t)
                return
            R = self.rest_matrix(REST, b)
            P, slide, offsets = self.play_parts(0.0)
            t_rot = smooth(min(1.0, t / ROT_LEAD.get(self.inst, 1.0)))
            pivot = Vector(self.grips[ROT_PIVOT[self.inst]]) if self.inst in ROT_PIVOT else Vector()
            M = blend_matrix(R @ Matrix.Translation(pivot), P @ Matrix.Translation(pivot), t, t_rot) @ Matrix.Translation(-pivot)
            M = Matrix.Translation(Vector(ARC[self.inst]) * self.s * arc_w) @ M
            self.set_instrument(M, slide * t)
            rest_h, play_h = HANDS[self.inst]
            assign, mix = {}, {}
            for sd in ("Left", "Right"):
                if rest_h[sd] == play_h[sd]:
                    assign[sd] = play_h[sd]
                else:
                    assign[sd] = rest_h[sd]
                    mix[sd] = (play_h[sd], hand_w[sd])
            offs = {k: v * t for k, v in offsets.items()}
            self.hands(assign, M, slide=slide * t, offsets=offs, mix=mix)
        ops = self.base(breath_f, breath_w=1 - t)     # the held breath fades as the horn comes up
        return ops + self.play_torso(0.0, torso_w) + list(extra) + [("fn", fn)]

    # --- actions ---------------------------------------------------------------
    def build_actions(self):
        if not self.fitted:
            raise RuntimeError(f"{self.rig.name}: call fit_seat() before build_actions()")
        rig = self.rig
        acts = []
        mk = lambda name, keys, loop=False: acts.append(ps2.make_action(rig, name, keys, fps=FPS, loop=loop))
        mk("rest", [(f, self.pose_rest(breath=f)) for f in range(0, REST_LEN + 1, 8)], loop=True)
        mk("raise", self.raise_keys())
        mk("play_loop", [(f, self.pose_play(f / BEAT)) for f in range(0, PLAY_LEN + 1, KEY_STEP)], loop=True)
        mk("lower", self.lower_keys())
        mk("freeze", self.freeze_keys())
        mk("startle", self.startle_keys())
        down = [("turn", "Spine", ps2.X, 6), ("turn", "Chest", ps2.X, 2)]
        mk("look_down", self.look_keys(36, 0, -32, down))
        yaw = self.drummer_yaw()
        k = TWIST.get(self.inst, 0.35)      # share of the turn taken by the torso, the head does the rest
        twist = [("turn", "Spine", ps2.Z, yaw * k * 0.12), ("turn", "Chest", ps2.Z, yaw * k * 0.28),
                 ("turn", "UpperChest", ps2.Z, yaw * k * 0.60)]
        mk("look_at_drummer", self.look_keys(36, yaw, -4, twist, head_share=1.0 - k, turn=yaw * k))
        mk("flip_page", self.flip_keys())
        mk("arms_rest", [(f, self.pose_rest(both_lap=True, breath=f)) for f in range(0, REST_LEN + 1, 8)], loop=True)
        if self.inst == "trombone":
            mk("spit_valve", self.spit_keys())
        return acts

    def raise_keys(self):
        """Anticipation (settle back and breathe in), the instrument swings up on its arc while the
        torso follows a few frames late, then leans into the horn and settles."""
        keys = []
        L = RAISE_LEN
        for f in range(0, L + 1, KEY_STEP):
            t = window(f, 2, L - 3, smoother)
            arc = math.sin(math.pi * t)
            torso = settle(f, 4, L, over=0.25)
            hand_w = {sd: window(f, *HAND_TIMING.get((self.inst, sd), HAND_TIMING[sd])) for sd in ("Left", "Right")}
            antic = pulse(f, 0, 3, 7)
            extra = [("turn", "Spine", ps2.X, -2.0 * antic), ("turn", "UpperChest", ps2.X, -1.5 * antic)]
            if f == L:
                keys.append((f, self.pose_play(0.0)))
            elif f == 0:
                keys.append((f, self.pose_rest(breath=0)))
            else:
                keys.append((f, self.pose_between(t, torso, hand_w, arc, breath_f=0, extra=extra)))
        return keys

    def lower_keys(self):
        """Breath out and the horn comes off the lips, swings down on its arc, the torso
        slumps a little past rest and settles."""
        keys = []
        L = RAISE_LEN
        for f in range(0, L + 1, KEY_STEP):
            t = 1.0 - window(f, 1, L - 4, smoother)
            arc = math.sin(math.pi * t)
            torso = 1.0 - window(f, 0, L - 6)
            hand_w = {sd: 1.0 - window(f, *HAND_TIMING_LOWER.get((self.inst, sd), HAND_TIMING_LOWER[sd]))
                      for sd in ("Left", "Right")}
            slump = pulse(f, L - 8, L - 3, L)
            extra = [("turn", "Spine", ps2.X, 2.5 * slump), ("turn", "UpperChest", ps2.X, 1.0 * slump)]
            if f == 0:
                keys.append((f, self.pose_play(0.0)))
            elif f == L:
                keys.append((f, self.pose_rest(breath=0)))
            else:
                keys.append((f, self.pose_between(t, torso, hand_w, arc, breath_f=0, extra=extra)))
        return keys

    def freeze_keys(self):
        """Snaps rigid: the shoulders brace and the chest stiffens in 3 frames with a small
        overshoot, then a held, barely trembling stillness."""
        tense = [("turn", "UpperChest", ps2.X, 3), ("turn", "LeftShoulder", ps2.Y, -3), ("turn", "RightShoulder", ps2.Y, 3)]
        keys = []
        for f in (0, 1, 2, 3, 4, 6, 9, 12, 15, 18, 21, 24):
            w = settle(f, 0, 6, over=0.18)
            tremble = 0.06 * math.sin(f * 2.1) if f > 6 else 0.0
            keys.append((f, self.pose_play(0.0, extra=scale_ops(tense, w + tremble))))
        return keys

    def startle_keys(self):
        """No anticipation: a fast jolt back with the shoulders up and the hands lifting (peak at 4),
        then an eased settle with a glance to the side, back to rest by 18."""
        jolt = [("turn", "Spine", ps2.X, -4), ("turn", "UpperChest", ps2.X, -2),
                ("turn", "LeftShoulder", ps2.Y, -6), ("turn", "RightShoulder", ps2.Y, 6), ("look", 0, 12)]
        up = {"Left": Vector((0, 0.03, 0.08)), "Right": Vector((0, 0.03, 0.08))}
        keys = []
        for f in (0, 1, 2, 3, 4, 6, 8, 10, 12, 14, 16, 18):
            w = window(f, 0, 3.5, lambda x: 1 - (1 - x) ** 3) if f <= 4 else 1.0 - window(f, 4, 17, smoother)
            w = w + (0.1 * math.sin(math.pi * (f - 4) / 4) if 4 < f < 8 else 0.0)
            glance = pulse(f, 6, 11, 18)
            extra = scale_ops(jolt, w) + [("look", 8 * glance, -2 * glance)]
            offs = {sd: v * w for sd, v in up.items()}
            keys.append((f, self.pose_rest(extra=extra, hand_offsets=offs, breath=f)))
        return keys

    def look_keys(self, length, yaw, pitch, body_ops, head_share=1.0, turn=0.0):
        """Head leads (eased, slight overshoot), the body follows a few frames later, then a
        breathing hold. turn (deg of torso twist) slides the lap hands back on the side turned
        toward, so the forearm stays off the belly."""
        keys = []
        for f in (0, 2, 4, 6, 8, 10, 12, 14, 18, 24, 30, length):
            head = settle(f, 0, 10, over=0.07)
            body = window(f, 3, 12)
            ops = scale_ops(body_ops, body) + [("look", yaw * head_share * head, pitch * head)]
            offs = {}
            if self.inst:
                for sd, sx in (("Left", 1), ("Right", -1)):
                    toward = max(0.0, sx * turn) / 30.0     # 1 at a 30 degree twist toward this side
                    if HANDS[self.inst][0][sd] is None and toward > 0:
                        offs[sd] = Vector((sx * 0.10, 0.10, 0.03)) * self.s * toward * body
            keys.append((f, self.pose_rest(extra=ops, breath=f, hand_offsets=offs)))
        return keys

    def drummer_yaw(self):
        d = self.drummer_local
        yaw = math.degrees(math.atan2(d.x, -d.y))   # 0 = straight ahead (-Y), + = toward the left (+X)
        return max(-110.0, min(110.0, yaw))

    def flip_keys(self):
        """The free hand lifts off and travels out and around the instrument, pinches the right
        page edge at 10, sweeps the page across on a forward arc to the left edge at 20 and comes
        back around to rest by 30. The torso leans toward the stand and the head follows the hand."""
        sd = FLIP_HAND.get(self.inst, "Right")
        sx = 1 if sd == "Left" else -1
        desk_z = 1.20 if self.standing else 1.02     # the room's stands (the bassist's is taller)
        right_edge = Vector((-0.20, -0.50, desk_z + 0.12))     # top corner of the right page
        left_edge = Vector((FLIP_LEFT_X.get(self.inst, 0.03), -0.54, desk_z + 0.14))   # lets go past the middle
        both_free = self.kind == "piano"
        around = Vector(FLIP_AROUND.get((self.inst, sd), FLIP_AROUND[sd])) * self.s + Vector((0, 0, desk_z))

        def bez(a, c, b, t):
            return a.lerp(c, t).lerp(c.lerp(b, t), t)

        def bez3(a, c1, c2, b, t):
            return bez(a, c1, c2, t).lerp(bez(c1, c2, b, t), t)

        keys = []
        for f in range(0, 31, KEY_STEP):
            if f <= 10:
                phase, w = 0, window(f, 1, 10)
            elif f <= 20:
                phase, w = 1, window(f, 10, 20)
            else:
                phase, w = 2, 1.0 - window(f, 21, 30)
            lean = window(f, 2, 10) * (1.0 - window(f, 21, 30))
            sweep = 0.0 if phase == 0 else (w if phase == 1 else 1.0)
            look_yaw = 6 * sweep - 3 * (1 - sweep)
            body = [("turn", "Spine", ps2.X, 4 * lean), ("look", look_yaw * lean, -8 * lean)]

            def after(rig, phase=phase, w=w):
                if phase != 1 and w <= 0.0:
                    return
                rest_pos = rig.pose.bones[f"{sd}Hand"].head.copy()
                if phase == 0:
                    pos = bez(rest_pos, around, right_edge, w)
                elif phase == 1:
                    pos = right_edge.lerp(left_edge, smooth(w)) + Vector((0, -0.08, 0.04)) * math.sin(math.pi * w)
                else:   # back in front of the desk, then out around the instrument
                    front = Vector((FLIP_FRONT[0], FLIP_FRONT[1], desk_z + FLIP_FRONT[2]))
                    pos = bez3(rest_pos, around, front, left_edge, w)
                out = 1.0 if phase == 1 else w
                g = None if both_free else HANDS[self.inst][0][sd]
                rest_dir = rig.pose.bones[f"{sd}Hand"].tail - rest_pos
                pole = self.pole(sd, g).lerp(Vector((sx * 1.0, 0.3, -0.6)), smooth(out))
                hdir = rest_dir.normalized().lerp(Vector((-sx * 0.3, -1, 0.1)).normalized(), smooth(out))
                self.reach(sd, pos, pole=pole, curl=0.2 + 0.25 * out, hand_dir=hdir, wrist_back=0.0)
            keys.append((f, self.pose_rest(extra=body, breath=f, fn_after=after)))
        return keys

    def spit_keys(self):
        """Tilts the trombone (eased) by 10, shakes the water out at 16 / 22 / 28, eases back to rest by 42."""
        loc, rot = SPIT
        tilt = Matrix.Translation(Vector(loc) * self.s) @ rot_matrix(rot)
        R = self.rest_matrix(REST)
        pivot = Vector(self.grips["grip_l"])
        keys = []
        for f in range(0, 43, KEY_STEP):
            w = window(f, 0, 10) * (1.0 - window(f, 32, 42))
            shake = sum(-0.03 * pulse(f, c - 3, c, c + 3) for c in (16, 22, 28))
            look = [("look", 0, -20 * w)]

            def fn(rig, w=w, shake=shake):
                M = blend_matrix(R @ Matrix.Translation(pivot), tilt @ Matrix.Translation(pivot), w) @ Matrix.Translation(-pivot)
                M = Matrix.Translation((0, 0, shake)) @ M
                M = Matrix.Translation(Vector((0, -0.08, 0.05)) * self.s * math.sin(math.pi * w)) @ M if 0 < w < 1 else M
                self.set_instrument(M)
                self.hands({"Left": "grip_l", "Right": None}, M, mix={"Right": ("grip_r", window(w, 0.2, 0.9))})
            keys.append((f, self.base(f) + look + [("fn", fn)]))
        return keys
