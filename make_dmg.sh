#!/bin/bash
# Build, sign, notarize & staple the distributable TRDSpeak.dmg (#13).
#
# Packages the notarized + stapled app (./notarize_app.sh, #12) into a compressed
# DMG with an /Applications drop target, signs the DMG with the Developer ID
# cert, notarizes it, and staples the ticket so the download opens on a clean Mac
# with no Gatekeeper warning.
#
# Usage:   ./make_dmg.sh [path/to/TRDSpeak.app]
# Output:  dist/TRDSpeak.dmg
# Env:     $CODESIGN_IDENTITY (default: RELEASING.md cert)
#          $NOTARY_PROFILE    (default: trd-notary)
set -euo pipefail

cd "$(dirname "$0")"
REPO="$(pwd)"
APP="${1:-$REPO/dist/TRDSpeak.app}"
DMG="$REPO/dist/TRDSpeak.dmg"
VOLNAME="TRD Speak"
IDENTITY="${CODESIGN_IDENTITY:-Developer ID Application: Filippo Diotalevi (2FV8WB29XC)}"
PROFILE="${NOTARY_PROFILE:-trd-notary}"

if [ ! -d "$APP" ]; then
    echo "Error: $APP not found — build, sign and notarize it first." >&2
    exit 1
fi
# The payload app must already be notarized+stapled, so it launches on a clean
# Mac regardless of the DMG's own ticket.
if ! xcrun stapler validate "$APP" >/dev/null 2>&1; then
    echo "Error: $APP is not stapled — run ./notarize_app.sh first." >&2
    exit 1
fi
if ! security find-identity -v -p codesigning | grep -qF "$IDENTITY"; then
    echo "Error: signing identity not in keychain: $IDENTITY" >&2
    exit 1
fi

echo "[1/4] Staging DMG contents (app + /Applications symlink) ..."
STAGING="$(mktemp -d)"
ditto "$APP" "$STAGING/$(basename "$APP")"   # ditto preserves the signature + staple
ln -s /Applications "$STAGING/Applications"

echo "[2/4] Building compressed DMG (hdiutil, UDZO) ..."
rm -f "$DMG"
hdiutil create -volname "$VOLNAME" -srcfolder "$STAGING" -ov -format UDZO "$DMG" >/dev/null
rm -rf "$STAGING"

echo "[3/4] Signing the DMG ..."
codesign --force --timestamp --sign "$IDENTITY" "$DMG"

echo "[4/4] Notarizing + stapling the DMG ..."
SUBMIT_LOG="$(mktemp)"
xcrun notarytool submit "$DMG" --keychain-profile "$PROFILE" --wait 2>&1 | tee "$SUBMIT_LOG" || true
ID="$(grep -Eo '^[[:space:]]*id: [0-9a-fA-F-]+' "$SUBMIT_LOG" | head -1 | awk '{print $2}')"
STATUS="$(grep -E '^[[:space:]]*status:' "$SUBMIT_LOG" | tail -1 | awk '{print $2}')"
rm -f "$SUBMIT_LOG"
if [ "$STATUS" != "Accepted" ]; then
    echo "DMG notarization status: ${STATUS:-unknown} (id ${ID:-?}). Fetching log ..." >&2
    [ -n "$ID" ] && xcrun notarytool log "$ID" --keychain-profile "$PROFILE" >&2 || true
    exit 1
fi
xcrun stapler staple "$DMG"

echo
echo "Verifying ..."
xcrun stapler validate "$DMG"
spctl -a -t open --context context:primary-signature -vvv "$DMG"

echo
echo "Built + notarized + stapled: $DMG"
