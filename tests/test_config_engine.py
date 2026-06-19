import pytest

from flow.config import Config, load_config


def test_default_engine_is_whisper():
    assert Config().engine == "whisper"


def test_load_engine_from_toml(tmp_path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text('[engine]\nname = "whisper"\n')
    assert load_config(str(cfg_file)).engine == "whisper"


def test_invalid_engine_name_raises(tmp_path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text('[engine]\nname = "bogus"\n')
    with pytest.raises(ValueError):
        load_config(str(cfg_file))
