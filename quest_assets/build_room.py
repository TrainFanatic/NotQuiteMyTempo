"""Build the band rehearsal room for "Not Quite My Tempo".

Run:
    /Applications/Blender.app/Contents/MacOS/Blender --background --python build_room.py

Look is matched to the Shaffer studio band room in Whiplash (2014): dark raised-panel wood
walls floor to ceiling with pilasters and warm wall sconces, dark hardwood floor, a coffered
ceiling with recessed glowing panels, black padded chairs with black music stands, a black
grand piano behind the drum kit, portraits and a clock on the back wall, and a tall stool
beside the bandleader. Rendered PS2 style: low poly, 128 px nearest-filtered textures.

Outputs (all in room/):
    room_<VERSION>.glb       room geometry + markers only (no lights, no cameras; room.glb = v003)
    room_<VERSION>.blend     full scene incl. preview lights/cameras (collection "preview_only")
    room_<VERSION>_sheet.png drummer eye view | back-corner overview
    textures/*.png           the procedural base-color textures (also embedded in glb/blend)

Conventions (Blender, Z up; glTF exporter converts to Godot's Y up):
    +Y is the FRONT of the room (bandleader side), -Y is the back.
    -X is the band's LEFT, +X is the band's right (drums, piano, guitar, bass side).
    As in the film, the rhythm section is on the bandleader's left.
    Markers, chairs and stands face along their local +Y axis. After glTF Y-up conversion
    that axis becomes Godot's -Z, so in Godot each marker's forward (-basis.z) points where
    the person should look. Markers sit at floor level (riser top for riser seats).
"""

import math
import shutil
import tempfile
from pathlib import Path

import bmesh
import bpy
import numpy as np
from mathutils import Euler, Matrix, Vector

# ---------------------------------------------------------------------------
# Layout (placeholders, metres)
# ---------------------------------------------------------------------------
# Proportions measured from the film stills, using the players (~1.8 m) as scale:
# ceiling beams sit just under 3 m, the back row and the kit are ~1-1.5 m off the
# back wall, and the bandleader stands ~1.5 m off the front wall.
ROOM_W = 10.0          # x extent
ROOM_D = 7.6           # y extent
ROOM_H = 3.0

BAND_CX = -1.3         # centre of the horn section; rhythm section sits to its right
TERENCE_POS = (BAND_CX, 2.3)

# Horns sit in straight, tight rows facing straight ahead (+Y), like the film.
SEAT_H = 0.46
SEAT_SPACING = 0.75    # chair centre to chair centre within a row
STAND_GAP = 0.55       # chair centre to music stand
ROW_FACING = 0.0       # Z rotation of every horn chair; 0 = straight at the front wall


def row_x(count):
    return [BAND_CX + (i - (count - 1) / 2) * SEAT_SPACING for i in range(count)]


SAX_X = row_x(5)
SAX_ROW_Y = 0.1
BRASS_X = row_x(4)
TROMBONE_Y = -1.2
TRUMPET_Y = -2.5
RISER_HALF_W = 1.6
LOW_RISER = dict(x0=BAND_CX - RISER_HALF_W, x1=BAND_CX + RISER_HALF_W, y0=-1.85, y1=-0.55, h=0.2)
# the high riser runs on past the trumpets so the bassist stands up there with them
HIGH_RISER = dict(x0=BAND_CX - RISER_HALF_W, x1=BAND_CX + RISER_HALF_W + 0.6, y0=-3.15, y1=-1.85, h=0.4)

# Rhythm section on the bandleader's left (+X): kit at the end of the rows,
# grand piano behind it, bass at the end of the trumpet row, guitar in front of the piano.
DRUMMER_POS = (1.9, -1.45)
DRUMMER_EYE_H = 1.2    # seated eye height for the preview camera
DRUM_RUG = (1.9, 2.0)  # rug under the real kit
PIANO_SEAT = (4.2, -3.1)
PIANO_TURN_DEG = -35.0 # pianist faces the keys, turned this far (CCW +) from the bandleader
GUITAR_SEAT = (3.4, 0.6)
BASS_SEAT = (BRASS_X[-1] + SEAT_SPACING, TRUMPET_Y)  # tall stool on the high riser

TERENCE_STOOL = (BAND_CX + 1.0, 2.65)
TABLE_POS = (0.9, 3.3)
SPARE_CHAIRS = [       # (x, y, look_at_x, look_at_y)
    (-3.4, 3.0, BAND_CX, 0.0),  # the one near the front
    (-4.0, -3.4, -4.0, 0.0),
    (-3.4, -3.4, -3.4, 0.0),
    (-4.55, -2.5, 0.0, -2.5),
]

# Wall items. Pilasters sit at fixed spacing, everything else goes between them.
PILASTER_W, PILASTER_D = 0.3, 0.12
PILASTERS = {
    "front": [-3.75, -1.25, 1.25, 3.75],
    "back": [-3.75, -1.25, 1.25, 3.75],
    "left": [-1.27, 1.27],
    "right": [-1.27, 1.27],
}
SCONCES = [(wall, a) for wall, along in PILASTERS.items() for a in along]
SCONCE_Z = 1.95
MAIN_DOOR = ("front", -2.5, 1.0)
OFFICE_DOOR = ("front", 2.5, 0.9)
DOOR_H = 2.1
CLOSET = ("left", 0.0, 2.0)   # percussion closet, built-in
CLOSET_DEPTH = 0.7
CLOSET_H = 2.2
PORTRAITS = [("back", -0.45, "portrait_a"), ("back", 0.45, "portrait_b"),
             ("left", -2.5, "photo_c"), ("left", 2.5, "photo_d"), ("right", 2.5, "photo_e"), ("right", -2.5, "photo_f")]
# v004 rehearsal-room detail
CASES = [  # instrument cases along the right wall: (y, length, width, height, material, lean deg)
    (1.65, 0.78, 0.30, 0.20, "case_black", 0), (2.05, 0.62, 0.26, 0.18, "case_brown", 0),
    (2.45, 0.90, 0.34, 0.22, "case_black", 0), (2.90, 0.55, 0.22, 0.16, "case_black", 0),
    (3.30, 1.05, 0.38, 0.24, "case_brown", 0)]
FOLDER_SHELF = ("left", 2.45, 1.2, 1.1, 0.34)   # wall, along, width, height, depth
DOWNLIGHTS = [(x, y) for x in (-2.5, 0.0, 2.5) for y in (-1.27, 1.27)]
SWITCHES = [("front", -1.75), ("front", 3.25)]
PORTRAIT_Z = 1.75
CLOCK = ("back", -2.5)
CLOCK_Z = 2.3
CLOCK_R = 0.2

# Coffered ceiling: beam lines, each cell gets a recessed glowing panel
COFFER_X = [-5.0, -2.5, 0.0, 2.5, 5.0]
COFFER_Y = [-3.8, -1.27, 1.27, 3.8]
BEAM_W, BEAM_D = 0.26, 0.3
COFFER_PANEL_INSET = 0.45

# Preview lighting (blend + sheet only, never exported)
COFFER_LIGHT_W = 22.0
COFFER_LIGHT_COLOR = (1.0, 0.70, 0.42)
SCONCE_LIGHT_W = 22.0
SCONCE_LIGHT_COLOR = (1.0, 0.58, 0.28)
WORLD_COLOR = (0.011, 0.007, 0.004)

TRI_BUDGET = 10000
TEX = 128

OUT_DIR = Path(__file__).resolve().parent / "room"
TEX_DIR = OUT_DIR / "textures"
VERSION = "v005"
GLB_NAME = f"room_{VERSION}.glb"   # room.glb stays the v003 export (overnight rule: never overwrite)

# ---------------------------------------------------------------------------
# Scene setup
# ---------------------------------------------------------------------------
bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
OUT_DIR.mkdir(exist_ok=True)
if TEX_DIR.exists():
    shutil.rmtree(TEX_DIR)
TEX_DIR.mkdir()

ROOM_COL = bpy.data.collections.new("room")
PREVIEW_COL = bpy.data.collections.new("preview_only")
scene.collection.children.link(ROOM_COL)
scene.collection.children.link(PREVIEW_COL)

# ---------------------------------------------------------------------------
# Procedural textures (sRGB values, row 0 = bottom of image)
# ---------------------------------------------------------------------------
def grid(w, h):
    y, x = np.mgrid[0:h, 0:w].astype(float)
    return x, y


def colorize(base, factor):
    return np.array(base, float)[None, None, :] * factor[..., None]


def tex_wood(w, h, base, seed, plank_px=32, vertical=True, seams=True, joint_px=0):
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
    return colorize(base, f)


def bevel_rect(arr, x0, x1, y0, y1, fill_mul, light=1.35, dark=0.55, edge=2):
    """Raised panel: lighter top/left edge, darker bottom/right edge."""
    arr[y0:y1, x0:x1] *= fill_mul
    arr[y1 - edge:y1, x0:x1] *= light
    arr[y0:y1, x0:x0 + edge] *= light
    arr[y0:y0 + edge, x0:x1] *= dark
    arr[y0:y1, x1 - edge:x1] *= dark


def tex_wall_panel(w, h, seed):
    """One 1.2 m wide x full-height wall bay of raised panels."""
    arr = tex_wood(w, h, (0.25, 0.135, 0.075), seed, plank_px=w, seams=False)
    px_m = h / ROOM_H
    for z0, z1 in ((0.15, 0.85), (1.05, ROOM_H - 0.78), (ROOM_H - 0.64, ROOM_H - 0.42)):
        bevel_rect(arr, 10, w - 10, int(z0 * px_m), int(z1 * px_m), 1.12)
    rail = (int(0.9 * px_m), int(1.0 * px_m))
    arr[rail[0]:rail[1]] *= 0.75
    arr[rail[1] - 1:rail[1]] *= 1.6
    arr[0:int(0.08 * px_m)] *= 0.6
    return arr


def tex_floor_wood(n, seed):
    return tex_wood(n, n, (0.33, 0.19, 0.10), seed, plank_px=16, joint_px=96)


def tex_light(n):
    x, y = grid(n, n)
    arr = np.ones((n, n, 3)) * np.array((0.95, 0.74, 0.48))
    cx, cy = np.abs(x - n / 2) / (n / 2), np.abs(y - n / 2) / (n / 2)
    arr *= (1.0 - 0.25 * np.maximum(cx, cy) ** 2)[..., None]
    border = (x < 5) | (y < 5) | (x >= n - 5) | (y >= n - 5)
    arr[border] = (0.42, 0.30, 0.18)
    return arr


def tex_lamp(n):
    x, y = grid(n, n)
    v = y / n
    arr = np.ones((n, n, 3)) * np.array((1.0, 0.80, 0.52))
    arr *= (0.75 + 0.35 * v)[..., None]
    arr[(y < 6) | (y >= n - 6)] = (0.45, 0.33, 0.16)
    return arr


def tex_noise(n, base, amount, seed, clump=2):
    rng = np.random.default_rng(seed)
    small = rng.uniform(-amount, amount, (n // clump, n // clump))
    f = 1.0 + np.kron(small, np.ones((clump, clump)))
    return colorize(base, f)


def tex_brushed(n, base, seed):
    rng = np.random.default_rng(seed)
    rows = rng.uniform(-0.06, 0.06, (n, 1))
    f = 1.0 + rows + rng.uniform(-0.02, 0.02, (n, n))
    return colorize(base, f)


def tex_keys(n):
    x, y = grid(n, n)
    white_n = 21
    kw = n / white_n
    arr = np.ones((n, n, 3)) * np.array((0.88, 0.85, 0.78))
    arr[(x % kw) < 1.0] = (0.50, 0.48, 0.45)
    for i in range(white_n - 1):
        if i % 7 in (0, 1, 3, 4, 5):
            c = (i + 1) * kw
            arr[(np.abs(x - c) < 0.32 * kw) & (y > n * 0.42)] = (0.04, 0.04, 0.04)
    arr[y < 3] *= 0.8
    return arr


def tex_door(w, h, seed, window):
    arr = tex_wood(w, h, (0.27, 0.15, 0.08), seed, plank_px=w, seams=False)
    bevel_rect(arr, 14, w - 14, 20, 120, 1.1)
    if window == "clear":
        arr[150:228, 30:98] = (0.10, 0.12, 0.13)
        arr[150:228, 62:66] = (0.20, 0.12, 0.06)
    elif window == "frosted":
        arr[140:226, 30:98] = (0.62, 0.55, 0.42)
    bevel_rect(arr, 26, w - 26, 136, 232, 1.0)
    return arr


def tex_louver(w, h, seed):
    arr = tex_wood(w, h, (0.26, 0.15, 0.08), seed, plank_px=w, seams=False)
    x, y = grid(w, h)
    inner = (x >= 12) & (x < w - 12) & (y >= 16) & (y < h - 16)
    arr[inner & ((y % 8) < 2)] *= 0.5
    arr[inner & ((y % 8) == 2)] *= 1.2
    return arr


def tex_clock(n):
    x, y = grid(n, n)
    dx, dy = x + 0.5 - n / 2, y + 0.5 - n / 2
    r = np.hypot(dx, dy)
    ang = np.arctan2(dx, dy)  # 0 at 12 o'clock, clockwise positive
    arr = np.ones((n, n, 3)) * np.array((0.90, 0.87, 0.78))
    for k in range(12):
        a = k * math.pi / 6
        d = np.abs(np.angle(np.exp(1j * (ang - a))))
        width = 0.07 if k % 3 == 0 else 0.035
        arr[(d < width) & (r > n * 0.34) & (r < n * 0.43)] = (0.08, 0.07, 0.06)

    def hand(a, length, half_w):
        d = np.array((math.sin(a), math.cos(a)))
        t = np.clip(dx * d[0] + dy * d[1], -4, length)
        arr[np.hypot(dx - t * d[0], dy - t * d[1]) < half_w] = (0.08, 0.07, 0.06)

    hand((8 + 58 / 60) / 12 * 2 * math.pi, n * 0.24, 2.6)  # 8:58, two minutes to nine
    hand(58 / 60 * 2 * math.pi, n * 0.36, 1.8)
    arr[r > n * 0.46] = (0.10, 0.08, 0.06)
    return arr


def tex_sheet_music(n, seed):
    rng = np.random.default_rng(seed)
    x, y = grid(n, n)
    arr = np.ones((n, n, 3)) * np.array((0.86, 0.83, 0.74))
    for top in range(n - 12, 8, -14):
        for k in range(5):
            arr[top - 2 * k, 6:n - 6] = (0.30, 0.28, 0.25)
        for nx in rng.integers(8, n - 8, 9):
            ny = top - rng.integers(0, 9)
            arr[ny - 1:ny + 1, nx - 1:nx + 2] = (0.10, 0.09, 0.08)
    arr[:, n // 2 - 1:n // 2 + 1] *= 0.7
    return arr


def tex_portrait(w, h, seed, tint):
    rng = np.random.default_rng(seed)
    x, y = grid(w, h)
    arr = np.ones((h, w, 3)) * np.array(tint) * (0.8 + 0.4 * y / h)[..., None]
    arr *= (1.0 + rng.uniform(-0.04, 0.04, (h, w)))[..., None]
    face = ((x - w / 2) / (w * 0.17)) ** 2 + ((y - h * 0.62) / (h * 0.15)) ** 2 < 1
    arr[face] = (0.52, 0.38, 0.27)
    body = (y < h * 0.45) & (np.abs(x - w / 2) < w * 0.12 + (h * 0.45 - y) * 0.45)
    arr[body] = (0.07, 0.06, 0.06)
    arr[(y < h * 0.45) & (y > h * 0.36) & (np.abs(x - w / 2) < w * 0.05)] = (0.75, 0.72, 0.65)
    frame = (x < 12) | (x >= w - 12) | (y < 12) | (y >= h - 12)
    arr[frame] = (0.50, 0.36, 0.14)
    arr[(x == 12) | (x == w - 13) | (y == 12) | (y == h - 13)] = (0.20, 0.13, 0.05)
    return arr


def tex_rug(n, seed):
    rng = np.random.default_rng(seed)
    x, y = grid(n, n)
    arr = tex_noise(n, (0.24, 0.07, 0.05), 0.10, seed)
    edge = np.minimum(np.minimum(x, n - 1 - x), np.minimum(y, n - 1 - y))
    arr[(edge >= 6) & (edge < 12)] = (0.42, 0.27, 0.12)
    diamond = (np.abs(x - n / 2) + np.abs(y - n / 2)) < n * 0.22
    arr[diamond] *= 1.5
    return arr


def tex_amp(n, seed):
    x, y = grid(n, n)
    arr = tex_noise(n, (0.20, 0.17, 0.13), 0.08, seed)
    arr[((x + y) % 4) < 1] *= 0.6
    arr[(x < 8) | (x >= n - 8) | (y < 8) | (y >= n - 24)] = (0.07, 0.065, 0.06)
    arr[n - 18:n - 12, 14:40] = (0.75, 0.72, 0.65)
    return arr


def tex_stack_folders(n, seed):
    """Cubby shelf front: a 4 x 3 grid of pigeonholes stuffed with black and tan folders."""
    rng = np.random.default_rng(seed)
    x, y = grid(n, n)
    arr = np.ones((n, n, 3)) * np.array((0.04, 0.03, 0.025))
    cw, ch = n // 4, n // 3
    for i in range(4):
        for j in range(3):
            x0, y0 = i * cw + 4, j * ch + 4
            k = 0
            while x0 + k < (i + 1) * cw - 4:
                w = int(rng.integers(2, 5))
                c = (0.08, 0.075, 0.07) if rng.random() < 0.6 else (0.55, 0.45, 0.30)
                top = (j + 1) * ch - 4 - int(rng.integers(0, 8))
                arr[y0:top, x0 + k:min(x0 + k + w, (i + 1) * cw - 4)] = np.array(c) * (1 + rng.uniform(-0.1, 0.1))
                k += w + 1
    arr[(x % cw) < 4] = (0.20, 0.11, 0.06)
    arr[(y % ch) < 4] = (0.20, 0.11, 0.06)
    return arr


def make_image(name, arr):
    h, w, _ = arr.shape
    rgba = np.concatenate([np.clip(arr, 0.0, 1.0), np.ones((h, w, 1))], axis=2).astype(np.float32)
    img = bpy.data.images.new(name, w, h, alpha=False)
    img.pixels.foreach_set(rgba.ravel())
    img.filepath_raw = str(TEX_DIR / f"{name}.png")
    img.file_format = "PNG"
    img.save()
    img.pack()
    return img


# ---------------------------------------------------------------------------
# Materials: name -> (texture array, uv mode, roughness, metallic, emission)
# uv mode: a tile size in metres, a (u, v) pair of tile sizes, or "fit" (each face 0..1).
# ---------------------------------------------------------------------------
MAT_SPECS = {
    "wall_panel":  (tex_wall_panel(TEX, 256, 1), (1.2, ROOM_H), 0.55, 0.0, 0.0),
    "pilaster":    (tex_wood(TEX, TEX, (0.21, 0.11, 0.06), 2, plank_px=TEX, seams=False), 1.0, 0.5, 0.0, 0.0),
    "floor_wood":  (tex_floor_wood(TEX, 3), 1.2, 0.3, 0.0, 0.0),
    "ceiling":     (tex_noise(TEX, (0.17, 0.11, 0.07), 0.05, 4), 1.0, 0.9, 0.0, 0.0),
    "light":       (tex_light(TEX), "fit", 0.5, 0.0, 0.42),
    "lamp":        (tex_lamp(TEX), "fit", 0.5, 0.0, 2.0),
    "carpet":      (tex_noise(TEX, (0.14, 0.09, 0.07), 0.10, 5), 1.0, 1.0, 0.0, 0.0),
    "rug":         (tex_rug(TEX, 6), "fit", 1.0, 0.0, 0.0),
    "black_vinyl": (tex_noise(TEX, (0.07, 0.065, 0.06), 0.06, 7), 0.5, 0.45, 0.0, 0.0),
    "dark_metal":  (tex_brushed(TEX, (0.16, 0.16, 0.17), 8), 0.5, 0.4, 0.5, 0.0),
    "piano_black": (tex_noise(TEX, (0.035, 0.03, 0.03), 0.05, 9), 1.0, 0.2, 0.0, 0.0),
    "dark_wood":   (tex_wood(TEX, TEX, (0.15, 0.08, 0.045), 10, plank_px=TEX, vertical=False, seams=False), 1.0, 0.5, 0.0, 0.0),
    "stool_black": (tex_wood(TEX, TEX, (0.06, 0.05, 0.045), 11, plank_px=TEX, seams=False), 0.6, 0.4, 0.0, 0.0),
    "table_wood":  (tex_wood(TEX, TEX, (0.30, 0.18, 0.10), 12, plank_px=TEX, vertical=False, seams=False), 1.0, 0.45, 0.0, 0.0),
    "beige_metal": (tex_brushed(TEX, (0.62, 0.58, 0.50), 13), 0.5, 0.5, 0.1, 0.0),
    "keys":        (tex_keys(TEX), "fit", 0.35, 0.0, 0.0),
    "door_main":   (tex_door(TEX, 256, 14, "clear"), "fit", 0.5, 0.0, 0.0),
    "door_office": (tex_door(TEX, 256, 15, "frosted"), "fit", 0.5, 0.0, 0.0),
    "closet_door": (tex_louver(TEX, 256, 16), "fit", 0.6, 0.0, 0.0),
    "clock_face":  (tex_clock(TEX), "fit", 0.4, 0.0, 0.0),
    "sheet_music": (tex_sheet_music(TEX, 17), "fit", 0.9, 0.0, 0.0),
    "portrait_a":  (tex_portrait(TEX, 160, 18, (0.16, 0.12, 0.09)), "fit", 0.6, 0.0, 0.0),
    "portrait_b":  (tex_portrait(TEX, 160, 19, (0.10, 0.11, 0.10)), "fit", 0.6, 0.0, 0.0),
    "amp_grille":  (tex_amp(TEX, 21), "fit", 0.9, 0.0, 0.0),
    "gilt":        (tex_brushed(TEX, (0.45, 0.32, 0.12), 20), 0.3, 0.4, 0.4, 0.0),
    "photo_c":     (tex_portrait(TEX, 160, 22, (0.20, 0.19, 0.17)), "fit", 0.6, 0.0, 0.0),
    "photo_d":     (tex_portrait(TEX, 160, 23, (0.14, 0.12, 0.10)), "fit", 0.6, 0.0, 0.0),
    "photo_e":     (tex_portrait(TEX, 160, 24, (0.22, 0.20, 0.16)), "fit", 0.6, 0.0, 0.0),
    "photo_f":     (tex_portrait(TEX, 160, 25, (0.12, 0.12, 0.12)), "fit", 0.6, 0.0, 0.0),
    "case_black":  (tex_noise(TEX, (0.05, 0.05, 0.055), 0.12, 26), 0.4, 0.7, 0.0, 0.0),
    "case_brown":  (tex_noise(TEX, (0.28, 0.17, 0.09), 0.10, 27), 0.4, 0.6, 0.0, 0.0),
    "folders":     (tex_stack_folders(TEX, 28), (0.3, 0.3), 0.8, 0.0, 0.0),
}

MATS = {}
UV_MODE = {}
for mname, (arr, uv_mode, rough, metal, emit) in MAT_SPECS.items():
    img = make_image(f"tex_{mname}", arr)
    mat = bpy.data.materials.new(mname)
    nt = mat.node_tree
    bsdf = nt.nodes["Principled BSDF"]
    tex = nt.nodes.new("ShaderNodeTexImage")
    tex.image = img
    tex.interpolation = "Closest"
    tex.location = (-350, 200)
    nt.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
    bsdf.inputs["Roughness"].default_value = rough
    bsdf.inputs["Metallic"].default_value = metal
    if emit > 0.0:
        nt.links.new(tex.outputs["Color"], bsdf.inputs["Emission Color"])
        bsdf.inputs["Emission Strength"].default_value = emit
    MATS[mname] = mat
    UV_MODE[mname] = uv_mode


# ---------------------------------------------------------------------------
# Mesh building
# ---------------------------------------------------------------------------
class Builder:
    def __init__(self):
        self.bm = bmesh.new()
        self.mats = []

    def _mi(self, mname):
        if mname not in MATS:
            raise KeyError(f"unknown material {mname}")
        if mname not in self.mats:
            self.mats.append(mname)
        return self.mats.index(mname)

    def _faces_of(self, verts):
        return {f for v in verts for f in v.link_faces}

    def _assign(self, verts, mat, top=None, bottom=None, front=None):
        for f in self._faces_of(verts):
            f.normal_update()
            m = mat
            if top and f.normal.z > 0.9:
                m = top
            elif bottom and f.normal.z < -0.9:
                m = bottom
            elif front and f.normal.y < -0.5:
                m = front
            f.material_index = self._mi(m)

    def box(self, center, size, mat, top=None, bottom=None, front=None, rot=(0.0, 0.0, 0.0)):
        """Axis box, optionally rotated (radians XYZ) about its centre.
        front = material for faces pointing toward local -Y."""
        r = Euler(rot).to_matrix()
        c = Vector(center)
        verts = bmesh.ops.create_cube(self.bm, size=1.0)["verts"]
        for v in verts:
            v.co = c + r @ Vector((v.co.x * size[0], v.co.y * size[1], v.co.z * size[2]))
        self._assign(verts, mat, top, bottom, front)

    def prism(self, pts, z0, z1, mat, matrix=Matrix()):
        """Extrude a CCW 2D outline from z0 to z1."""
        bot = [self.bm.verts.new(matrix @ Vector((x, y, z0))) for x, y in pts]
        top = [self.bm.verts.new(matrix @ Vector((x, y, z1))) for x, y in pts]
        self.bm.faces.new(top)
        self.bm.faces.new(list(reversed(bot)))
        n = len(pts)
        for i in range(n):
            j = (i + 1) % n
            self.bm.faces.new((bot[i], bot[j], top[j], top[i]))
        self._assign(bot + top, mat)

    def quad(self, pts, mat, inward):
        vs = [self.bm.verts.new(p) for p in pts]
        f = self.bm.faces.new(vs)
        f.normal_update()
        if f.normal.dot(Vector(inward)) < 0:
            f.normal_flip()
        f.material_index = self._mi(mat)

    def disc_facing_neg_y(self, center, radius, depth, segments, face_mat, rim_mat):
        m = Matrix.Translation(center) @ Matrix.Rotation(math.radians(90), 4, "X")
        verts = bmesh.ops.create_cone(self.bm, cap_ends=True, cap_tris=False, segments=segments,
                                      radius1=radius, radius2=radius, depth=depth, matrix=m)["verts"]
        for f in self._faces_of(verts):
            f.normal_update()
            f.material_index = self._mi(face_mat if f.normal.y < -0.9 else rim_mat)

    def build(self, name, loc=(0.0, 0.0, 0.0), rot_z=0.0):
        bm = self.bm
        bm.normal_update()
        uv = bm.loops.layers.uv.new("UVMap")
        up = Vector((0, 0, 1))
        for f in bm.faces:
            mode = UV_MODE[self.mats[f.material_index]]
            n = f.normal
            if abs(n.z) > 0.7:
                ua, va = Vector((1, 0, 0)), Vector((0, 1, 0))
            else:
                fwd = Vector((-n.x, -n.y, 0.0)).normalized()
                ua, va = fwd.cross(up), up
            pts = [(l.vert.co.dot(ua), l.vert.co.dot(va)) for l in f.loops]
            if mode == "fit":
                us, vs = [p[0] for p in pts], [p[1] for p in pts]
                u0, u1, v0, v1 = min(us), max(us), min(vs), max(vs)
                for l, (u, v) in zip(f.loops, pts):
                    l[uv].uv = ((u - u0) / (u1 - u0), (v - v0) / (v1 - v0))
            else:
                tu, tv = mode if isinstance(mode, tuple) else (mode, mode)
                for l, (u, v) in zip(f.loops, pts):
                    l[uv].uv = (u / tu, v / tv)
        me = bpy.data.meshes.new(name)
        bm.to_mesh(me)
        bm.free()
        for m in self.mats:
            me.materials.append(MATS[m])
        ob = bpy.data.objects.new(name, me)
        ob.location = loc
        ob.rotation_euler = (0.0, 0.0, rot_z)
        ROOM_COL.objects.link(ob)
        return ob


def facing(frm, to):
    """Z rotation that points local +Y from frm toward to."""
    return math.atan2(-(to[0] - frm[0]), to[1] - frm[1])


def forward(rot_z):
    return (-math.sin(rot_z), math.cos(rot_z))


def offset(xy, rot_z, dist):
    f = forward(rot_z)
    return (xy[0] + f[0] * dist, xy[1] + f[1] * dist)


def wall_xform(wall, along):
    """Origin on the wall surface; local -Y points into the room."""
    hw, hd = ROOM_W / 2, ROOM_D / 2
    if wall == "front":
        return (along, hd), 0.0
    if wall == "back":
        return (along, -hd), math.pi
    if wall == "right":
        return (hw, along), -math.pi / 2
    if wall == "left":
        return (-hw, along), math.pi / 2
    raise ValueError(f"unknown wall {wall}")


def marker(name, xy, z, rot_z):
    ob = bpy.data.objects.new(name, None)
    ob.empty_display_type = "ARROWS"
    ob.empty_display_size = 0.35
    ob.location = (xy[0], xy[1], z)
    ob.rotation_euler = (0.0, 0.0, rot_z)
    ROOM_COL.objects.link(ob)
    return ob


# ---------------------------------------------------------------------------
# Room shell: floor, ceiling, raised-panel walls, baseboard, crown
# ---------------------------------------------------------------------------
hw, hd, H = ROOM_W / 2, ROOM_D / 2, ROOM_H
shell = Builder()
shell.quad([(-hw, -hd, 0), (hw, -hd, 0), (hw, hd, 0), (-hw, hd, 0)], "floor_wood", (0, 0, 1))
shell.quad([(-hw, -hd, H), (hw, -hd, H), (hw, hd, H), (-hw, hd, H)], "ceiling", (0, 0, -1))
walls = [  # (corner a, corner b, inward normal)
    ((-hw, hd), (hw, hd), (0, -1, 0)),
    ((hw, -hd), (-hw, -hd), (0, 1, 0)),
    ((hw, hd), (hw, -hd), (-1, 0, 0)),
    ((-hw, -hd), (-hw, hd), (1, 0, 0)),
]
for (ax, ay), (bx, by), n in walls:
    shell.quad([(ax, ay, 0), (bx, by, 0), (bx, by, H), (ax, ay, H)], "wall_panel", n)
    cx, cy = (ax + bx) / 2, (ay + by) / 2
    length = abs(bx - ax) + abs(by - ay)
    for z, height, depth in ((0.07, 0.14, 0.02), (H - BEAM_D - 0.05, 0.1, 0.05)):
        off = Vector(n) * depth / 2
        size = (length, depth, height) if n[0] == 0 else (depth, length, height)
        shell.box((cx + off.x, cy + off.y, z), size, "dark_wood")
shell.build("room_shell")

pil = Builder()
for wall, positions in PILASTERS.items():
    for along in positions:
        (x, y), rot = wall_xform(wall, along)
        f = forward(rot)  # local +Y; into-room is -f
        c = (x - f[0] * PILASTER_D / 2, y - f[1] * PILASTER_D / 2, H / 2)
        size = (PILASTER_W, PILASTER_D, H) if wall in ("front", "back") else (PILASTER_D, PILASTER_W, H)
        pil.box(c, size, "pilaster")
pil.build("pilasters")

ceil = Builder()
for x in COFFER_X:
    ceil.box((x, 0.0, H - BEAM_D / 2), (BEAM_W, ROOM_D, BEAM_D), "dark_wood")
for y in COFFER_Y:
    ceil.box((0.0, y, H - BEAM_D / 2), (ROOM_W, BEAM_W, BEAM_D), "dark_wood")
ceil.build("ceiling_beams")

coffer_cells = []
lights = Builder()
for x0, x1 in zip(COFFER_X, COFFER_X[1:]):
    for y0, y1 in zip(COFFER_Y, COFFER_Y[1:]):
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        sx, sy = x1 - x0 - 2 * COFFER_PANEL_INSET, y1 - y0 - 2 * COFFER_PANEL_INSET
        lights.box((cx, cy, H - 0.02), (sx, sy, 0.04), "light")
        coffer_cells.append((cx, cy, sx, sy))
lights.build("ceiling_lights")

# ---------------------------------------------------------------------------
# Risers and the drum rug
# ---------------------------------------------------------------------------
risers = Builder()
for r in (LOW_RISER, HIGH_RISER):
    risers.box(((r["x0"] + r["x1"]) / 2, (r["y0"] + r["y1"]) / 2, r["h"] / 2),
               (r["x1"] - r["x0"], r["y1"] - r["y0"], r["h"]), "dark_wood", top="carpet")
risers.box((DRUMMER_POS[0], DRUMMER_POS[1] + 0.3, 0.005), (DRUM_RUG[0], DRUM_RUG[1], 0.01), "rug")
risers.build("risers")

# ---------------------------------------------------------------------------
# Furniture
# ---------------------------------------------------------------------------
def build_chair(name, xy, z, rot_z):
    """Black padded band chair, faces local +Y."""
    b = Builder()
    b.box((0, 0, SEAT_H - 0.03), (0.46, 0.44, 0.07), "black_vinyl")
    b.box((0, -0.21, 0.76), (0.44, 0.06, 0.32), "black_vinyl", rot=(math.radians(8), 0, 0))
    for sx in (-0.2, 0.2):
        for sy in (-0.18, 0.18):
            b.box((sx, sy, (SEAT_H - 0.06) / 2), (0.025, 0.025, SEAT_H - 0.06), "dark_metal")
        b.box((sx, -0.2, 0.55), (0.025, 0.025, 0.24), "dark_metal")
    return b.build(name, (xy[0], xy[1], z), rot_z)


def build_stand(name, xy, z, rot_z, desk_h=1.02):
    """Black music stand; the player stands/sits on its local -Y side."""
    b = Builder()
    for k in range(3):
        a = math.radians(90 + k * 120)
        b.box((0.13 * math.cos(a), 0.13 * math.sin(a), 0.012), (0.28, 0.02, 0.02), "dark_metal",
              rot=(0, 0, a))
    b.box((0, 0, desk_h / 2), (0.022, 0.022, desk_h - 0.1), "dark_metal")
    tilt = math.radians(-25)
    b.box((0, 0, desk_h), (0.52, 0.02, 0.36), "dark_metal", front="sheet_music", rot=(tilt, 0, 0))
    b.box((0, -0.09, desk_h - 0.155), (0.52, 0.05, 0.02), "dark_metal")
    return b.build(name, (xy[0], xy[1], z), rot_z)


def build_stool(name, xy, rot_z, seat_h=0.76, z=0.0):
    b = Builder()
    b.box((0, 0, seat_h - 0.02), (0.36, 0.36, 0.04), "stool_black")
    for sx in (-0.14, 0.14):
        for sy in (-0.14, 0.14):
            b.box((sx, sy, (seat_h - 0.04) / 2), (0.035, 0.035, seat_h - 0.04), "stool_black")
    b.box((0, -0.14, 0.26), (0.28, 0.025, 0.025), "stool_black")
    b.box((0, 0.14, 0.26), (0.28, 0.025, 0.025), "stool_black")
    return b.build(name, (xy[0], xy[1], z), rot_z)


def build_bench(name, xy, rot_z):
    b = Builder()
    b.box((0, 0, 0.48), (0.80, 0.34, 0.06), "black_vinyl", bottom="piano_black")
    for sx in (-0.36, 0.36):
        for sy in (-0.13, 0.13):
            b.box((sx, sy, 0.225), (0.05, 0.05, 0.45), "piano_black")
    return b.build(name, (xy[0], xy[1], 0.0), rot_z)


GRAND_OUTLINE = [(-0.75, 0.0), (0.75, 0.0), (0.75, 0.35), (0.55, 0.75), (0.25, 1.1),
                 (0.15, 1.45), (0.0, 1.75), (-0.3, 1.85), (-0.6, 1.8), (-0.75, 1.6)]


def build_grand_piano(name, xy, rot_z):
    """Black grand piano, keyboard at local y=0 facing -Y, lid propped on the treble side."""
    b = Builder()
    b.prism(GRAND_OUTLINE, 0.62, 0.95, "piano_black")
    hinge = Matrix.Translation((-0.75, 0, 0.95))
    lid = hinge @ Matrix.Rotation(math.radians(-28), 4, "Y") @ hinge.inverted()
    b.prism(GRAND_OUTLINE, 0.95, 0.97, "piano_black", matrix=lid)
    b.box((0.55, 0.75, 1.2), (0.02, 0.02, 0.55), "piano_black", rot=(0, math.radians(-12), 0))
    b.box((0, -0.12, 0.70), (1.42, 0.24, 0.06), "piano_black", top="keys")
    for sx in (-0.73, 0.73):
        b.box((sx, -0.12, 0.74), (0.05, 0.25, 0.14), "piano_black")
    b.box((0, 0.12, 1.08), (0.9, 0.02, 0.24), "piano_black", rot=(math.radians(-15), 0, 0))
    for lx, ly in ((-0.62, 0.12), (0.62, 0.12), (-0.35, 1.55)):
        b.box((lx, ly, 0.31), (0.08, 0.08, 0.62), "piano_black")
    return b.build(name, (xy[0], xy[1], 0.0), rot_z)


def build_table(name, xy):
    """Bandleader's cart/table with a tape deck box on it."""
    b = Builder()
    b.box((0, 0, 0.82), (1.2, 0.6, 0.04), "table_wood")
    b.box((0, 0, 0.18), (1.1, 0.5, 0.03), "table_wood")
    for sx in (-0.56, 0.56):
        for sy in (-0.26, 0.26):
            b.box((sx, sy, 0.41), (0.035, 0.035, 0.8), "dark_metal")
    b.box((-0.2, 0.0, 0.92), (0.46, 0.32, 0.16), "beige_metal")
    b.box((0.35, 0.05, 0.846), (0.3, 0.24, 0.012), "black_vinyl")
    return b.build(name, (xy[0], xy[1], 0.0), 0.0)


def build_door(name, spec, mat):
    wall, along, w = spec
    xy, rot = wall_xform(wall, along)
    b = Builder()
    for sx in (-1, 1):
        b.box((sx * (w / 2 + 0.05), -0.03, (DOOR_H + 0.05) / 2), (0.1, 0.06, DOOR_H + 0.05), "dark_wood")
    b.box((0, -0.03, DOOR_H + 0.05), (w + 0.2, 0.06, 0.1), "dark_wood")
    b.box((0, -0.012, DOOR_H / 2), (w, 0.024, DOOR_H), mat)
    b.box((w / 2 - 0.09, -0.05, 1.0), (0.12, 0.04, 0.03), "gilt")
    return b.build(name, (xy[0], xy[1], 0.0), rot)


def build_closet(name):
    wall, along, w = CLOSET
    xy, rot = wall_xform(wall, along)
    d = CLOSET_DEPTH
    b = Builder()
    b.box((0, -d / 2, CLOSET_H / 2), (w, d, CLOSET_H), "pilaster", top="dark_wood")
    door_w = w / 2 - 0.08
    for sx in (-1, 1):
        b.box((sx * (door_w / 2 + 0.03), -d - 0.012, 1.03), (door_w, 0.024, 2.0), "closet_door")
        b.box((sx * 0.09, -d - 0.04, 1.05), (0.03, 0.03, 0.18), "gilt")
    b.box((0, -d - 0.006, 2.16), (0.36, 0.012, 0.09), "gilt")  # label plate
    return b.build(name, (xy[0], xy[1], 0.0), rot)


def build_clock(name):
    wall, along = CLOCK
    xy, rot = wall_xform(wall, along)
    b = Builder()
    b.disc_facing_neg_y((0, -0.035, 0), CLOCK_R, 0.07, 16, "clock_face", "dark_wood")
    return b.build(name, (xy[0], xy[1], CLOCK_Z), rot)


def build_portrait(name, wall, along, mat):
    xy, rot = wall_xform(wall, along)
    b = Builder()
    b.box((0, -0.02, 0), (0.56, 0.04, 0.70), "gilt", front=mat)
    return b.build(name, (xy[0], xy[1], PORTRAIT_Z), rot)


def build_case(name, y, length, width, height, mat, lean):
    b = Builder()
    b.box((0, 0, height / 2), (width, length, height), mat)
    b.box((0, 0, height + 0.012), (0.03, 0.12, 0.024), "gilt")          # handle
    for sy in (-1, 1):
        b.box((width / 2 + 0.002, sy * length * 0.3, height * 0.7), (0.006, 0.03, 0.03), "gilt")  # latches
    return b.build(name, (ROOM_W / 2 - PILASTER_D - width / 2 - 0.05, y, 0.0), math.radians(lean))


def build_folder_shelf(name):
    wall, along, w, h, d = FOLDER_SHELF
    xy, rot = wall_xform(wall, along)
    b = Builder()
    b.box((0, -d / 2, h / 2), (w, d, h), "pilaster", front="folders", top="dark_wood")
    return b.build(name, (xy[0], xy[1], 0.0), rot)


def build_switch(name, wall, along):
    xy, rot = wall_xform(wall, along)
    b = Builder()
    b.box((0, -0.005, 0), (0.08, 0.01, 0.12), "beige_metal")
    return b.build(name, (xy[0], xy[1], 1.20), rot)


def build_sconce(name, wall, along):
    xy, rot = wall_xform(wall, along)
    b = Builder()
    y0 = -PILASTER_D
    b.box((0, y0 - 0.01, 0), (0.1, 0.02, 0.2), "gilt")
    b.box((0, y0 - 0.06, -0.02), (0.025, 0.09, 0.025), "gilt")
    b.box((0, y0 - 0.13, 0.04), (0.15, 0.13, 0.17), "lamp")
    return b.build(name, (xy[0], xy[1], SCONCE_Z), rot)


# Band seats: (instrument, n, (x, y), floor z)
seats = []
for i, x in enumerate(SAX_X, 1):
    seats.append(("sax", i, (x, SAX_ROW_Y), 0.0))
for i, x in enumerate(BRASS_X, 1):
    seats.append(("trombone", i, (x, TROMBONE_Y), LOW_RISER["h"]))
for i, x in enumerate(BRASS_X, 1):
    seats.append(("trumpet", i, (x, TRUMPET_Y), HIGH_RISER["h"]))

for inst, n, xy, z in seats + [("guitar", 1, GUITAR_SEAT, 0.0)]:
    rot = ROW_FACING if inst != "guitar" else facing(xy, TERENCE_POS)
    build_chair(f"chair_{inst}_{n}", xy, z, rot)
    build_stand(f"stand_{inst}_{n}", offset(xy, rot, STAND_GAP), z, rot)
    marker(f"seat_{inst}_{n}", xy, z, rot)

guitar_rot = facing(GUITAR_SEAT, TERENCE_POS)
amp = Builder()
amp.box((0, 0, 0.25), (0.5, 0.26, 0.5), "black_vinyl", front="amp_grille")
amp.build("guitar_amp", (*offset((GUITAR_SEAT[0] + 0.7, GUITAR_SEAT[1]), guitar_rot, 0.1), 0.0), guitar_rot + math.pi)

bass_rot = facing(BASS_SEAT, TERENCE_POS)
bass_z = HIGH_RISER["h"]
# the bassist stands (as in the film); the stool sits behind him so it no longer goes through his legs
BASS_STOOL_BACK = 0.35
build_stool("stool_bass_1", offset(BASS_SEAT, bass_rot, -BASS_STOOL_BACK), bass_rot, z=bass_z)
build_stand("stand_bass_1", offset(BASS_SEAT, bass_rot, STAND_GAP), bass_z, bass_rot, desk_h=1.2)
marker("seat_bass_1", BASS_SEAT, bass_z, bass_rot)

# Piano: pianist faces the keys, turned PIANO_TURN_DEG left of the bandleader
piano_rot = facing(PIANO_SEAT, TERENCE_POS) + math.radians(PIANO_TURN_DEG)
build_grand_piano("piano_grand", offset(PIANO_SEAT, piano_rot, 0.62), piano_rot)
build_bench("piano_bench", PIANO_SEAT, piano_rot)
marker("seat_piano_1", PIANO_SEAT, 0.0, piano_rot)

for i, (x, y, lx, ly) in enumerate(SPARE_CHAIRS, 1):
    build_chair(f"chair_spare_{i}", (x, y), 0.0, facing((x, y), (lx, ly)))

terence_rot = facing(TERENCE_POS, (BAND_CX, 0.0))
build_stand("stand_terence", offset(TERENCE_POS, terence_rot, 0.5), 0.0, terence_rot, desk_h=1.15)
build_stool("stool_terence", TERENCE_STOOL, terence_rot)
build_table("bandleader_table", TABLE_POS)
build_door("door_main", MAIN_DOOR, "door_main")
build_door("door_office", OFFICE_DOOR, "door_office")
build_closet("percussion_closet")
build_clock("wall_clock")
for i, (wall, along, mat) in enumerate(PORTRAITS, 1):
    build_portrait(f"portrait_{i}", wall, along, mat)
for i, (wall, along) in enumerate(SCONCES, 1):
    build_sconce(f"sconce_{i}", wall, along)
for i, (y, length, width, height, mat, lean) in enumerate(CASES, 1):
    build_case(f"instrument_case_{i}", y, length, width, height, mat, lean)
build_folder_shelf("folder_shelf")
for i, (x, y) in enumerate(DOWNLIGHTS, 1):
    dl = Builder()
    dl.disc_facing_neg_y((0, 0, 0), 0.065, 0.02, 8, "lamp", "gilt")
    ob = dl.build(f"downlight_{i}", (x, y, ROOM_H - BEAM_D - 0.012), 0.0)
    ob.rotation_euler = (math.radians(90), 0, 0)    # disc face (local -Y) points down
for i, (wall, along) in enumerate(SWITCHES, 1):
    build_switch(f"light_switch_{i}", wall, along)

marker("spot_terence", TERENCE_POS, 0.0, terence_rot)
marker("spot_drummer", DRUMMER_POS, 0.0, facing(DRUMMER_POS, TERENCE_POS))

# ---------------------------------------------------------------------------
# Triangle budget
# ---------------------------------------------------------------------------
tri_total = 0
for ob in ROOM_COL.objects:
    if ob.type == "MESH":
        tri_total += sum(len(p.vertices) - 2 for p in ob.data.polygons)
print(f"[build_room] triangles: {tri_total}")
if tri_total >= TRI_BUDGET:
    raise RuntimeError(f"triangle budget exceeded: {tri_total} >= {TRI_BUDGET}")

# ---------------------------------------------------------------------------
# Export room.glb (room collection only)
# ---------------------------------------------------------------------------
bpy.ops.object.select_all(action="DESELECT")
for ob in ROOM_COL.objects:
    ob.select_set(True)
bpy.ops.export_scene.gltf(
    filepath=str(OUT_DIR / GLB_NAME),
    export_format="GLB",
    use_selection=True,
    export_cameras=False,
    export_lights=False,
    export_yup=True,
    export_apply=True,
)
bpy.ops.object.select_all(action="DESELECT")

# ---------------------------------------------------------------------------
# Preview lights, cameras, contact sheet
# ---------------------------------------------------------------------------
for i, (cx, cy, sx, sy) in enumerate(coffer_cells, 1):
    ld = bpy.data.lights.new(f"preview_coffer_{i}", "AREA")
    ld.shape = "RECTANGLE"
    ld.size, ld.size_y = sx, sy
    ld.energy = COFFER_LIGHT_W
    ld.color = COFFER_LIGHT_COLOR
    lo = bpy.data.objects.new(ld.name, ld)
    lo.location = (cx, cy, H - 0.05)
    PREVIEW_COL.objects.link(lo)

for i, (wall, along) in enumerate(SCONCES, 1):
    (x, y), rot = wall_xform(wall, along)
    f = forward(rot)
    ld = bpy.data.lights.new(f"preview_sconce_{i}", "POINT")
    ld.energy = SCONCE_LIGHT_W
    ld.color = SCONCE_LIGHT_COLOR
    ld.shadow_soft_size = 0.08
    lo = bpy.data.objects.new(ld.name, ld)
    lo.location = (x - f[0] * (PILASTER_D + 0.3), y - f[1] * (PILASTER_D + 0.3), SCONCE_Z + 0.05)
    PREVIEW_COL.objects.link(lo)

world = bpy.data.worlds.new("preview_world")
world.node_tree.nodes["Background"].inputs["Color"].default_value = (*WORLD_COLOR, 1.0)
scene.world = world

scene.render.engine = "BLENDER_EEVEE"
scene.eevee.taa_render_samples = 32
scene.eevee.use_raytracing = True
scene.eevee.shadow_pool_size = "1024"
scene.view_settings.view_transform = "Standard"
scene.render.resolution_x = 960
scene.render.resolution_y = 720
scene.render.resolution_percentage = 100
scene.render.image_settings.file_format = "PNG"


def add_camera(name, loc, target, lens):
    cd = bpy.data.cameras.new(name)
    cd.lens = lens
    cd.clip_start = 0.05
    co = bpy.data.objects.new(name, cd)
    co.location = loc
    co.rotation_euler = (Vector(target) - Vector(loc)).to_track_quat("-Z", "Y").to_euler()
    PREVIEW_COL.objects.link(co)
    return co


cams = [
    add_camera("preview_cam_drummer", (DRUMMER_POS[0], DRUMMER_POS[1], DRUMMER_EYE_H),
               (TERENCE_POS[0], TERENCE_POS[1], 1.3), 18.0),
    add_camera("preview_cam_overview", (-hw + 0.5, -hd + 0.5, 2.3), (1.5, 1.2, 0.2), 17.0),
]

tmp = Path(tempfile.mkdtemp(prefix="room_preview_"))
frames = []
for cam in cams:
    scene.camera = cam
    path = tmp / f"{cam.name}.png"
    scene.render.filepath = str(path)
    bpy.ops.render.render(write_still=True)
    img = bpy.data.images.load(str(path))
    w, h = img.size
    px = np.empty(w * h * 4, np.float32)
    img.pixels.foreach_get(px)
    frames.append(px.reshape(h, w, 4))
    bpy.data.images.remove(img)
shutil.rmtree(tmp)

gap = np.ones((frames[0].shape[0], 8, 4), np.float32) * np.array((0.05, 0.05, 0.05, 1.0), np.float32)
sheet_px = np.concatenate([frames[0], gap, frames[1]], axis=1)
sh, sw, _ = sheet_px.shape
sheet = bpy.data.images.new("room_sheet", sw, sh, alpha=False)
sheet.pixels.foreach_set(sheet_px.ravel())
sheet.filepath_raw = str(OUT_DIR / f"room_{VERSION}_sheet.png")
sheet.file_format = "PNG"
sheet.save()
bpy.data.images.remove(sheet)

scene.camera = cams[0]
bpy.ops.wm.save_as_mainfile(filepath=str(OUT_DIR / f"room_{VERSION}.blend"))
print(f"[build_room] done: {OUT_DIR}")
