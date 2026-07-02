"""Functional tests for issue #24: never synthesize Cmd+V while modifier keys
are physically held; self-heal a stale hotkey shadow state.

Both paste paths are exercised end to end (App._do_repaste and App._process)
with a genuinely wedged listener shadow state (_held contains a modifier whose
keyUp was "missed", so the REAL wait_all_released times out) and the OS-truth
helper flow.hotkey.modifiers_physically_down monkeypatched for determinism:

  * OS says modifiers are physically down  -> the paste must be SKIPPED and the
    skip surfaced via the notify hook AND the log (banners are unreliable on
    this machine; the log line is mandatory).
  * OS says all modifiers are up          -> the shadow state was stale and the
    paste must PROCEED (self-healing) instead of being dropped/blocked forever.

A live-keyboard e2e is not feasible here (it would require synthesizing real
hardware key state); these tests drive the exact production code paths with
only the paster, the shadow state, and the OS-flags helper controlled.

No real Quartz tap is created (HotkeyListener.start/stop are neutered) and no
machine configuration is touched. One smoke test calls the real
CGEventSourceFlagsState-backed helper — that API needs no TCC permission.
"""

import pytest

import flow.app as app_mod
import flow.hotkey as hk
from flow.app import App, IDLE
from flow.config import Config


@pytest.fixture(autouse=True)
def _isolate_history(tmp_path, monkeypatch):
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


def _wedge(listener, monkeypatch):
    """Simulate a missed ctrl keyUp: the shadow state says ctrl is held, so the
    REAL wait_all_released blocks and times out. The timeout is shortened so
    the genuine timeout path runs without a 2 s stall per test."""
    listener._held = {"ctrl": {59}}
    orig = listener.wait_all_released
    monkeypatch.setattr(
        listener, "wait_all_released", lambda timeout=2.0: orig(timeout=0.05)
    )


# ---------------------------------------------------------------------------
# Re-paste path (App._do_repaste)
# ---------------------------------------------------------------------------

def test_repaste_wedged_and_os_modifiers_down_skips_paste(monkeypatch, capsys):
    """wait_all_released times out AND the OS confirms modifiers are physically
    held -> Cmd+V must NOT be synthesized (it would arrive as ⌘⌃V), and the
    skip is surfaced via notify AND the log."""
    app, pasted, notes = _build_app(monkeypatch)
    app.history.add("held hostage")
    _wedge(app.repaste_hotkey, monkeypatch)
    monkeypatch.setattr(app_mod, "modifiers_physically_down", lambda: True)

    app._do_repaste()

    assert pasted == []
    assert any("keys still held" in n for n in notes)
    assert "keys still held" in capsys.readouterr().out
    assert app._state == IDLE  # nothing stranded in PROCESSING


def test_repaste_wedged_but_os_clear_self_heals_and_pastes(monkeypatch):
    """wait_all_released times out but the OS says NO modifier is physically
    down: the shadow state was stale (missed keyUp) -> the paste must proceed
    instead of failing silently."""
    app, pasted, notes = _build_app(monkeypatch)
    app.history.add("self healed")
    _wedge(app.repaste_hotkey, monkeypatch)
    monkeypatch.setattr(app_mod, "modifiers_physically_down", lambda: False)

    app._do_repaste()

    assert pasted == ["self healed "]
    assert not any("keys still held" in n for n in notes)


def test_repaste_normal_release_pastes_without_consulting_os(monkeypatch):
    """Normal path: wait_all_released returns True (nothing held) -> paste
    proceeds and the OS-flags helper is never needed."""
    app, pasted, _ = _build_app(monkeypatch)
    app.history.add("normal")
    calls: list[bool] = []
    monkeypatch.setattr(
        app_mod, "modifiers_physically_down", lambda: calls.append(True) or True
    )

    app._do_repaste()

    assert pasted == ["normal "]
    assert calls == []  # OS truth only consulted on a wait timeout


# ---------------------------------------------------------------------------
# Dictation path (App._process)
# ---------------------------------------------------------------------------

def _build_dictation_app(monkeypatch):
    app, pasted, notes = _build_app(monkeypatch)
    app.recorder = type(
        "R", (), {"stop": lambda self: __import__("numpy").ones(16000, dtype="float32")}
    )()
    app.transcriber = type(
        "T", (), {"transcribe": lambda self, audio, hotwords=None: "dictated words"}
    )()
    return app, pasted, notes


def test_process_wedged_but_os_clear_pastes_dictation(monkeypatch):
    """Regression for the wedged-shadow-state lockout: today a stuck _held entry
    makes EVERY dictation skip its paste. With the OS reporting all modifiers
    up, the dictation paste must proceed."""
    app, pasted, _ = _build_dictation_app(monkeypatch)
    _wedge(app.hotkey, monkeypatch)
    monkeypatch.setattr(app_mod, "modifiers_physically_down", lambda: False)

    app._process()

    assert pasted == ["dictated words "]
    assert app.history.items()[0] == "dictated words"


def test_process_os_modifiers_down_skips_paste_with_message(monkeypatch, capsys):
    """Keys genuinely held at paste time -> skip, with the existing log message
    (the text stays recoverable from the history)."""
    app, pasted, _ = _build_dictation_app(monkeypatch)
    _wedge(app.hotkey, monkeypatch)
    monkeypatch.setattr(app_mod, "modifiers_physically_down", lambda: True)

    app._process()

    assert pasted == []
    out = capsys.readouterr().out
    assert "still held" in out and "paste skipped" in out
    assert app.history.items()[0] == "dictated words"  # recoverable via re-paste
    assert app._state == IDLE


def test_process_normal_release_pastes(monkeypatch):
    """Normal dictation path unchanged: wait succeeds -> paste."""
    app, pasted, _ = _build_dictation_app(monkeypatch)
    monkeypatch.setattr(
        app_mod, "modifiers_physically_down", lambda: pytest.fail(
            "OS flags must not be consulted when wait_all_released succeeds"
        )
    )

    app._process()

    assert pasted == ["dictated words "]


# ---------------------------------------------------------------------------
# OS-truth helper
# ---------------------------------------------------------------------------

def test_modifiers_physically_down_smoke():
    """The real helper calls CGEventSourceFlagsState (no TCC permission needed)
    and must return a plain bool without raising."""
    assert hk.modifiers_physically_down() in (True, False)


def test_modifiers_physically_down_masks_only_modifier_bits(monkeypatch):
    """Non-modifier flag bits (e.g. caps lock / alphashift) must not count as a
    held modifier; each of the four modifier masks must."""
    monkeypatch.setattr(
        hk.Quartz, "CGEventSourceFlagsState", lambda state: 0x10000  # alphashift
    )
    assert hk.modifiers_physically_down() is False
    for mask in hk._MODIFIER_MASKS.values():
        monkeypatch.setattr(
            hk.Quartz, "CGEventSourceFlagsState", lambda state, m=mask: m
        )
        assert hk.modifiers_physically_down() is True
    monkeypatch.setattr(hk.Quartz, "CGEventSourceFlagsState", lambda state: 0)
    assert hk.modifiers_physically_down() is False
