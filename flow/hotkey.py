"""Global push-to-talk hotkey listener built on a Quartz CGEventTap.

No pynput, no TIS/TSM: on macOS 26 the Text Input Services APIs that
pynput's macOS backend calls from its listener thread assert main-thread
in any process that has initialized NSApplication and kill the process.
This implementation identifies keys purely by virtual keycode and never
translates keycodes to characters at runtime.

The event tap source is added to the MAIN run loop (flow.menubar runs
NSApp there), so tap callbacks fire on the main thread; start() itself
may safely be called from any thread.

Layout note: single-character hotkeys (e.g. "v") are matched against a
static ANSI keyboard layout char->keycode table, so they assume ANSI key
positions. Modifier-only combos (the default, e.g. ctrl+shift) and named
keys (arrows, space, f1-f20, ...) are layout-independent.
"""

from __future__ import annotations

import threading
import time
from typing import Callable

import Quartz

_ALIASES = {"option": "alt", "command": "cmd"}

# Modifier virtual keycodes: both left/right variants map to one canonical
# token, but held state is tracked per raw keycode so releasing one of two
# held Ctrls does not deactivate the combo.
_MODIFIER_KEYCODES = {
    54: "cmd",    # right command
    55: "cmd",    # left command
    56: "shift",  # left shift
    58: "alt",    # left option
    59: "ctrl",   # left control
    60: "shift",  # right shift
    61: "alt",    # right option
    62: "ctrl",   # right control
}

_MODIFIER_MASKS = {
    "cmd": Quartz.kCGEventFlagMaskCommand,
    "shift": Quartz.kCGEventFlagMaskShift,
    "alt": Quartz.kCGEventFlagMaskAlternate,
    "ctrl": Quartz.kCGEventFlagMaskControl,
}

_NAMED_KEYCODES = {
    "space": 49,
    "tab": 48,
    "enter": 36,
    "esc": 53,
    "left": 123,
    "right": 124,
    "down": 125,
    "up": 126,
    "f1": 122, "f2": 120, "f3": 99, "f4": 118, "f5": 96,
    "f6": 97, "f7": 98, "f8": 100, "f9": 101, "f10": 109,
    "f11": 103, "f12": 111, "f13": 105, "f14": 107, "f15": 113,
    "f16": 106, "f17": 64, "f18": 79, "f19": 80, "f20": 90,
}

# Static ANSI-layout char -> virtual keycode table (see module docstring).
_CHAR_KEYCODES = {
    "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7,
    "c": 8, "v": 9, "b": 11, "q": 12, "w": 13, "e": 14, "r": 15,
    "y": 16, "t": 17, "1": 18, "2": 19, "3": 20, "4": 21, "6": 22,
    "5": 23, "9": 25, "7": 26, "8": 28, "0": 29, "o": 31, "u": 32,
    "i": 34, "p": 35, "l": 37, "j": 38, "k": 40, "n": 45, "m": 46,
}

_MODIFIER_TOKENS = frozenset(_MODIFIER_MASKS)


def _parse_key_name(name: str) -> str:
    """Validate a configured key name and return its canonical token."""
    token = _ALIASES.get(name.strip().lower(), name.strip().lower())
    if token in _MODIFIER_TOKENS or token in _NAMED_KEYCODES:
        return token
    if len(token) == 1 and token in _CHAR_KEYCODES:
        return token
    raise ValueError(
        f"Unknown hotkey name {name!r}: use ctrl/alt/cmd/shift, arrows, "
        "space/tab/enter/esc, f1-f20, or a single character"
    )


def _keycodes_for_token(token: str) -> tuple[int, ...]:
    """All virtual keycodes whose events map to this canonical token."""
    if token in _MODIFIER_TOKENS:
        return tuple(kc for kc, t in _MODIFIER_KEYCODES.items() if t == token)
    if token in _NAMED_KEYCODES:
        return (_NAMED_KEYCODES[token],)
    return (_CHAR_KEYCODES[token],)


# Inverted lookup tables for the recorder (keycode -> token). Built once at
# module load from the authoritative tables above; never rebuilt per call.
# (_MODIFIER_KEYCODES is already keyed by keycode, so it is used directly.)
_KEYCODE_TO_NAMED = {v: k for k, v in _NAMED_KEYCODES.items()}
_KEYCODE_TO_CHAR = {v: k for k, v in _CHAR_KEYCODES.items()}


def token_for_keycode(keycode: int) -> str | None:
    """Map an NSEvent/Quartz virtual keycode to a canonical hotkey token,
    or None if the keycode is not one this app recognizes.

    Inverts the modifier, named-key, and ANSI-char keycode tables (the same
    tables HotkeyListener matches against). Modifier keycodes win over any
    overlap. NSEvent keyCodes equal Quartz virtual keycodes, so this is
    authoritative for the recorder's NSEvent capture.
    """
    if keycode in _MODIFIER_KEYCODES:
        return _MODIFIER_KEYCODES[keycode]
    if keycode in _KEYCODE_TO_NAMED:
        return _KEYCODE_TO_NAMED[keycode]
    return _KEYCODE_TO_CHAR.get(keycode)


def modifier_tokens_from_flags(flags: int) -> set[str]:
    """The set of modifier tokens currently down for an NSEvent modifierFlags
    (or Quartz event-flags) value, using _MODIFIER_MASKS. NSEvent and Quartz
    share the same mask bits, so one mapping serves both."""
    return {token for token, mask in _MODIFIER_MASKS.items() if flags & mask}


def validate_combo(
    keys: list[str],
    *,
    min_keys: int = 2,
    max_keys: int = 3,
    require_modifier: bool = True,
) -> None:
    """Raise ValueError with a human-readable message if `keys` is an
    unusable global shortcut: too few keys, too many keys, or (when
    require_modifier) no modifier. Returns None on success.

    Used by the settings window's Save validation and unit-tested directly.
    A 1-key global hotkey is unusable, hence min_keys defaults to 2.
    """
    if len(keys) < min_keys:
        raise ValueError(f"A shortcut needs at least {min_keys} keys.")
    if len(keys) > max_keys:
        raise ValueError(f"A shortcut can have at most {max_keys} keys.")
    if require_modifier and not any(k in _MODIFIER_TOKENS for k in keys):
        raise ValueError(
            "A shortcut needs at least one modifier (cmd, ctrl, alt, or shift)."
        )


class HotkeyListener:
    """Fires callbacks when a combo of keys is held and then released.

    Two modes, chosen by which callbacks are supplied:

    - **hold** (``on_activate`` / ``on_deactivate``): on_activate fires once
      when all configured keys are held simultaneously; on_deactivate fires
      once when any of them is subsequently released. This drives push-to-talk.

    - **tap** (``on_trigger``): on_trigger fires once when the combo is held and
      then released *cleanly* — i.e. no other key was pressed during the hold.
      A contaminating keypress (e.g. the ``4`` of a Cmd+Ctrl+Shift+4 screenshot)
      cancels the trigger for that hold. Firing on release also guarantees the
      combo modifiers are physically up, so a synthesized paste is clean.

    Callbacks run on the main thread (the event tap's run loop) and must
    return quickly.

    Known v1 limitation: if the tap misses a release event (e.g. secure
    input steals focus), the key stays marked as held until it is pressed
    and released again; wait_all_released() will time out meanwhile. For
    modifiers this self-heals on the next flagsChanged event because the
    event flags are consulted directly.
    """

    def __init__(
        self,
        keys: list[str],
        on_activate: Callable[[], None] | None = None,
        on_deactivate: Callable[[], None] | None = None,
        on_trigger: Callable[[], None] | None = None,
        debug_label: str | None = None,
    ) -> None:
        if not keys:
            raise ValueError("Hotkey keys list must not be empty")
        # Diagnostic logging label (e.g. "repaste"); None disables all logging.
        # Gated by the caller to dev builds only — never the production build —
        # because it logs keycodes pressed while a modifier is held.
        self._debug = debug_label
        self._targets: frozenset[str] = frozenset(_parse_key_name(k) for k in keys)
        self._mode = "tap" if on_trigger is not None else "hold"
        self._on_activate = on_activate or (lambda: None)
        self._on_deactivate = on_deactivate or (lambda: None)
        self._on_trigger = on_trigger or (lambda: None)
        # A tap combo that includes a non-modifier ("character") key fires on
        # that key's keyDOWN — with the required modifiers verified from the
        # event's absolute flags — NOT on its release. macOS withholds the
        # character keyUp while Command is held (an AppKit dispatch quirk), so
        # release-based detection leaves the character key "stuck" and the
        # trigger then stops firing and false-fires on the bare modifier
        # subset. Modifier-only tap combos have no character key to key off and
        # keep clean-release detection (modifiers self-heal via flagsChanged).
        self._target_mods = frozenset(t for t in self._targets if t in _MODIFIER_TOKENS)
        self._target_keys = frozenset(self._targets - self._target_mods)
        self._keydown_fire = self._mode == "tap" and bool(self._target_keys)
        # keydown-fire state: which target character keys are physically down,
        # and whether this hold has already fired (re-armed when the modifier
        # flags clear — see _rearm_on_modifier_release).
        self._keys_down: set[str] = set()
        self._fired_this_hold = False
        # tap mode: set when a non-combo key is pressed during the hold; a
        # contaminated hold does not fire on_trigger when it is released.
        self._contaminated = False
        # keycode -> token, restricted to the configured target keys.
        self._keycode_to_token: dict[int, str] = {}
        for token in self._targets:
            for keycode in _keycodes_for_token(token):
                self._keycode_to_token[keycode] = token
        # token -> raw keycodes currently down (e.g. keycodes 59 and 62 both
        # map to "ctrl"); a token counts as released only when its last
        # physical variant goes up.
        self._held: dict[str, set[int]] = {}
        self._active = False
        self._cond = threading.Condition()
        # Python references to the tap machinery; if the callback (or the
        # tap/source) is garbage-collected the process crashes.
        self._tap = None
        self._source = None
        self._callback = None
        # Liveness counter: how many events the tap has delivered since the
        # last poll. Counts only THAT events arrive, never which keys.
        self._event_count = 0

    def start(self) -> None:
        """Create and enable the event tap on the main run loop (non-blocking).

        Safe to call from a worker thread: the run loop source is attached
        to CFRunLoopGetMain(), where flow.menubar runs NSApp.

        Raises RuntimeError if the tap cannot be created (Input Monitoring
        permission missing or revoked).
        """
        if self._tap is not None:
            return
        callback = self._tap_callback  # keep a strong reference on self
        mask = (
            Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown)
            | Quartz.CGEventMaskBit(Quartz.kCGEventKeyUp)
            | Quartz.CGEventMaskBit(Quartz.kCGEventFlagsChanged)
        )
        tap = Quartz.CGEventTapCreate(
            Quartz.kCGSessionEventTap,
            Quartz.kCGTailAppendEventTap,
            Quartz.kCGEventTapOptionListenOnly,
            mask,
            callback,
            None,
        )
        if tap is None:
            raise RuntimeError(
                "Could not create the keyboard event tap. Grant TRD Speak "
                "Input Monitoring permission in System Settings > Privacy & "
                "Security > Input Monitoring, then restart the app."
            )
        source = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
        Quartz.CFRunLoopAddSource(
            Quartz.CFRunLoopGetMain(), source, Quartz.kCFRunLoopCommonModes
        )
        Quartz.CGEventTapEnable(tap, True)
        Quartz.CFRunLoopWakeUp(Quartz.CFRunLoopGetMain())
        self._callback = callback
        self._tap = tap
        self._source = source

    def stop(self) -> None:
        """Disable the event tap and detach it from the main run loop."""
        tap, source = self._tap, self._source
        if tap is None:
            return
        try:
            Quartz.CGEventTapEnable(tap, False)
            if source is not None:
                Quartz.CFRunLoopRemoveSource(
                    Quartz.CFRunLoopGetMain(), source, Quartz.kCFRunLoopCommonModes
                )
            Quartz.CFMachPortInvalidate(tap)
        except Exception as exc:
            print(f"Hotkey listener stop error: {exc}")
        self._tap = None
        self._source = None
        self._callback = None
        with self._cond:
            self._held.clear()
            self._active = False
            self._cond.notify_all()

    def ensure_enabled(self) -> bool:
        """Re-assert the event tap if macOS has disabled it.

        A tap callback that runs too long trips the system's tap-timeout
        watchdog, which disables the tap; the keyboard then stops reaching us
        even though the process is alive. A periodic caller (the menu poll)
        uses this to recover. Returns True if the tap had to be re-enabled,
        False if it was already enabled or no tap exists.
        """
        tap = self._tap
        if tap is None:
            return False
        if not Quartz.CGEventTapIsEnabled(tap):
            Quartz.CGEventTapEnable(tap, True)
            return True
        return False

    def take_event_count(self) -> int:
        """Return the number of events seen since the last call, and reset.

        A periodic caller logs this as a tap heartbeat: a long stretch of
        zeros while the app is in use means the tap has gone silent.
        """
        count = self._event_count
        self._event_count = 0
        return count

    def wait_all_released(self, timeout: float = 2.0) -> bool:
        """Block until every trigger key is physically up.

        Returns True once released, False if the timeout expires first.
        Called from worker threads; the condition is updated by the tap
        callbacks on the main thread.
        """
        deadline = time.monotonic() + timeout
        with self._cond:
            while self._held:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._cond.wait(remaining)
            return True

    # -- event tap callback (runs on the main thread) ------------------

    def _tap_callback(self, proxy, event_type, event, refcon):
        # An exception escaping a tap callback kills the tap silently, so
        # the entire body is guarded.
        try:
            self._event_count += 1
            if event_type in (
                Quartz.kCGEventTapDisabledByTimeout,
                Quartz.kCGEventTapDisabledByUserInput,
            ):
                if self._tap is not None:
                    Quartz.CGEventTapEnable(self._tap, True)
                    print("Keyboard event tap was disabled by the system — re-enabled it.")
                return event
            keycode = Quartz.CGEventGetIntegerValueField(
                event, Quartz.kCGKeyboardEventKeycode
            )
            token = self._keycode_to_token.get(keycode)
            if token is None:
                # Diagnostic: a key we don't recognize, pressed while a modifier
                # is held — this is exactly how a mis-keycoded chord character
                # would slip past (e.g. if 'p' were not keycode 35 here).
                if (
                    self._debug
                    and event_type == Quartz.kCGEventKeyDown
                    and self._held
                ):
                    self._dbg(
                        f"keyDown UNMAPPED keycode={keycode} while held="
                        f"{sorted(self._held)} flags_mods="
                        f"{sorted(modifier_tokens_from_flags(Quartz.CGEventGetFlags(event)))}"
                    )
                # A non-combo key. For a modifier-only tap combo, an ordinary
                # key pressed while the combo is held contaminates the hold
                # (e.g. the "4" of Cmd+Ctrl+Shift+4), so the trigger will not
                # fire on release. A keydown-fire combo has already fired on its
                # own character key, so stray keys are irrelevant to it. Other
                # modifiers arrive as flagsChanged and are ignored here.
                if (
                    self._mode == "tap"
                    and not self._keydown_fire
                    and event_type == Quartz.kCGEventKeyDown
                ):
                    with self._cond:
                        if self._active:
                            self._contaminated = True
                return event
            if self._keydown_fire and token in self._target_keys:
                # The chord's character key: fire on its keyDown (modifiers read
                # from this event's absolute flags), never on its keyUp.
                if event_type == Quartz.kCGEventKeyDown:
                    self._char_key_down(token, Quartz.CGEventGetFlags(event))
                elif event_type == Quartz.kCGEventKeyUp:
                    self._char_key_up(token)
                return event
            if event_type == Quartz.kCGEventKeyDown:
                self._press(token, keycode)
            elif event_type == Quartz.kCGEventKeyUp:
                self._release(token, keycode)
            elif event_type == Quartz.kCGEventFlagsChanged:
                flags = Quartz.CGEventGetFlags(event)
                self._flags_changed(token, keycode, flags)
                if self._keydown_fire:
                    self._rearm_on_modifier_release(flags)
                self._dbg(
                    f"flagsChanged token={token!r} keycode={keycode} "
                    f"mods_now={sorted(modifier_tokens_from_flags(flags))} "
                    f"held={sorted(self._held)}"
                )
        except Exception as exc:
            print(f"Hotkey tap callback error: {exc}")
        return event

    def _dbg(self, msg: str) -> None:
        """Diagnostic log (dev builds only — gated by self._debug being set)."""
        if self._debug:
            print(f"[{self._debug}] {msg}")

    def _char_key_down(self, token: str, flags: int) -> None:
        """Fire on the keyDown of a chord's character key.

        Gated on the required modifiers all being present in THIS event's
        absolute flags (read fresh, never accumulated). Fires at most once per
        hold; auto-repeat keyDowns are absorbed by the `_fired_this_hold` guard.
        The hold is re-armed by _rearm_on_modifier_release (modifier flags
        clearing) or _char_key_up — never by waiting on this key's own keyUp,
        which macOS withholds while Command is held.
        """
        held_mods = modifier_tokens_from_flags(flags)
        fire = False
        with self._cond:
            self._keys_down.add(token)
            already = self._fired_this_hold
            if (
                self._target_mods <= held_mods
                and self._target_keys <= self._keys_down
                and not self._fired_this_hold
            ):
                self._fired_this_hold = True
                fire = True
            keys_down = sorted(self._keys_down)
        self._dbg(
            f"char keyDown {token!r}: held_mods={sorted(held_mods)} "
            f"need_mods={sorted(self._target_mods)} keys_down={keys_down} "
            f"already_fired={already} -> FIRE={fire}"
        )
        if fire:
            self._on_trigger()

    def _char_key_up(self, token: str) -> None:
        """A chord character key released (when its keyUp is actually delivered).

        Drop it and re-arm. The keyUp may never arrive while Command is held,
        so _rearm_on_modifier_release is the reliable re-arm path; this only
        handles the case where the keyUp does come through.
        """
        with self._cond:
            self._keys_down.discard(token)
            self._fired_this_hold = False
        self._dbg(f"char keyUp {token!r}")

    def _rearm_on_modifier_release(self, flags: int) -> None:
        """Re-arm the keydown-fire trigger once the required modifiers are no
        longer all held. Reading absolute flags makes this self-healing and
        independent of the withheld character keyUp; it also clears any
        character key left 'stuck' by a missing keyUp so the bare modifier
        subset can never phantom-trigger."""
        if not (self._target_mods <= modifier_tokens_from_flags(flags)):
            with self._cond:
                self._fired_this_hold = False
                self._keys_down.clear()
            self._dbg(
                f"re-armed (modifiers cleared: now "
                f"{sorted(modifier_tokens_from_flags(flags))})"
            )

    def _flags_changed(self, token: str, keycode: int, flags: int) -> None:
        """Resolve modifier presses/releases from a flagsChanged event.

        flagsChanged carries the keycode of the physical key that changed AND
        the ABSOLUTE modifier flags now in effect. Two steps:

        1. If the carried token's mask bit is set, the keycode toggled — press
           it if we did not have it held, release that variant if we did.
        2. Reconcile EVERY held modifier against the absolute flags: release any
           modifier token whose mask bit is now clear. This is what makes the
           listener self-healing — a missed release, or TWO modifiers clearing
           in a single event (a quick simultaneous release can surface as one
           flagsChanged carrying just one keycode), can never leave a modifier
           stuck in _held. A stuck modifier would wedge wait_all_released() and
           silently drop the next trigger.
        """
        if flags & _MODIFIER_MASKS[token]:
            with self._cond:
                held_now = keycode in self._held.get(token, ())
            if held_now:
                self._release(token, keycode)
            else:
                self._press(token, keycode)
        self._release_modifiers_absent_from(flags)

    def _release_modifiers_absent_from(self, flags: int) -> None:
        """Release every held modifier variant whose flag bit is clear in
        `flags`. Non-modifier ("character") keys are tracked by keyDown/keyUp,
        not by the flag bits, so they are deliberately left untouched here."""
        with self._cond:
            stale = [
                (tok, kc)
                for tok, variants in self._held.items()
                if tok in _MODIFIER_MASKS and not (flags & _MODIFIER_MASKS[tok])
                for kc in list(variants)
            ]
        for tok, kc in stale:
            self._release(tok, kc)

    def _press(self, token: str, keycode: int) -> None:
        fire_activate = False
        with self._cond:
            self._held.setdefault(token, set()).add(keycode)
            # keydown-fire combos trigger in _char_key_down, never here: their
            # character key is routed away from _held, so this branch must not
            # arm the release-based path (which would let the bare modifier
            # subset phantom-fire). The explicit guard keeps that invariant.
            if (
                not self._active
                and not self._keydown_fire
                and self._held.keys() == self._targets
            ):
                self._active = True
                if self._mode == "tap":
                    # A fresh full-hold starts clean; contamination is per-hold.
                    self._contaminated = False
                else:
                    fire_activate = True
        if fire_activate:
            self._on_activate()

    def _release(self, token: str, keycode: int) -> None:
        fire_deactivate = False
        fire_trigger = False
        with self._cond:
            variants = self._held.get(token)
            if variants is not None:
                variants.discard(keycode)
                if not variants:
                    del self._held[token]
            if self._active and token not in self._held:
                self._active = False
                if self._mode == "tap":
                    fire_trigger = not self._contaminated
                else:
                    fire_deactivate = True
            if not self._held:
                self._cond.notify_all()
        if fire_deactivate:
            self._on_deactivate()
        if fire_trigger:
            self._on_trigger()
