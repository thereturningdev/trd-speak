#!/bin/bash
# Start TRD Speak.
set -euo pipefail

cd "$(dirname "$0")"

if [ ! -x .venv/bin/python ]; then
    echo "Error: .venv not found — run ./setup.sh first." >&2
    exit 1
fi

# Absolute path so stop.sh can match this process reliably.
exec "$(pwd)/.venv/bin/python" "$(pwd)/main.py" "$@"
