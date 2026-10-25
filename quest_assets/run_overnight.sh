#!/bin/bash
cd ~/not-quite-my-tempo-assets
while true; do
  claude -p "Read OVERNIGHT.md and PROGRESS.md, then continue with the next unfinished task. Keep working through tasks until you run out of context or usage. Update PROGRESS.md, MORNING_REPORT.md and commit after every finished task." \
    --permission-mode acceptEdits \
    --allowedTools "Bash" "Read" "Write" "Edit" "Glob" "Grep" \
    >> logs/overnight.log 2>&1
  echo "--- session ended $(date), restarting in 5 min ---" >> logs/overnight.log
  sleep 300
done
