#!/bin/sh
# render_menu.sh [out dir] [extra args...]: the game menu and the flow into a song
# (menu_movie.gd) -> fletcher/preview/menu_flow_drummer.mp4. Renders from a copy of quest_app
# whose game + Fletcher scripts tell time with MovieClock (frames / fps), so wall-clock
# scheduling (t0, the backing's play_at, cues) lands on rendered time.
set -e
R=/Users/aaronwang/NotQuiteMyTempo; T=$R/fletcher/preview/tools
W=${W:-/Users/aaronwang/.claude/jobs/7b7e33c1/tmp/agent_d_movie}; C=$W/quest_app_copy
G=/Users/aaronwang/.claude/jobs/7b7e33c1/tmp/Godot.app/Contents/MacOS/Godot
O=${1:-$W/out}; shift 2>/dev/null || true
mkdir -p "$C" "$O"
rsync -a --delete --exclude movie "$R/quest_app/" "$C/"
mkdir -p "$C/movie"
cp "$T/movie_clock.gd" "$T/menu_movie.gd" "$T/menu_movie.tscn" "$C/movie/"
for f in "$C"/scripts/game/*.gd "$C"/scripts/fletcher/*.gd; do
  sed -i '' 's#Time.get_ticks_usec() / 1e6#MovieClock.now()#g' "$f"
done
"$G" --headless --path "$C" --import > "$O/import.log" 2>&1 || true
perl -e 'alarm 400; exec @ARGV' "$G" --path "$C" --write-movie "$O/menu_flow.avi" --fixed-fps 30 --resolution 1280x720 \
  res://movie/menu_movie.tscn -- songs="$R/songs" "$@" > "$O/menu_flow.log" 2>&1 || true
echo "errors: $(grep -c 'SCRIPT ERROR' "$O/menu_flow.log")"
ffmpeg -loglevel error -y -i "$O/menu_flow.avi" -c:v libx264 -crf 20 -pix_fmt yuv420p -c:a aac -b:a 160k \
  -movflags +faststart "$R/fletcher/preview/menu_flow_drummer.mp4"
ffprobe -v error -show_entries format=duration -of csv=p=0 "$R/fletcher/preview/menu_flow_drummer.mp4"
