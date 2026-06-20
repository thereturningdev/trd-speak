"""Entry point for local-flow."""

import argparse
import fcntl
import os
import pathlib
import subprocess
import sys

# User-writable support dir. The lock MUST NOT live inside the app bundle: a
# signed/notarized .app is read-only, so writing the lock next to __file__ (as a
# source checkout does) fails once bundled. This path works for both modes.
_APP_SUPPORT = pathlib.Path(
    os.path.expanduser("~/Library/Application Support/LocalFlow")
)
_LOCK_PATH = _APP_SUPPORT / ".localflow.lock"
_lock_file = None  # module-level: keeps the lock fd alive for the process lifetime


def _acquire_single_instance_lock() -> bool:
    """Try to take an exclusive non-blocking lock; False if another instance holds it."""
    global _lock_file
    _APP_SUPPORT.mkdir(parents=True, exist_ok=True)
    _lock_file = open(_LOCK_PATH, "w")
    try:
        fcntl.flock(_lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        _lock_file.close()
        _lock_file = None
        return False


def _notify(message: str) -> None:
    """Post a macOS notification (never a modal)."""
    escaped = message.replace("\\", "\\\\").replace('"', '\\"')
    subprocess.Popen([
        "osascript", "-e",
        f'display notification "{escaped}" with title "LocalFlow"',
    ])


def _selftest() -> int:
    """Import every heavy/native dependency to prove a frozen bundle is
    self-contained. Returns 0 on success. Used by the release build's smoke
    test (`LocalFlow --selftest`), not by normal startup."""
    import av  # noqa: F401
    import ctranslate2  # noqa: F401
    import faster_whisper  # noqa: F401
    import numpy  # noqa: F401
    import onnxruntime  # noqa: F401
    import sounddevice  # noqa: F401
    import tokenizers  # noqa: F401
    from flow import app, engines, menubar  # noqa: F401

    print("selftest OK")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="local-flow: local push-to-talk dictation for macOS"
    )
    parser.add_argument(
        "--config", metavar="PATH", default=None, help="path to config.toml"
    )
    parser.add_argument(
        "--selftest", action="store_true",
        help="import all dependencies and exit (release build smoke test)",
    )
    args = parser.parse_args()

    if args.selftest:
        sys.exit(_selftest())

    # Single-instance guard: System Settings' own quit-and-reopen races our
    # self-restart, and two instances must collapse to one cleanly.
    if not _acquire_single_instance_lock():
        print("LocalFlow is already running")
        _notify("LocalFlow is already running")
        sys.exit(0)

    from flow.config import load_config
    from flow.menubar import run

    run(load_config(args.config))


if __name__ == "__main__":
    main()
