"""Global push-to-talk hotkey listener: a combo-matching state machine fed by
the shared flow.event_tap.EventTapHub (one hardened CGEventTap per process —
issue #20; the tap parameters and their hard-won rationale live there).

No pynput, no TIS/TSM: on macOS 26 the Text Input Services APIs that
pynput's macOS backend calls from its listener thread assert main-thread
in any process that has initialized NSApplication and kill the process.
This implementation identifies keys purely by virtual keycode and never
translates keycodes to characters at runtime.

The hub's tap source is added to the MAIN run loop (flow.menubar runs
NSApp there), so _tap_callback fires on the main thread; start() itself
may safely be called from any thread.

Layout note: single-character hotkeys (e.g. "v") are matched against a
static ANSI keyboard layout char->keycode table, so they assume ANSI key
positions. Modifier-only combos (the default, e.g. ctrl+shift) and named
keys (arrows, space, f1-f20, ...) are layout-independent.
"""

from __future__ import annotations

import threading
import time
import traceback
from typing import Callable

import Quartz

from flow.event_tap import EventTapHub

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


def modifiers_physically_down() -> bool:
    """True if any modifier key (cmd/shift/alt/ctrl) is physically held right
    now, per the OS's own combined session state.

    CGEventSourceFlagsState reads the system's live flag state — independent
    of any event tap's shadow bookkeeping, which can go stale on a missed
    keyUp (see the class docstring's known v1 limitation). The paste paths use
    this as the tie-breaker when wait_all_released() times out: a stale shadow
    state self-heals (paste proceeds), genuinely held keys skip the paste.
    Masked to the four modifier bits so caps lock / fn never count.
    Needs no TCC permission.
    """
    flags = Quartz.CGEventSourceFlagsState(
        Quartz.kCGEventSourceStateCombinedSessionState
    )
    return any(flags & mask for mask in _MODIFIER_MASKS.values())


def canonicalize_combo(keys: list[str]) -> list[str]:
    """Return `keys` normalized to canonical tokens: whitespace-stripped,
    lower-cased, aliases resolved (see _parse_key_name). Raises ValueError if
    any token is not a recognized key name.

    Used wherever a combo that has passed validate_combo needs to be
    STORED/displayed/matched consistently with how HotkeyListener itself
    will interpret it (flow.config, flow.hotkey_state -- issue #26): without
    this, a whitespace-padded but otherwise legitimate token (e.g. " ctrl")
    would validate successfully yet be persisted/displayed untrimmed.
    """
    return [_parse_key_name(k) for k in keys]


def validate_combo(
    keys: list[str],
    *,
    min_keys: int = 2,
    max_keys: int = 3,
    require_modifier: bool = True,
) -> None:
    """Raise ValueError with a human-readable message if `keys` is an
    unusable global shortcut: an unrecognized key name, the same key
    repeated, too few keys, too many keys, or (when require_modifier) no
    modifier. Returns None on success.

    Used by the settings window's Save validation, by flow.config and
    flow.hotkey_state for config.toml/hotkeys.json (issue #26), and
    unit-tested directly. A 1-key global hotkey is unusable, hence min_keys
    defaults to 2.

    Canonicalizes every token via _parse_key_name FIRST -- the same
    normalization (strip whitespace, lower-case, resolve aliases like
    "option" -> "alt") the real HotkeyListener applies -- before counting or
    checking for a modifier. This closes two loopholes a raw string
    comparison would miss (found by adversarial testing, issue #26):
    (1) a garbage/whitespace-only token (e.g. " ") is not a real key and is
    rejected here rather than raising deep inside HotkeyListener
    construction when the app is already starting up; (2) two spellings of
    the same physical key (["ctrl", "ctrl"] or ["cmd", "command"]) count as
    ONE key, not two -- otherwise the "a 1-key hotkey is unusable" rule this
    function exists to enforce could be trivially defeated.
    """
    canonical = canonicalize_combo(keys)
    if len(set(canonical)) != len(canonical):
        raise ValueError("A shortcut cannot use the same key twice.")
    if len(canonical) < min_keys:
        raise ValueError(f"A shortcut needs at least {min_keys} keys.")
    if len(canonical) > max_keys:
        raise ValueError(f"A shortcut can have at most {max_keys} keys.")
    if require_modifier and not any(k in _MODIFIER_TOKENS for k in canonical):
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
        name: str | None = None,
        hub: EventTapHub | None = None,
    ) -> None:
        if not keys:
            raise ValueError("Hotkey keys list must not be empty")
        # Always-on label used to ATTRIBUTE errors to a specific tap (e.g.
        # "correction"). An exception raised by the on_trigger callback — for
        # example the correction window failing to build — surfaces here, so
        # without the label every tap's failures read identically and a window
        # bug masquerades as a generic "tap error".
        self._name = name or debug_label or "hotkey"
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
        # True while any modifier OUTSIDE the target set is physically down
        # (from the last flagsChanged's absolute flags). Matching is EXACT:
        # a combo that is merely a subset of what is held must not fire —
        # otherwise ⌘⌃⇧4 starts a ctrl+shift dictation and ⌘⌃⌥ fires both
        # the cmd+ctrl and cmd+alt tap combos (issue #21).
        self._extra_mods_down = False
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
        # The shared per-process tap hub (issue #20). App passes its single
        # hub so all listeners share ONE CGEventTap; a listener constructed
        # without one (tests, tools) gets a private hub.
        self._hub = hub if hub is not None else EventTapHub()
        self._registered = False

    def start(self) -> None:
        """Register with the event-tap hub, creating the hub's single tap on
        first need (idempotent, safe from a worker thread — the hub attaches
        its source to CFRunLoopGetMain(), where flow.menubar runs NSApp).

        Raises RuntimeError if the tap cannot be created (Input Monitoring
        permission missing or revoked) — the same contract as when each
        listener owned a tap, so App's failure isolation (#22) and the boot
        "Restart TRD Speak now" flow are unchanged.
        """
        if self._registered:
            return
        self._hub.register(self)
        self._registered = True

    def stop(self) -> None:
        """Unregister from the hub and forget all shadow state.

        The hub's tap deliberately survives (one tap per process; see
        flow.event_tap). State is cleared even if this listener was never
        registered, so a stop() always leaves a clean slate and unblocks any
        wait_all_released() waiter.
        """
        try:
            self._hub.unregister(self)
        except Exception as exc:
            print(f"Hotkey listener stop error: {exc}")
        self._registered = False
        self.reset_hold_state()

    def reset_hold_state(self) -> None:
        """Forget every per-hold shadow state: held keys, active/contaminated
        flags, keydown-fire arming. Called by stop() and by the hub on
        unmute(), because keys pressed while the hub was muted were never
        seen — stale state must not phantom-fire on the next event (#21
        per-hold semantics). Wakes wait_all_released() waiters.

        A hold-mode listener whose combo was ACTIVE gets one balancing
        on_deactivate (guarded, fired outside the lock): a window can be
        opened with the mouse while push-to-talk is held, and clearing
        _active silently would leave the recording with no stop signal until
        max_seconds (adversarial finding ADV-15). Tap-mode state is only
        cleared — synthesizing an on_trigger here would paste into whatever
        window just opened.
        """
        with self._cond:
            fire_deactivate = self._active and self._mode == "hold"
            self._held.clear()
            self._keys_down.clear()
            self._active = False
            self._contaminated = False
            self._fired_this_hold = False
            self._extra_mods_down = False
            self._cond.notify_all()
        if fire_deactivate:
            self._guarded(self._on_deactivate)

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

    # -- event callback (runs on the main thread, fed by the hub) -------

    def _tap_callback(self, proxy, event_type, event, refcon):
        # The hub guards each listener, but an exception must still never
        # escape here (defense in depth: a direct caller is a tap callback).
        try:
            if event_type in (
                Quartz.kCGEventTapDisabledByTimeout,
                Quartz.kCGEventTapDisabledByUserInput,
            ):
                # Tap lifecycle is the hub's job (flow.event_tap re-enables
                # and never forwards these); ignore them defensively.
                return event
            # Modifiers are tracked SOLELY from the absolute CGEventGetFlags()
            # bitmask, never from the per-event keycode: that keycode can be
            # dropped, merged with another modifier's change, or arrive as 0
            # (ShortcutRecorder #129), and any one missed toggle would desync a
            # shadow state that never re-syncs. Reconciling every target
            # modifier against the absolute flags on each flagsChanged is the
            # documented Hammerspoon/Karabiner/pynput practice and self-heals.
            if event_type == Quartz.kCGEventFlagsChanged:
                flags = Quartz.CGEventGetFlags(event)
                self._reconcile_modifiers(flags)
                if self._keydown_fire:
                    self._rearm_on_modifier_release(flags)
                return event
            keycode = Quartz.CGEventGetIntegerValueField(
                event, Quartz.kCGKeyboardEventKeycode
            )
            token = self._keycode_to_token.get(keycode)
            if token is None or token in self._target_mods:
                # A non-combo key, or a stray modifier keyDown/keyUp (modifiers
                # are handled above via flagsChanged and ignored here). For a
                # modifier-only tap combo, an ordinary key pressed while the
                # combo is held contaminates the hold (e.g. the "4" of
                # Cmd+Ctrl+Shift+4), so the trigger will not fire on release. A
                # keydown-fire combo has already fired on its own character key,
                # so stray keys are irrelevant to it.
                if (
                    token is None
                    and self._mode == "tap"
                    and not self._keydown_fire
                    and event_type == Quartz.kCGEventKeyDown
                ):
                    with self._cond:
                        if self._active:
                            self._contaminated = True
                return event
            # token is a character key belonging to this chord.
            if self._keydown_fire:
                # Fire on its keyDown (modifiers read from this event's absolute
                # flags), never on its keyUp.
                if event_type == Quartz.kCGEventKeyDown:
                    self._char_key_down(token, Quartz.CGEventGetFlags(event))
                elif event_type == Quartz.kCGEventKeyUp:
                    self._char_key_up(token)
                return event
            if event_type == Quartz.kCGEventKeyDown:
                self._press(token, keycode)
            elif event_type == Quartz.kCGEventKeyUp:
                self._release(token, keycode)
        except Exception as exc:
            # Includes exceptions raised by the on_trigger / on_activate
            # callbacks (they run synchronously from here). Log the tap's name
            # AND the full traceback: a bare message like "No attribute
            # monospacedSystemFontOfSize_" with no stack and no tap identity is
            # exactly what made a one-line correction-window bug take many
            # rounds to localize.
            print(
                f"[{self._name}] tap callback error: {exc}\n"
                f"{traceback.format_exc()}"
            )
        return event

    def _dbg(self, msg: str) -> None:
        """Diagnostic log (dev builds only — gated by self._debug being set)."""
        if self._debug:
            print(f"[{self._debug}] {msg}")

    def _char_key_down(self, token: str, flags: int) -> None:
        """Fire on the keyDown of a chord's character key.

        Gated on the held modifiers in THIS event's absolute flags (read
        fresh, never accumulated) being EXACTLY the required set — an extra
        modifier means a bigger chord (⌘⌃⇧P must not fire a ⌘⌃P chord).
        Fires at most once per hold; auto-repeat keyDowns are absorbed by the
        `_fired_this_hold` guard. The hold is re-armed by
        _rearm_on_modifier_release (modifier flags clearing) or _char_key_up —
        never by waiting on this key's own keyUp, which macOS withholds while
        Command is held.
        """
        held_mods = modifier_tokens_from_flags(flags)
        fire = False
        with self._cond:
            self._keys_down.add(token)
            already = self._fired_this_hold
            if (
                self._target_mods == held_mods
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

    def _reconcile_modifiers(self, flags: int) -> None:
        """Make the set of held TARGET modifiers exactly match those present in
        the absolute flag bitmask, pressing or releasing as needed.

        This is the whole modifier story: flagsChanged carries the ABSOLUTE
        modifier flags now in effect, and that bitmask — not the per-event
        keycode — is authoritative. Driving state purely from it means a
        dropped flagsChanged, two modifiers changing in one event, or an event
        carrying keycode 0 all self-heal on the very next event and can never
        leave a target modifier stuck held (which would wedge
        wait_all_released) or stuck un-held (which would silently drop every
        trigger — the bug this replaces).

        Held modifiers collapse to one entry per token keyed on the token's
        canonical keycode: left/right variants share a flag bit, so the bit
        stays set while either is down and clears only when the last goes up —
        exactly the variant semantics, without per-keycode bookkeeping.
        Character keys (tracked by keyDown/keyUp) are deliberately untouched.

        Modifiers OUTSIDE the target set are not tracked in _held, but their
        presence (extra = flags mods - targets) gates matching: an extra
        modifier contaminates an active tap hold or deactivates an active
        hold-mode combo, and _press consults the stored flag so a combo that
        completes UNDER an extra modifier never activates cleanly. The extra
        check runs BEFORE stale target releases on purpose: a coalesced event
        that simultaneously drops a target and introduces an extra (cmd+ctrl
        flags jumping to cmd+alt) is ambiguous — the alt may have gone down
        before the ctrl release — so it counts as contamination, never as a
        clean release (a false fire pastes into the user's window; a missed
        fire is just a retry). Once the extras clear, matching recovers on the
        next fresh full hold — the current hold stays contaminated/deactivated
        (per-hold semantics).
        """
        all_mods = modifier_tokens_from_flags(flags)
        present = all_mods & self._target_mods
        extra = all_mods - self._target_mods
        fire_deactivate = False
        with self._cond:
            self._extra_mods_down = bool(extra)
            if extra and self._active:
                if self._mode == "tap":
                    # Same as a stray character keyDown: the hold is spoiled.
                    self._contaminated = True
                else:
                    # The user rolled into a bigger chord — stop the hold now.
                    self._active = False
                    fire_deactivate = True
            stale = [
                (tok, kc)
                for tok in self._held
                if tok in self._target_mods and tok not in present
                for kc in list(self._held[tok])
            ]
            fresh = [tok for tok in present if tok not in self._held]
        if self._debug and (fresh or stale or extra):
            self._dbg(
                f"reconcile: flags_mods={sorted(all_mods)} "
                f"present={sorted(present)} extra={sorted(extra)} "
                f"held={sorted(self._held)} "
                f"press={fresh} release={[t for t, _ in stale]}"
            )
        # Each step is individually guarded: a callback raising mid-loop (e.g.
        # on_trigger during a coalesced release of both modifiers) must not
        # abort the remaining releases — that strands a modifier in _held and
        # wedges wait_all_released until an unrelated event self-heals it.
        if fire_deactivate:
            self._guarded(self._on_deactivate)
        for tok, kc in stale:
            self._guarded(lambda t=tok, k=kc: self._release(t, k))
        for tok in fresh:
            self._guarded(lambda t=tok: self._press(t, _keycodes_for_token(t)[0]))

    def _guarded(self, fn: Callable[[], None]) -> None:
        """Run one reconcile step, logging (never propagating) an exception
        escaping a user callback, so the rest of the reconcile still runs."""
        try:
            fn()
        except Exception as exc:
            print(
                f"[{self._name}] reconcile callback error: {exc}\n"
                f"{traceback.format_exc()}"
            )

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
                if self._mode == "tap":
                    # A fresh full-hold starts clean; contamination is
                    # per-hold. A hold completed UNDER an extra modifier
                    # (e.g. cmd+ctrl finishing while alt is down) starts
                    # contaminated instead, so it cannot fire on release.
                    self._active = True
                    self._contaminated = self._extra_mods_down
                elif not self._extra_mods_down:
                    # Hold mode activates only on the EXACT modifier set; a
                    # bigger chord (⌘⌃⇧4 vs ctrl+shift) must not start it.
                    self._active = True
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
