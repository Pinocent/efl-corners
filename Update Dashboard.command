#!/bin/bash
# Double-click to refresh the dashboard locally and open it.
cd "$(dirname "$0")" || exit 1
PY=""
for c in python3 /usr/bin/python3 /usr/local/bin/python3 /opt/homebrew/bin/python3; do
    if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
done
if [ -z "$PY" ]; then
    echo "Python 3 isn't installed. Run: xcode-select --install"
    read -r -p "Press Enter to close..."
    exit 1
fi
"$PY" run.py && open docs/index.html
read -r -p "Press Enter to close..."
