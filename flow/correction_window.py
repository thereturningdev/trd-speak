"""Correction window: edit the last dictation and teach the app new rules.

A programmatically-built NSWindow (no nib). NOT unit-tested — verified by
import (`python -c "import flow.correction_window"`) plus manual run, consistent
with flow.settings_window and flow.menubar already being un-unit-tested.

The user opens this window from the menu or via the correction tap. It pre-fills
an editable NSTextView with the most-recent dictation text. As the user types,
a live preview label shows what rules/vocab would be derived (via
flow.learning.derive). On Save the controller calls app.learn(original,
current_text); on Cancel/Esc/X it discards and resumes the global taps unchanged.

app.suspend_hotkeys() is called before the window is shown; app.resume_hotkeys()
is guaranteed on every close path (Save, Cancel, Esc, red X button) exactly once.

All of this runs on the AppKit main thread (the menu action thread), so no
locking is needed beyond what App already documents.
"""

from __future__ import annotations

import AppKit
import Foundation
import objc
from Foundation import NSObject

from flow import common_words, learning

_ESC_KEYCODE = 53


def open_correction_window(app) -> None:
    """Entry point: called by the menubar (or correction tap) on the main thread.

    If there is no recent dictation, shows a brief NSAlert and returns.
    Otherwise constructs (or re-uses) a CorrectionWindowController and opens it.
    """
    original = app.history.latest()
    if original is None:
        alert = AppKit.NSAlert.alloc().init()
        alert.setMessageText_("Nothing to correct yet.")
        alert.setInformativeText_(
            "Dictate something first, then use this menu item to correct it."
        )
        alert.addButtonWithTitle_("OK")
        alert.runModal()
        return

    # Stash the controller on the app object so it is not GC'd while open.
    # We create a fresh controller each call so the window always reflects the
    # latest dictation (history.latest() may change between calls).
    controller = CorrectionWindowController(app)
    app._correction_window_controller = controller
    controller.open(original)


class _CorrectionTextViewDelegate(NSObject):
    """NSTextView delegate that calls back into the controller on every change."""

    def textDidChange_(self, _notification) -> None:
        controller = getattr(self, "_controller", None)
        if controller is not None:
            controller._refresh_preview()


class _CorrectionWindowDelegate(NSObject):
    """Routes the NSWindow close (X button) to the controller's cancel path."""

    def windowWillClose_(self, _notification) -> None:
        controller = getattr(self, "_controller", None)
        if controller is not None:
            controller._on_window_will_close()


class _CorrectionButtonTarget(NSObject):
    """ObjC target for the Save/Cancel buttons; forwards to the controller."""

    def saveClicked_(self, _sender) -> None:
        controller = getattr(self, "_controller", None)
        if controller is not None:
            controller.save()

    def cancelClicked_(self, _sender) -> None:
        controller = getattr(self, "_controller", None)
        if controller is not None:
            controller.cancel()


class CorrectionWindowController:
    """Owns the correction NSWindow and its Save/Cancel logic.

    Held by a strong reference on the App object (app._correction_window_controller)
    while the window is open; re-created on each open() call so the window always
    reflects the latest dictation.
    """

    def __init__(self, app) -> None:
        self._app = app
        self._original: str = ""
        self._window = None
        self._text_view = None
        self._preview_field = None
        self._delegate = None
        self._button_target = None
        self._text_delegate = None
        # True while a Save is closing the window so the close handler does not
        # also resume (already-live) hotkeys.
        self._saved = False

    # -- public entry point ---------------------------------------------------

    def open(self, original: str) -> None:
        """Suspend hotkeys, build the window, pre-fill the text, and show it."""
        self._original = original
        self._saved = False
        AppKit.NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        self._app.suspend_hotkeys()
        if self._window is None:
            self._build_window()
        # Pre-fill the editable text view with the original dictation.
        self._text_view.setString_(original)
        self._refresh_preview()
        self._window.makeKeyAndOrderFront_(None)

    # -- window construction --------------------------------------------------

    def _build_window(self) -> None:
        width, height = 520.0, 340.0
        rect = Foundation.NSMakeRect(0, 0, width, height)
        style = (
            AppKit.NSWindowStyleMaskTitled
            | AppKit.NSWindowStyleMaskClosable
            | AppKit.NSWindowStyleMaskResizable
        )
        window = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            rect, style, AppKit.NSBackingStoreBuffered, False
        )
        window.setTitle_("Correct last dictation")
        window.setReleasedWhenClosed_(False)
        content = window.contentView()

        # -- "Original / edit:" label -----------------------------------------
        self._add_label("Edit the dictation below:", 20, height - 36, content, bold=True)

        # -- Editable NSTextView inside an NSScrollView -----------------------
        scroll_top = height - 46
        scroll_height = 120.0
        scroll_rect = Foundation.NSMakeRect(20, scroll_top - scroll_height, width - 40, scroll_height)
        scroll = AppKit.NSScrollView.alloc().initWithFrame_(scroll_rect)
        scroll.setHasVerticalScroller_(True)
        scroll.setAutoresizingMask_(
            AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable
        )
        scroll.setBorderType_(AppKit.NSBezelBorder)

        text_rect = Foundation.NSMakeRect(0, 0, scroll_rect.size.width, scroll_rect.size.height)
        text_view = AppKit.NSTextView.alloc().initWithFrame_(text_rect)
        text_view.setEditable_(True)
        text_view.setSelectable_(True)
        text_view.setRichText_(False)
        text_view.setFont_(AppKit.NSFont.systemFontOfSize_(13))
        text_view.setAutoresizingMask_(AppKit.NSViewWidthSizable)

        # Wire up the delegate for live preview.
        self._text_delegate = _CorrectionTextViewDelegate.alloc().init()
        self._text_delegate._controller = self
        text_view.setDelegate_(self._text_delegate)

        scroll.setDocumentView_(text_view)
        content.addSubview_(scroll)
        self._text_view = text_view

        # -- "Will learn:" preview label --------------------------------------
        preview_y = scroll_top - scroll_height - 16
        self._add_label("What will be learned:", 20, preview_y, content, bold=True)

        preview_rect = Foundation.NSMakeRect(20, preview_y - 80, width - 40, 76)
        preview = AppKit.NSTextField.alloc().initWithFrame_(preview_rect)
        preview.setBezeled_(False)
        preview.setDrawsBackground_(True)
        preview.setBackgroundColor_(AppKit.NSColor.controlBackgroundColor())
        preview.setEditable_(False)
        preview.setSelectable_(False)
        preview.setStringValue_("No new rule from this edit.")
        preview.setFont_(AppKit.NSFont.monospacedSystemFontOfSize_(11, AppKit.NSFontWeightRegular))
        preview.setLineBreakMode_(AppKit.NSLineBreakByWordWrapping)
        preview.cell().setWraps_(True)
        content.addSubview_(preview)
        self._preview_field = preview

        # -- Cancel / Save buttons --------------------------------------------
        target = self._action_target()

        cancel = AppKit.NSButton.alloc().initWithFrame_(
            Foundation.NSMakeRect(width - 200, 16, 88, 32)
        )
        cancel.setTitle_("Cancel")
        cancel.setBezelStyle_(AppKit.NSBezelStyleRounded)
        cancel.setKeyEquivalent_("\x1b")  # Esc
        cancel.setTarget_(target)
        cancel.setAction_("cancelClicked:")
        content.addSubview_(cancel)

        save = AppKit.NSButton.alloc().initWithFrame_(
            Foundation.NSMakeRect(width - 104, 16, 88, 32)
        )
        save.setTitle_("Save & learn")
        save.setBezelStyle_(AppKit.NSBezelStyleRounded)
        save.setKeyEquivalent_("\r")  # Return — default button
        save.setTarget_(target)
        save.setAction_("saveClicked:")
        content.addSubview_(save)

        # -- Window delegate (X button → cancel path) -------------------------
        self._delegate = _CorrectionWindowDelegate.alloc().init()
        self._delegate._controller = self
        window.setDelegate_(self._delegate)
        self._window = window
        self._window.center()

    def _add_label(self, text: str, x: float, y: float, content, bold: bool = False) -> None:
        label = AppKit.NSTextField.alloc().initWithFrame_(
            Foundation.NSMakeRect(x, y, 480, 22)
        )
        label.setStringValue_(text)
        label.setBezeled_(False)
        label.setDrawsBackground_(False)
        label.setEditable_(False)
        label.setSelectable_(False)
        if bold:
            label.setFont_(AppKit.NSFont.boldSystemFontOfSize_(12))
        content.addSubview_(label)

    def _action_target(self):
        """Return (and cache) the ObjC button-target object."""
        if self._button_target is None:
            self._button_target = _CorrectionButtonTarget.alloc().init()
            self._button_target._controller = self
        return self._button_target

    # -- live preview ---------------------------------------------------------

    def _refresh_preview(self) -> None:
        """Recompute the learn preview from the current text-view contents."""
        if self._preview_field is None or self._text_view is None:
            return
        current = self._text_view.string()
        result = learning.derive(self._original, current, common_words.is_common)
        lines: list[str] = []
        for rule in result.rules:
            lines.append(f"Will learn:  {rule.from_} → {rule.to}")
        if result.vocab:
            targets = ", ".join(result.vocab)
            lines.append(f"Bias vocabulary:  {targets}")
        if not lines:
            preview_text = "No new rule from this edit."
        else:
            preview_text = "\n".join(lines)
        self._preview_field.setStringValue_(preview_text)

    # -- Save / Cancel / close ------------------------------------------------

    def save(self) -> None:
        """Read the current text, call app.learn, then close (resumes hotkeys
        via _on_window_will_close with _saved=True bypassing resume)."""
        current_text = self._text_view.string() if self._text_view is not None else self._original
        self._app.learn(self._original, current_text)
        self._saved = True
        self._window.close()
        # Resume hotkeys now that learning is done (saved path).
        self._app.resume_hotkeys()

    def cancel(self) -> None:
        """Discard the edit; the close handler resumes the taps."""
        self._window.close()

    def _on_window_will_close(self) -> None:
        """Window closed (Cancel or X button).

        Resume hotkeys unless Save already did so.  This fires for every close
        path (Cancel button, X button, Esc key equivalent on the Cancel button).
        After a Save the _saved flag is True so we skip the second resume.
        """
        if not self._saved:
            self._app.resume_hotkeys()
        self._saved = False
