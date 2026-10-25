"""Assemble review_scene.blend (task A4 of OVERNIGHT.md).

Run (after the room, Terence, band, instruments and props are built):
    /Applications/Blender.app/Contents/MacOS/Blender --background --python build_review_scene.py

Imports every exported .glb (so this doubles as an import check), places Terence on
spot_terence and each band member at their seat_ marker, seated in a preview pose with
their instrument at rest, and scatters the props around the bandleader. Poses here are
preview only; nothing is exported. Writes review_scene.blend and review_scene_sheet.png.

Placement rule for Godot: characters face -Y in Blender (+Z in glTF), the room's markers
face +Y (their -Z in Godot), so a character's yaw = marker yaw + 180 degrees.
"""

import math
import sys
from pathlib import Path

import bpy
from mathutils import Matrix, Vector

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib_ps2 as ps2  # noqa: E402

R = ps2.ROOT
ROOM_BLEND = R / "room/room_v003.blend"

from band_anims import SEATING  # noqa: E402  (seat marker -> member, instrument)

# Instrument at rest, in the player's local frame (faces -Y): (location, rotation XYZ degrees)
REST = {
    "alto_sax": ((-0.12, -0.30, 1.00), (0, 0, 10)),
    "tenor_sax": ((-0.12, -0.32, 1.08), (0, 0, 10)),
    "baritone_sax": ((-0.30, -0.30, 1.22), (0, 0, 15)),
    "trumpet": ((-0.14, -0.30, 0.60), (-35, 0, 0)),
    "trombone": ((-0.36, -0.10, 1.00), (90, 0, 0)),
    "guitar": ((-0.05, -0.28, 0.70), (0, -12, 0)),
    "upright_bass": ((0.18, -0.38, 0.0), (-14, 0, 0)),
}


def import_glb(path, col):
    if not path.exists():
        raise FileNotFoundError(path)
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=str(path))
    new = [o for o in bpy.data.objects if o not in before]
    if not new:
        raise RuntimeError(f"nothing imported from {path}")
    for o in new:
        for c in list(o.users_collection):
            c.objects.unlink(o)
        col.objects.link(o)
    # the glTF importer adds Icosphere bone-display shapes; keep them out of renders
    for o in new:
        if o.name.startswith("Icosphere"):
            o.hide_render = True
            o.hide_set(True)
    new = [o for o in new if not o.name.startswith("Icosphere")]
    roots = [o for o in new if o.parent is None]
    return new, roots


def place(root, loc, yaw):
    root.location = loc
    root.rotation_euler = (0, 0, yaw)


def main():
    sc = ps2.reset_scene()
    room_col = ps2.new_collection("room")
    ps2.append_room(ROOM_BLEND, room_col)
    chars = ps2.new_collection("characters")
    inst_col = ps2.new_collection("instruments")
    prop_col = ps2.new_collection("props")
    prev = ps2.new_collection("preview_only")
    obj = bpy.data.objects

    # Terence
    _, roots = import_glb(R / "terence/terence_v002.glb", chars)
    spot = obj["spot_terence"]
    terence = next(o for o in roots if o.type == "ARMATURE")
    place(terence, spot.location, spot.rotation_euler.z + math.pi)
    bpy.context.view_layer.update()
    ps2.apply_pose(terence, [("aim", "LeftUpperArm", (0.18, 0.02, -1)), ("aim", "LeftLowerArm", (0.12, -0.15, -1)),
                             ("aim", "RightUpperArm", (-0.18, 0.02, -1)), ("aim", "RightLowerArm", (-0.12, -0.15, -1))])

    for marker, (member, inst) in SEATING.items():
        m = obj[marker]
        _, roots = import_glb(R / "band" / f"{member}.glb", chars)
        rig = next(o for o in roots if o.type == "ARMATURE")
        place(rig, m.location, m.rotation_euler.z + math.pi)
        bpy.context.view_layer.update()
        if member == "band_bass_1":
            ps2.apply_pose(rig, [("aim", "LeftUpperArm", (0.25, -0.35, -0.8)), ("aim", "LeftLowerArm", (-0.1, -0.5, 0.85)),
                                 ("aim", "RightUpperArm", (-0.15, -0.3, -1)), ("aim", "RightLowerArm", (0.35, -0.8, -0.2))])
        else:
            ps2.apply_pose(rig, ps2.seated_ops(rig))
        if inst:
            _, iroots = import_glb(R / "instruments" / f"{inst}.glb", inst_col)
            iroot = next(o for o in iroots if o.type == "MESH")
            kind = inst.rsplit("_", 1)[0]
            loc, rot = REST[kind]
            local = Matrix.Translation(loc) @ Matrix.Rotation(math.radians(rot[2]), 4, "Z") @ \
                Matrix.Rotation(math.radians(rot[1]), 4, "Y") @ Matrix.Rotation(math.radians(rot[0]), 4, "X")
            iroot.matrix_world = rig.matrix_world @ local

    # props around the bandleader
    table = obj["bandleader_table"]
    tz = 0.86
    spots = {
        "cowbell": (table.location + Vector((0.3, 0.0, tz + 0.04)), 20),
        "chair": (table.location + Vector((-1.0, -0.2, 0)), 200),
        "spare_drum": (obj["spot_drummer"].location + Vector((1.25, 0.6, 0)), 0),
        "cymbal": (obj["spot_drummer"].location + Vector((1.25, 0.6, 0.15)), 0),
        "sheet_music_pages": (spot.location + Vector((0.4, -0.8, 0.002)), 30),
        "music_stand_knockable": (spot.location + Vector((0.9, -0.3, 0)), 180),
    }
    for name, (loc, yaw) in spots.items():
        _, proots = import_glb(R / "props" / f"{name}.glb", prop_col)
        root = proots[0]
        place(root, loc, math.radians(yaw))

    ps2.setup_render(512, 512, samples=16)
    sc.eevee.use_raytracing = True
    hw, hd = 5.0, 3.8
    cams = [
        ps2.add_camera("preview_cam_overview", prev, (-hw + 0.9, -hd + 0.6, 2.2), (1.2, 1.0, 0.3), lens=17),
        ps2.add_camera("preview_cam_from_terence", prev, spot.location + Vector((0.9, 0.9, 1.7)),
                       obj["seat_trombone_2"].location + Vector((0, 0, 0.9)), lens=20),
    ]
    frames = ps2.render_frames([(c, None) for c in cams])
    ps2.compose_sheet(frames, R / "review_scene_sheet.png", cols=2, size=(1024, 512),
                      labels=["overview", "from terence"])
    sc.camera = cams[0]
    bpy.ops.wm.save_as_mainfile(filepath=str(R / "review_scene.blend"))
    print("[build_review_scene] done")


main()
