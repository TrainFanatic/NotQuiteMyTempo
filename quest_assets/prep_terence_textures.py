"""Build Terence's PS2-style head texture from the front reference still.

Run with a Python that has Pillow (system python3 here):
    python3 prep_terence_textures.py

Writes terence/textures/terence_head_v002.png (256 x 256; v001 used terence_head.png):
    x 0..179    the face photo, levelled and colour corrected, background filled with skin
    x 180..255  a plain scalp-skin strip used for the back and top of the head
Also writes terence/textures/face_map.json (FACE_MAP + skin tone), which build_terence.py
reads to map the photo onto the head.
"""

import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "refs/terence/user_refs/face_front_film.jpg"
OUT_DIR = ROOT / "terence/textures"

# Photo landmarks after levelling, in source pixels.
FACE_MAP = dict(
    roll_deg=10.4,            # rotate CCW by this to level the eyes
    roll_center=(284, 152),
    crop=(140, 10, 420, 440), # x0, y0, x1, y1 of the head region (down past the collar)
    center_x=278,             # vertical centre line of the skull
    top_y=22,                 # top of the scalp
    metres_per_px=0.00083,    # 82 px eye spacing (~63 mm), head enlarged ~8% for PS2 proportions
    tex_size=256,
    photo_w=180,              # width of the photo area in the texture
)

SKIN_LUMA = 35              # raw pixels brighter than this count as skin
TARGET_SKIN = (198, 142, 112)  # sRGB the forehead is balanced to
BALANCE = 0.55
TINT = 0.65                 # v002: how far the face colour moves to the body beige (0 = photo colour)
EDGE_PX = 25                # width of the band inside the head outline sampled for skin (odd)
FEATHER_PX = 4              # how softly the photo blends into that skin
OUT_NAME = "terence_head_v002.png"   # v001 keeps terence_head.png
CLOSE_PX = 25               # gap size closed in the head silhouette (odd)


def main():
    if not SRC.exists():
        raise FileNotFoundError(SRC)
    m = FACE_MAP
    im = Image.open(SRC).convert("RGB")
    im = im.rotate(m["roll_deg"], resample=Image.BICUBIC, center=m["roll_center"])
    im = im.crop(m["crop"])

    # The still is graded deep orange with crushed blues. White-balance it so the
    # forehead lands on a warm PS2-style skin tone; the room's amber light adds the rest.
    raw = np.asarray(im).astype(np.float32)
    # Skin mask from the raw grade: bright and strongly red-dominant.
    luma = raw @ np.array((0.299, 0.587, 0.114))
    skin = (luma > SKIN_LUMA) & (raw[..., 0] > raw[..., 2] * 1.6)
    if skin.mean() < 0.3:
        raise RuntimeError(f"skin mask too small ({skin.mean():.2f}); check SKIN_LUMA")

    # White balance toward TARGET_SKIN, softened (BALANCE < 1) so the crushed blue
    # channel isn't amplified into noise.
    x0, y0 = m["crop"][0], m["crop"][1]
    forehead = raw[70 - y0:110 - y0, 200 - x0:340 - x0].reshape(-1, 3).mean(axis=0)
    gain = (np.array(TARGET_SKIN) / forehead) ** BALANCE
    arr = raw * gain[None, None, :]
    lw = np.array((0.299, 0.587, 0.114))
    arr *= (np.array(TARGET_SKIN) @ lw) / ((forehead * gain) @ lw)  # match target brightness
    # v002: pull the photo's yellow-orange toward the body's beige. Keep the photo's
    # brightness (all the facial detail lives there) and swap in TINT of the beige colour,
    # which avoids amplifying the noisy blue channel the way a stronger white balance would.
    luma = arr @ lw
    beige = luma[..., None] * (np.array(TARGET_SKIN) / (np.array(TARGET_SKIN) @ lw))
    arr = arr * (1.0 - TINT) + beige * TINT

    mask = Image.fromarray((skin * 255).astype(np.uint8))
    mask = mask.filter(ImageFilter.MinFilter(5)).filter(ImageFilter.MaxFilter(5))
    # Close shadowed gaps (cheek, jaw) into one silhouette, then fill interior holes by
    # flood-filling the background from the image border.
    mask = mask.filter(ImageFilter.MaxFilter(CLOSE_PX)).filter(ImageFilter.MinFilter(CLOSE_PX))
    bg = mask.point(lambda v: 255 if v < 128 else 0).convert("L")
    ImageDraw.floodfill(bg, (0, 0), 128)
    ImageDraw.floodfill(bg, (bg.width - 1, 0), 128)
    mask = bg.point(lambda v: 0 if v == 128 else 255)
    mask.paste(0, (0, 0, mask.width, max(0, m["top_y"] - 2 - m["crop"][1])))
    # v002: the back of the head and the stretched silhouette take their colour from the
    # photo's own skin just inside the head outline, and the photo feathers into it, so the
    # temples and ears don't show a seam against a flat target tone.
    hard = np.asarray(mask) > 127
    inner_img = mask.filter(ImageFilter.MinFilter(EDGE_PX))
    inner = np.asarray(inner_img) > 127
    rows = np.zeros_like(hard)
    rows[max(0, 30 - y0):240 - y0, :] = True
    band = hard & ~inner & rows
    if band.sum() < 200:
        raise RuntimeError(f"edge band too small ({band.sum()} px)")
    scalp = np.median(arr[band], axis=0).astype(np.float32)
    soft_edge = mask.filter(ImageFilter.MinFilter(5)).filter(ImageFilter.GaussianBlur(FEATHER_PX))
    feather = np.asarray(soft_edge).astype(np.float32)[..., None] / 255.0
    out = arr * feather + scalp[None, None, :] * (1.0 - feather)

    size, pw = m["tex_size"], m["photo_w"]
    photo = Image.fromarray(out.clip(0, 255).astype(np.uint8)).resize((pw, size), Image.LANCZOS)

    # Scalp strip for the back of the head, slightly darker toward the nape.
    rng = np.random.default_rng(7)
    sw = size - pw
    strip = np.ones((size, sw, 3), np.float32) * scalp
    shade = np.linspace(1.0, 0.86, size)[:, None, None]
    mottle = np.kron(rng.uniform(-0.05, 0.05, (size // 4, sw // 4 + 1, 1)), np.ones((4, 4, 1)))[:size, :sw]
    strip *= shade * (1.0 + mottle)

    tex = np.zeros((size, size, 3), np.float32)
    tex[:, :pw] = np.asarray(photo)
    tex[:, pw:] = strip
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / OUT_NAME
    Image.fromarray(tex.clip(0, 255).astype(np.uint8)).save(path)
    (OUT_DIR / "face_map.json").write_text(json.dumps(dict(
        FACE_MAP, skin_srgb=[c / 255 for c in TARGET_SKIN],          # v001 body skin
        skin_srgb_v002=[float(c) / 255 for c in scalp]), indent=2))   # v002 body skin = photo side skin
    print(f"wrote {path} (scalp sRGB {scalp.round(1)})")


if __name__ == "__main__":
    main()
