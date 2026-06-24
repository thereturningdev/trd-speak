"""Tests for WhisperTranscriber hotwords pass-through (Tier-A vocabulary bias)."""

import numpy as np
from flow.engines.whisper import WhisperTranscriber


class _FakeModel:
    def __init__(self): self.kwargs = None
    def transcribe(self, audio, **kw):
        self.kwargs = kw
        class S: text = "hello"
        return [S()], object()


def _ready(monkeypatch):
    t = WhisperTranscriber()
    fake = _FakeModel()
    monkeypatch.setattr(t, "load", lambda: None)
    t._model = fake
    return t, fake


def test_hotwords_passed_through(monkeypatch):
    t, fake = _ready(monkeypatch)
    audio = np.ones(16000, dtype=np.float32)
    t.transcribe(audio, hotwords="GitHub, Claude")
    assert fake.kwargs["hotwords"] == "GitHub, Claude"
    assert "prefix" not in fake.kwargs  # must never be set


def test_empty_hotwords_is_none(monkeypatch):
    t, fake = _ready(monkeypatch)
    t.transcribe(np.ones(16000, dtype=np.float32), hotwords="")
    assert fake.kwargs["hotwords"] is None
