"""Settings window: the status line names the backend flavor of a
just-recorded combo (issue #23, item 3).

Functional: a REAL SettingsWindowController with its real NSWindow and
_Recorder controls; a recorded combo is driven through the recorder's actual
NSEvent handlers (keyDown for key+modifier, flagsChanged for modifier-only)
and the status NSTextField is read back. Only the hotkey backends' start/stop
are neutered (no tap, no Carbon registration, no machine state).
"""

import pytest

pytest.importorskip("AppKit")

import flow.app as app_mod
import flow.settings_window as settings_mod
from flow.app import App
from flow.config import Config
from flow.menubar import MenuBar, _Delegate
from flow.settings_window import SettingsWindowController


class _FakeKeyEvent:
    """Duck-typed stand-in for the NSEvent the local monitor hands the
    recorder (only keyCode()/modifierFlags() are consulted)."""

    def __init__(self, keycode, flags):
        self._keycode = keycode
        self._flags = flags

    def keyCode(self):
        return self._keycode

    def modifierFlags(self):
        return self._flags

    def type(self):
        import AppKit

        return AppKit.NSEventTypeKeyDown


@pytest.fixture
def controller(monkeypatch, tmp_path):
    monkeypatch.setattr(app_mod.HotkeyListener, "start", lambda self: None)
    monkeypatch.setattr(app_mod.HotkeyListener, "stop", lambda self: None)
    monkeypatch.setattr(app_mod.CarbonHotkey, "start", lambda self: None)
    monkeypatch.setattr(app_mod.CarbonHotkey, "stop", lambda self: None)
    monkeypatch.setattr(
        app_mod.engine_state,
        "save_engine",
        lambda name, path=tmp_path / "engine": path.write_text(name),
    )
    monkeypatch.setattr(
        settings_mod.hotkey_state,
        "save",
        lambda d, r, c, path=None: (tmp_path / "hotkeys.json").write_text("saved"),
    )
    app = App(Config())
    ui = MenuBar("+".join(app.config.keys), _Delegate.alloc().init())
    c = SettingsWindowController(app, ui)
    c.open()
    yield c
    c._window.close()


def _record(recorder, event):
    """Begin a real recording and feed one event through the monitor path."""
    recorder._begin_recording()
    recorder._handle_event(event)


def test_key_plus_modifier_combo_shows_the_carbon_flavor(controller):
    import Quartz

    # Record cmd+r on the Paste recorder: keyCode 15 ('r') with cmd held.
    _record(
        controller._repaste_recorder,
        _FakeKeyEvent(15, Quartz.kCGEventFlagMaskCommand),
    )
    assert controller._repaste_recorder.keys() == ["cmd", "r"]
    status = str(controller._status.stringValue())
    assert status == "Paste: Maximum-reliability shortcut (no permissions needed)."


def test_modifier_only_combo_shows_the_tap_flavor(controller):
    import Quartz

    rec = controller._dictate_recorder
    rec._begin_recording()
    both = Quartz.kCGEventFlagMaskControl | Quartz.kCGEventFlagMaskShift
    # flagsChanged sequence: ctrl down, ctrl+shift down (peak), then release.
    for flags in (Quartz.kCGEventFlagMaskControl, both, Quartz.kCGEventFlagMaskShift):
        event = _FakeKeyEvent(0, flags)
        event.type = lambda: 0  # unused by _on_flags_changed
        rec._on_flags_changed(event)
    assert rec.keys() == ["ctrl", "shift"]
    status = str(controller._status.stringValue())
    assert status == (
        "Dictate: Modifier-only — uses the keyboard tap (needs Input Monitoring)."
    )


def test_a_raising_status_hook_does_not_break_the_recorder(controller, monkeypatch):
    import Quartz

    rec = controller._correct_recorder
    rec._on_recorded = lambda keys: (_ for _ in ()).throw(RuntimeError("boom"))
    _record(rec, _FakeKeyEvent(8, Quartz.kCGEventFlagMaskCommand))  # cmd+c
    # The combo was still committed and recording ended cleanly.
    assert rec.keys() == ["cmd", "c"]
    assert rec._recording is False
