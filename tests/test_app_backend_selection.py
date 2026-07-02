"""Backend selection: Carbon for key+modifier combos, the tap hub for
modifier-only combos (issue #23).

The Carbon layer is mocked at flow.carbon_hotkey's module seams; the tap side
reuses the HotkeyListener start/stop monkeypatch pattern from
tests/test_app_hotkeys.py. No real Quartz tap and no real Carbon registration
is ever created.
"""

import pytest

import flow.carbon_hotkey as ch
import flow.app as app_mod
from flow import menubar
from flow.app import App, make_hotkey
from flow.carbon_hotkey import CarbonHotkey
from flow.config import Config
from flow.event_tap import EventTapHub
from flow.hotkey import HotkeyListener


@pytest.fixture
def carbon(monkeypatch):
    """Mock the Carbon seams; record registrations/unregistrations."""
    calls = {"registered": {}, "unregistered": [], "next_ref": 0, "fail": False}

    def fake_register(vk, mask, hkid):
        if calls["fail"]:
            return -9878, None
        calls["next_ref"] += 1
        ref = f"ref-{calls['next_ref']}"
        calls["registered"][hkid] = (vk, mask, ref)
        return 0, ref

    def fake_unregister(ref):
        calls["unregistered"].append(ref)
        calls["registered"] = {
            k: v for k, v in calls["registered"].items() if v[2] != ref
        }
        return 0

    monkeypatch.setattr(ch, "_register", fake_register)
    monkeypatch.setattr(ch, "_unregister", fake_unregister)
    monkeypatch.setattr(ch, "_ensure_handler", lambda: None)
    return calls


@pytest.fixture
def hub_registry(monkeypatch):
    """Record EventTapHub registrations without creating a real tap; make any
    CG tap/preflight touch blow up loudly (the Carbon path must never get
    there)."""
    registered = []

    def fake_register(self, listener):
        registered.append(listener)

    monkeypatch.setattr(EventTapHub, "register", fake_register)
    monkeypatch.setattr(EventTapHub, "unregister", lambda self, listener: None)
    import flow.event_tap as et

    def forbidden(*args, **kwargs):
        raise AssertionError("CGEventTapCreate must not be touched")

    monkeypatch.setattr(et.Quartz, "CGEventTapCreate", forbidden)
    return registered


@pytest.fixture
def app(monkeypatch, tmp_path, carbon, hub_registry):
    monkeypatch.setattr(
        app_mod.engine_state,
        "save_engine",
        lambda name, path=tmp_path / "engine": path.write_text(name),
    )
    cfg = Config()
    cfg.repaste_keys = ["cmd", "shift", "r"]  # Carbon-flavored from boot
    return App(cfg)


# -- the factory ---------------------------------------------------------------

def test_factory_routes_modifier_only_to_the_tap_listener():
    hub = EventTapHub()
    lis = make_hotkey(["ctrl", "shift"], hub=hub, on_activate=lambda: None,
                      on_deactivate=lambda: None, name="dictation")
    assert isinstance(lis, HotkeyListener)
    assert lis._hub is hub


def test_factory_routes_key_plus_modifier_to_carbon(carbon):
    lis = make_hotkey(["cmd", "shift", "r"], hub=EventTapHub(),
                      on_trigger=lambda: None, name="re-paste")
    assert isinstance(lis, CarbonHotkey)


def test_factory_routes_multi_char_combo_to_the_tap_listener():
    """Carbon can hold exactly one virtual key: a (hand-edited) two-character
    combo stays on the tap listener, which supports it."""
    lis = make_hotkey(["cmd", "a", "b"], hub=EventTapHub(),
                      on_trigger=lambda: None, name="re-paste")
    assert isinstance(lis, HotkeyListener)


def test_factory_rejects_invalid_keys():
    with pytest.raises(ValueError):
        make_hotkey(["cmd", "banana"], hub=EventTapHub(),
                    on_trigger=lambda: None, name="re-paste")


# -- App construction: both backends coexist ------------------------------------

def test_app_mixes_backends_from_config(app):
    assert isinstance(app.hotkey, HotkeyListener)  # ctrl+shift default
    assert isinstance(app.repaste_hotkey, CarbonHotkey)  # cmd+shift+r
    assert isinstance(app.correction_hotkey, HotkeyListener)  # cmd+alt default


def test_default_config_stays_entirely_on_the_tap(monkeypatch, tmp_path, carbon,
                                                  hub_registry):
    """Regression: the shipped defaults are modifier-only — no migration, all
    three listeners stay on the tap hub."""
    monkeypatch.setattr(
        app_mod.engine_state, "save_engine", lambda name: None
    )
    a = App(Config())
    for _label, lis in a.iter_hotkeys():
        assert isinstance(lis, HotkeyListener)


def test_carbon_path_never_touches_the_tap_hub_or_cg(app, carbon, hub_registry):
    """Acceptance (a): a key+modifier combo works with Input Monitoring
    revoked. CGEventTapCreate raises AssertionError if touched (fixture), and
    the hub registration list must contain only the two tap listeners."""
    app._start_all_hotkeys()
    assert app.hotkey in hub_registry
    assert app.correction_hotkey in hub_registry
    assert app.repaste_hotkey not in hub_registry
    assert len(carbon["registered"]) == 1  # the Carbon re-paste registration


def test_iter_hotkeys_reports_all_three_across_backends(app):
    labels = {label: lis for label, lis in app.iter_hotkeys()}
    assert set(labels) == {"Hotkey", "Re-paste", "Correction"}
    assert labels["Re-paste"] is app.repaste_hotkey


# -- set_hotkeys: swapping a combo between backends ------------------------------

def test_set_hotkeys_moves_a_combo_from_tap_to_carbon(app, carbon):
    app._start_all_hotkeys()
    app.set_hotkeys(["ctrl", "shift"], ["cmd", "shift", "r"], ["cmd", "alt", "c"])
    assert isinstance(app.correction_hotkey, CarbonHotkey)
    assert app.config.correct_keys == ["cmd", "alt", "c"]
    assert len(carbon["registered"]) == 2  # re-paste + correction


def test_set_hotkeys_moves_a_combo_from_carbon_back_to_tap(app, carbon,
                                                           hub_registry):
    app._start_all_hotkeys()
    app.set_hotkeys(["ctrl", "shift"], ["cmd", "ctrl"], ["cmd", "alt"])
    assert isinstance(app.repaste_hotkey, HotkeyListener)
    # The old Carbon registration is gone (unregistered on stop).
    assert carbon["registered"] == {}
    assert app.repaste_hotkey in hub_registry


def test_set_hotkeys_invalid_combo_keeps_current_shortcuts(app, carbon):
    old = app.repaste_hotkey
    app.set_hotkeys(["ctrl", "shift"], ["cmd", "banana"], ["cmd", "alt"])
    assert app.repaste_hotkey is old
    assert app.config.repaste_keys == ["cmd", "shift", "r"]


def test_set_hotkeys_swap_mid_hold_fires_balancing_deactivate(app, carbon):
    """Swapping the dictate combo to Carbon while push-to-talk is held must
    stop the recording (balancing on_deactivate from the old listener's
    stop()), not leave it running to max_seconds."""
    events = []
    app.hotkey._on_deactivate = lambda: events.append("off")
    app._start_all_hotkeys()
    # Simulate an active hold on the (tap-backed) dictate listener.
    with app.hotkey._cond:
        app.hotkey._active = True
    app.set_hotkeys(["cmd", "ctrl", "d"], ["cmd", "shift", "r"], ["cmd", "alt"])
    assert events == ["off"]
    assert isinstance(app.hotkey, CarbonHotkey)


# -- failure isolation & watchdog retry across backends --------------------------

def test_carbon_start_failure_is_reported_and_retried(app, carbon):
    reported = []
    app.on_hotkeys_degraded = lambda labels: reported.append(tuple(labels))
    carbon["fail"] = True

    app.resume_hotkeys()  # must not raise
    assert reported[-1] == ("Re-paste",)

    # Registration heals (e.g. the clashing app quit): the watchdog recovers it.
    carbon["fail"] = False
    assert menubar.reenable_disabled_taps(app) == ["Re-paste"]
    assert reported[-1] == ()
    assert len(carbon["registered"]) == 1


def test_carbon_failure_does_not_strand_the_tap_listeners(app, carbon,
                                                          hub_registry):
    carbon["fail"] = True
    app.resume_hotkeys()
    assert app.hotkey in hub_registry
    assert app.correction_hotkey in hub_registry


# -- suspend / resume with Carbon listeners --------------------------------------

def test_suspend_unregisters_carbon_hotkeys(app, carbon):
    """A registered Carbon hotkey CONSUMES its chord system-wide, so while the
    settings recorder is capturing, the combo would never reach the window's
    local monitor — suspend must unregister Carbon hotkeys, not just mute the
    hub."""
    app._start_all_hotkeys()
    assert len(carbon["registered"]) == 1

    app.suspend_hotkeys()
    assert carbon["registered"] == {}
    assert app.tap_hub._muted is True

    app.resume_hotkeys()
    assert len(carbon["registered"]) == 1
    assert app.tap_hub._muted is False


def test_watchdog_does_not_resurrect_suspended_carbon_hotkeys(app, carbon):
    app._start_all_hotkeys()
    app.suspend_hotkeys()
    assert menubar.reenable_disabled_taps(app) == []
    assert carbon["registered"] == {}


def test_shutdown_unregisters_carbon_hotkeys(app, carbon):
    app._start_all_hotkeys()
    app.shutdown()
    assert carbon["registered"] == {}


# -- settings window flavor line --------------------------------------------------

def test_backend_description_for_the_status_line():
    assert ch.combo_backend_description(["cmd", "shift", "r"]) == (
        "Maximum-reliability shortcut (no permissions needed)."
    )
    assert ch.combo_backend_description(["ctrl", "shift"]) == (
        "Modifier-only — uses the keyboard tap (needs Input Monitoring)."
    )


def test_backend_description_never_raises_on_garbage():
    """The status line is decoration; a half-recorded/unknown combo must not
    blow up the recorder callback."""
    assert isinstance(ch.combo_backend_description(["banana"]), str)
    assert isinstance(ch.combo_backend_description([]), str)
