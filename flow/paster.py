"""Clipboard-based text insertion via pbpaste/pbcopy and a synthesized Cmd+V."""

import subprocess
import time

import Quartz

_SETTLE_DELAY = 0.1  # seconds to let the clipboard settle before pasting

_V_KEYCODE = 9  # ANSI virtual keycode for 'v'


def get_clipboard() -> str:
    """Return the current clipboard text ("" if empty or non-text)."""
    try:
        result = subprocess.run(["pbpaste"], capture_output=True, timeout=5)
        return result.stdout.decode("utf-8", errors="replace")
    except (subprocess.SubprocessError, OSError):
        return ""


def set_clipboard(text: str) -> None:
    """Set the clipboard to the given text via pbcopy."""
    subprocess.run(["pbcopy"], input=text.encode("utf-8"), timeout=5, check=True)


def paste_text(text: str, restore_delay: float = 0.4) -> None:
    """Insert text at the cursor of the focused app via clipboard + Cmd+V.

    Saves the current clipboard, copies the text, synthesizes Cmd+V, then
    restores the original clipboard after restore_delay seconds.

    Known v1 limitation: only plain-text clipboard contents are preserved;
    rich text, images, and other pasteboard types are lost.
    """
    saved = get_clipboard()
    set_clipboard(text)
    time.sleep(_SETTLE_DELAY)
    _press_cmd_v()
    time.sleep(restore_delay)
    set_clipboard(saved)


def _press_cmd_v() -> None:
    """Synthesize a Cmd+V keystroke with Quartz CGEvents.

    Safe to call from a worker thread: CGEventPost does not touch the
    Text Input Services (TIS/TSM) APIs that assert main-thread on macOS 26.
    """
    down = Quartz.CGEventCreateKeyboardEvent(None, _V_KEYCODE, True)
    Quartz.CGEventSetFlags(down, Quartz.kCGEventFlagMaskCommand)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, down)
    up = Quartz.CGEventCreateKeyboardEvent(None, _V_KEYCODE, False)
    Quartz.CGEventSetFlags(up, Quartz.kCGEventFlagMaskCommand)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, up)
