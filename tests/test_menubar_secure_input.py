"""Functional tests for Secure Keyboard Entry detection in the menu (#25).

Drives the REAL MenuBar (real NSMenuItem rows) and the real poll-tick helper
flow.menubar._update_secure_input_state — the same function flow.menubar.run's
2 s NSTimer calls — with only flow.secure_input's is_enabled()/
describe_culprit() monkeypatched (no real Secure Input toggle needed for
determinism; flow/secure_input.py's own real-Carbon smoke test already covers
that end of the seam).
"""

import pytest

pytest.importorskip("AppKit")

from flow import menubar, secure_input
from flow.menubar import MenuBar, _Delegate, _update_secure_input_state


def _ui():
    return MenuBar("ctrl+shift", _Delegate.alloc().init())


def _fresh_state():
    return {"secure_input_active": False, "secure_input_blocker": None}


def _row(ui):
    ui._render()
    return bool(ui._secure_input_row.isHidden()), str(ui._secure_input_row.title())


def _icon(ui):
    ui._render()
    return str(ui._item.button().title())


# ---------------------------------------------------------------------------
# Acceptance criteria: row appears/disappears within one poll tick
# ---------------------------------------------------------------------------


def test_row_appears_within_one_tick_when_secure_input_turns_on(monkeypatch):
    ui = _ui()
    state = _fresh_state()
    monkeypatch.setattr(secure_input, "is_enabled", lambda: True)
    monkeypatch.setattr(secure_input, "describe_culprit", lambda: "Terminal")

    _update_secure_input_state(state, ui)

    hidden, title = _row(ui)
    assert not hidden
    assert title == "⚠️ Secure Input is on — shortcuts are paused by macOS (Terminal)"


def test_row_disappears_within_one_tick_when_secure_input_turns_off(monkeypatch):
    ui = _ui()
    state = _fresh_state()
    monkeypatch.setattr(secure_input, "is_enabled", lambda: True)
    monkeypatch.setattr(secure_input, "describe_culprit", lambda: "Terminal")
    _update_secure_input_state(state, ui)
    assert not _row(ui)[0]

    monkeypatch.setattr(secure_input, "is_enabled", lambda: False)
    _update_secure_input_state(state, ui)

    hidden, _ = _row(ui)
    assert hidden


def test_row_hidden_by_default_before_any_poll_tick():
    ui = _ui()
    hidden, _ = _row(ui)
    assert hidden


# ---------------------------------------------------------------------------
# Icon
# ---------------------------------------------------------------------------


def test_icon_switches_to_the_distinct_secure_input_glyph(monkeypatch):
    ui = _ui()
    state = _fresh_state()
    ui.set_state("ready")
    assert _icon(ui) == menubar._STATE_ICONS["ready"]

    monkeypatch.setattr(secure_input, "is_enabled", lambda: True)
    monkeypatch.setattr(secure_input, "describe_culprit", lambda: "iTerm2")
    _update_secure_input_state(state, ui)

    assert _icon(ui) == menubar._STATE_ICONS["secure_input"]
    assert menubar._STATE_ICONS["secure_input"] not in (
        menubar._STATE_ICONS["ready"],
        menubar._STATE_ICONS["permissions"],
        menubar._STATE_ICONS["waiting"],
        menubar._STATE_ICONS["recording"],
        menubar._STATE_ICONS["processing"],
    )


def test_icon_reverts_to_the_normal_state_icon_once_secure_input_clears(monkeypatch):
    ui = _ui()
    state = _fresh_state()
    ui.set_state("ready")
    monkeypatch.setattr(secure_input, "is_enabled", lambda: True)
    monkeypatch.setattr(secure_input, "describe_culprit", lambda: "iTerm2")
    _update_secure_input_state(state, ui)
    assert _icon(ui) == menubar._STATE_ICONS["secure_input"]

    monkeypatch.setattr(secure_input, "is_enabled", lambda: False)
    _update_secure_input_state(state, ui)
    assert _icon(ui) == menubar._STATE_ICONS["ready"]


# ---------------------------------------------------------------------------
# Culprit resolution (real describe_culprit(), mocking only its inputs)
# ---------------------------------------------------------------------------


def test_culprit_name_included_in_the_row_text(monkeypatch):
    ui = _ui()
    state = _fresh_state()
    monkeypatch.setattr(secure_input, "is_enabled", lambda: True)
    monkeypatch.setattr(secure_input, "culprit_name", lambda: "Slack")

    _update_secure_input_state(state, ui)

    _, title = _row(ui)
    assert "Slack" in title


def test_culprit_resolution_failure_falls_back_to_generic_message_no_crash(monkeypatch):
    ui = _ui()
    state = _fresh_state()
    monkeypatch.setattr(secure_input, "is_enabled", lambda: True)
    monkeypatch.setattr(secure_input, "culprit_name", lambda: None)  # unresolved

    _update_secure_input_state(state, ui)  # must not raise

    _, title = _row(ui)
    assert secure_input.GENERIC_BLOCKER in title
    assert "None" not in title


def test_zombie_pid_and_missing_dictionary_key_both_resolve_to_generic(monkeypatch):
    """culprit_pid() itself raises/returns None for a whole family of cases
    (missing key, zombie pid, CGSessionCopyCurrentDictionary() == None) —
    describe_culprit() must swallow all of them into the same fallback."""
    ui = _ui()
    state = _fresh_state()
    monkeypatch.setattr(secure_input, "is_enabled", lambda: True)
    monkeypatch.setattr(secure_input, "culprit_pid", lambda: None)

    _update_secure_input_state(state, ui)

    _, title = _row(ui)
    assert title.endswith(f"({secure_input.GENERIC_BLOCKER})")


# ---------------------------------------------------------------------------
# Transition logging
# ---------------------------------------------------------------------------


def test_transition_on_is_logged_with_the_culprit(monkeypatch, capsys):
    ui = _ui()
    state = _fresh_state()
    monkeypatch.setattr(secure_input, "is_enabled", lambda: True)
    monkeypatch.setattr(secure_input, "describe_culprit", lambda: "Terminal")

    _update_secure_input_state(state, ui)

    out = capsys.readouterr().out
    assert "Secure" in out and "ON" in out.upper() and "Terminal" in out


def test_transition_off_is_logged(monkeypatch, capsys):
    ui = _ui()
    state = _fresh_state()
    monkeypatch.setattr(secure_input, "is_enabled", lambda: True)
    monkeypatch.setattr(secure_input, "describe_culprit", lambda: "Terminal")
    _update_secure_input_state(state, ui)
    capsys.readouterr()  # discard the ON log

    monkeypatch.setattr(secure_input, "is_enabled", lambda: False)
    _update_secure_input_state(state, ui)

    out = capsys.readouterr().out
    assert "off" in out.lower()


def test_steady_state_does_not_re_log_every_tick(monkeypatch, capsys):
    """Only the TRANSITION is logged — repeated ticks with the same value
    must not spam the log (a real 2 s poll would otherwise flood it)."""
    ui = _ui()
    state = _fresh_state()
    monkeypatch.setattr(secure_input, "is_enabled", lambda: True)
    monkeypatch.setattr(secure_input, "describe_culprit", lambda: "Terminal")

    _update_secure_input_state(state, ui)
    capsys.readouterr()  # discard the first transition's log
    for _ in range(5):
        _update_secure_input_state(state, ui)

    assert capsys.readouterr().out == ""


def test_rapid_flapping_across_ticks_logs_each_transition(monkeypatch, capsys):
    ui = _ui()
    state = _fresh_state()
    values = iter([True, False, True, False])
    monkeypatch.setattr(secure_input, "is_enabled", lambda: next(values))
    monkeypatch.setattr(secure_input, "describe_culprit", lambda: "Terminal")

    for _ in range(4):
        _update_secure_input_state(state, ui)

    out = capsys.readouterr().out
    assert out.count("Secure Keyboard Entry") == 4


# ---------------------------------------------------------------------------
# Resilience: the check itself must never crash the poll
# ---------------------------------------------------------------------------


def test_is_enabled_raising_does_not_crash_the_poll_tick(monkeypatch, capsys):
    ui = _ui()
    state = _fresh_state()

    def _boom():
        raise RuntimeError("ctypes exploded")

    monkeypatch.setattr(secure_input, "is_enabled", _boom)

    _update_secure_input_state(state, ui)  # must not raise

    hidden, _ = _row(ui)
    assert hidden  # stayed in the previous (inactive) state
    assert "Secure Input check failed" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Interaction with existing states (#22 degraded-shortcuts row, permissions)
# ---------------------------------------------------------------------------


def test_secure_input_row_coexists_with_the_degraded_shortcuts_row(monkeypatch):
    ui = _ui()
    state = _fresh_state()
    ui.update_hotkey_failures(("dictation",))
    monkeypatch.setattr(secure_input, "is_enabled", lambda: True)
    monkeypatch.setattr(secure_input, "describe_culprit", lambda: "Terminal")

    _update_secure_input_state(state, ui)
    ui._render()

    assert not ui._hotkey_warning.isHidden()
    assert not ui._secure_input_row.isHidden()


def test_secure_input_does_not_hide_or_disable_permission_rows(monkeypatch):
    """Onboarding (permissions missing) must render exactly as before; the
    secure-input row/icon are additive, never suppressing the onboarding
    step rows."""
    ui = _ui()
    state = _fresh_state()
    ui.update_permissions({"listen": False, "post": True, "mic": "granted"})
    monkeypatch.setattr(secure_input, "is_enabled", lambda: True)
    monkeypatch.setattr(secure_input, "describe_culprit", lambda: "Terminal")

    _update_secure_input_state(state, ui)
    ui._render()

    listen_item = ui._perm_items["listen"]
    assert not listen_item.isHidden()
    assert listen_item.isEnabled()
    assert not ui._secure_input_row.isHidden()


def test_update_secure_input_can_be_called_directly_before_first_poll_tick():
    """MenuBar.update_secure_input is the public thread-safe setter poll()
    drives via _update_secure_input_state; also exercise it directly."""
    ui = _ui()
    ui.update_secure_input(True, "Terminal")
    ui._render()
    assert not ui._secure_input_row.isHidden()
    ui.update_secure_input(False, None)
    ui._render()
    assert ui._secure_input_row.isHidden()
