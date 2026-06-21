#!/bin/bash
# Build a self-contained, INSTALLABLE development build of TRD Speak.
#
# This is the development-build half of CLAUDE.md ground rule 2 (the production
# half is the GitHub Actions release workflow). It produces, on the development
# machine:
#
#   dist/TRD Speak Dev.app   — self-contained PyInstaller app, runs from
#                              /Applications with nothing from this repo
#   dist/TRD Speak Dev.dmg   — the same app packaged for a normal drag-install
#
# Versioning (ground rule 2): the version is the LATEST PUBLISHED GitHub release
# with "+dev" appended — derived here, never hand-typed. Latest stable 0.1.3 =>
# this build is 0.1.3+dev.
#
# Identity: com.thereturningdev.speak.dev / "TRD Speak Dev", so the dev build
# installs alongside the stable build with its own permissions, never clobbering
# it. Ad-hoc signed (dev builds are not notarized).
#
# Ground rule 4: this script only PRODUCES the application. It does not install
# it, launch its GUI, or grant it any permissions — that is the user's job. The
# only thing it runs are the app's own non-GUI smoke tests (--version,
# --selftest), which are the automated functional test for this build and which
# touch no machine or permission state.
set -euo pipefail

cd "$(dirname "$0")"
REPO="$(pwd)"
PY="$REPO/.venv/bin/python"
APP="$REPO/dist/TRD Speak Dev.app"
DMG="$REPO/dist/TRD Speak Dev.dmg"
MODEL_DIR="$REPO/models/faster-whisper-base.en"
DEV_BUNDLE_ID="com.thereturningdev.speak.dev"
DEV_NAME="TRD Speak Dev"

# --- prerequisites -------------------------------------------------------
if [ ! -x "$PY" ]; then
    echo "Error: .venv missing — run ./setup.sh first." >&2
    exit 1
fi
if ! "$PY" -c "import PyInstaller" 2>/dev/null; then
    echo "Error: PyInstaller missing — pip install -r requirements-build.txt" >&2
    exit 1
fi
if ! command -v gh >/dev/null 2>&1; then
    echo "Error: gh CLI required to read the latest published release." >&2
    exit 1
fi
# The spec refuses to build without the embedded default model; fetch on demand.
if [ ! -d "$MODEL_DIR" ]; then
    echo "[0/5] Fetching the default Whisper model ..."
    "$PY" scripts/fetch_model.py
fi

# --- derive the version from the latest PUBLISHED GitHub release ---------
# /releases/latest excludes drafts and pre-releases, so it is the latest stable.
echo "[1/5] Resolving version from the latest published GitHub release ..."
BASE="$(gh api 'repos/{owner}/{repo}/releases/latest' -q .tag_name 2>/dev/null | sed 's/^v//')"
if [ -z "$BASE" ]; then
    echo "Error: could not read the latest published release from GitHub." >&2
    exit 1
fi
DEVVER="${BASE}+dev"
echo "      latest stable $BASE  ->  development build $DEVVER"

# --- stamp __version__ for this build only; always revert ----------------
# CFBundle*Version (plist) must be a plain dotted number, so the bundle carries
# $BASE; the in-app __version__ (the menu row) carries the full $DEVVER.
trap 'git checkout -- flow/__init__.py 2>/dev/null || true' EXIT
sed -i '' "s/^__version__ = .*/__version__ = \"${DEVVER}\"/" flow/__init__.py

# --- build the self-contained app ----------------------------------------
echo "[2/5] PyInstaller self-contained build ..."
rm -rf "$APP" "$DMG"
TRDSPEAK_VERSION="$BASE" "$PY" -m PyInstaller --noconfirm TRDSpeak.spec
git checkout -- flow/__init__.py   # source has been read; restore it now
trap - EXIT

# --- re-flavour as the dev identity, then ad-hoc sign --------------------
echo "[3/5] Applying dev identity and ad-hoc signature ..."
mv "$REPO/dist/TRDSpeak.app" "$APP"
/usr/libexec/PlistBuddy -c "Set :CFBundleIdentifier $DEV_BUNDLE_ID" "$APP/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleName $DEV_NAME" "$APP/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleDisplayName $DEV_NAME" "$APP/Contents/Info.plist" 2>/dev/null \
    || /usr/libexec/PlistBuddy -c "Add :CFBundleDisplayName string $DEV_NAME" "$APP/Contents/Info.plist"
codesign --force --deep --sign - "$APP"

# --- package the DMG -----------------------------------------------------
echo "[4/5] Packaging DMG ..."
hdiutil create -volname "$DEV_NAME" -srcfolder "$APP" -ov -format UDZO "$DMG" >/dev/null

# --- automated functional test (ground rule 1): fail on ANY mismatch -----
echo "[5/5] Verifying the build (no GUI, no install, no permissions) ..."
fail() { echo "FUNCTIONAL TEST FAILED: $1" >&2; exit 1; }

got_id="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$APP/Contents/Info.plist")"
[ "$got_id" = "$DEV_BUNDLE_ID" ] || fail "bundle id is '$got_id', expected '$DEV_BUNDLE_ID'"

got_name="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleName' "$APP/Contents/Info.plist")"
[ "$got_name" = "$DEV_NAME" ] || fail "name is '$got_name', expected '$DEV_NAME'"

got_ver="$("$APP/Contents/MacOS/TRDSpeak" --version)"
[ "$got_ver" = "TRD Speak $DEVVER" ] || fail "version is '$got_ver', expected 'TRD Speak $DEVVER'"

"$APP/Contents/MacOS/TRDSpeak" --selftest || fail "--selftest did not pass"
codesign --verify --deep "$APP" 2>/dev/null || fail "signature did not verify"
[ -f "$DMG" ] || fail "DMG was not created"

cat <<EOF

Functional test passed. Development build $DEVVER is ready:
  app: $APP
  dmg: $DMG

It is NOT installed and NOT configured (ground rule 4). To test it as a user:
  - open "$DMG", drag "$DEV_NAME.app" to /Applications
  - launch it; follow the menu's permission steps (Microphone, Input
    Monitoring, Accessibility)
  - hold your dictate hotkey, speak, release
  - the menu shows the greyed row "TRD Speak $DEVVER (dev)"
EOF
