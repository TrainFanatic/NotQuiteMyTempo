"""Build the 17 band members (task A1 of OVERNIGHT.md).

Run:
    /Applications/Blender.app/Contents/MacOS/Blender --background --python build_band.py

Three base bodies (slim, average, broad) are varied by head size, skin tone, painted face,
hair (mesh shell or texture), clothing style and colours into the 17 members. Each member
is exported to band/<name>.glb: one skinned mesh + a SkeletonProfileHumanoid rig with
finger bones, T-pose rest, facing -Y, feet on the floor at the origin.
Animations (task B1) come from band_anims.py and are exported inside each glb, with an
extra "Instrument" bone (+ "InstrumentSlide" on trombones) that carries the instrument.

Every member's actions are measured by band_clearance.py before export; the worst values go
to logs/band_clearance_<VERSION>.txt and the build fails past band_clearance.LIMITS. For quick
iteration, `-- --check-only [--detail] [band_<member> ...]` measures without exporting.

Also writes band/band_<VERSION>.blend (line-up, actions not kept) and its sheet.
"""

import math
import sys
from pathlib import Path

import bpy
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib_ps2 as ps2  # noqa: E402
import band_anims  # noqa: E402
import band_clearance  # noqa: E402

OUT = ps2.ROOT / "band"
TEX_DIR = OUT / "textures"
VERSION = "v004"
TRI_TARGET = 2300      # OVERNIGHT starting target is 2,000; finger hands cost ~260 (see DECISIONS.md)
TEX = 128
HEIGHT_JITTER = 0.03   # +-3% height per member
BUILD_JITTER = 0.04    # +-4% shoulder/chest/waist per member

BASES = {
    "slim": dict(height=1.75, shoulder=0.92, chest=0.90, waist=0.88, hip=0.95, depth=0.92,
                 arm=0.90, leg=0.90, neck=0.94, head=1.00, jaw=0.95),
    "average": dict(height=1.79, shoulder=1.0, chest=1.0, waist=1.0, hip=1.0, depth=1.0,
                    arm=1.0, leg=1.0, neck=1.0, head=0.98, jaw=1.0),
    "broad": dict(height=1.83, shoulder=1.07, chest=1.10, waist=1.14, belly=0.03, hip=1.06,
                  depth=1.08, arm=1.08, leg=1.08, neck=1.08, head=1.0, jaw=1.06),
}

SKIN = {
    "fair": (0.86, 0.68, 0.56), "light": (0.80, 0.60, 0.46), "tan": (0.70, 0.50, 0.36),
    "brown": (0.52, 0.35, 0.24), "dark": (0.36, 0.23, 0.16),
}
HAIR = {"black": (0.05, 0.04, 0.035), "dark_brown": (0.16, 0.10, 0.06), "brown": (0.30, 0.19, 0.10),
        "blond": (0.62, 0.48, 0.26), "red": (0.45, 0.20, 0.09), "gray": (0.45, 0.44, 0.42)}

# name: base, skin, hair style, hair colour, facial hair, top style, top colours, pants, shoes, extras
MEMBERS = {
    "band_alto_sax_1":     ("average", "light", "short", "dark_brown", None, "plaid", ((0.35, 0.10, 0.10), (0.08, 0.08, 0.10)), "jeans", "sneaker_black", ()),
    "band_alto_sax_2":     ("slim", "fair", "curly", "brown", None, "tee", ((0.20, 0.30, 0.22),), "khaki", "sneaker_white", ()),
    "band_tenor_sax_1":    ("broad", "brown", "buzz", "black", "goatee", "hoodie", ((0.30, 0.30, 0.32),), "jeans", "sneaker_black", ()),
    "band_tenor_sax_2":    ("average", "tan", "long", "black", None, "shirt", ((0.60, 0.68, 0.78),), "black", "boot_brown", ()),
    "band_baritone_sax_1": ("broad", "light", "short", "red", "beard", "plaid", ((0.15, 0.25, 0.40), (0.85, 0.85, 0.80)), "gray", "boot_brown", ()),
    "band_trumpet_1":      ("slim", "dark", "short", "black", None, "tee", ((0.10, 0.10, 0.12),), "black", "sneaker_white", ()),
    "band_trumpet_2":      ("average", "fair", "cap", "blond", None, "stripes", ((0.75, 0.73, 0.68), (0.15, 0.20, 0.35)), "jeans", "sneaker_black", ()),
    "band_trumpet_3":      ("slim", "tan", "beanie", "dark_brown", "stubble", "tee", ((0.45, 0.12, 0.08),), "khaki", "sneaker_black", ()),
    "band_trumpet_4":      ("average", "light", "short", "brown", None, "polo", ((0.18, 0.22, 0.30),), "khaki", "boot_brown", ("glasses",)),
    "band_trombone_1":     ("broad", "fair", "short", "blond", "stubble", "shirt", ((0.85, 0.83, 0.78),), "black", "boot_brown", ()),
    "band_trombone_2":     ("average", "dark", "curly", "black", "mustache", "tee", ((0.55, 0.45, 0.20),), "jeans", "sneaker_white", ()),
    "band_trombone_3":     ("slim", "light", "long", "brown", None, "plaid", ((0.20, 0.30, 0.18), (0.10, 0.10, 0.08)), "jeans", "boot_brown", ()),
    "band_trombone_4":     ("average", "brown", "buzz", "black", None, "hoodie", ((0.12, 0.14, 0.22),), "gray", "sneaker_white", ()),
    "band_piano_1":        ("slim", "fair", "short", "dark_brown", None, "shirt", ((0.30, 0.33, 0.40),), "black", "boot_brown", ("glasses",)),
    "band_guitar_1":       ("average", "tan", "cap", "black", "beard", "stripes", ((0.12, 0.12, 0.14), (0.55, 0.55, 0.50)), "jeans", "sneaker_black", ()),
    "band_bass_1":         ("broad", "dark", "bald", "black", "goatee", "polo", ((0.25, 0.12, 0.10),), "khaki", "boot_brown", ()),
}
# OVERNIGHT lists 17 names but its ranges add up to 16 (2+2+1+4+4+1+1+1); the 17th player is
# the drummer, who is the real person. See DECISIONS.md.

PANTS = {"jeans": ((0.16, 0.22, 0.36), "denim"), "khaki": ((0.52, 0.45, 0.32), "twill"),
         "black": ((0.07, 0.07, 0.08), "twill"), "gray": ((0.30, 0.30, 0.31), "twill")}
SHOES = {"sneaker_white": (0.80, 0.80, 0.78), "sneaker_black": (0.08, 0.08, 0.09), "boot_brown": (0.26, 0.15, 0.08)}
HAIR_MESH = {  # front_z, back_z, side_z, thickness, bump
    "short": (1.752, 1.615, 1.66, 0.007, 0.0), "curly": (1.748, 1.60, 1.65, 0.014, 0.012),
    "long": (1.755, 1.505, 1.585, 0.011, 0.0), "beanie": (1.722, 1.640, 1.655, 0.013, 0.0),
    "cap": (1.725, 1.640, 1.655, 0.010, 0.0),
}

# Face texture layout (canonical head space, see ps2.default_face_uv): front planar into
# u 0..0.75, v from z=1.53 to the scalp; the right quarter is a strip for the back of the head.
def u_of(x):
    return 0.375 + x / 0.19 * 0.75


def v_of(z):
    return (z - 1.53) / (ps2.HEAD_TOP - 1.53)


def paint_face(seed, skin, hair, style, facial, glasses):
    rng = np.random.default_rng(seed)
    n = TEX
    x, y = ps2.grid(n, n)
    u, v = x / n, y / n
    arr = ps2.noise(n, skin, 0.035, seed)
    # soft shading toward the sides of the face and under the chin
    edge = np.clip(np.abs(u - 0.375) / 0.375, 0, 1)
    arr *= (1.0 - 0.22 * edge ** 2)[..., None]
    arr[(v < 0.10) & (u < 0.75)] *= 0.82
    dark = np.array(skin) * 0.55

    def blob(cx, cy, rx, ry, color, mix=1.0):
        m = (((u - cx) / rx) ** 2 + ((v - cy) / ry) ** 2) < 1
        arr[m] = arr[m] * (1 - mix) + np.array(color) * mix
        return m

    ey = v_of(1.692)
    iris = [(0.25, 0.16, 0.08), (0.20, 0.28, 0.38), (0.22, 0.25, 0.14)][rng.integers(0, 3)]
    for ex in (-0.031, 0.031):
        cx = u_of(ex)
        blob(cx, ey + 0.012, 0.042, 0.03, dark, 0.35)               # socket shadow
        blob(cx, ey, 0.030, 0.016, (0.86, 0.84, 0.80))               # white
        blob(cx, ey, 0.012, 0.016, iris)                             # iris
        blob(cx, ey, 0.006, 0.009, (0.03, 0.03, 0.03))               # pupil
        arr[(np.abs(u - cx) < 0.032) & (np.abs(v - (ey + 0.016)) < 0.006)] = dark * 0.6  # upper lid
        # brow
        by = v_of(1.708)
        tilt = (u - cx) * (0.12 if ex > 0 else -0.12)
        arr[(np.abs(u - cx) < 0.038) & (np.abs(v - by - tilt) < 0.009)] = np.array(hair) * 0.9 + 0.02
        if glasses:
            ring = ((np.abs(u - cx) < 0.05) & (np.abs(v - ey) < 0.034)) & ~((np.abs(u - cx) < 0.042) & (np.abs(v - ey) < 0.026))
            arr[ring] = (0.05, 0.05, 0.05)
    if glasses:
        arr[(np.abs(u - 0.375) < 0.025) & (np.abs(v - ey - 0.01) < 0.006)] = (0.05, 0.05, 0.05)
    # nose
    ny = v_of(1.650)
    for sx in (-1, 1):
        arr[(np.abs(u - (0.375 + sx * 0.035)) < 0.010) & (v > ny) & (v < ey - 0.03)] *= 0.86
        blob(0.375 + sx * 0.020, ny - 0.004, 0.010, 0.007, dark * 0.7)
    blob(0.375, ny + 0.01, 0.03, 0.03, np.array(skin) * 1.08, 0.5)
    # mouth
    my = v_of(1.607)
    blob(0.375, my, 0.07, 0.012, (skin[0] * 0.62, skin[1] * 0.38, skin[2] * 0.36))
    arr[(np.abs(u - 0.375) < 0.065) & (np.abs(v - my) < 0.004)] = dark * 0.55
    blob(0.375, my - 0.022, 0.05, 0.012, np.array(skin) * 0.92, 0.6)
    # facial hair
    hc = np.array(hair)
    jaw = (u < 0.75) & (v < v_of(1.630))
    if facial == "beard":
        m = jaw & ((v < my - 0.015) | (edge > 0.55)) & (v > 0.0)
        arr[m] = hc * (0.9 + 0.2 * rng.random((m.sum(), 1)))
        arr[(np.abs(u - 0.375) < 0.085) & (np.abs(v - (my + 0.025)) < 0.014)] = hc
    elif facial == "goatee":
        arr[(np.abs(u - 0.375) < 0.06) & (v < my - 0.012) & (v > 0.03)] = hc
        arr[(np.abs(u - 0.375) < 0.075) & (np.abs(v - (my + 0.022)) < 0.01)] = hc
    elif facial == "mustache":
        arr[(np.abs(u - 0.375) < 0.08) & (np.abs(v - (my + 0.024)) < 0.012)] = hc
    elif facial == "stubble":
        m = jaw & (v < my + 0.04) & ~((np.abs(u - 0.375) < 0.06) & (np.abs(v - my) < 0.014))
        arr[m] = arr[m] * 0.82 + hc * 0.18
    # scalp hair painted into the texture (front above the hairline, strip above the nape)
    if style != "bald":
        hl = v_of(1.752 if style != "buzz" else 1.748)
        mix = 0.55 if style == "buzz" else 1.0
        front = (u < 0.75) & (v > hl + 0.02 * np.cos((u - 0.375) * 6))
        sides = (u < 0.75) & (edge > 0.78) & (v > v_of(1.66))
        back = (u >= 0.75) & (v > (1.615 - 1.44) / (ps2.HEAD_TOP - 1.44))
        for m in (front, sides, back):
            tone = hc * (0.85 + 0.3 * rng.random((m.sum(), 1)))
            arr[m] = arr[m] * (1 - mix) + tone * mix
    return arr


def tex_hair(seed, hair, style):
    n = TEX
    x, y = ps2.grid(n, n)
    hc = np.array(hair)
    if style == "beanie":
        knit = 1.0 + 0.25 * ((x % 4) < 2) - 0.12 * ((y % 6) < 1)
        return np.ones((n, n, 3)) * np.array(hair) * knit[..., None]
    if style == "cap":
        return ps2.noise(n, hair, 0.05, seed)
    rng = np.random.default_rng(seed)
    streak = 1.0 + 0.25 * np.sin(x * 0.9 + rng.uniform(0, 6)) * np.sin(y * 0.15)
    if style == "curly":
        streak = 1.0 + 0.35 * np.sin(x * 0.8) * np.sin(y * 0.8)
    return hc[None, None, :] * (streak * (1 + rng.uniform(-0.12, 0.12, (n, n))))[..., None]


def tex_top(style, colors, seed):
    n = TEX
    c1 = colors[0]
    if style == "plaid":
        return ps2.plaid(n, c1, colors[1], (0.8, 0.8, 0.75), seed)
    if style == "stripes":
        return ps2.stripes(n, c1, colors[1], seed, period=14, width=6)
    arr = ps2.noise(n, c1, 0.06, seed)
    x, y = ps2.grid(n, n)
    if style in ("shirt", "polo"):
        placket = np.abs(x - n * 0.75) < 2      # u 0.75 faces the front on torso lofts
        arr[placket] *= 0.8
        if style == "shirt":
            arr[placket & ((y % 16) < 2)] = (0.9, 0.9, 0.88)
    if style == "hoodie":
        arr[(np.abs(x - n * 0.75) < 14) & (y > n * 0.30) & (y < n * 0.50)] *= 0.85   # pocket
    folds = 1.0 + 0.10 * np.sin(x / n * 2 * math.pi * 4 + np.sin(y / 9.0))
    return arr * folds[..., None]


def tex_pants(kind, seed):
    color, weave = PANTS[kind]
    n = TEX
    x, y = ps2.grid(n, n)
    arr = ps2.noise(n, color, 0.07, seed)
    if weave == "denim":
        arr *= (1.0 + 0.10 * (((x + y) % 4) < 2))[..., None]
    arr *= (1.0 + 0.25 * np.exp(-((x - n * 0.75) / 2.5) ** 2))[..., None]
    return arr


def tex_shoe(kind, seed):
    n = TEX
    x, y = ps2.grid(n, n)
    arr = ps2.noise(n, SHOES[kind], 0.05, seed)
    if kind.startswith("sneaker"):
        arr[y < 16] = (0.85, 0.84, 0.80) if kind == "sneaker_black" else (0.55, 0.55, 0.55)
        arr[(y > 40) & ((x % 32) < 3)] *= 0.6   # laces/stitching
    else:
        arr[y < 10] = (0.06, 0.04, 0.03)
    return arr


def tex_skin(skin, seed):
    return ps2.noise(TEX, skin, 0.05, seed)


def build_member(name, spec, seed, col):
    base, skin_k, hair_style, hair_k, facial, top_style, top_cols, pants_k, shoes_k, extras = spec
    P = dict(BASES[base])
    height = P.pop("height")
    # per-member build variation on top of the base body (deterministic from the seed)
    rng = np.random.default_rng(seed)
    height *= 1.0 + rng.uniform(-HEIGHT_JITTER, HEIGHT_JITTER)
    for k in ("shoulder", "chest", "waist"):
        P[k] = P.get(k, 1.0) * (1.0 + rng.uniform(-BUILD_JITTER, BUILD_JITTER))
    skin, hair = SKIN[skin_k], HAIR[hair_k]
    lib = ps2.MatLib(TEX_DIR, prefix=f"{name}_")
    lib.add("head", paint_face(seed, skin, hair, hair_style, facial, "glasses" in extras), uv="fit", rough=0.6)
    lib.add("skin", tex_skin(skin, seed + 1), rough=0.6)
    lib.add("top", tex_top(top_style, top_cols, seed + 2), rough=0.9)
    lib.add("pants", tex_pants(pants_k, seed + 3), rough=0.85)
    lib.add("shoes", tex_shoe(shoes_k, seed + 4), rough=0.5)
    hair_col = (0.10, 0.12, 0.20) if hair_style == "beanie" else ((0.55, 0.10, 0.08) if hair_style == "cap" else hair)
    lib.add("hair", tex_hair(seed + 5, hair_col, hair_style), rough=0.8)

    hs = P["head"]
    P["mats"] = dict(head="head", skin="skin", top="top", pants="pants", shoes="shoes")
    P["sleeve"] = "long" if top_style in ("plaid", "shirt", "hoodie") else "short"
    P["untucked"] = top_style in ("plaid", "hoodie", "tee", "stripes")
    P["face_uv"] = lambda co: (0.375 + co.x / (0.19 * hs) * 0.75, (co.z - (ps2.CHIN_Z - 0.018 * hs)) / (0.27 * hs))
    b = ps2.Builder(lib)
    info = ps2.build_humanoid(b, P)
    if hair_style in HAIR_MESH:
        fz, bz, sz, th, bump = HAIR_MESH[hair_style]
        ps2.hair_cap(b, "hair", info["head_rings"], ps2.CHIN_Z + (fz - ps2.CHIN_Z) * hs,
                     ps2.CHIN_Z + (bz - ps2.CHIN_Z) * hs, ps2.CHIN_Z + (sz - ps2.CHIN_Z) * hs,
                     thick=th * hs, top_z=info["top_z"], bump=bump, seed=seed, segs=12, steps=3)
    if hair_style == "cap":
        z = ps2.CHIN_Z + (1.728 - ps2.CHIN_Z) * hs
        fy = ps2.head_point(info["head_rings"], z, math.radians(270)).y
        b.box((0, fy - 0.045 * hs, z - 0.004), (0.15 * hs, 0.09 * hs, 0.008), "hair",
              rot=(math.radians(-8), 0, 0), weight={"Head": 1.0})
    s = height / ps2.CANON_H
    from mathutils import Matrix
    b.transform(Matrix.Scale(s, 4))
    mesh_ob = b.build(f"{name}_mesh", col, smooth=True)
    bones = ps2.humanoid_bones(s)
    extra = ["Instrument"] + (["InstrumentSlide"] if "trombone" in name else [])
    bones.update(ps2.prop_bones(extra))
    rig = ps2.make_armature(name, bones, col)
    ps2.skin_mesh(mesh_ob, rig, b.weight_list)
    tris = ps2.tri_count(mesh_ob)
    if tris > TRI_TARGET:
        raise RuntimeError(f"{name}: {tris} triangles > {TRI_TARGET}")
    rig["base_body"] = base
    rig["triangles"] = tris
    return rig, mesh_ob, tris


def kind_of(name):
    k = name.replace("band_", "").rsplit("_", 1)[0]
    return k


def room_markers():
    with bpy.data.libraries.load(str(ps2.ROOT / "room/room_v003.blend"), link=False) as (src, dst):
        dst.objects = [n for n in src.objects if n.startswith(("seat_", "spot_"))]
    out = {o.name: (o.location.copy(), o.rotation_euler.z) for o in dst.objects}
    for o in dst.objects:
        bpy.data.objects.remove(o, do_unlink=True)
    return out


def drummer_local(markers, member):
    from mathutils import Matrix
    seat = next(m for m, (mem, _) in band_anims.SEATING.items() if mem == member)
    loc, yaw = markers[seat]
    d = markers["spot_drummer"][0] - loc
    return Matrix.Rotation(-(yaw + math.pi), 3, "Z") @ d


def family_of(kind):
    return "sax" if kind.endswith("sax") else kind


def clearance_report(rows):
    """rows: [(member, {check: (depth, action, frame)})] -> report lines, worst per family first."""
    fam = {}
    for name, worst in rows:
        f = family_of(kind_of(name))
        for k, (d, act, fr) in worst.items():
            if k not in fam.setdefault(f, {}) or d > fam[f][k][0]:
                fam[f][k] = (d, name, act, fr)
    lines = [f"# band {VERSION} clearance (metres of penetration, sampled every {band_clearance.STEP} frames)", ""]
    for f, checks in fam.items():
        lines.append(f"[{f}]")
        for k in sorted(checks):
            d, name, act, fr = checks[k]
            lines.append(f"  {k:<14} {d * 100:6.2f} cm   {name} {act} @{fr}")
    lines.append("")
    for name, worst in rows:
        bad = sorted(((d, k, a, fr) for k, (d, a, fr) in worst.items() if d > band_clearance.REPORT_CM / 100), reverse=True)
        lines.append(f"{name}: " + (", ".join(f"{k} {d * 100:.1f} ({a}@{fr})" for d, k, a, fr in bad[:6]) or "clear"))
    return lines


def main():
    args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    check_only = "--check-only" in args
    only = [a for a in args if a.startswith("band_")]
    ps2.reset_scene()
    col = ps2.new_collection("band")
    prev = ps2.new_collection("preview_only")
    grips, shapes = {}, {}
    for inst in {v for v in band_anims.INSTRUMENT_OF.values() if v}:
        grips[inst] = band_anims.read_grips(ps2.ROOT / "instruments" / f"{inst}_1.glb")
        shapes[inst] = band_clearance.read_shape(ps2.ROOT / "instruments" / f"{inst}_1.glb")
    markers = room_markers()
    results, rows = [], []
    for i, (name, spec) in enumerate(MEMBERS.items()):
        if only and name not in only:
            continue
        rig, mesh_ob, tris = build_member(name, spec, 100 + i * 17, col)
        player = band_anims.Player(rig, kind_of(name), grips, drummer_local(markers, name), name)
        furniture = "stool" if player.standing else ("piano" if player.kind == "piano" else "chair")
        clr = band_clearance.Clearance(rig, mesh_ob, shapes.get(player.inst), wind=player.wind, furniture=furniture)
        player.fit_seat(clr)
        acts = player.build_actions()
        worst = clr.sample_actions(acts)
        rows.append((name, worst))
        if "--detail" in args:
            for an, pa in clr.per_action.items():
                bad = sorted(((v, k, f, w) for k, (v, f, w) in pa.items() if v > band_clearance.REPORT_CM / 100), reverse=True)
                print(f"[detail] {name} {an}: " + ", ".join(f"{k} {v * 100:.1f}@{f} ({w})" for v, k, f, w in bad))
        print(f"[build_band] {name} clearance: " + ", ".join(f"{k} {v[0] * 100:.1f}" for k, v in sorted(worst.items())))
        if not check_only:
            ps2.export_glb(OUT / f"{name}.glb", [rig, mesh_ob], animations=True)
        rig["actions"] = ", ".join(a.name for a in acts)
        for a in acts:   # actions with matching bones would leak into the next member's glb
            bpy.data.actions.remove(a)
        results.append((name, rig, mesh_ob, tris, player))
        print(f"[build_band] {name}: {tris} tris, base {spec[0]}, {len(acts)} actions")
    report = clearance_report(rows)
    log = ps2.ROOT / "logs" / f"band_clearance_{VERSION}{'_check' if check_only else ''}.txt"
    log.write_text("\n".join(report) + "\n")
    print("\n".join(report))
    over = band_clearance.over_limits(rows)
    if over:
        raise RuntimeError("band clearance over the limits: " + "; ".join(over))
    if check_only or only:
        return
    # B1 review: rest and play poses for one player per family, instrument placed at the
    # Instrument bone exactly as Godot's BoneAttachment3D will place it.
    sc = ps2.setup_render(256, 256, samples=16)
    ps2.neutral_lights(prev, center=(0, 0, 1.0), power=300)
    picks = ["band_alto_sax_1", "band_trumpet_1", "band_trombone_1", "band_guitar_1"]
    for nm, rg, me, _, _ in results:
        if nm not in picks:
            rg.hide_render = True
            me.hide_render = True
    shots, labels = [], []
    by_name = {r[0]: r for r in results}
    for i, name in enumerate(picks):
        _, rig, _, _, player = by_name[name]
        x = (i - 1.5) * 3.0
        rig.location = (x, 0, 0)
        bpy.context.view_layer.update()
        inst = band_anims.SEATING[next(m for m, (mem, _) in band_anims.SEATING.items() if mem == name)][1]
        before = set(bpy.data.objects)
        bpy.ops.import_scene.gltf(filepath=str(ps2.ROOT / "instruments" / f"{inst}.glb"))
        new = [o for o in bpy.data.objects if o not in before]
        body = next(o for o in new if o.parent is None and o.type == "MESH")
        slide = next((o for o in new if o.name.endswith("_slide")), None)

        def place(ops, rig=rig, player=player, body=body, slide=slide):
            ps2.apply_pose(rig, ops)
            body.matrix_world = rig.matrix_world @ rig.pose.bones["Instrument"].matrix
            if slide is not None:
                slide.matrix_world = rig.matrix_world @ rig.pose.bones["InstrumentSlide"].matrix
            bpy.context.view_layer.update()
        place(player.pose_rest())   # so it never sits at the world origin in another player's shot
        for mode in ("rest", "play"):
            def setup(player=player, mode=mode, place=place):
                place(player.pose_rest() if mode == "rest" else player.pose_play(0.0))
            cam = ps2.add_camera(f"preview_cam_{name}_{mode}", prev, (x + 1.1, -1.6, 1.25), (x, -0.15, 0.85), lens=32)
            shots.append((cam, setup))
            labels.append(f"{name.replace('band_', '')} {mode}")
    for ld in [o for o in prev.objects if o.type == "LIGHT"]:
        ld.data.size = 30
    frames = ps2.render_frames(shots)
    ps2.compose_sheet(frames, OUT / f"band_{VERSION}_sheet.png", cols=4, size=(1024, 512), labels=labels)
    bpy.ops.wm.save_as_mainfile(filepath=str(OUT / f"band_{VERSION}.blend"))
    print("[build_band] done", sum(r[3] for r in results), "total tris")


main()
