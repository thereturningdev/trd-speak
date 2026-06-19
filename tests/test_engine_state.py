from flow import engine_state


def test_save_then_load(tmp_path):
    p = tmp_path / "engine"
    engine_state.save_engine("other", path=p)
    assert engine_state.load_engine(path=p) == "other"


def test_load_missing_returns_none(tmp_path):
    assert engine_state.load_engine(path=tmp_path / "absent") is None


def test_resolve_prefers_valid_state_file(tmp_path):
    p = tmp_path / "engine"
    engine_state.save_engine("other", path=p)
    assert engine_state.resolve_engine("whisper", ("whisper", "other"), path=p) == "other"


def test_resolve_ignores_invalid_state_file(tmp_path):
    p = tmp_path / "engine"
    p.write_text("bogus")
    assert engine_state.resolve_engine("whisper", ("whisper", "other"), path=p) == "whisper"


def test_resolve_falls_back_to_config_when_no_state(tmp_path):
    assert (
        engine_state.resolve_engine(
            "other", ("whisper", "other"), path=tmp_path / "absent"
        )
        == "other"
    )
