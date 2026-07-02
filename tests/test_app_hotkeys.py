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


def test_suspend_hotkeys_mutes_the_hub_without_stopping_listeners(app, monkeypatch):
    """Issue #20 contract change: suspend no longer stops (destroys) anything.
    The single shared tap is MUTED — zero tap create/destroy on window
    open/close — and resume unmutes. Listener stop() must not run."""
    stopped = []
    monkeypatch.setattr(
        app_mod.HotkeyListener, "stop", lambda self: stopped.append(id(self))
    )

    app.suspend_hotkeys()

    assert stopped == []
    assert app.tap_hub._muted is True
    assert app._hotkeys_suspended is True

    app.resume_hotkeys()
    assert app.tap_hub._muted is False


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


def test_watchdog_reenables_the_shared_tap(app, monkeypatch):
    """Issue #20 contract change: there is ONE tap (App.tap_hub) covering all
    three shortcuts — the correction tap can no longer be 'left unwatched'
    because there is no per-listener tap. The watchdog re-enables the hub's
    tap when macOS disabled it, and reports nothing when it is healthy."""
    import flow.event_tap as et
    from flow import menubar

    state = {"enabled": False}
    app.tap_hub._tap = object()  # a live-but-disabled tap (sentinel)
    monkeypatch.setattr(et.Quartz, "CGEventTapIsEnabled", lambda tap: state["enabled"])
    monkeypatch.setattr(
        et.Quartz, "CGEventTapEnable",
        lambda tap, on: state.__setitem__("enabled", bool(on)),
    )

    assert menubar.reenable_disabled_taps(app) == ["Event tap"]
    assert state["enabled"] is True
    # When nothing is disabled, the watchdog reports nothing.
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
