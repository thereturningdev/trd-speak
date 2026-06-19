"""Tests for the re-paste hotkey config ([repaste] keys)."""

import pytest

from flow.config import Config, load_config


def test_default_repaste_keys():
    assert Config().repaste_keys == ["cmd", "ctrl", "shift"]


def test_missing_repaste_table_keeps_default(tmp_path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text('[hotkey]\nkeys = ["ctrl", "alt"]\n')
    assert load_config(str(cfg_file)).repaste_keys == ["cmd", "ctrl", "shift"]


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
