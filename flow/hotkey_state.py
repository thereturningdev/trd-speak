"""Persist the user's chosen dictate/re-paste/correct shortcuts outside config.toml.

Mirrors flow.engine_state: the menu/settings-window choice is stored here,
NOT written back into the hand-edited, commented config.toml. At startup this
file takes precedence over config.toml (per-combo). A single JSON file at
~/Library/Application Support/TRD Speak/hotkeys.json:
    {"dictate": ["ctrl", "shift"], "repaste": ["cmd", "ctrl"], "correct": ["cmd", "alt"]}
"""

import json
from pathlib import Path

from flow import paths
from flow.config import validate_keys

# Per-build (dev vs production) via flow.paths so the dev build does not read or
# overwrite the production build's saved shortcuts.
_DEFAULT_PATH = paths.HOTKEYS_PATH


def load(path: Path = _DEFAULT_PATH) -> dict | None:
    """Return the parsed {"dictate": [...], "repaste": [...], "correct": [...]}
    dict, or None if the file is unset, unreadable, or not valid JSON.
    Never raises."""
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def save(
    dictate_keys: list[str],
    repaste_keys: list[str],
    correct_keys: list[str],
    path: Path = _DEFAULT_PATH,
) -> None:
    """Persist all three combos as JSON, creating the parent directory as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({
            "dictate": list(dictate_keys),
            "repaste": list(repaste_keys),
            "correct": list(correct_keys),
        })
    )


def resolve(config, path: Path = _DEFAULT_PATH) -> tuple[list[str], list[str], list[str]]:
    """Per-combo: the saved value (if present and valid) wins, else the
    config.toml value. Returns (dictate_keys, repaste_keys, correct_keys).
    Invalid, partial, or missing state silently falls back to config and
    never wedges startup."""
    data = load(path) or {}
    resolved: list[list[str]] = []
    for key, fallback in (
        ("dictate", config.keys),
        ("repaste", config.repaste_keys),
        ("correct", config.correct_keys),
    ):
        candidate = data.get(key)
        try:
            resolved.append(validate_keys(candidate, key))
        except (ValueError, Exception):
            resolved.append(fallback)
    return resolved[0], resolved[1], resolved[2]
