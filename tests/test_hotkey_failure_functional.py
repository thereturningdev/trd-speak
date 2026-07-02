"""Functional tests for hotkey start-failure isolation (issue #22).

Drives the REAL code paths end-to-end: a real flow.app.App, a real AppKit
correction/settings window (built, shown, closed), and a real MenuBar wired to
App.on_hotkeys_degraded — with only HotkeyListener.start/stop monkeypatched,
because a live CGEventTap e2e would need Input Monitoring granted to the test
runner (not permitted: tests must not change the machine's permissions).

Scenario per window:
- open the window (suspends the taps);
- close/save it with ONE listener's start() raising (stale-TCC simulation);
- assert the close path does not raise into the ObjC delegate, the OTHER
  listeners started, the menu shows the degraded row;
- heal the listener and run the real watchdog entry point
  (flow.menubar.reenable_disabled_taps, what the 2 s poll calls) — the dead
  listener recovers and the menu row clears.
"""

import pytest

pytest.importorskip("AppKit")

import flow.app as app_mod
import flow.settings_window as settings_mod
from flow import menubar
from flow.app import App
from flow.config import Config
from flow.menubar import MenuBar, _Delegate


@pytest.fixture
def rig(monkeypatch, tmp_path):
    """(app, ui, started, failing): a real App wired to a real MenuBar, with
    HotkeyListener.start recording by listener name and raising for names in
    the mutable `failing` set."""
    started: list[str] = []
    failing: set[str] = set()

    def fake_start(self):
        if self._name in failing:
            raise RuntimeError(f"CGEventTapCreate returned None ({self._name})")
        started.append(self._name)

    monkeypatch.setattr(app_mod.HotkeyListener, "start", fake_start)
    monkeypatch.setattr(app_mod.HotkeyListener, "stop", lambda self: None)
    # Key+modifier combos rebuild onto the Carbon backend (issue #23): the
    # same instrumentation must cover it, or a swapped combo would really
    # register with Carbon mid-test.
    monkeypatch.setattr(app_mod.CarbonHotkey, "start", fake_start)
    monkeypatch.setattr(app_mod.CarbonHotkey, "stop", lambda self: None)
    monkeypatch.setattr(
        app_mod.engine_state,
        "save_engine",
        lambda name, path=tmp_path / "engine": path.write_text(name),
    )
    # Never write the user's real hotkeys.json.
    monkeypatch.setattr(
        settings_mod.hotkey_state,
        "save",
        lambda d, r, c, path=None: (tmp_path / "hotkeys.json").write_text("saved"),
    )
    app = App(Config())
    ui = MenuBar("+".join(app.config.keys), _Delegate.alloc().init())
    app.on_hotkeys_degraded = ui.update_hotkey_failures  # as run() wires it
    return app, ui, started, failing


def _warning_row(ui):
    """(hidden, title) of the degraded-shortcuts row after a direct render
    (tests have no running run loop, so _on_main blocks never drain)."""
    ui._render()
    return bool(ui._hotkey_warning.isHidden()), str(ui._hotkey_warning.title())


def test_correction_window_close_with_failing_listener_recovers_via_watchdog(rig):
    app, ui, started, failing = rig
    from flow.correction_window import CorrectionWindowController

    controller = CorrectionWindowController(app)
    controller.open("hello world")  # suspends the taps, shows the window
    started.clear()

    failing.add("dictation")  # the grant went stale while the window was open
    controller.cancel()  # real NSWindow close -> windowWillClose -> resume

    # No exception escaped the delegate path; the other two listeners run.
    assert started == ["re-paste", "correction"]
    hidden, title = _warning_row(ui)
    assert not hidden
    assert "Hotkey" in title

    # Watchdog tick while still failing: nothing recovers, row stays.
    assert menubar.reenable_disabled_taps(app) == []
    hidden, _ = _warning_row(ui)
    assert not hidden

    # Grant comes back: the next tick restarts the listener and clears the row.
    failing.clear()
    assert menubar.reenable_disabled_taps(app) == ["Hotkey"]
    assert "dictation" in started
    hidden, _ = _warning_row(ui)
    assert hidden


def test_correction_window_save_with_failing_listener_does_not_raise(rig, monkeypatch):
    app, ui, started, failing = rig
    from flow.correction_window import CorrectionWindowController

    controller = CorrectionWindowController(app)
    controller.open("hello world")
    started.clear()
    monkeypatch.setattr(app, "learn", lambda original, edited: None)

    failing.add("re-paste")
    controller.save()  # learn -> close -> resume_hotkeys, must not raise

    assert started == ["dictation", "correction"]
    hidden, title = _warning_row(ui)
    assert not hidden
    assert "Re-paste" in title


def test_settings_window_save_with_failing_listener_stays_consistent(rig):
    app, ui, started, failing = rig
    from flow.settings_window import SettingsWindowController

    controller = SettingsWindowController(app, ui)
    controller.open()  # suspends taps, fills recorders with current combos
    controller._dictate_recorder.set_keys(["cmd", "ctrl", "v"])
    controller._repaste_recorder.set_keys(["cmd", "shift", "r"])
    controller._correct_recorder.set_keys(["cmd", "alt", "c"])
    started.clear()

    failing.add("correction")
    controller.save()  # real Save path: set_hotkeys + persist + close

    # No raise; the window closed via the saved path.
    assert not controller._window.isVisible()
    # The other listeners started; config reflects the NEW combos even though
    # the correction listener is dead (the watchdog retries with them).
    assert started == ["dictation", "re-paste"]
    assert app.config.keys == ["cmd", "ctrl", "v"]
    assert app.config.correct_keys == ["cmd", "alt", "c"]
    hidden, title = _warning_row(ui)
    assert not hidden
    assert "Correction" in title

    # Watchdog recovery uses the rebuilt listener with the new combo.
    failing.clear()
    assert menubar.reenable_disabled_taps(app) == ["Correction"]
    assert app.correction_hotkey._targets == frozenset({"cmd", "alt", "c"})
    hidden, _ = _warning_row(ui)
    assert hidden


def test_watchdog_never_restarts_taps_while_settings_window_is_open(rig):
    """The 2 s poll fires while the settings window is open (taps suspended);
    it must NOT resurrect them — the window's recorder must stay the only
    listener. Regression guard on the retry/suspend interaction."""
    app, ui, started, failing = rig
    from flow.settings_window import SettingsWindowController

    failing.add("dictation")
    app.resume_hotkeys()  # leaves a tracked failure behind
    failing.clear()
    controller = SettingsWindowController(app, ui)
    controller.open()  # suspend
    started.clear()

    assert menubar.reenable_disabled_taps(app) == []
    assert started == []

    controller.cancel()  # close without save -> resume all three
    assert started == ["dictation", "re-paste", "correction"]
    hidden, _ = _warning_row(ui)
    assert hidden
