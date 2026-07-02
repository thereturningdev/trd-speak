"""Adversarial test battery for the "paste last dictation" (re-paste) feature.

Goal: break the re-paste path at both layers, asserting the INTENDED behavior
documented in the docstrings of flow/hotkey.py and flow/app.py — not merely what
the current code happens to do.

Two layers under attack:

  * Unit  -- flow.hotkey.HotkeyListener in TAP mode (on_trigger=), with special
            focus on the keyDown-fire path for chords that contain a non-modifier
            character key (e.g. ["cmd","ctrl","p"]): _char_key_down,
            _char_key_up, _rearm_on_modifier_release, _tap_callback dispatch,
            plus the modifier-only tap path (_press / _release / _contaminated).

  * Functional -- the end-to-end chain in flow.app.App:
            _on_repaste -> _do_repaste -> flow.paster.paste_text, including the
            IDLE-state guard, wait_all_released, empty-history handling and the
            can_paste gate.

Every test is kept (passing ones are a regression suite). No production code is
modified by this file.
"""

import queue
import threading
import time

import pytest

import flow.app as app_mod
import flow.carbon_hotkey as carbon_mod
import flow.hotkey as hk
from flow.app import App, IDLE, PROCESSING
from flow.config import Config
from flow.hotkey import HotkeyListener


@pytest.fixture(autouse=True)
def _isolate_history(tmp_path, monkeypatch):
    """Point the per-build dictations file at a throwaway temp file so tests
    never read or write the user's real ~/Library/Application Support store."""
    monkeypatch.setattr(app_mod.paths, "DICTATIONS_PATH", tmp_path / "dictations.json")

# --- virtual keycodes (per the harness contract in the task brief) ---------
_CMD_L, _CMD_R = 55, 54
_CTRL_L, _CTRL_R = 59, 62
_SHIFT_L, _SHIFT_R = 56, 60
_ALT_L, _ALT_R = 58, 61
_P = 35
_A = 0
_B = 11
_V = 9
_SPACE = 49
_F5 = 96
_FOUR = 21

_CMD_MASK = hk.Quartz.kCGEventFlagMaskCommand
_CTRL_MASK = hk.Quartz.kCGEventFlagMaskControl
_SHIFT_MASK = hk.Quartz.kCGEventFlagMaskShift
_ALT_MASK = hk.Quartz.kCGEventFlagMaskAlternate


class _Driver:
    """Feeds synthesized key/modifier events into a listener's tap callback.

    Mirrors the real Quartz tap: modifiers arrive as flagsChanged carrying the
    changed keycode + cumulative flags; ordinary keys arrive as keyDown/keyUp.
    keyDown/keyUp events also carry flags (CGEventGetFlags), which the char-chord
    path reads — so this driver lets a caller specify the flags on a key event.
    """

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

    def tap_disabled(self):
        self._l._tap_callback(None, hk.Quartz.kCGEventTapDisabledByTimeout, object(), None)


def _tap_listener(keys):
    fired = []
    listener = HotkeyListener(keys=keys, on_trigger=lambda: fired.append(1))
    return listener, fired


# ===========================================================================
# CATEGORY: char-chord ordering & boundaries
# ===========================================================================

def test_charchord_char_pressed_before_modifiers(monkeypatch):
    """P down BEFORE the modifiers are present must NOT fire (flags lack mods).
    Then a later clean tap (mods first, then P) must fire."""
    l, fired = _tap_listener(["cmd", "ctrl", "p"])
    d = _Driver(l, monkeypatch)
    d.key_down(_P, flags=0)            # P alone, no modifiers in flags
    assert fired == []
    # Now the proper tap.
    d.modifier(_CMD_L, _CMD_MASK)
    d.modifier(_CTRL_L, _CMD_MASK | _CTRL_MASK)
    d.key_down(_P, flags=_CMD_MASK | _CTRL_MASK)
    assert fired == [1]


def test_charchord_modifiers_present_in_keydown_flags_only(monkeypatch):
    """The gate reads THIS keyDown event's absolute flags. If the P keyDown
    carries the full modifier set, it fires even if no flagsChanged preceded
    (a real tap always carries the live flags on the key event)."""
    l, fired = _tap_listener(["cmd", "ctrl", "p"])
    d = _Driver(l, monkeypatch)
    d.key_down(_P, flags=_CMD_MASK | _CTRL_MASK)
    assert fired == [1]


def test_charchord_one_modifier_missing_in_keydown_flags(monkeypatch):
    """Only ctrl in the P keyDown flags (cmd missing) => no fire even though a
    prior flagsChanged claimed cmd. Absolute flags on the key event are king."""
    l, fired = _tap_listener(["cmd", "ctrl", "p"])
    d = _Driver(l, monkeypatch)
    d.modifier(_CMD_L, _CMD_MASK)
    d.modifier(_CTRL_L, _CMD_MASK | _CTRL_MASK)
    d.key_down(_P, flags=_CTRL_MASK)   # cmd absent from THIS event
    assert fired == []


def test_charchord_extra_unrelated_modifier_held_blocks_fire(monkeypatch):
    """Holding an extra modifier (shift) beyond the required set blocks
    firing: since issue #21 the gate is target_mods EQUALS held_mods, so
    ⌘⌃⇧P is a different chord than ⌘⌃P and must not fire it."""
    l, fired = _tap_listener(["cmd", "ctrl", "p"])
    d = _Driver(l, monkeypatch)
    d.key_down(_P, flags=_CMD_MASK | _CTRL_MASK | _SHIFT_MASK)
    assert fired == []


# ===========================================================================
# CATEGORY: char-chord re-arm & keyUp-suppression permutations
# ===========================================================================

def test_charchord_rearm_then_second_tap_no_char_keyup(monkeypatch):
    """Classic reported bug: works once, then again, with P keyUp never
    delivered. Modifiers clearing must re-arm."""
    l, fired = _tap_listener(["cmd", "ctrl", "p"])
    d = _Driver(l, monkeypatch)
    d.modifier(_CMD_L, _CMD_MASK)
    d.modifier(_CTRL_L, _CMD_MASK | _CTRL_MASK)
    d.key_down(_P, flags=_CMD_MASK | _CTRL_MASK)
    d.modifier(_CMD_L, _CTRL_MASK)     # cmd up (P keyUp suppressed)
    d.modifier(_CTRL_L, 0)             # ctrl up -> clear -> re-arm
    d.modifier(_CMD_L, _CMD_MASK)
    d.modifier(_CTRL_L, _CMD_MASK | _CTRL_MASK)
    d.key_down(_P, flags=_CMD_MASK | _CTRL_MASK)
    assert fired == [1, 1]


def test_charchord_no_phantom_on_bare_modifier_subset(monkeypatch):
    """After a suppressed-keyUp tap, tapping ONLY Cmd+Ctrl (no P) must not
    re-paste (no stuck char key)."""
    l, fired = _tap_listener(["cmd", "ctrl", "p"])
    d = _Driver(l, monkeypatch)
    d.modifier(_CMD_L, _CMD_MASK)
    d.modifier(_CTRL_L, _CMD_MASK | _CTRL_MASK)
    d.key_down(_P, flags=_CMD_MASK | _CTRL_MASK)
    d.modifier(_CMD_L, _CTRL_MASK)
    d.modifier(_CTRL_L, 0)
    assert fired == [1]
    # bare modifier subset
    d.modifier(_CMD_L, _CMD_MASK)
    d.modifier(_CTRL_L, _CMD_MASK | _CTRL_MASK)
    d.modifier(_CMD_L, _CTRL_MASK)
    d.modifier(_CTRL_L, 0)
    assert fired == [1]


def test_charchord_both_modifiers_drop_in_one_flagschanged(monkeypatch):
    """Both Cmd and Ctrl reported up in a SINGLE flagsChanged (flags->0). The
    re-arm gate (target_mods subset held) must clear, so the next tap fires."""
    l, fired = _tap_listener(["cmd", "ctrl", "p"])
    d = _Driver(l, monkeypatch)
    d.modifier(_CMD_L, _CMD_MASK)
    d.modifier(_CTRL_L, _CMD_MASK | _CTRL_MASK)
    d.key_down(_P, flags=_CMD_MASK | _CTRL_MASK)
    assert fired == [1]
    # one flagsChanged with everything cleared at once
    d.modifier(_CTRL_L, 0)             # flags now 0; ctrl is the carried key
    # second tap
    d.modifier(_CMD_L, _CMD_MASK)
    d.modifier(_CTRL_L, _CMD_MASK | _CTRL_MASK)
    d.key_down(_P, flags=_CMD_MASK | _CTRL_MASK)
    assert fired == [1, 1]


def test_charchord_late_char_keyup_after_modifiers_released(monkeypatch):
    """The P keyUp arrives LATE (after modifiers already released and re-armed).
    A late keyUp for an already-cleared key must not break the next tap and must
    not itself trigger anything."""
    l, fired = _tap_listener(["cmd", "ctrl", "p"])
    d = _Driver(l, monkeypatch)
    d.modifier(_CMD_L, _CMD_MASK)
    d.modifier(_CTRL_L, _CMD_MASK | _CTRL_MASK)
    d.key_down(_P, flags=_CMD_MASK | _CTRL_MASK)
    d.modifier(_CMD_L, _CTRL_MASK)
    d.modifier(_CTRL_L, 0)             # re-armed
    d.key_up(_P, flags=0)              # late keyUp, no modifiers
    assert fired == [1]                # the keyUp itself fired nothing
    d.modifier(_CMD_L, _CMD_MASK)
    d.modifier(_CTRL_L, _CMD_MASK | _CTRL_MASK)
    d.key_down(_P, flags=_CMD_MASK | _CTRL_MASK)
    assert fired == [1, 1]


def test_charchord_char_keyup_delivered_normally(monkeypatch):
    """When the P keyUp IS delivered (no Command suppression case), the chord
    re-arms via _char_key_up and the next tap fires."""
    l, fired = _tap_listener(["cmd", "ctrl", "p"])
    d = _Driver(l, monkeypatch)
    d.modifier(_CMD_L, _CMD_MASK)
    d.modifier(_CTRL_L, _CMD_MASK | _CTRL_MASK)
    d.key_down(_P, flags=_CMD_MASK | _CTRL_MASK)
    d.key_up(_P, flags=_CMD_MASK | _CTRL_MASK)   # keyUp delivered, mods still held
    d.modifier(_CMD_L, _CTRL_MASK)
    d.modifier(_CTRL_L, 0)
    d.modifier(_CMD_L, _CMD_MASK)
    d.modifier(_CTRL_L, _CMD_MASK | _CTRL_MASK)
    d.key_down(_P, flags=_CMD_MASK | _CTRL_MASK)
    assert fired == [1, 1]


# ===========================================================================
# CATEGORY: idempotency / repeats / autorepeat
# ===========================================================================

def test_charchord_autorepeat_storm_single_fire(monkeypatch):
    """A long autorepeat storm of P keyDowns fires exactly once per hold."""
    l, fired = _tap_listener(["cmd", "ctrl", "p"])
    d = _Driver(l, monkeypatch)
    d.modifier(_CMD_L, _CMD_MASK)
    d.modifier(_CTRL_L, _CMD_MASK | _CTRL_MASK)
    for _ in range(50):
        d.key_down(_P, flags=_CMD_MASK | _CTRL_MASK)
    assert fired == [1]


def test_charchord_p_twice_while_mods_held(monkeypatch):
    """P pressed, P keyUp delivered, P pressed again WITHOUT modifiers ever
    releasing. Per the docstring the re-arm path is the modifier flags clearing;
    _char_key_up also resets _fired_this_hold, so a discrete second P keydown
    with mods held re-fires. Document the realized behavior."""
    l, fired = _tap_listener(["cmd", "ctrl", "p"])
    d = _Driver(l, monkeypatch)
    d.modifier(_CMD_L, _CMD_MASK)
    d.modifier(_CTRL_L, _CMD_MASK | _CTRL_MASK)
    d.key_down(_P, flags=_CMD_MASK | _CTRL_MASK)   # fires
    d.key_up(_P, flags=_CMD_MASK | _CTRL_MASK)     # resets fired flag
    d.key_down(_P, flags=_CMD_MASK | _CTRL_MASK)   # second discrete press
    # Two discrete taps of P with the modifiers held => two pastes is acceptable
    # (each is a distinct user action). Assert it fired exactly twice, not stuck.
    assert fired == [1, 1]


def test_charchord_rapid_many_taps(monkeypatch):
    """Many rapid clean taps each fire exactly once."""
    l, fired = _tap_listener(["cmd", "ctrl", "p"])
    d = _Driver(l, monkeypatch)
    for _ in range(20):
        d.modifier(_CMD_L, _CMD_MASK)
        d.modifier(_CTRL_L, _CMD_MASK | _CTRL_MASK)
        d.key_down(_P, flags=_CMD_MASK | _CTRL_MASK)
        d.modifier(_CMD_L, _CTRL_MASK)
        d.modifier(_CTRL_L, 0)
    assert fired == [1] * 20


def test_charchord_contaminating_key_irrelevant(monkeypatch):
    """For a char chord, a stray key pressed during the hold is irrelevant: the
    chord already fired on P's keyDown. A stray '4' must neither suppress the
    already-fired trigger nor cause a second fire."""
    l, fired = _tap_listener(["cmd", "ctrl", "p"])
    d = _Driver(l, monkeypatch)
    d.modifier(_CMD_L, _CMD_MASK)
    d.modifier(_CTRL_L, _CMD_MASK | _CTRL_MASK)
    d.key_down(_P, flags=_CMD_MASK | _CTRL_MASK)
    d.key_down(_FOUR, flags=_CMD_MASK | _CTRL_MASK)
    d.key_up(_FOUR, flags=_CMD_MASK | _CTRL_MASK)
    assert fired == [1]


# ===========================================================================
# CATEGORY: left/right modifier variants
# ===========================================================================

def test_charchord_right_variant_modifiers_fire(monkeypatch):
    """Right Cmd (54) + right Ctrl (62) map to the same tokens and must fire."""
    l, fired = _tap_listener(["cmd", "ctrl", "p"])
    d = _Driver(l, monkeypatch)
    d.modifier(_CMD_R, _CMD_MASK)
    d.modifier(_CTRL_R, _CMD_MASK | _CTRL_MASK)
    d.key_down(_P, flags=_CMD_MASK | _CTRL_MASK)
    assert fired == [1]


def test_modonly_both_ctrl_variants_one_release_keeps_active(monkeypatch):
    """Hold both left AND right ctrl; releasing one must NOT deactivate (token
    is still held by the other variant), so the combo only fires when the LAST
    variant goes up."""
    l, fired = _tap_listener(["ctrl", "shift"])
    d = _Driver(l, monkeypatch)
    # Press left ctrl, then right ctrl (flag stays set), then shift.
    d.modifier(_CTRL_L, _CTRL_MASK)
    d.modifier(_CTRL_R, _CTRL_MASK)            # second ctrl variant, flag still set
    d.modifier(_SHIFT_L, _CTRL_MASK | _SHIFT_MASK)  # combo held
    # Release shift cleanly first.
    d.modifier(_SHIFT_L, _CTRL_MASK)
    # Now one ctrl variant up but flag still set (other still down).
    d.modifier(_CTRL_L, _CTRL_MASK)            # still set -> toggles left off only
    # Trigger should have fired exactly once on the shift release (combo broke).
    assert fired == [1]


def test_modonly_left_right_same_modifier_flag_self_heal(monkeypatch):
    """flagsChanged whose mask bit is clear means every variant is up: this must
    drop ALL held variants for the token (self-heal), notifying waiters."""
    l, fired = _tap_listener(["ctrl", "shift"])
    d = _Driver(l, monkeypatch)
    d.modifier(_CTRL_L, _CTRL_MASK)
    d.modifier(_CTRL_R, _CTRL_MASK)       # both ctrl variants held
    d.modifier(_SHIFT_L, _CTRL_MASK | _SHIFT_MASK)
    d.modifier(_SHIFT_L, _CTRL_MASK)      # shift up -> fires
    # ctrl flag clears entirely in one event -> both variants must drop.
    d.modifier(_CTRL_R, 0)
    assert fired == [1]
    assert l._held == {}                  # nothing stuck


# ===========================================================================
# CATEGORY: modifier-only tap chord (contamination)
# ===========================================================================

def test_modonly_clean_release_fires(monkeypatch):
    l, fired = _tap_listener(["cmd", "ctrl"])
    d = _Driver(l, monkeypatch)
    d.modifier(_CMD_L, _CMD_MASK)
    d.modifier(_CTRL_L, _CMD_MASK | _CTRL_MASK)
    d.modifier(_CMD_L, _CTRL_MASK)
    d.modifier(_CTRL_L, 0)
    assert fired == [1]


def test_modonly_contamination_cancels(monkeypatch):
    """Cmd+Ctrl combo with a stray '4' pressed during the hold (the screenshot
    case Cmd+Ctrl+Shift+4 minus shift) must cancel the trigger."""
    l, fired = _tap_listener(["cmd", "ctrl"])
    d = _Driver(l, monkeypatch)
    d.modifier(_CMD_L, _CMD_MASK)
    d.modifier(_CTRL_L, _CMD_MASK | _CTRL_MASK)
    d.key_down(_FOUR, flags=_CMD_MASK | _CTRL_MASK)
    d.key_up(_FOUR, flags=_CMD_MASK | _CTRL_MASK)
    d.modifier(_CMD_L, _CTRL_MASK)
    d.modifier(_CTRL_L, 0)
    assert fired == []


def test_modonly_contamination_then_clean_hold_fires(monkeypatch):
    """Contamination is per-hold; a clean hold afterwards fires."""
    l, fired = _tap_listener(["cmd", "ctrl"])
    d = _Driver(l, monkeypatch)
    d.modifier(_CMD_L, _CMD_MASK)
    d.modifier(_CTRL_L, _CMD_MASK | _CTRL_MASK)
    d.key_down(_FOUR, flags=_CMD_MASK | _CTRL_MASK)
    d.modifier(_CMD_L, _CTRL_MASK)
    d.modifier(_CTRL_L, 0)
    assert fired == []
    d.modifier(_CMD_L, _CMD_MASK)
    d.modifier(_CTRL_L, _CMD_MASK | _CTRL_MASK)
    d.modifier(_CMD_L, _CTRL_MASK)
    d.modifier(_CTRL_L, 0)
    assert fired == [1]


def test_modonly_extra_modifier_contaminates_the_hold(monkeypatch):
    """For a Cmd+Ctrl combo, Shift joining mid-hold is an EXTRA modifier and
    contaminates the hold (issue #21) — exactly like a stray character
    keyDown. Even though shift leaves before the release, the hold stays
    contaminated and must not fire."""
    l, fired = _tap_listener(["cmd", "ctrl"])
    d = _Driver(l, monkeypatch)
    d.modifier(_CMD_L, _CMD_MASK)
    d.modifier(_CTRL_L, _CMD_MASK | _CTRL_MASK)
    d.modifier(_SHIFT_L, _CMD_MASK | _CTRL_MASK | _SHIFT_MASK)  # extra modifier
    d.modifier(_SHIFT_L, _CMD_MASK | _CTRL_MASK)                # shift up
    d.modifier(_CMD_L, _CTRL_MASK)
    d.modifier(_CTRL_L, 0)
    assert fired == []


# ===========================================================================
# CATEGORY: BOTH required modifiers cleared in ONE flagsChanged  ***BUGS***
# ===========================================================================
#
# On macOS, releasing two modifiers in quick succession can surface as a single
# flagsChanged whose absolute flags have already dropped BOTH bits, carrying the
# keycode of just one of them. _flags_changed only releases the token of the
# carried keycode; it does NOT scan the cleared flags for the OTHER held tokens.
# Result: the second modifier is left permanently in self._held.


def test_modonly_both_modifiers_clear_in_one_flagschanged_no_stuck_key(monkeypatch):
    """INTENDED: a clean simultaneous release of Cmd+Ctrl fires once and leaves
    NOTHING held (every variant whose flag is now clear must be released).

    ACTUAL: only the carried token (ctrl) is released; cmd is left stuck in
    _held, so wait_all_released() can never confirm release."""
    l, fired = _tap_listener(["cmd", "ctrl"])
    d = _Driver(l, monkeypatch)
    d.modifier(_CMD_L, _CMD_MASK)
    d.modifier(_CTRL_L, _CMD_MASK | _CTRL_MASK)        # combo held
    d.modifier(_CTRL_L, 0)                             # BOTH bits clear in one event
    assert fired == [1]
    assert l._held == {}, f"a modifier was left stuck in _held: {l._held}"


def test_modonly_wait_all_released_after_simultaneous_release(monkeypatch):
    """INTENDED: after a clean simultaneous release, wait_all_released() returns
    True immediately. ACTUAL: it times out because cmd is stuck in _held — the
    exact condition _do_repaste blocks on before pasting."""
    l, fired = _tap_listener(["cmd", "ctrl"])
    d = _Driver(l, monkeypatch)
    d.modifier(_CMD_L, _CMD_MASK)
    d.modifier(_CTRL_L, _CMD_MASK | _CTRL_MASK)
    d.modifier(_CTRL_L, 0)
    assert l.wait_all_released(timeout=0.3) is True


def test_modonly_second_tap_fires_after_simultaneous_release(monkeypatch):
    """INTENDED: after a simultaneous release, the NEXT clean tap fires again.
    ACTUAL: the stuck cmd corrupts the held set so the second tap is silently
    dropped (this is a re-paste that simply stops working)."""
    l, fired = _tap_listener(["cmd", "ctrl"])
    d = _Driver(l, monkeypatch)
    # First tap, released simultaneously.
    d.modifier(_CMD_L, _CMD_MASK)
    d.modifier(_CTRL_L, _CMD_MASK | _CTRL_MASK)
    d.modifier(_CTRL_L, 0)
    assert fired == [1]
    # Second, perfectly clean tap.
    d.modifier(_CMD_L, _CMD_MASK)
    d.modifier(_CTRL_L, _CMD_MASK | _CTRL_MASK)
    d.modifier(_CMD_L, _CTRL_MASK)
    d.modifier(_CTRL_L, 0)
    assert fired == [1, 1], "second clean tap did not re-paste"


def test_charchord_simultaneous_modifier_release_no_stuck_key(monkeypatch):
    """Same simultaneous-release defect for a CHAR chord (Cmd+Ctrl+P): after
    firing on P's keyDown, if both modifiers clear in one flagsChanged the char
    chord re-arms (good) but a modifier is still left stuck in _held, so
    wait_all_released() — which _do_repaste blocks on — times out."""
    l, fired = _tap_listener(["cmd", "ctrl", "p"])
    d = _Driver(l, monkeypatch)
    d.modifier(_CMD_L, _CMD_MASK)
    d.modifier(_CTRL_L, _CMD_MASK | _CTRL_MASK)
    d.key_down(_P, flags=_CMD_MASK | _CTRL_MASK)
    assert fired == [1]
    d.modifier(_CTRL_L, 0)                            # both mods clear at once
    assert l._held == {}, f"a modifier was left stuck in _held: {l._held}"


# ===========================================================================
# CATEGORY: partial combos / wrong combos must not fire
# ===========================================================================

def test_charchord_only_one_modifier_then_char_no_fire(monkeypatch):
    l, fired = _tap_listener(["cmd", "ctrl", "p"])
    d = _Driver(l, monkeypatch)
    d.modifier(_CMD_L, _CMD_MASK)
    d.key_down(_P, flags=_CMD_MASK)    # only cmd, ctrl missing
    assert fired == []


def test_charchord_wrong_char_no_fire(monkeypatch):
    """Pressing a DIFFERENT character key (e.g. V) with the modifiers held must
    not fire a Cmd+Ctrl+P chord."""
    l, fired = _tap_listener(["cmd", "ctrl", "p"])
    d = _Driver(l, monkeypatch)
    d.modifier(_CMD_L, _CMD_MASK)
    d.modifier(_CTRL_L, _CMD_MASK | _CTRL_MASK)
    d.key_down(_V, flags=_CMD_MASK | _CTRL_MASK)   # V, not P
    assert fired == []


def test_modonly_partial_combo_no_fire(monkeypatch):
    """Only one of two required modifiers ever pressed -> no fire on release."""
    l, fired = _tap_listener(["cmd", "ctrl"])
    d = _Driver(l, monkeypatch)
    d.modifier(_CMD_L, _CMD_MASK)
    d.modifier(_CMD_L, 0)
    assert fired == []


# ===========================================================================
# CATEGORY: multi-character chords & named non-modifier keys
# ===========================================================================

def test_two_char_chord_fires_only_when_both_chars_down(monkeypatch):
    """["cmd","a","b"]: must fire only once BOTH a and b are down together with
    cmd held — pressing a alone must not fire."""
    l, fired = _tap_listener(["cmd", "a", "b"])
    d = _Driver(l, monkeypatch)
    d.modifier(_CMD_L, _CMD_MASK)
    d.key_down(_A, flags=_CMD_MASK)    # only a -> not the full key set
    assert fired == []
    d.key_down(_B, flags=_CMD_MASK)    # now a and b both down
    assert fired == [1]


def test_two_char_chord_either_char_alone_never_fires(monkeypatch):
    """If only one of the two chars is ever pressed, the chord must not fire."""
    l, fired = _tap_listener(["cmd", "a", "b"])
    d = _Driver(l, monkeypatch)
    d.modifier(_CMD_L, _CMD_MASK)
    for _ in range(5):
        d.key_down(_A, flags=_CMD_MASK)
    assert fired == []


def test_named_key_chord_space_fires_on_keydown(monkeypatch):
    """["cmd","space"]: space is a NAMED non-modifier key, so the chord is a
    keydown-fire chord and must fire on space's keyDown with cmd held."""
    l, fired = _tap_listener(["cmd", "space"])
    d = _Driver(l, monkeypatch)
    d.modifier(_CMD_L, _CMD_MASK)
    d.key_down(_SPACE, flags=_CMD_MASK)
    assert fired == [1]


def test_named_key_chord_f5_fires_on_keydown(monkeypatch):
    """["ctrl","f5"]: f5 is a named key -> keydown-fire chord."""
    l, fired = _tap_listener(["ctrl", "f5"])
    d = _Driver(l, monkeypatch)
    d.modifier(_CTRL_L, _CTRL_MASK)
    d.key_down(_F5, flags=_CTRL_MASK)
    assert fired == [1]


def test_named_key_chord_f5_rearms(monkeypatch):
    """The named-key chord must re-arm on modifier release like the char chord."""
    l, fired = _tap_listener(["ctrl", "f5"])
    d = _Driver(l, monkeypatch)
    d.modifier(_CTRL_L, _CTRL_MASK)
    d.key_down(_F5, flags=_CTRL_MASK)
    d.modifier(_CTRL_L, 0)             # ctrl up -> re-arm
    d.modifier(_CTRL_L, _CTRL_MASK)
    d.key_down(_F5, flags=_CTRL_MASK)
    assert fired == [1, 1]


# ===========================================================================
# CATEGORY: tap disable/re-enable mid-sequence
# ===========================================================================

def test_charchord_tap_disabled_event_midsequence_does_not_break(monkeypatch):
    """A kCGEventTapDisabledByTimeout arriving mid-hold must be handled (tap
    re-enabled) without disturbing the chord state; the P keyDown still fires."""
    l, fired = _tap_listener(["cmd", "ctrl", "p"])
    l._tap = object()  # so the disabled-handler's re-enable branch runs
    monkeypatch.setattr(hk.Quartz, "CGEventTapEnable", lambda tap, on: None)
    d = _Driver(l, monkeypatch)
    d.modifier(_CMD_L, _CMD_MASK)
    d.modifier(_CTRL_L, _CMD_MASK | _CTRL_MASK)
    d.tap_disabled()                   # system disables tap mid-hold
    d.key_down(_P, flags=_CMD_MASK | _CTRL_MASK)
    assert fired == [1]


def test_modonly_flagschanged_self_heal_missed_release(monkeypatch):
    """If a ctrl keyUp (flagsChanged with bit clear) is the first thing seen for
    a ctrl never recorded as pressed, the self-heal release path must not raise
    and must leave nothing stuck."""
    l, fired = _tap_listener(["ctrl", "shift"])
    d = _Driver(l, monkeypatch)
    # ctrl flag clear with no prior press recorded
    d.modifier(_CTRL_L, 0)
    assert fired == []
    assert l._held == {}


# ===========================================================================
# FUNCTIONAL LAYER -- App._on_repaste -> _do_repaste -> paste_text
# ===========================================================================

class _AppDriver:
    """Drives a real App's repaste hotkey for a char chord. Char chords ride
    the Carbon backend (issue #23): one tap = a Carbon pressed+released pair
    routed through flow.carbon_hotkey's dispatch registry, the same route the
    real installed Carbon event handler takes."""

    def __init__(self, app, monkeypatch):
        self._l = app.repaste_hotkey

    def tap_cmd_ctrl_p(self):
        carbon_mod._dispatch(self._l._hotkey_id, carbon_mod.kEventHotKeyPressed)
        carbon_mod._dispatch(self._l._hotkey_id, carbon_mod.kEventHotKeyReleased)


def _build_app(monkeypatch, repaste_keys=("cmd", "ctrl", "p")):
    monkeypatch.setattr(app_mod.HotkeyListener, "start", lambda self: None)
    monkeypatch.setattr(app_mod.HotkeyListener, "stop", lambda self: None)
    # Carbon seams (issue #23): no real registration; the wait's OS modifier
    # poll answers "all up".
    monkeypatch.setattr(
        carbon_mod, "_register", lambda vk, mask, hkid: (0, f"ref-{hkid}")
    )
    monkeypatch.setattr(carbon_mod, "_unregister", lambda ref: 0)
    monkeypatch.setattr(carbon_mod, "_ensure_handler", lambda: None)
    monkeypatch.setattr(carbon_mod, "modifiers_physically_down", lambda: False)
    cfg = Config()
    cfg.repaste_keys = list(repaste_keys)
    app = App(cfg)
    app.repaste_hotkey.start()  # register with the (mocked) Carbon layer
    app.can_paste = lambda: True
    pasted: queue.Queue = queue.Queue()
    monkeypatch.setattr(
        app_mod, "paste_text", lambda text, restore_delay=0.4: pasted.put(text)
    )
    return app, pasted


def test_func_pastes_newest_plus_trailing_space(monkeypatch):
    app, pasted = _build_app(monkeypatch)
    app.history.add("first")
    app.history.add("second")          # newest
    _AppDriver(app, monkeypatch).tap_cmd_ctrl_p()
    assert pasted.get(timeout=3.0) == "second "   # newest + trailing space


def test_func_empty_history_no_paste(monkeypatch):
    """Empty history => no paste; the app notifies instead."""
    app, pasted = _build_app(monkeypatch)
    notes = []
    app.notify = lambda m: notes.append(m)
    _AppDriver(app, monkeypatch).tap_cmd_ctrl_p()
    # give the worker a moment, then assert nothing pasted.
    with pytest.raises(queue.Empty):
        pasted.get(timeout=1.0)
    assert any("No recent dictation" in n for n in notes)


def test_func_cannot_paste_gate(monkeypatch):
    """can_paste() False => no paste."""
    app, pasted = _build_app(monkeypatch)
    app.history.add("blocked")
    app.can_paste = lambda: False
    _AppDriver(app, monkeypatch).tap_cmd_ctrl_p()
    with pytest.raises(queue.Empty):
        pasted.get(timeout=1.0)


def test_func_not_idle_blocks_repaste(monkeypatch):
    """If the app is mid-dictation (state != IDLE) the re-paste must not paste
    and must notify the user to finish first."""
    app, pasted = _build_app(monkeypatch)
    app.history.add("midflight")
    notes = []
    app.notify = lambda m: notes.append(m)
    app._state = PROCESSING        # simulate an in-flight dictation
    _AppDriver(app, monkeypatch).tap_cmd_ctrl_p()
    with pytest.raises(queue.Empty):
        pasted.get(timeout=1.0)
    assert any("Finish the current dictation" in n for n in notes)
    # And the worker must NOT have clobbered the in-flight state back to IDLE.
    assert app._state == PROCESSING


def test_func_state_returns_to_idle_after_paste(monkeypatch):
    app, pasted = _build_app(monkeypatch)
    app.history.add("once")
    _AppDriver(app, monkeypatch).tap_cmd_ctrl_p()
    assert pasted.get(timeout=3.0) == "once "
    # allow the finally: block to run
    deadline = time.monotonic() + 2.0
    while app._state != IDLE and time.monotonic() < deadline:
        time.sleep(0.01)
    assert app._state == IDLE


def test_func_second_tap_repastes_again(monkeypatch):
    app, pasted = _build_app(monkeypatch)
    app.history.add("twice")
    d = _AppDriver(app, monkeypatch)
    d.tap_cmd_ctrl_p()
    assert pasted.get(timeout=3.0) == "twice "
    # wait until back to IDLE so the second _do_repaste isn't blocked
    deadline = time.monotonic() + 2.0
    while app._state != IDLE and time.monotonic() < deadline:
        time.sleep(0.01)
    d.tap_cmd_ctrl_p()
    assert pasted.get(timeout=3.0) == "twice "


def test_func_unicode_and_whitespace_text(monkeypatch):
    app, pasted = _build_app(monkeypatch)
    weird = "  café — naïve 𝓤𝓷𝓲𝓬𝓸𝓭𝓮 \t漢字\n"
    app.history.add(weird)
    _AppDriver(app, monkeypatch).tap_cmd_ctrl_p()
    assert pasted.get(timeout=3.0) == weird + " "


def test_func_very_long_text(monkeypatch):
    app, pasted = _build_app(monkeypatch)
    long = "x" * 100_000
    app.history.add(long)
    _AppDriver(app, monkeypatch).tap_cmd_ctrl_p()
    assert pasted.get(timeout=3.0) == long + " "


def test_func_empty_string_dictation(monkeypatch):
    """An empty-string item in history is still the newest item; per the code it
    pastes "" + " " = " ". (History.add stores whatever it's given; _do_repaste
    only guards on the LIST being empty, not the string.)"""
    app, pasted = _build_app(monkeypatch)
    app.history.add("")
    _AppDriver(app, monkeypatch).tap_cmd_ctrl_p()
    assert pasted.get(timeout=3.0) == " "


def test_func_concurrent_taps_single_paste(monkeypatch):
    """Two re-paste taps racing: the IDLE guard + state=PROCESSING must let only
    ONE through; the second sees non-IDLE and is refused. So exactly one paste
    reaches paste_text for a single history item, even under a race."""
    app, pasted = _build_app(monkeypatch)
    app.history.add("racy")
    notes = []
    app.notify = lambda m: notes.append(m)

    # Make paste_text slow so the two workers genuinely overlap.
    real_q = pasted
    def slow_paste(text, restore_delay=0.4):
        time.sleep(0.3)
        real_q.put(text)
    monkeypatch.setattr(app_mod, "paste_text", slow_paste)

    # Fire two _do_repaste workers as directly and simultaneously as possible.
    barrier = threading.Barrier(2)
    def worker():
        barrier.wait()
        app._do_repaste()
    t1 = threading.Thread(target=worker, daemon=True)
    t2 = threading.Thread(target=worker, daemon=True)
    t1.start(); t2.start()
    t1.join(timeout=5); t2.join(timeout=5)

    # Exactly one paste should have happened.
    got = []
    try:
        while True:
            got.append(real_q.get_nowait())
    except queue.Empty:
        pass
    assert got == ["racy "], f"expected one paste, got {got}"


def test_func_wait_all_released_before_paste(monkeypatch):
    """_do_repaste must wait for the combo keys to be physically released before
    pasting (so the synthesized Cmd+V is plain). Hold the Carbon chord pressed
    and verify the paste is delayed until the released event arrives."""
    app, pasted = _build_app(monkeypatch)
    app.history.add("held")
    # The chord is still physically held: pressed arrived, released has not.
    carbon_mod._dispatch(app.repaste_hotkey._hotkey_id, carbon_mod.kEventHotKeyPressed)

    done = threading.Event()
    def run():
        app._do_repaste()
        done.set()
    threading.Thread(target=run, daemon=True).start()

    # While still held, nothing should paste.
    with pytest.raises(queue.Empty):
        pasted.get(timeout=0.5)
    # Release the shadow state the way the Carbon released event would —
    # without dispatching a real released event, which would also fire a
    # SECOND on_trigger (this test drives _do_repaste directly).
    with app.repaste_hotkey._cond:
        app.repaste_hotkey._pressed = False
        app.repaste_hotkey._cond.notify_all()
    assert pasted.get(timeout=3.0) == "held "
    assert done.wait(timeout=2.0)


def test_func_modonly_repaste_combo_end_to_end(monkeypatch):
    """The DEFAULT repaste combo is modifier-only (cmd+ctrl). A clean tap of it
    must re-paste the newest dictation end to end."""
    app, pasted = _build_app(monkeypatch, repaste_keys=("cmd", "ctrl"))
    app.history.add("modonly")
    l = app.repaste_hotkey
    kc = {"v": 0}
    flags = {"v": 0}
    monkeypatch.setattr(hk.Quartz, "CGEventGetIntegerValueField", lambda e, f: kc["v"])
    monkeypatch.setattr(hk.Quartz, "CGEventGetFlags", lambda e: flags["v"])

    def mod(keycode, fl):
        kc["v"], flags["v"] = keycode, fl
        l._tap_callback(None, hk.Quartz.kCGEventFlagsChanged, object(), None)

    mod(_CMD_L, _CMD_MASK)
    mod(_CTRL_L, _CMD_MASK | _CTRL_MASK)
    mod(_CMD_L, _CTRL_MASK)
    mod(_CTRL_L, 0)
    assert pasted.get(timeout=3.0) == "modonly "


def test_func_history_caps_at_ten_newest_wins(monkeypatch):
    """Adding more than MAX_HISTORY keeps the NEWEST as items()[0]."""
    app, pasted = _build_app(monkeypatch)
    for i in range(15):
        app.history.add(f"item{i}")
    _AppDriver(app, monkeypatch).tap_cmd_ctrl_p()
    assert pasted.get(timeout=3.0) == "item14 "


def test_func_modonly_simultaneous_release_does_not_stall_repaste(monkeypatch):
    """END-TO-END regression guard for the simultaneous-release bug on the
    DEFAULT (modifier-only) re-paste combo Cmd+Ctrl.

    A clean Cmd+Ctrl tap whose two modifiers clear in one flagsChanged must
    leave nothing stuck in _held, so the first gate _do_repaste runs
    (wait_all_released) confirms release instantly and the re-paste proceeds
    promptly. Before the fix, cmd was left stuck and wait_all_released() blocked
    for its full 2s timeout, corrupting the held-set for the next tap.

    We exercise wait_all_released synchronously (on_trigger neutered) so this
    test spawns no background worker and cannot leak into any other test.
    """
    app, pasted = _build_app(monkeypatch, repaste_keys=("cmd", "ctrl"))
    app.history.add("prompt")
    l = app.repaste_hotkey
    state = {"kc": 0, "fl": 0}
    monkeypatch.setattr(hk.Quartz, "CGEventGetIntegerValueField", lambda e, f: state["kc"])
    monkeypatch.setattr(hk.Quartz, "CGEventGetFlags", lambda e: state["fl"])

    def mod(keycode, fl):
        state["kc"], state["fl"] = keycode, fl
        l._tap_callback(None, hk.Quartz.kCGEventFlagsChanged, object(), None)

    # Drive a real simultaneous-release tap on the listener. Neuter on_trigger so
    # the listener does NOT auto-spawn a _do_repaste worker (we exercise the
    # blocking step _do_repaste depends on — wait_all_released — synchronously,
    # so this test spawns NO background thread and cannot leak into any other
    # test).
    l._on_trigger = lambda: None
    mod(_CMD_L, _CMD_MASK)
    mod(_CTRL_L, _CMD_MASK | _CTRL_MASK)
    mod(_CTRL_L, 0)                      # BOTH modifiers clear in one event

    # Fixed behavior: a clean simultaneous release leaves NOTHING stuck, so the
    # first gate _do_repaste runs (wait_all_released) confirms release instantly
    # and the re-paste proceeds promptly instead of stalling on the 2s timeout.
    assert l._held == {}, f"a modifier was left stuck in _held: {l._held}"
    assert l.wait_all_released(timeout=0.3) is True
