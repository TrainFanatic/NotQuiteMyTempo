#!/bin/sh
# Runs tests/integration_terence.gd against a COPY of the repo's quest_app (Adam's, on main; never touched).
#   fletcher/godot/tests/run_integration.sh [sweep]
# Env: GODOT (binary), QUEST_APP (default: <repo>/quest_app), SCRATCH (where the copy goes).
set -e
HERE=$(cd "$(dirname "$0")/.." && pwd)
JOB=/Users/aaronwang/.claude/jobs/7b7e33c1/tmp
GODOT=${GODOT:-$JOB/Godot.app/Contents/MacOS/Godot}
QUEST_APP=${QUEST_APP:-$(cd "$HERE/../.." && pwd)/quest_app}
SCRATCH=${SCRATCH:-$JOB/agent_godot/quest_app_copy}

mkdir -p "$SCRATCH"
# keep the copy's .godot import cache between runs; everything else mirrors his tree,
# including its scripts/fletcher wiring (fletcher_setup.gd); our three scripts then replace
# the synced copies there, so this always tests fletcher/godot as it is now
rsync -a --delete --exclude .godot --exclude tests/fletcher_integration.gd \
    "$QUEST_APP/" "$SCRATCH/"
mkdir -p "$SCRATCH/scripts/fletcher"
cp "$HERE/fletcher_hub.gd" "$HERE/fletcher_voice.gd" "$HERE/fletcher_link.gd" "$SCRATCH/scripts/fletcher/"
cp "$HERE/tests/integration_terence.gd" "$SCRATCH/tests/fletcher_integration.gd"
"$GODOT" --headless --path "$SCRATCH" --import >/dev/null 2>&1 || true
# Godot exits 0 on a script parse error, so success is the test's own "ALL PASSED" line
LOG="$SCRATCH/../fletcher_integration.log"
"$GODOT" --headless --path "$SCRATCH" --script tests/fletcher_integration.gd -- "$HERE/../barks" "$@" 2>&1 | tee "$LOG"
grep -q "^ALL PASSED" "$LOG"
