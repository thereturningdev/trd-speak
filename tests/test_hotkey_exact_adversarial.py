"""Adversarial tests for exact-modifier matching (issue #21) in HotkeyListener.

Asserts the INTENDED spec, not current behaviour:

- HOLD: activate only when held targets == target set AND no extra modifier is
  down; an extra appearing mid-hold deactivates once; no late activation when
  the extra leaves while targets stay held; a fresh full completion re-arms.
- TAP (modifier-only): an extra present when the full hold completes, or
  appearing during the hold, contaminates that hold (per-hold); a target
  modifier bounced with no extras resets contamination.
- Keydown-fire chords: char keyDown fires only when the event's modifiers
  EQUAL the target modifiers; re-arm via _rearm_on_modifier_release / char
  keyUp; autorepeat fires once per hold.
- Invariants: after full release (flags 0), _held == {} and
  wait_all_released(0.2) is True; on_activate/on_deactivate strictly
  alternate; callback exceptions must not corrupt state.

Drives the real _tap_callback with synthetic events (the _Driver pattern from
tests/test_hotkey_exact_modifiers.py); no real event tap needed.
"""

import flow.hotkey as hk
from flow.hotkey import HotkeyListener

# Virtual keycodes.
_CMD_L = 55
_CMD_R = 54
_CTRL_L = 59
_SHIFT_L = 56
_ALT_L = 58
_P = 35
_FOUR = 21

# Flag masks.
_CMD = hk.Quartz.kCGEventFlagMaskCommand
_CTRL = hk.Quartz.kCGEventFlagMaskControl
_SHIFT = hk.Quartz.kCGEventFlagMaskShift
_ALT = hk.Quartz.kCGEventFlagMaskAlternate


class _Driver:
    """Feeds synthesized key/modifier events into one or more listeners'
    tap callbacks (flagsChanged carries cumulative ABSOLUTE modifier flags)."""

    def __init__(self, listeners, monkeypatch):
        self._listeners = listeners
        self._keycode = 0
        self._flags = 0
        monkeypatch.setattr(
            hk.Quartz, "CGEventGetIntegerValueField", lambda e, f: self._keycode
        )
        monkeypatch.setattr(hk.Quartz, "CGEventGetFlags", lambda e: self._flags)

    def modifier(self, keycode, flags):
        self._keycode, self._flags = keycode, flags
        for l in self._listeners:
            l._tap_callback(None, hk.Quartz.kCGEventFlagsChanged, object(), None)

    def key_down(self, keycode, flags=None):
        self._keycode = keycode
        if flags is not None:
            self._flags = flags
        for l in self._listeners:
            l._tap_callback(None, hk.Quartz.kCGEventKeyDown, object(), None)

    def key_up(self, keycode, flags=None):
        self._keycode = keycode
        if flags is not None:
            self._flags = flags
        for l in self._listeners:
            l._tap_callback(None, hk.Quartz.kCGEventKeyUp, object(), None)


def _tap(keys, monkeypatch):
    fired = []
    l = HotkeyListener(keys=keys, on_trigger=lambda: fired.append(1))
    return _Driver([l], monkeypatch), fired, l


def _hold(keys, monkeypatch):
    ev = []
    l = HotkeyListener(
        keys=keys,
        on_activate=lambda: ev.append("on"),
        on_deactivate=lambda: ev.append("off"),
    )
    return _Driver([l], monkeypatch), ev, l


def _assert_released(listener):
    """Invariant after a full release: nothing held, waiters unblock."""
    assert listener._held == {}, f"_held not empty: {listener._held}"
    assert listener.wait_all_released(timeout=0.2) is True


# ===========================================================================
# CATEGORY: hold-extras — extra modifiers vs hold-mode activation
# ===========================================================================


def test_hold_last_target_and_extra_coalesced_in_one_event(monkeypatch):
    """ctrl held; ONE flagsChanged adds shift (last target) AND cmd (extra)
    together. The hold completes UNDER an extra -> must not activate."""
    d, ev, l = _hold(["ctrl", "shift"], monkeypatch)
    d.modifier(_CTRL_L, _CTRL)
    d.modifier(_SHIFT_L, _CTRL | _SHIFT | _CMD)  # shift + cmd in one event
    assert ev == []
    d.modifier(0, 0)
    assert ev == []
    _assert_released(l)


def test_hold_target_drop_and_extra_add_same_event_deactivates_once(monkeypatch):
    """Active ctrl+shift; one event drops shift AND adds cmd. Exactly one
    'off', and the later full release must not produce a second 'off'."""
    d, ev, l = _hold(["ctrl", "shift"], monkeypatch)
    d.modifier(_CTRL_L, _CTRL)
    d.modifier(_SHIFT_L, _CTRL | _SHIFT)
    assert ev == ["on"]
    d.modifier(_SHIFT_L, _CTRL | _CMD)  # shift up + cmd down, coalesced
    assert ev == ["on", "off"]
    d.modifier(0, 0)
    assert ev == ["on", "off"]
    _assert_released(l)


def test_hold_extra_bounces_repeatedly_only_one_deactivate(monkeypatch):
    """Active hold; extra joins (off), leaves, joins again, leaves; then full
    release. Strict alternation: exactly one on/off pair."""
    d, ev, l = _hold(["ctrl", "shift"], monkeypatch)
    d.modifier(_CTRL_L, _CTRL)
    d.modifier(_SHIFT_L, _CTRL | _SHIFT)
    d.modifier(_CMD_L, _CTRL | _SHIFT | _CMD)  # extra -> off
    d.modifier(_CMD_L, _CTRL | _SHIFT)         # extra leaves: no late 'on'
    d.modifier(_CMD_L, _CTRL | _SHIFT | _CMD)  # extra again: already off
    d.modifier(_CMD_L, _CTRL | _SHIFT)
    d.modifier(0, 0)
    assert ev == ["on", "off"]
    _assert_released(l)


def test_hold_reactivates_on_fresh_completion_after_extra_cleared(monkeypatch):
    """After an extra-caused deactivation and the extra leaving, bouncing one
    target (up+down, no extras) is a fresh full completion -> activates."""
    d, ev, l = _hold(["ctrl", "shift"], monkeypatch)
    d.modifier(_CTRL_L, _CTRL)
    d.modifier(_SHIFT_L, _CTRL | _SHIFT)
    d.modifier(_CMD_L, _CTRL | _SHIFT | _CMD)  # off
    d.modifier(_CMD_L, _CTRL | _SHIFT)         # extra gone; no late activation
    assert ev == ["on", "off"]
    d.modifier(_SHIFT_L, _CTRL)                # bounce shift
    d.modifier(_SHIFT_L, _CTRL | _SHIFT)       # fresh completion, no extras
    assert ev == ["on", "off", "on"]
    d.modifier(0, 0)
    assert ev == ["on", "off", "on", "off"]
    _assert_released(l)


def test_hold_target_bounce_under_persistent_extra_never_activates(monkeypatch):
    """Extra stays down the whole time; bouncing a target re-completes the set
    but the extra still gates activation."""
    d, ev, l = _hold(["ctrl", "shift"], monkeypatch)
    d.modifier(_CMD_L, _CMD)                           # extra first
    d.modifier(_CTRL_L, _CMD | _CTRL)
    d.modifier(_SHIFT_L, _CMD | _CTRL | _SHIFT)
    d.modifier(_SHIFT_L, _CMD | _CTRL)                 # bounce shift
    d.modifier(_SHIFT_L, _CMD | _CTRL | _SHIFT)        # complete again, extra still down
    d.modifier(0, 0)
    assert ev == []
    _assert_released(l)


def test_hold_keycode0_full_chord_plus_extra_no_activation(monkeypatch):
    """Keycode-0 flagsChanged (ShortcutRecorder #129) carrying targets AND an
    extra in one bitmask must not activate."""
    d, ev, l = _hold(["ctrl", "shift"], monkeypatch)
    d.modifier(0, _CTRL | _SHIFT | _CMD)
    d.modifier(0, 0)
    assert ev == []
    _assert_released(l)


def test_hold_with_char_key_extra_blocks_activation(monkeypatch):
    """HOLD-mode combo containing a character key (cmd+p, on_activate): the P
    keyDown completes the set while shift (extra) is down -> no activation."""
    d, ev, l = _hold(["cmd", "p"], monkeypatch)
    d.modifier(_CMD_L, _CMD)
    d.modifier(_SHIFT_L, _CMD | _SHIFT)      # extra
    d.key_down(_P, _CMD | _SHIFT)            # completes under extra
    assert ev == []
    d.key_up(_P, _CMD | _SHIFT)
    d.modifier(0, 0)
    assert ev == []
    _assert_released(l)


def test_hold_with_char_key_extra_mid_hold_deactivates_once(monkeypatch):
    """HOLD cmd+p active; shift joins mid-hold -> deactivate immediately, and
    the later P keyUp / full release adds no second 'off'."""
    d, ev, l = _hold(["cmd", "p"], monkeypatch)
    d.modifier(_CMD_L, _CMD)
    d.key_down(_P, _CMD)
    assert ev == ["on"]
    d.modifier(_SHIFT_L, _CMD | _SHIFT)      # extra joins mid-hold
    assert ev == ["on", "off"]
    d.key_up(_P, _CMD | _SHIFT)
    d.modifier(0, 0)
    assert ev == ["on", "off"]
    _assert_released(l)


# ===========================================================================
# CATEGORY: tap-extras — extra modifiers vs modifier-only tap combos
# ===========================================================================


def test_tap_keycode0_completes_under_extra_no_fire(monkeypatch):
    """One keycode-0 flagsChanged shows cmd+ctrl AND alt at once: the hold
    completed under an extra -> contaminated, no fire on release."""
    d, fired, l = _tap(["cmd", "ctrl"], monkeypatch)
    d.modifier(0, _CMD | _CTRL | _ALT)
    d.modifier(0, 0)
    assert fired == []
    _assert_released(l)


def test_tap_target_drop_and_extra_add_same_event_no_fire(monkeypatch):
    """Active cmd+ctrl; ONE event drops ctrl AND adds alt (a dropped alt-down
    flagsChanged coalesced into ctrl's release). Physically alt joined during
    the hold -> contaminated -> must NOT fire."""
    d, fired, l = _tap(["cmd", "ctrl"], monkeypatch)
    d.modifier(_CMD_L, _CMD)
    d.modifier(_CTRL_L, _CMD | _CTRL)
    d.modifier(_ALT_L, _CMD | _ALT)   # ctrl bit gone + alt bit new, same event
    assert fired == []
    d.modifier(0, 0)
    assert fired == []
    _assert_released(l)


def test_tap_full_release_to_extra_only_in_one_event_no_fire(monkeypatch):
    """Active cmd+ctrl; ONE event goes straight to alt-only flags (both targets
    released AND an extra pressed, coalesced). The extra was down before the
    tap observed the release -> contaminated -> must NOT fire."""
    d, fired, l = _tap(["cmd", "ctrl"], monkeypatch)
    d.modifier(_CMD_L, _CMD)
    d.modifier(_CTRL_L, _CMD | _CTRL)
    d.modifier(_ALT_L, _ALT)          # cmd+ctrl -> alt in a single event
    assert fired == []
    d.modifier(0, 0)
    assert fired == []
    _assert_released(l)


def test_tap_extra_swaps_for_another_extra_stays_contaminated(monkeypatch):
    """alt contaminates the hold; one event swaps alt for shift (still an
    extra). Contamination persists through the swap and the release."""
    d, fired, l = _tap(["cmd", "ctrl"], monkeypatch)
    d.modifier(_CMD_L, _CMD)
    d.modifier(_CTRL_L, _CMD | _CTRL)
    d.modifier(_ALT_L, _CMD | _CTRL | _ALT)     # contaminated
    d.modifier(_ALT_L, _CMD | _CTRL | _SHIFT)   # alt->shift swap, one event
    d.modifier(_SHIFT_L, _CMD | _CTRL)          # extras gone; hold stays dirty
    d.modifier(0, 0)
    assert fired == []
    _assert_released(l)


def test_tap_extra_contamination_reset_by_target_bounce(monkeypatch):
    """Contaminated by an extra; extra leaves; a target bounced with no extras
    is a fresh full hold -> clean release fires exactly once."""
    d, fired, l = _tap(["cmd", "ctrl"], monkeypatch)
    d.modifier(_CMD_L, _CMD)
    d.modifier(_CTRL_L, _CMD | _CTRL)
    d.modifier(_ALT_L, _CMD | _CTRL | _ALT)  # contaminated
    d.modifier(_ALT_L, _CMD | _CTRL)         # extra gone
    d.modifier(_CTRL_L, _CMD)                # bounce ctrl (hold ends, no fire)
    assert fired == []
    d.modifier(_CTRL_L, _CMD | _CTRL)        # fresh full hold, no extras
    d.modifier(0, 0)
    assert fired == [1]
    _assert_released(l)


def test_tap_repress_under_persistent_extra_stays_contaminated(monkeypatch):
    """alt held throughout: cmd+ctrl pressed, released, pressed again — every
    hold completes under the extra, so none fires; a clean hold after alt
    finally leaves fires once."""
    d, fired, l = _tap(["cmd", "ctrl"], monkeypatch)
    d.modifier(_ALT_L, _ALT)
    d.modifier(_CMD_L, _ALT | _CMD)
    d.modifier(_CTRL_L, _ALT | _CMD | _CTRL)
    d.modifier(_CMD_L, _ALT)                     # release targets, alt stays
    d.modifier(_CMD_L, _ALT | _CMD)
    d.modifier(_CTRL_L, _ALT | _CMD | _CTRL)     # complete again under alt
    d.modifier(0, 0)
    assert fired == []
    d.modifier(_CMD_L, _CMD)                     # clean hold
    d.modifier(_CTRL_L, _CMD | _CTRL)
    d.modifier(0, 0)
    assert fired == [1]
    _assert_released(l)


def test_tap_extra_tapped_twice_before_completion_still_clean(monkeypatch):
    """An extra pressed and fully released TWICE while the combo is incomplete
    must not poison the eventual clean hold."""
    d, fired, l = _tap(["cmd", "ctrl"], monkeypatch)
    d.modifier(_CMD_L, _CMD)
    d.modifier(_ALT_L, _CMD | _ALT)
    d.modifier(_ALT_L, _CMD)
    d.modifier(_ALT_L, _CMD | _ALT)
    d.modifier(_ALT_L, _CMD)
    d.modifier(_CTRL_L, _CMD | _CTRL)   # completes cleanly
    d.modifier(0, 0)
    assert fired == [1]
    _assert_released(l)


def test_tap_contaminated_hold_release_invariants(monkeypatch):
    """After a contaminated hold fully releases via flags 0, nothing may stay
    held and wait_all_released must return promptly."""
    d, fired, l = _tap(["cmd", "ctrl"], monkeypatch)
    d.modifier(_CMD_L, _CMD)
    d.modifier(_CTRL_L, _CMD | _CTRL)
    d.modifier(_SHIFT_L, _CMD | _CTRL | _SHIFT)
    d.key_down(_FOUR, _CMD | _CTRL | _SHIFT)   # stray key on top
    d.modifier(0, 0)
    assert fired == []
    _assert_released(l)


# ===========================================================================
# CATEGORY: multi-listener — several combos fed the same stream
# ===========================================================================


def _three_listeners(monkeypatch):
    dictate, repaste, correct = [], [], []
    l1 = HotkeyListener(
        keys=["ctrl", "shift"],
        on_activate=lambda: dictate.append("on"),
        on_deactivate=lambda: dictate.append("off"),
    )
    l2 = HotkeyListener(keys=["cmd", "ctrl"], on_trigger=lambda: repaste.append(1))
    l3 = HotkeyListener(keys=["cmd", "alt"], on_trigger=lambda: correct.append(1))
    d = _Driver([l1, l2, l3], monkeypatch)
    return d, dictate, repaste, correct, (l1, l2, l3)


def test_multi_screenshot_chord_silences_all_listeners(monkeypatch):
    """⌘⌃⇧4 across dictate=ctrl+shift(hold), repaste=cmd+ctrl(tap),
    correct=cmd+alt(tap): nothing may fire."""
    d, dictate, repaste, correct, ls = _three_listeners(monkeypatch)
    d.modifier(_CMD_L, _CMD)
    d.modifier(_CTRL_L, _CMD | _CTRL)
    d.modifier(_SHIFT_L, _CMD | _CTRL | _SHIFT)
    d.key_down(_FOUR, _CMD | _CTRL | _SHIFT)
    d.key_up(_FOUR, _CMD | _CTRL | _SHIFT)
    d.modifier(0, 0)
    assert dictate == []
    assert repaste == []
    assert correct == []
    for l in ls:
        _assert_released(l)


def test_multi_dictation_leaves_tap_listeners_silent(monkeypatch):
    """Plain ctrl+shift dictation: dictate on/off fires; neither tap combo
    (each seeing ctrl or shift as extra/partial) may fire."""
    d, dictate, repaste, correct, ls = _three_listeners(monkeypatch)
    d.modifier(_CTRL_L, _CTRL)
    d.modifier(_SHIFT_L, _CTRL | _SHIFT)
    d.modifier(_SHIFT_L, _CTRL)
    d.modifier(_CTRL_L, 0)
    assert dictate == ["on", "off"]
    assert repaste == []
    assert correct == []
    for l in ls:
        _assert_released(l)


def test_multi_roll_with_dropped_event_must_not_fire_both(monkeypatch):
    """cmd+ctrl held, then ONE event shows cmd+alt (ctrl-up and alt-down
    coalesced / alt's flagsChanged dropped). Whatever the physical order was,
    firing BOTH repaste (cmd+ctrl) and correct (cmd+alt) from one gesture is
    the exact issue-#21 double-fire and must not happen."""
    d, dictate, repaste, correct, ls = _three_listeners(monkeypatch)
    d.modifier(_CMD_L, _CMD)
    d.modifier(_CTRL_L, _CMD | _CTRL)
    d.modifier(_ALT_L, _CMD | _ALT)   # coalesced ctrl-up + alt-down
    d.modifier(0, 0)
    assert not (repaste == [1] and correct == [1]), (
        f"double fire: repaste={repaste} correct={correct}"
    )
    for l in ls:
        _assert_released(l)


def test_multi_exact_combos_after_shared_contamination_recover(monkeypatch):
    """After a ⌘⌃⌥ chord (contaminates both taps), each combo pressed alone
    fires exactly its own listener."""
    d, dictate, repaste, correct, ls = _three_listeners(monkeypatch)
    d.modifier(_CMD_L, _CMD)
    d.modifier(_CTRL_L, _CMD | _CTRL)
    d.modifier(_ALT_L, _CMD | _CTRL | _ALT)
    d.modifier(0, 0)
    assert repaste == [] and correct == []
    d.modifier(_CMD_L, _CMD)
    d.modifier(_CTRL_L, _CMD | _CTRL)
    d.modifier(0, 0)
    assert repaste == [1] and correct == []
    d.modifier(_CMD_L, _CMD)
    d.modifier(_ALT_L, _CMD | _ALT)
    d.modifier(0, 0)
    assert repaste == [1] and correct == [1]
    assert dictate == []
    for l in ls:
        _assert_released(l)


# ===========================================================================
# CATEGORY: keydown-fire — char chords under extra modifiers
# ===========================================================================


def test_keydown_extra_tapped_before_char_then_exact_fires(monkeypatch):
    """cmd+ctrl held, shift taps (down+up) BEFORE P arrives: the P keyDown then
    carries exactly cmd+ctrl and must fire once."""
    d, fired, l = _tap(["cmd", "ctrl", "p"], monkeypatch)
    d.modifier(_CMD_L, _CMD)
    d.modifier(_CTRL_L, _CMD | _CTRL)
    d.modifier(_SHIFT_L, _CMD | _CTRL | _SHIFT)
    d.modifier(_SHIFT_L, _CMD | _CTRL)
    d.key_down(_P, _CMD | _CTRL)
    assert fired == [1]


def test_keydown_fires_once_per_hold_despite_extra_bounce(monkeypatch):
    """Fire on P; shift bounces (targets never all released -> no re-arm);
    autorepeat P with exact modifiers again must NOT re-fire this hold."""
    d, fired, l = _tap(["cmd", "ctrl", "p"], monkeypatch)
    d.modifier(_CMD_L, _CMD)
    d.modifier(_CTRL_L, _CMD | _CTRL)
    d.key_down(_P, _CMD | _CTRL)
    assert fired == [1]
    d.modifier(_SHIFT_L, _CMD | _CTRL | _SHIFT)   # extra joins (superset: no re-arm)
    d.key_down(_P, _CMD | _CTRL | _SHIFT)         # autorepeat under extra
    d.modifier(_SHIFT_L, _CMD | _CTRL)            # extra leaves (still subset held)
    d.key_down(_P, _CMD | _CTRL)                  # autorepeat, exact mods again
    assert fired == [1]


def test_keydown_blocked_under_extra_fires_on_autorepeat_after_extra_drops(monkeypatch):
    """keycode-0 flags show cmd+ctrl+shift; P under them does not fire; a
    keycode-0 event dropping shift leaves exactly cmd+ctrl, and the next P
    autorepeat fires once (the hold never fired yet)."""
    d, fired, l = _tap(["cmd", "ctrl", "p"], monkeypatch)
    d.modifier(0, _CMD | _CTRL | _SHIFT)
    d.key_down(_P, _CMD | _CTRL | _SHIFT)
    assert fired == []
    d.modifier(0, _CMD | _CTRL)          # extra drops via keycode-0 event
    d.key_down(_P, _CMD | _CTRL)         # autorepeat, exact
    assert fired == [1]
    d.key_down(_P, _CMD | _CTRL)         # further autorepeat: once per hold
    assert fired == [1]


def test_keydown_coalesced_release_to_extra_only_rearms(monkeypatch):
    """After a fire, ONE event replaces cmd+ctrl with shift-only flags (targets
    released + extra pressed together). Targets no longer all held -> re-arm;
    the next full exact chord fires again."""
    d, fired, l = _tap(["cmd", "ctrl", "p"], monkeypatch)
    d.modifier(_CMD_L, _CMD)
    d.modifier(_CTRL_L, _CMD | _CTRL)
    d.key_down(_P, _CMD | _CTRL)
    assert fired == [1]
    d.modifier(_SHIFT_L, _SHIFT)         # cmd+ctrl up, shift down, one event
    d.modifier(_SHIFT_L, 0)
    d.modifier(_CMD_L, _CMD)
    d.modifier(_CTRL_L, _CMD | _CTRL)
    d.key_down(_P, _CMD | _CTRL)
    assert fired == [1, 1]
    d.modifier(0, 0)
    _assert_released(l)


def test_keydown_char_first_then_modifiers_completed_fires_on_repeat(monkeypatch):
    """P pressed under only cmd (no fire), then ctrl joins; the next P keyDown
    (autorepeat) carries exactly cmd+ctrl -> spec-literal: it fires once."""
    d, fired, l = _tap(["cmd", "ctrl", "p"], monkeypatch)
    d.modifier(_CMD_L, _CMD)
    d.key_down(_P, _CMD)                 # mods not equal -> no fire
    assert fired == []
    d.modifier(_CTRL_L, _CMD | _CTRL)    # completes the modifier set
    d.key_down(_P, _CMD | _CTRL)         # autorepeat with exact mods
    assert fired == [1]


def test_keydown_right_variant_mods_with_extra_blocked(monkeypatch):
    """Right-side cmd variant + ctrl + alt(extra): P must not fire; after alt
    drops, P autorepeat fires."""
    d, fired, l = _tap(["cmd", "ctrl", "p"], monkeypatch)
    d.modifier(_CMD_R, _CMD)
    d.modifier(_CTRL_L, _CMD | _CTRL)
    d.modifier(_ALT_L, _CMD | _CTRL | _ALT)
    d.key_down(_P, _CMD | _CTRL | _ALT)
    assert fired == []
    d.modifier(_ALT_L, _CMD | _CTRL)
    d.key_down(_P, _CMD | _CTRL)
    assert fired == [1]


# ===========================================================================
# CATEGORY: exception-safety — callback exceptions must not corrupt state
# ===========================================================================


def _boom(record):
    def cb():
        record.append(1)
        raise RuntimeError("boom")
    return cb


def test_tap_on_trigger_raises_coalesced_release_state_intact(monkeypatch):
    """on_trigger raises. Both targets release in ONE flags-0 event. The
    exception must not strand the second target in _held (which would wedge
    wait_all_released until an unrelated future event)."""
    fired = []
    l = HotkeyListener(keys=["cmd", "ctrl"], on_trigger=_boom(fired))
    d = _Driver([l], monkeypatch)
    d.modifier(_CMD_L, _CMD)
    d.modifier(_CTRL_L, _CMD | _CTRL)
    d.modifier(0, 0)                     # coalesced full release
    assert fired == [1]
    _assert_released(l)                  # INVARIANT: nothing may stay held


def test_hold_on_deactivate_raises_coalesced_release_state_intact(monkeypatch):
    """on_deactivate raises. Both targets release in ONE flags-0 event. The
    remaining target must still be released from _held."""
    offs = []
    l = HotkeyListener(
        keys=["ctrl", "shift"],
        on_activate=lambda: None,
        on_deactivate=_boom(offs),
    )
    d = _Driver([l], monkeypatch)
    d.modifier(_CTRL_L, _CTRL)
    d.modifier(_SHIFT_L, _CTRL | _SHIFT)
    d.modifier(0, 0)
    assert offs == [1]
    _assert_released(l)


def test_tap_on_trigger_raises_sequential_release_recovers(monkeypatch):
    """on_trigger raises but releases arrive as separate events: state
    self-heals and the NEXT clean hold fires again."""
    fired = []
    l = HotkeyListener(keys=["cmd", "ctrl"], on_trigger=_boom(fired))
    d = _Driver([l], monkeypatch)
    d.modifier(_CMD_L, _CMD)
    d.modifier(_CTRL_L, _CMD | _CTRL)
    d.modifier(_CMD_L, _CTRL)            # cmd up -> trigger fires + raises
    d.modifier(_CTRL_L, 0)               # ctrl up in its own event
    assert fired == [1]
    _assert_released(l)
    d.modifier(_CMD_L, _CMD)
    d.modifier(_CTRL_L, _CMD | _CTRL)
    d.modifier(_CMD_L, _CTRL)
    d.modifier(_CTRL_L, 0)
    assert fired == [1, 1]
    _assert_released(l)


def test_hold_on_activate_raises_alternation_survives(monkeypatch):
    """on_activate raises; the deactivate must still fire on release and
    alternation must hold across a second cycle."""
    ev = []

    def on():
        ev.append("on")
        raise RuntimeError("boom")

    l = HotkeyListener(
        keys=["ctrl", "shift"],
        on_activate=on,
        on_deactivate=lambda: ev.append("off"),
    )
    d = _Driver([l], monkeypatch)
    d.modifier(_CTRL_L, _CTRL)
    d.modifier(_SHIFT_L, _CTRL | _SHIFT)
    d.modifier(_SHIFT_L, _CTRL)
    d.modifier(_CTRL_L, 0)
    assert ev == ["on", "off"]
    _assert_released(l)
    d.modifier(0, _CTRL | _SHIFT)
    d.modifier(0, 0)
    assert ev == ["on", "off", "on", "off"]
    _assert_released(l)


# ===========================================================================
# CATEGORY: lifecycle — stop() mid-hold with extras
# ===========================================================================


def _stub_quartz_teardown(monkeypatch):
    monkeypatch.setattr(hk.Quartz, "CGEventTapEnable", lambda *a: None)
    monkeypatch.setattr(hk.Quartz, "CFMachPortInvalidate", lambda *a: None)
    monkeypatch.setattr(hk.Quartz, "CFRunLoopRemoveSource", lambda *a: None)
    monkeypatch.setattr(hk.Quartz, "CFRunLoopGetMain", lambda: None)


def test_stop_mid_contaminated_hold_clears_state(monkeypatch):
    """stop() while cmd+ctrl+alt(extra) are held: _held cleared, waiters
    unblock, and after a 'restart' a clean hold fires (no stale
    contamination or extra-modifier flag)."""
    d, fired, l = _tap(["cmd", "ctrl"], monkeypatch)
    _stub_quartz_teardown(monkeypatch)
    d.modifier(_CMD_L, _CMD)
    d.modifier(_CTRL_L, _CMD | _CTRL)
    d.modifier(_ALT_L, _CMD | _CTRL | _ALT)   # contaminated, extra down
    l._tap = object()                          # simulate a started tap
    l._source = None
    l.stop()
    _assert_released(l)
    assert l._active is False
    # "Restarted": user performs a fresh clean tap.
    d.modifier(_CMD_L, _CMD)
    d.modifier(_CTRL_L, _CMD | _CTRL)
    d.modifier(0, 0)
    assert fired == [1]
    _assert_released(l)


def test_stop_mid_hold_mode_with_extra_clears_state(monkeypatch):
    """HOLD mode: extra already deactivated the combo; stop() mid-hold must
    leave no held keys and a post-restart fresh hold works."""
    d, ev, l = _hold(["ctrl", "shift"], monkeypatch)
    _stub_quartz_teardown(monkeypatch)
    d.modifier(_CTRL_L, _CTRL)
    d.modifier(_SHIFT_L, _CTRL | _SHIFT)
    d.modifier(_CMD_L, _CTRL | _SHIFT | _CMD)  # extra -> off
    assert ev == ["on", "off"]
    l._tap = object()
    l._source = None
    l.stop()
    _assert_released(l)
    d.modifier(_CTRL_L, _CTRL)
    d.modifier(_SHIFT_L, _CTRL | _SHIFT)
    d.modifier(0, 0)
    assert ev == ["on", "off", "on", "off"]
    _assert_released(l)
