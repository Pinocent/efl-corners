#!/bin/bash
# Upload the hand-collected files (manual_results.csv, referees.csv) to GitHub.
# The push starts the cloud update, which rebuilds the online board in about
# two minutes. Used by the scheduled Claude task and by
# "Send Flashscore Results.command".
#
# Every sync also sends last_sync.txt (when this Mac last synced), so every
# sync rebuilds the board straight away, new data or not: GitHub's own timer
# is best-effort, and has started runs here hours late or not at all.
#
# Exit codes: 0 sent, 1 couldn't reach or merge, 2 GitHub refused the push
# (usually an expired login).
cd "$(dirname "$0")" || exit 1

# data/ and docs/ are rebuilt by the cloud several times a day; local copies of
# them are always out of date, so drop them before bringing this folder up to date
git checkout -q -- data docs 2>/dev/null
if ! git pull -q --rebase --autostash origin main; then
    git rebase --abort 2>/dev/null
    echo "Couldn't bring this folder up to date with GitHub. Nothing was sent."
    exit 1
fi

git add manual_results.csv referees.csv checkin.txt 2>/dev/null
git add schedule_*.csv efl_referees.csv 2>/dev/null   # fixture list and EFL appointments, refreshed from this Mac
if git diff --cached --quiet; then
    sent="No new data, so just asked GitHub to rebuild the board."
    msg="Nightly refresh $(date '+%Y-%m-%d %H:%M')"
else
    sent="Sent to GitHub."
    msg="Flashscore data $(date '+%Y-%m-%d %H:%M')"
fi
date -u '+%Y-%m-%dT%H:%M:%SZ' > last_sync.txt
git add last_sync.txt
git commit -q -m "$msg"

if git push -q origin main; then
    echo "$sent The online board refreshes in about 2 minutes:"
    echo "https://pinocent.github.io/efl-corners/"
    exit 0
fi
echo "GitHub refused the upload - the saved login has probably expired."
echo "The data is committed on this Mac and will go up with the next successful sync."
exit 2
