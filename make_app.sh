#!/bin/bash
# Build TRDSpeak.app — a minimal app bundle so macOS permissions (Microphone,
# Input Monitoring, Accessibility) attach to "TRD Speak Dev" instead of your
# terminal app. Requires the Xcode Command Line Tools (xcode-select --install).
#
# The bundle's executable is a small compiled launcher that runs the Python
# process as a CHILD and waits. It must NOT exec/replace itself with Python:
# macOS attributes permissions to the code identity of the running executable,
# so an exec'd interpreter makes the prompts name "Python 3.12" instead of
# TRD Speak Dev. A live parent passes its app identity down to its children.
#
# The child it spawns is a COPY of the real CPython Mach-O placed inside
# Contents/MacOS (TRDSpeak-python). LaunchServices names a checked-in app
# after the bundle enclosing its executable, so spawning .venv/bin/python
# directly would register the GUI process as "Python" (org.python.python) —
# wrong Dock label, and `lsappinfo info "TRD Speak Dev"` would find nothing. The
# interpreter links libpython by absolute path, so the copy still runs; the
# launcher sets PYTHONEXECUTABLE=.venv/bin/python so it adopts the venv
# (pyvenv.cfg, site-packages, sys.executable) exactly as before.
#
# Safe to re-run, but rebuilding changes the ad-hoc signing identity, so macOS
# may ask you to re-grant the permissions afterwards.
set -euo pipefail

cd "$(dirname "$0")"
REPO="$(pwd)"
APP="$REPO/TRDSpeak.app"

if [ ! -x .venv/bin/python ]; then
    echo "Error: .venv missing — run ./setup.sh first." >&2
    exit 1
fi
if ! command -v cc >/dev/null 2>&1; then
    echo "Error: no C compiler — install the Xcode Command Line Tools with: xcode-select --install" >&2
    exit 1
fi

# Never rebuild over a running instance: replacing a live bundle makes macOS
# attribute its pending permission prompts to a phantom app named "old".
./stop.sh >/dev/null 2>&1 || true

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"

cat > "$APP/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundlePackageType</key>      <string>APPL</string>
    <key>CFBundleIdentifier</key>       <string>com.thereturningdev.speak.dev</string>
    <key>CFBundleName</key>             <string>TRD Speak Dev</string>
    <key>CFBundleExecutable</key>       <string>TRDSpeak</string>
    <key>CFBundleShortVersionString</key> <string>0.1.0</string>
    <key>CFBundleIconFile</key>         <string>AppIcon</string>
    <key>NSMicrophoneUsageDescription</key>
    <string>TRD Speak records your voice while the hotkey is held, to transcribe it locally on this machine.</string>
</dict>
</plist>
EOF

# --- App icon: a mic glyph on a rounded rectangle, drawn with AppKit -----
ICONSET="$(mktemp -d)/AppIcon.iconset"
mkdir -p "$ICONSET" "$APP/Contents/Resources"
.venv/bin/python - "$ICONSET/icon_512x512@2x.png" <<'PYEOF'
import sys
import AppKit
import Foundation

SIZE = 1024
img = AppKit.NSImage.alloc().initWithSize_((SIZE, SIZE))
img.lockFocus()
rect = Foundation.NSMakeRect(64, 64, SIZE - 128, SIZE - 128)
bg = AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(rect, 180, 180)
AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(0.13, 0.13, 0.16, 1.0).setFill()
bg.fill()
text = Foundation.NSAttributedString.alloc().initWithString_attributes_(
    "🎤", {AppKit.NSFontAttributeName: AppKit.NSFont.systemFontOfSize_(560)}
)
ts = text.size()
text.drawAtPoint_(((SIZE - ts.width) / 2, (SIZE - ts.height) / 2))
img.unlockFocus()
rep = AppKit.NSBitmapImageRep.imageRepWithData_(img.TIFFRepresentation())
png = rep.representationUsingType_properties_(AppKit.NSBitmapImageFileTypePNG, None)
png.writeToFile_atomically_(sys.argv[1], True)
PYEOF
for s in 16 32 128 256 512; do
    sips -z "$s" "$s" "$ICONSET/icon_512x512@2x.png" --out "$ICONSET/icon_${s}x${s}.png" >/dev/null
    d=$((s * 2))
    sips -z "$d" "$d" "$ICONSET/icon_512x512@2x.png" --out "$ICONSET/icon_${s}x${s}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/AppIcon.icns"
rm -rf "$(dirname "$ICONSET")"

# --- Bundle-internal interpreter (LaunchServices identity) ---------------
# Resolve the Mach-O actually running when .venv/bin/python is invoked (the
# bin/ entries are symlinks/stubs that re-exec the framework's Python.app
# binary) and copy it into the bundle, so the GUI process checks in with
# LaunchServices as TRD Speak instead of Python.
PYBIN="$(.venv/bin/python - <<'PYEOF'
import ctypes
import os

buf = ctypes.create_string_buffer(4096)
size = ctypes.c_uint32(len(buf))
ctypes.CDLL(None)._NSGetExecutablePath(buf, ctypes.byref(size))
print(os.path.realpath(buf.value.decode()))
PYEOF
)"
cp "$PYBIN" "$APP/Contents/MacOS/TRDSpeak-python"

LAUNCHER_SRC="$(mktemp -d)/launcher.c"
cat > "$LAUNCHER_SRC" <<EOF
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <spawn.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <unistd.h>

#define REPO "$REPO"

extern char **environ;
static pid_t child = 0;

static void forward(int sig) {
    if (child > 0) kill(child, sig);
}

int main(void) {
    if (chdir(REPO) != 0) {
        perror("TRD Speak: chdir " REPO);
        return 1;
    }

    char log_path[1024];
    const char *home = getenv("HOME");
    snprintf(log_path, sizeof log_path, "%s/Library/Logs/trd-speak.log",
             home ? home : "/tmp");

    struct stat st;
    if (stat(REPO "/.venv/bin/python", &st) != 0) {
        system("osascript -e 'display dialog \"TRD Speak: .venv is missing. "
               "Run ./setup.sh in the trd-speak folder first.\" "
               "buttons {\"OK\"} default button 1' >/dev/null 2>&1");
        return 1;
    }

    posix_spawn_file_actions_t fa;
    posix_spawn_file_actions_init(&fa);
    posix_spawn_file_actions_addopen(&fa, STDOUT_FILENO, log_path,
                                     O_WRONLY | O_APPEND | O_CREAT, 0644);
    posix_spawn_file_actions_adddup2(&fa, STDOUT_FILENO, STDERR_FILENO);

    /* Lets the Python side relaunch the bundle after permissions change. */
    setenv("TRDSPEAK_BUNDLE", REPO "/TRDSpeak.app", 1);

    /* The spawned binary is the bundle's copy of the CPython Mach-O, so
     * LaunchServices identifies the GUI process as TRD Speak (Dock label,
     * lsappinfo). PYTHONEXECUTABLE makes that copy adopt the .venv
     * (pyvenv.cfg lookup + sys.executable), exactly as if .venv/bin/python
     * had been run. */
    setenv("PYTHONEXECUTABLE", REPO "/.venv/bin/python", 1);

    /* -u: unbuffered stdout so the log file updates live */
    char *argv[] = {REPO "/TRDSpeak.app/Contents/MacOS/TRDSpeak-python",
                    "-u", REPO "/main.py", NULL};
    int err = posix_spawn(&child, argv[0], &fa, NULL, argv, environ);
    if (err != 0) {
        fprintf(stderr, "TRD Speak: spawn failed: %d\n", err);
        return 1;
    }

    signal(SIGTERM, forward);
    signal(SIGINT, forward);
    signal(SIGHUP, forward);

    int status = 0;
    while (waitpid(child, &status, 0) < 0 && errno == EINTR) {}
    return WIFEXITED(status) ? WEXITSTATUS(status) : 1;
}
EOF

cc -O2 -o "$APP/Contents/MacOS/TRDSpeak" "$LAUNCHER_SRC"
rm -rf "$(dirname "$LAUNCHER_SRC")"

codesign --force --deep --sign - "$APP"

cat <<EOF

Built $APP

Start it:        open "$APP"
Watch its logs:  tail -f ~/Library/Logs/trd-speak.log
Stop it:         ./stop.sh

On first launch the menu bar icon shows ⚠️ — click it and the menu guides
you through the three permissions (Microphone, Input Monitoring,
Accessibility). The app restarts itself once all are granted.

Tip: add TRD Speak to System Settings -> General -> Login Items to start
dictation automatically at login.
EOF
