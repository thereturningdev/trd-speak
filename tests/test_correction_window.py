"""Functional guard: the correction window must actually BUILD without raising.

A malformed PyObjC selector — ``monospacedSystemFontOfSize_(11, weight)`` passes
two args to a one-arg selector that does not exist — raised AttributeError every
time the correction hotkey fired. The tap callback swallowed it, so the window
silently never appeared and "the correct-last-dictation shortcut does not work".

Merely importing the module never caught this (tests/test_gui_imports.py does
that). Only *building* the window exercises the failing line, so this test does
exactly that, against the real AppKit objects.
"""

import pytest


class _FakeApp:
    """Just enough of App for the controller to construct and build a window;
    _build_window itself touches none of these, but open() would."""

    def suspend_hotkeys(self) -> None:
        pass

    def resume_hotkeys(self) -> None:
        pass


def test_build_window_does_not_raise():
    pytest.importorskip("AppKit")
    from flow.correction_window import CorrectionWindowController

    controller = CorrectionWindowController(_FakeApp())
    # The exact call that crashed on the bad monospaced-font selector.
    controller._build_window()

    assert controller._window is not None
    # The preview field is the one built with the monospaced font; a font of
    # None (or a raise above) means the selector regressed again.
    assert controller._preview_field is not None
    assert controller._preview_field.font() is not None


def test_monospaced_font_selector_exists():
    """Pin the exact root cause: the two-arg weighted selector is the real one;
    the one-arg name the bug used does not exist on NSFont."""
    AppKit = pytest.importorskip("AppKit")
    assert hasattr(AppKit.NSFont, "monospacedSystemFontOfSize_weight_")
    assert not hasattr(AppKit.NSFont, "monospacedSystemFontOfSize_")
