"""Configuration window: record the dictate + re-paste global shortcuts.

A programmatically-built NSWindow (no nib). NOT unit-tested — verified by
import (`python -c "import flow.settings_window"`) plus manual run, consistent
with flow.menubar already being un-unit-tested.

The crux is recording a shortcut by *pressing* it. The global event taps are
listen-only, so flow.app.App.suspend_hotkeys() stops both for the window's whole
lifetime; while open, a recorder field's local NSEvent monitor is the only
listener active, so pressing a combo to record it never self-triggers a real
dictation. Captured NSEvent keycodes/flags are mapped to canonical tokens via
flow.hotkey.token_for_keycode / modifier_tokens_from_flags (the same tables the
live listener matches against). On Save the combos are validated, applied live
(App.set_hotkeys), persisted (hotkey_state.save) and reflected in the menu
header (MenuBar.update_combo); on Cancel/close the unchanged taps are resumed.

All of this runs on the AppKit main thread (the menu action thread), so no
locking is needed beyond what App already documents.
"""

from __future__ import annotations

import AppKit
import Foundation
import objc
from Foundation import NSObject

from flow import hotkey_state
from flow.hotkey import (
    modifier_tokens_from_flags,
    token_for_keycode,
    validate_combo,
)

# Stable canonical order for display + the stored token list, so the same
# physical combo always yields the same list and glyph string. Modifiers first
# in this order, then the (single) non-modifier key.
_MODIFIER_ORDER = ["cmd", "ctrl", "alt", "shift"]
_GLYPHS = {"cmd": "⌘", "alt": "⌥", "ctrl": "⌃", "shift": "⇧"}

_ESC_KEYCODE = 53


def _order_combo(modifiers: set[str], key: str | None) -> list[str]:
    """Canonical token order: modifiers in _MODIFIER_ORDER, then the key."""
    ordered = [m for m in _MODIFIER_ORDER if m in modifiers]
    if key is not None and key not in _GLYPHS:
        ordered.append(key)
    return ordered


def _glyph_string(keys: list[str]) -> str:
    """macOS glyph display for a token list (⌘⌥⌃⇧ + uppercased key)."""
    if not keys:
        return "(none)"
    out = []
    for token in keys:
        out.append(_GLYPHS.get(token, token.upper()))
    return "".join(out)


class _Recorder(NSObject):
    """One shortcut field: a clickable NSButton that records a combo by press.

    Not an NSButton subclass (PyObjC subclassing of controls is fiddly to keep
    GC-safe); instead this owns a borderless NSButton wired to its own
    record-toggle action. Internal state: the captured token list, the prior
    value (Esc/new-click restore), and the live NSEvent local-monitor handle.
    """

    def initWithFrame_(self, frame):
        self = objc.super(_Recorder, self).init()
        if self is None:
            return None
        self._keys: list[str] = []
        self._prior: list[str] = []
        self._monitor = None
        self._recording = False
        # Per-recording capture state.
        self._peak_modifiers: set[str] = set()
        self._got_key = False
        self.button = AppKit.NSButton.alloc().initWithFrame_(frame)
        self.button.setBezelStyle_(AppKit.NSBezelStyleRounded)
        self.button.setTarget_(self)
        self.button.setAction_("toggleRecording:")
        self._refresh_title()
        return self

    # -- public API used by the controller --------------------------------

    @objc.python_method
    def keys(self) -> list[str]:
        """The captured combo as canonical tokens."""
        return list(self._keys)

    @objc.python_method
    def set_keys(self, keys: list[str]) -> None:
        """Populate the field with a combo (used on open)."""
        self._keys = list(keys)
        self._prior = list(keys)
        self._refresh_title()

    @objc.python_method
    def cancel_recording(self) -> None:
        """Stop an in-progress recording without changing the value."""
        if self._recording:
            self._finish_recording(restore=True)

    # -- recording lifecycle ----------------------------------------------

    def toggleRecording_(self, _sender) -> None:
        if self._recording:
            # A second click while recording cancels and restores.
            self._finish_recording(restore=True)
            return
        self._begin_recording()

    @objc.python_method
    def _begin_recording(self) -> None:
        self._recording = True
        self._prior = list(self._keys)
        self._peak_modifiers = set()
        self._got_key = False
        self.button.setTitle_("Recording… press a shortcut")
        mask = AppKit.NSEventMaskKeyDown | AppKit.NSEventMaskFlagsChanged
        self._monitor = AppKit.NSEvent.addLocalMonitorForEventsMatchingMask_handler_(
            mask, self._handle_event
        )

    @objc.python_method
    def _finish_recording(self, *, restore: bool) -> None:
        self._recording = False
        if self._monitor is not None:
            AppKit.NSEvent.removeMonitor_(self._monitor)
            self._monitor = None
        if restore:
            self._keys = list(self._prior)
        self._refresh_title()

    @objc.python_method
    def _commit(self, keys: list[str]) -> None:
        """Accept a finalized combo and stop recording."""
        self._keys = keys
        self._finish_recording(restore=False)

    @objc.python_method
    def _refresh_title(self) -> None:
        self.button.setTitle_(_glyph_string(self._keys))

    # -- the local NSEvent monitor handler (returns None to swallow) -------

    @objc.python_method
    def _handle_event(self, event):
        """Capture keyDown/flagsChanged while recording; swallow the event.

        Returning None stops the event from leaking to the rest of the window
        while the field is recording.
        """
        try:
            etype = event.type()
            if etype == AppKit.NSEventTypeKeyDown:
                self._on_key_down(event)
            elif etype == AppKit.NSEventTypeFlagsChanged:
                self._on_flags_changed(event)
        except Exception as exc:  # never let the monitor die mid-recording
            print(f"Recorder monitor error: {exc}")
        return None

    @objc.python_method
    def _on_key_down(self, event) -> None:
        keycode = event.keyCode()
        if keycode == _ESC_KEYCODE:
            # Esc cancels recording and restores the prior value.
            self._finish_recording(restore=True)
            return
        key_token = token_for_keycode(keycode)
        if key_token is None or key_token in _GLYPHS:
            # Unmapped key, or a modifier reported as a keyDown — keep
            # recording; a real non-modifier key will finalize.
            return
        self._got_key = True
        modifiers = modifier_tokens_from_flags(event.modifierFlags())
        self._commit(_order_combo(modifiers, key_token))

    @objc.python_method
    def _on_flags_changed(self, event) -> None:
        held = modifier_tokens_from_flags(event.modifierFlags())
        if len(held) >= len(self._peak_modifiers) and held >= self._peak_modifiers:
            # Still pressing more (or the same) modifiers — track the peak.
            self._peak_modifiers = held
            return
        # A modifier was released. If no non-modifier key was pressed, finalize
        # the modifier-only combo as the peak set.
        if not self._got_key and self._peak_modifiers:
            self._commit(_order_combo(self._peak_modifiers, None))


class _WindowDelegate(NSObject):
    """Routes the NSWindow close (X button) to the controller's cancel path."""

    def windowWillClose_(self, _notification) -> None:
        controller = getattr(self, "_controller", None)
        if controller is not None:
            controller._on_window_will_close()


class SettingsWindowController:
    """Owns the settings NSWindow, the two recorder controls, and Save/Cancel.

    Held by a strong reference on the menu delegate (windows/controllers must
    not be GC'd). Construct once with the App logic and the MenuBar; call
    open() to (re)show it.
    """

    def __init__(self, logic, menubar) -> None:
        """logic: flow.app.App (for set/suspend/resume_hotkeys + current
        config.keys/config.repaste_keys). menubar: flow.menubar.MenuBar (for
        update_combo on save). Builds the window lazily on first open()."""
        self._logic = logic
        self._menubar = menubar
        self._window = None
        self._dictate_recorder = None
        self._repaste_recorder = None
        self._status = None
        self._delegate = None
        # True while a Save is closing the window, so the close handler does
        # not also resume the (already-live) hotkeys.
        self._saved = False

    # -- public entry point ------------------------------------------------

    def open(self) -> None:
        """Activate the app, suspend the live taps, populate both recorder
        fields with the current combos, clear the status line, and show the
        window."""
        AppKit.NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        self._logic.suspend_hotkeys()
        if self._window is None:
            self._build_window()
        self._saved = False
        self._dictate_recorder.set_keys(list(self._logic.config.keys))
        self._repaste_recorder.set_keys(list(self._logic.config.repaste_keys))
        self._set_status("")
        self._window.makeKeyAndOrderFront_(None)

    # -- window construction ----------------------------------------------

    def _build_window(self) -> None:
        width, height = 420.0, 200.0
        rect = Foundation.NSMakeRect(0, 0, width, height)
        style = (
            AppKit.NSWindowStyleMaskTitled
            | AppKit.NSWindowStyleMaskClosable
        )
        window = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            rect, style, AppKit.NSBackingStoreBuffered, False
        )
        window.setTitle_("LocalFlow Configuration")
        window.setReleasedWhenClosed_(False)
        content = window.contentView()

        # Row 1: dictate.
        self._add_label("Dictate (hold):", 24, height - 48, content)
        self._dictate_recorder = _Recorder.alloc().initWithFrame_(
            Foundation.NSMakeRect(180, height - 54, 216, 28)
        )
        content.addSubview_(self._dictate_recorder.button)

        # Row 2: re-paste.
        self._add_label("Paste last dictation:", 24, height - 90, content)
        self._repaste_recorder = _Recorder.alloc().initWithFrame_(
            Foundation.NSMakeRect(180, height - 96, 216, 28)
        )
        content.addSubview_(self._repaste_recorder.button)

        # Status / validation line.
        self._status = AppKit.NSTextField.alloc().initWithFrame_(
            Foundation.NSMakeRect(24, height - 134, width - 48, 34)
        )
        self._status.setBezeled_(False)
        self._status.setDrawsBackground_(False)
        self._status.setEditable_(False)
        self._status.setSelectable_(False)
        self._status.setStringValue_("")
        font = AppKit.NSFont.systemFontOfSize_(11)
        self._status.setFont_(font)
        content.addSubview_(self._status)

        # Save / Cancel buttons.
        cancel = AppKit.NSButton.alloc().initWithFrame_(
            Foundation.NSMakeRect(width - 200, 16, 88, 32)
        )
        cancel.setTitle_("Cancel")
        cancel.setBezelStyle_(AppKit.NSBezelStyleRounded)
        cancel.setTarget_(self._action_target())
        cancel.setAction_("cancelClicked:")
        content.addSubview_(cancel)

        save = AppKit.NSButton.alloc().initWithFrame_(
            Foundation.NSMakeRect(width - 104, 16, 88, 32)
        )
        save.setTitle_("Save")
        save.setBezelStyle_(AppKit.NSBezelStyleRounded)
        save.setKeyEquivalent_("\r")  # default button (Return)
        save.setTarget_(self._action_target())
        save.setAction_("saveClicked:")
        content.addSubview_(save)

        self._delegate = _WindowDelegate.alloc().init()
        self._delegate._controller = self
        window.setDelegate_(self._delegate)
        self._window = window
        self._window.center()

    def _add_label(self, text: str, x: float, y: float, content) -> None:
        label = AppKit.NSTextField.alloc().initWithFrame_(
            Foundation.NSMakeRect(x, y, 150, 22)
        )
        label.setStringValue_(text)
        label.setBezeled_(False)
        label.setDrawsBackground_(False)
        label.setEditable_(False)
        label.setSelectable_(False)
        content.addSubview_(label)

    def _action_target(self):
        """An ObjC target object exposing saveClicked:/cancelClicked: that
        forwards to this controller. Held on self so it is not GC'd."""
        target = getattr(self, "_button_target", None)
        if target is None:
            target = self._button_target = _ButtonTarget.alloc().init()
            target._controller = self
        return target

    # -- status helper -----------------------------------------------------

    def _set_status(self, text: str) -> None:
        if self._status is not None:
            self._status.setStringValue_(text)

    # -- Save / Cancel / close --------------------------------------------

    def save(self) -> None:
        """Validate both combos, apply + persist + refresh, then close.

        On any validation failure the message goes to the status line and the
        window stays open (nothing saved).
        """
        # End any in-progress recording so keys() reflects the final value.
        self._dictate_recorder.cancel_recording()
        self._repaste_recorder.cancel_recording()
        dictate = self._dictate_recorder.keys()
        repaste = self._repaste_recorder.keys()
        try:
            validate_combo(dictate)
        except ValueError as exc:
            self._set_status(f"Dictate: {exc}")
            return
        try:
            validate_combo(repaste)
        except ValueError as exc:
            self._set_status(f"Paste: {exc}")
            return
        if set(dictate) == set(repaste):
            self._set_status("Dictate and paste shortcuts cannot be identical.")
            return
        # Overlapping-but-different: a strict subset of the other. Non-blocking.
        d_set, r_set = set(dictate), set(repaste)
        if d_set < r_set or r_set < d_set:
            self._set_status(
                "Warning: the shortcuts overlap, but that is allowed."
            )
        self._logic.set_hotkeys(dictate, repaste)
        hotkey_state.save(dictate, repaste)
        self._menubar.update_combo(dictate, repaste)
        # set_hotkeys already started the new taps — do NOT resume on close.
        self._saved = True
        self._window.close()

    def cancel(self) -> None:
        """Discard edits and close; the close handler resumes the taps."""
        self._window.close()

    def _on_window_will_close(self) -> None:
        """Window closed (Cancel or X). Resume the unchanged taps unless a
        Save already started new ones."""
        # Make sure no monitor is left installed.
        if self._dictate_recorder is not None:
            self._dictate_recorder.cancel_recording()
        if self._repaste_recorder is not None:
            self._repaste_recorder.cancel_recording()
        if not self._saved:
            self._logic.resume_hotkeys()
        self._saved = False


class _ButtonTarget(NSObject):
    """ObjC target for the Save/Cancel buttons; forwards to the controller."""

    def saveClicked_(self, _sender) -> None:
        controller = getattr(self, "_controller", None)
        if controller is not None:
            controller.save()

    def cancelClicked_(self, _sender) -> None:
        controller = getattr(self, "_controller", None)
        if controller is not None:
            controller.cancel()
