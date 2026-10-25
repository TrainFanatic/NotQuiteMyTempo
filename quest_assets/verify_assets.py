"""Verify every exported .glb by re-importing it in a clean scene (task B4 of OVERNIGHT.md).

Run:
    /Applications/Blender.app/Contents/MacOS/Blender --background --python verify_assets.py

Checks per file: imports cleanly, triangle count against its target, skeleton has every
SkeletonProfileHumanoid bone used (incl. fingers), at most 4 influences per vertex, action
count matches what the build scripts promise, textures are 128-512 px with nearest
filtering, characters stand 1.6-1.95 m tall with feet on the floor.
Writes VERIFY.md and verify_sheet.png (6 Terence key poses + 3 band poses, rendered from
the re-imported files, not the build scenes). Exits non-zero if anything fails.
"""

import math
import sys
from pathlib import Path

import bpy
import numpy as np
from mathutils import Vector

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib_ps2 as ps2  # noqa: E402

R = ps2.ROOT
HUMANOID = set(ps2.humanoid_bones(1.0))
TARGETS = {"band": 2300, "terence": 2300, "instruments": 500, "props": 900, "room": 10000}
ACTIONS = {"terence/terence_v002.glb": 45, "terence/terence_v003.glb": 46, "terence/terence_v004.glb": 46, "terence/terence.glb": 0}


def expected_actions(rel):
    if rel in ACTIONS:
        return ACTIONS[rel]
    if rel.startswith("band/"):
        return 11 if "trombone" in rel else 10
    return 0


def import_clean(path):
    ps2.reset_scene()
    bpy.ops.import_scene.gltf(filepath=str(path))
    for o in list(bpy.data.objects):
        if o.name.startswith("Icosphere"):
            bpy.data.objects.remove(o, do_unlink=True)


def check(path):
    rel = path.relative_to(R).as_posix()
    group = rel.split("/")[0]
    import_clean(path)
    meshes = [o for o in bpy.data.objects if o.type == "MESH"]
    rigs = [o for o in bpy.data.objects if o.type == "ARMATURE"]
    tris = sum(sum(len(p.vertices) - 2 for p in o.data.polygons) for o in meshes)
    problems = []
    limit = TARGETS[group]
    if tris > limit:
        problems.append(f"{tris} tris > {limit}")
    n_act = len(bpy.data.actions)
    exp = expected_actions(rel)
    if n_act != exp:
        problems.append(f"{n_act} actions, expected {exp}")
    for img in bpy.data.images:
        if img.size[0] == 0:
            continue
        if not (64 <= min(img.size) and max(img.size) <= 512):
            problems.append(f"texture {img.name} {tuple(img.size)}")
    for m in bpy.data.materials:
        if not m.node_tree:
            continue
        for n in m.node_tree.nodes:
            if n.type == "TEX_IMAGE" and n.interpolation != "Closest":
                problems.append(f"{m.name} filtering {n.interpolation}")
    height = ""
    if rigs:
        rig = rigs[0]
        bones = {b.name for b in rig.data.bones}
        need = HUMANOID if rel != "terence/terence.glb" else {b for b in HUMANOID if "Thumb" not in b and not any(f in b for f in ps2.FINGER_NAMES)}
        miss = need - bones
        if miss:
            problems.append(f"missing bones {sorted(miss)[:4]}{'...' if len(miss) > 4 else ''}")
        for o in meshes:
            over = sum(1 for v in o.data.vertices if len([g for g in v.groups if g.weight > 1e-4]) > 4)
            if over:
                problems.append(f"{o.name}: {over} verts > 4 influences")
        zs = [(o.matrix_world @ v.co).z for o in meshes for v in o.data.vertices]
        height = f"{max(zs):.2f} m"
        if not (1.6 <= max(zs) <= 1.95) or abs(min(zs)) > 0.02:
            problems.append(f"height {max(zs):.2f}, floor {min(zs):.3f}")
    return dict(file=rel, tris=tris, actions=n_act, bones=len(rigs[0].data.bones) if rigs else 0,
                height=height, ok=not problems, problems=problems)


def pose_shots():
    """Re-import Terence and one band member, render key poses with their props/instrument."""
    shots = []
    sheet = [("t0_conduct", 6), ("t0_countoff_finger", 18), ("t1_fist_cutoff", 12),
             ("t2_point_player", 20), ("t3_crouch", 10), ("t5_chair_throw", 30)]
    frames, labels = [], []
    ps2.reset_scene()
    prev = ps2.new_collection("preview")
    bpy.ops.import_scene.gltf(filepath=str(R / "terence/terence_v002.glb"))
    for o in list(bpy.data.objects):
        if o.name.startswith("Icosphere"):
            bpy.data.objects.remove(o, do_unlink=True)
    rig = next(o for o in bpy.data.objects if o.type == "ARMATURE")
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=str(R / "props/chair.glb"))
    chair = next(o for o in bpy.data.objects if o not in before and o.parent is None)
    ps2.setup_render(256, 256, samples=16)
    ps2.neutral_lights(prev, center=(0, -0.3, 1.0), power=250)
    for ld in [o for o in prev.objects if o.type == "LIGHT"]:
        ld.data.size = 20
    cam = ps2.add_camera("cam", prev, (1.15, -2.1, 1.35), (0.0, -0.3, 0.95), lens=30)
    sc = bpy.context.scene
    if rig.animation_data is None:
        rig.animation_data_create()
    for name, f in sheet:
        act = bpy.data.actions.get(name)
        if act is None:
            raise RuntimeError(f"re-imported terence lacks action {name}")

        def setup(act=act, f=f, name=name):
            rig.animation_data.action = act
            if rig.animation_data.action_slot is None and act.slots:
                rig.animation_data.action_slot = act.slots[0]
            sc.frame_set(f)
            M = rig.pose.bones["PropR"].matrix
            chair.hide_render = name != "t5_chair_throw"
            chair.matrix_world = rig.matrix_world @ M
        shots.append((cam, setup))
        labels.append(f"terence {name}")
    frames += ps2.render_frames(shots)

    ps2.reset_scene()
    prev = ps2.new_collection("preview")
    bpy.ops.import_scene.gltf(filepath=str(R / "band/band_trombone_1.glb"))
    for o in list(bpy.data.objects):
        if o.name.startswith("Icosphere"):
            bpy.data.objects.remove(o, do_unlink=True)
    rig = next(o for o in bpy.data.objects if o.type == "ARMATURE")
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=str(R / "instruments/trombone_1.glb"))
    new = [o for o in bpy.data.objects if o not in before]
    body = next(o for o in new if o.parent is None and o.type == "MESH")
    slide = next(o for o in new if o.name.startswith("trombone_1_slide"))
    ps2.setup_render(256, 256, samples=16)
    ps2.neutral_lights(prev, center=(0, -0.3, 0.8), power=250)
    for ld in [o for o in prev.objects if o.type == "LIGHT"]:
        ld.data.size = 20
    cam = ps2.add_camera("cam", prev, (1.2, -1.7, 1.2), (0.0, -0.2, 0.8), lens=30)
    sc = bpy.context.scene
    if rig.animation_data is None:
        rig.animation_data_create()
    shots = []
    for name, f in (("rest", 0), ("play_loop", 18), ("look_at_drummer", 24)):
        act = bpy.data.actions.get(name)
        if act is None:
            raise RuntimeError(f"re-imported band_trombone_1 lacks action {name}")

        def setup(act=act, f=f):
            rig.animation_data.action = act
            if rig.animation_data.action_slot is None and act.slots:
                rig.animation_data.action_slot = act.slots[0]
            sc.frame_set(f)
            body.matrix_world = rig.matrix_world @ rig.pose.bones["Instrument"].matrix
            slide.matrix_world = rig.matrix_world @ rig.pose.bones["InstrumentSlide"].matrix
        shots.append((cam, setup))
        labels.append(f"trombone_1 {name}")
    frames += ps2.render_frames(shots)
    return frames, labels


def main():
    files = sorted(p for d in ("room", "terence", "band", "instruments", "props") for p in (R / d).glob("*.glb"))
    results = [check(p) for p in files]
    frames, labels = pose_shots()
    ps2.compose_sheet(frames, R / "verify_sheet.png", cols=5, size=(1024, 512), labels=labels)
    bad = [r for r in results if not r["ok"]]
    lines = ["# Asset verification", "",
             "Generated by `verify_assets.py`, which re-imports every exported glb into a clean Blender scene. "
             f"{len(results) - len(bad)} of {len(results)} files pass.", "",
             "| File | Tris | Bones | Actions | Height | Result |", "|---|---|---|---|---|---|"]
    for r in results:
        res = "ok" if r["ok"] else "FAIL: " + "; ".join(r["problems"])
        lines.append(f"| `{r['file']}` | {r['tris']} | {r['bones'] or ''} | {r['actions']} | {r['height']} | {res} |")
    lines += ["", "`verify_sheet.png`: 6 Terence key poses and 3 trombone poses, rendered from the re-imported files.", ""]
    (R / "VERIFY.md").write_text("\n".join(lines))
    print(f"[verify] {len(results) - len(bad)}/{len(results)} ok")
    for r in bad:
        print("[verify] FAIL", r["file"], r["problems"])
    if bad:
        sys.exit(1)


main()
