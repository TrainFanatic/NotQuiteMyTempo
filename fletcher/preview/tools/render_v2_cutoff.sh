#!/bin/sh
M=/Users/aaronwang/.claude/jobs/7b7e33c1/tmp/agent_movie; C=$M/quest_app_copy; G=$M/../Godot.app/Contents/MacOS/Godot; B=/Users/aaronwang/NotQuiteMyTempo/fletcher/barks; CO=/Users/aaronwang/NotQuiteMyTempo/fletcher/countoff
SIDE="eye=4.8,2.0,-3.6 fov=30"
run() { name=$1; shift; start=$(date +%s); "$G" --path "$C" --write-movie "$M/out/$name.avi" --fixed-fps 30 --resolution 1280x720 res://movie/movie.tscn -- log="$M/out/$name.jsonl" barks="$B" countoff_dir="$CO" "$@" > "$M/out/$name.log" 2>&1; echo "$name exit $? $(( $(date +%s) - start )) s"; }
run cutoff_drummer stream=$M/cutoff_stream.jsonl cam=drummer from=226381.621 lead=0.5 tail=3.0 bpm=94
run cutoff_side stream=$M/cutoff_stream.jsonl cam=track $SIDE from=226381.621 lead=0.5 tail=3.0 bpm=94
