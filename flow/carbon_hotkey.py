"""Carbon RegisterEventHotKey backend for key+modifier shortcuts (issue #23).

Why a second backend exists: CGEventTaps carry an entire failure class —
timeout-disable, TCC identity/staleness, sleep/wake death, Secure Input
blackout — that a Carbon hotkey registration simply does not have. Apple DTS
and every mature hotkey app (Hammerspoon, Rectangle/MASShortcut,
Maccy/KeyboardShortcuts, Electron/Chromium) use RegisterEventHotKey for
discrete shortcuts: it needs NO TCC permission (no Input Monitoring, no
Accessibility), keeps working under Secure Keyboard Entry, and delivers both
kEventHotKeyPressed and kEventHotKeyReleased, so it even drives push-to-talk.
Its one hard limitation: a hotkey is exactly ONE virtual key + a modifier
mask — it cannot express the app's modifier-only default combos, which stay
on the hardened tap (flow.event_tap / flow.hotkey).

The PyObjC bridge is vendored from quickmachotkey (MIT) under
flow/_vendor/quickmachotkey/ — see that package's __init__ for the
vendor-vs-depend rationale. This module installs ONE process-wide Carbon
event handler for BOTH pressed and released events (the soffes/HotKey /
KeyboardShortcuts pattern; upstream only handles pressed) and dispatches to
CarbonHotkey instances by hotkey id.

CarbonHotkey mirrors the surface flow.app.App uses from
flow.hotkey.HotkeyListener: constructor callbacks (on_activate/on_deactivate
for hold mode, on_trigger for tap mode), start() raising RuntimeError on
registration failure (feeding the #22 degraded/retry machinery), stop(),
wait_all_released(), reset_hold_state(), and the _name/_targets attributes
the failure reporting and tests read.

Semantics:
- hold mode: pressed -> on_activate, released -> on_deactivate.
- tap mode: on_trigger fires on RELEASED, never pressed, so the synthesized
  Cmd+V can never race the user's still-held combo — the same clean-release
  guarantee as the tap path. Carbon matches the modifier mask EXACTLY, so the
  #21 subset/superset false-fires cannot happen by construction.

Carbon handler callbacks fire on the main thread's run loop (the dispatcher
target), the same thread as the tap callbacks; user callbacks must return
quickly, exactly as with HotkeyListener.
"""

from __future__ import annotations

import threading
import time
import traceback
from struct import unpack
from typing import Callable

import objc

from flow._vendor.quickmachotkey._MinimalHIToolbox import (
    EventTypeSpec,
    GetEventDispatcherTarget,
    GetEventKind,
    GetEventParameter,
    InstallEventHandler,
    RegisterEventHotKey,
    UnregisterEventHotKey,
    kEventClassKeyboard,
    kEventHotKeyPressed,
    kEventHotKeyReleased,
    kEventParamDirectObject,
    typeEventHotKeyID,
)
from flow.hotkey import (
    _CHAR_KEYCODES,
    _MODIFIER_TOKENS,
    _NAMED_KEYCODES,
    _parse_key_name,
    modifiers_physically_down,
)

# Carbon modifier masks. Literals per the issue spec, asserted equal to the
# Carbon.framework constants exposed by the vendored bridge in
# tests/test_carbon_hotkey.py (cmdKey/shiftKey/optionKey/controlKey).
_CARBON_MODIFIER_MASKS = {
    "cmd": 0x100,     # cmdKey
    "shift": 0x200,   # shiftKey
    "alt": 0x800,     # optionKey
    "ctrl": 0x1000,   # controlKey
}

# Our EventHotKeyID signature ('TRDS' as a FourCharCode); events carrying any
# other signature belong to someone else's handler and are declined.
[_SIGNATURE] = unpack("@I", b"TRDS")

# OSStatus eventNotHandledErr: "not mine, keep looking".
_EVENT_NOT_HANDLED = -9874

# hotkey id -> CarbonHotkey, for the process-wide handler's dispatch.
_registry: dict[int, "CarbonHotkey"] = {}
_next_id = 0
# Strong references to the installed handler + callback (a GC'd Carbon
# callback crashes the process, same rule as the event tap's callback).
_handler_ref = None


def _allocate_id() -> int:
    global _next_id
    _next_id += 1
    return _next_id


# -- thin, monkeypatchable seams over the Carbon bridge -------------------------

def _register(vk: int, mask: int, hkid: int):
    """RegisterEventHotKey; returns (OSStatus, EventHotKeyRef|None)."""
    return RegisterEventHotKey(
        vk, mask, (_SIGNATURE, hkid), GetEventDispatcherTarget(), 0, None
    )


def _unregister(ref) -> int:
    return UnregisterEventHotKey(ref)


def _event_hotkey_id(event) -> tuple[int, int]:
    """The (signature, id) pair of a Carbon hotkey event. Raises on a
    GetEventParameter failure (the caller declines the event)."""
    result, _atype, _asize, param = GetEventParameter(
        event, kEventParamDirectObject, typeEventHotKeyID, None, 8, None, None
    )
    if result != 0:
        raise RuntimeError(f"GetEventParameter failed (OSStatus {result})")
    sig, hkid = unpack("@II", param)
    return sig, hkid


def _event_kind(event) -> int:
    return GetEventKind(event)


# -- the ONE process-wide Carbon event handler -----------------------------------

def _handle_carbon_event(event) -> int:
    """Body of the installed Carbon callback: route a pressed/released hotkey
    event to its CarbonHotkey. Events with a foreign signature — or whose
    parameters cannot be read — are declined (eventNotHandledErr) so another
    in-process handler can claim them; never swallowed."""
    try:
        sig, hkid = _event_hotkey_id(event)
    except Exception as exc:
        print(f"Carbon hotkey event unreadable ({exc}) — declined.")
        return _EVENT_NOT_HANDLED
    if sig != _SIGNATURE:
        return _EVENT_NOT_HANDLED
    try:
        _dispatch(hkid, _event_kind(event))
    except Exception as exc:
        # _dispatch guards per-hotkey; this is defense in depth — an exception
        # escaping a Carbon handler must never reach the run loop.
        print(f"Carbon hotkey dispatch error: {exc}\n{traceback.format_exc()}")
    return 0


@objc.callbackFor(InstallEventHandler)
def _carbon_callback(callref, event, void) -> int:
    return _handle_carbon_event(event)


def _ensure_handler() -> None:
    """Install the pressed+released handler once per process (idempotent).

    Upstream quickmachotkey registers kEventHotKeyPressed only; push-to-talk
    and the clean-release trigger both need kEventHotKeyReleased, hence the
    two EventTypeSpecs (the soffes/HotKey pattern). Raises RuntimeError on
    failure — start() propagates it into the #22 retry machinery.
    """
    global _handler_ref
    if _handler_ref is not None:
        return
    specs = [
        EventTypeSpec(eventClass=kEventClassKeyboard, eventKind=kEventHotKeyPressed),
        EventTypeSpec(eventClass=kEventClassKeyboard, eventKind=kEventHotKeyReleased),
    ]
    result, ref = InstallEventHandler(
        GetEventDispatcherTarget(), _carbon_callback, len(specs), specs, None, None
    )
    if result != 0 or ref is None:
        raise RuntimeError(
            f"Could not install the Carbon hotkey event handler "
            f"(OSStatus {result})."
        )
    _handler_ref = ref


def _dispatch(hkid: int, kind: int) -> None:
    """Route one pressed/released event to the owning CarbonHotkey (no-op for
    unknown ids — e.g. an event racing a stop()). Exceptions are logged with
    the hotkey's name, never propagated (they would kill the Carbon handler's
    run-loop dispatch)."""
    hotkey = _registry.get(hkid)
    if hotkey is None:
        return
    try:
        hotkey._handle(kind)
    except Exception as exc:
        print(
            f"[{hotkey._name}] Carbon hotkey callback error: {exc}\n"
            f"{traceback.format_exc()}"
        )


# -- combo classification ----------------------------------------------------------

def _split_tokens(keys: list[str]) -> tuple[list[str], list[str]]:
    """(modifier tokens, non-modifier tokens) in canonical form, DEDUPED in
    first-seen order: ["cmd","v","v"] IS the cmd+v chord — every listener
    holds its targets as a frozenset, so backend choice must not depend on
    token duplication (adversarial finding CADV-25). Raises ValueError for
    unknown or non-string key names (the same gate as HotkeyListener,
    honoring the documented ValueError contract — CADV-28b)."""
    tokens: list[str] = []
    for k in keys:
        if not isinstance(k, str):
            raise ValueError(f"Hotkey names must be strings, got {k!r}")
        token = _parse_key_name(k)
        if token not in tokens:
            tokens.append(token)
    mods = [t for t in tokens if t in _MODIFIER_TOKENS]
    others = [t for t in tokens if t not in _MODIFIER_TOKENS]
    return mods, others


def is_carbon_combo(keys: list[str]) -> bool:
    """True when this combo gets the Carbon backend: EXACTLY one non-modifier
    key plus at least one modifier (RegisterEventHotKey holds one virtual key
    and a modifier mask — nothing else). Modifier-only combos and exotic
    multi-character combos stay on the tap listener. Raises ValueError for
    unknown key names."""
    mods, others = _split_tokens(keys)
    return len(others) == 1 and len(mods) >= 1


def combo_backend_description(keys: list[str]) -> str:
    """One status-line sentence describing which backend a combo gets (shown
    by the settings window after recording). Never raises: the status line is
    decoration and must not break the recorder. A non-Carbon combo that is
    NOT modifier-only (multiple distinct character keys) must not be called
    modifier-only (adversarial finding CADV-26)."""
    try:
        mods, others = _split_tokens(keys)
    except Exception:
        return ""
    if len(others) == 1 and mods:
        return "Maximum-reliability shortcut (no permissions needed)."
    if others:
        return "Multi-key combo — uses the keyboard tap (needs Input Monitoring)."
    return "Modifier-only — uses the keyboard tap (needs Input Monitoring)."


# -- the backend --------------------------------------------------------------------

class CarbonHotkey:
    """One Carbon-registered global shortcut with HotkeyListener's surface.

    Modes, chosen exactly like HotkeyListener:
    - hold (on_activate/on_deactivate): pressed -> on_activate, released ->
      on_deactivate — push-to-talk.
    - tap (on_trigger): fires once, on RELEASED (see the module docstring for
      why never on pressed).

    start()/stop() are idempotent; start() raises RuntimeError when Carbon
    refuses the registration (e.g. another app owns the chord), which feeds
    App's #22 degraded-menu/watchdog-retry machinery unchanged.
    """

    def __init__(
        self,
        keys: list[str],
        on_activate: Callable[[], None] | None = None,
        on_deactivate: Callable[[], None] | None = None,
        on_trigger: Callable[[], None] | None = None,
        name: str | None = None,
        debug_label: str | None = None,
    ) -> None:
        mods, others = _split_tokens(keys)
        if len(others) != 1 or not mods:
            raise ValueError(
                "A Carbon hotkey needs exactly one non-modifier key plus at "
                f"least one modifier; got {keys!r}"
            )
        self._name = name or debug_label or "hotkey"
        self._debug = debug_label
        self._targets: frozenset[str] = frozenset(mods) | frozenset(others)
        self._mode = "tap" if on_trigger is not None else "hold"
        self._on_activate = on_activate or (lambda: None)
        self._on_deactivate = on_deactivate or (lambda: None)
        self._on_trigger = on_trigger or (lambda: None)
        key = others[0]
        self._vk = _NAMED_KEYCODES.get(key, _CHAR_KEYCODES.get(key))
        self._mask = 0
        for mod in mods:
            self._mask |= _CARBON_MODIFIER_MASKS[mod]
        # Registration state.
        self._ref = None
        self._hotkey_id: int | None = None
        # Chord-held shadow state (pressed but not yet released), guarded by
        # the condition so wait_all_released() can block on it.
        self._pressed = False
        self._cond = threading.Condition()

    # -- lifecycle -------------------------------------------------------------

    def start(self) -> None:
        """Install the process handler (first need) and register the hotkey.

        Idempotent. Raises RuntimeError on failure with nothing half-done, so
        the #22 watchdog can retry start() until it succeeds.
        """
        if self._ref is not None:
            return
        _ensure_handler()
        hkid = _allocate_id()
        result, ref = _register(self._vk, self._mask, hkid)
        if result != 0 or ref is None:
            raise RuntimeError(
                f"Could not register the {self._name} Carbon hotkey "
                f"(OSStatus {result}) — another app may own this shortcut."
            )
        self._hotkey_id = hkid
        self._ref = ref
        _registry[hkid] = self
        self._dbg(f"registered (vk={self._vk}, mask={hex(self._mask)})")

    def stop(self) -> None:
        """Unregister and forget the held state (idempotent, never raises).

        Removed from the dispatch registry FIRST, so an event racing the stop
        cannot fire a callback on a half-stopped hotkey. Like
        HotkeyListener.reset_hold_state, an ACTIVE hold-mode chord gets one
        balancing on_deactivate (ADV-15: stopping mid-dictation must stop the
        recording, not leave it running to max_seconds); tap mode only clears —
        synthesizing a trigger here would paste into whatever just happened.
        """
        ref = self._ref
        if ref is not None:
            if self._hotkey_id is not None:
                _registry.pop(self._hotkey_id, None)
            self._ref = None
            self._hotkey_id = None
            try:
                _unregister(ref)
            except Exception as exc:
                print(f"[{self._name}] Carbon hotkey unregister error: {exc}")
        self.reset_hold_state()

    def reset_hold_state(self) -> None:
        """Forget the pressed shadow state, waking wait_all_released()
        waiters; fire the balancing on_deactivate for an active hold."""
        with self._cond:
            fire_deactivate = self._pressed and self._mode == "hold"
            self._pressed = False
            self._cond.notify_all()
        if fire_deactivate:
            self._guarded(self._on_deactivate)

    # -- App's paste-guard surface ----------------------------------------------

    def wait_all_released(self, timeout: float = 2.0) -> bool:
        """Block until the chord is released AND no modifier is physically
        down; False on timeout or if the OS flag check fails.

        Carbon's released event fires as soon as the chord breaks — the user
        may still hold the modifiers, and a synthesized Cmd+V under a held
        Ctrl becomes ⌘⌃V (issue #24). So after the released event this also
        polls the OS's live modifier flags (CGEventSourceFlagsState — no TCC
        needed, works with Input Monitoring revoked). On an OS-check failure
        it returns False: App._released_or_stale then makes its own guarded
        decision, and a paste is never posted blind.
        """
        deadline = time.monotonic() + timeout
        with self._cond:
            while self._pressed:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._cond.wait(remaining)
        while True:
            try:
                if not modifiers_physically_down():
                    return True
            except Exception as exc:
                print(
                    f"[{self._name}] could not read the OS modifier state "
                    f"({exc}) — reporting keys still held."
                )
                return False
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.02)

    # -- event handling (main thread, from the Carbon handler) --------------------

    def _handle(self, kind: int) -> None:
        if kind == kEventHotKeyPressed:
            with self._cond:
                if self._pressed:
                    # Duplicate pressed (e.g. a missed released event): the
                    # chord is already accounted for — never double-activate.
                    return
                self._pressed = True
            self._dbg("pressed")
            if self._mode == "hold":
                self._guarded(self._on_activate)
        elif kind == kEventHotKeyReleased:
            with self._cond:
                was_pressed = self._pressed
                self._pressed = False
                self._cond.notify_all()
            if not was_pressed:
                # Released with no matching pressed (stale/racing event) —
                # firing here would be a phantom trigger.
                return
            self._dbg("released")
            if self._mode == "hold":
                self._guarded(self._on_deactivate)
            else:
                self._guarded(self._on_trigger)

    def _guarded(self, fn: Callable[[], None]) -> None:
        """Run a user callback, logging (never propagating) exceptions — the
        caller is a Carbon run-loop handler."""
        try:
            fn()
        except Exception as exc:
            print(
                f"[{self._name}] Carbon hotkey callback error: {exc}\n"
                f"{traceback.format_exc()}"
            )

    def _dbg(self, msg: str) -> None:
        if self._debug:
            print(f"[{self._debug}] carbon: {msg}")
