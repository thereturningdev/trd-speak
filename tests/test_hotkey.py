"""Tests for the event-tap watchdog (ensure_enabled).

macOS disables a CGEventTap whose callback runs too long. The listener must
expose a way to detect that and re-assert the tap so a poll timer can recover
it. These tests stub the Quartz tap calls — no real tap / Input Monitoring
permission is needed.
"""

import pytest

import flow.hotkey as hk
from flow.hotkey import (
    HotkeyListener,
    modifier_tokens_from_flags,
    token_for_keycode,
    validate_combo,
)

# Virtual keycodes used by the driver below.
_CTRL = 59  # left control
_SHIFT = 56  # left shift
_CMD = 55  # left command
_FOUR = 21  # the "4" key (a screenshot shortcut's action key)
_P = 35  # the "p" key (a character key in a chord like Cmd+Ctrl+P)

_CTRL_MASK = hk.Quartz.kCGEventFlagMaskControl
_SHIFT_MASK = hk.Quartz.kCGEventFlagMaskShift
_CMD_MASK = hk.Quartz.kCGEventFlagMaskCommand


def _listener():
    return HotkeyListener(
        keys=["ctrl", "shift"], on_activate=lambda: None, on_deactivate=lambda: None
    )


class _Driver:
    """Feeds synthesized key/modifier events into a listener's tap callback.

    Modifiers reach the real tap as flagsChanged events carrying the changed
    keycode and the cumulative modifier flags; ordinary keys reach it as
    keyDown/keyUp. This mirrors that so tests exercise the real callback path.
    """

    def __init__(self, listener, monkeypatch):
        self._listener = listener
        self._keycode = 0
        self._flags = 0
        monkeypatch.setattr(
            hk.Quartz, "CGEventGetIntegerValueField", lambda e, f: self._keycode
        )
        monkeypatch.setattr(hk.Quartz, "CGEventGetFlags", lambda e: self._flags)

    def modifier(self, keycode, flags):
        """A modifier changed; `flags` is the cumulative mask now in effect."""
        self._keycode = keycode
        self._flags = flags
        self._listener._tap_callback(
            None, hk.Quartz.kCGEventFlagsChanged, object(), None
        )

    def key_down(self, keycode):
        self._keycode = keycode
        self._listener._tap_callback(None, hk.Quartz.kCGEventKeyDown, object(), None)

    def key_up(self, keycode):
        self._keycode = keycode
        self._listener._tap_callback(None, hk.Quartz.kCGEventKeyUp, object(), None)


def test_tap_mode_fires_on_clean_release(monkeypatch):
    """on_trigger fires once when the combo is held then released with no
    other key pressed in between."""
    fired = []
    listener = HotkeyListener(keys=["ctrl", "shift"], on_trigger=lambda: fired.append(1))
    d = _Driver(listener, monkeypatch)

    d.modifier(_CTRL, _CTRL_MASK)  # ctrl down
    d.modifier(_SHIFT, _CTRL_MASK | _SHIFT_MASK)  # shift down -> combo held
    assert fired == []  # nothing on press
    d.modifier(_SHIFT, _CTRL_MASK)  # shift up -> clean release
    d.modifier(_CTRL, 0)  # ctrl up

    assert fired == [1]


def test_tap_mode_suppressed_by_contaminating_key(monkeypatch):
    """A non-combo key pressed during the hold (the Cmd+Ctrl+Shift+4 screenshot
    case) cancels the trigger."""
    fired = []
    listener = HotkeyListener(keys=["ctrl", "shift"], on_trigger=lambda: fired.append(1))
    d = _Driver(listener, monkeypatch)

    d.modifier(_CTRL, _CTRL_MASK)
    d.modifier(_SHIFT, _CTRL_MASK | _SHIFT_MASK)  # combo held
    d.key_down(_FOUR)  # action key pressed -> contaminates
    d.key_up(_FOUR)
    d.modifier(_SHIFT, _CTRL_MASK)  # release
    d.modifier(_CTRL, 0)

    assert fired == []


def test_tap_mode_fires_at_most_once_per_hold(monkeypatch):
    """Releasing the second combo key after the first must not re-fire."""
    fired = []
    listener = HotkeyListener(keys=["ctrl", "shift"], on_trigger=lambda: fired.append(1))
    d = _Driver(listener, monkeypatch)

    d.modifier(_CTRL, _CTRL_MASK)
    d.modifier(_SHIFT, _CTRL_MASK | _SHIFT_MASK)
    d.modifier(_SHIFT, _CTRL_MASK)  # first release -> fires
    d.modifier(_CTRL, 0)  # second release -> must not re-fire

    assert fired == [1]


def test_tap_after_contamination_fires_on_next_clean_hold(monkeypatch):
    """Contamination is per-hold: a fresh clean hold afterwards still fires."""
    fired = []
    listener = HotkeyListener(keys=["ctrl", "shift"], on_trigger=lambda: fired.append(1))
    d = _Driver(listener, monkeypatch)

    # Contaminated hold.
    d.modifier(_CTRL, _CTRL_MASK)
    d.modifier(_SHIFT, _CTRL_MASK | _SHIFT_MASK)
    d.key_down(_FOUR)
    d.key_up(_FOUR)
    d.modifier(_SHIFT, _CTRL_MASK)
    d.modifier(_CTRL, 0)
    assert fired == []

    # Clean hold.
    d.modifier(_CTRL, _CTRL_MASK)
    d.modifier(_SHIFT, _CTRL_MASK | _SHIFT_MASK)
    d.modifier(_SHIFT, _CTRL_MASK)
    d.modifier(_CTRL, 0)
    assert fired == [1]


# --- tap mode with a character key in the chord (e.g. Cmd+Ctrl+P) ---------
#
# macOS withholds the keyUp of a character key while Command is held, so a
# release-based trigger gets the character key "stuck" and stops firing (and
# false-fires on the bare modifier subset). A chord that contains a character
# key must therefore fire on the character key's keyDOWN — with the required
# modifiers read from that event's absolute flags — and must re-arm when the
# modifier flags clear, never depending on the character key's keyUp.


def test_char_chord_tap_fires_on_character_keydown_with_modifiers_held(monkeypatch):
    fired = []
    listener = HotkeyListener(keys=["cmd", "ctrl", "p"], on_trigger=lambda: fired.append(1))
    d = _Driver(listener, monkeypatch)

    d.modifier(_CMD, _CMD_MASK)                # cmd down
    d.modifier(_CTRL, _CMD_MASK | _CTRL_MASK)  # ctrl down -> both modifiers held
    assert fired == []                         # modifiers alone must not fire
    d.key_down(_P)                             # P down with Cmd+Ctrl in the flags

    assert fired == [1]


def test_char_chord_tap_does_not_fire_if_a_modifier_is_missing(monkeypatch):
    fired = []
    listener = HotkeyListener(keys=["cmd", "ctrl", "p"], on_trigger=lambda: fired.append(1))
    d = _Driver(listener, monkeypatch)

    d.modifier(_CTRL, _CTRL_MASK)  # only ctrl held — cmd is missing
    d.key_down(_P)                 # P down without the full modifier set

    assert fired == []


def test_char_chord_tap_rearms_when_modifiers_release_even_if_char_keyup_never_arrives(monkeypatch):
    """The character keyUp may never reach the listener (Command suppression);
    re-arming must key off the modifier flags clearing so a second tap fires."""
    fired = []
    listener = HotkeyListener(keys=["cmd", "ctrl", "p"], on_trigger=lambda: fired.append(1))
    d = _Driver(listener, monkeypatch)

    # First tap.
    d.modifier(_CMD, _CMD_MASK)
    d.modifier(_CTRL, _CMD_MASK | _CTRL_MASK)
    d.key_down(_P)
    assert fired == [1]
    # P's keyUp is suppressed (never delivered) — only the modifiers release.
    d.modifier(_CMD, _CTRL_MASK)  # cmd up
    d.modifier(_CTRL, 0)          # ctrl up -> all modifiers clear

    # Second clean tap must fire again.
    d.modifier(_CMD, _CMD_MASK)
    d.modifier(_CTRL, _CMD_MASK | _CTRL_MASK)
    d.key_down(_P)

    assert fired == [1, 1]


def test_char_chord_tap_does_not_fire_on_bare_modifiers_after_suppressed_char_keyup(monkeypatch):
    """After a tap whose character keyUp was suppressed, pressing only the
    modifiers (no character key) must NOT trigger — the character key must not
    stay 'stuck' as held."""
    fired = []
    listener = HotkeyListener(keys=["cmd", "ctrl", "p"], on_trigger=lambda: fired.append(1))
    d = _Driver(listener, monkeypatch)

    d.modifier(_CMD, _CMD_MASK)
    d.modifier(_CTRL, _CMD_MASK | _CTRL_MASK)
    d.key_down(_P)                # fires
    d.modifier(_CMD, _CTRL_MASK)  # cmd up (P keyUp suppressed)
    d.modifier(_CTRL, 0)          # ctrl up
    assert fired == [1]

    # Tap ONLY Cmd+Ctrl (the modifier subset), no P.
    d.modifier(_CMD, _CMD_MASK)
    d.modifier(_CTRL, _CMD_MASK | _CTRL_MASK)
    d.modifier(_CMD, _CTRL_MASK)
    d.modifier(_CTRL, 0)

    assert fired == [1]           # unchanged — no phantom trigger


def test_char_chord_tap_autorepeat_does_not_double_fire(monkeypatch):
    """Holding the character key emits repeated keyDowns; fire once per hold."""
    fired = []
    listener = HotkeyListener(keys=["cmd", "ctrl", "p"], on_trigger=lambda: fired.append(1))
    d = _Driver(listener, monkeypatch)

    d.modifier(_CMD, _CMD_MASK)
    d.modifier(_CTRL, _CMD_MASK | _CTRL_MASK)
    d.key_down(_P)  # initial press -> fires
    d.key_down(_P)  # autorepeat
    d.key_down(_P)  # autorepeat

    assert fired == [1]


def test_hold_mode_still_fires_activate_and_deactivate(monkeypatch):
    """A listener built without on_trigger keeps the original hold behavior."""
    events = []
    listener = HotkeyListener(
        keys=["ctrl", "shift"],
        on_activate=lambda: events.append("on"),
        on_deactivate=lambda: events.append("off"),
    )
    d = _Driver(listener, monkeypatch)

    d.modifier(_CTRL, _CTRL_MASK)
    d.modifier(_SHIFT, _CTRL_MASK | _SHIFT_MASK)  # combo held -> activate
    d.modifier(_SHIFT, _CTRL_MASK)  # release -> deactivate
    d.modifier(_CTRL, 0)

    assert events == ["on", "off"]


def test_ensure_enabled_is_noop_without_a_tap():
    listener = _listener()
    assert listener._tap is None
    assert listener.ensure_enabled() is False


def test_ensure_enabled_reenables_a_disabled_tap(monkeypatch):
    listener = _listener()
    listener._tap = object()  # sentinel standing in for a real tap
    state = {"enabled": False}  # macOS has disabled the tap
    calls = []

    monkeypatch.setattr(hk.Quartz, "CGEventTapIsEnabled", lambda tap: state["enabled"])

    def fake_enable(tap, on):
        calls.append(on)
        state["enabled"] = on

    monkeypatch.setattr(hk.Quartz, "CGEventTapEnable", fake_enable)

    # Disabled -> re-enabled, reports True.
    assert listener.ensure_enabled() is True
    assert calls == [True]
    # Already enabled -> no-op, reports False.
    assert listener.ensure_enabled() is False
    assert calls == [True]


def test_take_event_count_reads_and_resets():
    listener = _listener()
    assert listener.take_event_count() == 0
    listener._event_count = 3
    assert listener.take_event_count() == 3  # reads
    assert listener.take_event_count() == 0  # and resets


def test_callback_counts_every_event_for_liveness(monkeypatch):
    """The tap counts that events arrive (never which keys), so the poll can
    tell a live tap from a silently-dead one."""
    listener = _listener()
    # Non-target keycode: exercises the count without firing the combo.
    monkeypatch.setattr(hk.Quartz, "CGEventGetIntegerValueField", lambda e, f: 999)

    listener._tap_callback(None, hk.Quartz.kCGEventKeyDown, object(), None)
    listener._tap_callback(None, hk.Quartz.kCGEventKeyUp, object(), None)

    assert listener.take_event_count() == 2


# --- recorder helpers: keycode/flags -> token + combo validation ---------


def test_token_for_keycode_modifier():
    assert token_for_keycode(59) == "ctrl"  # left control
    assert token_for_keycode(56) == "shift"  # left shift


def test_token_for_keycode_letter():
    assert token_for_keycode(9) == "v"  # ANSI "v"


def test_token_for_keycode_named():
    assert token_for_keycode(49) == "space"


def test_token_for_keycode_unmapped_returns_none():
    assert token_for_keycode(999) is None


def test_modifier_tokens_from_flags_single():
    assert modifier_tokens_from_flags(hk.Quartz.kCGEventFlagMaskControl) == {"ctrl"}


def test_modifier_tokens_from_flags_multiple():
    flags = hk.Quartz.kCGEventFlagMaskControl | hk.Quartz.kCGEventFlagMaskShift
    assert modifier_tokens_from_flags(flags) == {"ctrl", "shift"}


def test_modifier_tokens_from_flags_none():
    assert modifier_tokens_from_flags(0) == set()


def test_validate_combo_accepts_two_and_three_key_with_modifier():
    assert validate_combo(["ctrl", "shift"]) is None
    assert validate_combo(["cmd", "ctrl", "v"]) is None


def test_validate_combo_rejects_one_key():
    with pytest.raises(ValueError):
        validate_combo(["ctrl"])


def test_validate_combo_rejects_four_keys():
    with pytest.raises(ValueError):
        validate_combo(["cmd", "ctrl", "alt", "shift"])


def test_validate_combo_rejects_no_modifier():
    with pytest.raises(ValueError):
        validate_combo(["a", "b"])
