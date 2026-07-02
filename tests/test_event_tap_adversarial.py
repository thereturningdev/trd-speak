"""Adversarial tests for the single-event-tap architecture (issue #20).

Attacks EventTapHub (flow/event_tap.py), HotkeyListener start/stop/reset
(flow/hotkey.py), the App suspend/resume/set/shutdown integration
(flow/app.py) and the watchdog entry point (flow/menubar.py) with hostile
edge cases: registration churn, mid-dispatch mutation, mute/unmute abuse,
repeated recreate failures, wake notifications at the worst possible time,
and combos that must NOT fire under exact-modifier semantics.

Every test asserts INTENDED behavior (docstrings + issue #20/#21/#22
contracts). Failing tests are kept deliberately — they document real bugs.

No real CGEventTap is ever created and no real NSWorkspace observer is ever
installed: the Quartz tap primitives and the workspace notification center
are monkeypatched (the fixture pattern of tests/test_event_tap.py).
"""

import threading
import time

import pytest

import flow.app as app_mod
import flow.event_tap as et
import flow.hotkey as hk
from flow import menubar
from flow.app import App
from flow.config import Config
from flow.event_tap import EventTapHub
from flow.hotkey import HotkeyListener

_CTRL_MASK = hk.Quartz.kCGEventFlagMaskControl
_SHIFT_MASK = hk.Quartz.kCGEventFlagMaskShift
_CMD_MASK = hk.Quartz.kCGEventFlagMaskCommand
_ALT_MASK = hk.Quartz.kCGEventFlagMaskAlternate


# ---------------------------------------------------------------------------
# Fixtures (the established fake-Quartz / fake-center pattern)
# ---------------------------------------------------------------------------


@pytest.fixture
def quartz(monkeypatch):
    """Fake tap machinery on the shared Quartz module."""
    state = {
        "creates": 0,
        "fail_create": False,
        "enabled": False,
        "enables": [],
        "invalidated": 0,
        "sources_added": 0,
        "sources_removed": 0,
    }

    def fake_create(location, placement, option, mask, callback, refcon):
        state["creates"] += 1
        if state["fail_create"]:
            return None
        return ("tap", state["creates"])

    def fake_enable(tap, on):
        state["enables"].append(bool(on))
        state["enabled"] = bool(on)

    q = et.Quartz  # same module object flow.hotkey uses
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
    monkeypatch.setattr(q, "CFRunLoopWakeUp", lambda rl: None)
    monkeypatch.setattr(
        q, "CFMachPortInvalidate",
        lambda tap: state.__setitem__("invalidated", state["invalidated"] + 1),
    )
    return state


@pytest.fixture
def fake_center(monkeypatch):
    """Fake NSWorkspace notification center capturing the observer blocks."""

    class _Center:
        def __init__(self):
            self.added = []  # (name, obj, queue, block)

        def addObserverForName_object_queue_usingBlock_(self, name, obj, queue, block):
            self.added.append((name, obj, queue, block))
            return object()

    center = _Center()
    monkeypatch.setattr(et, "_workspace_notification_center", lambda: center)
    monkeypatch.setattr(
        et, "_wake_notification_names", lambda: ("DidWake", "SessionDidBecomeActive")
    )
    monkeypatch.setattr(et, "_main_queue", lambda: "main-queue")
    return center


@pytest.fixture
def app(quartz, fake_center, monkeypatch, tmp_path):
    """A real App (real listener start/stop, only Quartz faked), isolated
    from the user's Application Support paths."""
    monkeypatch.setattr(app_mod.paths, "DICTATIONS_PATH", tmp_path / "dictations.json")
    monkeypatch.setattr(app_mod.paths, "DICTIONARY_PATH", tmp_path / "dictionary.json")
    monkeypatch.setattr(
        app_mod.engine_state,
        "save_engine",
        lambda name, path=tmp_path / "engine": path.write_text(name),
    )
    return App(Config())


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


def _send_key(hub, event_type=None):
    hub._tap_callback(None, event_type or hk.Quartz.kCGEventKeyDown, object(), None)


class _FlagDriver:
    """Drive flagsChanged events through a hub into its listeners."""

    def __init__(self, hub, monkeypatch):
        self._hub = hub
        self._flags = 0
        monkeypatch.setattr(hk.Quartz, "CGEventGetIntegerValueField", lambda e, f: 0)
        monkeypatch.setattr(hk.Quartz, "CGEventGetFlags", lambda e: self._flags)

    def modifier(self, flags):
        self._flags = flags
        self._hub._tap_callback(None, hk.Quartz.kCGEventFlagsChanged, object(), None)


# ===========================================================================
# A. Registration / single-tap invariants
# ===========================================================================


def test_adv01_register_unregister_churn_never_creates_a_second_tap(quartz, fake_center):
    """ADV-01: 50 register/unregister/register cycles must reuse the one tap."""
    hub = EventTapHub()
    spy = _Spy()
    for _ in range(50):
        hub.register(spy)
        hub.unregister(spy)
        hub.register(spy)
    assert quartz["creates"] == 1
    assert quartz["invalidated"] == 0
    assert hub._listeners == [spy]


def test_adv02_create_failure_registers_nobody_and_both_retries_recover(quartz, fake_center):
    """ADV-02: with create failing, BOTH registers raise and neither listener
    may be left half-registered; when the permission returns both recover."""
    quartz["fail_create"] = True
    hub = EventTapHub()
    a, b = _Spy("a"), _Spy("b")
    with pytest.raises(RuntimeError):
        hub.register(a)
    with pytest.raises(RuntimeError):
        hub.register(b)
    assert hub._listeners == []
    assert hub._tap is None
    quartz["fail_create"] = False
    hub.register(a)
    hub.register(b)
    assert hub._listeners == [a, b]
    _send_key(hub)
    assert a.events == [hk.Quartz.kCGEventKeyDown]
    assert b.events == [hk.Quartz.kCGEventKeyDown]


def test_adv03_registration_is_identity_based_not_equality_based(quartz, fake_center):
    """ADV-03: two listeners that compare EQUAL are still distinct
    registrations; unregistering one must not evict the other."""

    class _EqualSpy(_Spy):
        def __eq__(self, other):
            return True  # hostile equality

        def __hash__(self):
            return 42

    a, b = _EqualSpy("a"), _EqualSpy("b")
    hub = EventTapHub()
    hub.register(a)
    hub.register(b)  # equal to a, but a different object: must register
    assert len(hub._listeners) == 2
    hub.unregister(a)
    assert len(hub._listeners) == 1
    assert hub._listeners[0] is b
    _send_key(hub)
    assert a.events == []
    assert b.events == [hk.Quartz.kCGEventKeyDown]


def test_adv04_listener_start_after_failed_start_recovers(quartz, fake_center):
    """ADV-04: start() raising must leave the listener unregistered so the
    next start() re-attempts the create and delivery then works."""
    hub = EventTapHub()
    fired = []
    lis = HotkeyListener(keys=["ctrl", "shift"], on_activate=lambda: fired.append(1), hub=hub)
    quartz["fail_create"] = True
    with pytest.raises(RuntimeError):
        lis.start()
    assert lis._registered is False
    assert hub._listeners == []
    quartz["fail_create"] = False
    lis.start()  # start-after-failed-start
    assert lis._registered is True
    assert hub._listeners == [lis]
    assert quartz["creates"] == 2


def test_adv05_double_start_double_stop_stop_without_start(quartz, fake_center):
    """ADV-05: start/start, stop/stop, stop-without-start, stop-then-start
    are all safe; the tap is created once and survives every stop."""
    hub = EventTapHub()
    a = HotkeyListener(keys=["ctrl", "shift"], on_activate=lambda: None, hub=hub)
    b = HotkeyListener(keys=["cmd", "ctrl"], on_trigger=lambda: None, hub=hub)
    b.stop()  # stop without start: clean no-op
    assert hub._listeners == []
    a.start()
    a.start()  # double start: idempotent
    assert hub._listeners == [a]
    a.stop()
    a.stop()  # double stop: idempotent
    assert hub._listeners == []
    a.start()  # stop-then-start
    assert hub._listeners == [a]
    assert quartz["creates"] == 1
    assert quartz["invalidated"] == 0


# ===========================================================================
# B. Mid-dispatch mutation of the listener list
# ===========================================================================


def test_adv06_listener_registers_another_listener_mid_dispatch(quartz, fake_center):
    """ADV-06: a callback registering a NEW listener must not corrupt the
    iteration, must not create a second tap, and the new listener starts
    receiving from the NEXT event."""
    hub = EventTapHub()
    late = _Spy("late")

    class _Registrar(_Spy):
        def _tap_callback(self, proxy, event_type, event, refcon):
            super()._tap_callback(proxy, event_type, event, refcon)
            hub.register(late)

    a, b = _Registrar("a"), _Spy("b")
    hub.register(a)
    hub.register(b)
    _send_key(hub)
    assert a.events == [hk.Quartz.kCGEventKeyDown]
    assert b.events == [hk.Quartz.kCGEventKeyDown]
    assert late.events == []  # snapshot: not the in-flight event
    assert quartz["creates"] == 1  # no second tap
    _send_key(hub)
    assert len(late.events) == 1  # registered from the next event on
    assert len(a.events) == 2 and len(b.events) == 2


def test_adv07_listener_unregisters_another_listener_mid_dispatch(quartz, fake_center):
    """ADV-07: a callback unregistering a DIFFERENT listener: the snapshot
    still delivers the in-flight event, the next event skips it, no crash."""
    hub = EventTapHub()
    b = _Spy("b")

    class _Evictor(_Spy):
        def _tap_callback(self, proxy, event_type, event, refcon):
            super()._tap_callback(proxy, event_type, event, refcon)
            hub.unregister(b)

    a = _Evictor("a")
    hub.register(a)
    hub.register(b)
    _send_key(hub)
    assert a.events == [hk.Quartz.kCGEventKeyDown]
    assert b.events == [hk.Quartz.kCGEventKeyDown]  # snapshot semantics
    _send_key(hub)
    assert len(a.events) == 2
    assert len(b.events) == 1  # evicted: no longer dispatched


def test_adv08_raising_listener_plus_self_removal_plus_survivor(quartz, fake_center, capsys):
    """ADV-08: worst-case dispatch — listener a raises, listener b removes
    itself, listener c must still get every event."""
    hub = EventTapHub()

    class _SelfRemover(_Spy):
        def _tap_callback(self, proxy, event_type, event, refcon):
            super()._tap_callback(proxy, event_type, event, refcon)
            hub.unregister(self)

    a = _Spy("broken", raise_on_event=True)
    b = _SelfRemover("b")
    c = _Spy("c")
    for lis in (a, b, c):
        hub.register(lis)
    _send_key(hub)
    assert c.events == [hk.Quartz.kCGEventKeyDown]
    assert "broken" in capsys.readouterr().out
    _send_key(hub)
    assert len(a.events) == 2  # still registered (raising is not eviction)
    assert len(b.events) == 1  # removed itself
    assert len(c.events) == 2


def test_adv09_listener_re_registering_itself_mid_dispatch_stays_single(quartz, fake_center):
    """ADV-09: a callback re-registering ITSELF must stay idempotent — no
    duplicate registration, no double dispatch on the next event."""
    hub = EventTapHub()

    class _SelfRegistrar(_Spy):
        def _tap_callback(self, proxy, event_type, event, refcon):
            super()._tap_callback(proxy, event_type, event, refcon)
            hub.register(self)

    a = _SelfRegistrar("a")
    hub.register(a)
    _send_key(hub)
    _send_key(hub)
    assert len(hub._listeners) == 1
    assert len(a.events) == 2  # one delivery per event, never two
    assert quartz["creates"] == 1


def test_adv10_in_flight_events_after_destroy_do_not_crash(quartz, fake_center):
    """ADV-10: a queued callback firing AFTER destroy() (tap already None)
    must not crash — including the disabled-by-timeout event, which must not
    call CGEventTapEnable on a dead tap."""
    spy = _Spy()
    hub = EventTapHub()
    hub.register(spy)
    hub.unregister(spy)
    hub.destroy()
    enables_before = list(quartz["enables"])
    _send_key(hub)  # in-flight key event: swallowed safely
    _send_key(hub, hk.Quartz.kCGEventTapDisabledByTimeout)  # lifecycle event
    assert quartz["enables"] == enables_before  # no enable on a None tap
    assert hub.take_event_count() == 2  # still counted (tap liveness metric)


# ===========================================================================
# C. Disabled-tap lifecycle events
# ===========================================================================


def test_adv11_disabled_by_user_input_reenables_even_while_muted(quartz, fake_center):
    """ADV-11: kCGEventTapDisabledByUserInput while MUTED must still re-enable
    the tap and never reach a listener."""
    spy = _Spy()
    hub = EventTapHub()
    hub.register(spy)
    hub.mute()
    quartz["enabled"] = False
    _send_key(hub, hk.Quartz.kCGEventTapDisabledByUserInput)
    assert quartz["enabled"] is True
    assert spy.events == []
    hub.unmute()
    _send_key(hub)
    assert spy.events == [hk.Quartz.kCGEventKeyDown]


def test_adv12_disabled_events_count_toward_the_heartbeat(quartz, fake_center):
    """ADV-12: the heartbeat measures tap liveness, so disabled-lifecycle
    events and muted events must all count."""
    hub = EventTapHub()
    hub.register(_Spy())
    _send_key(hub, hk.Quartz.kCGEventTapDisabledByTimeout)
    hub.mute()
    _send_key(hub)
    _send_key(hub, hk.Quartz.kCGEventKeyUp)
    hub.unmute()
    _send_key(hub, hk.Quartz.kCGEventFlagsChanged)
    assert hub.take_event_count() == 4
    assert hub.take_event_count() == 0


# ===========================================================================
# D. mute / unmute abuse
# ===========================================================================


def test_adv13_double_mute_double_unmute_unmute_without_mute(quartz, fake_center):
    """ADV-13: mute();mute(), unmute();unmute(), and unmute-without-mute are
    all safe and leave dispatch in the right state."""
    spy = _Spy()
    hub = EventTapHub()
    hub.register(spy)
    hub.unmute()  # unmute without mute: must be safe
    _send_key(hub)
    assert spy.events == [hk.Quartz.kCGEventKeyDown]
    hub.mute()
    hub.mute()  # double mute
    _send_key(hub)
    assert len(spy.events) == 1  # still gated
    hub.unmute()
    hub.unmute()  # double unmute
    _send_key(hub)
    assert len(spy.events) == 2
    assert quartz["creates"] == 1 and quartz["invalidated"] == 0


def test_adv14_listener_registered_while_muted_is_reset_on_unmute(quartz, fake_center):
    """ADV-14: a listener registered DURING a mute (set_hotkeys while a window
    is open) must also get the unmute reset and then receive events."""
    hub = EventTapHub()
    hub.register(_Spy("old"))
    hub.mute()
    late = _Spy("late")
    hub.register(late)
    _send_key(hub)
    assert late.events == []  # muted
    hub.unmute()
    assert late.resets == 1  # reset applied to the newcomer too
    _send_key(hub)
    assert late.events == [hk.Quartz.kCGEventKeyDown]


def test_adv15_active_hold_across_mute_unmute_must_balance_deactivate(
    quartz, fake_center, monkeypatch
):
    """ADV-15: if on_activate fired (dictation recording), a mute/unmute cycle
    while the combo is held must still produce EXACTLY ONE matching
    on_deactivate — at mute, at unmute, or at the physical release — otherwise
    the recording is stranded with no stop signal (the window can be opened
    with the mouse while the push-to-talk combo is held)."""
    events = []
    hub = EventTapHub()
    lis = HotkeyListener(
        keys=["ctrl", "shift"],
        on_activate=lambda: events.append("on"),
        on_deactivate=lambda: events.append("off"),
        hub=hub,
    )
    lis.start()
    d = _FlagDriver(hub, monkeypatch)
    d.modifier(_CTRL_MASK | _SHIFT_MASK)
    assert events == ["on"]  # dictation started
    hub.mute()   # settings window opened mid-hold
    hub.unmute()  # window closed; the combo is STILL physically held
    d.modifier(0)  # the user finally releases
    assert events.count("on") == 1
    assert events.count("off") == 1, (
        "an activated hold must be balanced by exactly one deactivate; "
        f"got {events!r} — the recording has no stop signal"
    )


def test_adv16_wait_all_released_is_unblocked_by_unmute(quartz, fake_center, monkeypatch):
    """ADV-16: a worker blocked in wait_all_released() while a key is
    shadow-held must be woken by the unmute reset, not left to time out."""
    hub = EventTapHub()
    lis = HotkeyListener(keys=["ctrl", "shift"], on_activate=lambda: None, hub=hub)
    lis.start()
    d = _FlagDriver(hub, monkeypatch)
    d.modifier(_CTRL_MASK)  # ctrl shadow-held
    assert lis._held
    result = {}

    def worker():
        t0 = time.monotonic()
        result["released"] = lis.wait_all_released(timeout=5.0)
        result["elapsed"] = time.monotonic() - t0

    t = threading.Thread(target=worker)
    t.start()
    time.sleep(0.1)
    hub.mute()
    hub.unmute()  # reset clears _held and must notify the waiter
    t.join(timeout=2.0)
    assert not t.is_alive()
    assert result["released"] is True
    assert result["elapsed"] < 2.0  # woken, not timed out


def test_adv17_tap_combo_pressed_while_muted_never_phantom_triggers(
    quartz, fake_center, monkeypatch
):
    """ADV-17: a TAP-mode combo (re-paste) held before/into a mute must not
    fire on the release seen after unmute; a fresh clean tap fires once."""
    fired = []
    hub = EventTapHub()
    lis = HotkeyListener(
        keys=["cmd", "ctrl"], on_trigger=lambda: fired.append(1), hub=hub
    )
    lis.start()
    d = _FlagDriver(hub, monkeypatch)
    d.modifier(_CMD_MASK | _CTRL_MASK)  # combo down (live)
    hub.mute()  # window opens mid-hold
    hub.unmute()  # per-hold state wiped
    d.modifier(0)  # release: must NOT trigger
    assert fired == []
    d.modifier(_CMD_MASK | _CTRL_MASK)  # fresh clean tap
    d.modifier(0)
    assert fired == [1]


# ===========================================================================
# E. stop() semantics
# ===========================================================================


def test_adv18_stopping_one_listener_leaves_the_other_dispatching(quartz, fake_center):
    """ADV-18: stop() of listener A must not gate or reset listener B."""
    hub = EventTapHub()
    a = HotkeyListener(keys=["ctrl", "shift"], on_activate=lambda: None, hub=hub)
    b_spy = _Spy("b")
    a.start()
    hub.register(b_spy)
    a.stop()
    _send_key(hub)
    assert b_spy.events == [hk.Quartz.kCGEventKeyDown]
    assert b_spy.resets == 0  # stop(a) must not reset b
    assert hub._tap is not None


def test_adv19_stop_clears_held_state_and_unblocks_waiter(quartz, fake_center, monkeypatch):
    """ADV-19: stop() mid-hold clears the shadow state and wakes a blocked
    wait_all_released() worker."""
    hub = EventTapHub()
    lis = HotkeyListener(keys=["ctrl", "shift"], on_activate=lambda: None, hub=hub)
    lis.start()
    d = _FlagDriver(hub, monkeypatch)
    d.modifier(_CTRL_MASK | _SHIFT_MASK)
    assert lis._held
    result = {}

    def worker():
        result["released"] = lis.wait_all_released(timeout=5.0)

    t = threading.Thread(target=worker)
    t.start()
    time.sleep(0.1)
    lis.stop()
    t.join(timeout=2.0)
    assert not t.is_alive()
    assert result["released"] is True
    assert lis._held == {} and lis._active is False


def test_adv20_release_after_stop_and_restart_never_phantom_fires(
    quartz, fake_center, monkeypatch
):
    """ADV-20: keys held across a stop()/start() cycle (set_hotkeys) must not
    phantom-fire callbacks when they are finally released; the NEXT fresh hold
    works normally.

    Amended when ADV-15 was fixed: stop() during an ACTIVE hold now fires the
    ONE balancing on_deactivate — that is the recording's stop signal, not a
    phantom (without it the dictation strands until max_seconds; see
    HotkeyListener.reset_hold_state). The phantom-fire claim this test exists
    for is unchanged: the later physical RELEASE must fire nothing."""
    events = []
    hub = EventTapHub()
    lis = HotkeyListener(
        keys=["ctrl", "shift"],
        on_activate=lambda: events.append("on"),
        on_deactivate=lambda: events.append("off"),
        hub=hub,
    )
    lis.start()
    d = _FlagDriver(hub, monkeypatch)
    d.modifier(_CTRL_MASK | _SHIFT_MASK)
    assert events == ["on"]
    lis.stop()  # force-ends the active hold: the balancing off fires here
    assert events == ["on", "off"]
    lis.start()
    d.modifier(0)  # release seen by the restarted listener: no shadow state
    assert events == ["on", "off"]  # no phantom off (and no phantom trigger)
    d.modifier(_CTRL_MASK | _SHIFT_MASK)
    d.modifier(0)
    assert events == ["on", "off", "on", "off"]


# ===========================================================================
# F. Watchdog
# ===========================================================================


def test_adv21_alternating_disable_patterns_reenable_never_recreate(quartz, fake_center):
    """ADV-21: disabled → healthy → disabled again must produce TWO cheap
    re-enables and never a spurious recreate (the sticky flag must clear on a
    healthy tick)."""
    hub = EventTapHub()
    hub.register(_Spy())
    quartz["enabled"] = False
    assert hub.watchdog_tick() == "re-enabled"  # tick 1: disabled
    assert hub.watchdog_tick() is None          # tick 2: healthy again
    quartz["enabled"] = False
    assert hub.watchdog_tick() == "re-enabled"  # tick 3: disabled AGAIN
    assert quartz["creates"] == 1
    assert quartz["invalidated"] == 0


def test_adv22_watchdog_keeps_retrying_after_repeated_create_failures(
    quartz, fake_center, monkeypatch
):
    """ADV-22: recreate failing for several consecutive ticks returns None
    each time but keeps ATTEMPTING a create while listeners exist; the first
    successful create reports 'recreated' and delivery resumes."""
    spy = _Spy()
    hub = EventTapHub()
    hub.register(spy)
    monkeypatch.setattr(et.Quartz, "CGEventTapEnable", lambda tap, on: None)
    quartz["enabled"] = False
    assert hub.watchdog_tick() == "re-enabled"
    quartz["fail_create"] = True
    assert hub.watchdog_tick() is None  # recreate attempted, create failed
    creates_after_first_fail = quartz["creates"]
    assert hub.watchdog_tick() is None  # keeps retrying...
    assert hub.watchdog_tick() is None
    assert quartz["creates"] == creates_after_first_fail + 2  # one try per tick
    quartz["fail_create"] = False
    assert hub.watchdog_tick() == "recreated"
    assert hub._tap is not None
    assert hub._listeners == [spy]
    _send_key(hub)
    assert spy.events == [hk.Quartz.kCGEventKeyDown]


def test_adv23_watchdog_never_resurrects_after_full_teardown(quartz, fake_center):
    """ADV-23: all listeners unregistered + destroy() (the shutdown order):
    any number of later ticks must do nothing."""
    spy = _Spy()
    hub = EventTapHub()
    hub.register(spy)
    hub.unregister(spy)
    hub.destroy()
    for _ in range(5):
        assert hub.watchdog_tick() is None
    assert quartz["creates"] == 1
    assert hub._tap is None


def test_adv24_watchdog_recreate_preserves_mute_and_registrations(
    quartz, fake_center, monkeypatch
):
    """ADV-24: a watchdog-driven recreate while MUTED (window open when the
    tap dies) must keep the hub muted and the listeners registered."""
    spy = _Spy()
    hub = EventTapHub()
    hub.register(spy)
    hub.mute()
    monkeypatch.setattr(et.Quartz, "CGEventTapEnable", lambda tap, on: None)
    quartz["enabled"] = False
    assert hub.watchdog_tick() == "re-enabled"
    assert hub.watchdog_tick() == "recreated"
    assert hub._listeners == [spy]
    _send_key(hub)
    assert spy.events == []  # STILL muted after the rebuild
    hub.unmute()
    _send_key(hub)
    assert spy.events == [hk.Quartz.kCGEventKeyDown]


def test_adv25_watchdog_rebuilds_missing_tap_while_muted_and_stays_muted(quartz, fake_center):
    """ADV-25: tap lost entirely (failed recreate) while muted: the watchdog
    rebuilds it and the mute gate survives."""
    spy = _Spy()
    hub = EventTapHub()
    hub.register(spy)
    hub.mute()
    quartz["fail_create"] = True
    assert hub.recreate() is False  # the tap is now gone
    assert hub._tap is None
    quartz["fail_create"] = False
    assert hub.watchdog_tick() == "recreated"
    _send_key(hub)
    assert spy.events == []  # muted preserved across the resurrect
    hub.unmute()
    _send_key(hub)
    assert spy.events == [hk.Quartz.kCGEventKeyDown]


def test_adv26_destroy_is_exception_safe_and_watchdog_recovers(
    quartz, fake_center, monkeypatch
):
    """ADV-26: Quartz raising during destroy (dead mach port) must not leak a
    half-dead tap: the tap reference is cleared and the watchdog can rebuild."""
    spy = _Spy()
    hub = EventTapHub()
    hub.register(spy)

    def exploding_enable(tap, on):
        raise OSError("mach port is dead")

    monkeypatch.setattr(et.Quartz, "CGEventTapEnable", exploding_enable)
    hub.destroy()  # must not raise
    assert hub._tap is None
    monkeypatch.setattr(
        et.Quartz, "CGEventTapEnable",
        lambda tap, on: quartz.__setitem__("enabled", bool(on)),
    )
    assert hub.watchdog_tick() == "recreated"  # listeners exist -> rebuild
    _send_key(hub)
    assert spy.events == [hk.Quartz.kCGEventKeyDown]


# ===========================================================================
# G. Wake / session notifications at the worst time
# ===========================================================================


def test_adv27_wake_block_after_destroy_is_safe_and_does_not_resurrect(quartz, fake_center):
    """ADV-27: a wake notification delivered AFTER shutdown's destroy() (the
    block is still registered) must not crash and must not resurrect the tap."""
    spy = _Spy()
    hub = EventTapHub()
    hub.register(spy)
    hub.unregister(spy)
    hub.destroy()
    creates_before = quartz["creates"]
    for _name, _obj, _queue, block in fake_center.added:
        block(object())  # late wake notification
    assert hub._tap is None
    assert quartz["creates"] == creates_before


def test_adv28_wake_block_while_muted_keeps_the_gate_closed(quartz, fake_center):
    """ADV-28: a sleep/wake recreate while a window is open must come back
    MUTED — a fresh tap must not leak events past an open settings window."""
    spy = _Spy()
    hub = EventTapHub()
    hub.register(spy)
    hub.mute()
    for _name, _obj, _queue, block in fake_center.added:
        block(object())
    assert quartz["creates"] == 3  # initial + one recreate per notification
    _send_key(hub)
    assert spy.events == []  # gate survived the wake rebuild
    hub.unmute()
    _send_key(hub)
    assert spy.events == [hk.Quartz.kCGEventKeyDown]


def test_adv29_wake_block_with_failing_create_leaves_recoverable_state(quartz, fake_center):
    """ADV-29: the wake recreate racing a create failure must not crash; the
    watchdog then resurrects the tap on a later tick."""
    spy = _Spy()
    hub = EventTapHub()
    hub.register(spy)
    quartz["fail_create"] = True
    for _name, _obj, _queue, block in fake_center.added:
        block(object())  # recreate: destroy ok, create fails
    assert hub._tap is None
    quartz["fail_create"] = False
    assert hub.watchdog_tick() == "recreated"
    _send_key(hub)
    assert spy.events == [hk.Quartz.kCGEventKeyDown]


# ===========================================================================
# H. App integration (real App, real listeners, faked Quartz)
# ===========================================================================


def test_adv30_suspend_resume_cycles_and_double_calls(app, quartz, monkeypatch):
    """ADV-30: suspend;suspend / resume;resume and repeated cycles keep the
    mute flag coherent, perform zero tap create/destroy, and end dispatching."""
    app.resume_hotkeys()
    fired = []
    monkeypatch.setattr(app.hotkey, "_on_activate", lambda: fired.append("on"))
    monkeypatch.setattr(app.hotkey, "_on_deactivate", lambda: fired.append("off"))
    d = _FlagDriver(app.tap_hub, monkeypatch)
    for _ in range(3):
        app.suspend_hotkeys()
        app.suspend_hotkeys()  # double suspend
        assert app.tap_hub._muted is True
        d.modifier(_CTRL_MASK | _SHIFT_MASK)
        d.modifier(0)
        assert fired == []  # gated every cycle
        app.resume_hotkeys()
        app.resume_hotkeys()  # double resume
        assert app.tap_hub._muted is False
    d.modifier(_CTRL_MASK | _SHIFT_MASK)
    d.modifier(0)
    assert fired == ["on", "off"]
    assert quartz["creates"] == 1
    assert quartz["invalidated"] == 0


def test_adv31_set_hotkeys_while_suspended_swaps_listeners_and_unmutes(app, quartz):
    """ADV-31: Save while the settings window is open (suspended): the NEW
    listeners must end registered on the same single tap, the OLD ones gone,
    and the hub unmuted."""
    app.resume_hotkeys()
    old = [lis for _l, lis in app.iter_hotkeys()]
    app.suspend_hotkeys()
    app.set_hotkeys(["cmd", "shift"], ["cmd", "alt", "r"], ["ctrl", "alt"])
    assert app.tap_hub._muted is False
    new = [lis for _l, lis in app.iter_hotkeys()]
    assert app.tap_hub._listeners == new
    for stale in old:
        assert all(reg is not stale for reg in app.tap_hub._listeners)
    assert quartz["creates"] == 1
    assert quartz["invalidated"] == 0
    assert app._failed_hotkeys == set()
    assert app.config.keys == ["cmd", "shift"]


def test_adv32_shutdown_then_watchdog_then_second_shutdown(app, quartz):
    """ADV-32: after shutdown the watchdog entry point must report nothing and
    never resurrect the tap; a second shutdown is harmless."""
    app.resume_hotkeys()
    app.shutdown()
    assert menubar.reenable_disabled_taps(app) == []
    assert quartz["creates"] == 1
    assert app.tap_hub._tap is None
    app.shutdown()  # double shutdown
    assert quartz["invalidated"] == 1  # destroyed exactly once


def test_adv33_exact_modifier_semantics_preserved_through_the_hub(app, quartz, monkeypatch):
    """ADV-33: the #21 exact-match semantics through the single tap:
    ⌘⌃⇧ fires NOTHING (superset of both ctrl+shift hold and cmd+ctrl tap);
    clean cmd+ctrl fires ONLY re-paste; clean ctrl+shift drives ONLY the
    dictation hold; clean cmd+alt fires ONLY correction."""
    app.resume_hotkeys()
    events = []
    monkeypatch.setattr(app.hotkey, "_on_activate", lambda: events.append("dictate-on"))
    monkeypatch.setattr(app.hotkey, "_on_deactivate", lambda: events.append("dictate-off"))
    monkeypatch.setattr(app.repaste_hotkey, "_on_trigger", lambda: events.append("repaste"))
    monkeypatch.setattr(app.correction_hotkey, "_on_trigger", lambda: events.append("correct"))
    d = _FlagDriver(app.tap_hub, monkeypatch)

    d.modifier(_CMD_MASK | _CTRL_MASK | _SHIFT_MASK)  # superset chord
    d.modifier(0)
    assert events == []  # nothing may fire on a superset

    d.modifier(_CMD_MASK | _CTRL_MASK)  # clean re-paste tap
    d.modifier(0)
    assert events == ["repaste"]

    d.modifier(_CTRL_MASK | _SHIFT_MASK)  # clean dictation hold
    assert events == ["repaste", "dictate-on"]
    d.modifier(0)
    assert events == ["repaste", "dictate-on", "dictate-off"]

    d.modifier(_CMD_MASK | _ALT_MASK)  # clean correction tap
    d.modifier(0)
    assert events == ["repaste", "dictate-on", "dictate-off", "correct"]


def test_adv34_cmd_ctrl_alt_chord_fires_neither_tap_combo(app, quartz, monkeypatch):
    """ADV-34: ⌘⌃⌥ contains both cmd+ctrl (re-paste) and cmd+alt (correction);
    per #21 exact matching it must fire NEITHER — through the hub path."""
    app.resume_hotkeys()
    events = []
    monkeypatch.setattr(app.repaste_hotkey, "_on_trigger", lambda: events.append("repaste"))
    monkeypatch.setattr(app.correction_hotkey, "_on_trigger", lambda: events.append("correct"))
    d = _FlagDriver(app.tap_hub, monkeypatch)
    d.modifier(_CMD_MASK | _CTRL_MASK | _ALT_MASK)
    d.modifier(0)
    assert events == []
    # And both still work cleanly afterwards (per-hold contamination only).
    d.modifier(_CMD_MASK | _CTRL_MASK)
    d.modifier(0)
    d.modifier(_CMD_MASK | _ALT_MASK)
    d.modifier(0)
    assert events == ["repaste", "correct"]


def test_adv35_set_hotkeys_never_raises_even_on_an_invalid_key_name(app, quartz):
    """ADV-35: set_hotkeys' contract is 'Never raises (the caller is an ObjC
    delegate callback)'. An invalid key name (anything the recorder/config
    layer lets through, e.g. a non-ANSI character) must not escape — if it
    does, the old listeners are already stopped, so EVERY shortcut is dead and
    the exception unwinds into the ObjC delegate."""
    app.resume_hotkeys()
    try:
        app.set_hotkeys(["ctrl", "€"], ["cmd", "ctrl"], ["cmd", "alt"])
    except Exception as exc:  # noqa: BLE001 — the contract says: never raises
        pytest.fail(
            f"set_hotkeys raised {exc!r} after stopping the old listeners; "
            f"hub listeners now: {app.tap_hub._listeners!r} (all shortcuts dead)"
        )
    # Whatever it decided (reject or apply), the shortcuts must still be live.
    assert app.tap_hub._listeners, "no listener left registered after the failed save"


def test_adv36_resume_with_failing_create_degrades_then_watchdog_recovers(app, quartz):
    """ADV-36: resume_hotkeys with CGEventTapCreate failing must not raise,
    must report all three listeners degraded, and one watchdog pass after the
    permission returns must recover all three."""
    quartz["fail_create"] = True
    app.resume_hotkeys()  # contract: never raises
    assert app._failed_hotkeys == {"Hotkey", "Re-paste", "Correction"}
    assert app.tap_hub._muted is False
    assert app.tap_hub._tap is None
    quartz["fail_create"] = False
    recovered = menubar.reenable_disabled_taps(app)
    assert sorted(recovered) == ["Correction", "Hotkey", "Re-paste"]
    assert app._failed_hotkeys == set()
    assert app.tap_hub._listeners == [lis for _l, lis in app.iter_hotkeys()]
    assert app.tap_hub._tap is not None


def test_adv37_watchdog_does_not_retry_failed_listeners_while_suspended(app, quartz):
    """ADV-37: with a window open (suspended) the watchdog must NOT resurrect
    failed listeners — but it must still heal the tap itself."""
    quartz["fail_create"] = True
    app.resume_hotkeys()
    assert app._failed_hotkeys == {"Hotkey", "Re-paste", "Correction"}
    quartz["fail_create"] = False
    app.suspend_hotkeys()
    recovered = menubar.reenable_disabled_taps(app)
    assert recovered == []  # no listener resurrection while suspended
    assert app._failed_hotkeys == {"Hotkey", "Re-paste", "Correction"}
    app.resume_hotkeys()  # the real resume brings them back
    assert app._failed_hotkeys == set()


# ===========================================================================
# I. Heartbeat continuity
# ===========================================================================


def test_adv38_heartbeat_counter_survives_a_recreate(quartz, fake_center):
    """ADV-38: the liveness counter lives on the hub, not the tap: a recreate
    between two heartbeat reads must not lose the events already counted."""
    hub = EventTapHub()
    hub.register(_Spy())
    _send_key(hub)
    assert hub.recreate() is True
    _send_key(hub)
    assert hub.take_event_count() == 2
