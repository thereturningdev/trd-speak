"""Failure isolation for hotkey start (issue #22).

resume_hotkeys()/set_hotkeys() used to run three bare start() calls: the first
raise (stale TCC grant, transient CGEventTapCreate failure) stranded the
remaining listeners — ALL shortcuts dead — and propagated into AppKit delegate
callbacks. These tests pin the fixed contract:

- every start() is attempted regardless of individual failures;
- failures are reported through the on_hotkeys_degraded hook (menu layer);
- no exception escapes resume_hotkeys()/set_hotkeys();
- the 2 s watchdog retries start() on dead listeners until they recover,
  but never while the taps are deliberately suspended (settings window open).

No AppKit: HotkeyListener.start/stop are monkeypatched (same pattern as
tests/test_app_hotkeys.py) so no real Quartz event tap is ever created.
"""

import pytest

import flow.app as app_mod
from flow import menubar
from flow.app import App
from flow.config import Config


@pytest.fixture
def app(monkeypatch, tmp_path):
    monkeypatch.setattr(app_mod.HotkeyListener, "start", lambda self: None)
    monkeypatch.setattr(app_mod.HotkeyListener, "stop", lambda self: None)
    monkeypatch.setattr(
        app_mod.engine_state,
        "save_engine",
        lambda name, path=tmp_path / "engine": path.write_text(name),
    )
    return App(Config())


def _instrument(monkeypatch, app, failing_names):
    """Make HotkeyListener.start record successes by listener name and raise
    for names in `failing_names` (a mutable set, so tests can 'heal' a
    listener later). Returns (started, reported) recorders; `reported`
    collects every on_hotkeys_degraded call."""
    started = []

    def fake_start(self):
        if self._name in failing_names:
            raise RuntimeError(f"tap create failed for {self._name}")
        started.append(self._name)

    monkeypatch.setattr(app_mod.HotkeyListener, "start", fake_start)
    reported = []
    app.on_hotkeys_degraded = lambda labels: reported.append(tuple(labels))
    return started, reported


# -- resume_hotkeys ----------------------------------------------------------

def test_resume_isolates_first_listener_failure(app, monkeypatch):
    """Dictation start raising must not strand re-paste/correction, must not
    escape (the caller is an ObjC delegate callback), and must be reported."""
    started, reported = _instrument(monkeypatch, app, {"dictation"})

    app.resume_hotkeys()  # must not raise

    assert started == ["re-paste", "correction"]
    assert reported == [("Hotkey",)]


def test_resume_isolates_middle_listener_failure(app, monkeypatch):
    started, reported = _instrument(monkeypatch, app, {"re-paste"})

    app.resume_hotkeys()

    assert started == ["dictation", "correction"]
    assert reported == [("Re-paste",)]


def test_resume_all_listeners_failing_reports_all_and_does_not_raise(app, monkeypatch):
    started, reported = _instrument(
        monkeypatch, app, {"dictation", "re-paste", "correction"}
    )

    app.resume_hotkeys()

    assert started == []
    assert reported == [("Hotkey", "Re-paste", "Correction")]


def test_resume_success_reports_empty_failures(app, monkeypatch):
    """A clean resume must report an EMPTY failure set so a stale 'shortcuts
    degraded' menu indication clears itself."""
    started, reported = _instrument(monkeypatch, app, set())

    app.resume_hotkeys()

    assert started == ["dictation", "re-paste", "correction"]
    assert reported == [()]


def test_resume_survives_a_raising_degraded_hook(app, monkeypatch):
    """The UI hook is best-effort: it raising must not break the resume."""
    started, _ = _instrument(monkeypatch, app, set())
    app.on_hotkeys_degraded = lambda labels: (_ for _ in ()).throw(RuntimeError())

    app.resume_hotkeys()  # must not raise

    assert started == ["dictation", "re-paste", "correction"]


# -- set_hotkeys -------------------------------------------------------------

def test_set_hotkeys_middle_failure_still_consistent(app, monkeypatch):
    """A failing re-paste start must not stop the others, must not lose the
    config update (the watchdog keeps retrying with the NEW combos), must be
    reported, and must not escape."""
    started, reported = _instrument(monkeypatch, app, {"re-paste"})

    app.set_hotkeys(["cmd", "ctrl", "v"], ["cmd", "shift", "r"], ["cmd", "alt", "c"])

    assert started == ["dictation", "correction"]
    assert reported == [("Re-paste",)]
    # Config reflects the new combos even though one listener is dead.
    assert app.config.keys == ["cmd", "ctrl", "v"]
    assert app.config.repaste_keys == ["cmd", "shift", "r"]
    assert app.config.correct_keys == ["cmd", "alt", "c"]
    # The rebuilt listeners carry the new combos (the retry uses them).
    assert app.repaste_hotkey._targets == frozenset({"cmd", "shift", "r"})


def test_set_hotkeys_survives_a_raising_stop(app, monkeypatch):
    """Even a stop() raising (defensive) must not abort the rebuild."""
    monkeypatch.setattr(
        app_mod.HotkeyListener,
        "stop",
        lambda self: (_ for _ in ()).throw(RuntimeError("stop failed")),
    )
    started, reported = _instrument(monkeypatch, app, set())

    app.set_hotkeys(["cmd", "ctrl", "v"], ["cmd", "shift", "r"], ["cmd", "alt", "c"])

    assert started == ["dictation", "re-paste", "correction"]
    assert app.config.keys == ["cmd", "ctrl", "v"]


# -- watchdog retry ----------------------------------------------------------

def test_watchdog_retries_and_recovers_a_dead_listener(app, monkeypatch):
    """A listener whose start() failed gets a start() retry on the next
    watchdog tick; on success the failure indication clears."""
    failing = {"dictation"}
    started, reported = _instrument(monkeypatch, app, failing)
    app.resume_hotkeys()
    assert reported == [("Hotkey",)]

    # First tick: still failing — nothing recovered, no exception.
    assert menubar.reenable_disabled_taps(app) == []

    # The grant comes back: the next tick recovers the listener.
    failing.clear()
    recovered = menubar.reenable_disabled_taps(app)

    assert recovered == ["Hotkey"]
    assert "dictation" in started
    assert reported[-1] == ()  # indication cleared


def test_watchdog_retry_is_noop_when_all_healthy(app, monkeypatch):
    started, reported = _instrument(monkeypatch, app, set())
    app.resume_hotkeys()
    started.clear()

    assert menubar.reenable_disabled_taps(app) == []
    assert started == []  # no spurious start() on live listeners


def test_watchdog_does_not_restart_suspended_taps(app, monkeypatch):
    """suspend_hotkeys (settings/correction window open) must silence the
    retry: the watchdog must never resurrect deliberately-stopped taps."""
    failing = {"dictation"}
    started, reported = _instrument(monkeypatch, app, failing)
    app.resume_hotkeys()
    failing.clear()  # would recover if the watchdog (wrongly) ran
    started.clear()

    app.suspend_hotkeys()

    assert menubar.reenable_disabled_taps(app) == []
    assert started == []

    # Resume re-evaluates: the healed listener starts and the report clears.
    app.resume_hotkeys()
    assert started == ["dictation", "re-paste", "correction"]
    assert reported[-1] == ()


def test_watchdog_retry_survives_set_hotkeys_rebuild(app, monkeypatch):
    """set_hotkeys rebuilds the listener objects; the retry must target the
    NEW object (iter_hotkeys read fresh), with the NEW combo."""
    failing = {"correction"}
    started, reported = _instrument(monkeypatch, app, failing)
    app.set_hotkeys(["cmd", "ctrl", "v"], ["cmd", "shift", "r"], ["cmd", "alt", "c"])
    assert reported == [("Correction",)]

    failing.clear()
    assert menubar.reenable_disabled_taps(app) == ["Correction"]
    assert app.correction_hotkey._targets == frozenset({"cmd", "alt", "c"})
    assert reported[-1] == ()


def test_boot_start_tracks_convenience_listener_failures(app, monkeypatch):
    """App.start() already tolerated re-paste/correction failures but never
    told the watchdog — those taps stayed dead until restart. They must now
    be reported and retried like any other failure."""
    failing = {"correction"}
    started, reported = _instrument(monkeypatch, app, failing)
    monkeypatch.setattr(app.transcriber, "load", lambda: None)

    app.start()

    assert reported[-1] == ("Correction",)
    failing.clear()
    assert menubar.reenable_disabled_taps(app) == ["Correction"]
