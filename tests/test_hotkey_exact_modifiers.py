"""Issue #21: subset combos must not false-fire — exact modifier matching.

Each HotkeyListener used to ignore modifiers outside its own combo, so a combo
fired whenever its keys were a SUBSET of what was physically held:

- ⌘⌃⇧4 (screenshot) started a dictation because dictate=ctrl+shift is a
  subset (hold mode never saw the extra cmd, and keyDown contamination only
  applied to tap mode).
- ⌘⌃⌥ fired BOTH the re-paste (cmd+ctrl) and correction (cmd+alt) tap combos.

The fix: matching considers the EXTRA modifiers held beyond the target set,
computed from the absolute flags.

- hold mode: activate only when no extra modifier is held; an extra modifier
  appearing mid-hold deactivates immediately.
- tap mode: an extra modifier present at (or appearing during) the full hold
  contaminates it, exactly like a stray character keyDown.
- keydown-fire chords: the character keyDown fires only when the held
  modifiers EQUAL the target modifiers (was: subset).

Drives the real _tap_callback path with synthetic events (the _Driver pattern
from tests/test_hotkey.py); no real event tap or Input Monitoring needed.
"""

import flow.hotkey as hk
from flow.hotkey import HotkeyListener

# Virtual keycodes.
_CMD_L = 55
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
    tap callbacks, mirroring what the real tap delivers (flagsChanged carries
    the cumulative modifier flags; keyDown/keyUp carry the keycode)."""

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
    return _Driver([l], monkeypatch), fired


def _hold(keys, monkeypatch):
    ev = []
    l = HotkeyListener(
        keys=keys,
        on_activate=lambda: ev.append("on"),
        on_deactivate=lambda: ev.append("off"),
    )
    return _Driver([l], monkeypatch), ev


# ===========================================================================
# Hold mode: extra modifiers block activation / force deactivation
# ===========================================================================


def test_hold_does_not_activate_when_extra_modifier_is_held(monkeypatch):
    """The ⌘⌃⇧4 screenshot bug: dictate=ctrl+shift must NOT activate while
    cmd is also physically held."""
    d, ev = _hold(["ctrl", "shift"], monkeypatch)
    d.modifier(_CMD_L, _CMD)                          # cmd down (extra)
    d.modifier(_CTRL_L, _CMD | _CTRL)                 # ctrl down
    d.modifier(_SHIFT_L, _CMD | _CTRL | _SHIFT)       # shift down -> subset held
    assert ev == []                                   # no dictation start
    d.key_down(_FOUR, _CMD | _CTRL | _SHIFT)          # the screenshot's "4"
    d.key_up(_FOUR, _CMD | _CTRL | _SHIFT)
    d.modifier(0, 0)                                  # everything released
    assert ev == []                                   # never activated


def test_hold_deactivates_when_extra_modifier_appears_mid_hold(monkeypatch):
    """Recording in progress on ctrl+shift; cmd joins -> stop immediately."""
    d, ev = _hold(["ctrl", "shift"], monkeypatch)
    d.modifier(_CTRL_L, _CTRL)
    d.modifier(_SHIFT_L, _CTRL | _SHIFT)              # exact combo -> activate
    assert ev == ["on"]
    d.modifier(_CMD_L, _CTRL | _SHIFT | _CMD)         # cmd appears mid-hold
    assert ev == ["on", "off"]                        # deactivated immediately
    d.modifier(0, 0)                                  # full release
    assert ev == ["on", "off"]                        # no second deactivate


def test_hold_does_not_reactivate_when_extra_modifier_leaves(monkeypatch):
    """cmd+ctrl+shift held (no activation); cmd released while ctrl+shift stay
    down. The hold must NOT activate late — a fresh full press is required.
    (Otherwise ⌘⌃⇧4 would still start recording the moment cmd is released.)"""
    d, ev = _hold(["ctrl", "shift"], monkeypatch)
    d.modifier(_CMD_L, _CMD)
    d.modifier(_CTRL_L, _CMD | _CTRL)
    d.modifier(_SHIFT_L, _CMD | _CTRL | _SHIFT)
    assert ev == []
    d.modifier(_CMD_L, _CTRL | _SHIFT)                # cmd up; targets still held
    assert ev == []                                   # no late activation
    d.modifier(0, 0)
    assert ev == []


def test_hold_extra_modifier_released_before_full_hold_does_not_poison(monkeypatch):
    """Extra modifier pressed AND released before the target set completes:
    the subsequent full hold starts clean and activates."""
    d, ev = _hold(["ctrl", "shift"], monkeypatch)
    d.modifier(_CTRL_L, _CTRL)
    d.modifier(_CMD_L, _CTRL | _CMD)                  # extra cmd, combo incomplete
    d.modifier(_CMD_L, _CTRL)                         # extra cmd gone
    d.modifier(_SHIFT_L, _CTRL | _SHIFT)              # combo completes cleanly
    assert ev == ["on"]
    d.modifier(0, 0)
    assert ev == ["on", "off"]


def test_hold_exact_combo_still_activates_and_deactivates(monkeypatch):
    """Regression guard: the plain exact hold keeps working."""
    d, ev = _hold(["ctrl", "shift"], monkeypatch)
    d.modifier(_CTRL_L, _CTRL)
    d.modifier(_SHIFT_L, _CTRL | _SHIFT)
    d.modifier(_SHIFT_L, _CTRL)
    d.modifier(_CTRL_L, 0)
    assert ev == ["on", "off"]


# ===========================================================================
# Tap mode (modifier-only): extra modifiers contaminate the hold
# ===========================================================================


def test_tap_no_trigger_when_extra_modifier_held_from_the_start(monkeypatch):
    """cmd+ctrl tap: alt pressed first, then cmd+ctrl — release all: no fire."""
    d, fired = _tap(["cmd", "ctrl"], monkeypatch)
    d.modifier(_ALT_L, _ALT)
    d.modifier(_CMD_L, _ALT | _CMD)
    d.modifier(_CTRL_L, _ALT | _CMD | _CTRL)
    d.modifier(0, 0)                                  # release everything
    assert fired == []


def test_tap_no_trigger_when_extra_modifier_joins_mid_hold(monkeypatch):
    """cmd+ctrl tap: press cmd+ctrl, then alt joins, release all: no fire."""
    d, fired = _tap(["cmd", "ctrl"], monkeypatch)
    d.modifier(_CMD_L, _CMD)
    d.modifier(_CTRL_L, _CMD | _CTRL)
    d.modifier(_ALT_L, _CMD | _CTRL | _ALT)           # extra modifier joins
    d.modifier(0, 0)
    assert fired == []


def test_tap_rolling_extra_modifier_leaves_hold_stays_contaminated(monkeypatch):
    """cmd↓ ctrl↓ alt↓ alt↑ then release: the contaminated hold does not
    un-contaminate when the extra modifier leaves — still no fire."""
    d, fired = _tap(["cmd", "ctrl"], monkeypatch)
    d.modifier(_CMD_L, _CMD)
    d.modifier(_CTRL_L, _CMD | _CTRL)
    d.modifier(_ALT_L, _CMD | _CTRL | _ALT)           # alt joins -> contaminated
    d.modifier(_ALT_L, _CMD | _CTRL)                  # alt leaves
    d.modifier(_CMD_L, _CTRL)                         # release
    d.modifier(_CTRL_L, 0)
    assert fired == []


def test_tap_exact_combo_still_fires(monkeypatch):
    """Regression guard: press cmd+ctrl, release -> trigger fires once."""
    d, fired = _tap(["cmd", "ctrl"], monkeypatch)
    d.modifier(_CMD_L, _CMD)
    d.modifier(_CTRL_L, _CMD | _CTRL)
    d.modifier(_CMD_L, _CTRL)
    d.modifier(_CTRL_L, 0)
    assert fired == [1]


def test_tap_extra_modifier_released_before_full_hold_does_not_poison(monkeypatch):
    """Extra alt pressed and released while the combo is still incomplete:
    the hold that completes afterwards is clean and fires."""
    d, fired = _tap(["cmd", "ctrl"], monkeypatch)
    d.modifier(_CMD_L, _CMD)
    d.modifier(_ALT_L, _CMD | _ALT)                   # extra while incomplete
    d.modifier(_ALT_L, _CMD)                          # extra gone
    d.modifier(_CTRL_L, _CMD | _CTRL)                 # combo completes cleanly
    d.modifier(_CMD_L, _CTRL)
    d.modifier(_CTRL_L, 0)
    assert fired == [1]


def test_tap_contaminated_hold_then_fresh_clean_hold_fires(monkeypatch):
    """Contamination is per-hold: after a contaminated hold fully releases,
    a fresh exact hold fires."""
    d, fired = _tap(["cmd", "ctrl"], monkeypatch)
    d.modifier(_CMD_L, _CMD)
    d.modifier(_CTRL_L, _CMD | _CTRL)
    d.modifier(_ALT_L, _CMD | _CTRL | _ALT)           # contaminated
    d.modifier(0, 0)
    assert fired == []
    d.modifier(_CMD_L, _CMD)
    d.modifier(_CTRL_L, _CMD | _CTRL)
    d.modifier(0, 0)
    assert fired == [1]


def test_one_chord_cannot_fire_two_tap_listeners(monkeypatch):
    """The ⌘⌃⌥ bug: with re-paste=cmd+ctrl and correct=cmd+alt listening on
    the same events, pressing and releasing cmd+ctrl+alt fires NEITHER."""
    repaste, correct = [], []
    l1 = HotkeyListener(keys=["cmd", "ctrl"], on_trigger=lambda: repaste.append(1))
    l2 = HotkeyListener(keys=["cmd", "alt"], on_trigger=lambda: correct.append(1))
    d = _Driver([l1, l2], monkeypatch)
    d.modifier(_CMD_L, _CMD)
    d.modifier(_CTRL_L, _CMD | _CTRL)
    d.modifier(_ALT_L, _CMD | _CTRL | _ALT)           # all three held
    d.modifier(0, 0)                                  # release all
    assert repaste == []
    assert correct == []
    # And each combo alone still fires only its own listener.
    d.modifier(_CMD_L, _CMD)
    d.modifier(_CTRL_L, _CMD | _CTRL)
    d.modifier(0, 0)
    assert repaste == [1] and correct == []
    d.modifier(_CMD_L, _CMD)
    d.modifier(_ALT_L, _CMD | _ALT)
    d.modifier(0, 0)
    assert repaste == [1] and correct == [1]


# ===========================================================================
# Keydown-fire chords: exact modifier equality
# ===========================================================================


def test_keydown_fire_requires_exact_modifiers(monkeypatch):
    """cmd+ctrl+p: P keyDown with cmd+ctrl+shift held must NOT fire."""
    d, fired = _tap(["cmd", "ctrl", "p"], monkeypatch)
    d.modifier(_CMD_L, _CMD)
    d.modifier(_CTRL_L, _CMD | _CTRL)
    d.modifier(_SHIFT_L, _CMD | _CTRL | _SHIFT)
    d.key_down(_P, _CMD | _CTRL | _SHIFT)
    assert fired == []


def test_keydown_fire_fires_with_exact_modifiers(monkeypatch):
    """cmd+ctrl+p: P keyDown with exactly cmd+ctrl held fires once."""
    d, fired = _tap(["cmd", "ctrl", "p"], monkeypatch)
    d.modifier(_CMD_L, _CMD)
    d.modifier(_CTRL_L, _CMD | _CTRL)
    d.key_down(_P, _CMD | _CTRL)
    assert fired == [1]
