#!/bin/bash
# Build the Quest APK and (optionally) install + launch it on a USB-connected headset.
#   tools/build_quest.sh            build build/not_quite_my_tempo.apk
#   tools/build_quest.sh install    build, adb install, launch
#   tools/build_quest.sh install-debug   same, launched with -- --passthrough-debug (see-through room)
#   tools/build_quest.sh demo       build the judge demo, build/nqmt_demo.apk (preset "Quest NQMT Demo")
#   tools/build_quest.sh install-demo    build it, adb install, launch (it plays itself, no input)
# Needs: JDK 17, Android SDK at ~/Library/Android/sdk, Godot 4.5 export templates.
set -euo pipefail
cd "$(dirname "$0")/.."
GODOT="${GODOT:-$HOME/Downloads/Godot.app/Contents/MacOS/Godot}"
export JAVA_HOME="${JAVA_HOME:-/opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home}"
export ANDROID_HOME="${ANDROID_HOME:-$HOME/Library/Android/sdk}"
ADB="$ANDROID_HOME/platform-tools/adb"
PKG="com.notquitemytempo.app"
PRESET="Quest"
APK="build/not_quite_my_tempo.apk"
MODE="${1:-}"
case "$MODE" in
  "" | install) LAUNCH_ARGS=() ;;
  # Godot's Android activity takes extra command-line arguments from this intent extra
  install-debug) LAUNCH_ARGS=(--esa command_line_params "--,--passthrough-debug") ;;
  demo | install-demo) LAUNCH_ARGS=(); PKG="com.notquitemytempo.demo"; PRESET="Quest NQMT Demo"; APK="build/nqmt_demo.apk" ;;
  *) echo "unknown mode '$MODE' (expected install, install-debug, demo or install-demo)" >&2; exit 1 ;;
esac

[ -x "$GODOT" ] || { echo "Godot not found at $GODOT (set GODOT=...)" >&2; exit 1; }
# the template install runs an export too, so the output folder has to exist first
mkdir -p build
[ -d android/build ] || "$GODOT" --headless --path . --install-android-build-template --export-debug Quest build/not_quite_my_tempo.apk >/dev/null

# The OpenXR Vendors plugin (4.3.1) adds the Quest metadata but not its uses-feature
# entries on this Godot version, so declare passthrough in the Gradle build manifest.
MANIFEST=android/build/AndroidManifest.xml
if ! grep -q "oculus.software.handtracking" "$MANIFEST"; then
  # strip an older injection (passthrough only) so the block is re-added whole
  sed -i '' '/com.oculus.feature.PASSTHROUGH/d;/com.oculus.permission.USE_SCENE/d' "$MANIFEST"
  python3 - "$MANIFEST" <<'PY'
import sys
p = sys.argv[1]
s = open(p).read()
add = ('    <uses-feature android:name="com.oculus.feature.PASSTHROUGH" android:required="true" />\n'
       '    <uses-feature android:name="oculus.software.handtracking" android:required="false" />\n'
       '    <uses-permission android:name="com.oculus.permission.HAND_TRACKING" />\n'
       '    <uses-permission android:name="com.oculus.permission.USE_SCENE" />\n\n    <application')
s = s.replace("    <application", add, 1)
open(p, "w").write(s)
PY
fi

"$GODOT" --headless --path . --export-debug "$PRESET" "$APK" > build/export.log 2>&1
grep -q "com.oculus.feature.PASSTHROUGH" <("$ANDROID_HOME/build-tools/35.0.1/aapt2" dump xmltree --file AndroidManifest.xml "$APK") \
  || { echo "APK lacks the passthrough feature; see build/export.log" >&2; exit 1; }
echo "built $APK"

if [ -n "$MODE" ] && [ "$MODE" != demo ]; then
  # the Godot export restarts adb, so give it a moment to see the headset again
  "$ADB" start-server >/dev/null 2>&1
  for _ in $(seq 1 15); do
    state="$("$ADB" get-state 2>/dev/null || true)"
    [ "$state" = "device" ] && break
    sleep 1
  done
  [ "$state" = "device" ] || { echo "headset not ready (adb state: ${state:-none}); put it on and accept 'Allow USB debugging'" >&2; exit 1; }
  "$ADB" install -r "$APK"
  "$ADB" shell am force-stop "$PKG"
  "$ADB" shell am start -n "$PKG/com.godot.game.GodotApp" ${LAUNCH_ARGS[@]+"${LAUNCH_ARGS[@]}"}
  echo "launched $PKG"
fi
