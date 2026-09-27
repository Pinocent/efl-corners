#!/bin/bash
# Double-click this to update your corners tracker.
cd "$(dirname "$0")" || exit 1
echo ""
echo "Starting up..."
echo ""

PY=""
for c in python3 /usr/bin/python3 /usr/local/bin/python3 /opt/homebrew/bin/python3; do
    if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
done

if [ -z "$PY" ]; then
    echo "  Python 3 isn't installed on this Mac."
    echo ""
    echo "  Run this in Terminal, follow the prompt, then try again:"
    echo "      xcode-select --install"
    echo ""
    read -r -p "Press Enter to close..."
    exit 1
fi

if ! "$PY" -c "import openpyxl" >/dev/null 2>&1; then
    echo "  First run - installing a required component (one time only)..."
    "$PY" -m pip install --user --quiet openpyxl 2>/dev/null \
        || "$PY" -m pip install --user --quiet --break-system-packages openpyxl
    if ! "$PY" -c "import openpyxl" >/dev/null 2>&1; then
        echo ""
        echo "  Couldn't install it automatically. Run this in Terminal:"
        echo "      $PY -m pip install --user openpyxl"
        echo ""
        read -r -p "Press Enter to close..."
        exit 1
    fi
    echo "  Done."
fi

"$PY" update_corners.py
