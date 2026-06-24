"""Functional test of the "paste last dictation" feature, end to end.

Drives the REAL HotkeyListener tap callback for a character-key chord
(Cmd+Ctrl+P) wired into the App, and asserts the most recent dictation is
actually pasted — exercising chord -> _on_repaste -> _do_repaste -> paste_text.

No real Quartz tap / Input Monitoring: HotkeyListener.start/stop are neutered
and the synthesized events are fed straight into _tap_callback, the same path a
live tap drives. The character key's keyUp is deliberately NOT delivered, to
reproduce macOS withholding it while Command is held.
"""

import queue
import time

import pytest

import flow.app as app_mod
import flow.hotkey as hk
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

_CMD, _CTRL, _P = 55, 59, 35
_CMD_MASK = hk.Quartz.kCGEventFlagMaskCommand
_CTRL_MASK = hk.Quartz.kCGEventFlagMaskControl


class _Driver:
    """Feeds synthesized key/modifier events into a listener's tap callback,
    mirroring how a real Quartz tap delivers them (see tests/test_hotkey.py)."""

    def __init__(self, listener, monkeypatch):
        self._l = listener
        self._kc = 0
        self._flags = 0
        monkeypatch.setattr(
            hk.Quartz, "CGEventGetIntegerValueField", lambda e, f: self._kc
        )
        monkeypatch.setattr(hk.Quartz, "CGEventGetFlags", lambda e: self._flags)

    def modifier(self, keycode, flags):
        self._kc, self._flags = keycode, flags
        self._l._tap_callback(None, hk.Quartz.kCGEventFlagsChanged, object(), None)

    def key_down(self, keycode):
        self._kc = keycode
        self._l._tap_callback(None, hk.Quartz.kCGEventKeyDown, object(), None)

    def tap_cmd_ctrl_p(self):
        """One clean Cmd+Ctrl+P tap whose P keyUp is suppressed (Command held):
        press cmd, ctrl, P, then release only the modifiers."""
        self.modifier(_CMD, _CMD_MASK)
        self.modifier(_CTRL, _CMD_MASK | _CTRL_MASK)
        self.key_down(_P)            # fires the trigger
        self.modifier(_CMD, _CTRL_MASK)  # cmd up (P keyUp never arrives)
        self.modifier(_CTRL, 0)          # ctrl up -> modifiers clear, re-arm


def _build_app(monkeypatch):
    monkeypatch.setattr(app_mod.HotkeyListener, "start", lambda self: None)
    monkeypatch.setattr(app_mod.HotkeyListener, "stop", lambda self: None)
    cfg = Config()
    cfg.repaste_keys = ["cmd", "ctrl", "p"]
    logic = App(cfg)
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
    """The reported bug: it works once, then stops. With the character key's
    keyUp suppressed, the second tap must still re-paste."""
    logic, pasted = _build_app(monkeypatch)
    logic.history.add("again please")
    d = _Driver(logic.repaste_hotkey, monkeypatch)

    d.tap_cmd_ctrl_p()
    assert pasted.get(timeout=3.0) == "again please "
    _wait_idle(logic)  # the App serializes re-paste; wait for IDLE before the next tap

    d.tap_cmd_ctrl_p()
    assert pasted.get(timeout=3.0) == "again please "
    _wait_idle(logic)
