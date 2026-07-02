"""Tests for the [hotkey] shortcut config (keys / dictate combo).

flow.config.validate_keys only checks shape (1-3 non-empty strings);
flow.hotkey.validate_combo enforces the stronger "usable global shortcut"
rule (2-3 keys, at least one modifier). load_config must apply both, never
raising on the combo-usability rule -- a bad config.toml must not prevent
the app from starting (issue #26).
"""

import pytest

from flow.config import Config, load_config


def test_default_hotkey_keys():
    assert Config().keys == ["ctrl", "shift"]


def test_hotkey_keys_loaded(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('[hotkey]\nkeys = ["cmd", "shift"]\n')
    assert load_config(str(p)).keys == ["cmd", "shift"]


def test_hotkey_table_must_be_table(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text("hotkey = 5\n")
    with pytest.raises(ValueError):
        load_config(str(p))


@pytest.mark.parametrize(
    "value",
    [
        "[]",  # empty list -- still a shape violation, must still raise
        '["a", "b", "c", "d"]',  # more than 3
        '["a", 2]',  # non-string entry
        '["a", ""]',  # empty string entry
        '"ctrl"',  # not a list
    ],
)
def test_shape_invalid_hotkey_keys_still_raise(tmp_path, value):
    """Regression: malformed values (wrong type, wrong count, non-string
    entries) are unchanged -- load_config still raises ValueError for these,
    same as before #26."""
    p = tmp_path / "config.toml"
    p.write_text(f"[hotkey]\nkeys = {value}\n")
    with pytest.raises(ValueError):
        load_config(str(p))


def test_single_bare_modifier_hotkey_falls_back_to_default(tmp_path, capsys):
    """keys = ["ctrl"] is a shape-valid 1-item list, but a 1-key global
    hotkey is unusable -- must fall back to the default and log why,
    never raise (issue #26 acceptance test)."""
    p = tmp_path / "config.toml"
    p.write_text('[hotkey]\nkeys = ["ctrl"]\n')
    cfg = load_config(str(p))
    assert cfg.keys == ["ctrl", "shift"]
    out = capsys.readouterr()
    assert "hotkey.keys" in out.out + out.err
    assert "ctrl" in out.out + out.err


def test_single_bare_char_hotkey_falls_back_to_default(tmp_path):
    """keys = ["v"] -- exactly the scenario from the issue: every press of
    the letter v would otherwise arm dictation system-wide."""
    p = tmp_path / "config.toml"
    p.write_text('[hotkey]\nkeys = ["v"]\n')
    assert load_config(str(p)).keys == ["ctrl", "shift"]


def test_two_keys_no_modifier_hotkey_falls_back_to_default(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('[hotkey]\nkeys = ["v", "b"]\n')
    assert load_config(str(p)).keys == ["ctrl", "shift"]


def test_valid_two_key_modifier_combo_still_resolves_unchanged(tmp_path):
    """Regression: a legitimate 2-key modifier-only combo is untouched."""
    p = tmp_path / "config.toml"
    p.write_text('[hotkey]\nkeys = ["ctrl", "alt"]\n')
    assert load_config(str(p)).keys == ["ctrl", "alt"]


def test_valid_three_key_combo_with_character_still_resolves_unchanged(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('[hotkey]\nkeys = ["cmd", "shift", "r"]\n')
    assert load_config(str(p)).keys == ["cmd", "shift", "r"]
