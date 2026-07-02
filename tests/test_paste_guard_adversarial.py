"""Adversarial test battery for the issue #24 paste guard.

Targets: App._released_or_stale, App._do_repaste's skip/self-heal path,
App._process's paste guard, and flow.hotkey.modifiers_physically_down — the
tie-break logic that decides whether a synthesized Cmd+V may be posted after
wait_all_released() times out.

Every test asserts the INTENDED behavior of issue #24 (never paste while a
modifier is physically down; self-heal a stale shadow state; skips are
surfaced and never wedge the state machine), not merely what the code happens
to do today. Failing tests are kept deliberately.

No real Quartz tap is created (HotkeyListener.start/stop are neutered), no
real events are posted, and no machine configuration is touched.

Deliberately NOT duplicated here: the happy-path skip/self-heal matrix
(tests/test_paste_key_guard.py), the tap-callback event battery
(tests/test_repaste_adversarial.py), and the plain repaste guards
(tests/test_app_engine.py, tests/test_app_repaste.py).
"""

import threading
import time

import numpy
import pytest

import flow.app as app_mod
from flow.app import App, IDLE, PROCESSING
from flow.config import Config


@pytest.fixture(autouse=True)
def _isolate_storage(tmp_path, monkeypatch):
    """Keep dictations/dictionary out of the user's real Application Support."""
    monkeypatch.setattr(app_mod.paths, "DICTATIONS_PATH", tmp_path / "dictations.json")
    monkeypatch.setattr(app_mod.paths, "DICTIONARY_PATH", tmp_path / "dictionary.json")


def _build_app(monkeypatch):
    monkeypatch.setattr(app_mod.HotkeyListener, "start", lambda self: None)
    monkeypatch.setattr(app_mod.HotkeyListener, "stop", lambda self: None)
    app = App(Config())
    app.can_paste = lambda: True
    pasted: list[str] = []
    monkeypatch.setattr(
        app_mod, "paste_text", lambda text, restore_delay=0.4: pasted.append(text)
    )
    notes: list[str] = []
    app.notify = notes.append
    return app, pasted, notes


def _build_dictation_app(monkeypatch, transcript="dictated words"):
    app, pasted, notes = _build_app(monkeypatch)
    app.recorder = type(
        "R", (), {"stop": lambda self: numpy.ones(16000, dtype="float32")}
    )()
    app.transcriber = type(
        "T", (), {"transcribe": lambda self, audio, hotwords=None: transcript}
    )()
    return app, pasted, notes


def _wedge(listener, monkeypatch, timeout=0.05):
    """Simulate a missed ctrl keyUp: the shadow state says ctrl is held, so the
    REAL wait_all_released blocks and times out (shortened for test speed)."""
    listener._held = {"ctrl": {59}}
    orig = listener.wait_all_released
    monkeypatch.setattr(
        listener, "wait_all_released", lambda t=2.0: orig(timeout=timeout)
    )


# ===========================================================================
# CATEGORY: notify raising on the skip path
# ===========================================================================

def test_pg01_notify_raising_must_not_escape_do_repaste(monkeypatch):
    """PG-01: the re-paste skip is best-effort UX. notify raising on the skip
    path (wedged + OS says keys held) must not propagate out of _do_repaste —
    the notification is advisory, the worker must terminate cleanly."""
    app, pasted, _ = _build_app(monkeypatch)
    app.history.add("held hostage")
    _wedge(app.repaste_hotkey, monkeypatch)
    monkeypatch.setattr(app_mod, "modifiers_physically_down", lambda: True)
    app.notify = lambda msg: (_ for _ in ()).throw(RuntimeError("banner backend gone"))

    app._do_repaste()  # must NOT raise

    assert pasted == []
    assert app._state == IDLE


def test_pg02_notify_raising_in_worker_thread_leaves_app_functional(monkeypatch):
    """PG-02: same failure through the PRODUCTION path (_on_repaste spawns the
    worker): the raise may kill that daemon thread, but the app must survive —
    state stays IDLE and the very next re-paste works."""
    app, pasted, _ = _build_app(monkeypatch)
    app.history.add("survivor")
    _wedge(app.repaste_hotkey, monkeypatch)
    monkeypatch.setattr(app_mod, "modifiers_physically_down", lambda: True)
    seen = threading.Event()

    def bad_notify(msg):
        seen.set()
        raise RuntimeError("banner backend gone")

    app.notify = bad_notify
    app._on_repaste()
    assert seen.wait(timeout=2.0), "skip path never reached notify"
    time.sleep(0.1)  # let the worker unwind

    assert pasted == []
    assert app._state == IDLE
    # Recovery: keys now released, notify healthy -> re-paste must work.
    app.notify = lambda msg: None
    with app.repaste_hotkey._cond:
        app.repaste_hotkey._held = {}
        app.repaste_hotkey._cond.notify_all()
    app._do_repaste()
    assert pasted == ["survivor "]
    assert app._state == IDLE


# ===========================================================================
# CATEGORY: the OS-truth helper itself failing mid-guard
# ===========================================================================

def test_pg03_os_helper_raising_repaste_fails_safe_without_crashing(monkeypatch):
    """PG-03: CGEventSourceFlagsState failing (modifiers_physically_down
    raises) while the wait has timed out. The defensible intent: never paste
    blind (keys may be held) AND never crash the worker — the attempt is
    dropped safely."""
    app, pasted, _ = _build_app(monkeypatch)
    app.history.add("blind")
    _wedge(app.repaste_hotkey, monkeypatch)

    def boom():
        raise RuntimeError("CGEventSourceFlagsState failed")

    monkeypatch.setattr(app_mod, "modifiers_physically_down", boom)

    app._do_repaste()  # must neither paste nor raise

    assert pasted == []
    assert app._state == IDLE


def test_pg04_os_helper_raising_dictation_fails_safe_text_recoverable(
    monkeypatch, capsys
):
    """PG-04: same failure on the DICTATION path. _process must not paste
    blind, must return to IDLE, and the text must stay in history so the user
    can recover it once the helper works again."""
    app, pasted, _ = _build_dictation_app(monkeypatch)
    _wedge(app.hotkey, monkeypatch)

    def boom():
        raise RuntimeError("CGEventSourceFlagsState failed")

    monkeypatch.setattr(app_mod, "modifiers_physically_down", boom)

    app._process()

    assert pasted == []
    assert app._state == IDLE
    assert app.history.items()[0] == "dictated words"
    assert "CGEventSourceFlagsState failed" in capsys.readouterr().out


# ===========================================================================
# CATEGORY: state-machine invariants on the skip path
# ===========================================================================

def test_pg05_skipped_repaste_while_processing_does_not_clobber_state(monkeypatch):
    """PG-05: the re-paste skip happens BEFORE the IDLE guard. A skip while a
    dictation is mid-flight (state PROCESSING) must leave that state alone —
    resetting it to IDLE would let a second dictation start over the first."""
    app, pasted, notes = _build_app(monkeypatch)
    app.history.add("midflight")
    app._state = PROCESSING  # in-flight dictation owns the state
    _wedge(app.repaste_hotkey, monkeypatch)
    monkeypatch.setattr(app_mod, "modifiers_physically_down", lambda: True)

    app._do_repaste()

    assert pasted == []
    assert app._state == PROCESSING, "skip path clobbered an in-flight dictation"
    assert any("keys still held" in n for n in notes)


def test_pg06_notify_raising_inside_lock_does_not_strand_the_lock(monkeypatch):
    """PG-06: the 'Finish the current dictation first.' notify runs while
    app._lock is HELD. If it raises, the lock must still be released and the
    in-flight state untouched (a stranded lock deadlocks every hotkey)."""
    app, pasted, _ = _build_app(monkeypatch)
    app.history.add("locked")
    app._state = PROCESSING
    app.notify = lambda msg: (_ for _ in ()).throw(RuntimeError("notify died"))

    try:
        app._do_repaste()
    except RuntimeError:
        pass  # the raise itself is judged in PG-01; here we judge the lock

    assert app._lock.acquire(timeout=1.0), "app._lock left held after notify raised"
    app._lock.release()
    assert app._state == PROCESSING
    assert pasted == []


def test_pg07_concurrent_wedged_self_heal_repastes_paste_exactly_once(monkeypatch):
    """PG-07: two re-paste workers racing through the SAME wedged wait, both
    self-healing (OS clear). The IDLE guard must serialize them: exactly one
    paste, the loser refused."""
    app, pasted, notes = _build_app(monkeypatch)
    app.history.add("racy")
    _wedge(app.repaste_hotkey, monkeypatch)
    monkeypatch.setattr(app_mod, "modifiers_physically_down", lambda: False)

    def slow_paste(text, restore_delay=0.4):
        time.sleep(0.4)
        pasted.append(text)

    monkeypatch.setattr(app_mod, "paste_text", slow_paste)

    barrier = threading.Barrier(2)

    def worker():
        barrier.wait()
        app._do_repaste()

    t1 = threading.Thread(target=worker, daemon=True)
    t2 = threading.Thread(target=worker, daemon=True)
    t1.start(); t2.start()
    t1.join(timeout=5); t2.join(timeout=5)

    assert pasted == ["racy "], f"expected exactly one paste, got {pasted}"
    assert any("Finish the current dictation" in n for n in notes)
    assert app._state == IDLE


# ===========================================================================
# CATEGORY: self-heal path meeting the OTHER guards
# ===========================================================================

def test_pg08_self_heal_with_empty_history_notifies_and_returns_idle(monkeypatch):
    """PG-08: the self-heal path must still hit the empty-history guard —
    notify 'No recent dictation', no paste, state back to IDLE."""
    app, pasted, notes = _build_app(monkeypatch)
    _wedge(app.repaste_hotkey, monkeypatch)
    monkeypatch.setattr(app_mod, "modifiers_physically_down", lambda: False)

    app._do_repaste()

    assert pasted == []
    assert any("No recent dictation" in n for n in notes)
    assert app._state == IDLE


def test_pg09_self_heal_with_can_paste_false_skips_and_returns_idle(
    monkeypatch, capsys
):
    """PG-09: self-heal + missing Accessibility permission: the can_paste gate
    must still refuse the paste, log it, and return to IDLE."""
    app, pasted, _ = _build_app(monkeypatch)
    app.history.add("blocked")
    app.can_paste = lambda: False
    _wedge(app.repaste_hotkey, monkeypatch)
    monkeypatch.setattr(app_mod, "modifiers_physically_down", lambda: False)

    app._do_repaste()

    assert pasted == []
    assert "CANNOT proceed" in capsys.readouterr().out
    assert app._state == IDLE


def test_pg10_repeated_timeout_skips_notify_every_time_without_crashing(monkeypatch):
    """PG-10: a wedge the skip path never clears (keys genuinely held) hit
    three times in a row: every attempt must skip, notify, and leave IDLE —
    no crash, no paste, no wedged state accumulating."""
    app, pasted, notes = _build_app(monkeypatch)
    app.history.add("persistent")
    _wedge(app.repaste_hotkey, monkeypatch)
    monkeypatch.setattr(app_mod, "modifiers_physically_down", lambda: True)

    for _ in range(3):
        app._do_repaste()

    assert pasted == []
    assert [n for n in notes if "keys still held" in n] == [
        "Re-paste skipped — keys still held."
    ] * 3
    assert app._state == IDLE


def test_pg11_unicode_dictation_survives_the_self_heal_path(monkeypatch, capsys):
    """PG-11: a self-healed dictation paste must deliver the text byte-exact
    (unicode, combining marks, CJK) and log the stale-shadow explanation."""
    weird = "café — naïve ́combining 𝓤𝓷𝓲 漢字 🙂"
    app, pasted, _ = _build_dictation_app(monkeypatch, transcript=weird)
    _wedge(app.hotkey, monkeypatch)
    monkeypatch.setattr(app_mod, "modifiers_physically_down", lambda: False)

    app._process()

    assert pasted == [weird + " "]
    assert "stale" in capsys.readouterr().out
    assert app._state == IDLE


def test_pg12_held_clearing_mid_wait_pastes_without_consulting_os(monkeypatch):
    """PG-12: _held mutating DURING wait_all_released (the keyUp arrives late,
    inside the wait window): the wait returns True, so the paste proceeds and
    the OS tie-breaker must never be consulted."""
    app, pasted, _ = _build_app(monkeypatch)
    app.history.add("late release")
    app.repaste_hotkey._held = {"ctrl": {59}}  # wedged, but NOT timing out
    os_calls: list[bool] = []
    monkeypatch.setattr(
        app_mod, "modifiers_physically_down", lambda: os_calls.append(True) or True
    )

    def release_later():
        time.sleep(0.15)
        with app.repaste_hotkey._cond:
            app.repaste_hotkey._held = {}
            app.repaste_hotkey._cond.notify_all()

    threading.Thread(target=release_later, daemon=True).start()
    app._do_repaste()

    assert pasted == ["late release "]
    assert os_calls == [], "OS flags consulted although the wait succeeded"
    assert app._state == IDLE


# ===========================================================================
# CATEGORY: guard interplay with the dictation pipeline
# ===========================================================================

@pytest.mark.xfail(
    reason="Known gap OUTSIDE issue #24's scope: the falsy-text gate runs before "
    "correction, so a corrector that empties the transcript pastes a bare ' '. "
    "Pre-existing pipeline behavior; fix separately, then drop this marker.",
    strict=True,
)
def test_pg13_correction_emptying_the_text_must_not_paste_a_bare_space(monkeypatch):
    """PG-13: the falsy-text gate runs BEFORE correction. If the corrector
    reduces the transcript to '', the pipeline should behave like an empty
    transcription — pasting a lone ' ' into the focused window is garbage
    output. (Debatable: outside issue #24's letter, but the paste guard is the
    last line before paste_text and lets it through.)"""
    app, pasted, _ = _build_dictation_app(monkeypatch, transcript="uh")
    app.corrector = type("C", (), {"correct": lambda self, text: ""})()

    app._process()

    assert pasted == [], f"pasted a bare separator: {pasted!r}"
    assert app._state == IDLE


def test_pg14_skipped_dictation_recovers_via_repaste_after_release(monkeypatch):
    """PG-14: the full recovery loop the skip promises: dictation paste skipped
    (keys genuinely held) -> keys released -> re-paste delivers exactly the
    skipped text."""
    app, pasted, _ = _build_dictation_app(monkeypatch)
    _wedge(app.hotkey, monkeypatch)
    monkeypatch.setattr(app_mod, "modifiers_physically_down", lambda: True)
    app._process()
    assert pasted == []

    # Keys released: the repaste listener was never wedged, its wait succeeds.
    monkeypatch.setattr(app_mod, "modifiers_physically_down", lambda: False)
    app._do_repaste()

    assert pasted == ["dictated words "]
    assert app._state == IDLE


@pytest.mark.xfail(
    reason="Documented design gap, out of issue #24's prescribed fix: the OS "
    "tie-breaker runs only on a wait_all_released timeout. The issue's own "
    "Tests section requires the normal path to paste WITHOUT consulting the OS "
    "(see test_paste_key_guard.py), so a modifier pressed after a clean combo "
    "release still leaks into the synthesized paste. Revisit if reported.",
    strict=True,
)
def test_pg15_paste_must_not_fire_while_os_reports_a_late_held_modifier(monkeypatch):
    """PG-15: issue #24, rule 1 as written: a synthesized Cmd+V must NEVER be
    posted while any modifier is physically down. Here the trigger keys were
    released cleanly (wait_all_released True) but the user is holding Ctrl at
    paste time (pressed DURING transcription — e.g. ctrl-scrolling): the OS
    reports it, yet the guard only consults the OS on a wait timeout, so the
    paste goes out as ⌘⌃V. (Debatable: test_paste_key_guard.py encodes the
    opposite — OS not consulted on a clean wait — so this documents a known
    design gap in the guard, not a regression.)"""
    app, pasted, _ = _build_dictation_app(monkeypatch)
    # Shadow state clean: the combo was released properly.
    assert app.hotkey.wait_all_released(timeout=0.01) is True
    # But the OS says a modifier is physically down right now.
    monkeypatch.setattr(app_mod, "modifiers_physically_down", lambda: True)

    app._process()

    assert pasted == [], (
        "Cmd+V synthesized while the OS reported a modifier physically down "
        f"(would arrive as ⌘⌃V): {pasted!r}"
    )
    assert app.history.items()[0] == "dictated words"
