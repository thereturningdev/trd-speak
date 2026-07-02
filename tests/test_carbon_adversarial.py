"""Adversarial tests for the Carbon hotkey backend and backend selection (#23).

Every test asserts INTENDED behavior from the docstrings/contracts of
flow.carbon_hotkey and flow.app — not current behavior. The Carbon layer is
mocked at the module seams (_register/_unregister/_ensure_handler,
modifiers_physically_down); the tap side is mocked at EventTapHub, exactly
like tests/test_carbon_hotkey.py and tests/test_app_backend_selection.py.
No real hotkey registration, no real CGEventTap, no machine state touched.

Case ids CADV-01..CADV-39 map to the adversarial report
(adversarial-test-report.html).
"""

import threading
import time

import pytest

import flow.app as app_mod
import flow.carbon_hotkey as ch
from flow import menubar
from flow.app import App, make_hotkey
from flow.carbon_hotkey import CarbonHotkey
from flow.config import Config
from flow.event_tap import EventTapHub
from flow.hotkey import HotkeyListener


# -- fixtures (same seams as the established suites) ---------------------------

@pytest.fixture(autouse=True)
def registry_hygiene():
    """Never let a test leak entries into the process-global dispatch
    registry: later tests (and later suites) must start clean."""
    before = set(ch._registry)
    yield
    for k in set(ch._registry) - before:
        ch._registry.pop(k, None)


@pytest.fixture
def carbon(monkeypatch):
    calls = {"registered": {}, "unregistered": [], "next_ref": 0, "fail": False}

    def fake_register(vk, mask, hkid):
        if calls["fail"]:
            return -9878, None  # eventHotKeyExistsErr
        calls["next_ref"] += 1
        ref = f"ref-{calls['next_ref']}"
        calls["registered"][hkid] = (vk, mask, ref)
        return 0, ref

    def fake_unregister(ref):
        calls["unregistered"].append(ref)
        calls["registered"] = {
            k: v for k, v in calls["registered"].items() if v[2] != ref
        }
        return 0

    monkeypatch.setattr(ch, "_register", fake_register)
    monkeypatch.setattr(ch, "_unregister", fake_unregister)
    monkeypatch.setattr(ch, "_ensure_handler", lambda: None)
    return calls


@pytest.fixture
def hub_registry(monkeypatch):
    registered = []
    monkeypatch.setattr(
        EventTapHub, "register", lambda self, lis: registered.append(lis)
    )
    monkeypatch.setattr(EventTapHub, "unregister", lambda self, lis: None)
    import flow.event_tap as et

    def forbidden(*args, **kwargs):
        raise AssertionError("CGEventTapCreate must not be touched")

    monkeypatch.setattr(et.Quartz, "CGEventTapCreate", forbidden)
    return registered


@pytest.fixture
def app(monkeypatch, tmp_path, carbon, hub_registry):
    """App with a MIXED backend layout: Carbon dictate (hold mode), Carbon
    re-paste (tap mode), tap-hub correction (modifier-only)."""
    monkeypatch.setattr(
        app_mod.engine_state,
        "save_engine",
        lambda name, path=tmp_path / "engine": path.write_text(name),
    )
    cfg = Config()
    cfg.keys = ["cmd", "ctrl", "d"]          # Carbon, hold mode
    cfg.repaste_keys = ["cmd", "shift", "r"]  # Carbon, tap mode
    # correct_keys stays the modifier-only default -> tap hub
    return App(cfg)


def _ids(carbon):
    return list(carbon["registered"])


def _neuter(app, events):
    """Replace the dictate hold callbacks with recorders so simulated Carbon
    events never start a real recording thread."""
    app.hotkey._on_activate = lambda: events.append("on")
    app.hotkey._on_deactivate = lambda: events.append("off")


# ================================ lifecycle ====================================

def test_cadv01_start_stop_churn_ids_never_reused_registry_clean(carbon):
    hk = CarbonHotkey(["cmd", "ctrl", "v"], on_trigger=lambda: None, name="t")
    seen_ids = []
    for _ in range(5):
        hk.start()
        seen_ids.append(hk._hotkey_id)
        hk.stop()
    assert len(set(seen_ids)) == 5, "hotkey ids must never be reused"
    assert sorted(seen_ids) == seen_ids, "ids must be strictly increasing"
    assert carbon["registered"] == {}
    assert all(i not in ch._registry for i in seen_ids)


def test_cadv02_ensure_handler_failure_leaves_nothing_half_done(carbon, monkeypatch):
    def broken():
        raise RuntimeError("InstallEventHandler OSStatus -50")

    monkeypatch.setattr(ch, "_ensure_handler", broken)
    hk = CarbonHotkey(["cmd", "ctrl", "v"], on_trigger=lambda: None, name="t")
    before = set(ch._registry)
    with pytest.raises(RuntimeError):
        hk.start()
    assert hk._ref is None and hk._hotkey_id is None
    assert set(ch._registry) == before
    assert carbon["registered"] == {}
    # Handler heals (e.g. transient): the #22 retry contract must succeed.
    monkeypatch.setattr(ch, "_ensure_handler", lambda: None)
    hk.start()
    assert len(carbon["registered"]) == 1
    hk.stop()


def test_cadv03_two_hotkeys_same_combo_are_independent(carbon):
    fired_a, fired_b = [], []
    a = CarbonHotkey(["cmd", "ctrl", "v"], on_trigger=lambda: fired_a.append(1), name="a")
    b = CarbonHotkey(["cmd", "ctrl", "v"], on_trigger=lambda: fired_b.append(1), name="b")
    a.start()
    b.start()
    assert a._hotkey_id != b._hotkey_id
    a.stop()
    # b must still dispatch after a's stop.
    ch._dispatch(b._hotkey_id, ch.kEventHotKeyPressed)
    ch._dispatch(b._hotkey_id, ch.kEventHotKeyReleased)
    assert fired_a == [] and fired_b == [1]
    b.stop()
    assert carbon["registered"] == {}


def test_cadv04_stop_survives_unregister_raising_and_restart_works(carbon, monkeypatch):
    hk = CarbonHotkey(["cmd", "ctrl", "v"], on_trigger=lambda: None, name="t")
    hk.start()
    hkid = hk._hotkey_id

    def boom(ref):
        raise RuntimeError("UnregisterEventHotKey exploded")

    monkeypatch.setattr(ch, "_unregister", boom)
    hk.stop()  # contract: stop() never raises
    assert hk._ref is None
    assert hkid not in ch._registry
    monkeypatch.setattr(ch, "_unregister", lambda ref: 0)
    hk.start()  # restart after the failed unregister must still work
    assert hk._ref is not None
    hk.stop()


def test_cadv05_on_trigger_calling_stop_reentrantly_is_safe(carbon):
    hk = CarbonHotkey(["cmd", "ctrl", "v"], name="t", on_trigger=lambda: None)
    fired = []

    def trigger():
        fired.append(1)
        hk.stop()  # reentrant stop from inside the dispatch

    hk._on_trigger = trigger
    hk.start()
    hkid = hk._hotkey_id
    ch._dispatch(hkid, ch.kEventHotKeyPressed)
    ch._dispatch(hkid, ch.kEventHotKeyReleased)  # must not raise/deadlock
    assert fired == [1]
    assert hk._ref is None and hkid not in ch._registry
    # A stale event after the reentrant stop is a no-op.
    ch._dispatch(hkid, ch.kEventHotKeyReleased)
    assert fired == [1]


def test_cadv06_on_activate_calling_stop_gets_exactly_one_deactivate(carbon):
    events = []
    hk = CarbonHotkey(["cmd", "ctrl", "d"], name="d",
                      on_activate=lambda: None, on_deactivate=lambda: None)

    def activate():
        events.append("on")
        hk.stop()

    hk._on_activate = activate
    hk._on_deactivate = lambda: events.append("off")
    hk.start()
    hkid = hk._hotkey_id
    ch._dispatch(hkid, ch.kEventHotKeyPressed)
    assert events == ["on", "off"], "stop mid-hold owes exactly one deactivate"
    ch._dispatch(hkid, ch.kEventHotKeyReleased)  # stale; must not double-fire
    assert events == ["on", "off"]


# ============================== dispatch hygiene ================================

def test_cadv07_duplicate_released_fires_trigger_once(carbon):
    fired = []
    hk = CarbonHotkey(["cmd", "ctrl", "v"], on_trigger=lambda: fired.append(1), name="t")
    hk.start()
    hkid = hk._hotkey_id
    ch._dispatch(hkid, ch.kEventHotKeyPressed)
    ch._dispatch(hkid, ch.kEventHotKeyReleased)
    ch._dispatch(hkid, ch.kEventHotKeyReleased)  # duplicate/stale released
    assert fired == [1]
    hk.stop()


def test_cadv08_unknown_event_kind_is_a_noop_and_state_survives(carbon):
    events = []
    hk = CarbonHotkey(["cmd", "ctrl", "d"], name="d",
                      on_activate=lambda: events.append("on"),
                      on_deactivate=lambda: events.append("off"))
    hk.start()
    hkid = hk._hotkey_id
    ch._dispatch(hkid, 999)  # neither kEventHotKeyPressed(5) nor Released(6)
    assert events == []
    ch._dispatch(hkid, ch.kEventHotKeyPressed)
    ch._dispatch(hkid, 999)  # unknown kind mid-hold must not clear the hold
    ch._dispatch(hkid, ch.kEventHotKeyReleased)
    assert events == ["on", "off"]
    hk.stop()


def test_cadv09_unreadable_event_is_declined_not_swallowed(carbon, monkeypatch):
    hk = CarbonHotkey(["cmd", "ctrl", "v"], on_trigger=lambda: None, name="t")
    hk.start()

    def unreadable(event):
        raise RuntimeError("GetEventParameter failed (OSStatus -9870)")

    monkeypatch.setattr(ch, "_event_hotkey_id", unreadable)
    assert ch._handle_carbon_event(object()) == ch._EVENT_NOT_HANDLED
    hk.stop()


def test_cadv10_handler_returns_0_even_if_dispatch_itself_raises(carbon, monkeypatch):
    hk = CarbonHotkey(["cmd", "ctrl", "v"], on_trigger=lambda: None, name="t")
    hk.start()
    hkid = hk._hotkey_id
    monkeypatch.setattr(ch, "_event_hotkey_id", lambda e: (ch._SIGNATURE, hkid))
    monkeypatch.setattr(ch, "_event_kind", lambda e: ch.kEventHotKeyPressed)

    def boom(hkid_, kind):
        raise RuntimeError("dispatch blew up")

    monkeypatch.setattr(ch, "_dispatch", boom)
    # Defense in depth: an exception must never escape a Carbon handler.
    assert ch._handle_carbon_event(object()) == 0
    hk.stop()


def test_cadv11_dispatch_guards_a_raising_handle(carbon, monkeypatch):
    hk = CarbonHotkey(["cmd", "ctrl", "v"], on_trigger=lambda: None, name="t")
    hk.start()
    hkid = hk._hotkey_id
    monkeypatch.setattr(
        hk, "_handle",
        lambda kind: (_ for _ in ()).throw(RuntimeError("handle blew up")),
        raising=False,
    )
    ch._dispatch(hkid, ch.kEventHotKeyPressed)  # must not raise
    hk.stop()


def test_cadv12_stale_event_with_a_pre_restart_id_never_fires(carbon):
    fired = []
    hk = CarbonHotkey(["cmd", "ctrl", "v"], on_trigger=lambda: fired.append(1), name="t")
    hk.start()
    old_id = hk._hotkey_id
    hk.stop()
    hk.start()  # new registration -> NEW id (ids never reused)
    assert hk._hotkey_id != old_id
    ch._dispatch(old_id, ch.kEventHotKeyPressed)
    ch._dispatch(old_id, ch.kEventHotKeyReleased)
    assert fired == [], "an event racing a stop/restart must hit the void"
    hk.stop()


def test_cadv13_dispatch_racing_stop_popped_id_is_a_noop(carbon):
    fired = []
    hk = CarbonHotkey(["cmd", "ctrl", "v"], on_trigger=lambda: fired.append(1), name="t")
    hk.start()
    hkid = hk._hotkey_id
    ch._dispatch(hkid, ch.kEventHotKeyPressed)
    # Simulate the registry pop happening between the OS enqueuing the
    # released event and the dispatch running.
    ch._registry.pop(hkid)
    ch._dispatch(hkid, ch.kEventHotKeyReleased)
    assert fired == []
    hk.stop()


# ============================== wait_all_released ===============================

def test_cadv14_zero_timeout_while_pressed_is_false_and_fast(carbon, monkeypatch):
    monkeypatch.setattr(ch, "modifiers_physically_down", lambda: False)
    hk = CarbonHotkey(["cmd", "ctrl", "v"], on_trigger=lambda: None, name="t")
    hk.start()
    ch._dispatch(hk._hotkey_id, ch.kEventHotKeyPressed)
    t0 = time.monotonic()
    assert hk.wait_all_released(timeout=0) is False
    assert time.monotonic() - t0 < 0.5
    hk.stop()


def test_cadv15_negative_timeout_with_modifiers_held_is_false_and_fast(carbon, monkeypatch):
    monkeypatch.setattr(ch, "modifiers_physically_down", lambda: True)
    hk = CarbonHotkey(["cmd", "ctrl", "v"], on_trigger=lambda: None, name="t")
    hk.start()
    t0 = time.monotonic()
    assert hk.wait_all_released(timeout=-1) is False
    assert time.monotonic() - t0 < 0.5
    hk.stop()


def test_cadv16_os_modifier_check_raising_mid_poll_returns_false(carbon, monkeypatch):
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            return True  # first poll: modifiers held
        raise RuntimeError("CGEventSourceFlagsState died")  # then the OS check dies

    monkeypatch.setattr(ch, "modifiers_physically_down", flaky)
    hk = CarbonHotkey(["cmd", "ctrl", "v"], on_trigger=lambda: None, name="t")
    hk.start()
    assert hk.wait_all_released(timeout=1.0) is False
    hk.stop()


def test_cadv17_concurrent_waiters_all_unblock_on_release(carbon, monkeypatch):
    monkeypatch.setattr(ch, "modifiers_physically_down", lambda: False)
    hk = CarbonHotkey(["cmd", "ctrl", "v"], on_trigger=lambda: None, name="t")
    hk.start()
    hkid = hk._hotkey_id
    ch._dispatch(hkid, ch.kEventHotKeyPressed)
    results = {}

    def waiter(key):
        results[key] = hk.wait_all_released(timeout=2.0)

    threads = [threading.Thread(target=waiter, args=(i,)) for i in range(3)]
    for t in threads:
        t.start()
    time.sleep(0.05)
    ch._dispatch(hkid, ch.kEventHotKeyReleased)
    for t in threads:
        t.join(timeout=2.0)
    assert results == {0: True, 1: True, 2: True}
    hk.stop()


def test_cadv18_reset_hold_state_unblocks_a_waiter(carbon, monkeypatch):
    monkeypatch.setattr(ch, "modifiers_physically_down", lambda: False)
    hk = CarbonHotkey(["cmd", "ctrl", "v"], on_trigger=lambda: None, name="t")
    hk.start()
    ch._dispatch(hk._hotkey_id, ch.kEventHotKeyPressed)
    result = {}

    def waiter():
        result["ok"] = hk.wait_all_released(timeout=2.0)

    t = threading.Thread(target=waiter)
    t.start()
    time.sleep(0.05)
    hk.reset_hold_state()
    t.join(timeout=2.0)
    assert result["ok"] is True
    hk.stop()


def test_cadv19_zero_timeout_when_already_released_is_true(carbon, monkeypatch):
    monkeypatch.setattr(ch, "modifiers_physically_down", lambda: False)
    hk = CarbonHotkey(["cmd", "ctrl", "v"], on_trigger=lambda: None, name="t")
    hk.start()
    assert hk.wait_all_released(timeout=0) is True
    hk.stop()


# ============================== reset_hold_state ================================

def test_cadv20_tap_mode_reset_never_synthesizes_a_trigger(carbon):
    fired = []
    hk = CarbonHotkey(["cmd", "ctrl", "v"], on_trigger=lambda: fired.append(1), name="t")
    hk.start()
    hkid = hk._hotkey_id
    ch._dispatch(hkid, ch.kEventHotKeyPressed)
    hk.reset_hold_state()
    assert fired == []
    # The hold was forgotten: the (now stale) released must not fire either.
    ch._dispatch(hkid, ch.kEventHotKeyReleased)
    assert fired == []
    hk.stop()


def test_cadv21_double_reset_fires_the_balancing_deactivate_once(carbon):
    events = []
    hk = CarbonHotkey(["cmd", "ctrl", "d"], name="d",
                      on_activate=lambda: events.append("on"),
                      on_deactivate=lambda: events.append("off"))
    hk.start()
    ch._dispatch(hk._hotkey_id, ch.kEventHotKeyPressed)
    hk.reset_hold_state()
    hk.reset_hold_state()  # second reset: nothing left to balance
    assert events == ["on", "off"]
    hk.stop()
    assert events == ["on", "off"]  # stop after reset owes nothing more


def test_cadv22_stop_mid_hold_survives_a_raising_deactivate(carbon):
    def boom():
        raise RuntimeError("deactivate exploded")

    hk = CarbonHotkey(["cmd", "ctrl", "d"], name="d",
                      on_activate=lambda: None, on_deactivate=boom)
    hk.start()
    hkid = hk._hotkey_id
    ch._dispatch(hkid, ch.kEventHotKeyPressed)
    hk.stop()  # contract: stop() never raises
    assert hk._ref is None and hkid not in ch._registry
    assert hk._pressed is False


# ====================== classification & factory validation ======================

def test_cadv23_aliases_case_and_whitespace_route_to_carbon(carbon):
    assert ch.is_carbon_combo(["Command", "OPTION", " V "]) is True
    lis = make_hotkey(["Command", "OPTION", " V "], hub=EventTapHub(),
                      on_trigger=lambda: None, name="t")
    assert isinstance(lis, CarbonHotkey)
    lis.start()
    (vk, mask, _ref) = next(iter(carbon["registered"].values()))
    assert vk == 9  # kVK_ANSI_V
    assert mask == 0x100 | 0x800  # cmdKey | optionKey
    lis.stop()


def test_cadv24_duplicate_modifier_tokens_collapse(carbon):
    lis = make_hotkey(["cmd", "cmd", "v"], hub=EventTapHub(),
                      on_trigger=lambda: None, name="t")
    assert isinstance(lis, CarbonHotkey)
    lis.start()
    (_vk, mask, _ref) = next(iter(carbon["registered"].values()))
    assert mask == 0x100  # cmdKey once, not doubled/corrupted
    lis.stop()


def test_cadv25_duplicate_char_tokens_still_route_by_effective_chord(carbon):
    """["cmd","v","v"] IS the cmd+v chord (keys are a set — _targets is a
    frozenset). is_carbon_combo's contract is 'EXACTLY one non-modifier key';
    counting the duplicate as two keys silently demotes the combo to the tap
    backend, reintroducing every tap failure mode for a combo Carbon fully
    supports — and making backend choice depend on token duplication."""
    assert ch.is_carbon_combo(["cmd", "v", "v"]) is True
    lis = make_hotkey(["cmd", "v", "v"], hub=EventTapHub(),
                      on_trigger=lambda: None, name="t")
    assert isinstance(lis, CarbonHotkey)


def test_cadv26_backend_description_not_misleading_for_multi_char_combo():
    """['cmd','a','b'] is NOT modifier-only; the status line must not claim it
    is (the description is user-facing in the settings window)."""
    desc = ch.combo_backend_description(["cmd", "a", "b"])
    assert "Modifier-only" not in desc


def test_cadv27_make_hotkey_rejects_an_empty_combo():
    with pytest.raises(ValueError):
        make_hotkey([], hub=EventTapHub(), on_trigger=lambda: None, name="t")


def test_cadv28_make_hotkey_unknown_name_raises_valueerror_registers_nothing(carbon):
    with pytest.raises(ValueError):
        make_hotkey(["cmd", "banana"], hub=EventTapHub(),
                    on_trigger=lambda: None, name="t")
    assert carbon["registered"] == {}


def test_cadv28b_is_carbon_combo_nonstring_token_raises_valueerror():
    """Contract: 'Raises ValueError for unknown key names' — a non-string
    token (hand-edited config) must surface as the documented ValueError, not
    an AttributeError."""
    with pytest.raises(ValueError):
        ch.is_carbon_combo(["cmd", 5])


# ========================= App: suspend / resume cycles ==========================

def test_cadv29_double_suspend_single_resume_registers_once(app, carbon):
    app._start_all_hotkeys()
    assert len(carbon["registered"]) == 2  # dictate + re-paste on Carbon
    app.suspend_hotkeys()
    app.suspend_hotkeys()  # window-open callback firing twice
    assert carbon["registered"] == {}
    app.resume_hotkeys()
    assert len(carbon["registered"]) == 2, "one registration per hotkey, not per suspend"
    app.shutdown()


def test_cadv30_resume_without_suspend_is_idempotent(app, carbon):
    app._start_all_hotkeys()
    app.resume_hotkeys()  # spurious resume (no matching suspend)
    app.resume_hotkeys()
    assert len(carbon["registered"]) == 2
    assert app.tap_hub._muted is False
    app.shutdown()


def test_cadv31_suspend_then_save_brings_hotkeys_live_without_resume(app, carbon):
    app._start_all_hotkeys()
    app.suspend_hotkeys()
    assert carbon["registered"] == {}
    # Save from the settings window: set_hotkeys alone must leave the new
    # shortcuts live ("After this call the new taps are live, so no separate
    # resume_hotkeys is needed").
    app.set_hotkeys(["cmd", "ctrl", "d"], ["cmd", "shift", "r"], ["cmd", "alt", "c"])
    assert len(carbon["registered"]) == 3  # all three Carbon now
    assert app._hotkeys_suspended is False
    assert app.tap_hub._muted is False
    app.shutdown()


def test_cadv32_failing_swap_keeps_old_carbon_registrations_dispatchable(app, carbon):
    app._start_all_hotkeys()
    fired = []
    app.repaste_hotkey._on_trigger = lambda: fired.append(1)
    old_repaste_id = app.repaste_hotkey._hotkey_id
    old_config = (list(app.config.keys), list(app.config.repaste_keys),
                  list(app.config.correct_keys))
    app.set_hotkeys(["cmd", "ctrl", "d"], ["cmd", "banana"], ["cmd", "alt"])
    # Config untouched, old registration still present AND still firing.
    assert (app.config.keys, app.config.repaste_keys,
            app.config.correct_keys) == old_config
    assert old_repaste_id in carbon["registered"]
    ch._dispatch(old_repaste_id, ch.kEventHotKeyPressed)
    ch._dispatch(old_repaste_id, ch.kEventHotKeyReleased)
    assert fired == [1]
    app.shutdown()


def test_cadv33_swap_whose_carbon_start_fails_commits_config_and_recovers(app, carbon):
    reported = []
    app.on_hotkeys_degraded = lambda labels: reported.append(tuple(labels))
    app._start_all_hotkeys()
    carbon["fail"] = True
    app.set_hotkeys(["ctrl", "shift"], ["cmd", "shift", "x"], ["cmd", "alt"])
    # Config committed BEFORE the starts (consistent, self-reported state).
    assert app.config.repaste_keys == ["cmd", "shift", "x"]
    assert "Re-paste" in app._failed_hotkeys
    assert reported[-1] == ("Re-paste",)
    # The clashing app quits: the watchdog recovers with the NEW combo.
    carbon["fail"] = False
    assert menubar.reenable_disabled_taps(app) == ["Re-paste"]
    assert reported[-1] == ()
    (vk, _mask, _ref) = next(iter(carbon["registered"].values()))
    assert vk == 7  # kVK_ANSI_X — the new combo, not the old one
    app.shutdown()


def test_cadv34_swap_mid_hold_carbon_to_tap_fires_one_deactivate(app, carbon, hub_registry):
    events = []
    _neuter(app, events)
    app._start_all_hotkeys()
    dictate_id = app.hotkey._hotkey_id
    ch._dispatch(dictate_id, ch.kEventHotKeyPressed)  # push-to-talk held
    assert events == ["on"]
    app.set_hotkeys(["ctrl", "shift"], ["cmd", "shift", "r"], ["cmd", "alt"])
    assert events == ["on", "off"], "the recording needs exactly one stop signal"
    assert isinstance(app.hotkey, HotkeyListener)
    assert dictate_id not in carbon["registered"]
    # A stale released for the dead Carbon id must do nothing.
    ch._dispatch(dictate_id, ch.kEventHotKeyReleased)
    assert events == ["on", "off"]
    app.shutdown()


def test_cadv35_swap_mid_hold_carbon_to_carbon_fires_one_deactivate(app, carbon):
    events = []
    _neuter(app, events)
    app._start_all_hotkeys()
    old_id = app.hotkey._hotkey_id
    ch._dispatch(old_id, ch.kEventHotKeyPressed)
    app.set_hotkeys(["cmd", "ctrl", "e"], ["cmd", "shift", "r"], ["cmd", "alt"])
    assert events == ["on", "off"]
    assert isinstance(app.hotkey, CarbonHotkey)
    new_id = app.hotkey._hotkey_id
    assert new_id != old_id and old_id not in carbon["registered"]
    ch._dispatch(old_id, ch.kEventHotKeyReleased)  # stale
    assert events == ["on", "off"]
    app.shutdown()


def test_cadv36_suspend_mid_hold_fires_one_deactivate_resume_stays_clean(app, carbon):
    events = []
    _neuter(app, events)
    app._start_all_hotkeys()
    old_id = app.hotkey._hotkey_id
    ch._dispatch(old_id, ch.kEventHotKeyPressed)
    app.suspend_hotkeys()
    assert events == ["on", "off"]
    app.resume_hotkeys()
    # The rebound registration must start with a clean (unpressed) slate:
    # a released event alone must not fire anything.
    _neuter(app, events)  # resume kept the same object; re-pin the recorders
    ch._dispatch(app.hotkey._hotkey_id, ch.kEventHotKeyReleased)
    assert events == ["on", "off"]
    app.shutdown()


def test_cadv37_watchdog_never_resurrects_suspended_failed_carbon(app, carbon):
    reported = []
    app.on_hotkeys_degraded = lambda labels: reported.append(tuple(labels))
    carbon["fail"] = True
    app.resume_hotkeys()
    assert app._failed_hotkeys == {"Hotkey", "Re-paste"}
    app.suspend_hotkeys()
    carbon["fail"] = False  # registration would now succeed…
    assert menubar.reenable_disabled_taps(app) == []  # …but suspended wins
    assert carbon["registered"] == {}
    app.resume_hotkeys()
    assert len(carbon["registered"]) == 2
    assert reported[-1] == ()
    app.shutdown()


def test_cadv38_shutdown_leaves_the_registry_clean_and_dispatch_dead(app, carbon):
    events = []
    _neuter(app, events)
    app._start_all_hotkeys()
    ids = _ids(carbon)
    app.shutdown()
    assert carbon["registered"] == {}
    for hkid in ids:
        assert hkid not in ch._registry
        ch._dispatch(hkid, ch.kEventHotKeyPressed)
        ch._dispatch(hkid, ch.kEventHotKeyReleased)
    assert events == []
    app.shutdown()  # double shutdown must be safe
    assert carbon["registered"] == {}


def test_cadv39_suspend_suspend_resume_mid_hold_one_deactivate_total(app, carbon):
    events = []
    _neuter(app, events)
    app._start_all_hotkeys()
    ch._dispatch(app.hotkey._hotkey_id, ch.kEventHotKeyPressed)
    app.suspend_hotkeys()
    app.suspend_hotkeys()
    app.resume_hotkeys()
    assert events == ["on", "off"], "exactly one balancing deactivate, ever"
    app.shutdown()
