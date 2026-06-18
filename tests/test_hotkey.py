"""Tests for the event-tap watchdog (ensure_enabled).

macOS disables a CGEventTap whose callback runs too long. The listener must
expose a way to detect that and re-assert the tap so a poll timer can recover
it. These tests stub the Quartz tap calls — no real tap / Input Monitoring
permission is needed.
"""

import flow.hotkey as hk
from flow.hotkey import HotkeyListener


def _listener():
    return HotkeyListener(
        keys=["ctrl", "shift"], on_activate=lambda: None, on_deactivate=lambda: None
    )


def test_ensure_enabled_is_noop_without_a_tap():
    listener = _listener()
    assert listener._tap is None
    assert listener.ensure_enabled() is False


def test_ensure_enabled_reenables_a_disabled_tap(monkeypatch):
    listener = _listener()
    listener._tap = object()  # sentinel standing in for a real tap
    state = {"enabled": False}  # macOS has disabled the tap
    calls = []

    monkeypatch.setattr(hk.Quartz, "CGEventTapIsEnabled", lambda tap: state["enabled"])

    def fake_enable(tap, on):
        calls.append(on)
        state["enabled"] = on

    monkeypatch.setattr(hk.Quartz, "CGEventTapEnable", fake_enable)

    # Disabled -> re-enabled, reports True.
    assert listener.ensure_enabled() is True
    assert calls == [True]
    # Already enabled -> no-op, reports False.
    assert listener.ensure_enabled() is False
    assert calls == [True]
