#!/bin/bash
# Developer ID code-sign TRDSpeak.app INSIDE-OUT for notarization (#11).
#
# Signs every nested Mach-O first, then the .app last — each with the Hardened
# Runtime (--options runtime) and a secure timestamp (--timestamp). The final
# .app sign also applies entitlements.plist (#10), which codesign attaches to
# the bundle's main executable (Contents/MacOS/TRDSpeak). We do NOT use --deep:
# nested code is signed explicitly so nothing is left ad-hoc.
#
# Usage:   ./sign_app.sh [path/to/TRDSpeak.app]
# Identity is read from $CODESIGN_IDENTITY (never a secret — it's a cert name);
# defaults to the Developer ID recorded in RELEASING.md.
set -euo pipefail

cd "$(dirname "$0")"
REPO="$(pwd)"
APP="${1:-$REPO/dist/TRDSpeak.app}"
ENTITLEMENTS="$REPO/entitlements.plist"
IDENTITY="${CODESIGN_IDENTITY:-Developer ID Application: Filippo Diotalevi (2FV8WB29XC)}"

if [ ! -d "$APP" ]; then
    echo "Error: $APP not found — build it first: .venv/bin/pyinstaller --noconfirm TRDSpeak.spec" >&2
    exit 1
fi
if [ ! -f "$ENTITLEMENTS" ]; then
    echo "Error: $ENTITLEMENTS not found." >&2
    exit 1
fi
if ! security find-identity -v -p codesigning | grep -qF "$IDENTITY"; then
    echo "Error: signing identity not in keychain: $IDENTITY" >&2
    echo "Set CODESIGN_IDENTITY or import the Developer ID Application cert." >&2
    exit 1
fi

# Common flags for nested Mach-Os (no entitlements — those go on the app).
sign() { codesign --force --options runtime --timestamp --sign "$IDENTITY" "$@"; }

echo "Signing $APP"
echo "  identity: $IDENTITY"

# 1. Every nested extension module / dylib. These are leaves, so order among
#    them is irrelevant — they just must all be signed before any bundle that
#    contains them is sealed (steps 2-3). This glob also covers the .so's that
#    live inside Python.framework (lib-dynload), signing them before step 2.
echo "  [1/3] nested .so / .dylib ..."
find "$APP" -type f \( -name "*.so" -o -name "*.dylib" \) -print0 \
    | while IFS= read -r -d '' f; do sign "$f"; done

# 2. The embedded CPython framework: sign each version's Mach-O, then seal the
#    versioned bundle. (Current is a symlink — skip it.)
PYFW="$APP/Contents/Frameworks/Python.framework"
if [ -d "$PYFW" ]; then
    echo "  [2/3] Python.framework ..."
    for ver in "$PYFW"/Versions/*/; do
        [ -d "$ver" ] || continue
        case "$ver" in */Current/) continue ;; esac
        [ -f "${ver}Python" ] && sign "${ver}Python"
        sign "${ver%/}"
    done
fi

# 3. The .app last. Sealing the bundle signs the main executable
#    (Contents/MacOS/TRDSpeak) WITH the Hardened Runtime entitlements.
echo "  [3/3] TRDSpeak.app (with entitlements) ..."
codesign --force --options runtime --timestamp \
    --entitlements "$ENTITLEMENTS" --sign "$IDENTITY" "$APP"

echo
echo "Verifying..."
codesign --verify --deep --strict --verbose=2 "$APP"
codesign -dvvv "$APP" 2>&1 | grep -E "^(Authority|TeamIdentifier|Identifier|CodeDirectory.*runtime)|flags=.*runtime"

echo
echo "Signed OK: $APP"
