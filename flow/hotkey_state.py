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
    Invalid (malformed shape), partial, or missing state silently falls back
    to config and never wedges startup.

    A saved combo that has the right SHAPE (validate_keys: 1-3 non-empty
    strings) but is an unusable global shortcut (validate_combo: needs 2-3
    keys and a modifier -- e.g. {"repaste": ["v"]}) also falls back, but this
    case is logged loudly rather than silently, per issue #26: the settings
    window already refuses to save such a combo, so seeing one here means
    hotkeys.json was hand-edited or corrupted.
    """
    from flow.hotkey import canonicalize_combo, validate_combo

    data = load(path) or {}
    resolved: list[list[str]] = []
    for key, fallback in (
        ("dictate", config.keys),
        ("repaste", config.repaste_keys),
        ("correct", config.correct_keys),
    ):
        candidate = data.get(key)
        try:
            keys = validate_keys(candidate, key)
        except (ValueError, Exception):
            resolved.append(fallback)
            continue
        try:
            validate_combo(keys)
        except ValueError as exc:
            print(
                f"[hotkey_state] rejected saved {key}={keys!r}: {exc} "
                f"Falling back to {fallback!r}."
            )
            resolved.append(fallback)
            continue
        # Canonicalize (strip whitespace, resolve aliases) rather than
        # storing the raw saved tokens -- see flow.hotkey.canonicalize_combo.
        resolved.append(canonicalize_combo(keys))
    return resolved[0], resolved[1], resolved[2]


def dedupe(
    dictate: list[str],
    repaste: list[str],
    correct: list[str],
) -> tuple[list[str], list[str], list[str]]:
    """Cross-combo duplicate check for the three FINAL resolved combos
    (issue #26): if two combos are the same set of keys, both listeners
    would arm on one keypress (e.g. a dictate AND a re-paste firing off one
    ctrl+shift press). Priority is dictate > repaste > correct: the first
    combo in that order keeps its value; any later combo that duplicates an
    earlier one falls back to ITS OWN built-in default and the demotion is
    logged loudly. Comparison is order-independent (a set), so
    ["ctrl", "shift"] and ["shift", "ctrl"] count as the same combo.

    The fallback is the hardcoded flow.config.Config() default for that
    role, NOT whatever config.toml set -- the exact scenario in issue #26
    is config.toml itself setting [hotkey] and [repaste] to the same combo,
    so falling back to "whatever config.toml says for this role" would be a
    no-op and leave the duplicate armed. A fresh Config() is untouched by
    config.toml/hotkeys.json, so its three fields are always the three
    mutually-distinct built-in defaults.
    """
    from flow.config import Config

    defaults = Config()
    combos = (
        ("dictate", dictate, defaults.keys),
        ("repaste", repaste, defaults.repaste_keys),
        ("correct", correct, defaults.correct_keys),
    )
    seen: list[tuple[str, frozenset[str]]] = []
    result: list[list[str]] = []
    for name, keys, default in combos:
        keyset = frozenset(keys)
        clash = next((n for n, s in seen if s == keyset), None)
        if clash is not None:
            print(
                f"[hotkey_state] {name} combo {list(keys)!r} duplicates "
                f"{clash}'s combo; falling back {name} to its default "
                f"{list(default)!r}."
            )
            keys = list(default)
            keyset = frozenset(keys)
            # Pathological residual case: the role's own built-in default
            # itself collides with an already-kept, higher-priority combo
            # (e.g. dictate was explicitly configured to repaste's default,
            # and repaste duplicated dictate). There is no further fallback
            # to invent, so this is logged loudly and the default is used
            # anyway -- never wedges startup, but the collision is not fully
            # resolved, which the log makes visible for the user to fix.
            still_clash = next((n for n, s in seen if s == keyset), None)
            if still_clash is not None:
                print(
                    f"[hotkey_state] {name}'s own default {list(keys)!r} "
                    f"ALSO duplicates {still_clash}'s combo; the two "
                    "listeners will still both fire on that combo. Please "
                    "reconfigure config.toml/hotkeys.json."
                )
        seen.append((name, keyset))
        result.append(list(keys))
    return result[0], result[1], result[2]
