"""Adversarial regression tests for the Secure Keyboard Entry menu/icon
integration (issue #25), targeting flow.menubar._update_secure_input_state
and MenuBar.update_secure_input()/_render().

Complements tests/test_menubar_secure_input.py: every test here targets a
distinct case that suite does not already assert, written against the
INTENDED contract (never raise, row/icon always reflect current reality,
bounded logging, no literal "None", secure-input never suppresses other
rows) rather than merely restating current output.
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
# Stale blocker: culprit process changes WITHOUT an on/off transition
# ---------------------------------------------------------------------------


def test_blocker_updates_when_culprit_changes_while_staying_continuously_active(
    monkeypatch,
):
    """Secure Input can stay on continuously while the responsible process
    changes (e.g. Terminal quits mid-session but loginwindow or another app
    is now the one holding the flag) -- CGSessionCopyCurrentDictionary's pid
    is reported live by the OS every time it is queried. The row must show
    the CURRENT blocker, not whatever was true when Secure Input first
    turned on, since is_enabled() never toggled False in between so
    describe_culprit() is never re-consulted."""
    ui = _ui()
    state = _fresh_state()
    monkeypatch.setattr(secure_input, "is_enabled", lambda: True)
    monkeypatch.setattr(secure_input, "describe_culprit", lambda: "Terminal")

    _update_secure_input_state(state, ui)
    _, title = _row(ui)
    assert "Terminal" in title

    # Secure Input never turns off; the responsible process changes underneath.
    monkeypatch.setattr(secure_input, "describe_culprit", lambda: "Slack")
    _update_secure_input_state(state, ui)

    _, title = _row(ui)
    assert "Slack" in title, (
        "row still shows the stale culprit ('Terminal') after the real "
        f"blocker changed to Slack while is_enabled() stayed True; got: {title!r}"
    )


def test_blocker_stays_current_across_many_same_active_ticks(monkeypatch):
    """Same bug, driven with a sequence of tick-by-tick culprit changes
    (simulating repeated 2 s polls) to make sure it isn't a one-off."""
    ui = _ui()
    state = _fresh_state()
    monkeypatch.setattr(secure_input, "is_enabled", lambda: True)

    names = iter(["A", "B", "C"])
    monkeypatch.setattr(secure_input, "describe_culprit", lambda: next(names))

    _update_secure_input_state(state, ui)
    assert "A" in _row(ui)[1]
    _update_secure_input_state(state, ui)
    assert "B" in _row(ui)[1]
    _update_secure_input_state(state, ui)
    assert "C" in _row(ui)[1]


# ---------------------------------------------------------------------------
# describe_culprit() raising must not crash the poll tick
# ---------------------------------------------------------------------------


def test_describe_culprit_raising_on_transition_does_not_crash_the_poll(
    monkeypatch, capsys
):
    """secure_input.describe_culprit() is documented to never raise, but
    _update_secure_input_state() calls it completely unguarded
    (`blocker = secure_input.describe_culprit() if active else None`, no
    try/except). If that contract is ever violated -- e.g. a future edit to
    describe_culprit(), or culprit_name() somehow escaping its own try block
    -- the entire 2 s poll tick dies. This locks in the INTENDED
    defense-in-depth: the poll must survive regardless."""
    ui = _ui()
    state = _fresh_state()
    monkeypatch.setattr(secure_input, "is_enabled", lambda: True)

    def _boom():
        raise RuntimeError("describe_culprit blew up unexpectedly")

    monkeypatch.setattr(secure_input, "describe_culprit", _boom)

    try:
        _update_secure_input_state(state, ui)  # must not raise
    except Exception as exc:
        pytest.fail(
            "_update_secure_input_state() must never raise even if "
            f"describe_culprit() does, but it propagated: {exc!r}"
        )


# ---------------------------------------------------------------------------
# Icon priority: secure input must win over the restart-needed icon too
# ---------------------------------------------------------------------------


def test_icon_overrides_restart_needed_state(monkeypatch):
    ui = _ui()
    state = _fresh_state()
    ui.set_restart_needed()
    assert _icon(ui) == menubar._STATE_ICONS["permissions"]

    monkeypatch.setattr(secure_input, "is_enabled", lambda: True)
    monkeypatch.setattr(secure_input, "describe_culprit", lambda: "Terminal")
    _update_secure_input_state(state, ui)

    assert _icon(ui) == menubar._STATE_ICONS["secure_input"], (
        "Secure Input icon must take priority over the restart-needed icon "
        "per _render()'s own comment (\"Secure Input overrides the icon "
        "LAST and unconditionally\")"
    )


def test_restart_row_still_shown_alongside_secure_input(monkeypatch):
    """The restart-needed row is a separate row from the icon; secure input
    must not hide it."""
    ui = _ui()
    state = _fresh_state()
    ui.set_restart_needed()
    monkeypatch.setattr(secure_input, "is_enabled", lambda: True)
    monkeypatch.setattr(secure_input, "describe_culprit", lambda: "Terminal")
    _update_secure_input_state(state, ui)
    ui._render()

    assert not ui._restart_item.isHidden()
    assert not ui._secure_input_row.isHidden()


# ---------------------------------------------------------------------------
# Bounded logging under sustained steady state (not just 5 repeats)
# ---------------------------------------------------------------------------


def test_log_output_stays_bounded_across_fifty_identical_ticks(monkeypatch, capsys):
    ui = _ui()
    state = _fresh_state()
    monkeypatch.setattr(secure_input, "is_enabled", lambda: True)
    monkeypatch.setattr(secure_input, "describe_culprit", lambda: "Terminal")

    _update_secure_input_state(state, ui)
    capsys.readouterr()  # discard the initial transition log

    for _ in range(50):
        _update_secure_input_state(state, ui)

    out = capsys.readouterr().out
    assert out == "", f"expected no further log lines across 50 steady ticks, got: {out!r}"


# ---------------------------------------------------------------------------
# Weird blocker values reaching the NSMenuItem title
# ---------------------------------------------------------------------------


def test_blocker_with_format_specifiers_rendered_literally_not_as_format_string(
    monkeypatch,
):
    """setTitle_ takes a plain NSString, not a format string, but this locks
    in that a culprit name containing %@ / %s / %n never gets interpreted as
    one (a classic NSString(format:) injection footgun) and never crashes."""
    ui = _ui()
    monkeypatch.setattr(secure_input, "is_enabled", lambda: True)
    monkeypatch.setattr(secure_input, "describe_culprit", lambda: "%@%s%n%d")
    state = _fresh_state()

    _update_secure_input_state(state, ui)
    _, title = _row(ui)
    assert "%@%s%n%d" in title


def test_blocker_very_long_string_does_not_raise(monkeypatch):
    ui = _ui()
    long_name = "A" * 5000
    monkeypatch.setattr(secure_input, "is_enabled", lambda: True)
    monkeypatch.setattr(secure_input, "describe_culprit", lambda: long_name)
    state = _fresh_state()

    _update_secure_input_state(state, ui)
    hidden, title = _row(ui)
    assert not hidden
    assert long_name in title


def test_blocker_with_newlines_and_control_chars_does_not_raise(monkeypatch):
    ui = _ui()
    weird = "Ev\nil\tApp\x00Name  "
    monkeypatch.setattr(secure_input, "is_enabled", lambda: True)
    monkeypatch.setattr(secure_input, "describe_culprit", lambda: weird)
    state = _fresh_state()

    _update_secure_input_state(state, ui)
    hidden, title = _row(ui)
    assert not hidden


def test_blocker_empty_string_falls_back_to_generic_not_empty_parens(monkeypatch):
    ui = _ui()
    monkeypatch.setattr(secure_input, "is_enabled", lambda: True)
    monkeypatch.setattr(secure_input, "describe_culprit", lambda: "")
    state = _fresh_state()

    _update_secure_input_state(state, ui)
    _, title = _row(ui)
    assert "()" not in title
    assert secure_input.GENERIC_BLOCKER in title


def test_update_secure_input_direct_call_with_none_blocker_never_shows_none(monkeypatch):
    """Focus area 7: update_secure_input(True, None) called directly (not
    via the poll helper) must show the generic fallback, never the literal
    string 'None'."""
    ui = _ui()
    ui.update_secure_input(True, None)
    ui._render()

    hidden, title = _row(ui)
    assert not hidden
    assert "None" not in title
    assert secure_input.GENERIC_BLOCKER in title


def test_update_secure_input_accepts_a_non_string_blocker_without_raising(monkeypatch):
    """Nothing in update_secure_input()'s signature enforces `blocker` is a
    str; if a future caller passes a non-string (e.g. an int pid instead of
    a resolved name), _render() must not crash formatting the row title."""
    ui = _ui()
    ui.update_secure_input(True, 4242)  # type: ignore[arg-type]

    try:
        ui._render()
    except Exception as exc:
        pytest.fail(f"_render() must not raise on a non-string blocker, got: {exc!r}")


# ---------------------------------------------------------------------------
# Rapid flapping: last-observed value must win, nothing sticks
# ---------------------------------------------------------------------------


def test_three_immediate_flaps_leave_row_matching_the_final_observed_value(monkeypatch):
    ui = _ui()
    state = _fresh_state()
    sequence = iter([True, False, True])
    monkeypatch.setattr(secure_input, "is_enabled", lambda: next(sequence))
    monkeypatch.setattr(secure_input, "describe_culprit", lambda: "Terminal")

    for _ in range(3):
        _update_secure_input_state(state, ui)

    hidden, _ = _row(ui)
    assert not hidden, "after True/False/True the row must be visible (last value True)"
    assert _icon(ui) == menubar._STATE_ICONS["secure_input"]


def test_many_flaps_never_leave_icon_stuck_on_secure_input_after_final_off(monkeypatch):
    ui = _ui()
    state = _fresh_state()
    sequence = iter([True, False] * 10 + [False])
    monkeypatch.setattr(secure_input, "is_enabled", lambda: next(sequence))
    monkeypatch.setattr(secure_input, "describe_culprit", lambda: "Terminal")

    for _ in range(21):
        _update_secure_input_state(state, ui)

    hidden, _ = _row(ui)
    assert hidden
    assert _icon(ui) != menubar._STATE_ICONS["secure_input"]


# ---------------------------------------------------------------------------
# Combined permissions onboarding + hotkey-degraded + secure-input, all at once
# ---------------------------------------------------------------------------


def test_all_three_signal_rows_coexist_without_exception(monkeypatch):
    """Onboarding rows, the #22 degraded-shortcuts row, and the #25
    secure-input row simultaneously active must not raise and must all
    render sensibly together -- the union of the two existing suites'
    individual-pair checks, run as one triple combination."""
    ui = _ui()
    state = _fresh_state()
    ui.update_permissions({"listen": False, "post": True, "mic": "granted"})
    ui.update_hotkey_failures(("dictation", "repaste"))
    monkeypatch.setattr(secure_input, "is_enabled", lambda: True)
    monkeypatch.setattr(secure_input, "describe_culprit", lambda: "Terminal")

    _update_secure_input_state(state, ui)
    try:
        ui._render()
    except Exception as exc:
        pytest.fail(f"_render() must not raise with all three signals active: {exc!r}")

    listen_item = ui._perm_items["listen"]
    assert not listen_item.isHidden()
    assert listen_item.isEnabled()
    assert not ui._hotkey_warning.isHidden()
    assert not ui._secure_input_row.isHidden()
