#!/bin/bash
# One-command distribution build for TRD Speak (#14).
#
# Produces a notarized, stapled dist/TRDSpeak.dmg that passes Gatekeeper on a
# clean Mac, by chaining the release steps (#7-#13):
#
#   clean -> PyInstaller self-contained build -> sign inside-out (#11)
#         -> notarize + staple the app (#12)
#         -> build + sign + notarize + staple the DMG (#13)
#         -> final verification (spctl + stapler)
#
# No secrets live here: the Developer ID cert is in the keychain and the notary
# credential is in the keychain profile. Only their NAMES are configurable:
#   $CODESIGN_IDENTITY  (default: the Developer ID in RELEASING.md)
#   $NOTARY_PROFILE     (default: trd-notary)
#
# Prerequisites — see RELEASING.md. Usage:  ./make_release.sh
set -euo pipefail

cd "$(dirname "$0")"
REPO="$(pwd)"
PY="$REPO/.venv/bin/python"
APP="$REPO/dist/TRDSpeak.app"
DMG="$REPO/dist/TRDSpeak.dmg"
MODEL_DIR="$REPO/models/faster-whisper-base.en"

if [ ! -x "$PY" ]; then
    echo "Error: .venv missing — run ./setup.sh first." >&2
    exit 1
fi
if ! "$PY" -c "import PyInstaller" 2>/dev/null; then
    echo "Error: PyInstaller missing — pip install -r requirements-build.txt" >&2
    exit 1
fi

# The spec refuses to build without the embedded default model; fetch on demand.
if [ ! -d "$MODEL_DIR" ]; then
    echo "[0/5] Fetching the default Whisper model ..."
    "$PY" scripts/fetch_model.py
fi

echo "[1/5] Clean ..."
rm -rf "$REPO/build" "$REPO/dist"

echo "[2/5] PyInstaller self-contained build ..."
"$PY" -m PyInstaller --noconfirm TRDSpeak.spec

echo "[3/5] Sign inside-out (Developer ID + Hardened Runtime + entitlements) ..."
./sign_app.sh "$APP"

echo "[4/5] Notarize + staple the app ..."
./notarize_app.sh "$APP"

echo "[5/5] Build + sign + notarize + staple the DMG ..."
./make_dmg.sh "$APP"

echo
echo "=== Final verification ==="
xcrun stapler validate "$APP"
xcrun stapler validate "$DMG"
spctl -a -vvv -t exec "$APP"
spctl -a -t open --context context:primary-signature -vvv "$DMG"

echo
echo "Release artifact ready: $DMG"
