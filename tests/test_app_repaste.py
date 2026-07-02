"""Functional test of the "paste last dictation" feature, end to end.

A character-key chord (Cmd+Ctrl+P) now rides the Carbon RegisterEventHotKey
backend (issue #23), so this drives the REAL CarbonHotkey dispatch — a
simulated Carbon pressed+released pair routed through flow.carbon_hotkey's
registry — wired into the App, and asserts the most recent dictation is
actually pasted: chord -> _on_repaste -> _do_repaste -> paste_text.

No real Carbon registration and no real Quartz tap: the Carbon module seams
(_register/_unregister/_ensure_handler) are mocked, and the dictate/correct
listeners' tap machinery is neutered. Carbon delivers a released event per
tap by design, so the old "withheld keyUp" trap of the tap path cannot occur;
the second-tap regression test is kept to pin that.
"""

import queue
import time

import pytest

import flow.app as app_mod
import flow.carbon_hotkey as ch
from flow.app import App
from flow.config import Config


@pytest.fixture(autouse=True)
def _isolate_history(tmp_path, monkeypatch):
    """Point the per-build dictations file at a throwaway temp file so tests
    never read or write the user's real ~/Library/Application Support store."""
    monkeypatch.setattr(app_mod.paths, "DICTATIONS_PATH", tmp_path / "dictations.json")


def _wait_idle(logic, timeout=3.0):
    """Block until the re-paste worker has returned the App to IDLE.

    _do_repaste runs on a fire-and-forget daemon thread and holds the state at
    PROCESSING across the paste. A real user taps seconds apart, but the test
    fires the next tap immediately; without this wait the second worker can see
    a not-yet-IDLE state and (by design) refuse. Polling the state is the
    condition-based wait that makes the test deterministic and hermetic — the
    worker is finished before the test ends, so it never leaks into a later
    test's monkeypatched paste queue."""
    deadline = time.monotonic() + timeout
    while logic._state != app_mod.IDLE and time.monotonic() < deadline:
        time.sleep(0.005)
    assert logic._state == app_mod.IDLE, f"worker did not return to IDLE: {logic._state}"

class _Driver:
    """Fires a Carbon pressed+released pair for a started CarbonHotkey through
    the module's dispatch registry — the same route the real installed Carbon
    event handler takes."""

    def __init__(self, listener, monkeypatch):
        self._l = listener

    def tap_cmd_ctrl_p(self):
        """One clean Cmd+Ctrl+P tap: Carbon delivers kEventHotKeyPressed then
        kEventHotKeyReleased; the trigger fires on RELEASED."""
        ch._dispatch(self._l._hotkey_id, ch.kEventHotKeyPressed)
        ch._dispatch(self._l._hotkey_id, ch.kEventHotKeyReleased)


def _build_app(monkeypatch):
    monkeypatch.setattr(app_mod.HotkeyListener, "start", lambda self: None)
    monkeypatch.setattr(app_mod.HotkeyListener, "stop", lambda self: None)
    # Carbon seams: no real registration; the chord counts as fully released
    # (the wait's OS modifier poll answers "all up").
    monkeypatch.setattr(ch, "_register", lambda vk, mask, hkid: (0, f"ref-{hkid}"))
    monkeypatch.setattr(ch, "_unregister", lambda ref: 0)
    monkeypatch.setattr(ch, "_ensure_handler", lambda: None)
    monkeypatch.setattr(ch, "modifiers_physically_down", lambda: False)
    cfg = Config()
    cfg.repaste_keys = ["cmd", "ctrl", "p"]
    logic = App(cfg)
    logic.repaste_hotkey.start()  # register with the (mocked) Carbon layer
    logic.can_paste = lambda: True
    pasted: queue.Queue = queue.Queue()
    monkeypatch.setattr(
        app_mod, "paste_text", lambda text, restore_delay=0.4: pasted.put(text)
    )
    return logic, pasted


def test_char_chord_tap_pastes_last_dictation(monkeypatch):
    logic, pasted = _build_app(monkeypatch)
    logic.history.add("hello world")

    _Driver(logic.repaste_hotkey, monkeypatch).tap_cmd_ctrl_p()

    # _do_repaste runs on a worker thread; Queue.get blocks until it pastes.
    assert pasted.get(timeout=3.0) == "hello world "
    _wait_idle(logic)  # let the worker finish so it cannot leak into a later test


def test_char_chord_repaste_still_works_on_a_second_tap(monkeypatch):
    """The historically reported bug: it works once, then stops. On the
    Carbon backend each tap delivers its own pressed/released pair, so a
    second tap must re-paste again (pins the per-tap re-arm)."""
    logic, pasted = _build_app(monkeypatch)
    logic.history.add("again please")
    d = _Driver(logic.repaste_hotkey, monkeypatch)

    d.tap_cmd_ctrl_p()
    assert pasted.get(timeout=3.0) == "again please "
    _wait_idle(logic)  # the App serializes re-paste; wait for IDLE before the next tap

    d.tap_cmd_ctrl_p()
    assert pasted.get(timeout=3.0) == "again please "
    _wait_idle(logic)
