"""Menu bar UI, step-by-step permission onboarding, and the macOS main loop.

The status item is BOTH the app's "I am running" indicator and the entire
onboarding UI — no modal alerts, ever, and at launch the app NEVER fires a
system prompt or opens System Settings on its own: with permissions missing
it only shows the ⚠️ icon, logs the details, and posts ONE notification
pointing at the icon.

The menu walks the user through the three permissions ONE STEP AT A TIME, in
an order fixed by OS behavior (Microphone, then Accessibility, then Input
Monitoring — the only restart-requiring grant goes last). Exactly one row is
enabled: the current step (the first unsatisfied permission in registry
order, recomputed from a live snapshot every poll tick, so grants made
directly in System Settings — in any order — check steps off by themselves).
Clicking the current step fires ONLY that permission's own macOS prompt
(whose "Open System Settings" button handles navigation); a re-click while
still ungranted opens the matching Settings pane directly, since the OS
dialog will not re-show.

THE APP NEVER RESTARTS ITSELF. A 2 s poll re-checks the true state via a
fresh child process (in-process preflights go stale; 30 s once Ready). When
everything is granted it finishes IN-PROCESS: normal boot (model load +
hotkey start) on a worker thread. Only if the event tap still cannot be
created (macOS sometimes honors a fresh Input Monitoring grant only in a new
process) does the menu switch to a final state offering a USER-initiated
"Restart TRD Speak now" row. If the user instead accepted System Settings'
own "Quit & Reopen", the relaunched instance sees all-granted at startup and
boots straight to Ready — same convergence.

The app also shows in the Dock; clicking the Dock icon opens the status
item's menu.
"""

import os
import signal
import subprocess
import sys
import threading
from pathlib import Path

import AppKit
import Foundation
from Foundation import NSObject

from flow import engine_state, hotkey_state, paster, permissions
from flow.app import App
from flow.config import Config
from flow.engines import ENGINE_NAMES, ENGINES

LOG_PATH = os.path.expanduser("~/Library/Logs/trd-speak.log")

_STATE_ICONS = {
    "waiting": "⏳",
    "ready": "🎤",
    "recording": "🔴",
    "processing": "✍️",
    "loading": "⏳",
    "permissions": "⚠️",
}

_TOTAL = len(permissions.PERMISSIONS)


def _on_main(fn) -> None:
    """Run fn on the AppKit main thread (UI may only be touched there)."""
    Foundation.NSOperationQueue.mainQueue().addOperationWithBlock_(fn)


def _notify(message: str) -> None:
    """Post a macOS notification (never a modal)."""
    escaped = message.replace("\\", "\\\\").replace('"', '\\"')
    subprocess.Popen([
        "osascript", "-e",
        f'display notification "{escaped}" with title "TRD Speak"',
    ])


def _open_pane(anchor: str) -> None:
    """Open System Settings at a Privacy & Security anchor."""
    subprocess.Popen([
        "open",
        f"x-apple.systempreferences:com.apple.preference.security?{anchor}",
    ])


def _mic_ok(mic: str) -> bool:
    # "unknown" = AVFoundation unavailable, so the state is undetectable; it
    # must not block startup (macOS still prompts at the first recording).
    return mic in ("granted", "unknown")


def _perm_ok(key: str, snap: dict) -> bool:
    """Is the permission `key` satisfied in snapshot {listen, post, mic}?"""
    return _mic_ok(snap["mic"]) if key == "mic" else bool(snap[key])


def _missing(snap: dict) -> list:
    """The registry entries not yet satisfied, in onboarding order."""
    return [p for p in permissions.PERMISSIONS if not _perm_ok(p.key, snap)]


def _snapshot_inprocess() -> dict:
    return {
        "listen": permissions.can_listen(),
        "post": permissions.can_post(),
        "mic": permissions.mic_status(),
    }


def _snapshot_fresh() -> dict:
    """Permissions snapshot taken by a fresh child process.

    Preflight results inside a long-running process can be stale; a child
    inherits this app's TCC attribution but gets a fresh evaluation.
    """
    # The child uses ONLY ctypes — it must never import AVFoundation (or
    # anything loading Apple framework callback threads): a short-lived
    # process exiting with a live GCD thread crashes with "BUG IN CLIENT OF
    # LIBPTHREAD: pthread_exit()...". Mic state is read in-process instead;
    # AVCaptureDevice.authorizationStatusForMediaType_ is live, not cached.
    code = (
        "import ctypes;"
        "cg = ctypes.cdll.LoadLibrary("
        "'/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics');"
        "print(int(bool(cg.CGPreflightListenEventAccess())),"
        " int(bool(cg.CGPreflightPostEventAccess())))"
    )
    try:
        out = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True, timeout=15, text=True,
        ).stdout.split()
        return {
            "listen": bool(int(out[0])),
            "post": bool(int(out[1])),
            "mic": permissions.mic_status(),
        }
    except Exception:
        # Fall back to the in-process answer rather than wedging onboarding.
        return _snapshot_inprocess()


def _status_line(snap: dict) -> str:
    bits = []
    for perm in permissions.PERMISSIONS:
        if perm.key == "mic":
            bits.append(f"{perm.name}: {snap['mic']}")
        else:
            bits.append(f"{perm.name}: {'OK' if snap[perm.key] else 'MISSING'}")
    return "Permissions — " + ", ".join(bits)


def _set_dock_icon(nsapp) -> None:
    """Use the bundle's mic icon in the Dock (also works in ./run.sh mode)."""
    candidates = []
    bundle = os.environ.get("TRDSPEAK_BUNDLE")
    if bundle:
        candidates.append(Path(bundle))
    candidates.append(Path(__file__).resolve().parent.parent / "TRDSpeak.app")
    for candidate in candidates:
        icns = candidate / "Contents" / "Resources" / "AppIcon.icns"
        if icns.is_file():
            image = AppKit.NSImage.alloc().initWithContentsOfFile_(str(icns))
            if image:
                nsapp.setApplicationIconImage_(image)
            return


def _relaunch() -> None:
    """USER-initiated quit + fresh start (a new process picks up the grant).

    Never called automatically — only from the explicit "Restart TRD Speak
    now" menu row.
    """
    bundle = os.environ.get("TRDSPEAK_BUNDLE")
    if bundle:
        print("Restarting TRD Speak…")
        # The helper must wait for THIS process to fully die before reopening:
        # teardown can exceed any fixed sleep (the whisper model is loaded by
        # now), and main.py's single-instance flock is only released at true
        # process exit — open too early and the new instance sees "already
        # running" and quits, leaving nothing running.
        pid = os.getpid()
        subprocess.Popen(
            [
                "/bin/sh", "-c",
                f'while /bin/kill -0 {pid} 2>/dev/null; do sleep 0.2; done; '
                f'open "{bundle}"',
            ],
            start_new_session=True,
        )
    else:
        print("Quitting — rerun ./run.sh to finish setup.")
        _notify("Run ./run.sh again to finish setup.")
    AppKit.NSApplication.sharedApplication().terminate_(None)


class _Delegate(NSObject):
    """NSApplication delegate + retained target for all menu actions."""

    def openLog_(self, _sender) -> None:
        subprocess.Popen(["open", LOG_PATH])

    def grantPerm_(self, sender) -> None:
        """Current-step row clicked: fire ONLY that permission's prompt.

        First click fires the matching macOS dialog (its own "Open System
        Settings" button handles navigation, so we must NOT also open the
        pane ourselves). A re-click while still ungranted opens the pane
        directly — the OS dialog will not re-show.
        """
        key = str(sender.representedObject())
        if key == "mic":
            if permissions.mic_status() == "undetermined":
                print("Triggering the macOS Microphone prompt…")
                permissions.request_mic()  # native Allow/Deny; applies live
            else:
                # Denied (or unknown): only the Settings pane can fix it.
                print("Opening System Settings -> Microphone…")
                _open_pane("Privacy_Microphone")
            return
        perm = next(p for p in permissions.PERMISSIONS if p.key == key)
        requested = getattr(self, "_requested", None)
        if requested is None:
            requested = self._requested = set()
        if key not in requested:
            requested.add(key)
            print(f"Triggering the macOS {perm.name} prompt…")
            try:
                if key == "post":
                    permissions.request_post()
                else:
                    permissions.request_listen()
            except Exception:
                pass
        else:
            print(f"Opening System Settings -> {perm.name}…")
            _open_pane(perm.anchor)

    def restartApp_(self, _sender) -> None:
        """USER-initiated relaunch ("Restart TRD Speak now" row)."""
        _relaunch()

    def selectEngine_(self, sender) -> None:
        """Engine submenu row clicked: ask the app to switch engines."""
        name = str(sender.representedObject())
        logic = getattr(self, "logic", None)
        if logic is not None:
            logic.set_engine(name)

    def copyDictation_(self, sender) -> None:
        """Recent-dictations row clicked: copy its full text to the clipboard
        and LEAVE it there (no save/restore — the whole point is to make the
        text ready to paste into the right window)."""
        text = str(sender.representedObject())
        paster.set_clipboard(text)
        _notify("Copied — switch to your window and paste")

    def clearDictations_(self, _sender) -> None:
        """"Clear Recent Dictations" row clicked: wipe the history on demand."""
        logic = getattr(self, "logic", None)
        if logic is not None:
            logic.history.clear()

    def openConfig_(self, _sender) -> None:
        """Configuration… row clicked: lazily build and raise the settings
        window controller, keeping a strong reference (controllers/windows must
        not be GC'd)."""
        logic = getattr(self, "logic", None)
        menubar = getattr(self, "menubar", None)
        if logic is None or menubar is None:
            return
        controller = getattr(self, "_settings_controller", None)
        if controller is None:
            from flow.settings_window import SettingsWindowController

            controller = self._settings_controller = SettingsWindowController(
                logic, menubar
            )
        controller.open()

    def applicationShouldHandleReopen_hasVisibleWindows_(self, _app, _flag):
        # Dock icon clicked while running: open the status item's menu so
        # the Dock leads straight to the controls.
        menubar = getattr(self, "menubar", None)
        if menubar is not None:
            menubar.open_menu()
        return False


def _row_title(text: str, limit: int = 60) -> str:
    """One-line, truncated label for a Recent Dictations row.

    Newlines and runs of whitespace collapse to single spaces so the row stays
    on one line; the full text is preserved in the tooltip and copied verbatim.
    """
    flat = " ".join(text.split())
    if len(flat) > limit:
        return flat[:limit].rstrip() + "…"
    return flat


class _HistoryMenuDelegate(NSObject):
    """Rebuilds the Recent Dictations submenu on the main thread each time it
    opens, reading the thread-safe ``History`` store. The menu object is thus
    never mutated from a worker thread — only here, right before display. Set
    ``_target`` (the action ``_Delegate``) after init.
    """

    def menuNeedsUpdate_(self, menu) -> None:
        target = getattr(self, "_target", None)
        logic = getattr(target, "logic", None)
        history = getattr(logic, "history", None)
        items = history.items() if history is not None else []
        menu.removeAllItems()
        if items:
            for text in items:
                row = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                    _row_title(text), "copyDictation:", ""
                )
                row.setTarget_(target)
                row.setToolTip_(text)
                row.setRepresentedObject_(text)
                menu.addItem_(row)
        else:
            empty = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                "No dictations yet", None, ""
            )
            empty.setEnabled_(False)
            menu.addItem_(empty)
        menu.addItem_(AppKit.NSMenuItem.separatorItem())
        clear = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Clear Recent Dictations", "clearDictations:", ""
        )
        clear.setTarget_(target)
        clear.setEnabled_(bool(items))
        menu.addItem_(clear)


class MenuBar:
    """The status item, its menu, and all rendering (icon + permission rows)."""

    def __init__(self, combo: str, delegate: _Delegate) -> None:
        self._combo = combo
        self._delegate = delegate  # keep the action target alive forever
        self._app_state = "waiting"
        self._app_detail = "Starting…"
        self._missing_keys: tuple = ()
        self._mic_status = ""
        self._restart_needed = False
        self._active_engine = ""

        self._item = AppKit.NSStatusBar.systemStatusBar().statusItemWithLength_(
            AppKit.NSVariableStatusItemLength
        )
        self._item.button().setTitle_(_STATE_ICONS["waiting"])

        menu = AppKit.NSMenu.alloc().init()
        menu.setAutoenablesItems_(False)

        self._header = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Starting…", None, ""
        )
        self._header.setEnabled_(False)
        menu.addItem_(self._header)
        menu.addItem_(AppKit.NSMenuItem.separatorItem())

        # One row per permission; hidden while everything is granted.
        self._perm_items: dict = {}
        for perm in permissions.PERMISSIONS:
            item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                f"Grant {perm.name} permission…", "grantPerm:", ""
            )
            item.setTarget_(delegate)
            item.setRepresentedObject_(perm.key)
            item.setHidden_(True)
            menu.addItem_(item)
            self._perm_items[perm.key] = item

        # Final-state row: shown only when a fresh Input Monitoring grant
        # needs a new process (the user clicks — the app never auto-restarts).
        self._restart_item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Restart TRD Speak now", "restartApp:", ""
        )
        self._restart_item.setTarget_(delegate)
        self._restart_item.setHidden_(True)
        menu.addItem_(self._restart_item)

        self._perm_separator = AppKit.NSMenuItem.separatorItem()
        self._perm_separator.setHidden_(True)
        menu.addItem_(self._perm_separator)

        # Recent Dictations submenu (above the engine picker). Populated lazily
        # by its delegate on open, so it is always fresh and only ever mutated
        # on the main thread.
        self._history_root = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Recent Dictations", None, ""
        )
        history_menu = AppKit.NSMenu.alloc().init()
        history_menu.setAutoenablesItems_(False)
        # NSMenu holds its delegate weakly — keep a strong ref for the app's life.
        self._history_delegate = _HistoryMenuDelegate.alloc().init()
        self._history_delegate._target = delegate
        history_menu.setDelegate_(self._history_delegate)
        self._history_root.setSubmenu_(history_menu)
        menu.addItem_(self._history_root)

        # "Configuration…" row: opens the settings window. Same visibility
        # gate as the history/engine rows (normal fully-granted state only).
        self._config_item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Configuration…", "openConfig:", "")
        self._config_item.setTarget_(delegate)
        self._config_item.setHidden_(True)
        menu.addItem_(self._config_item)

        # Transcription engine picker (registry-driven).
        self._engine_root = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Transcription Engine", None, ""
        )
        engine_menu = AppKit.NSMenu.alloc().init()
        engine_menu.setAutoenablesItems_(False)
        self._engine_items: dict = {}
        for info in ENGINES:
            item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                info.label, "selectEngine:", ""
            )
            item.setTarget_(delegate)
            item.setRepresentedObject_(info.name)
            item.setToolTip_(info.description)
            engine_menu.addItem_(item)
            self._engine_items[info.name] = item
        self._engine_root.setSubmenu_(engine_menu)
        menu.addItem_(self._engine_root)
        self._engine_separator = AppKit.NSMenuItem.separatorItem()
        menu.addItem_(self._engine_separator)

        log_item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Open Log", "openLog:", ""
        )
        log_item.setTarget_(delegate)
        menu.addItem_(log_item)

        quit_item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Quit TRD Speak", "terminate:", "q"
        )
        quit_item.setTarget_(AppKit.NSApplication.sharedApplication())
        menu.addItem_(quit_item)

        self._item.setMenu_(menu)

    def open_menu(self) -> None:
        """Pop the status item's menu open (main thread only)."""
        self._item.button().performClick_(None)

    def set_state(self, state: str, detail: str = "") -> None:
        """Thread-safe app-state update (waiting/ready/recording/processing)."""
        self._app_state = state
        self._app_detail = detail
        _on_main(self._render)

    def update_permissions(self, snap: dict) -> None:
        """Thread-safe re-render of the permission rows from a snapshot
        {listen: bool, post: bool, mic: str}."""
        self._missing_keys = tuple(p.key for p in _missing(snap))
        self._mic_status = snap["mic"]
        _on_main(self._render)

    def set_restart_needed(self) -> None:
        """Thread-safe switch to the final "restart to finish" menu state
        (all permissions granted but the event tap needs a fresh process)."""
        self._restart_needed = True
        _on_main(self._render)

    def update_engine(self, active_name: str) -> None:
        """Thread-safe: tick the active engine and refresh enabled state."""
        self._active_engine = active_name
        _on_main(self._render)

    def update_combo(self, dictate_keys: list[str], repaste_keys: list[str]) -> None:
        """Refresh the header's dictate combo ("Ready — hold … to dictate")
        after a shortcut change. Re-renders on the main thread.

        Only self._combo (the dictate combo in the "ready" header text) needs
        updating — the re-paste combo is not shown in the header. The
        repaste_keys parameter is kept for a stable, future-proof call site.
        """
        self._combo = "+".join(dictate_keys)
        _on_main(self._render)

    def _render(self) -> None:
        """Re-render icon, header, and onboarding rows (main thread only).

        Three exclusive top states:
          - "restart to finish": all granted, but the event tap needs a new
            process — one enabled row, USER-initiated restart;
          - onboarding: header "Setup — step N of 3: <Name>"; exactly one
            enabled row (the current step = first unsatisfied permission in
            registry order); done steps "✓ <Name>", future steps disabled;
          - normal app state (waiting/ready/recording/processing).
        """
        missing = self._missing_keys
        mic_unknown = self._mic_status == "unknown"
        # A revocation during the restart-offer brings the step UI back.
        restart = self._restart_needed and not missing
        if restart:
            self._item.button().setTitle_(_STATE_ICONS["permissions"])
            self._header.setTitle_("Setup complete — restart TRD Speak to finish")
        elif missing:
            current = next(
                p for p in permissions.PERMISSIONS if p.key == missing[0]
            )
            step_no = (
                [p.key for p in permissions.PERMISSIONS].index(current.key) + 1
            )
            self._item.button().setTitle_(_STATE_ICONS["permissions"])
            self._header.setTitle_(
                f"Setup — step {step_no} of {_TOTAL}: {current.name}"
            )
        else:
            texts = {
                "waiting": self._app_detail or "Starting…",
                "ready": f"Ready — hold {self._combo} to dictate",
                "recording": "Recording… release to transcribe",
                "processing": "Transcribing…",
                "loading": "Switching engine…",
            }
            self._item.button().setTitle_(
                _STATE_ICONS.get(self._app_state, _STATE_ICONS["ready"])
            )
            self._header.setTitle_(
                texts.get(self._app_state, self._app_detail or self._app_state)
            )
        self._restart_item.setHidden_(not restart)
        for step_no, perm in enumerate(permissions.PERMISSIONS, 1):
            item = self._perm_items[perm.key]
            if restart:
                item.setHidden_(True)
            elif missing and perm.key == missing[0]:
                # The current step — the ONLY enabled action row.
                item.setHidden_(False)
                item.setEnabled_(True)
                item.setTitle_(f"Step {step_no}: Grant {perm.name}…")
            elif perm.key in missing:
                # A future step: visible but locked until its turn.
                item.setHidden_(False)
                item.setEnabled_(False)
                item.setTitle_(
                    f"Step {step_no}: {perm.name} (after step {step_no - 1})"
                )
            elif perm.key == "mic" and mic_unknown:
                # AVFoundation is unavailable, so the true mic state is
                # undetectable. It must not block the steps, but keep a
                # clickable informational row that opens the Microphone pane
                # (grantPerm_ falls through to the pane for non-undetermined).
                item.setHidden_(False)
                item.setEnabled_(True)
                item.setTitle_("Microphone: status unknown — open Settings…")
            elif missing:
                item.setHidden_(False)
                item.setEnabled_(False)
                item.setTitle_(f"✓ {perm.name}")
            else:
                item.setHidden_(True)
        self._perm_separator.setHidden_(not (missing or mic_unknown or restart))
        # Engine picker: visible only in the normal (fully granted) state; the
        # active engine is checked; all rows disabled unless idle/ready.
        show_engine = not (missing or mic_unknown or restart)
        self._history_root.setHidden_(not show_engine)
        self._config_item.setHidden_(not show_engine)
        self._engine_root.setHidden_(not show_engine)
        self._engine_separator.setHidden_(not show_engine)
        ready = self._app_state == "ready"
        for name, item in self._engine_items.items():
            on = (
                AppKit.NSControlStateValueOn
                if name == self._active_engine
                else AppKit.NSControlStateValueOff
            )
            item.setState_(on)
            item.setEnabled_(ready)


def run(config: Config) -> None:
    """Set up the Dock + menu bar app and block in the AppKit main loop."""
    nsapp = AppKit.NSApplication.sharedApplication()
    nsapp.setActivationPolicy_(AppKit.NSApplicationActivationPolicyRegular)
    _set_dock_icon(nsapp)
    delegate = _Delegate.alloc().init()
    nsapp.setDelegate_(delegate)

    # Opt out of App Nap. A window-less, background menu-bar app gets
    # throttled/suspended by macOS after sitting idle in the background, which
    # silently stalls the keyboard event tap — the hotkey "stops working after
    # a while". Hold a process-activity assertion for the whole lifetime (kept
    # alive on the delegate). The "AllowingIdleSystemSleep" variant blocks App
    # Nap but still lets the Mac sleep normally.
    delegate._app_nap = Foundation.NSProcessInfo.processInfo().beginActivityWithOptions_reason_(
        Foundation.NSActivityUserInitiatedAllowingIdleSystemSleep,
        "TRD Speak push-to-talk hotkey must keep receiving key events",
    )

    # The settings-window/menu choice (App Support JSON) takes precedence over
    # config.toml, per-combo. Resolve BEFORE the combo display string and the
    # App build so both reflect the saved shortcuts.
    config.keys, config.repaste_keys = hotkey_state.resolve(config)
    combo = "+".join(config.keys)
    ui = MenuBar(combo, delegate)
    delegate.menubar = ui  # for applicationShouldHandleReopen
    # The menu-bar choice (state file) takes precedence over config.toml.
    config.engine = engine_state.resolve_engine(config.engine, ENGINE_NAMES)
    logic = App(config)
    logic.on_state = ui.set_state
    logic.on_engine = ui.update_engine
    logic.notify = _notify
    delegate.logic = logic
    ui.update_engine(logic.engine_name)

    # This process is freshly started, so the in-process answer is fresh.
    snap = _snapshot_inprocess()
    ui.update_permissions(snap)
    print(_status_line(snap))

    state = {
        "booting": False,  # boot thread launched and still running
        "boot_ok": False,  # logic.start() returned: hotkey live, app Ready
        "boot_failed": False,  # hotkey.start() raised: a new process is needed
        "was_missing": bool(_missing(snap)),
        "ticks": 0,
        "polling": False,
        "timer_fires": 0,
        "post_ok": bool(snap["post"]),
    }
    # The paste guard must use the freshest known answer, not the in-process
    # preflight macOS caches at first call (a mid-session grant would
    # otherwise read False forever and every paste would be refused).
    logic.can_paste = lambda: state["post_ok"]

    def boot() -> None:
        """Attempt the normal in-process boot (model load + hotkey start)."""
        state["booting"] = True
        ui.set_state("waiting", f"Loading {config.engine} engine…")

        def work() -> None:  # worker thread: no direct UI access
            try:
                logic.start()  # reports Ready via on_state -> set_state
            except Exception as exc:
                # macOS sometimes honors a fresh Input Monitoring grant only
                # in a new process. NEVER restart automatically — offer a
                # user-initiated restart row instead.
                print(f"Could not start the hotkey listener in-process: {exc}")
                print(
                    "Setup is complete, but macOS will only honor the new "
                    "Input Monitoring grant in a fresh process — click "
                    "\"Restart TRD Speak now\" in the ⚠️ menu."
                )
                state["boot_failed"] = True
                ui.set_restart_needed()  # thread-safe (_on_main inside)
                _notify(
                    "Setup complete — click the ⚠️ icon and choose "
                    "\"Restart TRD Speak now\" to finish."
                )
            else:
                state["boot_ok"] = True
            finally:
                state["booting"] = False

        threading.Thread(target=work, daemon=True).start()

    if not _missing(snap):
        boot()
    else:
        # At launch the app initiates NOTHING on screen: no system prompts,
        # no Settings panes, no dialogs — only the ⚠️ icon, the log lines,
        # and this single notification. The menu is the onboarding.
        permissions.report()  # print-only
        _notify("TRD Speak needs setup — click the ⚠️ icon in the menu bar.")

    def poll(_timer) -> None:
        state["timer_fires"] += 1
        # Event-tap watchdog: if macOS disabled the hotkey tap (a slow
        # callback trips its timeout), re-assert it. Runs every tick (before
        # the throttle below) so the hotkey recovers within ~2 s.
        if state["boot_ok"]:
            try:
                if logic.hotkey.ensure_enabled():
                    print("Hotkey tap had been disabled — re-enabled by watchdog.")
                if logic.repaste_hotkey.ensure_enabled():
                    print("Re-paste tap had been disabled — re-enabled by watchdog.")
                # Liveness heartbeat (~30 s): a long run of zeros while the app
                # is in use means the tap has gone silent — direct evidence of
                # the "stops after a while" freeze, no keystrokes logged.
                if state["timer_fires"] % 15 == 0:
                    n = logic.hotkey.take_event_count()
                    m = logic.repaste_hotkey.take_event_count()
                    print(
                        f"Hotkey tap heartbeat: {n} events "
                        f"(re-paste tap: {m}) in the last ~30 s."
                    )
            except Exception as exc:
                print(f"Hotkey watchdog error: {exc}")
        # 2 s while anything is missing or the boot has not finished; back
        # off to ~30 s once Ready (still catches a user revoking a grant,
        # without a helper process every 2 s forever).
        ready_idle = state["boot_ok"] and not state["was_missing"]
        if ready_idle and state["timer_fires"] % 15 != 0:
            return
        if state["polling"]:  # a slow check is still in flight — skip a tick
            return
        state["polling"] = True

        def check() -> None:  # worker thread: no UI here
            fresh = _snapshot_fresh()

            def apply() -> None:  # main thread
                state["polling"] = False
                state["ticks"] += 1
                state["post_ok"] = bool(fresh["post"])
                missing = _missing(fresh)
                changed = bool(missing) != state["was_missing"]
                heartbeat = (  # the ~10 s line while onboarding
                    (missing or state["was_missing"]) and state["ticks"] % 5 == 1
                )
                if changed or heartbeat:
                    print(_status_line(fresh))
                ui.update_permissions(fresh)
                if not missing and not (
                    state["booting"] or state["boot_ok"] or state["boot_failed"]
                ):
                    # Everything granted: finish IN-PROCESS — no restart. If
                    # the hotkey tap still cannot start, boot() switches the
                    # menu to the user-initiated "Restart TRD Speak now" state.
                    boot()
                state["was_missing"] = bool(missing)

            _on_main(apply)

        threading.Thread(target=check, daemon=True).start()

    Foundation.NSTimer.scheduledTimerWithTimeInterval_repeats_block_(2.0, True, poll)

    # NSApp's run loop starves Python signal handlers; restore the default
    # so Ctrl+C in terminal mode still kills the process.
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    nsapp.run()
