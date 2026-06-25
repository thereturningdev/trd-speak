"""Adversarial battery #2 against HotkeyListener modifier/firing logic.

Target: _reconcile_modifiers, _tap_callback, _press, _release, _char_key_down,
_rearm_on_modifier_release and the tap/hold/keydown-fire firing decisions after
the change to track modifiers purely from the absolute CGEventGetFlags() bitmask.

Every test asserts INTENDED behavior (per the module docstrings), not whatever
the code currently happens to do. Passing tests form a regression suite; failing
ones expose real defects. No production code is modified here.

Driver pattern reused from tests/test_hotkey.py and tests/test_repaste_adversarial.py:
monkeypatch hk.Quartz.CGEventGetIntegerValueField (keycode) and
hk.Quartz.CGEventGetFlags (absolute flags), then feed real flagsChanged/keyDown/
keyUp events through the real _tap_callback entry point.
"""

import threading

import pytest

import flow.hotkey as hk
from flow.hotkey import HotkeyListener

# Virtual keycodes (left/right variants).
_CMD_L, _CMD_R = 55, 54
_CTRL_L, _CTRL_R = 59, 62
_SHIFT_L, _SHIFT_R = 56, 60
_ALT_L, _ALT_R = 58, 61
_P = 35
_V = 9
_FOUR = 21

_CMD = hk.Quartz.kCGEventFlagMaskCommand
_CTRL = hk.Quartz.kCGEventFlagMaskControl
_SHIFT = hk.Quartz.kCGEventFlagMaskShift
_ALT = hk.Quartz.kCGEventFlagMaskAlternate


class _Driver:
    def __init__(self, listener, monkeypatch):
        self._l = listener
        self._kc = 0
        self._flags = 0
        monkeypatch.setattr(
            hk.Quartz, "CGEventGetIntegerValueField", lambda e, f: self._kc
        )
        monkeypatch.setattr(hk.Quartz, "CGEventGetFlags", lambda e: self._flags)

    def modifier(self, keycode, flags):
        self._kc, self._flags = keycode, flags
        self._l._tap_callback(None, hk.Quartz.kCGEventFlagsChanged, object(), None)

    def key_down(self, keycode, flags=0):
        self._kc, self._flags = keycode, flags
        self._l._tap_callback(None, hk.Quartz.kCGEventKeyDown, object(), None)

    def key_up(self, keycode, flags=0):
        self._kc, self._flags = keycode, flags
        self._l._tap_callback(None, hk.Quartz.kCGEventKeyUp, object(), None)


def _tap(keys):
    fired = []
    l = HotkeyListener(keys=keys, on_trigger=lambda: fired.append(1))
    return l, fired


def _hold(keys):
    ev = []
    l = HotkeyListener(
        keys=keys,
        on_activate=lambda: ev.append("on"),
        on_deactivate=lambda: ev.append("off"),
    )
    return l, ev


# ===========================================================================
# CATEGORY: press/release ordering (modifier-only tap)
# ===========================================================================

def test_modonly_reverse_press_order_fires(monkeypatch):
    """ctrl pressed first then cmd (opposite of the 'natural' order) still fires
    once on clean release."""
    l, fired = _tap(["cmd", "ctrl"])
    d = _Driver(l, monkeypatch)
    d.modifier(_CTRL_L, _CTRL)
    d.modifier(_CMD_L, _CTRL | _CMD)
    d.modifier(_CTRL_L, _CMD)
    d.modifier(_CMD_L, 0)
    assert fired == [1]


def test_modonly_release_order_other_first_fires_once(monkeypatch):
    """Release the FIRST-pressed modifier first (cmd up before ctrl). Combo
    breaks on the first release and fires exactly once."""
    l, fired = _tap(["cmd", "ctrl"])
    d = _Driver(l, monkeypatch)
    d.modifier(_CMD_L, _CMD)
    d.modifier(_CTRL_L, _CMD | _CTRL)
    d.modifier(_CMD_L, _CTRL)   # cmd up first -> fires
    d.modifier(_CTRL_L, 0)      # ctrl up -> must NOT re-fire
    assert fired == [1]


# ===========================================================================
# CATEGORY: rapid re-arm (two taps in a row)
# ===========================================================================

def test_modonly_two_clean_taps_fire_twice(monkeypatch):
    l, fired = _tap(["cmd", "ctrl"])
    d = _Driver(l, monkeypatch)
    for _ in range(2):
        d.modifier(_CMD_L, _CMD)
        d.modifier(_CTRL_L, _CMD | _CTRL)
        d.modifier(_CMD_L, _CTRL)
        d.modifier(_CTRL_L, 0)
    assert fired == [1, 1]


def test_modonly_two_coalesced_taps_fire_twice(monkeypatch):
    """Two taps where BOTH bits arrive in one event and BOTH clear in one event."""
    l, fired = _tap(["cmd", "ctrl"])
    d = _Driver(l, monkeypatch)
    d.modifier(_CMD_L, _CMD | _CTRL)   # both in
    d.modifier(_CMD_L, 0)              # both out
    d.modifier(_CTRL_L, _CMD | _CTRL)  # both in
    d.modifier(_CTRL_L, 0)             # both out
    assert fired == [1, 1]


# ===========================================================================
# CATEGORY: left/right variants
# ===========================================================================

def test_modonly_mixed_left_right_variants_fire(monkeypatch):
    """left cmd + right ctrl fires; flags don't distinguish variant."""
    l, fired = _tap(["cmd", "ctrl"])
    d = _Driver(l, monkeypatch)
    d.modifier(_CMD_L, _CMD)
    d.modifier(_CTRL_R, _CMD | _CTRL)
    d.modifier(_CMD_L, _CTRL)
    d.modifier(_CTRL_R, 0)
    assert fired == [1]


def test_modonly_swap_variant_same_token_no_double_press(monkeypatch):
    """Press left ctrl, then a flagsChanged for right ctrl arrives but the ctrl
    bit was already set: reconcile sees ctrl already present so it does NOT
    re-press. The token is a single held entry; releasing the bit drops it
    entirely. wait_all_released must be clean after release."""
    l, fired = _tap(["ctrl", "shift"])
    d = _Driver(l, monkeypatch)
    d.modifier(_CTRL_L, _CTRL)
    d.modifier(_CTRL_R, _CTRL)         # same bit still set
    # held['ctrl'] should be a single canonical entry, not two.
    assert l._held.get("ctrl") is not None and len(l._held["ctrl"]) == 1
    d.modifier(_SHIFT_L, _CTRL | _SHIFT)
    d.modifier(_SHIFT_L, _CTRL)        # shift up -> fires
    d.modifier(_CTRL_L, 0)             # ctrl bit clears -> token drops
    assert fired == [1]
    assert l._held == {}
    assert l.wait_all_released(timeout=0.2) is True


# ===========================================================================
# CATEGORY: dropped / coalesced flagsChanged (modifier-only)
# ===========================================================================

def test_modonly_dropped_release_self_heals(monkeypatch):
    """A release flagsChanged is dropped: the combo is held, then the NEXT event
    shows flags already cleared. Reconcile must release the stuck modifiers,
    fire once, and leave nothing held."""
    l, fired = _tap(["cmd", "ctrl"])
    d = _Driver(l, monkeypatch)
    d.modifier(_CMD_L, _CMD)
    d.modifier(_CTRL_L, _CMD | _CTRL)   # held
    # the cmd-up and ctrl-up events both dropped; next event flags==0.
    d.modifier(0, 0)
    assert fired == [1]
    assert l._held == {}


def test_modonly_partial_drop_one_mod_stays(monkeypatch):
    """cmd-up dropped, only ctrl-up delivered later. When the cmd-up's effect
    finally shows (flags drop cmd while ctrl still held) reconcile releases cmd,
    breaks combo, fires once."""
    l, fired = _tap(["cmd", "ctrl"])
    d = _Driver(l, monkeypatch)
    d.modifier(_CMD_L, _CMD)
    d.modifier(_CTRL_L, _CMD | _CTRL)
    # cmd released, ctrl still down (its own keycode carried but cmd bit gone).
    d.modifier(_CTRL_L, _CTRL)
    assert fired == [1]
    d.modifier(_CTRL_L, 0)
    assert fired == [1]
    assert l._held == {}


# ===========================================================================
# CATEGORY: contamination edge cases (modifier-only tap)
# ===========================================================================

def test_modonly_char_keydown_before_full_combo_not_contaminating(monkeypatch):
    """A stray key pressed BEFORE the combo is fully held (not _active yet) must
    not poison the subsequent clean hold: contamination only counts while the
    full combo is active."""
    l, fired = _tap(["cmd", "ctrl"])
    d = _Driver(l, monkeypatch)
    d.modifier(_CMD_L, _CMD)
    d.key_down(_FOUR, flags=_CMD)     # only cmd held, combo not active yet
    d.key_up(_FOUR, flags=_CMD)
    d.modifier(_CTRL_L, _CMD | _CTRL) # now combo held cleanly
    d.modifier(_CMD_L, _CTRL)
    d.modifier(_CTRL_L, 0)
    assert fired == [1]


def test_modonly_char_keyup_only_during_hold_does_not_contaminate(monkeypatch):
    """Only a stray keyUP (no keyDown) seen during the hold. Contamination is
    keyDOWN-driven, so a lone keyUp must not cancel; the clean hold still fires."""
    l, fired = _tap(["cmd", "ctrl"])
    d = _Driver(l, monkeypatch)
    d.modifier(_CMD_L, _CMD)
    d.modifier(_CTRL_L, _CMD | _CTRL)
    d.key_up(_FOUR, flags=_CMD | _CTRL)   # stray keyUp only
    d.modifier(_CMD_L, _CTRL)
    d.modifier(_CTRL_L, 0)
    assert fired == [1]


def test_modonly_contaminating_key_after_one_mod_released_still_cancels_nothing(monkeypatch):
    """Contaminating key pressed AFTER the combo already broke (one mod released)
    must not matter; the trigger already fired."""
    l, fired = _tap(["cmd", "ctrl"])
    d = _Driver(l, monkeypatch)
    d.modifier(_CMD_L, _CMD)
    d.modifier(_CTRL_L, _CMD | _CTRL)
    d.modifier(_CMD_L, _CTRL)     # combo breaks -> fires
    assert fired == [1]
    d.key_down(_FOUR, flags=_CTRL)
    d.modifier(_CTRL_L, 0)
    assert fired == [1]


# ===========================================================================
# CATEGORY: hold mode under coalesced / dropped events
# ===========================================================================

def test_hold_coalesced_press_and_release(monkeypatch):
    l, ev = _hold(["ctrl", "shift"])
    d = _Driver(l, monkeypatch)
    d.modifier(_CTRL_L, _CTRL | _SHIFT)   # both in one event -> activate
    assert ev == ["on"]
    d.modifier(_CTRL_L, 0)                # both out -> deactivate
    assert ev == ["on", "off"]


def test_hold_dropped_press_self_heals(monkeypatch):
    """ctrl-down event dropped; shift-down event shows both bits. Hold must
    activate."""
    l, ev = _hold(["ctrl", "shift"])
    d = _Driver(l, monkeypatch)
    d.modifier(_SHIFT_L, _CTRL | _SHIFT)  # both bits, only shift carried
    assert ev == ["on"]
    d.modifier(_SHIFT_L, 0)
    assert ev == ["on", "off"]


def test_hold_partial_release_deactivates_once(monkeypatch):
    """Releasing just one of the held modifiers deactivates exactly once even if
    the second release follows."""
    l, ev = _hold(["ctrl", "shift"])
    d = _Driver(l, monkeypatch)
    d.modifier(_CTRL_L, _CTRL)
    d.modifier(_SHIFT_L, _CTRL | _SHIFT)
    d.modifier(_SHIFT_L, _CTRL)   # one up -> deactivate
    d.modifier(_CTRL_L, 0)        # other up -> no second deactivate
    assert ev == ["on", "off"]


def test_hold_simultaneous_release_no_stuck_key(monkeypatch):
    """Both modifiers clear in one event: deactivate once and nothing left in
    _held (so wait_all_released is clean)."""
    l, ev = _hold(["ctrl", "shift"])
    d = _Driver(l, monkeypatch)
    d.modifier(_CTRL_L, _CTRL)
    d.modifier(_SHIFT_L, _CTRL | _SHIFT)
    d.modifier(_SHIFT_L, 0)       # both clear at once
    assert ev == ["on", "off"]
    assert l._held == {}
    assert l.wait_all_released(timeout=0.2) is True


def test_hold_no_phantom_activate_on_idempotent_event(monkeypatch):
    """A duplicate flagsChanged with the SAME full-combo flags must not
    re-activate (on must fire exactly once)."""
    l, ev = _hold(["ctrl", "shift"])
    d = _Driver(l, monkeypatch)
    d.modifier(_CTRL_L, _CTRL | _SHIFT)
    d.modifier(_SHIFT_L, _CTRL | _SHIFT)  # same absolute flags again
    d.modifier(_CTRL_R, _CTRL | _SHIFT)   # and again
    assert ev == ["on"]


# ===========================================================================
# CATEGORY: char chord re-arm / phantom (extra permutations)
# ===========================================================================

def test_charchord_partial_modifier_drop_does_not_rearm_prematurely(monkeypatch):
    """If ONE required modifier drops while the other stays, the chord must NOT
    re-arm (it's only re-armed when the FULL required set is no longer held).
    Then a second P keyDown without re-pressing must not fire again."""
    l, fired = _tap(["cmd", "ctrl", "p"])
    d = _Driver(l, monkeypatch)
    d.modifier(_CMD_L, _CMD)
    d.modifier(_CTRL_L, _CMD | _CTRL)
    d.key_down(_P, flags=_CMD | _CTRL)     # fires
    assert fired == [1]
    d.modifier(_CMD_L, _CTRL)              # cmd drops, ctrl still held -> NOT re-armed
    d.key_down(_P, flags=_CTRL)            # cmd missing anyway, must not fire
    assert fired == [1]


def test_charchord_rearm_on_any_required_modifier_drop(monkeypatch):
    """Per the documented re-arm rule ('once the required modifiers are no longer
    ALL held'), dropping cmd (even while ctrl stays) re-arms. Re-pressing cmd and
    pressing P again is a fresh required-mods-complete event, so it fires again."""
    l, fired = _tap(["cmd", "ctrl", "p"])
    d = _Driver(l, monkeypatch)
    d.modifier(_CMD_L, _CMD)
    d.modifier(_CTRL_L, _CMD | _CTRL)
    d.key_down(_P, flags=_CMD | _CTRL)     # fires
    d.modifier(_CMD_L, _CTRL)              # cmd up -> required set no longer all held -> re-arm
    d.modifier(_CMD_L, _CMD | _CTRL)       # cmd back down
    d.key_down(_P, flags=_CMD | _CTRL)     # P again with full mods -> fires again
    assert fired == [1, 1]


def test_charchord_keycode_zero_modifier_event_still_rearms(monkeypatch):
    """A keycode-0 flagsChanged clearing the modifiers must re-arm a char chord."""
    l, fired = _tap(["cmd", "ctrl", "p"])
    d = _Driver(l, monkeypatch)
    d.modifier(_CMD_L, _CMD)
    d.modifier(_CTRL_L, _CMD | _CTRL)
    d.key_down(_P, flags=_CMD | _CTRL)
    assert fired == [1]
    d.modifier(0, 0)                       # keycode 0, flags cleared -> re-arm
    d.modifier(_CMD_L, _CMD)
    d.modifier(_CTRL_L, _CMD | _CTRL)
    d.key_down(_P, flags=_CMD | _CTRL)
    assert fired == [1, 1]


def test_charchord_right_variant_rearm_via_left_variant(monkeypatch):
    """Press right variants, fire, release via flags; re-press left variants and
    fire again. Variant identity must not break re-arm."""
    l, fired = _tap(["cmd", "ctrl", "p"])
    d = _Driver(l, monkeypatch)
    d.modifier(_CMD_R, _CMD)
    d.modifier(_CTRL_R, _CMD | _CTRL)
    d.key_down(_P, flags=_CMD | _CTRL)
    assert fired == [1]
    d.modifier(_CMD_R, _CTRL)
    d.modifier(_CTRL_R, 0)
    d.modifier(_CMD_L, _CMD)
    d.modifier(_CTRL_L, _CMD | _CTRL)
    d.key_down(_P, flags=_CMD | _CTRL)
    assert fired == [1, 1]


# ===========================================================================
# CATEGORY: reconfigured / replaced shortcut (reported bug #2)
# ===========================================================================

def test_reconfigured_modonly_to_charchord_old_state_clear(monkeypatch):
    """A fresh listener for the NEW combo must fire for the new combo and never
    carry stale state. Simulate 'replaced shortcut' by building a new listener
    with new keys and driving the new combo."""
    l, fired = _tap(["cmd", "alt"])   # new modifier-only combo cmd+alt
    d = _Driver(l, monkeypatch)
    d.modifier(_CMD_L, _CMD)
    d.modifier(_ALT_L, _CMD | _ALT)
    d.modifier(_CMD_L, _ALT)
    d.modifier(_ALT_L, 0)
    assert fired == [1]


def test_reconfigured_old_combo_does_not_fire_new_listener(monkeypatch):
    """A listener configured for cmd+alt must NOT fire when the OLD cmd+ctrl
    combo is pressed (the replaced shortcut must not respond to the old keys)."""
    l, fired = _tap(["cmd", "alt"])
    d = _Driver(l, monkeypatch)
    d.modifier(_CMD_L, _CMD)
    d.modifier(_CTRL_L, _CMD | _CTRL)   # old combo, not the configured one
    d.modifier(_CMD_L, _CTRL)
    d.modifier(_CTRL_L, 0)
    assert fired == []


def test_reconfigured_charchord_new_char_only(monkeypatch):
    """Replacing cmd+ctrl+p with cmd+ctrl+v: pressing P must NOT fire, V must."""
    l, fired = _tap(["cmd", "ctrl", "v"])
    d = _Driver(l, monkeypatch)
    d.modifier(_CMD_L, _CMD)
    d.modifier(_CTRL_L, _CMD | _CTRL)
    d.key_down(_P, flags=_CMD | _CTRL)   # old char, must not fire
    assert fired == []
    d.key_down(_V, flags=_CMD | _CTRL)   # new char, must fire
    assert fired == [1]


# ===========================================================================
# CATEGORY: cmd+alt correction combo (reported bug #1, second combo)
# ===========================================================================

def test_cmd_alt_modonly_clean_tap_fires(monkeypatch):
    """The correction shortcut cmd+alt (modifier-only tap) must fire on a clean
    hold-and-release."""
    l, fired = _tap(["cmd", "alt"])
    d = _Driver(l, monkeypatch)
    d.modifier(_CMD_L, _CMD)
    d.modifier(_ALT_L, _CMD | _ALT)
    d.modifier(_ALT_L, _CMD)
    d.modifier(_CMD_L, 0)
    assert fired == [1]


def test_cmd_alt_right_option_variant_fires(monkeypatch):
    """cmd+alt with the RIGHT option key (keycode 61) must still fire."""
    l, fired = _tap(["cmd", "alt"])
    d = _Driver(l, monkeypatch)
    d.modifier(_CMD_L, _CMD)
    d.modifier(_ALT_R, _CMD | _ALT)
    d.modifier(_ALT_R, _CMD)
    d.modifier(_CMD_L, 0)
    assert fired == [1]


def test_cmd_alt_coalesced_release_fires_once(monkeypatch):
    l, fired = _tap(["cmd", "alt"])
    d = _Driver(l, monkeypatch)
    d.modifier(_CMD_L, _CMD)
    d.modifier(_ALT_L, _CMD | _ALT)
    d.modifier(_ALT_L, 0)         # both clear at once
    assert fired == [1]
    assert l._held == {}


# ===========================================================================
# CATEGORY: no-stuck-state invariants
# ===========================================================================

def test_no_stuck_state_after_contaminated_hold(monkeypatch):
    """After a contaminated hold and full release, _held empty and waiter clean."""
    l, fired = _tap(["cmd", "ctrl"])
    d = _Driver(l, monkeypatch)
    d.modifier(_CMD_L, _CMD)
    d.modifier(_CTRL_L, _CMD | _CTRL)
    d.key_down(_FOUR, flags=_CMD | _CTRL)
    d.modifier(_CMD_L, _CTRL)
    d.modifier(_CTRL_L, 0)
    assert fired == []
    assert l._held == {}
    assert l.wait_all_released(timeout=0.2) is True


def test_no_stuck_state_char_chord_after_full_release(monkeypatch):
    l, fired = _tap(["cmd", "ctrl", "p"])
    d = _Driver(l, monkeypatch)
    d.modifier(_CMD_L, _CMD)
    d.modifier(_CTRL_L, _CMD | _CTRL)
    d.key_down(_P, flags=_CMD | _CTRL)
    d.modifier(_CMD_L, _CTRL)
    d.modifier(_CTRL_L, 0)
    assert l._held == {}
    assert l.wait_all_released(timeout=0.2) is True


def test_no_stuck_state_after_three_mod_combo_simultaneous_release(monkeypatch):
    """Three-modifier combo (cmd+ctrl+shift would be 3 mods) released all at once
    leaves nothing held. Use cmd+ctrl+shift as a 3-key modifier-only combo."""
    l, fired = _tap(["cmd", "ctrl", "shift"])
    d = _Driver(l, monkeypatch)
    d.modifier(_CMD_L, _CMD)
    d.modifier(_CTRL_L, _CMD | _CTRL)
    d.modifier(_SHIFT_L, _CMD | _CTRL | _SHIFT)   # full combo held
    d.modifier(_SHIFT_L, 0)                        # all three clear in one event
    assert fired == [1]
    assert l._held == {}
    assert l.wait_all_released(timeout=0.2) is True


# ===========================================================================
# CATEGORY: three-modifier combo ordering
# ===========================================================================

def test_three_mod_combo_fires_only_when_all_three_held(monkeypatch):
    l, fired = _tap(["cmd", "ctrl", "shift"])
    d = _Driver(l, monkeypatch)
    d.modifier(_CMD_L, _CMD)
    d.modifier(_CTRL_L, _CMD | _CTRL)
    # release before shift ever pressed -> partial, must not fire.
    d.modifier(_CTRL_L, _CMD)
    d.modifier(_CMD_L, 0)
    assert fired == []


def test_three_mod_combo_partial_release_fires_once(monkeypatch):
    l, fired = _tap(["cmd", "ctrl", "shift"])
    d = _Driver(l, monkeypatch)
    d.modifier(_CMD_L, _CMD)
    d.modifier(_CTRL_L, _CMD | _CTRL)
    d.modifier(_SHIFT_L, _CMD | _CTRL | _SHIFT)   # held
    d.modifier(_SHIFT_L, _CMD | _CTRL)            # shift up -> fires once
    d.modifier(_CTRL_L, _CMD)                     # must not re-fire
    d.modifier(_CMD_L, 0)
    assert fired == [1]


# ===========================================================================
# CATEGORY: idempotent flagsChanged (modifier-only tap)
# ===========================================================================

def test_modonly_duplicate_full_combo_event_does_not_double_arm(monkeypatch):
    """Duplicate flagsChanged carrying the same full-combo flags must not cause
    two triggers on a single release."""
    l, fired = _tap(["cmd", "ctrl"])
    d = _Driver(l, monkeypatch)
    d.modifier(_CMD_L, _CMD)
    d.modifier(_CTRL_L, _CMD | _CTRL)
    d.modifier(_CTRL_L, _CMD | _CTRL)   # duplicate, same flags
    d.modifier(_CMD_L, _CTRL)
    d.modifier(_CTRL_L, 0)
    assert fired == [1]


# ===========================================================================
# CATEGORY: char chord — char before mods / contamination ordering
# ===========================================================================

def test_charchord_char_held_through_modifier_arrival_does_not_fire_on_mods(monkeypatch):
    """P down with no mods (no fire). Then mods arrive via flagsChanged while P
    is conceptually still down. The chord must NOT fire on the bare modifier
    arrival — it only fires on a char keyDown carrying the full mod flags."""
    l, fired = _tap(["cmd", "ctrl", "p"])
    d = _Driver(l, monkeypatch)
    d.key_down(_P, flags=0)              # P alone, no fire
    assert fired == []
    d.modifier(_CMD_L, _CMD)
    d.modifier(_CTRL_L, _CMD | _CTRL)    # mods now held; must not phantom-fire
    assert fired == []


def test_charchord_wrong_then_right_char(monkeypatch):
    """Wrong char (V) then right char (P) with mods held: only P fires."""
    l, fired = _tap(["cmd", "ctrl", "p"])
    d = _Driver(l, monkeypatch)
    d.modifier(_CMD_L, _CMD)
    d.modifier(_CTRL_L, _CMD | _CTRL)
    d.key_down(_V, flags=_CMD | _CTRL)
    assert fired == []
    d.key_down(_P, flags=_CMD | _CTRL)
    assert fired == [1]


# ===========================================================================
# CATEGORY: exception safety / callback robustness
# ===========================================================================

def test_modonly_simultaneous_swap_one_mod_for_another(monkeypatch):
    """One event drops cmd and adds shift simultaneously for a cmd+ctrl combo:
    flags go from cmd|ctrl to ctrl|shift. cmd must be released (combo breaks ->
    fires once), shift is irrelevant. Reconcile must handle a stale-release AND a
    fresh-press of a non-target in the same event without firing twice or
    stalling."""
    l, fired = _tap(["cmd", "ctrl"])
    d = _Driver(l, monkeypatch)
    d.modifier(_CMD_L, _CMD)
    d.modifier(_CTRL_L, _CMD | _CTRL)          # combo held
    d.modifier(_SHIFT_L, _CTRL | _SHIFT)       # cmd gone, shift added, ctrl stays
    assert fired == [1]
    d.modifier(_CTRL_L, _SHIFT)
    d.modifier(_SHIFT_L, 0)
    assert fired == [1]
    assert l._held == {}


def test_hold_mode_char_chord_activates_and_deactivates(monkeypatch):
    """Hold mode with a CHAR key in the combo (cmd+p, push-to-talk-style): the
    char key is routed through _press, so on_activate fires only when BOTH cmd
    and p are physically held, and on_deactivate when either leaves."""
    ev = []
    l = HotkeyListener(
        keys=["cmd", "p"],
        on_activate=lambda: ev.append("on"),
        on_deactivate=lambda: ev.append("off"),
    )
    d = _Driver(l, monkeypatch)
    d.modifier(_CMD_L, _CMD)         # only cmd -> not yet active
    assert ev == []
    d.key_down(_P, flags=_CMD)       # cmd + p both held -> activate
    assert ev == ["on"]
    d.key_up(_P, flags=_CMD)         # p up -> deactivate
    assert ev == ["on", "off"]


def test_hold_mode_char_chord_modifier_leaves_first_deactivates(monkeypatch):
    """Hold mode cmd+p where the MODIFIER leaves first (cmd up while p still
    down) must deactivate."""
    ev = []
    l = HotkeyListener(
        keys=["cmd", "p"],
        on_activate=lambda: ev.append("on"),
        on_deactivate=lambda: ev.append("off"),
    )
    d = _Driver(l, monkeypatch)
    d.modifier(_CMD_L, _CMD)
    d.key_down(_P, flags=_CMD)       # active
    assert ev == ["on"]
    d.modifier(_CMD_L, 0)            # cmd up -> deactivate
    assert ev == ["on", "off"]


def test_modonly_contamination_resets_on_new_full_hold(monkeypatch):
    """A re-entry into the full hold (a modifier re-pressed after a partial
    release, going through _press's full-combo branch again) must reset the
    contamination flag so a clean second hold fires."""
    l, fired = _tap(["cmd", "ctrl"])
    d = _Driver(l, monkeypatch)
    # First hold: contaminate it.
    d.modifier(_CMD_L, _CMD)
    d.modifier(_CTRL_L, _CMD | _CTRL)
    d.key_down(_FOUR, flags=_CMD | _CTRL)   # contaminate
    d.modifier(_CTRL_L, _CMD)               # ctrl up -> fires? no, contaminated
    assert fired == []
    # ctrl back down -> re-enters full-combo branch, must reset contamination.
    d.modifier(_CTRL_L, _CMD | _CTRL)
    d.modifier(_CTRL_L, _CMD)               # clean release this time
    d.modifier(_CMD_L, 0)
    assert fired == [1]


def test_alt_only_named_key_chord_alt_space(monkeypatch):
    """alt+space char chord fires on space keyDown with alt held and re-arms."""
    l, fired = _tap(["alt", "space"])
    d = _Driver(l, monkeypatch)
    d.modifier(_ALT_L, _ALT)
    d.key_down(49, flags=_ALT)       # space keycode 49
    assert fired == [1]
    d.modifier(_ALT_L, 0)            # re-arm
    d.modifier(_ALT_L, _ALT)
    d.key_down(49, flags=_ALT)
    assert fired == [1, 1]


def test_reconcile_ignores_nontarget_modifiers_in_flags(monkeypatch):
    """For a cmd+ctrl combo, a flagsChanged whose flags also carry shift/alt
    bits must NOT add shift/alt to _held (only target modifiers are tracked).
    The combo still fires on clean release."""
    l, fired = _tap(["cmd", "ctrl"])
    d = _Driver(l, monkeypatch)
    d.modifier(_CMD_L, _CMD | _SHIFT | _ALT)        # cmd + noise
    assert set(l._held) <= {"cmd"}
    d.modifier(_CTRL_L, _CMD | _CTRL | _SHIFT | _ALT)
    assert set(l._held) == {"cmd", "ctrl"}          # noise not tracked
    d.modifier(_CMD_L, _CTRL | _SHIFT | _ALT)        # cmd up -> fires
    d.modifier(_CTRL_L, _SHIFT | _ALT)
    assert fired == [1]
    # remaining noise bits don't keep the combo "held".
    assert "shift" not in l._held and "alt" not in l._held


def test_modonly_flags_unchanged_event_is_idempotent(monkeypatch):
    """Repeated identical flagsChanged events (same flags, possibly different
    carried keycode) must not press/release anything or change firing."""
    l, fired = _tap(["cmd", "ctrl"])
    d = _Driver(l, monkeypatch)
    d.modifier(_CMD_L, _CMD | _CTRL)
    snapshot = {k: set(v) for k, v in l._held.items()}
    d.modifier(_CTRL_L, _CMD | _CTRL)   # identical absolute flags
    d.modifier(0, _CMD | _CTRL)         # identical again, keycode 0
    assert {k: set(v) for k, v in l._held.items()} == snapshot
    d.modifier(_CMD_L, 0)
    assert fired == [1]


def test_charchord_modifier_noise_does_not_block_fire(monkeypatch):
    """cmd+ctrl+p where the P keyDown's flags also include shift noise: the gate
    is subset, so the extra shift must not block firing."""
    l, fired = _tap(["cmd", "ctrl", "p"])
    d = _Driver(l, monkeypatch)
    d.modifier(_CMD_L, _CMD)
    d.modifier(_CTRL_L, _CMD | _CTRL | _SHIFT)
    d.key_down(_P, flags=_CMD | _CTRL | _SHIFT)
    assert fired == [1]


def test_callback_swallows_trigger_exception_and_keeps_state(monkeypatch):
    """If on_trigger raises, the tap callback must not propagate it (it would
    kill the tap). State should still self-heal on the next clean tap."""
    calls = {"n": 0}

    def boom():
        calls["n"] += 1
        raise RuntimeError("boom")

    l = HotkeyListener(keys=["cmd", "ctrl"], on_trigger=boom)
    d = _Driver(l, monkeypatch)
    d.modifier(_CMD_L, _CMD)
    d.modifier(_CTRL_L, _CMD | _CTRL)
    d.modifier(_CMD_L, _CTRL)            # fires -> raises inside, must be swallowed
    d.modifier(_CTRL_L, 0)
    assert calls["n"] == 1
    # State must be clean for the next tap.
    assert l._held == {}
    d.modifier(_CMD_L, _CMD)
    d.modifier(_CTRL_L, _CMD | _CTRL)
    d.modifier(_CMD_L, _CTRL)
    d.modifier(_CTRL_L, 0)
    assert calls["n"] == 2
