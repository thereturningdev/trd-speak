"""Secure Keyboard Entry detection (issue #25).

When ANY process enables Secure Keyboard Entry — Terminal/iTerm2's "Secure
Keyboard Entry" menu option, a password field, loginwindow — macOS blocks
EVERY CoreGraphics event tap on the machine from receiving keystrokes (Apple
TN2150). A fully-permissioned, healthy tap then delivers zero events: from
inside this app that looks identical to every other tap failure this app has
already fought (dead battery of causes). This module gives flow.menubar's 2 s
poll a cheap, permission-free way to tell the two apart and name the culprit.

Mirrors flow/permissions.py's style: thin, individually monkeypatchable
wrapper functions over a ctypes/PyObjC call, never a bare inline call
scattered through the caller.
"""

import ctypes

try:  # Guarded: importable (and is_enabled() still usable) without AppKit.
    import AppKit
except Exception:  # pragma: no cover - depends on the installed environment
    AppKit = None

try:  # Guarded: importable without Quartz (culprit resolution degrades to None).
    import Quartz
except Exception:  # pragma: no cover - depends on the installed environment
    Quartz = None

_CARBON = "/System/Library/Frameworks/Carbon.framework/Carbon"

# CGSessionCopyCurrentDictionary carries this key (as a plain string — it is
# NOT bridged as a Quartz constant) only while Secure Input is on, mapping to
# the pid of the process that enabled it. Background enablers can report a
# missing or stale pid (see the openradar issue linked from #25) — resolution
# below treats that as "unknown", never as a crash.
_PID_KEY = "kCGSSessionSecureInputPID"

#: Shown in the menu row when the culprit process cannot be resolved.
GENERIC_BLOCKER = "an app"


def _carbon() -> ctypes.CDLL:
    return ctypes.cdll.LoadLibrary(_CARBON)


def is_enabled() -> bool:
    """True if Secure Keyboard Entry is on ANYWHERE on the system right now.

    Never raises: a ctypes/library failure reads as "not enabled" rather than
    crashing the poll (a false negative here just means the diagnostic row
    does not appear — it never blocks dictation the way a crash would).
    """
    try:
        lib = _carbon()
        lib.IsSecureEventInputEnabled.restype = ctypes.c_bool
        return bool(lib.IsSecureEventInputEnabled())
    except Exception as exc:
        print(f"[secure_input] IsSecureEventInputEnabled() failed: {exc}")
        return False


def culprit_pid() -> int | None:
    """The pid CGSessionCopyCurrentDictionary blames for Secure Input, or
    None if it is absent/invalid/unavailable. Never raises."""
    if Quartz is None:
        return None
    try:
        session = Quartz.CGSessionCopyCurrentDictionary()
        if not session:
            return None
        pid = session.get(_PID_KEY)
        if pid is None:
            return None
        pid = int(pid)
        return pid if pid > 0 else None
    except Exception as exc:
        print(f"[secure_input] CGSessionCopyCurrentDictionary() failed: {exc}")
        return None


def culprit_name() -> str | None:
    """Best-effort localized name of the process that enabled Secure Input,
    or None when it cannot be resolved (missing/invalid pid, a background
    enabler with no NSRunningApplication entry, pid of an exited/zombie
    process, AppKit unavailable). Never raises."""
    if AppKit is None:
        return None
    pid = culprit_pid()
    if pid is None:
        return None
    try:
        app = AppKit.NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
        if app is None:
            return None
        name = app.localizedName()
        return str(name) if name else None
    except Exception as exc:
        print(f"[secure_input] NSRunningApplication lookup failed: {exc}")
        return None


def describe_culprit() -> str:
    """Human-readable culprit for the menu row: the resolved app name, or the
    generic fallback when resolution fails for any reason.

    culprit_name()/culprit_pid() already guard their own steps, but this is
    the seam every caller actually uses, so it gets its own belt-and-suspenders
    guard too: it must NEVER raise, whatever changes underneath it later.
    """
    try:
        return culprit_name() or GENERIC_BLOCKER
    except Exception as exc:
        print(f"[secure_input] describe_culprit() failed: {exc}")
        return GENERIC_BLOCKER
