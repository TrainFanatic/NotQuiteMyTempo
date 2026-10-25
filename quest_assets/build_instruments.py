"""Build the band's instruments (task A2 of OVERNIGHT.md), one .glb each in instruments/.

Run:
    /Applications/Blender.app/Contents/MacOS/Blender --background --python build_instruments.py

Local frames are "as played": the player faces -Y and stands/sits on the +Y side.
    winds   origin at the mouthpiece tip (attach to the mouth); the horn extends to -Y/-Z
    guitar  origin at the body centre, top facing -Y, neck toward +X (player's left)
    bass    origin at the endpin's floor contact, front facing -Y
    stand   origin on the floor under the desk, the reader on its -Y side (same as the room's stands)
Handheld instruments carry grip_l / grip_r empties (children of the root) where the left
and right hands hold them. The trombone's outer slide is a separate child object
(trombone_<n>_slide) that moves only along its local -Y axis, 0 to SLIDE_TRAVEL metres.
"""

import math
import sys
from pathlib import Path

import bpy
import numpy as np
from mathutils import Matrix, Vector

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib_ps2 as ps2  # noqa: E402

OUT = ps2.ROOT / "instruments"
TEX_DIR = OUT / "textures"
VERSION = "v001"
TRI_TARGET = 500
SLIDE_TRAVEL = 0.58    # first to seventh position
TEX = 128

FINISH = {   # name: base colour (sRGB), metallic
    "gold": ((0.80, 0.60, 0.24), 0.6), "gold_dark": ((0.62, 0.44, 0.17), 0.6),
    "silver": ((0.74, 0.74, 0.76), 0.7), "gold_pale": ((0.84, 0.70, 0.38), 0.6),
}


def mats(prefix, finish):
    lib = ps2.MatLib(TEX_DIR, prefix=prefix)
    color, metal = FINISH[finish]
    lib.add("brass", ps2.brushed(TEX, color, 1), uv=0.2, rough=0.35, metal=metal)
    lib.add("black", ps2.noise(TEX, (0.04, 0.04, 0.045), 0.05, 2), uv=0.2, rough=0.4)
    lib.add("pad", ps2.noise(TEX, (0.45, 0.32, 0.18), 0.08, 3), uv=0.1, rough=0.7)
    lib.add("pearl", ps2.noise(TEX, (0.88, 0.86, 0.80), 0.04, 4), uv=0.05, rough=0.3)
    return lib


def grip(name, root, col, loc):
    ps2.empty(name, col, loc, size=0.04, parent=root)


def finish_object(b, name, col):
    ob = b.build(name, col, smooth=True)
    tris = ps2.tri_count(ob)
    return ob, tris


# ---------------------------------------------------------------------------
# Saxophones
# ---------------------------------------------------------------------------
SAX = {  # scale, neck rise (baritone loops up), body length
    "alto": dict(scale=1.0, loop=False),
    "tenor": dict(scale=1.25, loop=False),
    "baritone": dict(scale=1.55, loop=True),
}


def build_sax(name, kind, finish, col):
    k = SAX[kind]
    s = k["scale"]
    lib = mats(f"{name}_", finish)
    b = ps2.Builder(lib)
    # mouthpiece (black) into the neck
    b.tube([(0, 0, 0), (0, -0.035, -0.012)], [0.007, 0.010], "black", segs=6, cap_start="black")
    if k["loop"]:
        neck = [(0, -0.035, -0.012), (0, -0.07, 0.0), (0, -0.10, -0.02), (0, -0.10, -0.08), (0, -0.075, -0.13)]
    else:
        neck = [(0, -0.035, -0.012), (0, -0.06, -0.03), (0, -0.07, -0.07)]
    b.tube(neck, [0.010 + 0.002 * i for i in range(len(neck))], "brass", segs=6)
    top = Vector(neck[-1])
    path = [top, top + Vector((0, 0.005, -0.08 * s)), top + Vector((0, 0.01, -0.43 * s)),
            top + Vector((0, -0.01, -0.52 * s)), top + Vector((0, -0.07, -0.56 * s)),
            top + Vector((0, -0.14, -0.52 * s)), top + Vector((0, -0.16, -0.40 * s)),
            top + Vector((0, -0.17, -0.33 * s))]
    radii = [r * s for r in (0.014, 0.017, 0.028, 0.032, 0.034, 0.036, 0.044, 0.066)]
    b.tube(path, radii, "brass", segs=8)
    # bell rim
    b.tube([path[-1], path[-1] + Vector((0, -0.004, 0.012))], [radii[-1], radii[-1] * 1.04], "brass", segs=8)
    # key pads and pearls along the body's player side (+Y)
    for i in range(6):
        z = top.z - (0.12 + i * 0.05) * s
        b.box((0, top.y + 0.012 + 0.02 * s, z), (0.022 * s, 0.01, 0.018 * s), "pad")
    for i, z in enumerate((-0.14, -0.20, -0.26, -0.34, -0.40, -0.46)):
        b.box((0.012 * s, top.y + 0.035 * s, top.z + z * s), (0.012, 0.006, 0.012), "pearl")
    ob, tris = finish_object(b, name, col)
    grip("grip_l", ob, col, (0.0, top.y + 0.04 * s, top.z - 0.19 * s))
    grip("grip_r", ob, col, (0.0, top.y + 0.04 * s, top.z - 0.39 * s))
    return [ob], tris


# ---------------------------------------------------------------------------
# Trumpet
# ---------------------------------------------------------------------------
def build_trumpet(name, finish, col):
    lib = mats(f"{name}_", finish)
    b = ps2.Builder(lib)
    b.tube([(0, 0, 0), (0, -0.07, 0)], [0.006, 0.008], "brass", segs=6, cap_start="brass")    # mouthpiece
    b.tube([(0, -0.07, 0), (0, -0.20, 0.0)], [0.007, 0.007], "brass", segs=6)                  # leadpipe
    # main tuning slide loop behind the valves
    b.tube([(0, -0.19, -0.045), (0, -0.07, -0.045), (0, -0.05, -0.055), (0, -0.07, -0.068), (0, -0.19, -0.068)],
           [0.0065] * 5, "brass", segs=6)
    # bell section, flaring forward (-Y)
    bell = [(0, -0.25, -0.04), (0, -0.33, -0.04), (0, -0.42, -0.04), (0, -0.47, -0.04), (0, -0.51, -0.04)]
    b.tube(bell, [0.008, 0.010, 0.016, 0.030, 0.060], "brass", segs=8)
    b.tube([bell[-1], (0, -0.516, -0.04)], [0.060, 0.064], "brass", segs=8)
    # valves and finger buttons
    for i in range(3):
        y = -0.20 - i * 0.025
        b.cylinder((0, y, -0.075), (0, y, 0.015), 0.0105, "brass", segs=6)
        b.cylinder((0, y, 0.015), (0, y, 0.035), 0.0035, "brass", segs=4)
        b.cylinder((0, y, 0.035), (0, y, 0.042), 0.008, "pearl", segs=6)
    ob, tris = finish_object(b, name, col)
    grip("grip_l", ob, col, (0.025, -0.225, -0.03))
    grip("grip_r", ob, col, (-0.012, -0.21, 0.025))
    return [ob], tris


# ---------------------------------------------------------------------------
# Trombone (body + separate slide)
# ---------------------------------------------------------------------------
def build_trombone(name, finish, col):
    lib = mats(f"{name}_", finish)
    b = ps2.Builder(lib)
    b.tube([(0, 0.0, 0), (0, -0.06, 0)], [0.007, 0.009], "brass", segs=6, cap_start="brass")   # mouthpiece
    # inner slide tubes (fixed)
    b.tube([(0, -0.06, 0), (0, -0.74, 0)], [0.0065, 0.0065], "brass", segs=6)
    b.tube([(0, -0.02, -0.10), (0, -0.74, -0.10)], [0.0065, 0.0065], "brass", segs=6)
    b.box((0, -0.10, -0.05), (0.012, 0.012, 0.10), "brass")                                   # inner brace
    # bell section: back over the shoulder, then forward to the bell on the player's left
    loop = [(0, -0.02, -0.10), (0, 0.12, -0.10), (0.025, 0.23, -0.07), (0.055, 0.24, -0.01),
            (0.07, 0.13, -0.02), (0.07, -0.10, -0.02), (0.07, -0.30, -0.02), (0.07, -0.47, -0.02),
            (0.07, -0.57, -0.02), (0.07, -0.63, -0.02)]
    b.tube(loop, [0.007, 0.0075, 0.008, 0.008, 0.009, 0.010, 0.013, 0.030, 0.060, 0.105], "brass", segs=8)
    b.box((0.035, -0.12, -0.02), (0.07, 0.012, 0.012), "brass")                               # bell brace
    body, tris = finish_object(b, name, col)

    sl = ps2.Builder(lib)
    # outer slide: two sleeves, the U crook and the hand brace. Closed (first position) at 0.
    sl.tube([(0, -0.20, 0), (0, -0.76, 0)], [0.0085, 0.0085], "brass", segs=6)
    sl.tube([(0, -0.20, -0.10), (0, -0.76, -0.10)], [0.0085, 0.0085], "brass", segs=6)
    sl.tube([(0, -0.76, 0), (0, -0.80, -0.02), (0, -0.81, -0.05), (0, -0.80, -0.08), (0, -0.76, -0.10)],
            [0.0085] * 5, "brass", segs=6)
    sl.box((0, -0.55, -0.05), (0.014, 0.014, 0.10), "brass")
    slide, stris = finish_object(sl, f"{name}_slide", col)
    slide.parent = body
    slide["slide_axis"] = "-Y"
    slide["slide_travel_m"] = SLIDE_TRAVEL
    grip("grip_l", body, col, (0.035, -0.11, -0.04))
    grip("grip_r", slide, col, (0.0, -0.55, -0.05))
    return [body, slide], tris + stris


# ---------------------------------------------------------------------------
# Guitar (archtop) and upright bass
# ---------------------------------------------------------------------------
def outline(length, widths, n=16):
    """CCW outline around the origin: widths = [(x, half_width), ...] along the long axis."""
    xs = [w[0] for w in widths]
    pts = []
    for j in range(n):
        a = 2 * math.pi * j / n
        x = length / 2 * math.cos(a)
        w = np.interp(x, xs, [w[1] for w in widths])
        pts.append((x, w * math.sin(a)))
    return pts


def tex_archtop(n, seed):
    x, y = ps2.grid(n, n)
    r = np.hypot((x - n / 2) / (n / 2), (y - n / 2) / (n / 2))
    arr = np.ones((n, n, 3)) * np.array((0.80, 0.48, 0.16))
    arr *= (1.0 - 0.75 * np.clip(r - 0.35, 0, 1) ** 0.8)[..., None]           # sunburst
    for sx in (-1, 1):                                                          # f-holes
        fx = n / 2 + sx * n * 0.20
        m = (np.abs(x - fx - 3 * np.sin(y / 9)) < 1.5) & (np.abs(y - n / 2) < n * 0.18)
        arr[m] = (0.05, 0.03, 0.02)
    arr[(np.abs(y - n / 2) < n * 0.06) & (x > n * 0.38) & (x < n * 0.62)] = (0.10, 0.07, 0.05)  # bridge/pickguard
    return arr * (1 + np.random.default_rng(seed).uniform(-0.03, 0.03, (n, n)))[..., None]


def tex_fretboard(n, strings=6):
    x, y = ps2.grid(n, n)
    arr = ps2.noise(n, (0.10, 0.06, 0.04), 0.06, 5)
    arr[(x % 12) == 0] = (0.75, 0.73, 0.70)                                     # frets
    for i in range(strings):
        arr[np.abs(y - (n * (i + 0.5) / strings)) < 0.8] = (0.85, 0.83, 0.78)    # strings
    return arr


def build_guitar(name, col):
    lib = ps2.MatLib(TEX_DIR, prefix=f"{name}_")
    lib.add("top", tex_archtop(TEX, 6), uv="fit", rough=0.4)
    lib.add("wood", ps2.wood(TEX, TEX, (0.42, 0.22, 0.09), 7, plank_px=TEX, seams=False), uv=0.4, rough=0.4)
    lib.add("fretboard", tex_fretboard(TEX), uv="fit", rough=0.5)
    lib.add("metal", ps2.brushed(TEX, (0.78, 0.66, 0.36), 8), uv=0.1, rough=0.3, metal=0.6)
    b = ps2.Builder(lib)
    pts = outline(0.52, [(-0.26, 0.12), (-0.16, 0.215), (-0.04, 0.17), (0.02, 0.135), (0.12, 0.165), (0.26, 0.09)], n=20)
    b.prism(pts, -0.035, 0.035, "wood", matrix=Matrix.Rotation(math.radians(90), 4, "X"), top="top")
    b.box((0.47, -0.02, 0), (0.45, 0.025, 0.05), "wood", front="fretboard")      # neck
    b.box((0.765, -0.012, 0), (0.17, 0.02, 0.085), "wood", front="wood")          # headstock
    b.box((-0.17, -0.04, 0), (0.06, 0.012, 0.07), "metal")                        # tailpiece
    b.box((-0.03, -0.045, 0), (0.012, 0.015, 0.08), "wood")                       # bridge
    b.box((0.06, -0.04, 0), (0.25, 0.006, 0.03), "fretboard")                      # strings over the body
    ob, tris = finish_object(b, name, col)
    grip("grip_l", ob, col, (0.56, 0.0, 0.0))
    grip("grip_r", ob, col, (-0.06, -0.07, 0.03))
    return [ob], tris


def build_bass(name, col):
    lib = ps2.MatLib(TEX_DIR, prefix=f"{name}_")
    x, y = ps2.grid(TEX, TEX)
    top = ps2.wood(TEX, TEX, (0.46, 0.22, 0.10), 9, plank_px=64, seams=True)
    for sx in (-1, 1):
        fx = TEX / 2 + sx * TEX * 0.17
        top[(np.abs(x - fx - 3 * np.sin(y / 10)) < 1.5) & (np.abs(y - TEX * 0.46) < TEX * 0.13)] = (0.05, 0.03, 0.02)
    lib.add("top", top, uv="fit", rough=0.45)
    lib.add("wood", ps2.wood(TEX, TEX, (0.38, 0.17, 0.07), 10, plank_px=TEX, seams=False), uv=0.5, rough=0.45)
    lib.add("fretboard", tex_fretboard(TEX, strings=4), uv="fit", rough=0.5)
    lib.add("metal", ps2.brushed(TEX, (0.72, 0.72, 0.74), 11), uv=0.1, rough=0.3, metal=0.6)
    b = ps2.Builder(lib)
    body_z = 0.72                                                               # body centre height
    pts = outline(1.12, [(-0.56, 0.18), (-0.30, 0.34), (-0.02, 0.24), (0.05, 0.22), (0.30, 0.28), (0.56, 0.12)], n=18)
    pts = [(-p[1], p[0]) for p in pts]                                          # long axis vertical
    m = Matrix.Translation((0, 0, body_z)) @ Matrix.Rotation(math.radians(90), 4, "X")
    b.prism([(px, pz) for px, pz in pts], -0.10, 0.10, "wood", matrix=m, top="top")
    b.box((0, -0.12, body_z + 0.78), (0.06, 0.04, 0.90), "wood", front="fretboard")  # neck + fingerboard
    b.box((0, -0.08, body_z + 1.28), (0.05, 0.08, 0.14), "wood")                     # pegbox
    b.cylinder((0, -0.06, body_z + 1.36), (0, -0.10, body_z + 1.36), 0.04, "wood", segs=6)  # scroll
    b.box((0, -0.12, body_z - 0.14), (0.16, 0.02, 0.10), "wood")                     # bridge
    b.cylinder((0, 0, 0.0), (0, 0, body_z - 0.55), 0.008, "metal", segs=4)          # endpin
    ob, tris = finish_object(b, name, col)
    grip("grip_l", ob, col, (0.0, -0.10, body_z + 0.62))
    grip("grip_r", ob, col, (0.02, -0.15, body_z + 0.15))
    return [ob], tris


def build_stand(name, col):
    lib = ps2.MatLib(TEX_DIR, prefix=f"{name}_")
    lib.add("metal", ps2.brushed(TEX, (0.16, 0.16, 0.17), 12), uv=0.5, rough=0.4, metal=0.5)
    x, y = ps2.grid(TEX, TEX)
    music = np.ones((TEX, TEX, 3)) * np.array((0.86, 0.83, 0.74))
    for top in range(TEX - 12, 8, -14):
        for k in range(5):
            music[top - 2 * k, 6:TEX - 6] = (0.30, 0.28, 0.25)
    music[:, TEX // 2 - 1:TEX // 2 + 1] *= 0.7
    lib.add("music", music, uv="fit", rough=0.9)
    lib.add("folder", ps2.noise(TEX, (0.08, 0.07, 0.07), 0.05, 13), uv=0.3, rough=0.6)
    b = ps2.Builder(lib)
    for k in range(3):
        a = math.radians(90 + k * 120)
        b.box((0.13 * math.cos(a), 0.13 * math.sin(a), 0.012), (0.28, 0.02, 0.02), "metal", rot=(0, 0, a))
    b.box((0, 0, 0.51), (0.022, 0.022, 0.92), "metal")
    tilt = math.radians(25)   # desk top leans away from the reader (toward +Y)
    b.box((0, 0, 1.02), (0.52, 0.02, 0.36), "metal", rot=(-tilt, 0, 0))
    b.box((0, -0.09, 0.865), (0.52, 0.05, 0.02), "metal")
    # open black folder resting on the desk, music facing the reader
    for sx in (-1, 1):
        b.box((sx * 0.13, -0.016, 1.03), (0.25, 0.008, 0.32), "folder", rot=(-tilt, 0, math.radians(-sx * 4)))
    ob, tris = finish_object(b, name, col)
    # the music sheet is a separate thin quad so its UV fits exactly, facing the reader (+Y)
    mb = ps2.Builder(lib)
    r = Matrix.Rotation(-tilt, 4, "X")
    c = Vector((0, -0.024, 1.03))
    corners = [c + r @ Vector(v) for v in ((-0.235, 0, -0.145), (0.235, 0, -0.145), (0.235, 0, 0.145), (-0.235, 0, 0.145))]
    mb.quad(corners, "music", r @ Vector((0, -1, 0)), uvs=[(0, 0), (1, 0), (1, 1), (0, 1)])
    sheet, stris = finish_object(mb, f"{name}_music", col)
    sheet.parent = ob
    return [ob, sheet], tris + stris


# ---------------------------------------------------------------------------
INSTRUMENTS = {
    "alto_sax_1": lambda c: build_sax("alto_sax_1", "alto", "gold", c),
    "alto_sax_2": lambda c: build_sax("alto_sax_2", "alto", "gold_dark", c),
    "tenor_sax_1": lambda c: build_sax("tenor_sax_1", "tenor", "gold", c),
    "tenor_sax_2": lambda c: build_sax("tenor_sax_2", "tenor", "gold_pale", c),
    "baritone_sax_1": lambda c: build_sax("baritone_sax_1", "baritone", "gold_dark", c),
    "trumpet_1": lambda c: build_trumpet("trumpet_1", "gold", c),
    "trumpet_2": lambda c: build_trumpet("trumpet_2", "silver", c),
    "trumpet_3": lambda c: build_trumpet("trumpet_3", "gold_pale", c),
    "trumpet_4": lambda c: build_trumpet("trumpet_4", "gold_dark", c),
    "trombone_1": lambda c: build_trombone("trombone_1", "gold", c),
    "trombone_2": lambda c: build_trombone("trombone_2", "gold_pale", c),
    "trombone_3": lambda c: build_trombone("trombone_3", "gold_dark", c),
    "trombone_4": lambda c: build_trombone("trombone_4", "silver", c),
    "guitar_1": lambda c: build_guitar("guitar_1", c),
    "upright_bass_1": lambda c: build_bass("upright_bass_1", c),
    "music_stand_1": lambda c: build_stand("music_stand_1", c),
}


def collect(ob):
    out = [ob]
    for ch in ob.children:
        out.extend(collect(ch))
    return out


def main():
    ps2.reset_scene()
    col = ps2.new_collection("instruments")
    prev = ps2.new_collection("preview_only")
    built = []
    for name, fn in INSTRUMENTS.items():
        objs, tris = fn(col)
        if tris > TRI_TARGET:
            raise RuntimeError(f"{name}: {tris} triangles > {TRI_TARGET}")
        root = objs[0]
        ps2.export_glb(OUT / f"{name}.glb", collect(root))
        built.append((name, root, tris))
        print(f"[build_instruments] {name}: {tris} tris")
    # review layout: one row per family
    rows = {"sax": 0, "trumpet": 1, "trombone": 2}
    counters = {}
    for name, root, _ in built:
        fam = next((f for f in rows if f in name), "other")
        r = rows.get(fam, 3)
        k = counters.get(r, 0)
        counters[r] = k + 1
        root.location = (-2.0 + k * (0.9 if r != 3 else 1.3), r * 0.9, 1.0 if r < 3 else 0.0)
        if fam == "trumpet" or fam == "trombone":
            root.rotation_euler = (0, 0, math.radians(-60))
    ps2.setup_render(1024, 512, samples=16)
    ps2.neutral_lights(prev, center=(0, 1.2, 0.9), power=500)
    cam = ps2.add_camera("preview_cam", prev, (0.6, -3.6, 2.3), (0.4, 1.3, 0.8), lens=26)
    frames = ps2.render_frames([(cam, None)])
    ps2.compose_sheet(frames, OUT / f"instruments_{VERSION}_sheet.png", cols=1)
    bpy.ops.wm.save_as_mainfile(filepath=str(OUT / f"instruments_{VERSION}.blend"))
    print("[build_instruments] done")


main()
