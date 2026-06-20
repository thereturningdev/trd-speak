#!/bin/bash
# Stop any running TRD Speak instance (app bundle or terminal-launched).
cd "$(dirname "$0")"
if pkill -f "$(pwd)/main.py" 2>/dev/null; then
    echo "TRD Speak stopped."
else
    echo "TRD Speak is not running (note: instances started before stop.sh existed must be stopped with Ctrl+C in their terminal)."
fi
