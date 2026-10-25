#!/bin/sh
# render_v4.sh <copy dir> <out dir> <cutoff from rx> [names...]
C=$1; O=$2; FROM=$3; shift 3
M=/Users/aaronwang/.claude/jobs/7b7e33c1/tmp/agent_movie; G=$M/../Godot.app/Contents/MacOS/Godot; S=$M/v4
CUT=${CUT:-$S/cutoff_stream.jsonl}
SIDE="eye=4.8,2.0,-3.6 fov=30"
mkdir -p $O
run() { name=$1; shift; set -- "$@" $EXTRA; perl -e "alarm 240; exec @ARGV" "$G" --path "$C" --always-on-top --write-movie "$O/$name.avi" --fixed-fps 30 --resolution 1280x720 res://movie/movie.tscn -- log="$O/$name.jsonl" "$@" > "$O/$name.log" 2>&1; echo "$name exit $? errors $(grep -c 'SCRIPT ERROR' $O/$name.log)"; }
for n in "$@"; do case $n in
  gallery_drummer) run $n stream=$S/gallery_stream.jsonl cam=drummer lead=0.5 tail=1.5 ;;
  gallery_side) run $n stream=$S/gallery_stream.jsonl cam=track $SIDE lead=0.5 tail=1.5 ;;
  cutoff_drummer) run $n stream=$CUT cam=drummer from=$FROM lead=0.5 tail=3.0 bpm=94 ;;
  cutoff_side) run $n stream=$CUT cam=track $SIDE from=$FROM lead=0.5 tail=3.0 bpm=94 ;;
  cutoff_voice) run $n stream=$CUT cam=drummer from=$FROM lead=0.5 tail=3.0 bpm=94 stem=voice ;;
  cutoff_sfx) run $n stream=$CUT cam=drummer from=$FROM lead=0.5 tail=3.0 bpm=94 stem=sfx ;;
  gallery_voice) run $n stream=$S/gallery_stream.jsonl cam=drummer lead=0.5 tail=1.5 stem=voice ;;
  gallery_sfx) run $n stream=$S/gallery_stream.jsonl cam=drummer lead=0.5 tail=1.5 stem=sfx ;;
  stress_drummer) run $n stream=$S/stress_stream.jsonl cam=drummer lead=0.5 tail=13.0 bpm=94 ;;
  stress_side) run $n stream=$S/stress_stream.jsonl cam=track $SIDE lead=0.5 tail=13.0 bpm=94 ;;
  stress_voice) run $n stream=$S/stress_stream.jsonl cam=drummer lead=0.5 tail=13.0 bpm=94 stem=voice ;;
  stress_sfx) run $n stream=$S/stress_stream.jsonl cam=drummer lead=0.5 tail=13.0 bpm=94 stem=sfx ;;
esac; done
