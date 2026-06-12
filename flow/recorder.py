"""Microphone recorder using sounddevice."""

import threading

import numpy as np
import sounddevice as sd


class Recorder:
    """Records mono float32 audio from the default input device."""

    def __init__(self, sample_rate: int = 16000, max_seconds: int = 180) -> None:
        self.sample_rate = sample_rate
        self.max_seconds = max_seconds
        self._max_frames = sample_rate * max_seconds
        self._chunks: list[np.ndarray] = []
        self._frames = 0
        self._lock = threading.Lock()
        self._stream: sd.InputStream | None = None

    def _callback(self, indata: np.ndarray, frames: int, time, status) -> None:
        """Stream callback: accumulate copies of incoming audio up to the cap."""
        with self._lock:
            if self._frames >= self._max_frames:
                return  # cap reached; drop further frames silently
            remaining = self._max_frames - self._frames
            chunk = indata[:remaining] if frames > remaining else indata
            self._chunks.append(chunk.copy())
            self._frames += len(chunk)

    def start(self) -> None:
        """Begin recording. Raises RuntimeError if already recording."""
        if self._stream is not None:
            raise RuntimeError("Recorder is already recording")
        with self._lock:
            self._chunks = []
            self._frames = 0
        stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=1,
            dtype="float32",
            callback=self._callback,
        )
        try:
            stream.start()
        except Exception:
            stream.close()
            raise
        self._stream = stream

    def stop(self) -> np.ndarray:
        """Stop recording and return everything captured as a 1-D float32 array."""
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        with self._lock:
            chunks = self._chunks
            self._chunks = []
            self._frames = 0
        if not chunks:
            return np.array([], dtype=np.float32)
        return np.concatenate(chunks).reshape(-1).astype(np.float32, copy=False)
