#!/bin/bash
P=/Users/aaronwang/NotQuiteMyTempo/fletcher/preview; V=/Users/aaronwang/.claude/jobs/7b7e33c1/tmp/agent_movie/v4
mkdir -p "$P/before"
conv() { ffmpeg -loglevel error -y -i "$1" -c:v libx264 -crf 20 -pix_fmt yuv420p -c:a aac -b:a 160k -movflags +faststart "$2"; echo "$(basename "$2") $(ffprobe -v error -show_entries format=duration -of csv=p=0 "$2")"; }
for p in "gallery_drummer bark_gallery_drummer" "gallery_side bark_gallery_side" "cutoff_drummer cutoff_sequence_drummer" "cutoff_side cutoff_sequence_side" "stress_drummer sfx_stress_drummer" "stress_side sfx_stress_side"; do
  set -- $p; conv "$V/after/$1.avi" "$P/$2.mp4"; done
for p in "cutoff_drummer v4_cutoff_sequence_drummer_lead300_noduck" "cutoff_side v4_cutoff_sequence_side_lead300_noduck" "stress_drummer v4_sfx_stress_drummer_noduck"; do
  set -- $p; conv "$V/before/$1.avi" "$P/before/$2.mp4"; done
