"""Entry point for TRD Speak."""

import argparse
import fcntl
import os
import subprocess
import sys

from flow import paths

# User-writable support dir. The lock MUST NOT live inside the app bundle: a
# signed/notarized .app is read-only, so writing the lock next to __file__ (as a
# source checkout does) fails once bundled. flow.paths makes this per-build
# (dev vs production), so the two builds never share a lock or step on config.
_APP_SUPPORT = paths.APP_SUPPORT_DIR
_LOCK_PATH = paths.LOCK_PATH
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
        f'display notification "{escaped}" with title "TRD Speak"',
    ])


def _selftest() -> int:
    """Import every heavy/native dependency, then exercise PortAudio at runtime,
    to prove a frozen bundle is self-contained. Returns 0 on success. Used by the
    release build's smoke test (`TRDSpeak --selftest`), not by normal startup."""
    import av  # noqa: F401
    import ctranslate2  # noqa: F401
    import faster_whisper  # noqa: F401
    import numpy  # noqa: F401
    import onnxruntime  # noqa: F401
    import sounddevice as sd
    import tokenizers  # noqa: F401
    from flow import app, engines, menubar  # noqa: F401

    # Exercise PortAudio at runtime, not just at import: query_devices() runs
    # Pa_Initialize + device enumeration, and check_input_settings() negotiates
    # the Recorder's format (16 kHz mono float32) with CoreAudio. Together they
    # prove the bundled libportaudio actually works, not merely that it loaded.
    # Opening a stream / reading frames needs Microphone (TCC) permission and is
    # covered by clean-machine QA, so it is intentionally not done here.
    pa_version = sd.get_portaudio_version()[1]
    devices = sd.query_devices()
    try:
        default_input = sd.query_devices(kind="input")["name"]
        sd.check_input_settings(samplerate=16000, channels=1, dtype="float32")
        audio = f"input='{default_input}' OK"
    except Exception as exc:  # no input device on this machine, etc.
        audio = f"no usable input device ({exc})"

    # Prove the PyObjC AV stack actually loaded. A half-collected AVFoundation
    # still imports but can't answer, so mic_status() returns "unknown" and the
    # Microphone prompt never fires (see TRDSpeak.spec). Fail loudly if so.
    import AVFoundation  # noqa: F401

    from flow import permissions

    mic = permissions.mic_status()
    if mic == "unknown":
        print("selftest FAILED: AVFoundation unavailable — Microphone prompt cannot fire")
        return 1

    # Prove the vendored Carbon hotkey bridge (flow/_vendor/quickmachotkey,
    # issue #23) is bundled AND answers at runtime: read the modifier
    # constants from the real HIToolbox framework and resolve the event
    # dispatcher target. A half-bundled bridge imports but cannot answer, and
    # every key+modifier shortcut would then be dead in the frozen app. No
    # hotkey is registered here (that would briefly swallow a chord
    # system-wide on the user's machine).
    from flow._vendor.quickmachotkey import _MinimalHIToolbox as _hitoolbox
    from flow import carbon_hotkey  # noqa: F401  (the backend itself imports)

    if (
        _hitoolbox.cmdKey,
        _hitoolbox.shiftKey,
        _hitoolbox.optionKey,
        _hitoolbox.controlKey,
    ) != (0x100, 0x200, 0x800, 0x1000) or _hitoolbox.GetEventDispatcherTarget() is None:
        print("selftest FAILED: Carbon HIToolbox hotkey bridge unavailable")
        return 1

    print(f"selftest OK ({pa_version}; {len(devices)} devices; {audio}; mic={mic}; "
          f"carbon=OK)")
    return 0


def _selftest_model() -> int:
    """Load the (embedded) default model and run one transcription, to prove a
    frozen bundle works offline. Run with HF offline env + an empty HF cache so
    a download is impossible — success means the model came from the bundle."""
    import numpy as np

    from flow.config import Config
    from flow.engines import make_transcriber

    transcriber = make_transcriber("whisper", Config())
    transcriber.load()
    out = transcriber.transcribe(np.zeros(16000, dtype=np.float32))
    print(f"model OK (transcript={out!r})")
    return 0


def _preflight_tcc() -> int:
    """Print "<listen> <post>" (1/0 each) for Input Monitoring and Accessibility.

    CGPreflightListenEventAccess / CGPreflightPostEventAccess cache their result
    for a process's whole lifetime, so a long-running app can never observe a
    grant made AFTER launch. The menu bar's poll therefore re-checks by running
    THIS in a fresh child of the same signed binary (same TCC identity, uncached
    answer). It MUST run before _redirect_frozen_logs() so the line reaches the
    parent's captured pipe, not the log file, and writes to fd 1 directly
    because a windowed (console=False) build's sys.stdout may be a stub.
    """
    import ctypes

    cg = ctypes.cdll.LoadLibrary(
        "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics"
    )
    listen = int(bool(cg.CGPreflightListenEventAccess()))
    post = int(bool(cg.CGPreflightPostEventAccess()))
    os.write(1, f"{listen} {post}\n".encode())
    return 0


def _redirect_frozen_logs() -> None:
    """Send stdout/stderr to ~/Library/Logs/trd-speak.log in the frozen app.

    A Finder-launched .app has no terminal, and — unlike the dev launcher, which
    redirects in the shell — the PyInstaller build had nothing capturing its
    output, so prints, tracebacks and the menu's "Open Log" went nowhere. Mirror
    the dev launcher: append to the log file, line-buffered. dup2 onto fds 1/2 so
    native (C-level) writes land there too; a windowed build's sys.stdout may be
    a devnull stub, so don't rely on it.
    """
    if not getattr(sys, "frozen", False):
        return
    log_path = paths.LOG_PATH
    try:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        f = open(log_path, "a", buffering=1)
        os.dup2(f.fileno(), 1)
        os.dup2(f.fileno(), 2)
        sys.stdout = f
        sys.stderr = f
    except Exception:
        pass


def main() -> None:
    from flow import __version__

    parser = argparse.ArgumentParser(
        description="TRD Speak: local push-to-talk dictation for macOS"
    )
    parser.add_argument(
        "--version", action="version", version=f"TRD Speak {__version__}"
    )
    parser.add_argument(
        "--config", metavar="PATH", default=None, help="path to config.toml"
    )
    parser.add_argument(
        "--selftest", action="store_true",
        help="import all dependencies and exit (release build smoke test)",
    )
    parser.add_argument(
        "--selftest-model", action="store_true",
        help="load the embedded model and transcribe once, then exit",
    )
    parser.add_argument(
        "--preflight", action="store_true",
        help="print the Input Monitoring/Accessibility preflight state and exit "
             "(internal: the menu bar's fresh-process permission re-check)",
    )
    args = parser.parse_args()

    if args.selftest:
        sys.exit(_selftest())
    if args.selftest_model:
        sys.exit(_selftest_model())
    # Before _redirect_frozen_logs(): the result must reach the parent's pipe.
    if args.preflight:
        sys.exit(_preflight_tcc())

    _redirect_frozen_logs()

    # Single-instance guard: System Settings' own quit-and-reopen races our
    # self-restart, and two instances must collapse to one cleanly.
    if not _acquire_single_instance_lock():
        print("TRD Speak is already running")
        _notify("TRD Speak is already running")
        sys.exit(0)

    from flow.config import load_config
    from flow.menubar import run

    run(load_config(args.config))


if __name__ == "__main__":
    # PyInstaller-frozen apps MUST call this before any multiprocessing use:
    # ctranslate2/onnxruntime spawn a resource-tracker child that re-launches
    # this executable, and without freeze_support the child falls through to
    # argparse and errors. No-op in the parent and in a source run.
    import multiprocessing

    multiprocessing.freeze_support()
    main()
