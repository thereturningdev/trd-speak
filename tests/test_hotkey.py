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


def test_take_event_count_reads_and_resets():
    listener = _listener()
    assert listener.take_event_count() == 0
    listener._event_count = 3
    assert listener.take_event_count() == 3  # reads
    assert listener.take_event_count() == 0  # and resets


def test_callback_counts_every_event_for_liveness(monkeypatch):
    """The tap counts that events arrive (never which keys), so the poll can
    tell a live tap from a silently-dead one."""
    listener = _listener()
    # Non-target keycode: exercises the count without firing the combo.
    monkeypatch.setattr(hk.Quartz, "CGEventGetIntegerValueField", lambda e, f: 999)

    listener._tap_callback(None, hk.Quartz.kCGEventKeyDown, object(), None)
    listener._tap_callback(None, hk.Quartz.kCGEventKeyUp, object(), None)

    assert listener.take_event_count() == 2
