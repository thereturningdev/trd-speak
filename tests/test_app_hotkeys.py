"""Live shortcut apply/suspend/resume on App.

No AppKit: HotkeyListener.start/stop are monkeypatched to no-ops so no real
Quartz event tap is ever created. Mirrors the fixture pattern in
tests/test_app_engine.py.
"""

import pytest

import flow.app as app_mod
from flow.app import App
from flow.config import Config


@pytest.fixture
def app(monkeypatch, tmp_path):
    # No real event tap: neuter the listener's tap machinery for every
    # HotkeyListener (the constructor's pair and any rebuilt pair).
    monkeypatch.setattr(app_mod.HotkeyListener, "start", lambda self: None)
    monkeypatch.setattr(app_mod.HotkeyListener, "stop", lambda self: None)
    # Persist into a temp state file, not the real home dir.
    monkeypatch.setattr(
        app_mod.engine_state,
        "save_engine",
        lambda name, path=tmp_path / "engine": path.write_text(name),
    )
    return App(Config())


def test_set_hotkeys_replaces_all_listeners_and_updates_config(app):
    old_dictate = app.hotkey
    old_repaste = app.repaste_hotkey
    old_correct = app.correction_hotkey

    app.set_hotkeys(["cmd", "ctrl", "v"], ["cmd", "shift", "r"], ["cmd", "alt", "c"])

    assert app.hotkey is not old_dictate
    assert app.repaste_hotkey is not old_repaste
    assert app.correction_hotkey is not old_correct
    assert app.config.keys == ["cmd", "ctrl", "v"]
    assert app.config.repaste_keys == ["cmd", "shift", "r"]
    assert app.config.correct_keys == ["cmd", "alt", "c"]
    # New listeners carry the new combos.
    assert app.hotkey._targets == frozenset({"cmd", "ctrl", "v"})
    assert app.repaste_hotkey._targets == frozenset({"cmd", "shift", "r"})
    assert app.correction_hotkey._targets == frozenset({"cmd", "alt", "c"})


def test_set_hotkeys_calls_stop_then_start_on_all_three(app, monkeypatch):
    events = []
    monkeypatch.setattr(
        app_mod.HotkeyListener, "stop", lambda self: events.append(("stop", id(self)))
    )
    monkeypatch.setattr(
        app_mod.HotkeyListener, "start", lambda self: events.append(("start", id(self)))
    )
    old_ids = {id(app.hotkey), id(app.repaste_hotkey), id(app.correction_hotkey)}

    app.set_hotkeys(["cmd", "ctrl", "v"], ["cmd", "shift", "r"], ["cmd", "alt", "c"])

    stops = [e for e in events if e[0] == "stop"]
    starts = [e for e in events if e[0] == "start"]
    assert len(stops) == 3  # all three old listeners stopped
    assert len(starts) == 3  # all three new listeners started
    # The three stops are on the OLD listeners; the three starts on the NEW ones.
    assert {sid for _, sid in stops} == old_ids
    new_ids = {id(app.hotkey), id(app.repaste_hotkey), id(app.correction_hotkey)}
    assert {sid for _, sid in starts} == new_ids


def test_suspend_hotkeys_stops_all_three(app, monkeypatch):
    stopped = []
    monkeypatch.setattr(
        app_mod.HotkeyListener, "stop", lambda self: stopped.append(id(self))
    )

    app.suspend_hotkeys()

    assert id(app.hotkey) in stopped
    assert id(app.repaste_hotkey) in stopped
    assert id(app.correction_hotkey) in stopped


def test_iter_hotkeys_includes_all_three_taps(app):
    """The single source of truth the watchdog/heartbeat iterate over must name
    every tap — including the correction tap, previously omitted, which left it
    unwatched and able to stay dead until restart."""
    listeners = {label: lis for label, lis in app.iter_hotkeys()}
    assert set(listeners) == {"Hotkey", "Re-paste", "Correction"}
    assert listeners["Hotkey"] is app.hotkey
    assert listeners["Re-paste"] is app.repaste_hotkey
    assert listeners["Correction"] is app.correction_hotkey


def test_iter_hotkeys_tracks_rebuilt_listeners_after_set_hotkeys(app):
    """Read fresh, so the watchdog follows set_hotkeys' new objects, not stale
    ones — the property that makes 'apply a new shortcut' actually take hold."""
    app.set_hotkeys(["cmd", "ctrl", "v"], ["cmd", "shift", "r"], ["cmd", "alt", "c"])
    listeners = {label: lis for label, lis in app.iter_hotkeys()}
    assert listeners["Correction"] is app.correction_hotkey
    assert listeners["Re-paste"] is app.repaste_hotkey


def test_watchdog_reenables_every_disabled_tap_including_correction(app, monkeypatch):
    """The watchdog must re-enable ALL taps macOS disabled. Regression guard:
    the correction tap was previously never re-enabled by the watchdog."""
    from flow import menubar

    # Every listener reports itself disabled-then-reenabled once.
    monkeypatch.setattr(
        app_mod.HotkeyListener, "ensure_enabled", lambda self: True
    )
    reenabled = menubar.reenable_disabled_taps(app)
    assert set(reenabled) == {"Hotkey", "Re-paste", "Correction"}

    # When nothing is disabled, the watchdog reports nothing.
    monkeypatch.setattr(
        app_mod.HotkeyListener, "ensure_enabled", lambda self: False
    )
    assert menubar.reenable_disabled_taps(app) == []


def test_resume_hotkeys_starts_all_three(app, monkeypatch):
    started = []
    monkeypatch.setattr(
        app_mod.HotkeyListener, "start", lambda self: started.append(id(self))
    )
    old_dictate = app.hotkey
    old_repaste = app.repaste_hotkey
    old_correct = app.correction_hotkey

    app.resume_hotkeys()

    assert id(app.hotkey) in started
    assert id(app.repaste_hotkey) in started
    assert id(app.correction_hotkey) in started
    # Resume does NOT rebuild — the listener objects are unchanged.
    assert app.hotkey is old_dictate
    assert app.repaste_hotkey is old_repaste
    assert app.correction_hotkey is old_correct
