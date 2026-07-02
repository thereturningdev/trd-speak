"""Adversarial unit tests for HotkeyListener firing logic.

Drives the real `_tap_callback` path by monkeypatching the two Quartz
accessors (keycode + absolute flags), exactly like `_Driver` in
tests/test_hotkey.py. These tests assert what the firing state machine
SHOULD do under coalesced / dropped / keycode-0 / out-of-order / contaminated
event streams, especially for Command combos (the just-fixed bug class).

Categories cover: cmd+alt (modifier-only tap), cmd+ctrl+p (keydown-fire),
cmd+shift (hold).
"""

import flow.hotkey as hk
from flow.hotkey import HotkeyListener

# --- virtual keycodes -------------------------------------------------------
_CMD_L = 55   # left command
_CMD_R = 54   # right command
_CTRL_L = 59  # left control
_CTRL_R = 62  # right control
_SHIFT_L = 56
_SHIFT_R = 60
_ALT_L = 58
_ALT_R = 61
_P = 35
_FOUR = 21
_X = 7        # a foreign character key

# --- flag masks -------------------------------------------------------------
_CMD = hk.Quartz.kCGEventFlagMaskCommand
_CTRL = hk.Quartz.kCGEventFlagMaskControl
_SHIFT = hk.Quartz.kCGEventFlagMaskShift
_ALT = hk.Quartz.kCGEventFlagMaskAlternate


class _Driver:
    """Feeds synthesized key/modifier events into a listener's tap callback."""

    def __init__(self, listener, monkeypatch):
        self._listener = listener
        self._keycode = 0
        self._flags = 0
        monkeypatch.setattr(
            hk.Quartz, "CGEventGetIntegerValueField", lambda e, f: self._keycode
        )
        monkeypatch.setattr(hk.Quartz, "CGEventGetFlags", lambda e: self._flags)

    def modifier(self, keycode, flags):
        self._keycode = keycode
        self._flags = flags
        self._listener._tap_callback(
            None, hk.Quartz.kCGEventFlagsChanged, object(), None
        )

    def key_down(self, keycode, flags=None):
        self._keycode = keycode
        if flags is not None:
            self._flags = flags
        self._listener._tap_callback(None, hk.Quartz.kCGEventKeyDown, object(), None)

    def key_up(self, keycode, flags=None):
        self._keycode = keycode
        if flags is not None:
            self._flags = flags
        self._listener._tap_callback(None, hk.Quartz.kCGEventKeyUp, object(), None)


def _tap(keys, monkeypatch):
    fired = []
    listener = HotkeyListener(keys=keys, on_trigger=lambda: fired.append(1))
    return _Driver(listener, monkeypatch), fired


def _hold(keys, monkeypatch):
    events = []
    listener = HotkeyListener(
        keys=keys,
        on_activate=lambda: events.append("on"),
        on_deactivate=lambda: events.append("off"),
    )
    return _Driver(listener, monkeypatch), events


# ===========================================================================
# CATEGORY: cmd+alt modifier-only TAP
# ===========================================================================

def test_cmdalt_clean_release_fires_once(monkeypatch):
    d, fired = _tap(["cmd", "alt"], monkeypatch)
    d.modifier(_CMD_L, _CMD)
    d.modifier(_ALT_L, _CMD | _ALT)
    d.modifier(_ALT_L, _CMD)   # alt up
    d.modifier(_CMD_L, 0)      # cmd up
    assert fired == [1]


def test_cmdalt_coalesced_press_then_clean_release(monkeypatch):
    d, fired = _tap(["cmd", "alt"], monkeypatch)
    d.modifier(_CMD_L, _CMD | _ALT)  # both bits in one event
    d.modifier(_CMD_L, 0)            # both clear at once
    assert fired == [1]


def test_cmdalt_dropped_press_self_heals(monkeypatch):
    """cmd's flagsChanged is dropped; alt's event already shows both bits."""
    d, fired = _tap(["cmd", "alt"], monkeypatch)
    d.modifier(_ALT_L, _CMD | _ALT)  # only one event, both bits down
    d.modifier(_ALT_L, 0)
    assert fired == [1]


def test_cmdalt_keycode_zero_flagschanged(monkeypatch):
    d, fired = _tap(["cmd", "alt"], monkeypatch)
    d.modifier(0, _CMD | _ALT)
    d.modifier(0, 0)
    assert fired == [1]


def test_cmdalt_contaminated_then_clean_retry(monkeypatch):
    d, fired = _tap(["cmd", "alt"], monkeypatch)
    # contaminated hold
    d.modifier(_CMD_L, _CMD)
    d.modifier(_ALT_L, _CMD | _ALT)
    d.key_down(_X)
    d.key_up(_X)
    d.modifier(_ALT_L, _CMD)
    d.modifier(_CMD_L, 0)
    assert fired == []
    # clean retry
    d.modifier(_CMD_L, _CMD)
    d.modifier(_ALT_L, _CMD | _ALT)
    d.modifier(_ALT_L, _CMD)
    d.modifier(_CMD_L, 0)
    assert fired == [1]


def test_cmdalt_out_of_order_release_then_press_events(monkeypatch):
    """A stale 'release' flagsChanged arrives showing only cmd before alt's
    real press event; absolute-flag reconciliation must still converge."""
    d, fired = _tap(["cmd", "alt"], monkeypatch)
    d.modifier(_CMD_L, _CMD)
    d.modifier(_ALT_L, _CMD)          # out-of-order: alt keycode but bit not yet set
    d.modifier(_ALT_L, _CMD | _ALT)   # corrected: both bits
    d.modifier(_CMD_L, 0)
    assert fired == [1]


def test_cmdalt_left_right_variant_mix(monkeypatch):
    """Press left cmd + right alt; flags identical to left/left. Fires."""
    d, fired = _tap(["cmd", "alt"], monkeypatch)
    d.modifier(_CMD_L, _CMD)
    d.modifier(_ALT_R, _CMD | _ALT)
    d.modifier(_ALT_R, _CMD)
    d.modifier(_CMD_L, 0)
    assert fired == [1]


def test_cmdalt_no_phantom_fire_on_single_modifier(monkeypatch):
    """Pressing/releasing only cmd (never alt) must not fire the cmd+alt tap."""
    d, fired = _tap(["cmd", "alt"], monkeypatch)
    d.modifier(_CMD_L, _CMD)
    d.modifier(_CMD_L, 0)
    assert fired == []


def test_cmdalt_rapid_retrigger_fires_each_time(monkeypatch):
    d, fired = _tap(["cmd", "alt"], monkeypatch)
    for _ in range(3):
        d.modifier(_CMD_L, _CMD)
        d.modifier(_ALT_L, _CMD | _ALT)
        d.modifier(_ALT_L, _CMD)
        d.modifier(_CMD_L, 0)
    assert fired == [1, 1, 1]


def test_cmdalt_foreign_modifier_contaminates(monkeypatch):
    """Holding cmd+alt and additionally tapping shift (an extra modifier)
    contaminates the hold (issue #21) — the user rolled into a bigger chord,
    so releasing must NOT fire even though shift left before the release."""
    d, fired = _tap(["cmd", "alt"], monkeypatch)
    d.modifier(_CMD_L, _CMD)
    d.modifier(_ALT_L, _CMD | _ALT)
    d.modifier(_SHIFT_L, _CMD | _ALT | _SHIFT)  # extra modifier
    d.modifier(_SHIFT_L, _CMD | _ALT)
    d.modifier(_ALT_L, _CMD)
    d.modifier(_CMD_L, 0)
    assert fired == []


# ===========================================================================
# CATEGORY: cmd+ctrl+p keydown-FIRE
# ===========================================================================

def test_cmdctrlp_fires_on_p_keydown(monkeypatch):
    d, fired = _tap(["cmd", "ctrl", "p"], monkeypatch)
    d.modifier(_CMD_L, _CMD)
    d.modifier(_CTRL_L, _CMD | _CTRL)
    d.key_down(_P, _CMD | _CTRL)
    assert fired == [1]


def test_cmdctrlp_coalesced_modifier_press_then_p(monkeypatch):
    d, fired = _tap(["cmd", "ctrl", "p"], monkeypatch)
    d.modifier(_CMD_L, _CMD | _CTRL)  # both modifiers in one event
    d.key_down(_P, _CMD | _CTRL)
    assert fired == [1]


def test_cmdctrlp_no_fire_if_p_lacks_modifiers_in_event_flags(monkeypatch):
    """Even if listener tracked modifiers, the keyDown gates on THIS event's
    absolute flags. If P's event shows no modifiers, it must not fire."""
    d, fired = _tap(["cmd", "ctrl", "p"], monkeypatch)
    d.modifier(_CMD_L, _CMD)
    d.modifier(_CTRL_L, _CMD | _CTRL)
    d.key_down(_P, 0)  # P's event flags lost the modifiers
    assert fired == []


def test_cmdctrlp_autorepeat_single_fire(monkeypatch):
    d, fired = _tap(["cmd", "ctrl", "p"], monkeypatch)
    d.modifier(_CMD_L, _CMD)
    d.modifier(_CTRL_L, _CMD | _CTRL)
    d.key_down(_P, _CMD | _CTRL)
    d.key_down(_P, _CMD | _CTRL)
    d.key_down(_P, _CMD | _CTRL)
    assert fired == [1]


def test_cmdctrlp_rearm_after_modifier_release(monkeypatch):
    d, fired = _tap(["cmd", "ctrl", "p"], monkeypatch)
    d.modifier(_CMD_L, _CMD)
    d.modifier(_CTRL_L, _CMD | _CTRL)
    d.key_down(_P, _CMD | _CTRL)
    assert fired == [1]
    d.modifier(_CMD_L, _CTRL)  # cmd up
    d.modifier(_CTRL_L, 0)     # ctrl up -> re-arm (P keyUp suppressed)
    d.modifier(_CMD_L, _CMD)
    d.modifier(_CTRL_L, _CMD | _CTRL)
    d.key_down(_P, _CMD | _CTRL)
    assert fired == [1, 1]


def test_cmdctrlp_no_phantom_on_bare_modifier_subset(monkeypatch):
    """After a suppressed-P tap, tapping only cmd+ctrl must not fire."""
    d, fired = _tap(["cmd", "ctrl", "p"], monkeypatch)
    d.modifier(_CMD_L, _CMD)
    d.modifier(_CTRL_L, _CMD | _CTRL)
    d.key_down(_P, _CMD | _CTRL)
    d.modifier(_CMD_L, _CTRL)
    d.modifier(_CTRL_L, 0)
    assert fired == [1]
    # bare modifier subset, no P
    d.modifier(_CMD_L, _CMD)
    d.modifier(_CTRL_L, _CMD | _CTRL)
    d.modifier(_CMD_L, _CTRL)
    d.modifier(_CTRL_L, 0)
    assert fired == [1]


def test_cmdctrlp_partial_modifier_release_does_not_rearm(monkeypatch):
    """If only cmd releases but ctrl stays, the chord is not yet re-armed, so a
    second P keyDown (with full flags impossible since cmd is gone) shouldn't
    double-fire on a still-held P. Here we verify: after fire, releasing ONLY
    ctrl (cmd still held) re-arms (modifiers no longer all present)."""
    d, fired = _tap(["cmd", "ctrl", "p"], monkeypatch)
    d.modifier(_CMD_L, _CMD)
    d.modifier(_CTRL_L, _CMD | _CTRL)
    d.key_down(_P, _CMD | _CTRL)
    assert fired == [1]
    # ctrl released, cmd still down -> not all target mods present -> re-armed.
    d.modifier(_CTRL_L, _CMD)
    # re-press ctrl and P -> should fire again.
    d.modifier(_CTRL_L, _CMD | _CTRL)
    d.key_down(_P, _CMD | _CTRL)
    assert fired == [1, 1]


def test_cmdctrlp_left_right_ctrl_variant(monkeypatch):
    """Modifiers held via right-ctrl variant; flag bit identical."""
    d, fired = _tap(["cmd", "ctrl", "p"], monkeypatch)
    d.modifier(_CMD_R, _CMD)
    d.modifier(_CTRL_R, _CMD | _CTRL)
    d.key_down(_P, _CMD | _CTRL)
    assert fired == [1]


def test_cmdctrlp_extra_foreign_modifier_in_flags_blocks_fire(monkeypatch):
    """P's event carries cmd+ctrl PLUS shift (user also holding shift). The
    gate is exact equality (issue #21): ⌘⌃⇧P is a different chord and must
    NOT fire the ⌘⌃P listener."""
    d, fired = _tap(["cmd", "ctrl", "p"], monkeypatch)
    d.modifier(_CMD_L, _CMD)
    d.modifier(_CTRL_L, _CMD | _CTRL)
    d.modifier(_SHIFT_L, _CMD | _CTRL | _SHIFT)
    d.key_down(_P, _CMD | _CTRL | _SHIFT)
    assert fired == []


def test_cmdctrlp_dropped_ctrl_press_then_p(monkeypatch):
    """ctrl flagsChanged dropped entirely, but P's keyDown event flags show
    cmd+ctrl. keyDown gates on the event's own flags, so it must fire."""
    d, fired = _tap(["cmd", "ctrl", "p"], monkeypatch)
    d.modifier(_CMD_L, _CMD)
    # no ctrl event delivered
    d.key_down(_P, _CMD | _CTRL)
    assert fired == [1]


def test_cmdctrlp_p_keyup_delivered_rearms(monkeypatch):
    """If P's keyUp IS delivered (modifiers not Cmd-suppressed in this case),
    _char_key_up should re-arm so a second P fires."""
    d, fired = _tap(["cmd", "ctrl", "p"], monkeypatch)
    d.modifier(_CMD_L, _CMD)
    d.modifier(_CTRL_L, _CMD | _CTRL)
    d.key_down(_P, _CMD | _CTRL)
    d.key_up(_P, _CMD | _CTRL)   # keyUp delivered while mods still held
    d.key_down(_P, _CMD | _CTRL)
    assert fired == [1, 1]


# ===========================================================================
# CATEGORY: cmd+shift HOLD
# ===========================================================================

def test_cmdshift_hold_activate_deactivate(monkeypatch):
    d, events = _hold(["cmd", "shift"], monkeypatch)
    d.modifier(_CMD_L, _CMD)
    d.modifier(_SHIFT_L, _CMD | _SHIFT)
    d.modifier(_SHIFT_L, _CMD)
    d.modifier(_CMD_L, 0)
    assert events == ["on", "off"]


def test_cmdshift_hold_coalesced_press(monkeypatch):
    d, events = _hold(["cmd", "shift"], monkeypatch)
    d.modifier(_CMD_L, _CMD | _SHIFT)  # one event both
    assert events == ["on"]
    d.modifier(_CMD_L, 0)
    assert events == ["on", "off"]


def test_cmdshift_hold_coalesced_release_fires_off_once(monkeypatch):
    d, events = _hold(["cmd", "shift"], monkeypatch)
    d.modifier(_CMD_L, _CMD)
    d.modifier(_SHIFT_L, _CMD | _SHIFT)
    d.modifier(_CMD_L, 0)  # both released in one event
    assert events == ["on", "off"]


def test_cmdshift_hold_dropped_press_self_heals(monkeypatch):
    d, events = _hold(["cmd", "shift"], monkeypatch)
    d.modifier(_SHIFT_L, _CMD | _SHIFT)  # cmd event dropped, both bits shown
    assert events == ["on"]
    d.modifier(_SHIFT_L, 0)
    assert events == ["on", "off"]


def test_cmdshift_hold_keycode_zero(monkeypatch):
    d, events = _hold(["cmd", "shift"], monkeypatch)
    d.modifier(0, _CMD | _SHIFT)
    d.modifier(0, 0)
    assert events == ["on", "off"]


def test_cmdshift_hold_activate_once_on_repeated_full_flags(monkeypatch):
    """Repeated flagsChanged events that still show the full combo must not
    re-fire on_activate."""
    d, events = _hold(["cmd", "shift"], monkeypatch)
    d.modifier(_CMD_L, _CMD)
    d.modifier(_SHIFT_L, _CMD | _SHIFT)
    d.modifier(_SHIFT_L, _CMD | _SHIFT)  # redundant event, same flags
    d.modifier(_CMD_L, _CMD | _SHIFT)    # redundant
    assert events == ["on"]


def test_cmdshift_hold_two_shift_variants_release_one(monkeypatch):
    """Both shift variants down; releasing one keeps the shift bit set, so the
    hold must NOT deactivate until the bit clears."""
    d, events = _hold(["cmd", "shift"], monkeypatch)
    d.modifier(_CMD_L, _CMD)
    d.modifier(_SHIFT_L, _CMD | _SHIFT)   # left shift -> active
    # right shift also pressed; flags unchanged (bit already set)
    d.modifier(_SHIFT_R, _CMD | _SHIFT)
    assert events == ["on"]
    # release left shift; bit STILL set because right shift is held
    d.modifier(_SHIFT_L, _CMD | _SHIFT)
    assert events == ["on"]  # must not deactivate
    # release right shift; bit clears
    d.modifier(_SHIFT_R, _CMD)
    assert events == ["on", "off"]


def test_cmdshift_hold_no_phantom_on_single_modifier(monkeypatch):
    d, events = _hold(["cmd", "shift"], monkeypatch)
    d.modifier(_CMD_L, _CMD)
    d.modifier(_CMD_L, 0)
    assert events == []


def test_cmdshift_hold_rapid_retrigger(monkeypatch):
    d, events = _hold(["cmd", "shift"], monkeypatch)
    for _ in range(3):
        d.modifier(_CMD_L, _CMD)
        d.modifier(_SHIFT_L, _CMD | _SHIFT)
        d.modifier(_SHIFT_L, _CMD)
        d.modifier(_CMD_L, 0)
    assert events == ["on", "off", "on", "off", "on", "off"]


# ===========================================================================
# CATEGORY: cross-cutting / nasty state-machine corners
# ===========================================================================

def test_tap_modifier_only_left_right_both_held_release_one(monkeypatch):
    """cmd+alt tap: hold left+right cmd, release one cmd variant — the cmd bit
    stays set, so no clean release yet; only when both cmd up AND alt up does it
    fire exactly once."""
    d, fired = _tap(["cmd", "alt"], monkeypatch)
    d.modifier(_CMD_L, _CMD)
    d.modifier(_CMD_R, _CMD)         # second cmd variant; flags unchanged
    d.modifier(_ALT_L, _CMD | _ALT)  # combo fully held
    d.modifier(_CMD_L, _CMD | _ALT)  # release one cmd variant; cmd bit stays
    # nothing released the combo yet (cmd still up, alt up)
    d.modifier(_ALT_L, _CMD)         # alt up
    d.modifier(_CMD_R, 0)            # last cmd up -> clean release
    assert fired == [1]


def test_tap_contamination_survives_dropped_then_fires_clean(monkeypatch):
    """Contaminate, then drop the foreign keyUp, then clean re-hold fires."""
    d, fired = _tap(["cmd", "alt"], monkeypatch)
    d.modifier(_CMD_L, _CMD)
    d.modifier(_ALT_L, _CMD | _ALT)
    d.key_down(_FOUR)        # contaminate; its keyUp never arrives
    d.modifier(_ALT_L, _CMD)
    d.modifier(_CMD_L, 0)
    assert fired == []
    # clean hold
    d.modifier(_CMD_L, _CMD)
    d.modifier(_ALT_L, _CMD | _ALT)
    d.modifier(_ALT_L, _CMD)
    d.modifier(_CMD_L, 0)
    assert fired == [1]


def test_keydown_fire_combo_ignores_contaminating_key(monkeypatch):
    """A keydown-fire combo (cmd+ctrl+p) already fires on its own char key;
    an unrelated foreign key press before P must not block the fire."""
    d, fired = _tap(["cmd", "ctrl", "p"], monkeypatch)
    d.modifier(_CMD_L, _CMD)
    d.modifier(_CTRL_L, _CMD | _CTRL)
    d.key_down(_FOUR, _CMD | _CTRL)  # foreign key
    d.key_up(_FOUR, _CMD | _CTRL)
    d.key_down(_P, _CMD | _CTRL)
    assert fired == [1]


def test_tap_modifier_only_p_as_foreign_then_clean(monkeypatch):
    """For a cmd+alt modifier-only tap, P is just a foreign character key and
    contaminates; the next clean cmd+alt fires."""
    d, fired = _tap(["cmd", "alt"], monkeypatch)
    d.modifier(_CMD_L, _CMD)
    d.modifier(_ALT_L, _CMD | _ALT)
    d.key_down(_P)  # foreign char contaminates
    d.modifier(_ALT_L, _CMD)
    d.modifier(_CMD_L, 0)
    assert fired == []


def test_cmdctrlp_modifier_release_during_hold_then_repress_no_double(monkeypatch):
    """Hold cmd+ctrl, fire on P. Without releasing P, drop ctrl then re-add
    ctrl (P still 'down' in keys_down). A new P keyDown should fire because
    re-arm cleared keys_down when ctrl dropped. Exactly one extra fire."""
    d, fired = _tap(["cmd", "ctrl", "p"], monkeypatch)
    d.modifier(_CMD_L, _CMD)
    d.modifier(_CTRL_L, _CMD | _CTRL)
    d.key_down(_P, _CMD | _CTRL)
    assert fired == [1]
    d.modifier(_CTRL_L, _CMD)        # ctrl drops -> re-arm, keys_down cleared
    d.modifier(_CTRL_L, _CMD | _CTRL)  # ctrl back; no P event => no fire
    assert fired == [1]
    d.key_down(_P, _CMD | _CTRL)     # new P -> fire
    assert fired == [1, 1]


def test_tap_combo_alongside_foreign_held_modifier_does_not_fire(monkeypatch):
    """cmd+alt held while an unrelated shift is also down the whole time.
    Matching is exact (issue #21): the combo completed UNDER an extra
    modifier, so it starts contaminated and its release must NOT fire —
    this is exactly how ⌘⌃⌥ used to fire two tap combos at once."""
    d, fired = _tap(["cmd", "alt"], monkeypatch)
    d.modifier(_SHIFT_L, _SHIFT)               # foreign modifier down first
    d.modifier(_CMD_L, _SHIFT | _CMD)
    d.modifier(_ALT_L, _SHIFT | _CMD | _ALT)   # subset held under shift
    d.modifier(_ALT_L, _SHIFT | _CMD)
    d.modifier(_CMD_L, _SHIFT)                 # release of cmd+alt
    assert fired == []


def test_tap_contamination_cleared_by_midhold_modifier_bounce(monkeypatch):
    """If a contaminated hold has a target modifier released and re-pressed
    (re-establishing a full hold), the new hold starts clean and fires on its
    own clean release — contamination is strictly per full-hold."""
    d, fired = _tap(["cmd", "alt"], monkeypatch)
    d.modifier(_CMD_L, _CMD)
    d.modifier(_ALT_L, _CMD | _ALT)   # active
    d.key_down(_FOUR)                  # contaminate
    d.key_up(_FOUR)
    d.modifier(_ALT_L, _CMD)          # alt up -> hold ends, no fire
    assert fired == []
    d.modifier(_ALT_L, _CMD | _ALT)  # alt back -> fresh full hold, clean
    d.modifier(_ALT_L, _CMD)
    d.modifier(_CMD_L, 0)
    assert fired == [1]


def test_keydown_fire_blocked_by_foreign_modifier_until_it_drops(monkeypatch):
    """cmd+ctrl+p with shift ALSO held: the P keyDown must not fire (exact
    modifier match, issue #21). Once the foreign shift drops — leaving exactly
    cmd+ctrl — the next P keyDown (e.g. autorepeat) fires once."""
    d, fired = _tap(["cmd", "ctrl", "p"], monkeypatch)
    d.modifier(_SHIFT_L, _SHIFT)
    d.modifier(_CMD_L, _SHIFT | _CMD)
    d.modifier(_CTRL_L, _SHIFT | _CMD | _CTRL)
    d.key_down(_P, _SHIFT | _CMD | _CTRL)
    assert fired == []                  # bigger chord, must not fire
    d.modifier(_SHIFT_L, _CMD | _CTRL)  # foreign shift drops; cmd+ctrl remain
    d.key_down(_P, _CMD | _CTRL)        # now exactly cmd+ctrl+p -> fires
    assert fired == [1]
    d.key_down(_P, _CMD | _CTRL)        # autorepeat -> must NOT re-fire
    assert fired == [1]


def test_tap_disabled_event_midhold_does_not_lose_hold(monkeypatch):
    """A kCGEventTapDisabledByTimeout arriving mid-hold is handled (re-enable)
    without disturbing _held, so the subsequent clean release still fires."""
    d, fired = _tap(["cmd", "alt"], monkeypatch)
    d._listener._tap = object()  # so the re-enable branch has a tap to act on
    monkeypatch.setattr(hk.Quartz, "CGEventTapEnable", lambda *a: None)
    d.modifier(_CMD_L, _CMD)
    d.modifier(_ALT_L, _CMD | _ALT)
    d._listener._tap_callback(
        None, hk.Quartz.kCGEventTapDisabledByTimeout, object(), None
    )
    d.modifier(_ALT_L, _CMD)
    d.modifier(_CMD_L, 0)
    assert fired == [1]


def test_hold_does_not_fire_with_keydown_fire_path(monkeypatch):
    """A 3-key combo with a character key in HOLD mode (on_activate). The char
    key routes through _press (keydown_fire is False for hold mode). on_activate
    should fire only when ALL three keys (incl P) are physically down."""
    events = []
    listener = HotkeyListener(
        keys=["cmd", "ctrl", "p"],
        on_activate=lambda: events.append("on"),
        on_deactivate=lambda: events.append("off"),
    )
    d = _Driver(listener, monkeypatch)
    d.modifier(_CMD_L, _CMD)
    d.modifier(_CTRL_L, _CMD | _CTRL)
    assert events == []        # modifiers alone, P not down
    d.key_down(_P, _CMD | _CTRL)
    assert events == ["on"]    # all three held
    d.key_up(_P, _CMD | _CTRL)
    assert events == ["on", "off"]
