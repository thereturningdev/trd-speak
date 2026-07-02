"""Unit tests for flow.event_tap.EventTapHub (issue #20).

One hardened, listen-only, head-insert session tap shared by every hotkey
listener, replacing the three per-listener taps. These tests drive the hub
with synthetic events and monkeypatched Quartz calls (the established
_Driver pattern): no real CGEventTap is ever created, so no Input Monitoring
permission is needed and the machine configuration is never touched.

Contracts pinned here:
- registering any number of listeners creates exactly ONE tap, with the
  battle-tested parameters (session, HEAD-INSERT, listen-only, common modes,
  run-loop wakeup — see flow/hotkey.py's history for why each is mandatory);
- register/start raise RuntimeError when the tap cannot be created (the boot
  "Restart TRD Speak now" flow and the #22 retry machinery key off that);
- every event is forwarded to all registered listeners, each individually
  guarded (one raising listener must not starve the others — the _guarded
  pattern from #21);
- kCGEventTapDisabledByTimeout/ByUserInput re-enable the tap and are NOT
  forwarded;
- mute()/unmute() gate dispatch WITHOUT destroying the tap, and unmute resets
  per-hold listener state so keys pressed while muted cannot phantom-fire;
- ensure_enabled / watchdog_tick: re-enable a disabled tap; if the re-enable
  does not stick by the next tick, destroy and recreate from scratch;
- NSWorkspace wake/session-active notifications recreate the tap (observer
  tokens strongly referenced).
"""

import pytest

import flow.event_tap as et
import flow.hotkey as hk
from flow.event_tap import EventTapHub
from flow.hotkey import HotkeyListener

# Virtual keycodes / flag masks reused from the established drivers.
_CTRL = 59
_SHIFT = 56
_CTRL_MASK = hk.Quartz.kCGEventFlagMaskControl
_SHIFT_MASK = hk.Quartz.kCGEventFlagMaskShift


@pytest.fixture
def quartz(monkeypatch):
    """Fake tap machinery on the shared Quartz module: counts creates and
    destroys, tracks the enabled flag, records the creation parameters."""
    state = {
        "creates": 0,
        "params": None,
        "fail_create": False,
        "enabled": False,
        "enables": [],
        "invalidated": 0,
        "sources_added": 0,
        "sources_removed": 0,
        "wakeups": 0,
    }

    def fake_create(location, placement, option, mask, callback, refcon):
        state["creates"] += 1
        state["params"] = (location, placement, option, mask)
        if state["fail_create"]:
            return None
        return ("tap", state["creates"])

    def fake_enable(tap, on):
        state["enables"].append(bool(on))
        state["enabled"] = bool(on)

    q = et.Quartz  # the same module object flow.hotkey uses
    monkeypatch.setattr(q, "CGEventTapCreate", fake_create)
    monkeypatch.setattr(q, "CFMachPortCreateRunLoopSource", lambda a, t, o: ("source", t))
    monkeypatch.setattr(q, "CFRunLoopGetMain", lambda: "main-run-loop")
    monkeypatch.setattr(
        q, "CFRunLoopAddSource",
        lambda *a: state.__setitem__("sources_added", state["sources_added"] + 1),
    )
    monkeypatch.setattr(
        q, "CFRunLoopRemoveSource",
        lambda *a: state.__setitem__("sources_removed", state["sources_removed"] + 1),
    )
    monkeypatch.setattr(q, "CGEventTapEnable", fake_enable)
    monkeypatch.setattr(q, "CGEventTapIsEnabled", lambda tap: state["enabled"])
    monkeypatch.setattr(
        q, "CFRunLoopWakeUp",
        lambda rl: state.__setitem__("wakeups", state["wakeups"] + 1),
    )
    monkeypatch.setattr(
        q, "CFMachPortInvalidate",
        lambda tap: state.__setitem__("invalidated", state["invalidated"] + 1),
    )
    return state


@pytest.fixture
def fake_center(monkeypatch):
    """Fake NSWorkspace notification center so no real observers are installed
    and the wake blocks can be invoked directly."""

    class _Center:
        def __init__(self):
            self.added = []  # (name, obj, queue, block)
            self.tokens = []

        def addObserverForName_object_queue_usingBlock_(self, name, obj, queue, block):
            token = object()
            self.added.append((name, obj, queue, block))
            self.tokens.append(token)
            return token

    center = _Center()
    monkeypatch.setattr(et, "_workspace_notification_center", lambda: center)
    monkeypatch.setattr(
        et, "_wake_notification_names", lambda: ("DidWake", "SessionDidBecomeActive")
    )
    monkeypatch.setattr(et, "_main_queue", lambda: "main-queue")
    return center


class _Spy:
    """Minimal listener double: records forwarded events and state resets."""

    def __init__(self, name="spy", raise_on_event=False):
        self._name = name
        self.events = []
        self.resets = 0
        self.raise_on_event = raise_on_event

    def _tap_callback(self, proxy, event_type, event, refcon):
        self.events.append(event_type)
        if self.raise_on_event:
            raise RuntimeError(f"{self._name} boom")

    def reset_hold_state(self):
        self.resets += 1


def _hub(quartz, fake_center, *spies):
    hub = EventTapHub()
    for spy in spies:
        hub.register(spy)
    return hub


def _send_key(hub, event_type=None):
    hub._tap_callback(None, event_type or hk.Quartz.kCGEventKeyDown, object(), None)


# ---------------------------------------------------------------------------
# One tap, correct parameters
# ---------------------------------------------------------------------------


def test_registering_many_listeners_creates_exactly_one_tap(quartz, fake_center):
    hub = _hub(quartz, fake_center, _Spy("a"), _Spy("b"), _Spy("c"))
    assert quartz["creates"] == 1
    assert quartz["sources_added"] == 1
    # Registering the same listener again is idempotent.
    spy = _Spy("d")
    hub.register(spy)
    hub.register(spy)
    assert quartz["creates"] == 1


def test_tap_created_with_head_insert_listen_only_session_parameters(quartz, fake_center):
    """The hardened parameters are load-bearing: HEAD-INSERT (a tail-append tap
    loses ⌘-combos to upstream taps), listen-only, session tap, source on the
    main run loop in common modes, plus a run-loop wakeup."""
    _hub(quartz, fake_center, _Spy())
    location, placement, option, mask = quartz["params"]
    assert location == hk.Quartz.kCGSessionEventTap
    assert placement == hk.Quartz.kCGHeadInsertEventTap
    assert placement != hk.Quartz.kCGTailAppendEventTap
    assert option == hk.Quartz.kCGEventTapOptionListenOnly
    expected_mask = (
        hk.Quartz.CGEventMaskBit(hk.Quartz.kCGEventKeyDown)
        | hk.Quartz.CGEventMaskBit(hk.Quartz.kCGEventKeyUp)
        | hk.Quartz.CGEventMaskBit(hk.Quartz.kCGEventFlagsChanged)
    )
    assert mask == expected_mask
    assert quartz["enables"] == [True]
    assert quartz["wakeups"] == 1


def test_register_raises_runtime_error_when_tap_cannot_be_created(quartz, fake_center):
    """A None tap (Input Monitoring missing/revoked) must raise RuntimeError so
    the boot failure flow and the #22 retry machinery keep working; the failed
    listener must NOT be left registered."""
    quartz["fail_create"] = True
    hub = EventTapHub()
    spy = _Spy()
    with pytest.raises(RuntimeError, match="Input Monitoring"):
        hub.register(spy)
    assert hub._listeners == []
    # The permission comes back: the next register succeeds with a fresh tap.
    quartz["fail_create"] = False
    hub.register(spy)
    assert quartz["creates"] == 2
    assert hub._listeners == [spy]


def test_unregister_keeps_the_tap_alive(quartz, fake_center):
    """Unregistering (e.g. set_hotkeys rebuilding listeners) must NOT destroy
    the tap — the single tap persists for the process lifetime."""
    spy = _Spy()
    hub = _hub(quartz, fake_center, spy)
    hub.unregister(spy)
    assert quartz["invalidated"] == 0
    assert hub._tap is not None
    # Unregistering an unknown listener is a no-op, never a raise.
    hub.unregister(_Spy("stranger"))


def test_destroy_tears_the_tap_down(quartz, fake_center):
    hub = _hub(quartz, fake_center, _Spy())
    hub.destroy()
    assert hub._tap is None
    assert quartz["invalidated"] == 1
    assert quartz["sources_removed"] == 1
    assert quartz["enables"][-1] is False
    hub.destroy()  # idempotent
    assert quartz["invalidated"] == 1


# ---------------------------------------------------------------------------
# Dispatch: forward to all, guard each listener individually
# ---------------------------------------------------------------------------


def test_events_are_forwarded_to_every_registered_listener(quartz, fake_center):
    a, b = _Spy("a"), _Spy("b")
    hub = _hub(quartz, fake_center, a, b)
    _send_key(hub)
    assert a.events == [hk.Quartz.kCGEventKeyDown]
    assert b.events == [hk.Quartz.kCGEventKeyDown]


def test_a_raising_listener_does_not_starve_the_others(quartz, fake_center, capsys):
    """The #21 _guarded pattern at hub level: listener a raising must not stop
    b and c from seeing the event, and the error must be attributed by name."""
    a = _Spy("broken", raise_on_event=True)
    b, c = _Spy("b"), _Spy("c")
    hub = _hub(quartz, fake_center, a, b, c)
    _send_key(hub)
    assert b.events == [hk.Quartz.kCGEventKeyDown]
    assert c.events == [hk.Quartz.kCGEventKeyDown]
    out = capsys.readouterr().out
    assert "broken" in out


def test_listener_unregistering_itself_during_dispatch_is_safe(quartz, fake_center):
    """Registration changes mid-dispatch (a callback stopping a listener) must
    not corrupt the iteration or skip the remaining listeners."""
    hub = EventTapHub()
    b = _Spy("b")

    class _SelfRemover(_Spy):
        def _tap_callback(self, proxy, event_type, event, refcon):
            super()._tap_callback(proxy, event_type, event, refcon)
            hub.unregister(self)

    a = _SelfRemover("a")
    hub.register(a)
    hub.register(b)
    _send_key(hub)
    assert a.events == [hk.Quartz.kCGEventKeyDown]
    assert b.events == [hk.Quartz.kCGEventKeyDown]
    _send_key(hub)
    assert len(a.events) == 1  # unregistered — no longer dispatched to
    assert len(b.events) == 2


def test_tap_disabled_events_reenable_and_are_not_forwarded(quartz, fake_center):
    """kCGEventTapDisabledByTimeout/ByUserInput are tap lifecycle, handled by
    the hub (re-enable) and never forwarded to the matching listeners."""
    spy = _Spy()
    hub = _hub(quartz, fake_center, spy)
    quartz["enabled"] = False  # macOS disabled the tap
    _send_key(hub, hk.Quartz.kCGEventTapDisabledByTimeout)
    assert quartz["enabled"] is True
    _send_key(hub, hk.Quartz.kCGEventTapDisabledByUserInput)
    assert spy.events == []


# ---------------------------------------------------------------------------
# Heartbeat counter (moved from the listeners to the hub — one counter)
# ---------------------------------------------------------------------------


def test_take_event_count_reads_and_resets(quartz, fake_center):
    hub = _hub(quartz, fake_center, _Spy())
    assert hub.take_event_count() == 0
    _send_key(hub)
    _send_key(hub, hk.Quartz.kCGEventKeyUp)
    assert hub.take_event_count() == 2
    assert hub.take_event_count() == 0


def test_events_are_counted_even_while_muted(quartz, fake_center):
    """The heartbeat measures TAP liveness; a muted hub still receives events,
    so they must still count (zero while muted would read as a dead tap)."""
    hub = _hub(quartz, fake_center, _Spy())
    hub.mute()
    _send_key(hub)
    assert hub.take_event_count() == 1


def test_zero_events_on_an_enabled_tap_never_triggers_a_recreate(quartz, fake_center):
    """Regression/finding for issue #25 (Secure Keyboard Entry): a healthy
    tap that Secure Input is silently blackholing looks EXACTLY like this —
    CGEventTapIsEnabled() stays True (macOS filters the event stream, it
    does not disable the tap: no callback ever runs, so the timeout-disable
    path that only fires from a SLOW callback can't trigger either), the
    heartbeat counter stays at zero. take_event_count() is diagnostic-log
    only (flow/menubar.py never wires it into a recreate decision) and
    watchdog_tick() is driven purely by CGEventTapIsEnabled(), never by the
    event count — so many ticks of silence on an enabled tap must produce
    NO recreate. This is issue #25 step 5's "does this path already not
    exist" question, pinned as a permanent regression guard."""
    hub = _hub(quartz, fake_center, _Spy())
    quartz["enabled"] = True
    for _ in range(10):
        assert hub.take_event_count() == 0
        assert hub.watchdog_tick() is None
    assert quartz["creates"] == 1  # never destroyed/recreated


# ---------------------------------------------------------------------------
# mute / unmute (replaces destroy-and-recreate on settings/correction windows)
# ---------------------------------------------------------------------------


def test_mute_stops_dispatch_without_destroying_the_tap(quartz, fake_center):
    spy = _Spy()
    hub = _hub(quartz, fake_center, spy)
    hub.mute()
    _send_key(hub)
    assert spy.events == []
    assert quartz["invalidated"] == 0
    assert quartz["creates"] == 1
    hub.unmute()
    _send_key(hub)
    assert spy.events == [hk.Quartz.kCGEventKeyDown]


def test_mute_still_handles_tap_disabled_events(quartz, fake_center):
    """The tap must stay healthy while a window is open: disabled-by-timeout
    is re-enabled even when muted."""
    hub = _hub(quartz, fake_center, _Spy())
    hub.mute()
    quartz["enabled"] = False
    _send_key(hub, hk.Quartz.kCGEventTapDisabledByTimeout)
    assert quartz["enabled"] is True


def test_unmute_resets_per_hold_state_on_every_listener(quartz, fake_center):
    a, b = _Spy("a"), _Spy("b")
    hub = _hub(quartz, fake_center, a, b)
    hub.mute()
    hub.unmute()
    assert a.resets == 1
    assert b.resets == 1


def test_unmute_survives_a_raising_reset(quartz, fake_center, capsys):
    """One listener's reset raising must not strand the others un-reset."""

    class _BadReset(_Spy):
        def reset_hold_state(self):
            raise RuntimeError("reset boom")

    a, b = _BadReset("bad"), _Spy("b")
    hub = _hub(quartz, fake_center, a, b)
    hub.mute()
    hub.unmute()
    assert b.resets == 1
    assert "bad" in capsys.readouterr().out
    # Dispatch resumed despite the raise.
    _send_key(hub)
    assert b.events == [hk.Quartz.kCGEventKeyDown]


def test_no_phantom_activation_from_keys_pressed_while_muted(quartz, fake_center, monkeypatch):
    """The #21 per-hold semantics across a mute: a combo pressed while muted
    (e.g. recorded in the settings window) leaves NO shadow state behind, so
    after unmute the release of those keys cannot fire, and only a fresh full
    hold activates."""
    events = []
    listener = HotkeyListener(
        keys=["ctrl", "shift"],
        on_activate=lambda: events.append("on"),
        on_deactivate=lambda: events.append("off"),
    )
    hub = EventTapHub()
    hub.register(listener)
    flags = {"value": 0}
    monkeypatch.setattr(hk.Quartz, "CGEventGetIntegerValueField", lambda e, f: 0)
    monkeypatch.setattr(hk.Quartz, "CGEventGetFlags", lambda e: flags["value"])

    def flags_changed(value):
        flags["value"] = value
        hub._tap_callback(None, hk.Quartz.kCGEventFlagsChanged, object(), None)

    # Combo held while the hub is live: activates.
    flags_changed(_CTRL_MASK | _SHIFT_MASK)
    assert events == ["on"]
    flags_changed(0)
    assert events == ["on", "off"]

    # Muted: the user presses the combo to RECORD it in the settings window.
    hub.mute()
    flags_changed(_CTRL_MASK | _SHIFT_MASK)  # swallowed
    assert events == ["on", "off"]
    hub.unmute()  # reset: nothing is considered held
    assert listener._held == {}
    assert listener._active is False
    # Releasing the recorded combo after unmute must not phantom-fire.
    flags_changed(0)
    assert events == ["on", "off"]
    # A fresh full hold works normally.
    flags_changed(_CTRL_MASK | _SHIFT_MASK)
    flags_changed(0)
    assert events == ["on", "off", "on", "off"]


# ---------------------------------------------------------------------------
# Watchdog: re-enable, then recreate if the re-enable did not stick
# ---------------------------------------------------------------------------


def test_is_enabled_and_ensure_enabled(quartz, fake_center):
    hub = EventTapHub()
    assert hub.is_enabled() is False  # no tap yet
    assert hub.ensure_enabled() is False
    hub.register(_Spy())
    assert hub.is_enabled() is True
    assert hub.ensure_enabled() is False  # already enabled: no-op
    quartz["enabled"] = False
    assert hub.is_enabled() is False
    assert hub.ensure_enabled() is True  # re-asserted
    assert quartz["enabled"] is True


def test_watchdog_tick_reenables_a_disabled_tap(quartz, fake_center):
    hub = _hub(quartz, fake_center, _Spy())
    assert hub.watchdog_tick() is None  # healthy: nothing to do
    quartz["enabled"] = False
    assert hub.watchdog_tick() == "re-enabled"
    assert quartz["enabled"] is True
    assert hub.watchdog_tick() is None  # the re-enable stuck


def test_watchdog_tick_recreates_when_reenable_does_not_stick(quartz, fake_center, monkeypatch):
    """The 'a non-nil tap is not a healthy tap' case: CGEventTapEnable(True)
    does not stick (code-signing / silent-disable race). The next tick must
    destroy the tap and recreate it from scratch."""
    spy = _Spy()
    hub = _hub(quartz, fake_center, spy)
    # Enable calls are silently ignored by 'macOS'.
    monkeypatch.setattr(et.Quartz, "CGEventTapEnable", lambda tap, on: None)
    quartz["enabled"] = False
    assert hub.watchdog_tick() == "re-enabled"  # first attempt
    assert hub.watchdog_tick() == "recreated"  # did not stick -> full rebuild
    assert quartz["invalidated"] == 1
    assert quartz["creates"] == 2
    assert hub._tap == ("tap", 2)
    assert hub._listeners == [spy]  # registrations survive the recreate


def test_watchdog_tick_recreates_a_missing_tap_when_listeners_exist(quartz, fake_center, monkeypatch):
    """If a recreate failed transiently (tap None but listeners registered),
    the watchdog must keep trying rather than leaving the hotkeys dead."""
    spy = _Spy()
    hub = _hub(quartz, fake_center, spy)
    # 'macOS' keeps the tap stuck off and refuses new taps for a while.
    monkeypatch.setattr(et.Quartz, "CGEventTapEnable", lambda tap, on: None)
    quartz["fail_create"] = True
    quartz["enabled"] = False
    assert hub.watchdog_tick() == "re-enabled"  # attempt; does not stick
    assert hub.watchdog_tick() is None  # recreate attempted but create failed
    assert hub._tap is None
    # Next tick, creation works again: the watchdog resurrects the tap.
    quartz["fail_create"] = False
    assert hub.watchdog_tick() == "recreated"
    assert hub._tap is not None


def test_watchdog_tick_is_noop_with_no_tap_and_no_listeners(quartz, fake_center):
    hub = EventTapHub()
    assert hub.watchdog_tick() is None
    assert quartz["creates"] == 0


def test_recreate_preserves_mute_state(quartz, fake_center):
    spy = _Spy()
    hub = _hub(quartz, fake_center, spy)
    hub.mute()
    assert hub.recreate() is True
    assert quartz["creates"] == 2
    _send_key(hub)
    assert spy.events == []  # still muted after the recreate


def test_recreate_without_a_tap_is_a_noop(quartz, fake_center):
    hub = EventTapHub()
    assert hub.recreate() is False
    assert quartz["creates"] == 0


# ---------------------------------------------------------------------------
# Sleep/wake and session-active: recreate the tap
# ---------------------------------------------------------------------------


def test_wake_and_session_observers_installed_once_with_strong_refs(quartz, fake_center):
    hub = _hub(quartz, fake_center, _Spy())
    names = [name for name, _obj, _q, _b in fake_center.added]
    assert names == ["DidWake", "SessionDidBecomeActive"]
    # Strong refs kept (tokens must not be GC'd or the observer dies).
    assert len(hub._observer_tokens) == 2
    # A recreate must NOT stack a second set of observers.
    hub.recreate()
    assert len(fake_center.added) == 2


def test_wake_notification_recreates_the_tap(quartz, fake_center):
    spy = _Spy()
    hub = _hub(quartz, fake_center, spy)
    for _name, _obj, _queue, block in fake_center.added:
        block(object())  # the notification fires
    assert quartz["creates"] == 3  # initial + one recreate per notification
    assert hub._tap is not None
    _send_key(hub)
    assert spy.events == [hk.Quartz.kCGEventKeyDown]  # delivery survives


def test_wake_observer_block_bridging_with_a_real_notification_center(quartz, monkeypatch):
    """Functional check of the PyObjC bridging the fake-center tests bypass:
    a REAL (private) NSNotificationCenter accepts the Python block, keeps the
    token, and posting the notification runs the recreate path. Uses its own
    center — nothing is ever registered on the system's NSWorkspace center."""
    Foundation = pytest.importorskip("Foundation")
    center = Foundation.NSNotificationCenter.alloc().init()
    monkeypatch.setattr(et, "_workspace_notification_center", lambda: center)
    monkeypatch.setattr(et, "_wake_notification_names", lambda: ("TRDWakeTest",))
    monkeypatch.setattr(et, "_main_queue", lambda: None)  # synchronous delivery
    hub = EventTapHub()
    hub.register(_Spy())
    assert len(hub._observer_tokens) == 1
    try:
        center.postNotificationName_object_("TRDWakeTest", None)
        assert quartz["creates"] == 2  # initial + the wake-triggered recreate
        assert quartz["invalidated"] == 1
        assert hub._tap == ("tap", 2)
    finally:
        for token in hub._observer_tokens:
            center.removeObserver_(token)


def test_observer_installation_failure_does_not_break_the_tap(quartz, monkeypatch, capsys):
    """A headless/odd environment where NSWorkspace observers cannot be
    installed must not take the tap down with it."""
    monkeypatch.setattr(
        et, "_workspace_notification_center",
        lambda: (_ for _ in ()).throw(RuntimeError("no workspace")),
    )
    hub = EventTapHub()
    spy = _Spy()
    hub.register(spy)  # must not raise
    _send_key(hub)
    assert spy.events == [hk.Quartz.kCGEventKeyDown]
