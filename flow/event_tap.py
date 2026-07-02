"""One hardened keyboard event tap shared by every hotkey listener (issue #20).

The app used to run one CGEventTap per HotkeyListener — three independent
session taps (dictate, re-paste, correct), each a separate mach port that
macOS could disable, starve, or silently kill on its own. Mature hotkey tools
(Hammerspoon, skhd) run ONE tap and dispatch internally; EventTapHub does the
same: a single listen-only, HEAD-INSERT session tap on the main run loop that
forwards every keyboard event to all registered listeners.

Battle-tested constraints carried over from flow.hotkey (do not "simplify"):

- HEAD-INSERT, never tail-append: a tail tap sits at the END of the session
  chain, so an upstream active tap that claims a ⌘-combo deletes the event
  before we see it — Command shortcuts silently die depending on the
  registration order of every other tap on the machine.
- Listen-only: we observe, never swallow, the user's keystrokes.
- The run-loop source is added to CFRunLoopGetMain() in kCFRunLoopCommonModes
  and the run loop is woken, so callbacks fire on the main thread even when
  the tap is created from a worker thread (App.start runs off-main).
- Strong Python references to the tap, source, and callback live on the hub;
  if any is garbage-collected the process crashes.
- kCGEventTapDisabledByTimeout / ByUserInput are handled in the callback by
  re-enabling the tap; they are tap lifecycle, never forwarded to listeners.

Self-healing (the watchdog contract, driven by flow.menubar's 2 s poll):

- watchdog_tick(): a disabled tap is re-enabled; if the re-enable did not
  stick by the NEXT tick ("a non-nil tap is not a healthy tap" — the
  silent-disable race), the tap is destroyed and recreated from scratch. A
  missing tap with listeners still registered (a transiently failed recreate)
  is also recreated.
- NSWorkspaceDidWakeNotification and NSWorkspaceSessionDidBecomeActiveNotification
  recreate the tap: taps die across sleep/wake and fast user switching.
- The liveness heartbeat counter lives here (one counter for the one tap).
  Deliberate non-trigger: "0 events for N heartbeats" alone does NOT force a
  recreate — silence is indistinguishable from an idle user without extra
  signals, and the disabled-state check above already recreates a dead tap
  within two ticks. The heartbeat stays a diagnostic log line only.

mute()/unmute() replace the old destroy-everything suspend: while a
settings/correction window is open the tap KEEPS running (zero tap
create/destroy on window open/close — every recreation was a fresh chance to
fail) and dispatch is simply gated. On unmute every listener's per-hold
shadow state is reset, so combos pressed while muted (e.g. recorded in the
settings window) can never phantom-fire on release (the #21 per-hold
semantics).

AppKit is imported lazily (observers only), so importing this module — and
therefore flow.hotkey / flow.app — still needs only Quartz.
"""

from __future__ import annotations

import traceback

import Quartz


def _workspace_notification_center():
    """The shared NSWorkspace notification center (lazy AppKit import so the
    module stays importable without AppKit; tests monkeypatch this)."""
    import AppKit

    return AppKit.NSWorkspace.sharedWorkspace().notificationCenter()


def _wake_notification_names() -> tuple:
    """The notifications after which the tap must be rebuilt (sleep/wake and
    fast-user-switch session reactivation)."""
    import AppKit

    return (
        AppKit.NSWorkspaceDidWakeNotification,
        AppKit.NSWorkspaceSessionDidBecomeActiveNotification,
    )


def _main_queue():
    """NSOperationQueue.mainQueue() — observer blocks must run on the main
    thread (the same thread as the tap callbacks and the menu poll)."""
    import Foundation

    return Foundation.NSOperationQueue.mainQueue()


class EventTapHub:
    """Owns THE keyboard event tap and dispatches its events to listeners.

    Listeners are objects exposing ``_tap_callback(proxy, event_type, event,
    refcon)`` (the HotkeyListener matching state machine) and
    ``reset_hold_state()``. Registration is idempotent and identity-based.

    Thread notes: register/unregister run on the main thread or the boot
    worker; dispatch runs on the main thread (the tap's run loop). Dispatch
    iterates a snapshot, so a listener may unregister itself mid-dispatch.
    """

    def __init__(self) -> None:
        self._listeners: list = []
        self._muted = False
        # Strong references to the tap machinery (see module docstring).
        self._tap = None
        self._source = None
        self._callback = None
        # Liveness counter: how many events the tap delivered since the last
        # take_event_count(). Counts THAT events arrive, never which keys.
        self._event_count = 0
        # True when the previous watchdog_tick already re-enabled a disabled
        # tap: if it is STILL disabled now, the re-enable did not stick.
        self._reenabled_last_tick = False
        # Strong refs to the NSWorkspace observer tokens and their blocks.
        self._observer_tokens: list = []
        self._observer_blocks: list = []

    # -- registration -------------------------------------------------------

    def register(self, listener) -> None:
        """Add a listener, creating the single tap on first need.

        Raises RuntimeError (from create()) if the tap cannot be created —
        the caller's failure isolation (#22) and the boot "Restart TRD Speak
        now" flow depend on that propagating. On a raise the listener is NOT
        registered, so a later retry re-attempts the create.
        """
        if any(existing is listener for existing in self._listeners):
            return
        self.create()
        self._listeners.append(listener)

    def unregister(self, listener) -> None:
        """Remove a listener. The tap deliberately stays alive: it is one
        per process, and destroying/recreating it is exactly the failure
        surface this hub exists to remove. No-op for unknown listeners."""
        self._listeners = [x for x in self._listeners if x is not listener]

    # -- tap lifecycle -------------------------------------------------------

    def create(self) -> None:
        """Create and enable the tap on the main run loop (idempotent).

        Raises RuntimeError if CGEventTapCreate returns None (Input
        Monitoring permission missing or revoked).
        """
        if self._tap is not None:
            return
        callback = self._tap_callback  # keep a strong reference via self
        mask = (
            Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown)
            | Quartz.CGEventMaskBit(Quartz.kCGEventKeyUp)
            | Quartz.CGEventMaskBit(Quartz.kCGEventFlagsChanged)
        )
        tap = Quartz.CGEventTapCreate(
            Quartz.kCGSessionEventTap,
            Quartz.kCGHeadInsertEventTap,  # MANDATORY — see module docstring
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
        self._install_system_observers()

    def destroy(self) -> None:
        """Disable and tear down the tap (idempotent). Listener registrations
        survive, so recreate()/watchdog_tick() can bring delivery back. Only
        App.shutdown() calls this as a final teardown (after unregistering
        every listener, so the watchdog cannot resurrect the tap)."""
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
            print(f"Event tap destroy error: {exc}")
        self._tap = None
        self._source = None
        self._callback = None

    def recreate(self) -> bool:
        """Destroy the current tap and build a fresh one (mute state and
        registrations preserved). Returns True on success; False when there
        is no tap to recreate or the create failed (logged — watchdog_tick
        keeps retrying while listeners are registered)."""
        if self._tap is None:
            return False
        self.destroy()
        try:
            self.create()
        except Exception as exc:
            print(f"Could not recreate the keyboard event tap: {exc}")
            return False
        return True

    def is_enabled(self) -> bool:
        """True when the tap exists and macOS reports it enabled."""
        return self._tap is not None and bool(Quartz.CGEventTapIsEnabled(self._tap))

    def ensure_enabled(self) -> bool:
        """Re-assert the tap if macOS disabled it. Returns True if a re-enable
        was needed, False if healthy or no tap exists."""
        if self._tap is None:
            return False
        if not Quartz.CGEventTapIsEnabled(self._tap):
            Quartz.CGEventTapEnable(self._tap, True)
            return True
        return False

    def watchdog_tick(self) -> str | None:
        """One 2 s watchdog pass. Returns what recovery ran, if any:

        - "re-enabled": the tap was disabled; CGEventTapEnable(True) was
          re-asserted (the cheap, usual fix);
        - "recreated": the previous tick's re-enable did not stick, or the
          tap was missing entirely (failed recreate) — rebuilt from scratch;
        - None: healthy, nothing to watch, or a recreate attempt failed
          (logged; retried next tick).
        """
        if self._tap is None:
            if not self._listeners:
                return None
            # A recreate failed earlier: keep trying while listeners exist.
            try:
                self.create()
            except Exception as exc:
                print(f"Event tap watchdog: recreate still failing ({exc}).")
                return None
            self._reenabled_last_tick = False
            return "recreated"
        if Quartz.CGEventTapIsEnabled(self._tap):
            self._reenabled_last_tick = False
            return None
        if not self._reenabled_last_tick:
            Quartz.CGEventTapEnable(self._tap, True)
            self._reenabled_last_tick = True
            return "re-enabled"
        # Re-enabled last tick and STILL disabled: the re-enable did not
        # stick — a full rebuild is the only fix (see module docstring).
        self._reenabled_last_tick = False
        return "recreated" if self.recreate() else None

    # -- mute / unmute -------------------------------------------------------

    def mute(self) -> None:
        """Gate dispatch (settings/correction window open) WITHOUT touching
        the tap. The window's local NSEvent monitor is then the only combo
        listener; the tap keeps running and staying healthy."""
        self._muted = True

    def unmute(self) -> None:
        """Resume dispatch and reset every listener's per-hold shadow state:
        keys pressed while muted were never seen, so any pre-mute state is
        stale and must not phantom-fire on the next release (#21 per-hold
        semantics). Each reset is individually guarded."""
        self._muted = False
        for listener in tuple(self._listeners):
            try:
                listener.reset_hold_state()
            except Exception as exc:
                name = getattr(listener, "_name", "listener")
                print(
                    f"[{name}] unmute reset error: {exc}\n"
                    f"{traceback.format_exc()}"
                )

    # -- heartbeat ------------------------------------------------------------

    def take_event_count(self) -> int:
        """Events seen since the last call (then reset). A long run of zeros
        while the app is in use means the tap has gone silent — diagnostic
        only, see the module docstring for why it does not trigger recovery."""
        count = self._event_count
        self._event_count = 0
        return count

    # -- the tap callback (runs on the main thread) ---------------------------

    def _tap_callback(self, proxy, event_type, event, refcon):
        # An exception escaping a tap callback kills the tap silently, so the
        # entire body is guarded; each listener is guarded individually too.
        try:
            self._event_count += 1
            if event_type in (
                Quartz.kCGEventTapDisabledByTimeout,
                Quartz.kCGEventTapDisabledByUserInput,
            ):
                if self._tap is not None:
                    Quartz.CGEventTapEnable(self._tap, True)
                    print(
                        "Keyboard event tap was disabled by the system — "
                        "re-enabled it."
                    )
                return event
            if self._muted:
                return event
            # Snapshot: a listener may unregister itself mid-dispatch.
            for listener in tuple(self._listeners):
                try:
                    listener._tap_callback(proxy, event_type, event, refcon)
                except Exception as exc:
                    # One raising listener must not starve the others (#21).
                    name = getattr(listener, "_name", "listener")
                    print(
                        f"[{name}] hub dispatch error: {exc}\n"
                        f"{traceback.format_exc()}"
                    )
        except Exception as exc:
            print(f"Event tap hub callback error: {exc}\n{traceback.format_exc()}")
        return event

    # -- wake / session observers ---------------------------------------------

    def _install_system_observers(self) -> None:
        """Recreate the tap after sleep/wake and session reactivation (taps
        die across both). Installed once, on the first successful create();
        best-effort — a failure must never take the tap down. Block-based
        observers avoid declaring an NSObject subclass (PyObjC class names
        are process-global in this codebase; see tests/test_gui_imports.py)."""
        if self._observer_tokens:
            return
        try:
            center = _workspace_notification_center()
            names = _wake_notification_names()
            queue = _main_queue()
        except Exception as exc:
            print(f"Wake/session observers unavailable ({exc}); "
                  "the tap will not auto-rebuild after sleep.")
            return

        def _on_system_event(_notification) -> None:
            print("System wake/session-active — recreating the keyboard event tap.")
            try:
                self.recreate()
            except Exception as exc:  # recreate never raises, but stay safe
                print(f"Event tap rebuild after wake failed: {exc}")

        for name in names:
            try:
                token = center.addObserverForName_object_queue_usingBlock_(
                    name, None, queue, _on_system_event
                )
            except Exception as exc:
                print(f"Could not observe {name}: {exc}")
                continue
            # Strong refs: a GC'd token silently removes the observer.
            self._observer_tokens.append(token)
            self._observer_blocks.append(_on_system_event)
