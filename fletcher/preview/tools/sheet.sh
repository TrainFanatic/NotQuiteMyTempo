#!/bin/sh
# sheet.sh in.avi out.png frame...  -> 2-column contact sheet (640 px tiles)
in=$1; out=$2; shift 2; n=$#
sel=""; for f in "$@"; do sel="${sel}eq(n\\,$f)+"; done; sel=${sel%+}
ffmpeg -loglevel error -y -i "$in" -vf "select='$sel',scale=640:-1,tile=2x$(( (n+1)/2 ))" -fps_mode passthrough -frames:v 1 "$out"
