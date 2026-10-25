#!/bin/bash
# Copy Fletcher's Godot scripts and audio into Adam's quest_app (see quest_app/README.md,
# "Fletcher integration"). The canonical scripts live HERE, in fletcher/godot/; the copies in
# quest_app/scripts/fletcher/ are generated. Edit here, then re-run:
#   fletcher/godot/sync_to_quest_app.sh [path/to/quest_app]
# Idempotent: a file is rewritten only when its content changed (so Godot does not re-import
# everything), and WAVs that no longer exist here are removed there (with their .import).
# After it changes anything, run `Godot --headless --path quest_app --import`.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
FLETCHER="$(cd "$HERE/.." && pwd)"
APP="${1:-$(cd "$FLETCHER/.." && pwd)/quest_app}"
[ -f "$APP/project.godot" ] || { echo "not a Godot project: $APP" >&2; exit 1; }

SCRIPTS="$APP/scripts/fletcher"
BARKS="$APP/fletcher_barks"          # FletcherVoice.barks_dir    = res://fletcher_barks
COUNTOFF="$APP/fletcher_countoff"    # FletcherVoice.countoff_dir = res://fletcher_countoff
HEADER="# generated copy, edit fletcher/godot/ and re-run sync (fletcher/godot/sync_to_quest_app.sh)"
changed=0

# put(src_file, dst_file): copy if different
put() {
  if ! cmp -s "$1" "$2"; then
    cp "$1" "$2"
    changed=$((changed + 1))
  fi
}

# check everything exists before copying anything
for f in fletcher_hub fletcher_voice fletcher_link; do
  [ -f "$HERE/$f.gd" ] || { echo "missing $HERE/$f.gd" >&2; exit 1; }
done
compgen -G "$FLETCHER/barks/*.wav" > /dev/null || { echo "no WAVs in $FLETCHER/barks" >&2; exit 1; }
compgen -G "$FLETCHER/countoff/co_*.wav" > /dev/null || { echo "no co_*.wav in $FLETCHER/countoff" >&2; exit 1; }
for f in manifest.json cues.json; do
  [ -f "$FLETCHER/countoff/$f" ] || { echo "missing $FLETCHER/countoff/$f" >&2; exit 1; }
done

mkdir -p "$SCRIPTS" "$BARKS" "$COUNTOFF"
tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT
for f in fletcher_hub fletcher_voice fletcher_link; do
  { echo "$HEADER"; cat "$HERE/$f.gd"; } > "$tmp"
  put "$tmp" "$SCRIPTS/$f.gd"
done

# mirror(src_dir, dst_dir, glob): copy matching WAVs, drop stale ones
mirror() {
  local src="$1" dst="$2" glob="$3" f base
  for f in "$src"/$glob; do
    put "$f" "$dst/$(basename "$f")"
  done
  for f in "$dst"/*.wav; do
    [ -e "$f" ] || continue
    base="$(basename "$f")"
    if [ ! -f "$src/$base" ] || [[ "$base" != $glob ]]; then
      rm -f "$f" "$f.import"
      changed=$((changed + 1))
    fi
  done
}
mirror "$FLETCHER/barks" "$BARKS" "*.wav"
mirror "$FLETCHER/countoff" "$COUNTOFF" "co_*.wav"
put "$FLETCHER/countoff/manifest.json" "$COUNTOFF/manifest.json"
put "$FLETCHER/countoff/cues.json" "$COUNTOFF/cues.json"

echo "fletcher -> $APP: $changed file(s) changed;" \
  "$(ls "$BARKS"/*.wav | wc -l | tr -d ' ') barks, $(ls "$COUNTOFF"/*.wav | wc -l | tr -d ' ') count-off clips"
[ "$changed" -eq 0 ] || echo "now run: Godot --headless --path $APP --import"
