import numpy as np
import pytest

from flow.config import Config
from flow.engines import (
    ENGINE_NAMES,
    ENGINES,
    EngineUnavailable,
    Transcriber,
    make_transcriber,
)


def test_registry_lists_whisper():
    names = [e.name for e in ENGINES]
    assert names == ["whisper"]
    assert ENGINE_NAMES == ("whisper",)
    for e in ENGINES:
        assert e.label and e.description


def test_make_whisper_returns_whisper_transcriber():
    from flow.engines.whisper import WhisperTranscriber

    t = make_transcriber("whisper", Config())
    assert isinstance(t, WhisperTranscriber)
    assert isinstance(t, Transcriber)
    assert t.name == "whisper"
    assert t.label  # non-empty


def test_make_unknown_engine_raises():
    with pytest.raises(ValueError):
        make_transcriber("nope", Config())


def test_whisper_too_short_audio_returns_empty_without_loading():
    t = make_transcriber("whisper", Config())
    # 0.1 s < the 0.25 s minimum: must return "" and never touch the model.
    audio = np.zeros(1600, dtype=np.float32)
    assert t.transcribe(audio) == ""
    assert t._model is None


def test_engine_unavailable_is_runtime_error():
    assert issubclass(EngineUnavailable, RuntimeError)
