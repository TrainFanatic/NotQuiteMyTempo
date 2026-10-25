"""Build the props (task A3 of OVERNIGHT.md), one .glb each in props/.

Run:
    /Applications/Blender.app/Contents/MacOS/Blender --background --python build_props.py

Props Terence handles (chair, cowbell, drum, cymbal, stand, pages) carry grip_l / grip_r
empties where his hands go. Origins sit on the floor (or at the centre of mass for the
cymbal, cowbell, stick and pages) so a RigidBody3D in Godot behaves without offsets.
    chair                     black tubular-steel folding chair with vinyl padding, open, faces -Y like
                              the room's chairs (the same chair as chair_folded)
    chair_folded              the same folding chair folded flat, 0.85 x 0.45 x 0.07 m, standing on its
                              feet; grips on the top bar (thrown like a disc)
    props_<VERSION>_sheet.png all props; props_<VERSION>_chairs_sheet.png close-ups of the two chairs
    cowbell                   cowbell + separate child object cowbell_stick
    spare_drum                14" snare on its own (no stand)
    cymbal                    18" crash, origin at the bell hole
    sheet_music_pages         four loose pages, separate objects page_1..page_4
    music_stand_knockable     base, pole and desk as separate child meshes, pivot_tip empty
                              on the base edge it tips over
"""

import math
import sys
from pathlib import Path

import bpy
import numpy as np
from mathutils import Matrix, Vector

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib_ps2 as ps2  # noqa: E402

OUT = ps2.ROOT / "props"
TEX_DIR = OUT / "textures"
VERSION = "v001"
TRI_TARGET = 300
TEX = 128

SEAT_H = 0.46


def lib_for(name):
    lib = ps2.MatLib(TEX_DIR, prefix=f"{name}_")
    return lib


def grip(name, root, col, loc):
    ps2.empty(name, col, loc, size=0.04, parent=root)


def collect(ob):
    out = [ob]
    for ch in ob.children:
        out.extend(collect(ch))
    return out


# ---------------------------------------------------------------------------
# Folding chair (chair and chair_folded are the same chair, open and folded)
# ---------------------------------------------------------------------------
# Construction, shared by both props:
#   back frame   one round steel tube bent into an inverted U (back legs, rounded corners, top bar he
#                grips), a cross-brace low between the back legs, the curved padded backrest between
#                the uprights
#   front legs   two round tubes joined by a tight bend into a crossbar under the seat front (an inverted
#                U), a cross-brace low between them, a pivot rivet through each top corner
#   seat         padded vinyl cushion on a steel pan, side plates carrying the front-leg pivots, rear link
#                plates whose pins ride in the back legs (the pin sits higher on the back legs when folded)
#   rubber foot caps on all four legs
# Folding is the real collapse: the seat swings up about the front-leg pivots until it lies flat in front
# of the backrest (padded face out), and the front legs close up parallel to the back legs, nested just
# inside them. The same frames are placed by three matrices (back frame, front legs, seat), open or folded.
CHAIR_TRI_LIMIT = 900          # hero prop exception to TRI_TARGET, see DECISIONS.md
CHAIR_TEX = 256
CUSH_T = 0.025                 # pan bottom to crown
CUSH_LAYERS = ((0.010, 0.000), (0.000, 0.005), (0.004, 0.006), (-0.003, 0.013), (0.014, 0.022), (0.050, CUSH_T))
CUSH_BANDS = ("pan", "side", "side", "side", "top")   # (inset, height) ring pairs above, bottom to crown
B_X, B_R, B_CORNER = 0.2125, 0.011, 0.035              # back frame upright x, tube radius, top corner radius
F_X, F_R, F_CORNER = 0.186, 0.0095, 0.012              # front legs, nested inside the back legs
SEAT_W, SEAT_D = 0.346, 0.37
BACK_W, BACK_H, BACK_TOP_GAP, BACK_BEND = 0.39, 0.26, 0.035, 0.008
PIV_Y, PIV_Z = 0.03, 0.014     # front-leg pivot in seat space: behind the seat front, below the pan
LINK_Y0, LINK_Y1 = 0.33, 0.385 # rear link plates along the seat depth
# open chair: floor contact, matching the old chair's footprint, seat top at SEAT_H, grips on the top bar
OPEN_B_FOOT_Y, OPEN_B_TOP = 0.18, (0.22, 0.89)
OPEN_F_FOOT_Y, OPEN_SEAT_FRONT_Y = -0.18, -0.195
# folded chair: back frame plane at y 0 (the grips), front legs nested just in front of it
FOLD_B_H, FOLD_F_Y = 0.835, -0.015


def tex_chair_steel(seed, w=128, h=256):
    """Black-grey painted steel tube: lengthwise brush marks, pale scuffs, paint chips down to bare steel.
    u runs round the tube, v along it."""
    rng = np.random.default_rng(seed)
    f = 1.0 + rng.uniform(-0.07, 0.07, (1, w)) + rng.uniform(-0.03, 0.03, (h, w))
    f = f + np.kron(rng.uniform(-0.04, 0.04, (h // 16, 1)), np.ones((16, w)))
    arr = np.array((0.075, 0.075, 0.082))[None, None, :] * f[..., None]
    for _ in range(16):                                            # scuffs along the tube
        x0, y0 = rng.integers(0, w), rng.integers(0, h)
        rows = np.arange(y0, y0 + rng.integers(8, 44)) % h
        cols = np.arange(x0, x0 + rng.integers(1, 3)) % w
        arr[np.ix_(rows, cols)] = arr[np.ix_(rows, cols)] * 0.45 + np.array((0.21, 0.21, 0.22)) * 0.55
    x, y = ps2.grid(w, h)
    for _ in range(18):                                            # chips: dark rim, bare steel inside
        cx, cy, r = rng.integers(0, w), rng.integers(0, h), rng.uniform(0.7, 2.4)
        dx = np.minimum(abs(x - cx), w - abs(x - cx))
        dy = np.minimum(abs(y - cy), h - abs(y - cy)) * rng.uniform(0.5, 1.0)
        d = np.hypot(dx, dy)
        arr[d < r + 1.0] = (0.035, 0.035, 0.038)
        arr[d < r] = np.array((0.34, 0.34, 0.36)) * rng.uniform(0.8, 1.1)
    return arr


def _pebble(rng, w, h, base):
    f = (1.0 + rng.uniform(-0.06, 0.06, (h, w))
         + np.kron(rng.uniform(-0.035, 0.035, (h // 4, w // 4)), np.ones((4, 4))))
    return np.array(base)[None, None, :] * f[..., None]


def tex_chair_vinyl_panel(seed, wear, n=CHAIR_TEX):
    """Cushion face, mapped once over the panel: pebbled dark vinyl, a sewn seam with stitching inset
    from the edge, creases running in from the edges and corners, a shiny worn patch at `wear` (u, v, r)."""
    rng = np.random.default_rng(seed)
    arr = _pebble(rng, n, n, (0.060, 0.056, 0.053))
    x, y = ps2.grid(n, n)
    wx, wy, wr = wear
    g = np.exp(-((x / n - wx) ** 2 + (y / n - wy) ** 2) / (2 * wr * wr))
    arr = arr * (1 + 0.45 * g)[..., None] + (0.012 * g)[..., None]
    for _ in range(14):                                            # creases: dark fold with a lit ridge
        side = rng.integers(0, 4)
        t0 = rng.uniform(0.08, 0.92) * n
        p = np.array({0: (t0, 18.0), 1: (t0, n - 19.0), 2: (18.0, t0), 3: (n - 19.0, t0)}[side])
        ang = {0: 90, 1: -90, 2: 0, 3: 180}[side] + rng.uniform(-35, 35)
        d = np.array((math.cos(math.radians(ang)), math.sin(math.radians(ang))))
        nrm = np.array((-d[1], d[0]))
        length, phase = rng.uniform(14, 46), rng.uniform(0, 6)
        for s in np.arange(0, length, 0.5):
            q = p + d * s + nrm * 2.0 * math.sin(s / 7 + phase)
            fade = 1 - s / length
            for off, k in ((0, 1 - 0.45 * fade), (1, 1 + 0.35 * fade)):
                qx, qy = (q + nrm * off).astype(int)
                if 0 <= qx < n and 0 <= qy < n:
                    arr[qy, qx] *= k
    inset = 16
    inner = (x >= inset) & (x <= n - 1 - inset) & (y >= inset) & (y <= n - 1 - inset)
    arr[~inner] *= 0.9                                             # rolled edge outside the seam
    on_x = (np.abs(x - inset) < 1) | (np.abs(x - (n - 1 - inset)) < 1)
    on_y = (np.abs(y - inset) < 1) | (np.abs(y - (n - 1 - inset)) < 1)
    in_x = (x >= inset) & (x <= n - 1 - inset)
    in_y = (y >= inset) & (y <= n - 1 - inset)
    arr[(on_x & in_y) | (on_y & in_x)] *= 0.35                     # seam groove
    s_in = inset + 4
    st_x = ((np.abs(x - s_in) < 0.5) | (np.abs(x - (n - 1 - s_in)) < 0.5)) & (y >= s_in) & (y <= n - 1 - s_in)
    st_y = ((np.abs(y - s_in) < 0.5) | (np.abs(y - (n - 1 - s_in)) < 0.5)) & (x >= s_in) & (x <= n - 1 - s_in)
    dash = ((x + y) // 3) % 2 == 0
    arr[(st_x | st_y) & dash] = (0.19, 0.185, 0.175)               # stitching
    return arr


def tex_chair_vinyl_side(seed, n=128):
    """Cushion edge, tiled round the panel: pebbled vinyl with the welt seam (piping) at v 0.5."""
    rng = np.random.default_rng(seed)
    arr = _pebble(rng, n, n, (0.058, 0.054, 0.051))
    for cx in rng.integers(0, n, 9):                               # short pucker creases across the edge
        rows = np.arange(rng.integers(0, n // 3), rng.integers(n // 2, n))
        arr[rows, cx] *= 0.6
        arr[rows, (cx + 1) % n] *= 1.3
    m = n // 2
    arr[m - 1:m + 2] *= 1.45                                       # piping ridge
    arr[m + 2:m + 4] *= 0.4                                        # seam under it
    arr[m - 3:m - 1] *= 0.6
    return arr


def tex_chair_rubber(seed, n=128):
    rng = np.random.default_rng(seed)
    arr = ps2.noise(n, (0.035, 0.034, 0.033), 0.12, seed, clump=2)
    x, y = ps2.grid(n, n)
    arr[y < n // 8] *= 1.5                                         # scuffed ring at the floor end
    arr[(rng.random((n, n)) < 0.02)] *= 1.8
    return arr


def chair_lib(name):
    """Same textures (same seeds) for chair and chair_folded so they read as one chair."""
    lib = lib_for(name)
    lib.add("dark_metal", tex_chair_steel(41), uv=0.3, rough=0.45, metal=0.6)
    lib.add("black_vinyl", tex_chair_vinyl_side(42), uv=0.2, rough=0.72)
    lib.add("vinyl_seat", tex_chair_vinyl_panel(43, (0.5, 0.42, 0.20)), uv="fit", rough=0.7)
    lib.add("vinyl_back", tex_chair_vinyl_panel(44, (0.5, 0.62, 0.17)), uv="fit", rough=0.7)
    lib.add("rubber", tex_chair_rubber(45), uv=0.1, rough=0.85)
    lib.add("rivet", ps2.brushed(TEX, (0.52, 0.52, 0.55), 46), uv=0.05, rough=0.3, metal=0.8)
    return lib


def cushion(b, M, W, D, top_mat, bend=0.0):
    """Padded panel on a steel pan. Local x across (+-W/2), y along (0..D), z from the pan bottom (0)
    to the crown (CUSH_T); M maps local to prop space and must be a rigid motion. bend pushes the
    edges toward +z by bend * (2x/W)^2, so a backrest wraps round the sitter. The face gets one planar
    mapping of the panel texture, the edge a tiled side texture with the piping at the bulge."""
    n = 12
    locs, rows = [], []
    for inset, z in CUSH_LAYERS:
        ring = ps2.ring_points((0, D / 2, z), ps2.X, ps2.Y, W / 2 - inset, D / 2 - inset, n, sup=5)
        ring = [p + Vector((0, 0, bend * (2 * p.x / W) ** 2)) for p in ring]
        locs.append(ring)
        rows.append([b.bm.verts.new(M @ p) for p in ring])
    zs, ze = CUSH_LAYERS[1][1], CUSH_LAYERS[4][1]
    We, De = W + 0.006, D + 0.006

    def uv(kind, p, j):
        if kind == "top":
            return ((p.x + We / 2) / We, (p.y + 0.003) / De)
        if kind == "side":
            return (j / n * 6, (p.z - zs) / (ze - zs))
        if kind == "pan":
            return (j / n * 8, p.z / 0.3)
        return (p.x / 0.3, p.y / 0.3)                                # pan bottom

    mats = {"pan": "dark_metal", "side": "black_vinyl", "top": top_mat, "bottom": "dark_metal"}
    faces = []

    def face(items, kind):
        # rings run CCW seen from +z and rise band by band, so this order faces outward
        f = b.bm.faces.new([rows[i][j % n] for i, j in items])
        f.material_index = b._mi(mats[kind])
        for loop, (i, j) in zip(f.loops, items):
            loop[b.uv].uv = uv(kind, locs[i][j % n], j)
        faces.append(f)

    for i, kind in enumerate(CUSH_BANDS):
        for j in range(n):
            face([(i, j), (i, j + 1), (i + 1, j + 1), (i + 1, j)], kind)
    face([(len(rows) - 1, j) for j in range(n)], "top")
    face([(0, j) for j in reversed(range(n))], "bottom")
    b.explicit.update(faces)
    for f in faces:
        f.smooth = True
    return faces


def smooth_sides(faces):
    """Smooth-shade a tube's or cylinder's sides (quads), keep its end caps flat."""
    for f in faces:
        f.smooth = len(f.verts) == 4


def _u_frame(x, h, r_corner, arc_steps):
    """Inverted U: legs at +-x from z 0.012 (inside the foot caps) up into rounded corners and a bar at h."""
    pts = [Vector((-x, 0, 0.012))]
    for k in range(arc_steps + 1):
        a = math.radians(180 - 90 * k / arc_steps)
        pts.append(Vector((-x + r_corner + r_corner * math.cos(a), 0, h - r_corner + r_corner * math.sin(a))))
    for k in range(arc_steps + 1):
        a = math.radians(90 - 90 * k / arc_steps)
        pts.append(Vector((x - r_corner + r_corner * math.cos(a), 0, h - r_corner + r_corner * math.sin(a))))
    pts.append(Vector((x, 0, 0.012)))
    return pts


def chair_frames(folded):
    """(back-frame matrix, back-frame height, front-leg matrix, front-leg pivot height, seat matrix).
    Back-frame space: uprights vertical in the plane y 0, feet at z 0, top bar centre at z height.
    Front-leg space: the same, pivots at z pivot. Seat space: see cushion()."""
    open_seat = Matrix.Translation((0, OPEN_SEAT_FRONT_Y, SEAT_H - CUSH_T))
    piv = open_seat @ Vector((0, PIV_Y, -PIV_Z))                  # pivot of the open chair
    f_dy, f_dz = piv.y - OPEN_F_FOOT_Y, piv.z
    f_len = math.hypot(f_dy, f_dz)
    if folded:
        MB = Matrix.Identity(4)
        hb = FOLD_B_H
        MF = Matrix.Translation((0, FOLD_F_Y, 0))
        MS = Matrix.Translation((0, FOLD_F_Y - PIV_Z, f_len - PIV_Y)) @ Matrix.Rotation(math.radians(90), 4, "X")
    else:
        b_dy, b_dz = OPEN_B_TOP[0] - OPEN_B_FOOT_Y, OPEN_B_TOP[1]
        hb = math.hypot(b_dy, b_dz)
        MB = Matrix.Translation((0, OPEN_B_FOOT_Y, 0)) @ Matrix.Rotation(-math.atan2(b_dy, b_dz), 4, "X")
        MF = Matrix.Translation((0, OPEN_F_FOOT_Y, 0)) @ Matrix.Rotation(-math.atan2(f_dy, f_dz), 4, "X")
        MS = open_seat
    got = MF @ Vector((F_X, 0, f_len))
    want = MS @ Vector((F_X, PIV_Y, -PIV_Z))
    if (got - want).length > 1e-6:
        raise RuntimeError(f"chair: front-leg pivot {tuple(got)} does not meet the seat pivot {tuple(want)}")
    return MB, hb, MF, f_len, MS


def build_folding_chair(name, col, folded):
    lib = chair_lib(name)
    b = ps2.Builder(lib)
    MB, hb, MF, f_len, MS = chair_frames(folded)
    seat_rot = MS.to_euler()

    def at(M, *p):
        return M @ Vector(p)

    def lift(M, r):
        """Start a foot cap this far up its leg so the rim of a leaning leg's cap doesn't dip below the floor."""
        return r * math.tan(math.acos(min(1.0, M.col[2].z)))

    # back frame
    pts = [MB @ p for p in _u_frame(B_X, hb, B_CORNER, 3)]
    tubes = [b.tube(pts, [B_R] * len(pts), "dark_metal", segs=8, tile_v=0.3),
             b.cylinder(at(MB, -B_X, 0.005, 0.20), at(MB, B_X, 0.005, 0.20), 0.007, "dark_metal", caps=False)]
    back_z0 = hb - BACK_TOP_GAP - BACK_H
    cushion(b, MB @ Matrix.Translation((0, 0.006, back_z0)) @ Matrix.Rotation(math.radians(90), 4, "X"),
            BACK_W, BACK_H, "vinyl_back", bend=BACK_BEND)
    # pins of the seat's rear links ride in the back legs: wherever the link plates are now
    pin_z = (MB.inverted() @ MS @ Vector((B_X, (LINK_Y0 + LINK_Y1) / 2, -0.005))).z
    for sx in (-1, 1):
        tubes.append(b.cylinder(at(MB, sx * B_X, 0, lift(MB, 0.0135)), at(MB, sx * B_X, 0, 0.028), 0.0135, "rubber"))
        for z, x0 in ((back_z0 + BACK_H / 2, 0.19), (pin_z, 0.196)):
            tubes.append(b.cylinder(at(MB, sx * x0, 0, z), at(MB, sx * 0.2265, 0, z), 0.0055, "rivet", segs=6))
    # front legs
    f_top = f_len + 0.002
    pts = [MF @ p for p in _u_frame(F_X, f_top, F_CORNER, 2)]
    tubes += [b.tube(pts, [F_R] * len(pts), "dark_metal", segs=8, tile_v=0.3),
              b.cylinder(at(MF, -F_X, 0, 0.12), at(MF, F_X, 0, 0.12), 0.007, "dark_metal", caps=False)]
    for sx in (-1, 1):
        tubes.append(b.cylinder(at(MF, sx * F_X, 0, lift(MF, 0.012)), at(MF, sx * F_X, 0, 0.026), 0.012, "rubber"))
        tubes.append(b.cylinder(at(MF, sx * 0.170, 0, f_len), at(MF, sx * 0.195, 0, f_len), 0.0065, "rivet", segs=6))
    for faces, _ in tubes:
        smooth_sides(faces)
    # seat
    cushion(b, MS, SEAT_W, SEAT_D, "vinyl_seat")
    for sx in (-1, 1):
        b.box(at(MS, sx * 0.17475, 0.035, -0.006), (0.0035, 0.05, 0.032), "dark_metal", rot=seat_rot)
        b.box(at(MS, sx * 0.187, (LINK_Y0 + LINK_Y1) / 2, -0.005), (0.028, LINK_Y1 - LINK_Y0, 0.018),
              "dark_metal", rot=seat_rot)
    return b.build(name, col)


def build_chair(col):
    """The folding chair standing open, facing -Y, seat top at SEAT_H, grips on the top bar."""
    ob = build_folding_chair("chair", col, folded=False)
    grip("grip_l", ob, col, (0.17, 0.22, 0.90))
    grip("grip_r", ob, col, (-0.17, 0.22, 0.90))
    return ob


def build_chair_folded(col):
    """The folding chair collapsed flat, standing on its feet. Local frame: length along +Z (feet on the
    floor at z 0, top bar at z 0.835), width along X, thickness along Y (the short axis it spins about in
    flight; the padded seat faces -Y). Grips sit on the top bar."""
    ob = build_folding_chair("chair_folded", col, folded=True)
    grip("grip_l", ob, col, (0.14, 0.0, 0.835))
    grip("grip_r", ob, col, (-0.14, 0.0, 0.835))
    return ob


def build_cowbell(col):
    lib = lib_for("cowbell")
    lib.add("black", ps2.brushed(TEX, (0.06, 0.06, 0.065), 3), uv=0.15, rough=0.35, metal=0.5)
    lib.add("chrome", ps2.brushed(TEX, (0.75, 0.75, 0.78), 4), uv=0.1, rough=0.25, metal=0.8)
    lib.add("stick", ps2.wood(TEX, TEX, (0.78, 0.62, 0.38), 5, plank_px=TEX, seams=False), uv=0.2, rough=0.5)
    b = ps2.Builder(lib)
    # flattened tapered bell, open mouth toward -Y, handle at the back (+Y)
    rings = [ps2.ring_points((0, y, 0), ps2.X, ps2.Z, rx, rz, 8, sup=4)
             for y, rx, rz in ((0.09, 0.030, 0.022), (0.0, 0.045, 0.032), (-0.09, 0.060, 0.040))]
    b.loft(rings, "black", None, ps2.X, ps2.Z, cap_start="black", tile_v=0.2)
    b.cylinder((0, 0.09, 0), (0, 0.15, 0), 0.007, "chrome", segs=6)
    ob = b.build("cowbell", col)
    sb = ps2.Builder(lib)
    sb.cylinder((0, 0, -0.20), (0, 0, 0.20), 0.007, "stick", segs=6, r1=0.005)
    stick = sb.build("cowbell_stick", col)
    stick.parent = ob
    stick.location = (0.12, 0.0, 0.05)
    grip("grip_l", ob, col, (0.0, 0.13, 0.0))
    grip("grip_r", stick, col, (0.0, 0.0, -0.12))
    return ob


def tex_drum_shell(n):
    x, y = ps2.grid(n, n)
    arr = ps2.noise(n, (0.42, 0.06, 0.05), 0.03, 6)               # red sparkle wrap
    arr[(np.random.default_rng(6).random((n, n)) < 0.06)] *= 1.6
    return arr


def tex_head(n):
    x, y = ps2.grid(n, n)
    r = np.hypot(x - n / 2, y - n / 2) / (n / 2)
    arr = ps2.noise(n, (0.88, 0.86, 0.80), 0.03, 7)
    arr[r < 0.25] *= 0.92                                          # stick wear in the middle
    arr[r > 0.93] = (0.70, 0.70, 0.72)
    return arr


def build_spare_drum(col):
    lib = lib_for("spare_drum")
    lib.add("shell", tex_drum_shell(TEX), uv=0.3, rough=0.4)
    lib.add("head", tex_head(TEX), uv="fit", rough=0.7)
    lib.add("chrome", ps2.brushed(TEX, (0.75, 0.75, 0.78), 8), uv=0.1, rough=0.25, metal=0.8)
    b = ps2.Builder(lib)
    r, h = 0.178, 0.14
    rings = [ps2.ring_points((0, 0, z), ps2.X, ps2.Y, rr, rr, 12, sup=2)
             for z, rr in ((0.0, r + 0.006), (0.018, r + 0.006), (0.018, r), (h - 0.018, r), (h - 0.018, r + 0.006), (h, r + 0.006))]
    b.loft(rings, ["chrome", "chrome", "shell", "chrome", "chrome"], None, ps2.X, ps2.Y,
           cap_start="head", cap_end="head", tile_v=0.4)
    for k in range(8):                                             # lugs
        a = 2 * math.pi * k / 8
        b.box(((r + 0.012) * math.cos(a), (r + 0.012) * math.sin(a), h / 2), (0.016, 0.016, 0.05), "chrome",
              rot=(0, 0, a))
    ob = b.build("spare_drum", col)
    grip("grip_l", ob, col, (r, 0, h))
    grip("grip_r", ob, col, (-r, 0, h))
    return ob


def tex_cymbal(n):
    x, y = ps2.grid(n, n)
    r = np.hypot(x - n / 2, y - n / 2) / (n / 2)
    arr = np.ones((n, n, 3)) * np.array((0.78, 0.58, 0.26))
    arr *= (1.0 + 0.12 * np.sin(r * 90))[..., None]                # lathe grooves
    arr[r < 0.22] *= 1.12                                          # bell
    arr[r < 0.04] = (0.10, 0.08, 0.05)                             # hole
    return arr


def build_cymbal(col):
    lib = lib_for("cymbal")
    lib.add("bronze", tex_cymbal(TEX), uv="fit", rough=0.3, metal=0.8)
    b = ps2.Builder(lib)
    R = 0.229                                                       # 18"
    prof = [(0.008, 0.032), (0.04, 0.030), (0.055, 0.012), (R, -0.004)]  # (radius, z) top profile
    seg = 16
    top_rings = [ps2.ring_points((0, 0, z), ps2.X, ps2.Y, rr, rr, seg, sup=2) for rr, z in prof]
    bot_rings = [ps2.ring_points((0, 0, z - 0.003), ps2.X, ps2.Y, rr, rr, seg, sup=2) for rr, z in reversed(prof)]
    faces, _ = b.loft(top_rings + bot_rings, "bronze", None, ps2.X, ps2.Y, tile_v=0.3, flip=True)
    ob = b.build("cymbal", col)
    # map both sides with a top-down planar projection so the grooves are concentric
    for poly in ob.data.polygons:
        for li in poly.loop_indices:
            co = ob.data.vertices[ob.data.loops[li].vertex_index].co
            ob.data.uv_layers[0].data[li].uv = (0.5 + co.x / (2 * R), 0.5 + co.y / (2 * R))
    grip("grip_r", ob, col, (R - 0.02, 0, 0.0))
    grip("grip_l", ob, col, (-(R - 0.02), 0, 0.0))
    return ob


def tex_page(seed):
    rng = np.random.default_rng(seed)
    n = TEX
    arr = np.ones((n, n, 3)) * np.array((0.88, 0.86, 0.78))
    for top in range(n - 14, 10, -13):
        for k in range(5):
            arr[top - 2 * k, 8:n - 8] = (0.30, 0.28, 0.25)
        for nx in rng.integers(10, n - 10, 10):
            ny = top - rng.integers(0, 9)
            arr[ny - 1:ny + 1, nx - 1:nx + 2] = (0.08, 0.07, 0.06)
    arr *= (1 + rng.uniform(-0.03, 0.03, (n, n)))[..., None]
    return arr


def build_pages(col):
    lib = lib_for("pages")
    root = ps2.empty("sheet_music_pages", col, (0, 0, 0), size=0.1)
    for i in range(4):
        mname = f"page_{i + 1}"
        lib.add(mname, tex_page(20 + i), uv="fit", rough=0.9)
        b = ps2.Builder(lib)
        w, h = 0.216, 0.279                                         # US letter
        # 3 x 2 grid with a gentle curl along the width; both sides get the music
        nx, ny = 3, 2
        def p(ix, iy, sgn):
            u, v = ix / nx, iy / ny
            x = (u - 0.5) * w
            z = 0.012 * math.sin(u * math.pi) * (1 if sgn > 0 else 1) + (0.0005 * sgn)
            return Vector((x, (v - 0.5) * h, z))
        for sgn, inward in ((1, (0, 0, 1)), (-1, (0, 0, -1))):
            for ix in range(nx):
                for iy in range(ny):
                    pts = [p(ix, iy, sgn), p(ix + 1, iy, sgn), p(ix + 1, iy + 1, sgn), p(ix, iy + 1, sgn)]
                    uvs = [(ix / nx, iy / ny), ((ix + 1) / nx, iy / ny), ((ix + 1) / nx, (iy + 1) / ny), (ix / nx, (iy + 1) / ny)]
                    b.quad(pts, mname, inward, uvs=uvs)
        ob = b.build(mname, col)
        ob.parent = root
        ob.location = (i * 0.24 - 0.36, 0, 0)
        ob.rotation_euler = (0, 0, math.radians((-1) ** i * 6 * i))
        grip("grip_r", ob, col, (0.0, -0.12, 0.0))
    return root


def build_knockable_stand(col):
    lib = lib_for("stand_knock")
    lib.add("metal", ps2.brushed(TEX, (0.16, 0.16, 0.17), 30), uv=0.5, rough=0.4, metal=0.5)
    lib.add("music", tex_page(31), uv="fit", rough=0.9)
    root = ps2.empty("music_stand_knockable", col, (0, 0, 0), size=0.15)
    parts = {}
    b = ps2.Builder(lib)
    for k in range(3):
        a = math.radians(90 + k * 120)
        b.box((0.13 * math.cos(a), 0.13 * math.sin(a), 0.012), (0.28, 0.02, 0.02), "metal", rot=(0, 0, a))
    parts["base"] = b.build("stand_base", col)
    b = ps2.Builder(lib)
    b.box((0, 0, 0.51), (0.022, 0.022, 0.92), "metal")
    parts["pole"] = b.build("stand_pole", col)
    b = ps2.Builder(lib)
    tilt = math.radians(25)
    b.box((0, 0, 0), (0.52, 0.02, 0.36), "metal", rot=(-tilt, 0, 0))
    b.box((0, -0.09, -0.155), (0.52, 0.05, 0.02), "metal")
    r = Matrix.Rotation(-tilt, 4, "X")
    c = Vector((0, -0.015, 0))
    corners = [c + r @ Vector(v) for v in ((-0.235, 0, -0.145), (0.235, 0, -0.145), (0.235, 0, 0.145), (-0.235, 0, 0.145))]
    b.quad(corners, "music", r @ Vector((0, -1, 0)), uvs=[(0, 0), (1, 0), (1, 1), (0, 1)])
    parts["desk"] = b.build("stand_desk", col)
    parts["desk"].location = (0, 0, 1.02)
    for ob in parts.values():
        ob.parent = root
    ps2.empty("pivot_tip", col, (0, 0.13, 0.0), size=0.05, parent=root)   # falls away from the reader
    grip("grip_r", parts["pole"], col, (0, 0, 0.75))
    grip("grip_l", parts["desk"], col, (0.2, 0, 0.1))
    return root, list(parts.values())


def render_chair_closeups(prev, roots):
    """2 x 2 close-up sheet of the two chairs (look-dev only, the blend is saved before this)."""
    chair, folded = roots["chair"], roots["chair_folded"]
    every = [o for o in bpy.data.objects if o.type == "MESH"]
    shots = []
    side = (0, math.radians(90), 0)                                         # folded chair on its long edge
    views = ((folded, (0, 0, 0.23), side, ((0.55, -1.0, 0.5), (0.42, 0, 0.23))),      # folded, seat side
             (folded, (0, 0, 0.23), side, ((0.25, 0.95, 0.65), (0.42, 0, 0.21))),       # folded, back and edge
             (chair, (0, 0, 0), (0, 0, 0), ((0.85, -1.25, 0.85), (0, 0, 0.45))),         # open, front three-quarter
             (chair, (0, 0, 0), (0, 0, 0), ((-1.3, 0.7, 0.8), (0, 0.02, 0.43))))       # open, rear side
    for i, (ob, at, rot, (loc, target)) in enumerate(views):
        cam = ps2.add_camera(f"closeup_cam_{i}", prev, loc, target, lens=30)

        def setup(ob=ob, at=at, rot=rot):
            for o in every:
                o.hide_render = o not in collect(ob)
            ob.location = at
            ob.rotation_euler = rot
        shots.append((cam, setup))
    ps2.setup_render(512, 256, samples=16)
    frames = ps2.render_frames(shots)
    ps2.compose_sheet(frames, OUT / f"props_{VERSION}_chairs_sheet.png", cols=2,
                      labels=["chair_folded front", "chair_folded back", "chair front", "chair rear"])


def main():
    ps2.reset_scene()
    col = ps2.new_collection("props")
    prev = ps2.new_collection("preview_only")
    built = []
    for name, fn in (("chair", build_chair), ("cowbell", build_cowbell), ("spare_drum", build_spare_drum),
                     ("cymbal", build_cymbal), ("sheet_music_pages", build_pages)):
        root = fn(col)
        built.append((name, root))
    stand_root, stand_parts = build_knockable_stand(col)
    built.append(("music_stand_knockable", stand_root))
    built.append(("chair_folded", build_chair_folded(col)))   # last, so the older props keep their object names
    for name, root in built:
        objs = collect(root)
        tris = sum(ps2.tri_count(o) for o in objs if o.type == "MESH")
        limit = CHAIR_TRI_LIMIT if name in ("chair", "chair_folded") else TRI_TARGET
        if tris > limit:
            raise RuntimeError(f"{name}: {tris} triangles > {limit}")
        ps2.export_glb(OUT / f"{name}.glb", objs)
        print(f"[build_props] {name}: {tris} tris")
    layout = {"chair": (-1.6, 0, 0), "chair_folded": (-1.1, -0.55, 0.014), "cowbell": (-0.8, 0, 0.9), "spare_drum": (-0.2, 0, 0.5),
              "cymbal": (0.6, 0, 0.9), "sheet_music_pages": (0.5, -0.4, 0.3), "music_stand_knockable": (1.5, 0, 0)}
    for name, root in built:
        root.location = layout[name]
    dict(built)["cymbal"].rotation_euler = (math.radians(60), 0, 0)
    dict(built)["chair_folded"].rotation_euler = (math.radians(-90), 0, math.radians(-90))   # lying flat, seat up
    ps2.setup_render(1024, 512, samples=16)
    ps2.neutral_lights(prev, center=(0, 0, 0.8), power=400)
    cam = ps2.add_camera("preview_cam", prev, (0.0, -3.2, 1.8), (0.0, 0.0, 0.6), lens=30)
    frames = ps2.render_frames([(cam, None)])
    ps2.compose_sheet(frames, OUT / f"props_{VERSION}_sheet.png", cols=1)
    bpy.ops.wm.save_as_mainfile(filepath=str(OUT / f"props_{VERSION}.blend"))
    render_chair_closeups(prev, dict(built))
    print("[build_props] done")


main()
