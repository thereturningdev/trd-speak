#!/bin/bash
# One-time setup for TRD Speak: venv, dependencies, model download.
# Safe to re-run.
set -euo pipefail

cd "$(dirname "$0")"

# --- Parse options ------------------------------------------------------------
for arg in "$@"; do
    case "$arg" in
        *) echo "Unknown option: $arg" >&2; exit 2 ;;
    esac
done

# --- Pick a Python interpreter (need >= 3.11 for stdlib tomllib) --------------
PYTHON=""
candidates=(/opt/homebrew/bin/python3.12 python3.12 python3.11)
for c in "${candidates[@]}"; do
    if command -v "$c" >/dev/null 2>&1; then
        PYTHON="$(command -v "$c")"
        break
    fi
done
if [ -z "$PYTHON" ] && command -v python3 >/dev/null 2>&1; then
    if python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
        PYTHON="$(command -v python3)"
    fi
fi
if [ -z "$PYTHON" ]; then
    echo "Error: no Python >= 3.11 found. Install one with: brew install python@3.12" >&2
    exit 1
fi
echo "Using Python: $PYTHON ($("$PYTHON" --version))"

# --- Create the virtualenv and install dependencies ---------------------------
if command -v uv >/dev/null 2>&1; then
    if [ ! -x .venv/bin/python ]; then
        echo "Creating .venv with uv…"
        uv venv --python "$PYTHON" .venv
    fi
    echo "Installing dependencies with uv…"
    uv pip install --python .venv/bin/python -r requirements.txt
else
    if [ ! -x .venv/bin/python ]; then
        echo "Creating .venv…"
        "$PYTHON" -m venv .venv
    fi
    echo "Installing dependencies with pip…"
    .venv/bin/python -m pip install --upgrade pip
    .venv/bin/python -m pip install -r requirements.txt
fi

# --- Pre-download the model for the configured engine -------------------------
echo "Pre-downloading the transcription model (first run only)…"
.venv/bin/python - <<'EOF'
import tomllib
from pathlib import Path

model = "base.en"
path = Path("config.toml")
if path.exists():
    try:
        data = tomllib.loads(path.read_text())
        model = data.get("whisper", {}).get("model", model)
    except Exception:
        pass

from faster_whisper import WhisperModel
WhisperModel(model, device="cpu", compute_type="int8")
print(f"Whisper model '{model}' is ready.")
EOF

# --- Done ----------------------------------------------------------------------
cat <<'EOF'

Setup complete.

Run ./make_app.sh, then open TRDSpeak.app. On first launch the app stays
quiet — just a ⚠️ menu bar icon and one notification. Click the ⚠️ icon:
its menu walks you through the three permissions one step at a time
(1 Microphone, 2 Accessibility, 3 Input Monitoring). On the last step,
System Settings itself may offer "Quit & Reopen" — accept it, or use the
menu's "Restart TRD Speak now" row if it appears. The app never restarts
itself.
EOF
