#!/bin/bash
# Notarize & staple TRDSpeak.app for offline Gatekeeper acceptance (#12).
#
# Submits the Developer ID-signed app (./sign_app.sh, #11) to Apple's notary
# service, waits for the verdict, and staples the ticket into the bundle so it
# launches on a clean Mac with no network.
#
# Usage:   ./notarize_app.sh [path/to/TRDSpeak.app]
# Profile: $NOTARY_PROFILE  (notarytool keychain profile; default: trd-notary)
set -euo pipefail

cd "$(dirname "$0")"
REPO="$(pwd)"
APP="${1:-$REPO/dist/TRDSpeak.app}"
PROFILE="${NOTARY_PROFILE:-trd-notary}"
ZIP="${TMPDIR:-/tmp}/$(basename "$APP" .app)-notarize.zip"

if [ ! -d "$APP" ]; then
    echo "Error: $APP not found — build (TRDSpeak.spec) and sign (./sign_app.sh) it first." >&2
    exit 1
fi
# Apple rejects anything not already Developer ID-signed with the Hardened Runtime.
# Capture first, then grep: piping straight into `grep -q` trips pipefail when
# grep exits early and SIGPIPEs codesign.
SIG_INFO="$(codesign -dvv "$APP" 2>&1 || true)"
if ! grep -q "flags=.*runtime" <<<"$SIG_INFO"; then
    echo "Error: $APP lacks a Hardened Runtime signature — run ./sign_app.sh first." >&2
    exit 1
fi

echo "[1/3] Zipping for submission (ditto --keepParent) ..."
rm -f "$ZIP"
ditto -c -k --keepParent "$APP" "$ZIP"

echo "[2/3] Submitting to the notary service ($PROFILE) and waiting ..."
SUBMIT_LOG="$(mktemp)"
xcrun notarytool submit "$ZIP" --keychain-profile "$PROFILE" --wait 2>&1 | tee "$SUBMIT_LOG" || true
ID="$(grep -Eo '^[[:space:]]*id: [0-9a-fA-F-]+' "$SUBMIT_LOG" | head -1 | awk '{print $2}')"
STATUS="$(grep -E '^[[:space:]]*status:' "$SUBMIT_LOG" | tail -1 | awk '{print $2}')"
rm -f "$ZIP" "$SUBMIT_LOG"
if [ "$STATUS" != "Accepted" ]; then
    echo "Notarization status: ${STATUS:-unknown} (id ${ID:-?}). Fetching log ..." >&2
    [ -n "$ID" ] && xcrun notarytool log "$ID" --keychain-profile "$PROFILE" >&2 || true
    exit 1
fi

echo "[3/3] Stapling the ticket ..."
xcrun stapler staple "$APP"

echo
echo "Verifying ..."
xcrun stapler validate "$APP"
spctl -a -vvv -t exec "$APP"

echo
echo "Notarized + stapled: $APP"
