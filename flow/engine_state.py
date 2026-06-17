"""Persist the user's menu engine choice outside config.toml.

The choice the user makes from the menu bar is stored here, NOT written back
into config.toml (which the user hand-edits with comments). At startup this
file takes precedence over config.toml.
"""

import os
from pathlib import Path

_DEFAULT_PATH = Path(
    os.path.expanduser("~/Library/Application Support/LocalFlow/engine")
)


def load_engine(path: Path = _DEFAULT_PATH) -> str | None:
    """Return the saved engine name, or None if unset/unreadable."""
    try:
        return path.read_text().strip() or None
    except OSError:
        return None


def save_engine(name: str, path: Path = _DEFAULT_PATH) -> None:
    """Persist the engine name, creating the parent directory as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(name)


def resolve_engine(config_engine: str, valid_names, path: Path = _DEFAULT_PATH) -> str:
    """State file (if valid) wins, else the config value."""
    saved = load_engine(path)
    if saved in valid_names:
        return saved
    return config_engine
