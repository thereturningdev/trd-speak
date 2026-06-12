"""Configuration loading for local-flow."""

import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Config:
    """Runtime settings, populated from config.toml with sensible defaults."""

    model: str = "small.en"
    keys: list[str] = field(default_factory=lambda: ["ctrl", "alt"])
    max_seconds: int = 180
    sample_rate: int = 16000
    compute_type: str = "int8"
    beam_size: int = 1
    paste_restore_delay: float = 0.4


def _default_path() -> Path:
    """Return config.toml in the project root (parent of this package)."""
    return Path(__file__).resolve().parent.parent / "config.toml"


def load_config(path: str | None = None) -> Config:
    """Load config from a TOML file; missing file yields all defaults.

    Raises ValueError on invalid values. Unknown keys are ignored.
    """
    file = Path(path) if path is not None else _default_path()
    if not file.exists():
        return Config()

    with open(file, "rb") as f:
        data = tomllib.load(f)

    cfg = Config()

    hotkey = data.get("hotkey", {})
    if not isinstance(hotkey, dict):
        raise ValueError("[hotkey] must be a TOML table")
    if "keys" in hotkey:
        keys = hotkey["keys"]
        if (
            not isinstance(keys, list)
            or not 1 <= len(keys) <= 3
            or not all(isinstance(k, str) and k for k in keys)
        ):
            raise ValueError("hotkey.keys must be a list of 1-3 non-empty strings")
        cfg.keys = [k.lower() for k in keys]

    whisper = data.get("whisper", {})
    if not isinstance(whisper, dict):
        raise ValueError("[whisper] must be a TOML table")
    if "model" in whisper:
        if not isinstance(whisper["model"], str) or not whisper["model"]:
            raise ValueError("whisper.model must be a non-empty string")
        cfg.model = whisper["model"]
    if "compute_type" in whisper:
        if not isinstance(whisper["compute_type"], str) or not whisper["compute_type"]:
            raise ValueError("whisper.compute_type must be a non-empty string")
        cfg.compute_type = whisper["compute_type"]
    if "beam_size" in whisper:
        beam_size = whisper["beam_size"]
        if not isinstance(beam_size, int) or isinstance(beam_size, bool) or beam_size <= 0:
            raise ValueError("whisper.beam_size must be a positive integer")
        cfg.beam_size = beam_size

    recording = data.get("recording", {})
    if not isinstance(recording, dict):
        raise ValueError("[recording] must be a TOML table")
    if "max_seconds" in recording:
        max_seconds = recording["max_seconds"]
        if not isinstance(max_seconds, int) or isinstance(max_seconds, bool) or max_seconds <= 0:
            raise ValueError("recording.max_seconds must be a positive integer")
        cfg.max_seconds = max_seconds
    if "sample_rate" in recording:
        sample_rate = recording["sample_rate"]
        if not isinstance(sample_rate, int) or isinstance(sample_rate, bool) or sample_rate <= 0:
            raise ValueError("recording.sample_rate must be a positive integer")
        cfg.sample_rate = sample_rate

    paste = data.get("paste", {})
    if not isinstance(paste, dict):
        raise ValueError("[paste] must be a TOML table")
    if "restore_delay" in paste:
        delay = paste["restore_delay"]
        if not isinstance(delay, (int, float)) or isinstance(delay, bool) or delay < 0:
            raise ValueError("paste.restore_delay must be a non-negative number")
        cfg.paste_restore_delay = float(delay)

    return cfg
