#!/bin/bash
# Double-click to upload manual_results.csv and referees.csv to GitHub by hand.
# (The scheduled Claude task does the same thing automatically.)
cd "$(dirname "$0")" || exit 1
echo ""
./sync_to_github.sh
if [ $? -eq 2 ]; then
    echo ""
    echo "Make a new login token at https://github.com/settings/tokens/new?scopes=repo,workflow"
    echo "then double-click this file again and paste it when asked for a password."
    printf "protocol=https\nhost=github.com\n\n" | git credential-osxkeychain erase 2>/dev/null
fi
echo ""
read -r -p "Press Enter to close..."
