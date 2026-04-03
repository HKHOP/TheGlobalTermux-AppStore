#!/data/data/com.termux/files/usr/bin/bash

set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR" || exit 1

echo "Starting Termux App Store..."
echo "Working directory: $SCRIPT_DIR"
echo

python app.py
EXIT_CODE=$?

echo
echo "Termux App Store exited with code: $EXIT_CODE"
echo "Press Enter to close this terminal..."
read -r _
