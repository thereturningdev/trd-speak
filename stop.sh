#!/bin/bash
# Stop any running local-flow instance (app bundle or terminal-launched).
cd "$(dirname "$0")"
if pkill -f "$(pwd)/main.py" 2>/dev/null; then
    echo "local-flow stopped."
else
    echo "local-flow is not running (note: instances started before stop.sh existed must be stopped with Ctrl+C in their terminal)."
fi
