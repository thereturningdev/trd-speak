"""Pluggable local transcription engines.

This package keeps the heavy, engine-specific imports (CTranslate2, MLX) out
of module import time: the concrete classes are imported lazily inside
``make_transcriber`` so that a missing or broken optional engine dependency
never affects the others. ``App`` depends only on the ``Transcriber`` interface.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass

import numpy as np


class EngineUnavailable(RuntimeError):
    """Raised when an engine cannot be used (e.g. its package isn't installed)."""


class Transcriber(abc.ABC):
    """Common interface for all transcription engines.

    Audio is always 16 kHz mono float32. ``transcribe`` returns "" for audio
    shorter than the engine's minimum (no model work for empty/blip input).
    """

    name: str = ""
    label: str = ""

    @abc.abstractmethod
    def load(self) -> None:
        """Instantiate and warm up the model. Idempotent."""

    @abc.abstractmethod
    def transcribe(self, audio: np.ndarray) -> str:
        """Transcribe 16 kHz mono float32 audio to text."""

    def unload(self) -> None:
        """Release the model and its memory. Idempotent. Default: no-op."""


@dataclass(frozen=True)
class EngineInfo:
    """Static, import-free metadata for building the menu."""

    name: str
    label: str
    description: str


ENGINES: tuple[EngineInfo, ...] = (
    EngineInfo("whisper", "faster-whisper", "CPU · light · default"),
)

ENGINE_NAMES: tuple[str, ...] = tuple(e.name for e in ENGINES)


def make_transcriber(name: str, config) -> Transcriber:
    """Return an unloaded engine instance for ``name``.

    Concrete classes are imported here (lazily) so a missing optional engine
    dependency does not break the others.
    """
    if name == "whisper":
        from flow.engines.whisper import WhisperTranscriber

        return WhisperTranscriber(
            model_name=config.model,
            compute_type=config.compute_type,
            beam_size=config.beam_size,
        )
    raise ValueError(f"unknown engine: {name!r}")
