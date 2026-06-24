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
