"""Configuration loading for local-flow."""

import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Config:
    """Runtime settings, populated from config.toml with sensible defaults."""

    model: str = "base.en"
    engine: str = "whisper"
    keys: list[str] = field(default_factory=lambda: ["ctrl", "shift"])
    repaste_keys: list[str] = field(default_factory=lambda: ["cmd", "ctrl"])
    max_seconds: int = 180
    sample_rate: int = 16000
    compute_type: str = "int8"
    beam_size: int = 1
    paste_restore_delay: float = 0.4


def _default_path() -> Path:
    """Return config.toml in the project root (parent of this package)."""
    return Path(__file__).resolve().parent.parent / "config.toml"


def validate_keys(value: object, setting: str) -> list[str]:
    """Validate a hotkey combo: a list of 1-3 non-empty strings, lower-cased.

    Raises ValueError if `value` is not a list of 1-3 non-empty strings.
    Returns the tokens lower-cased. Shared by config.toml loading,
    flow.hotkey_state.resolve, and the settings window so all three apply
    the exact same 1-3-token rule.
    """
    if (
        not isinstance(value, list)
        or not 1 <= len(value) <= 3
        or not all(isinstance(k, str) and k for k in value)
    ):
        raise ValueError(f"{setting} must be a list of 1-3 non-empty strings")
    return [k.lower() for k in value]


# Backward-compatible alias for the previously-private name; existing internal
# call sites (load_config) and any external importer keep working.
_validate_keys = validate_keys


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
        cfg.keys = _validate_keys(hotkey["keys"], "hotkey.keys")

    repaste = data.get("repaste", {})
    if not isinstance(repaste, dict):
        raise ValueError("[repaste] must be a TOML table")
    if "keys" in repaste:
        cfg.repaste_keys = _validate_keys(repaste["keys"], "repaste.keys")

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

    engine = data.get("engine", {})
    if not isinstance(engine, dict):
        raise ValueError("[engine] must be a TOML table")
    if "name" in engine:
        from flow.engines import ENGINE_NAMES

        if engine["name"] not in ENGINE_NAMES:
            raise ValueError(f"engine.name must be one of {ENGINE_NAMES}")
        cfg.engine = engine["name"]

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
