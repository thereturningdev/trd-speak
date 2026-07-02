"""Functional tests for the single-tap architecture (issue #20 acceptance).

Drives the REAL production paths — flow.app.App with UN-patched
HotkeyListener.start/stop, the real settings/correction windows, and the real
watchdog entry point flow.menubar.reenable_disabled_taps — with only the
Quartz tap primitives faked (the same fixture as tests/test_event_tap.py).
A live-tap e2e would require granting Input Monitoring to the test runner,
which is not permitted (tests must never change the machine's permissions).

Acceptance criteria pinned:
1. exactly ONE CGEventTapCreate at runtime regardless of shortcut count
   (and across set_hotkeys rebuilds);
2. opening/closing the settings or correction window performs ZERO tap
   create/destroy operations (suspend/resume are hub mute/unmute);
3. force-disabling the tap is healed by ONE watchdog tick, after which event
   delivery to the listeners demonstrably resumes.
"""

import pytest

import flow.app as app_mod
import flow.event_tap as et
import flow.hotkey as hk
from flow import menubar
from flow.app import App
from flow.config import Config

_CTRL_MASK = hk.Quartz.kCGEventFlagMaskControl
_SHIFT_MASK = hk.Quartz.kCGEventFlagMaskShift


@pytest.fixture
def quartz(monkeypatch):
    """Fake tap machinery on the shared Quartz module (counts create/destroy,
    tracks the enabled flag). Same shape as tests/test_event_tap.py."""
    state = {
        "creates": 0,
        "fail_create": False,
        "enabled": False,
        "invalidated": 0,
    }

    def fake_create(location, placement, option, mask, callback, refcon):
        state["creates"] += 1
        if state["fail_create"]:
            return None
        state["callback"] = callback
        return ("tap", state["creates"])

    def fake_enable(tap, on):
        state["enabled"] = bool(on)

    q = et.Quartz
    monkeypatch.setattr(q, "CGEventTapCreate", fake_create)
    monkeypatch.setattr(q, "CFMachPortCreateRunLoopSource", lambda a, t, o: ("source", t))
    monkeypatch.setattr(q, "CFRunLoopGetMain", lambda: "main-run-loop")
    monkeypatch.setattr(q, "CFRunLoopAddSource", lambda *a: None)
    monkeypatch.setattr(q, "CFRunLoopRemoveSource", lambda *a: None)
    monkeypatch.setattr(q, "CGEventTapEnable", fake_enable)
    monkeypatch.setattr(q, "CGEventTapIsEnabled", lambda tap: state["enabled"])
    monkeypatch.setattr(q, "CFRunLoopWakeUp", lambda rl: None)
    monkeypatch.setattr(
        q, "CFMachPortInvalidate",
        lambda tap: state.__setitem__("invalidated", state["invalidated"] + 1),
    )
    return state


@pytest.fixture
def app(quartz, monkeypatch, tmp_path):
    """A real App with REAL listener start/stop (only Quartz faked), isolated
    from the user's Application Support and NSWorkspace observers."""
    monkeypatch.setattr(et, "_workspace_notification_center", lambda: _FakeCenter())
    monkeypatch.setattr(et, "_wake_notification_names", lambda: ("DidWake", "SessionActive"))
    monkeypatch.setattr(et, "_main_queue", lambda: "main-queue")
    monkeypatch.setattr(app_mod.paths, "DICTATIONS_PATH", tmp_path / "dictations.json")
    monkeypatch.setattr(app_mod.paths, "DICTIONARY_PATH", tmp_path / "dictionary.json")
    monkeypatch.setattr(
        app_mod.engine_state,
        "save_engine",
        lambda name, path=tmp_path / "engine": path.write_text(name),
    )
    return App(Config())


class _FakeCenter:
    def addObserverForName_object_queue_usingBlock_(self, name, obj, queue, block):
        return object()


class _HubDriver:
    """Feeds synthesized events through the HUB's tap callback — the exact
    entry point a real tap drives — down into every registered listener."""

    def __init__(self, hub, monkeypatch):
        self._hub = hub
        self._keycode = 0
        self._flags = 0
        monkeypatch.setattr(
            hk.Quartz, "CGEventGetIntegerValueField", lambda e, f: self._keycode
        )
        monkeypatch.setattr(hk.Quartz, "CGEventGetFlags", lambda e: self._flags)

    def modifier(self, flags):
        self._flags = flags
        self._hub._tap_callback(None, hk.Quartz.kCGEventFlagsChanged, object(), None)


# ---------------------------------------------------------------------------
# Acceptance 1: exactly one CGEventTapCreate regardless of shortcut count
# ---------------------------------------------------------------------------


def test_starting_all_three_shortcuts_creates_exactly_one_tap(app, quartz):
    app.resume_hotkeys()  # real start() on all three listeners
    assert quartz["creates"] == 1
    assert app._failed_hotkeys == set()
    # Every listener shares the App's single hub.
    assert {lis._hub for _label, lis in app.iter_hotkeys()} == {app.tap_hub}


def test_set_hotkeys_rebuild_does_not_create_or_destroy_taps(app, quartz):
    app.resume_hotkeys()
    app.set_hotkeys(["cmd", "ctrl", "v"], ["cmd", "shift", "r"], ["cmd", "alt", "c"])
    assert quartz["creates"] == 1
    assert quartz["invalidated"] == 0
    # The rebuilt listeners are live on the same hub.
    assert app.tap_hub._listeners == [lis for _l, lis in app.iter_hotkeys()]


def test_boot_tap_create_failure_still_raises_for_the_restart_flow(app, quartz, monkeypatch):
    """App.start() must still raise when the tap cannot be created, so
    flow.menubar's boot() can offer the user-initiated 'Restart TRD Speak
    now' row (macOS may honor a fresh Input Monitoring grant only in a new
    process)."""
    monkeypatch.setattr(app.transcriber, "load", lambda: None)
    quartz["fail_create"] = True
    with pytest.raises(RuntimeError, match="Input Monitoring"):
        app.start()


# ---------------------------------------------------------------------------
# Acceptance 2: zero tap create/destroy on settings/correction window open/close
# ---------------------------------------------------------------------------


def test_settings_window_open_close_performs_zero_tap_operations(app, quartz, monkeypatch):
    pytest.importorskip("AppKit")
    from flow.menubar import MenuBar, _Delegate
    from flow.settings_window import SettingsWindowController

    app.resume_hotkeys()
    ui = MenuBar("+".join(app.config.keys), _Delegate.alloc().init())
    controller = SettingsWindowController(app, ui)

    controller.open()  # suspend -> hub mute
    assert app.tap_hub._muted is True
    controller.cancel()  # close -> resume -> hub unmute
    assert app.tap_hub._muted is False

    assert quartz["creates"] == 1  # the boot create only
    assert quartz["invalidated"] == 0


def test_correction_window_open_close_performs_zero_tap_operations(app, quartz, monkeypatch):
    pytest.importorskip("AppKit")
    from flow.correction_window import CorrectionWindowController

    app.resume_hotkeys()
    controller = CorrectionWindowController(app)
    controller.open("hello world")
    assert app.tap_hub._muted is True
    controller.cancel()
    assert app.tap_hub._muted is False

    assert quartz["creates"] == 1
    assert quartz["invalidated"] == 0


def test_combo_pressed_while_settings_open_cannot_phantom_fire(app, quartz, monkeypatch):
    """While the window is open the hub is muted; recording the dictate combo
    must neither start a dictation nor leave shadow state that fires when the
    window closes (the #21 per-hold semantics across mute)."""
    pytest.importorskip("AppKit")
    from flow.menubar import MenuBar, _Delegate
    from flow.settings_window import SettingsWindowController

    activated = []
    app.resume_hotkeys()
    monkeypatch.setattr(app.hotkey, "_on_activate", lambda: activated.append(1))
    monkeypatch.setattr(app.hotkey, "_on_deactivate", lambda: activated.append(-1))
    d = _HubDriver(app.tap_hub, monkeypatch)
    ui = MenuBar("+".join(app.config.keys), _Delegate.alloc().init())
    controller = SettingsWindowController(app, ui)

    controller.open()
    d.modifier(_CTRL_MASK | _SHIFT_MASK)  # the user records ctrl+shift
    assert activated == []  # muted: no dictation started
    controller.cancel()
    d.modifier(0)  # the recorded combo is released after the window closed
    assert activated == []  # no phantom activation/deactivation
    # A fresh hold afterwards works normally.
    d.modifier(_CTRL_MASK | _SHIFT_MASK)
    assert activated == [1]
    d.modifier(0)
    assert activated == [1, -1]


# ---------------------------------------------------------------------------
# Acceptance 3: force-disabled tap healed within one watchdog poll tick
# ---------------------------------------------------------------------------


def test_watchdog_restores_event_delivery_within_one_poll_tick(app, quartz, monkeypatch):
    """Force-disable the tap (CGEventTapEnable(tap, False)) and assert one
    reenable_disabled_taps() pass — what the 2 s poll runs — restores event
    delivery to the listeners."""
    events = []
    app.resume_hotkeys()
    monkeypatch.setattr(app.hotkey, "_on_activate", lambda: events.append("on"))
    monkeypatch.setattr(app.hotkey, "_on_deactivate", lambda: events.append("off"))
    d = _HubDriver(app.tap_hub, monkeypatch)

    # Baseline: delivery works.
    d.modifier(_CTRL_MASK | _SHIFT_MASK)
    d.modifier(0)
    assert events == ["on", "off"]

    # Force-disable the tap, exactly as the acceptance criterion states.
    et.Quartz.CGEventTapEnable(app.tap_hub._tap, False)
    assert app.tap_hub.is_enabled() is False

    # ONE watchdog tick heals it and reports the recovery.
    recovered = menubar.reenable_disabled_taps(app)
    assert "Event tap" in recovered
    assert app.tap_hub.is_enabled() is True

    # Delivery is demonstrably restored.
    d.modifier(_CTRL_MASK | _SHIFT_MASK)
    d.modifier(0)
    assert events == ["on", "off", "on", "off"]


def test_watchdog_recreates_when_the_reenable_does_not_stick(app, quartz, monkeypatch):
    """Second tick with the tap still disabled: destroy + recreate, and the
    listeners keep receiving events through the fresh tap."""
    events = []
    app.resume_hotkeys()
    monkeypatch.setattr(app.hotkey, "_on_activate", lambda: events.append("on"))
    monkeypatch.setattr(app.hotkey, "_on_deactivate", lambda: events.append("off"))
    d = _HubDriver(app.tap_hub, monkeypatch)

    quartz["enabled"] = False
    real_enable = et.Quartz.CGEventTapEnable
    # Re-enables are silently ignored ('a non-nil tap is not a healthy tap')…
    monkeypatch.setattr(et.Quartz, "CGEventTapEnable", lambda tap, on: None)
    assert menubar.reenable_disabled_taps(app) == ["Event tap"]
    assert quartz["enabled"] is False  # …so the re-enable did not stick.
    # …until the recreate builds a fresh tap, whose enable works again.
    monkeypatch.setattr(et.Quartz, "CGEventTapEnable", real_enable)
    assert menubar.reenable_disabled_taps(app) == ["Event tap (recreated)"]
    assert quartz["creates"] == 2
    assert quartz["invalidated"] == 1
    assert app.tap_hub.is_enabled() is True

    d.modifier(_CTRL_MASK | _SHIFT_MASK)
    d.modifier(0)
    assert events == ["on", "off"]


def test_watchdog_combines_hub_recovery_with_listener_start_retry(app, quartz, monkeypatch):
    """One tick reports both the #22 start()-retry recovery and the hub
    re-enable, with no duplicates."""
    fail = {"flag": True}
    real_start = app_mod.HotkeyListener.start

    def flaky_start(self):
        if self._name == "re-paste" and fail["flag"]:
            raise RuntimeError("transient CGEventTapCreate refusal")
        real_start(self)

    monkeypatch.setattr(app_mod.HotkeyListener, "start", flaky_start)
    app.resume_hotkeys()
    assert app._failed_hotkeys == {"Re-paste"}

    fail["flag"] = False
    quartz["enabled"] = False
    recovered = menubar.reenable_disabled_taps(app)
    assert sorted(recovered) == ["Event tap", "Re-paste"]
    assert app._failed_hotkeys == set()


def test_heartbeat_counter_is_hub_level(app, quartz, monkeypatch):
    """The liveness heartbeat reads ONE counter from the hub (the old
    per-listener counters are gone)."""
    app.resume_hotkeys()
    d = _HubDriver(app.tap_hub, monkeypatch)
    d.modifier(_CTRL_MASK)
    d.modifier(0)
    assert app.tap_hub.take_event_count() == 2
    assert app.tap_hub.take_event_count() == 0


def test_shutdown_destroys_the_tap_exactly_once(app, quartz):
    app.resume_hotkeys()
    app.shutdown()
    assert quartz["invalidated"] == 1
    assert app.tap_hub._tap is None
    assert app.tap_hub._listeners == []
    # And the watchdog must NOT resurrect it after shutdown.
    assert app.tap_hub.watchdog_tick() is None
    assert quartz["creates"] == 1
