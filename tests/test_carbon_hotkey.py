"""CarbonHotkey: RegisterEventHotKey-backed shortcuts (issue #23).

The Carbon layer is mocked at the module seams flow.carbon_hotkey exposes
(_register/_unregister/_ensure_handler and the event-accessor pair), so no
real hotkey is registered and no run loop is needed — except the explicit
real-registration smoke test at the bottom, which IS permission-free and
unregisters immediately.

Real end-to-end key-event delivery (posting a physical key event and watching
Carbon route it back) is not feasible headlessly: it requires posting real
key events, which needs Accessibility permission and would type into the test
runner. The functional tests instead drive the module's own event handler
with simulated pressed/released Carbon events.
"""

import threading
import time

import pytest

import flow.carbon_hotkey as ch
from flow.carbon_hotkey import CarbonHotkey


@pytest.fixture
def carbon(monkeypatch):
    """Mock the Carbon seams; return a recorder of register/unregister calls."""
    calls = {"registered": {}, "unregistered": [], "next_ref": 100, "fail": False}

    def fake_register(vk, mask, hkid):
        if calls["fail"]:
            return -9878, None  # eventHotKeyExistsErr
        calls["next_ref"] += 1
        ref = f"ref-{calls['next_ref']}"
        calls["registered"][hkid] = (vk, mask, ref)
        return 0, ref

    def fake_unregister(ref):
        calls["unregistered"].append(ref)
        return 0

    monkeypatch.setattr(ch, "_register", fake_register)
    monkeypatch.setattr(ch, "_unregister", fake_unregister)
    monkeypatch.setattr(ch, "_ensure_handler", lambda: None)
    return calls


# -- token -> Carbon translation ---------------------------------------------

def test_translates_char_key_and_modifiers(carbon):
    hk = CarbonHotkey(["cmd", "ctrl", "v"], on_trigger=lambda: None, name="t")
    hk.start()
    (vk, mask, _ref) = next(iter(carbon["registered"].values()))
    assert vk == 9  # kVK_ANSI_V
    assert mask == 0x100 | 0x1000  # cmdKey | controlKey


def test_translates_named_key_and_alt_shift(carbon):
    hk = CarbonHotkey(["alt", "shift", "f5"], on_trigger=lambda: None, name="t")
    hk.start()
    (vk, mask, _ref) = next(iter(carbon["registered"].values()))
    assert vk == 96  # kVK_F5
    assert mask == 0x800 | 0x200  # optionKey | shiftKey


def test_modifier_masks_match_the_vendored_carbon_constants():
    """The literal masks must equal Carbon.framework's own definitions, read
    through the vendored bridge (the issue's 'verify against quickmachotkey's
    own definitions')."""
    from flow._vendor.quickmachotkey import constants as c

    assert ch._CARBON_MODIFIER_MASKS == {
        "cmd": c.cmdKey,
        "shift": c.shiftKey,
        "alt": c.optionKey,
        "ctrl": c.controlKey,
    }


# -- constructor validation ----------------------------------------------------

def test_rejects_modifier_only_combo(carbon):
    with pytest.raises(ValueError):
        CarbonHotkey(["ctrl", "shift"], on_trigger=lambda: None, name="t")


def test_rejects_two_character_keys(carbon):
    with pytest.raises(ValueError):
        CarbonHotkey(["cmd", "a", "b"], on_trigger=lambda: None, name="t")


def test_rejects_bare_key_without_modifier(carbon):
    with pytest.raises(ValueError):
        CarbonHotkey(["v"], on_trigger=lambda: None, name="t")


def test_rejects_unknown_key_name(carbon):
    with pytest.raises(ValueError):
        CarbonHotkey(["cmd", "banana"], on_trigger=lambda: None, name="t")


def test_exposes_targets_and_name_like_hotkeylistener(carbon):
    hk = CarbonHotkey(["cmd", "ctrl", "v"], on_trigger=lambda: None, name="re-paste")
    assert hk._targets == frozenset({"cmd", "ctrl", "v"})
    assert hk._name == "re-paste"


# -- start / stop lifecycle ----------------------------------------------------

def test_start_is_idempotent(carbon):
    hk = CarbonHotkey(["cmd", "ctrl", "v"], on_trigger=lambda: None, name="t")
    hk.start()
    hk.start()
    assert len(carbon["registered"]) == 1


def test_start_failure_raises_runtimeerror_and_retry_works(carbon):
    hk = CarbonHotkey(["cmd", "ctrl", "v"], on_trigger=lambda: None, name="t")
    carbon["fail"] = True
    with pytest.raises(RuntimeError):
        hk.start()
    # Nothing registered, nothing leaked; a later retry (the #22 watchdog
    # contract) re-attempts and succeeds.
    assert carbon["registered"] == {}
    carbon["fail"] = False
    hk.start()
    assert len(carbon["registered"]) == 1


def test_stop_unregisters_and_is_idempotent(carbon):
    hk = CarbonHotkey(["cmd", "ctrl", "v"], on_trigger=lambda: None, name="t")
    hk.start()
    (_vk, _mask, ref) = next(iter(carbon["registered"].values()))
    hk.stop()
    hk.stop()  # second stop is a no-op
    assert carbon["unregistered"] == [ref]


def test_stop_without_start_is_safe(carbon):
    hk = CarbonHotkey(["cmd", "ctrl", "v"], on_trigger=lambda: None, name="t")
    hk.stop()
    assert carbon["unregistered"] == []


def test_stop_removes_the_hotkey_from_the_dispatch_registry(carbon):
    fired = []
    hk = CarbonHotkey(["cmd", "ctrl", "v"], on_trigger=lambda: fired.append(1), name="t")
    hk.start()
    hkid = next(iter(carbon["registered"]))
    hk.stop()
    ch._dispatch(hkid, ch.kEventHotKeyPressed)
    ch._dispatch(hkid, ch.kEventHotKeyReleased)
    assert fired == []


def test_restart_after_stop_registers_again(carbon):
    hk = CarbonHotkey(["cmd", "ctrl", "v"], on_trigger=lambda: None, name="t")
    hk.start()
    hk.stop()
    hk.start()
    assert len(carbon["registered"]) == 2  # two distinct registrations


# -- event dispatch (functional: simulated Carbon press/release) ---------------

def _ids(carbon):
    return list(carbon["registered"])


def test_tap_mode_fires_on_release_only(carbon):
    """on_trigger fires on RELEASED, never pressed: the synthesized Cmd+V must
    not race the user's still-held combo (mirrors the tap path's clean-release
    semantics)."""
    fired = []
    hk = CarbonHotkey(["cmd", "ctrl", "v"], on_trigger=lambda: fired.append(1), name="t")
    hk.start()
    hkid = _ids(carbon)[0]
    ch._dispatch(hkid, ch.kEventHotKeyPressed)
    assert fired == []
    ch._dispatch(hkid, ch.kEventHotKeyReleased)
    assert fired == [1]


def test_hold_mode_activates_on_press_deactivates_on_release(carbon):
    events = []
    hk = CarbonHotkey(
        ["cmd", "ctrl", "d"],
        on_activate=lambda: events.append("on"),
        on_deactivate=lambda: events.append("off"),
        name="dictation",
    )
    hk.start()
    hkid = _ids(carbon)[0]
    ch._dispatch(hkid, ch.kEventHotKeyPressed)
    assert events == ["on"]
    ch._dispatch(hkid, ch.kEventHotKeyReleased)
    assert events == ["on", "off"]


def test_release_without_press_does_not_fire(carbon):
    """A released event with no matching pressed (stale state after a missed
    press, e.g. registration mid-hold) must not fire anything."""
    fired = []
    hk = CarbonHotkey(["cmd", "ctrl", "v"], on_trigger=lambda: fired.append(1), name="t")
    hk.start()
    ch._dispatch(_ids(carbon)[0], ch.kEventHotKeyReleased)
    assert fired == []


def test_duplicate_pressed_events_fire_activate_once(carbon):
    events = []
    hk = CarbonHotkey(
        ["cmd", "ctrl", "d"],
        on_activate=lambda: events.append("on"),
        on_deactivate=lambda: events.append("off"),
        name="dictation",
    )
    hk.start()
    hkid = _ids(carbon)[0]
    ch._dispatch(hkid, ch.kEventHotKeyPressed)
    ch._dispatch(hkid, ch.kEventHotKeyPressed)
    assert events == ["on"]


def test_raising_callback_does_not_propagate(carbon):
    def boom():
        raise RuntimeError("boom")

    hk = CarbonHotkey(["cmd", "ctrl", "v"], on_trigger=boom, name="t")
    hk.start()
    hkid = _ids(carbon)[0]
    ch._dispatch(hkid, ch.kEventHotKeyPressed)
    ch._dispatch(hkid, ch.kEventHotKeyReleased)  # must not raise


def test_dispatch_for_unknown_id_is_ignored(carbon):
    ch._dispatch(999999, ch.kEventHotKeyPressed)  # must not raise


def test_handler_routes_through_the_real_event_accessors(carbon, monkeypatch):
    """Drive _handle_carbon_event (the body of the installed Carbon callback)
    with a simulated event: press then release must reach the callbacks via
    the signature check, id unpack, and kind switch."""
    fired = []
    hk = CarbonHotkey(["cmd", "ctrl", "v"], on_trigger=lambda: fired.append(1), name="t")
    hk.start()
    hkid = _ids(carbon)[0]

    kinds = {"kind": ch.kEventHotKeyPressed}
    monkeypatch.setattr(ch, "_event_hotkey_id", lambda event: (ch._SIGNATURE, hkid))
    monkeypatch.setattr(ch, "_event_kind", lambda event: kinds["kind"])

    assert ch._handle_carbon_event(object()) == 0
    assert fired == []
    kinds["kind"] = ch.kEventHotKeyReleased
    assert ch._handle_carbon_event(object()) == 0
    assert fired == [1]


def test_handler_passes_on_foreign_signature(carbon, monkeypatch):
    """Events carrying another handler's hotkey signature must be declined
    (eventNotHandledErr), never claimed."""
    fired = []
    hk = CarbonHotkey(["cmd", "ctrl", "v"], on_trigger=lambda: fired.append(1), name="t")
    hk.start()
    hkid = _ids(carbon)[0]
    monkeypatch.setattr(ch, "_event_hotkey_id", lambda event: (0xDEADBEEF, hkid))
    monkeypatch.setattr(ch, "_event_kind", lambda event: ch.kEventHotKeyReleased)

    assert ch._handle_carbon_event(object()) == ch._EVENT_NOT_HANDLED
    assert fired == []


# -- wait_all_released / reset ---------------------------------------------------

def test_wait_all_released_true_when_never_pressed(carbon, monkeypatch):
    monkeypatch.setattr(ch, "modifiers_physically_down", lambda: False)
    hk = CarbonHotkey(["cmd", "ctrl", "v"], on_trigger=lambda: None, name="t")
    hk.start()
    assert hk.wait_all_released(timeout=0.2) is True


def test_wait_all_released_blocks_until_release(carbon, monkeypatch):
    monkeypatch.setattr(ch, "modifiers_physically_down", lambda: False)
    hk = CarbonHotkey(["cmd", "ctrl", "v"], on_trigger=lambda: None, name="t")
    hk.start()
    hkid = _ids(carbon)[0]
    ch._dispatch(hkid, ch.kEventHotKeyPressed)

    result = {}

    def waiter():
        result["ok"] = hk.wait_all_released(timeout=2.0)

    t = threading.Thread(target=waiter)
    t.start()
    time.sleep(0.05)
    ch._dispatch(hkid, ch.kEventHotKeyReleased)
    t.join(timeout=2.0)
    assert result["ok"] is True


def test_wait_all_released_times_out_while_held(carbon, monkeypatch):
    monkeypatch.setattr(ch, "modifiers_physically_down", lambda: False)
    hk = CarbonHotkey(["cmd", "ctrl", "v"], on_trigger=lambda: None, name="t")
    hk.start()
    ch._dispatch(_ids(carbon)[0], ch.kEventHotKeyPressed)
    assert hk.wait_all_released(timeout=0.1) is False


def test_wait_all_released_waits_for_physical_modifiers(carbon, monkeypatch):
    """Carbon's released event fires when the chord breaks, but the user may
    still hold the modifiers — the paste guard (#24) needs the OS flags clear
    before True."""
    state = {"down": True}
    monkeypatch.setattr(ch, "modifiers_physically_down", lambda: state["down"])
    hk = CarbonHotkey(["cmd", "ctrl", "v"], on_trigger=lambda: None, name="t")
    hk.start()
    assert hk.wait_all_released(timeout=0.1) is False  # modifiers still down
    state["down"] = False
    assert hk.wait_all_released(timeout=0.1) is True


def test_wait_all_released_returns_false_if_the_os_check_raises(carbon, monkeypatch):
    def boom():
        raise RuntimeError("no CG")

    monkeypatch.setattr(ch, "modifiers_physically_down", boom)
    hk = CarbonHotkey(["cmd", "ctrl", "v"], on_trigger=lambda: None, name="t")
    hk.start()
    assert hk.wait_all_released(timeout=0.1) is False


def test_stop_during_held_hold_mode_fires_balancing_deactivate(carbon):
    """ADV-15 analog: stopping (suspend, set_hotkeys) while push-to-talk is
    held must fire one balancing on_deactivate, or the recording runs to
    max_seconds with no stop signal."""
    events = []
    hk = CarbonHotkey(
        ["cmd", "ctrl", "d"],
        on_activate=lambda: events.append("on"),
        on_deactivate=lambda: events.append("off"),
        name="dictation",
    )
    hk.start()
    ch._dispatch(_ids(carbon)[0], ch.kEventHotKeyPressed)
    hk.stop()
    assert events == ["on", "off"]


def test_stop_during_held_tap_mode_does_not_synthesize_trigger(carbon):
    fired = []
    hk = CarbonHotkey(["cmd", "ctrl", "v"], on_trigger=lambda: fired.append(1), name="t")
    hk.start()
    ch._dispatch(_ids(carbon)[0], ch.kEventHotKeyPressed)
    hk.stop()
    assert fired == []  # a synthesized trigger would paste into a fresh window


def test_stop_unblocks_a_waiting_wait_all_released(carbon, monkeypatch):
    monkeypatch.setattr(ch, "modifiers_physically_down", lambda: False)
    hk = CarbonHotkey(["cmd", "ctrl", "v"], on_trigger=lambda: None, name="t")
    hk.start()
    ch._dispatch(_ids(carbon)[0], ch.kEventHotKeyPressed)

    result = {}

    def waiter():
        result["ok"] = hk.wait_all_released(timeout=2.0)

    t = threading.Thread(target=waiter)
    t.start()
    time.sleep(0.05)
    hk.stop()
    t.join(timeout=2.0)
    assert result["ok"] is True


# -- backend classification (shared with the factory & settings window) --------

def test_is_carbon_combo():
    assert ch.is_carbon_combo(["cmd", "ctrl", "v"]) is True
    assert ch.is_carbon_combo(["alt", "shift", "f5"]) is True
    assert ch.is_carbon_combo(["ctrl", "shift"]) is False  # modifier-only
    assert ch.is_carbon_combo(["cmd", "a", "b"]) is False  # two char keys
    assert ch.is_carbon_combo(["v"]) is False  # no modifier
    assert ch.is_carbon_combo([]) is False


def test_is_carbon_combo_raises_for_unknown_names():
    with pytest.raises(ValueError):
        ch.is_carbon_combo(["cmd", "banana"])


# -- the real thing: registration smoke test (permission-free) ------------------

def test_real_carbon_registration_smoke():
    """Register a real Carbon hotkey and unregister immediately. No TCC
    permission is needed and no event is simulated — this proves the vendored
    bridge and the OSStatus plumbing on the actual OS. Skipped if the Carbon
    bridge is unavailable."""
    try:
        from flow._vendor.quickmachotkey import _MinimalHIToolbox as hi

        hi.GetEventDispatcherTarget()
    except Exception:
        pytest.skip("Carbon HIToolbox bridge unavailable on this machine")
    # An implausible chord (all four modifiers + F19) so a real user shortcut
    # cannot collide during the milliseconds this is registered.
    hk = CarbonHotkey(
        ["cmd", "alt", "ctrl", "shift", "f19"], on_trigger=lambda: None, name="smoke"
    )
    hk.start()
    try:
        assert hk._ref is not None
    finally:
        hk.stop()
    assert hk._ref is None
