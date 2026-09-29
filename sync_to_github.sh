#!/bin/bash
# Upload the hand-collected files (manual_results.csv, referees.csv) to GitHub.
# Pushing either one starts the cloud update, which rebuilds the online board
# in about two minutes. Used by the scheduled Claude task and by
# "Send Flashscore Results.command".
#
# Exit codes: 0 sent or nothing to send, 1 couldn't reach or merge, 2 GitHub
# refused the push (usually an expired login).
cd "$(dirname "$0")" || exit 1

# data/ and docs/ are rebuilt by the cloud twice a day; local copies of them are
# always out of date, so drop them before bringing this folder up to date
git checkout -q -- data docs 2>/dev/null
if ! git pull -q --rebase --autostash origin main; then
    git rebase --abort 2>/dev/null
    echo "Couldn't bring this folder up to date with GitHub. Nothing was sent."
    exit 1
fi

git add manual_results.csv referees.csv checkin.txt 2>/dev/null
if git diff --cached --quiet; then
    echo "Nothing new to send."
    exit 0
fi
git commit -q -m "Flashscore data $(date '+%Y-%m-%d %H:%M')"

if git push -q origin main; then
    echo "Sent to GitHub. The online board refreshes in about 2 minutes:"
    echo "https://pinocent.github.io/efl-corners/"
    exit 0
fi
echo "GitHub refused the upload - the saved login has probably expired."
echo "The data is committed on this Mac and will go up with the next successful sync."
exit 2
