from flow import hotkey_state
from flow.config import Config


def test_save_then_load_round_trips_all_three_combos(tmp_path):
    p = tmp_path / "hotkeys.json"
    hotkey_state.save(["ctrl", "shift"], ["cmd", "ctrl"], ["cmd", "alt"], path=p)
    assert hotkey_state.load(path=p) == {
        "dictate": ["ctrl", "shift"],
        "repaste": ["cmd", "ctrl"],
        "correct": ["cmd", "alt"],
    }


def test_load_missing_returns_none(tmp_path):
    assert hotkey_state.load(path=tmp_path / "absent") is None


def test_load_garbage_returns_none(tmp_path):
    p = tmp_path / "hotkeys.json"
    p.write_text("not json {{{")
    assert hotkey_state.load(path=p) is None


def test_resolve_prefers_valid_saved_combos(tmp_path):
    p = tmp_path / "hotkeys.json"
    # Saved combos differ from Config() defaults, to prove precedence.
    hotkey_state.save(["cmd", "alt"], ["alt", "shift"], ["ctrl", "alt"], path=p)
    assert hotkey_state.resolve(Config(), path=p) == (
        ["cmd", "alt"], ["alt", "shift"], ["ctrl", "alt"]
    )


def test_resolve_falls_back_per_combo_when_missing(tmp_path):
    p = tmp_path / "hotkeys.json"
    p.write_text('{"dictate": ["cmd", "alt"]}')
    # dictate from the file, repaste and correct from config defaults.
    assert hotkey_state.resolve(Config(), path=p) == (
        ["cmd", "alt"], ["cmd", "ctrl"], ["cmd", "alt"]
    )


def test_resolve_falls_back_per_combo_when_invalid(tmp_path):
    p = tmp_path / "hotkeys.json"
    # dictate is invalid (empty), repaste is valid -> dictate falls back.
    p.write_text('{"dictate": [], "repaste": ["alt", "shift"]}')
    assert hotkey_state.resolve(Config(), path=p) == (
        ["ctrl", "shift"], ["alt", "shift"], ["cmd", "alt"]
    )


def test_resolve_falls_back_to_config_when_no_file(tmp_path):
    assert hotkey_state.resolve(Config(), path=tmp_path / "absent") == (
        ["ctrl", "shift"],
        ["cmd", "ctrl"],
        ["cmd", "alt"],
    )
