"""Per-build storage locations (config, engine state, single-instance lock, log).

The dev build ("TRD Speak Dev") and the production build ("TRD Speak") must NOT
share config, engine state, the single-instance lock, or the log file. Sharing
them lets the two builds overwrite each other's settings and append to one log,
so a shortcut set in one build silently appears in the other and it is
impossible to tell which binary actually ran.

ONLY the dev build is relocated, under "TRD Speak Dev". The production build and
a ./run.sh source run keep the historical "TRD Speak" location unchanged, so an
already-installed production build's existing config is never orphaned. The dev
build is identified exactly as flow.menubar does it: its bundle id ends ".dev".
"""

import os
import pathlib
import sys


def _is_dev_bundle() -> bool:
    """True only for the dev build (bundle id ends in '.dev').

    Read from the frozen app's own Info.plist with plistlib (stdlib), so this
    module stays AppKit-free — main.py needs these paths before AppKit loads. A
    non-frozen (./run.sh) run has no bundle and is never the dev build.
    """
    if not getattr(sys, "frozen", False):
        return False
    try:
        import plistlib

        info = (
            pathlib.Path(sys.executable).resolve().parents[2]
            / "Contents"
            / "Info.plist"
        )
        with open(info, "rb") as f:
            bundle_id = plistlib.load(f).get("CFBundleIdentifier") or ""
        return str(bundle_id).endswith(".dev")
    except Exception:
        return False


def _app_name(is_dev: bool) -> str:
    """The storage name. Production and source runs keep "TRD Speak" (never
    move an installed build's config); only the dev build is isolated."""
    return "TRD Speak Dev" if is_dev else "TRD Speak"


def _derive(app_name: str) -> dict:
    """All per-build storage locations for a storage name. Pure function (no
    I/O, creates nothing) so it is directly unit-testable."""
    slug = app_name.lower().replace(" ", "-")  # "TRD Speak Dev" -> "trd-speak-dev"
    support = (
        pathlib.Path(os.path.expanduser("~/Library/Application Support")) / app_name
    )
    return {
        "support": support,
        "log": os.path.expanduser(f"~/Library/Logs/{slug}.log"),
        "lock": support / ".trd-speak.lock",
        "hotkeys": support / "hotkeys.json",
        "engine": support / "engine",
        "dictations": support / "dictations.json",
        "dictionary": support / "dictionary.json",
    }


IS_DEV = _is_dev_bundle()
APP_NAME = _app_name(IS_DEV)
_PATHS = _derive(APP_NAME)

APP_SUPPORT_DIR = _PATHS["support"]
LOG_PATH = _PATHS["log"]
LOCK_PATH = _PATHS["lock"]
HOTKEYS_PATH = _PATHS["hotkeys"]
ENGINE_PATH = _PATHS["engine"]
DICTATIONS_PATH = _PATHS["dictations"]
DICTIONARY_PATH = _PATHS["dictionary"]
