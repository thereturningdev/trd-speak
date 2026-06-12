"""Startup checks for the three macOS permissions the app depends on.

macOS gates the pieces of this app behind SEPARATE permissions:
  - Input Monitoring (TCC ListenEvent): required to observe the global hotkey.
  - Accessibility (TCC PostEvent): required to synthesize the Cmd+V paste.
  - Microphone: required to record audio for transcription.
Crucially, a missing Accessibility grant does NOT raise an error — CGEventPost
silently drops the keystroke — so we must preflight it and warn loudly.
"""

import ctypes
from typing import NamedTuple

try:  # Guarded: keep working (mic state "unknown") if the framework is absent.
    import AVFoundation
except Exception:  # pragma: no cover - depends on the installed environment
    AVFoundation = None

_CG = "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics"


class Permission(NamedTuple):
    """One permission the UI can render: key, display name, Settings anchor."""

    key: str  # "listen" | "post" | "mic"
    name: str  # human-readable display name
    anchor: str  # System Settings pane anchor (Privacy_*)


#: Registry the menu bar UI renders — one row per permission, in onboarding
#: (step) order. The order is fixed by OS behavior: Microphone and
#: Accessibility apply live to the running process, while a fresh Input
#: Monitoring grant may only be honored by a new process (System Settings
#: itself shows "quit and reopen" for it), so the restart-requiring step goes
#: LAST. Keys: "mic" (Microphone), "post" (Accessibility), "listen" (Input
#: Monitoring).
PERMISSIONS: list[Permission] = [
    Permission("mic", "Microphone", "Privacy_Microphone"),
    Permission("post", "Accessibility", "Privacy_Accessibility"),
    Permission("listen", "Input Monitoring", "Privacy_ListenEvent"),
]


def _coregraphics() -> ctypes.CDLL:
    return ctypes.cdll.LoadLibrary(_CG)


def can_listen() -> bool:
    """True if this process may observe global key events (Input Monitoring)."""
    return bool(_coregraphics().CGPreflightListenEventAccess())


def can_post() -> bool:
    """True if this process may synthesize key events (Accessibility)."""
    return bool(_coregraphics().CGPreflightPostEventAccess())


def mic_status() -> str:
    """Microphone TCC state: "granted" | "undetermined" | "denied" | "unknown".

    "unknown" means AVFoundation is unavailable (or errored), so the caller
    should fall back to opening the Microphone pane in System Settings.
    """
    if AVFoundation is None:
        return "unknown"
    try:
        status = AVFoundation.AVCaptureDevice.authorizationStatusForMediaType_(
            AVFoundation.AVMediaTypeAudio
        )
    except Exception:
        return "unknown"
    if status == 3:  # AVAuthorizationStatusAuthorized
        return "granted"
    if status == 0:  # AVAuthorizationStatusNotDetermined
        return "undetermined"
    if status in (1, 2):  # Restricted / Denied
        return "denied"
    return "unknown"


def request_mic() -> None:
    """Trigger macOS's own Microphone permission prompt.

    No-op unless AVFoundation is available AND the state is undetermined
    (macOS only ever shows the dialog in that state).
    """
    if AVFoundation is None or mic_status() != "undetermined":
        return

    def _done(_granted: bool) -> None:  # completion runs on an arbitrary queue
        pass

    try:
        AVFoundation.AVCaptureDevice.requestAccessForMediaType_completionHandler_(
            AVFoundation.AVMediaTypeAudio, _done
        )
    except Exception:
        pass


def request_listen() -> None:
    """Trigger ONLY the Input Monitoring prompt / Settings registration.

    Fires macOS's own dialog (with its "Open System Settings" button) the
    first time; later calls just (re-)register the app in the pane. Never
    touches the Accessibility prompt.
    """
    cg = _coregraphics()
    if not cg.CGPreflightListenEventAccess():
        cg.CGRequestListenEventAccess()


def request_post() -> None:
    """Trigger ONLY the Accessibility prompt / Settings registration.

    Fires macOS's own dialog (with its "Open System Settings" button) the
    first time; later calls just (re-)register the app in the pane. Never
    touches the Input Monitoring prompt.
    """
    cg = _coregraphics()
    if not cg.CGPreflightPostEventAccess():
        cg.CGRequestPostEventAccess()


def request_permissions() -> None:
    """Trigger BOTH system permission prompts (compatibility wrapper).

    Prefer request_listen()/request_post(): firing both at once stacks two
    system dialogs, and answering one dismisses the other.
    """
    request_listen()
    request_post()


def report(
    terminal_hint: str = "LocalFlow (app mode) or your terminal app (./run.sh)",
) -> bool:
    """Print actionable warnings for any missing permission.

    Print-only: never fires a system prompt or opens System Settings — the
    menu bar onboarding owns when prompts appear (one step at a time).
    Returns True if everything needed is granted.
    """
    listen, post, mic = can_listen(), can_post(), mic_status()
    # "unknown" (AVFoundation unavailable) must not block startup.
    mic_ok = mic in ("granted", "unknown")
    if listen and post and mic_ok:
        return True
    print("\n*** MISSING macOS PERMISSIONS — dictation will not work ***")
    if not listen:
        print(
            "  - Input Monitoring (needed to detect the hotkey):\n"
            "      System Settings -> Privacy & Security -> Input Monitoring\n"
            f"      -> enable {terminal_hint}"
        )
    if not post:
        print(
            "  - Accessibility (needed to paste — WITHOUT IT THE PASTE FAILS\n"
            "    SILENTLY even though recording and transcription work):\n"
            "      System Settings -> Privacy & Security -> Accessibility\n"
            f"      -> enable {terminal_hint}"
        )
    if not mic_ok:
        print(
            f"  - Microphone (needed to record audio — currently {mic}):\n"
            "      System Settings -> Privacy & Security -> Microphone\n"
            f"      -> enable {terminal_hint}"
        )
    print(
        "  Grant them one step at a time from the app's own menu: click the\n"
        "  ⚠️ LocalFlow icon in the menu bar and follow the steps.\n"
    )
    return False
