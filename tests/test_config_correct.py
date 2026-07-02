"""Tests for the [correct] shortcut config (correct_keys)."""

import pytest

from flow.config import Config, load_config


def test_default_correct_keys():
    assert Config().correct_keys == ["cmd", "alt"]


def test_correct_keys_loaded(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('[correct]\nkeys = ["cmd", "shift"]\n')
    assert load_config(str(p)).correct_keys == ["cmd", "shift"]


def test_correct_table_must_be_table(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('correct = 5\n')
    with pytest.raises(ValueError):
        load_config(str(p))


def test_single_bare_key_correct_falls_back_to_default(tmp_path, capsys):
    """A 1-key combo is shape-valid but unusable as a global shortcut --
    falls back to the default (never raises), and logs the rejection (#26)."""
    p = tmp_path / "config.toml"
    p.write_text('[correct]\nkeys = ["ctrl"]\n')
    cfg = load_config(str(p))
    assert cfg.correct_keys == ["cmd", "alt"]
    out = capsys.readouterr()
    assert "correct.keys" in out.out + out.err


def test_two_keys_no_modifier_correct_falls_back_to_default(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('[correct]\nkeys = ["v", "b"]\n')
    assert load_config(str(p)).correct_keys == ["cmd", "alt"]
