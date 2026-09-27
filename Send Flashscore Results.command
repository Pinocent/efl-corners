#!/bin/bash
# Double-click after the Cowork Flashscore task has added rows to
# manual_results.csv. Uploads that file to GitHub, which triggers a refresh
# of the online dashboard (about 2 minutes).
cd "$(dirname "$0")" || exit 1
echo ""

if git diff --quiet -- manual_results.csv && git diff --cached --quiet -- manual_results.csv; then
    echo "  manual_results.csv hasn't changed since the last upload. Nothing to send."
    echo ""
    read -r -p "Press Enter to close..."
    exit 0
fi

# The cloud rewrites data/ and docs/ twice a day. Any copies of those made on
# this Mac are out of date, so take the cloud's versions before uploading.
git fetch -q origin || { echo "  Couldn't reach GitHub. Check the connection and try again."; read -r -p "Press Enter to close..."; exit 1; }
git checkout -q origin/main -- data docs 2>/dev/null
git add manual_results.csv
git commit -q -m "Flashscore results $(date +%Y-%m-%d)"
if ! git pull -q --rebase origin main; then
    echo "  Couldn't merge with GitHub's copy. Nothing was uploaded."
    git rebase --abort 2>/dev/null
    read -r -p "Press Enter to close..."
    exit 1
fi

if git push -q origin main; then
    echo "  Sent. The online dashboard refreshes in about 2 minutes:"
    echo "  https://pinocent.github.io/efl-corners/"
else
    echo "  GitHub refused the upload. Your login token has probably expired."
    echo "  Make a new one at https://github.com/settings/tokens/new?scopes=repo,workflow"
    echo "  then double-click this file again and paste it when asked for a password."
    printf "protocol=https\nhost=github.com\n\n" | git credential-osxkeychain erase 2>/dev/null
fi
echo ""
read -r -p "Press Enter to close..."
