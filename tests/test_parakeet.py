import importlib.util

import numpy as np
import pytest

from flow.config import Config
from flow.engines import EngineUnavailable, Transcriber, make_transcriber

_HAS_PARAKEET = importlib.util.find_spec("parakeet_mlx") is not None


def test_parakeet_instance_metadata():
    t = make_transcriber("parakeet", Config())
    assert isinstance(t, Transcriber)
    assert t.name == "parakeet"
    assert t.label


def test_parakeet_too_short_returns_empty():
    t = make_transcriber("parakeet", Config())
    assert t.transcribe(np.zeros(1600, dtype=np.float32)) == ""


@pytest.mark.skipif(_HAS_PARAKEET, reason="parakeet-mlx IS installed")
def test_parakeet_load_raises_when_missing():
    t = make_transcriber("parakeet", Config())
    with pytest.raises(EngineUnavailable):
        t.load()


@pytest.mark.skipif(not _HAS_PARAKEET, reason="parakeet-mlx not installed")
def test_parakeet_transcribes_real_clip():
    # 0.5 s of silence exercises the no-ffmpeg array path without asserting on
    # recognized words.
    t = make_transcriber("parakeet", Config())
    t.load()
    out = t.transcribe(np.zeros(8000, dtype=np.float32))
    assert isinstance(out, str)
    t.unload()
    assert t._model is None
