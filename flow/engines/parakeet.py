"""Parakeet (NVIDIA Parakeet TDT 0.6B) engine via Apple MLX — runs on the GPU.

parakeet-mlx is an OPTIONAL dependency. It is imported lazily in ``load`` so a
machine without it still runs faster-whisper. parakeet-mlx 0.5.2 only loads
audio from a file path via ffmpeg and exposes no array input; this app feeds
the microphone's already-decoded float32 audio by substituting parakeet's
module-level audio loader with one that returns our pre-set array. Because the
app's state machine serializes transcription, stashing the pending array on the
instance is safe.
"""

import numpy as np

from flow.engines import EngineUnavailable, Transcriber

_SAMPLE_RATE = 16000
_MIN_SECONDS = 0.25
_MODEL = "mlx-community/parakeet-tdt-0.6b-v2"


class ParakeetTranscriber(Transcriber):
    name = "parakeet"
    label = "Parakeet (parakeet-tdt-0.6b, GPU)"

    def __init__(self) -> None:
        self._model = None
        self._mx = None
        self._pending = None  # mx.array stashed for the patched loader

    def load(self) -> None:
        if self._model is not None:
            return
        try:
            import mlx.core as mx
            import parakeet_mlx.parakeet as pk
            from parakeet_mlx import from_pretrained
        except ImportError as exc:
            raise EngineUnavailable(
                "Parakeet isn't installed. Run ./setup.sh --parakeet"
            ) from exc

        if not hasattr(pk, "load_audio"):
            raise EngineUnavailable(
                "Incompatible parakeet-mlx: no load_audio to substitute."
            )

        # Feed pre-decoded audio instead of shelling out to ffmpeg.
        def _loader(filename, sampling_rate, dtype=None):
            return self._pending

        pk.load_audio = _loader

        self._mx = mx
        self._model = from_pretrained(_MODEL)
        # Warm up: first transcription compiles Metal kernels (~5 s) — pay it
        # here, during the "Loading…" phase, not on the user's first dictation.
        self._transcribe_array(np.zeros(_SAMPLE_RATE // 2, dtype=np.float32))

    def _transcribe_array(self, audio: np.ndarray) -> str:
        self._pending = self._mx.array(audio, dtype=self._mx.float32)
        result = self._model.transcribe("<memory>")
        return result.text.strip()

    def transcribe(self, audio: np.ndarray) -> str:
        if len(audio) < _SAMPLE_RATE * _MIN_SECONDS:
            return ""
        self.load()
        return self._transcribe_array(audio)

    def unload(self) -> None:
        self._model = None
        self._pending = None
        if self._mx is not None:
            try:
                self._mx.clear_cache()
            except Exception:
                pass
