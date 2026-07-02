"""Tests for the re-paste hotkey config ([repaste] keys)."""

import pytest

from flow.config import Config, load_config


def test_default_repaste_keys():
    # Modifier-only cmd+ctrl: the live log proves this combo actually re-pastes
    # on a real keyboard, unlike the char-chord path (see flow/config.py).
    assert Config().repaste_keys == ["cmd", "ctrl"]


def test_default_hotkey_keys():
    assert Config().keys == ["ctrl", "shift"]


def test_missing_repaste_table_keeps_default(tmp_path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text('[hotkey]\nkeys = ["ctrl", "alt"]\n')
    assert load_config(str(cfg_file)).repaste_keys == ["cmd", "ctrl"]


def test_load_repaste_keys_from_toml(tmp_path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text('[repaste]\nkeys = ["cmd", "shift", "r"]\n')
    assert load_config(str(cfg_file)).repaste_keys == ["cmd", "shift", "r"]


def test_repaste_keys_are_lowercased(tmp_path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text('[repaste]\nkeys = ["Cmd", "Ctrl", "Shift"]\n')
    assert load_config(str(cfg_file)).repaste_keys == ["cmd", "ctrl", "shift"]


@pytest.mark.parametrize(
    "value",
    [
        "[]",  # empty list
        '["a", "b", "c", "d"]',  # more than 3
        '["a", 2]',  # non-string entry
        '["a", ""]',  # empty string entry
        '"cmd"',  # not a list
    ],
)
def test_invalid_repaste_keys_raise(tmp_path, value):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(f"[repaste]\nkeys = {value}\n")
    with pytest.raises(ValueError):
        load_config(str(cfg_file))


def test_single_bare_modifier_repaste_falls_back_to_default(tmp_path, capsys):
    """A 1-key combo is shape-valid (validate_keys) but unusable as a global
    shortcut (validate_combo) -- must fall back to the default, not raise,
    and must log what was rejected (issue #26)."""
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text('[repaste]\nkeys = ["v"]\n')
    cfg = load_config(str(cfg_file))
    assert cfg.repaste_keys == ["cmd", "ctrl"]
    out = capsys.readouterr()
    assert "repaste.keys" in out.out + out.err


def test_two_keys_no_modifier_repaste_falls_back_to_default(tmp_path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text('[repaste]\nkeys = ["v", "b"]\n')
    assert load_config(str(cfg_file)).repaste_keys == ["cmd", "ctrl"]
