"""Entry point for TRD Speak."""

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
    os.path.expanduser("~/Library/Application Support/TRD Speak")
)
_LOCK_PATH = _APP_SUPPORT / ".trd-speak.lock"
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

    print(f"selftest OK ({pa_version}; {len(devices)} devices; {audio})")
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
    args = parser.parse_args()

    if args.selftest:
        sys.exit(_selftest())
    if args.selftest_model:
        sys.exit(_selftest_model())

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
