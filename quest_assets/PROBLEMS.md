# Problems log (overnight run)

- **run_overnight.sh could not authenticate.** `claude -p` returned `401 Unauthorized: Invalid token` on every restart. The work runs in the Claude Code session instead (see DECISIONS.md). To use the script later, log the CLI in with `claude` then `/login`.
- **Blender's glTF importer adds `Icosphere` bone-display objects.** They rendered as a white blob at the origin in the review scene. They're hidden on import in `build_review_scene.py`. This is Blender-only; Godot doesn't create them.
- **glTF export with several rigs in one file exports every compatible action** (renamed `.001`). Worked around by exporting members one at a time and deleting their actions (see DECISIONS.md, B1 export).
- **Cosmetic (fixed in polish 4):** `band/band_v002_sheet.png` showed other line-up rigs in T-pose behind the trombone tiles.
- **B2 hand roll isn't controlled** (the IK sets wrist position and hand direction, not palm rotation), so palm facing is approximate, e.g. on claps and slaps. Fine at PS2 detail, but worth a look in the headset.
- **(Resolved: Godot 4.5.1 was in ~/Downloads; Phase C is now built in ~/not-quite-my-tempo.) Phase C skipped: Godot 4 is not installed.** There's no `/Applications/Godot*.app` and no `godot`/`godot4` on PATH. Per the work order, C1 to C4 are logged and skipped. Install Godot 4 and rerun the work order to build `godot/` (import validator, MIDI parser and phrase extraction, AnimationTree state machines, scene director) with headless tests. Until then, nothing about the Godot side (BoneAttachment offsets, retargeting) has been tested.
- **Cosmetic:** labels overlap in `verify_sheet.png` (5 tiles per row). Fixed in `lib_ps2.compose_sheet` for future sheets; the sheet itself wasn't re-rendered.
