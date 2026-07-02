"""Unit + one real smoke test for flow.secure_input (issue #25).

is_enabled()/culprit_pid()/culprit_name()/describe_culprit() are thin,
individually monkeypatchable wrappers (flow/permissions.py's style) so
flow.menubar's poll can be driven deterministically. The one real test calls
the actual Carbon ctypes symbols directly (EnableSecureEventInput /
DisableSecureEventInput are regular, permission-free Carbon calls any process
can make and revert within itself — no System Settings, no sudo, no lasting
machine-configuration change) and always restores state in a try/finally.
"""

import ctypes
import types

import pytest

import flow.secure_input as si


def _fake_lib(fn):
    """A ctypes.CDLL stand-in: `fn.restype = ...` must work, exactly like a
    real ctypes function pointer — a bound method rejects that assignment,
    so the fake wraps a plain function via SimpleNamespace."""
    return types.SimpleNamespace(IsSecureEventInputEnabled=fn)


# ---------------------------------------------------------------------------
# is_enabled()
# ---------------------------------------------------------------------------


def test_is_enabled_true(monkeypatch):
    monkeypatch.setattr(si, "_carbon", lambda: _fake_lib(lambda: 1))
    assert si.is_enabled() is True


def test_is_enabled_false(monkeypatch):
    monkeypatch.setattr(si, "_carbon", lambda: _fake_lib(lambda: 0))
    assert si.is_enabled() is False


def test_is_enabled_never_raises_when_the_library_call_blows_up(monkeypatch, capsys):
    def _boom():
        raise OSError("dlopen failed")

    monkeypatch.setattr(si, "_carbon", _boom)
    assert si.is_enabled() is False
    assert "IsSecureEventInputEnabled" in capsys.readouterr().out


def test_is_enabled_never_raises_when_the_symbol_itself_raises(monkeypatch):
    def _boom():
        raise RuntimeError("boom")

    monkeypatch.setattr(si, "_carbon", lambda: _fake_lib(_boom))
    assert si.is_enabled() is False


# ---------------------------------------------------------------------------
# culprit_pid() / culprit_name() / describe_culprit()
# ---------------------------------------------------------------------------


class _FakeSession(dict):
    """Stands in for the NSDictionary CGSessionCopyCurrentDictionary returns."""


def test_culprit_pid_resolves_from_session_dictionary(monkeypatch):
    monkeypatch.setattr(
        si.Quartz, "CGSessionCopyCurrentDictionary",
        lambda: _FakeSession({si._PID_KEY: 4242}),
    )
    assert si.culprit_pid() == 4242


def test_culprit_pid_none_when_dictionary_is_none(monkeypatch):
    monkeypatch.setattr(si.Quartz, "CGSessionCopyCurrentDictionary", lambda: None)
    assert si.culprit_pid() is None


def test_culprit_pid_none_when_key_is_absent(monkeypatch):
    monkeypatch.setattr(
        si.Quartz, "CGSessionCopyCurrentDictionary", lambda: _FakeSession({})
    )
    assert si.culprit_pid() is None


def test_culprit_pid_none_when_pid_is_zero_or_negative(monkeypatch):
    monkeypatch.setattr(
        si.Quartz, "CGSessionCopyCurrentDictionary",
        lambda: _FakeSession({si._PID_KEY: 0}),
    )
    assert si.culprit_pid() is None
    monkeypatch.setattr(
        si.Quartz, "CGSessionCopyCurrentDictionary",
        lambda: _FakeSession({si._PID_KEY: -1}),
    )
    assert si.culprit_pid() is None


def test_culprit_pid_none_when_cgsession_raises(monkeypatch, capsys):
    def _boom():
        raise RuntimeError("no session")

    monkeypatch.setattr(si.Quartz, "CGSessionCopyCurrentDictionary", _boom)
    assert si.culprit_pid() is None
    assert "CGSessionCopyCurrentDictionary" in capsys.readouterr().out


def test_culprit_pid_none_without_quartz(monkeypatch):
    monkeypatch.setattr(si, "Quartz", None)
    assert si.culprit_pid() is None


class _FakeApp:
    def __init__(self, name):
        self._name = name

    def localizedName(self):
        return self._name


def test_culprit_name_resolves_via_nsrunningapplication(monkeypatch):
    monkeypatch.setattr(si, "culprit_pid", lambda: 4242)

    class _NSRunningApplication:
        @staticmethod
        def runningApplicationWithProcessIdentifier_(pid):
            assert pid == 4242
            return _FakeApp("Terminal")

    monkeypatch.setattr(si.AppKit, "NSRunningApplication", _NSRunningApplication)
    assert si.culprit_name() == "Terminal"


def test_culprit_name_none_when_pid_unresolved(monkeypatch):
    monkeypatch.setattr(si, "culprit_pid", lambda: None)
    assert si.culprit_name() is None


def test_culprit_name_none_when_pid_belongs_to_no_running_application(monkeypatch):
    """Covers the exited/zombie-process and "background enabler with no
    NSRunningApplication entry" cases the issue calls out explicitly."""
    monkeypatch.setattr(si, "culprit_pid", lambda: 999999)

    class _NSRunningApplication:
        @staticmethod
        def runningApplicationWithProcessIdentifier_(pid):
            return None

    monkeypatch.setattr(si.AppKit, "NSRunningApplication", _NSRunningApplication)
    assert si.culprit_name() is None


def test_culprit_name_none_when_lookup_raises(monkeypatch, capsys):
    monkeypatch.setattr(si, "culprit_pid", lambda: 4242)

    class _NSRunningApplication:
        @staticmethod
        def runningApplicationWithProcessIdentifier_(pid):
            raise RuntimeError("boom")

    monkeypatch.setattr(si.AppKit, "NSRunningApplication", _NSRunningApplication)
    assert si.culprit_name() is None
    assert "NSRunningApplication" in capsys.readouterr().out


def test_culprit_name_none_without_appkit(monkeypatch):
    monkeypatch.setattr(si, "AppKit", None)
    assert si.culprit_name() is None


def test_describe_culprit_falls_back_to_generic_message(monkeypatch):
    monkeypatch.setattr(si, "culprit_name", lambda: None)
    assert si.describe_culprit() == si.GENERIC_BLOCKER == "an app"


def test_describe_culprit_uses_the_resolved_name(monkeypatch):
    monkeypatch.setattr(si, "culprit_name", lambda: "iTerm2")
    assert si.describe_culprit() == "iTerm2"


# ---------------------------------------------------------------------------
# Real smoke test (acceptance criterion): the actual Carbon call, toggled
# and restored entirely within this process — no System Settings, no sudo.
# ---------------------------------------------------------------------------


def test_is_enabled_reflects_the_real_carbon_flag_and_is_fully_reversible():
    lib = ctypes.cdll.LoadLibrary(si._CARBON)
    lib.IsSecureEventInputEnabled.restype = ctypes.c_bool
    was_enabled = bool(lib.IsSecureEventInputEnabled())
    if was_enabled:
        pytest.skip("Secure Input is already on for this process/session — skip to "
                     "avoid disabling something this test did not enable.")
    lib.EnableSecureEventInput()
    try:
        assert si.is_enabled() is True
    finally:
        lib.DisableSecureEventInput()
    assert si.is_enabled() is False
