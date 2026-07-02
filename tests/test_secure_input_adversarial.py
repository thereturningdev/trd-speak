"""Adversarial regression tests for flow.secure_input (issue #25).

These target cases the happy-path suite in tests/test_secure_input.py does not
cover: garbage/overflow values coming back from CGSessionCopyCurrentDictionary,
an NSRunningApplication lookup that overflows/blows up on an out-of-range pid,
and describe_culprit()'s contract (never raise, never return None, never
literal "None") under adversarial monkeypatching. Every test asserts INTENDED
behaviour per the module's own docstrings, not just whatever the code
currently happens to do.
"""

import types

import pytest

import flow.secure_input as si


class _FakeSession(dict):
    """Stands in for the NSDictionary CGSessionCopyCurrentDictionary returns."""


def _fake_lib(fn):
    return types.SimpleNamespace(IsSecureEventInputEnabled=fn)


# ---------------------------------------------------------------------------
# culprit_pid(): garbage / overflow values for the session dictionary key
# ---------------------------------------------------------------------------


def test_culprit_pid_none_when_value_is_a_non_numeric_string(monkeypatch, capsys):
    monkeypatch.setattr(
        si.Quartz, "CGSessionCopyCurrentDictionary",
        lambda: _FakeSession({si._PID_KEY: "not-a-pid"}),
    )
    assert si.culprit_pid() is None
    # Must have logged instead of silently swallowing (matches the module's
    # own convention of printing on every caught exception).
    assert "CGSessionCopyCurrentDictionary" in capsys.readouterr().out


def test_culprit_pid_none_when_value_is_a_dict(monkeypatch):
    monkeypatch.setattr(
        si.Quartz, "CGSessionCopyCurrentDictionary",
        lambda: _FakeSession({si._PID_KEY: {"nested": "garbage"}}),
    )
    assert si.culprit_pid() is None


def test_culprit_pid_none_when_value_is_a_list(monkeypatch):
    monkeypatch.setattr(
        si.Quartz, "CGSessionCopyCurrentDictionary",
        lambda: _FakeSession({si._PID_KEY: [1, 2, 3]}),
    )
    assert si.culprit_pid() is None


def test_culprit_pid_none_when_value_is_none_type_masquerading_as_nsnull(monkeypatch):
    """NSNull doesn't coerce to int (no __int__); simulate with a bespoke
    object exposing neither __int__ nor __index__."""

    class _FakeNSNull:
        def __repr__(self):
            return "<NSNull>"

    monkeypatch.setattr(
        si.Quartz, "CGSessionCopyCurrentDictionary",
        lambda: _FakeSession({si._PID_KEY: _FakeNSNull()}),
    )
    assert si.culprit_pid() is None


def test_culprit_pid_truncates_float_rather_than_crashing(monkeypatch):
    """A float sneaking into the key coerces via int() truncation instead of
    raising -- documenting current (permissive) behaviour so a regression to
    "raises" or "returns the untruncated float" gets caught."""
    monkeypatch.setattr(
        si.Quartz, "CGSessionCopyCurrentDictionary",
        lambda: _FakeSession({si._PID_KEY: 4242.9}),
    )
    pid = si.culprit_pid()
    assert pid == 4242
    assert isinstance(pid, int)


def test_culprit_pid_huge_out_of_range_number_does_not_raise(monkeypatch):
    """A session dictionary can in principle carry a bogus 64-bit-plus value.
    culprit_pid() itself must not raise, and must not silently clamp/wrap --
    it just passes the (huge) int through; culprit_name() is responsible for
    surviving whatever NSRunningApplication does with it."""
    huge = 2 ** 63  # out of range for a C `pid_t` (signed 32-bit on Darwin)
    monkeypatch.setattr(
        si.Quartz, "CGSessionCopyCurrentDictionary",
        lambda: _FakeSession({si._PID_KEY: huge}),
    )
    assert si.culprit_pid() == huge


# ---------------------------------------------------------------------------
# culprit_name(): NSRunningApplication overflowing / raising on a bogus pid
# ---------------------------------------------------------------------------


def test_culprit_name_none_when_pid_overflows_the_bridge(monkeypatch, capsys):
    """PyObjC raises OverflowError bridging a pid outside pid_t's range to
    the ObjC call -- culprit_name() must swallow this like any other lookup
    failure, not propagate it up into the 2 s poll."""
    monkeypatch.setattr(si, "culprit_pid", lambda: 2 ** 63)

    class _NSRunningApplication:
        @staticmethod
        def runningApplicationWithProcessIdentifier_(pid):
            raise OverflowError("pid_t overflow")

    monkeypatch.setattr(si.AppKit, "NSRunningApplication", _NSRunningApplication)
    assert si.culprit_name() is None
    assert "NSRunningApplication" in capsys.readouterr().out


def test_culprit_name_none_when_localized_name_returns_empty_string(monkeypatch):
    """An app with an empty (not None) localizedName() must still resolve to
    None so describe_culprit() falls back to the generic blocker rather than
    rendering an empty parenthetical "()"."""
    monkeypatch.setattr(si, "culprit_pid", lambda: 4242)

    class _App:
        def localizedName(self):
            return ""

    class _NSRunningApplication:
        @staticmethod
        def runningApplicationWithProcessIdentifier_(pid):
            return _App()

    monkeypatch.setattr(si.AppKit, "NSRunningApplication", _NSRunningApplication)
    assert si.culprit_name() is None


def test_culprit_name_none_when_localized_name_itself_raises(monkeypatch, capsys):
    monkeypatch.setattr(si, "culprit_pid", lambda: 4242)

    class _App:
        def localizedName(self):
            raise RuntimeError("zombie proxy object")

    class _NSRunningApplication:
        @staticmethod
        def runningApplicationWithProcessIdentifier_(pid):
            return _App()

    monkeypatch.setattr(si.AppKit, "NSRunningApplication", _NSRunningApplication)
    assert si.culprit_name() is None
    assert "NSRunningApplication" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# describe_culprit(): never raises, never None, never the literal "None"
# ---------------------------------------------------------------------------


def test_describe_culprit_never_returns_the_literal_none_string(monkeypatch):
    monkeypatch.setattr(si, "culprit_name", lambda: None)
    result = si.describe_culprit()
    assert result != "None"
    assert "None" not in result


def test_describe_culprit_survives_culprit_name_raising(monkeypatch):
    """describe_culprit()'s docstring promises it never raises. culprit_name()
    is documented as never raising either, but describe_culprit() calls it
    with a bare `or`, which offers zero protection if that contract is ever
    violated (e.g. by a future edit). This test locks in the *intended*
    contract from the issue: describe_culprit() must be exception-proof on
    its own, independent of callee discipline."""
    def _boom():
        raise RuntimeError("culprit_name blew up")

    monkeypatch.setattr(si, "culprit_name", _boom)
    try:
        result = si.describe_culprit()
    except Exception as exc:  # pragma: no cover - this is what we're hunting for
        pytest.fail(
            "describe_culprit() must never raise, even if culprit_name() "
            f"does, but it propagated: {exc!r}"
        )
    assert result == si.GENERIC_BLOCKER


# ---------------------------------------------------------------------------
# is_enabled(): additional raise surfaces beyond dlopen/symbol failures
# ---------------------------------------------------------------------------


def test_is_enabled_false_when_restype_assignment_itself_raises(monkeypatch):
    """A library object that rejects the `.restype = ctypes.c_bool`
    assignment (e.g. a bogus/frozen namespace) must still degrade to False,
    not propagate a AttributeError/TypeError out of is_enabled()."""

    class _FrozenLib:
        @property
        def IsSecureEventInputEnabled(self):
            raise AttributeError("symbol not found")

    monkeypatch.setattr(si, "_carbon", lambda: _FrozenLib())
    assert si.is_enabled() is False


def test_is_enabled_false_when_call_returns_non_boolean_garbage(monkeypatch):
    """bool(...) coercion of a nonsensical return value (e.g. a ctypes
    structure or None) must not raise."""
    monkeypatch.setattr(si, "_carbon", lambda: _fake_lib(lambda: None))
    assert si.is_enabled() is False
