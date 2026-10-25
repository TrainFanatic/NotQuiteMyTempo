#!/bin/bash
# Copy the exported .glb assets from the Blender asset repo into res://assets/.
# Re-run after rebuilding anything in ../not-quite-my-tempo-assets; Godot re-imports on next open.
set -euo pipefail
SRC="${1:-$HOME/not-quite-my-tempo-assets}"
DST="$(cd "$(dirname "$0")/.." && pwd)/assets"
[ -d "$SRC" ] || { echo "asset repo not found: $SRC" >&2; exit 1; }
# the sound effects are required too: check before copying anything
for f in sfx_manifest.json sfx_cues.json; do
  [ -f "$SRC/sfx/$f" ] || { echo "missing $SRC/sfx/$f" >&2; exit 1; }
done
compgen -G "$SRC/sfx/*.wav" > /dev/null || { echo "no WAVs in $SRC/sfx" >&2; exit 1; }
for dir in band instruments props; do
  mkdir -p "$DST/$dir"
  cp "$SRC/$dir"/*.glb "$DST/$dir/"
done
mkdir -p "$DST/room" "$DST/terence"
cp "$SRC/room/room_v005.glb" "$DST/room/"
cp "$SRC/terence/terence_v003.glb" "$DST/terence/"
cp "$SRC/terence/terence_v003_moves.json" "$DST/terence/"
# sound effects (checked above): every WAV plus the manifest and cue tables (scripts/sfx reads all three)
mkdir -p "$DST/sfx"
cp "$SRC/sfx"/*.wav "$SRC/sfx/sfx_manifest.json" "$SRC/sfx/sfx_cues.json" "$DST/sfx/"
echo "synced $(find "$DST" -name '*.glb' | wc -l | tr -d ' ') glbs and $(find "$DST/sfx" -name '*.wav' | wc -l | tr -d ' ') sfx WAVs into $DST"
