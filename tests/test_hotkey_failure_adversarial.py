"""Adversarial tests for hotkey start-failure isolation (issue #22).

Asserts the INTENDED contract (not current behaviour) under hostile
interleavings: suspend/resume cycles while degraded, double suspend, resume
twice, set_hotkeys while suspended/degraded, hooks that raise or vanish
mid-flight, stop() raising during suspend, non-RuntimeError exceptions,
fail-recover-fail cycles, boot failures, combined watchdog work in one tick,
and the MenuBar warning-row rendering states.

Same fixture pattern as tests/test_app_hotkey_failures.py (unit) and
tests/test_hotkey_failure_functional.py (functional): HotkeyListener.start
and stop are ALWAYS monkeypatched — no real Quartz event tap is ever created
and the user's machine configuration is never touched.
"""

import pytest

pytest.importorskip("AppKit")

import flow.app as app_mod
import flow.settings_window as settings_mod
from flow import menubar, permissions
from flow.app import App
from flow.config import Config
from flow.menubar import MenuBar, _Delegate


# ---------------------------------------------------------------------------
# Fixtures (mirroring the existing test files)
# ---------------------------------------------------------------------------

@pytest.fixture
def app(monkeypatch, tmp_path):
    monkeypatch.setattr(app_mod.HotkeyListener, "start", lambda self: None)
    monkeypatch.setattr(app_mod.HotkeyListener, "stop", lambda self: None)
    monkeypatch.setattr(
        app_mod.engine_state,
        "save_engine",
        lambda name, path=tmp_path / "engine": path.write_text(name),
    )
    return App(Config())


def _instrument(monkeypatch, app, failing_names, exc_factory=None):
    """start() records successes by listener name and raises for names in the
    mutable `failing_names` set. exc_factory(name) customizes the exception."""
    started = []
    make = exc_factory or (lambda n: RuntimeError(f"tap create failed for {n}"))

    def fake_start(self):
        if self._name in failing_names:
            raise make(self._name)
        started.append(self._name)

    monkeypatch.setattr(app_mod.HotkeyListener, "start", fake_start)
    reported = []
    app.on_hotkeys_degraded = lambda labels: reported.append(tuple(labels))
    return started, reported


@pytest.fixture
def rig(monkeypatch, tmp_path):
    """Real App + real MenuBar wired together (functional harness)."""
    started: list[str] = []
    failing: set[str] = set()

    def fake_start(self):
        if self._name in failing:
            raise RuntimeError(f"CGEventTapCreate returned None ({self._name})")
        started.append(self._name)

    monkeypatch.setattr(app_mod.HotkeyListener, "start", fake_start)
    monkeypatch.setattr(app_mod.HotkeyListener, "stop", lambda self: None)
    monkeypatch.setattr(
        app_mod.engine_state,
        "save_engine",
        lambda name, path=tmp_path / "engine": path.write_text(name),
    )
    monkeypatch.setattr(
        settings_mod.hotkey_state,
        "save",
        lambda d, r, c, path=None: (tmp_path / "hotkeys.json").write_text("saved"),
    )
    app = App(Config())
    ui = MenuBar("+".join(app.config.keys), _Delegate.alloc().init())
    app.on_hotkeys_degraded = ui.update_hotkey_failures
    return app, ui, started, failing


def _warning_row(ui):
    ui._render()
    return bool(ui._hotkey_warning.isHidden()), str(ui._hotkey_warning.title())


# ---------------------------------------------------------------------------
# Interleavings: suspend / resume / set_hotkeys / watchdog
# ---------------------------------------------------------------------------

def test_adv01_double_suspend_then_resume_is_safe(app, monkeypatch):
    """suspend called twice (settings opens over correction) must stay
    suspended, keep the watchdog silent, and resume cleanly once."""
    started, reported = _instrument(monkeypatch, app, set())
    app.suspend_hotkeys()
    app.suspend_hotkeys()  # must not raise
    assert menubar.reenable_disabled_taps(app) == []
    assert started == []

    app.resume_hotkeys()
    assert started == ["dictation", "re-paste", "correction"]
    assert reported[-1] == ()


def test_adv02_resume_twice_reports_each_time_and_never_raises(app, monkeypatch):
    """resume_hotkeys twice in a row (double windowWillClose) must not raise
    and must report the failure set on EVERY attempt."""
    failing = {"dictation"}
    started, reported = _instrument(monkeypatch, app, failing)
    app.resume_hotkeys()
    app.resume_hotkeys()
    assert reported == [("Hotkey",), ("Hotkey",)]
    # Both attempts tried the healthy listeners too.
    assert started == ["re-paste", "correction", "re-paste", "correction"]


def test_adv03_suspend_while_degraded_keeps_watchdog_silent_even_after_heal(
    app, monkeypatch
):
    """Degraded -> suspend -> heal: the watchdog must NOT resurrect the healed
    listener while suspended; resume recovers it and clears the report."""
    failing = {"re-paste"}
    started, reported = _instrument(monkeypatch, app, failing)
    app.resume_hotkeys()
    assert reported[-1] == ("Re-paste",)
    app.suspend_hotkeys()
    failing.clear()
    started.clear()

    for _ in range(3):  # several 2 s ticks while the window is open
        assert menubar.reenable_disabled_taps(app) == []
    assert started == []

    app.resume_hotkeys()
    assert started == ["dictation", "re-paste", "correction"]
    assert reported[-1] == ()


def test_adv04_set_hotkeys_while_suspended_goes_live_with_new_combos(
    app, monkeypatch
):
    """Save from the settings window: set_hotkeys is called while the taps are
    suspended. The new taps must go live (suspension lifted), config committed,
    and the watchdog active again for any failure."""
    failing = set()
    started, reported = _instrument(monkeypatch, app, failing)
    app.suspend_hotkeys()
    failing.add("correction")

    app.set_hotkeys(["cmd", "ctrl", "v"], ["cmd", "shift", "r"], ["cmd", "alt", "c"])

    assert started == ["dictation", "re-paste"]
    assert reported[-1] == ("Correction",)
    assert app.config.correct_keys == ["cmd", "alt", "c"]
    # Suspension is over (Save closes the window): the watchdog must retry.
    failing.clear()
    assert menubar.reenable_disabled_taps(app) == ["Correction"]
    assert app.correction_hotkey._targets == frozenset({"cmd", "alt", "c"})
    assert reported[-1] == ()


def test_adv05_set_hotkeys_while_degraded_rebuilds_and_retargets_retry(
    app, monkeypatch
):
    """A listener already failed via resume; set_hotkeys rebuilds all three.
    The still-failing listener stays reported and the watchdog retry must hit
    the NEW object (new combo), not the stale pre-rebuild one."""
    failing = {"dictation"}
    started, reported = _instrument(monkeypatch, app, failing)
    app.resume_hotkeys()
    old_hotkey = app.hotkey

    app.set_hotkeys(["cmd", "ctrl", "v"], ["cmd", "shift", "r"], ["cmd", "alt", "c"])
    assert reported[-1] == ("Hotkey",)
    assert app.hotkey is not old_hotkey

    failing.clear()
    started.clear()
    assert menubar.reenable_disabled_taps(app) == ["Hotkey"]
    assert started == ["dictation"]
    assert app.hotkey._targets == frozenset({"cmd", "ctrl", "v"})
    assert reported[-1] == ()


def test_adv06_retry_tick_is_idempotent_after_recovery(app, monkeypatch):
    """Once recovered, further ticks must not re-start listeners nor re-report."""
    failing = {"re-paste"}
    started, reported = _instrument(monkeypatch, app, failing)
    app.resume_hotkeys()
    failing.clear()
    assert menubar.reenable_disabled_taps(app) == ["Re-paste"]
    n_reports = len(reported)
    started.clear()

    for _ in range(5):
        assert menubar.reenable_disabled_taps(app) == []
    assert started == []
    assert len(reported) == n_reports  # no spurious re-reports


def test_adv07_fail_recover_fail_again_on_later_resume(app, monkeypatch):
    """A listener that recovered must be re-tracked if it fails again on a
    later suspend/resume cycle."""
    failing = {"correction"}
    started, reported = _instrument(monkeypatch, app, failing)
    app.resume_hotkeys()
    failing.clear()
    assert menubar.reenable_disabled_taps(app) == ["Correction"]
    assert reported[-1] == ()

    app.suspend_hotkeys()
    failing.add("correction")  # grant went stale again while suspended
    app.resume_hotkeys()
    assert reported[-1] == ("Correction",)
    failing.clear()
    assert menubar.reenable_disabled_taps(app) == ["Correction"]
    assert reported[-1] == ()


def test_adv08_two_failures_reported_in_iter_hotkeys_order(app, monkeypatch):
    """The reported tuple must follow iter_hotkeys() order regardless of which
    listener failed first."""
    started, reported = _instrument(monkeypatch, app, {"correction", "dictation"})
    app.resume_hotkeys()
    assert reported == [("Hotkey", "Correction")]
    assert started == ["re-paste"]


def test_adv09_retry_partial_recovery_reports_remaining_failures(app, monkeypatch):
    """Two dead listeners, one heals: the tick must recover exactly it and the
    report must shrink to the still-dead one (not clear entirely)."""
    failing = {"dictation", "re-paste"}
    started, reported = _instrument(monkeypatch, app, failing)
    app.resume_hotkeys()
    assert reported[-1] == ("Hotkey", "Re-paste")

    failing.discard("re-paste")
    assert menubar.reenable_disabled_taps(app) == ["Re-paste"]
    assert reported[-1] == ("Hotkey",)

    failing.clear()
    assert menubar.reenable_disabled_taps(app) == ["Hotkey"]
    assert reported[-1] == ()


# ---------------------------------------------------------------------------
# Hostile hooks and hostile listener methods
# ---------------------------------------------------------------------------

def test_adv10_hook_raising_on_every_call_never_breaks_anything(app, monkeypatch):
    """on_hotkeys_degraded raising on EVERY call must not break resume,
    set_hotkeys, or the watchdog retry — and the internal failure tracking
    must keep working underneath."""
    failing = {"dictation"}
    started, _ = _instrument(monkeypatch, app, failing)
    calls = []

    def bad_hook(labels):
        calls.append(tuple(labels))
        raise ValueError("UI layer exploded")

    app.on_hotkeys_degraded = bad_hook

    app.resume_hotkeys()  # must not raise
    app.set_hotkeys(["cmd", "ctrl", "v"], ["cmd", "shift", "r"], ["cmd", "alt", "c"])
    failing.clear()
    recovered = menubar.reenable_disabled_taps(app)  # must not raise

    assert recovered == ["Hotkey"]
    assert calls[-1] == ()  # the hook was still invoked with the cleared set
    assert app.config.keys == ["cmd", "ctrl", "v"]


def test_adv11_hook_set_to_none_mid_flight(app, monkeypatch):
    """The menubar layer detaching the hook between the failure and the
    recovery must not crash the retry; recovery still happens."""
    failing = {"re-paste"}
    started, reported = _instrument(monkeypatch, app, failing)
    app.resume_hotkeys()
    assert reported[-1] == ("Re-paste",)

    app.on_hotkeys_degraded = None
    failing.clear()
    assert menubar.reenable_disabled_taps(app) == ["Re-paste"]  # no raise
    assert "re-paste" in started


def test_adv12_stop_raising_during_suspend_keeps_suspension_effective(
    app, monkeypatch
):
    """stop() raising during suspend_hotkeys must neither escape (ObjC caller)
    nor break the suspension contract: the watchdog must still be silent."""
    failing = {"dictation"}
    started, reported = _instrument(monkeypatch, app, failing)
    app.resume_hotkeys()  # leaves a tracked failure
    monkeypatch.setattr(
        app_mod.HotkeyListener,
        "stop",
        lambda self: (_ for _ in ()).throw(OSError("mach port gone")),
    )

    app.suspend_hotkeys()  # must not raise

    failing.clear()
    started.clear()
    assert menubar.reenable_disabled_taps(app) == []
    assert started == []


def test_adv13_non_runtimeerror_exceptions_are_isolated_everywhere(app, monkeypatch):
    """The isolation must catch ANY Exception subclass, not just RuntimeError:
    OSError from a mach-port failure, ValueError from a bad key table."""
    excs = {"dictation": OSError("mach port"), "re-paste": ValueError("bad key")}
    failing = {"dictation", "re-paste"}
    started, reported = _instrument(
        monkeypatch, app, failing, exc_factory=lambda n: excs[n]
    )

    app.resume_hotkeys()  # must not raise

    assert started == ["correction"]
    assert reported == [("Hotkey", "Re-paste")]
    failing.clear()
    assert sorted(menubar.reenable_disabled_taps(app)) == ["Hotkey", "Re-paste"]


def test_adv14_retry_swallows_a_changed_exception_type(app, monkeypatch):
    """A listener that failed with RuntimeError and now fails with OSError on
    the retry must stay quietly tracked (no raise, no recovery, no report)."""
    failing = {"correction"}
    exc_type = [RuntimeError]
    started, reported = _instrument(
        monkeypatch, app, failing, exc_factory=lambda n: exc_type[0]("still dead")
    )
    app.resume_hotkeys()
    n_reports = len(reported)

    exc_type[0] = OSError
    assert menubar.reenable_disabled_taps(app) == []  # must not raise
    assert len(reported) == n_reports  # unchanged set -> no re-report


# ---------------------------------------------------------------------------
# Boot (App.start) failure tracking
# ---------------------------------------------------------------------------

def test_adv15_boot_with_repaste_and_correction_both_failing(app, monkeypatch):
    """Both convenience listeners failing at boot: both reported, both
    retried, partial recovery handled."""
    failing = {"re-paste", "correction"}
    started, reported = _instrument(monkeypatch, app, failing)
    monkeypatch.setattr(app.transcriber, "load", lambda: None)

    app.start()

    assert reported[-1] == ("Re-paste", "Correction")
    failing.discard("re-paste")
    assert menubar.reenable_disabled_taps(app) == ["Re-paste"]
    assert reported[-1] == ("Correction",)
    failing.clear()
    assert menubar.reenable_disabled_taps(app) == ["Correction"]
    assert reported[-1] == ()


def test_adv16_boot_with_dictation_listener_failing_attempts_all_then_raises(
    app, monkeypatch
):
    """Boot is the ONE deliberate exception to "never raise": flow.menubar's
    boot() catches the dictation-tap failure and offers the user-initiated
    "Restart TRD Speak now" flow, because macOS often honors a fresh Input
    Monitoring grant only in a new process — an in-process watchdog retry
    would spin forever there. But per issue #22 the other two listeners must
    still be ATTEMPTED first (one failure must not strand the rest), and no
    degraded row may be reported (the restart flow owns the UX; the watchdog
    only runs after a successful boot)."""
    failing = {"dictation"}
    started, reported = _instrument(monkeypatch, app, failing)
    monkeypatch.setattr(app.transcriber, "load", lambda: None)

    with pytest.raises(RuntimeError):
        app.start()

    assert started == ["re-paste", "correction"]
    assert reported == []


def test_adv17_boot_failure_then_suspend_keeps_watchdog_silent(app, monkeypatch):
    """A failure tracked at boot must obey the suspension rule like any other:
    opening a window right after boot silences the retry."""
    failing = {"correction"}
    started, reported = _instrument(monkeypatch, app, failing)
    monkeypatch.setattr(app.transcriber, "load", lambda: None)
    app.start()
    failing.clear()
    started.clear()

    app.suspend_hotkeys()
    assert menubar.reenable_disabled_taps(app) == []
    assert started == []


# ---------------------------------------------------------------------------
# Watchdog tick combining both recovery shapes
# ---------------------------------------------------------------------------

def test_adv18_one_tick_combines_start_retry_and_ensure_enabled(app, monkeypatch):
    """One tick must report BOTH a start()-retry recovery and an
    ensure_enabled re-assert on another listener, with no duplicates."""
    failing = {"re-paste"}
    started, reported = _instrument(monkeypatch, app, failing)
    app.resume_hotkeys()
    failing.clear()

    # The dictation tap exists but macOS disabled it: ensure_enabled -> True.
    reasserted = []

    def fake_ensure(self):
        if self._name == "dictation" and not reasserted:
            reasserted.append(self._name)
            return True
        return False

    monkeypatch.setattr(app_mod.HotkeyListener, "ensure_enabled", fake_ensure)

    recovered = menubar.reenable_disabled_taps(app)
    assert sorted(recovered) == ["Hotkey", "Re-paste"]
    assert len(recovered) == len(set(recovered))  # no duplicate labels
    assert reported[-1] == ()


def test_adv19_ensure_enabled_still_runs_when_no_start_failures(app, monkeypatch):
    """The ensure_enabled leg of the tick must run even with an empty failure
    set (retry_failed_hotkeys early-returns)."""
    started, reported = _instrument(monkeypatch, app, set())
    app.resume_hotkeys()
    monkeypatch.setattr(
        app_mod.HotkeyListener,
        "ensure_enabled",
        lambda self: self._name == "correction",
    )
    assert menubar.reenable_disabled_taps(app) == ["Correction"]


# ---------------------------------------------------------------------------
# MenuBar warning row rendering (functional-ish: real AppKit menu items)
# ---------------------------------------------------------------------------

def test_adv20_warning_row_title_for_one_two_three_labels(rig):
    app, ui, started, failing = rig
    ui.update_hotkey_failures(("Hotkey",))
    hidden, title = _warning_row(ui)
    assert not hidden and "Hotkey" in title and "retrying" in title

    ui.update_hotkey_failures(("Hotkey", "Re-paste"))
    hidden, title = _warning_row(ui)
    assert not hidden and "Hotkey" in title and "Re-paste" in title

    ui.update_hotkey_failures(("Hotkey", "Re-paste", "Correction"))
    hidden, title = _warning_row(ui)
    assert not hidden
    for label in ("Hotkey", "Re-paste", "Correction"):
        assert label in title

    ui.update_hotkey_failures(())
    hidden, _ = _warning_row(ui)
    assert hidden


def test_adv21_warning_row_accepts_a_list_not_only_a_tuple(rig):
    """update_hotkey_failures is a hook: a caller passing a list must work
    identically (the hook contract is 'labels', not 'tuple')."""
    app, ui, started, failing = rig
    ui.update_hotkey_failures(["Re-paste", "Correction"])
    hidden, title = _warning_row(ui)
    assert not hidden and "Re-paste" in title and "Correction" in title
    ui.update_hotkey_failures([])
    hidden, _ = _warning_row(ui)
    assert hidden


def test_adv22_warning_row_visible_in_restart_needed_state(rig):
    """The degraded row is independent of the restart-offer state: a start()
    failure while the 'restart to finish' menu is up must still be visible."""
    app, ui, started, failing = rig
    ui._restart_needed = True  # as set_restart_needed() would set
    ui.update_hotkey_failures(("Hotkey",))
    hidden, title = _warning_row(ui)
    assert not hidden and "Hotkey" in title

    ui.update_hotkey_failures(())
    hidden, _ = _warning_row(ui)
    assert hidden


def test_adv23_warning_row_visible_during_onboarding(rig):
    """The degraded row is independent of the onboarding (missing permission)
    state too."""
    app, ui, started, failing = rig
    ui._missing_keys = (permissions.PERMISSIONS[0].key,)
    ui.update_hotkey_failures(("Correction",))
    hidden, title = _warning_row(ui)
    assert not hidden and "Correction" in title


def test_adv24_stale_row_cleared_by_a_clean_resume_end_to_end(rig):
    """Full loop: degrade -> row shown -> suspend -> heal -> resume -> the
    empty report must clear the stale row through the real MenuBar hook."""
    app, ui, started, failing = rig
    failing.add("dictation")
    app.resume_hotkeys()
    hidden, _ = _warning_row(ui)
    assert not hidden

    app.suspend_hotkeys()
    failing.clear()
    app.resume_hotkeys()
    hidden, _ = _warning_row(ui)
    assert hidden


# ---------------------------------------------------------------------------
# Functional: real settings / correction windows under hostile conditions
# ---------------------------------------------------------------------------

def test_adv25_settings_cancel_with_all_three_failing_then_full_recovery(rig):
    app, ui, started, failing = rig
    from flow.settings_window import SettingsWindowController

    controller = SettingsWindowController(app, ui)
    controller.open()
    started.clear()
    failing.update({"dictation", "re-paste", "correction"})

    controller.cancel()  # real close path — must not raise into the delegate

    assert started == []
    hidden, title = _warning_row(ui)
    assert not hidden
    for label in ("Hotkey", "Re-paste", "Correction"):
        assert label in title

    failing.clear()
    recovered = menubar.reenable_disabled_taps(app)
    assert sorted(recovered) == ["Correction", "Hotkey", "Re-paste"]
    hidden, _ = _warning_row(ui)
    assert hidden


def test_adv26_correction_window_over_degraded_state_then_save(rig, monkeypatch):
    """Open the correction window WHILE already degraded, heal during the
    window, save: the close path must restart everything and clear the row —
    and the watchdog must have stayed silent while the window was open."""
    app, ui, started, failing = rig
    from flow.correction_window import CorrectionWindowController

    failing.add("re-paste")
    app.resume_hotkeys()  # degraded before the window opens
    controller = CorrectionWindowController(app)
    controller.open("hello world")
    failing.clear()
    started.clear()
    monkeypatch.setattr(app, "learn", lambda original, edited: None)

    assert menubar.reenable_disabled_taps(app) == []  # window open: silent
    assert started == []

    controller.save()
    assert started == ["dictation", "re-paste", "correction"]
    hidden, _ = _warning_row(ui)
    assert hidden


def test_adv27_settings_save_failure_then_reopen_cancel_cycle(rig):
    """Save leaves a dead listener; the user immediately reopens settings
    (suspend) and cancels (resume). The failure must survive the cycle
    coherently: silent while open, retried after close, NEW combo kept."""
    app, ui, started, failing = rig
    from flow.settings_window import SettingsWindowController

    controller = SettingsWindowController(app, ui)
    controller.open()
    controller._dictate_recorder.set_keys(["cmd", "ctrl", "v"])
    controller._repaste_recorder.set_keys(["cmd", "shift", "r"])
    controller._correct_recorder.set_keys(["cmd", "alt", "c"])
    failing.add("re-paste")
    controller.save()
    hidden, title = _warning_row(ui)
    assert not hidden and "Re-paste" in title

    controller2 = SettingsWindowController(app, ui)
    controller2.open()  # suspend again, still degraded
    failing.clear()
    started.clear()
    assert menubar.reenable_disabled_taps(app) == []  # suspended: silent
    controller2.cancel()  # resume with unchanged (new) combos

    assert started == ["dictation", "re-paste", "correction"]
    assert app.config.repaste_keys == ["cmd", "shift", "r"]
    assert app.repaste_hotkey._targets == frozenset({"cmd", "shift", "r"})
    hidden, _ = _warning_row(ui)
    assert hidden
