"""Local speech-to-text using faster-whisper."""

import logging

import numpy as np

_SAMPLE_RATE = 16000
_MIN_SECONDS = 0.25

# Quiet the noisy download/progress loggers; warnings and errors still show.
for _name in ("faster_whisper", "huggingface_hub", "ctranslate2"):
    logging.getLogger(_name).setLevel(logging.WARNING)


class Transcriber:
    """Wraps a faster-whisper model for English transcription on CPU."""

    def __init__(
        self,
        model_name: str = "base.en",
        compute_type: str = "int8",
        beam_size: int = 1,
    ) -> None:
        self.model_name = model_name
        self.compute_type = compute_type
        self.beam_size = beam_size
        self._model = None

    def load(self) -> None:
        """Instantiate the Whisper model. Idempotent."""
        if self._model is not None:
            return
        from faster_whisper import WhisperModel

        self._model = WhisperModel(
            self.model_name, device="cpu", compute_type=self.compute_type
        )

    def transcribe(self, audio: np.ndarray) -> str:
        """Transcribe 16 kHz mono float32 audio; returns "" for too-short audio."""
        if len(audio) < _SAMPLE_RATE * _MIN_SECONDS:
            return ""
        self.load()
        # vad_filter skips non-speech (pauses, breathing) — faster on real
        # dictation and avoids hallucinated text during silences.
        # condition_on_previous_text=False prevents repetition spirals and
        # their expensive temperature-fallback re-decodes on long recordings.
        segments, _info = self._model.transcribe(
            audio,
            language="en",
            beam_size=self.beam_size,
            vad_filter=True,
            condition_on_previous_text=False,
        )
        return "".join(segment.text for segment in segments).strip()
