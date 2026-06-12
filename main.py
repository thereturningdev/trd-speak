"""Entry point for local-flow."""

import argparse
import fcntl
import pathlib
import subprocess
import sys

_LOCK_PATH = pathlib.Path(__file__).resolve().parent / ".localflow.lock"
_lock_file = None  # module-level: keeps the lock fd alive for the process lifetime


def _acquire_single_instance_lock() -> bool:
    """Try to take an exclusive non-blocking lock; False if another instance holds it."""
    global _lock_file
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


def main() -> None:
    # Single-instance guard before anything else: System Settings' own
    # quit-and-reopen races our self-restart, and two instances must
    # collapse to one cleanly.
    if not _acquire_single_instance_lock():
        print("LocalFlow is already running")
        _notify("LocalFlow is already running")
        sys.exit(0)

    from flow.config import load_config
    from flow.menubar import run

    parser = argparse.ArgumentParser(
        description="local-flow: local push-to-talk dictation for macOS"
    )
    parser.add_argument(
        "--config", metavar="PATH", default=None, help="path to config.toml"
    )
    args = parser.parse_args()
    run(load_config(args.config))


if __name__ == "__main__":
    main()
