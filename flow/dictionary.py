"""User dictionary: custom vocabulary (Tier A) + deterministic replacements (Tier B).

Loaded at startup and rebuilt live on Save/Reload. Missing file ⇒ inert
(empty). Malformed file ⇒ ValueError (the caller logs and degrades to empty so a
typo never stops dictation). Writes are atomic (temp + os.replace), mirroring
flow.history.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from flow import paths


@dataclass
class Replacement:
    from_: str
    to: str
    case_sensitive: bool = False
    whole_word: bool = True
    learned: bool = False
    ts: str | None = None


@dataclass
class Dictionary:
    vocabulary: list[str] = field(default_factory=list)
    replacements: list[Replacement] = field(default_factory=list)


def load_dictionary(path: Path = paths.DICTIONARY_PATH) -> Dictionary:
    p = Path(path)
    try:
        raw = p.read_text()
    except FileNotFoundError:
        return Dictionary()
    except OSError as exc:
        raise ValueError(f"cannot read dictionary.json: {exc}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"dictionary.json is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("dictionary.json must be a JSON object")

    vocab = data.get("vocabulary", [])
    if not isinstance(vocab, list) or not all(isinstance(v, str) and v for v in vocab):
        raise ValueError("vocabulary must be a list of non-empty strings")

    raw_reps = data.get("replacements", [])
    if not isinstance(raw_reps, list):
        raise ValueError("replacements must be a list")
    reps: list[Replacement] = []
    for r in raw_reps:
        if not isinstance(r, dict):
            raise ValueError("each replacement must be an object")
        frm, to = r.get("from"), r.get("to")
        if not (isinstance(frm, str) and frm and isinstance(to, str) and to):
            raise ValueError("each replacement needs non-empty string 'from' and 'to'")
        ts = r.get("ts")
        reps.append(Replacement(
            from_=frm, to=to,
            case_sensitive=bool(r.get("case_sensitive", False)),
            whole_word=bool(r.get("whole_word", True)),
            learned=bool(r.get("learned", False)),
            ts=ts if isinstance(ts, str) else None,
        ))
    return Dictionary(vocabulary=list(vocab), replacements=reps)


def save_dictionary(d: Dictionary, path: Path = paths.DICTIONARY_PATH) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "vocabulary": list(d.vocabulary),
        "replacements": [
            {
                "from": r.from_,
                "to": r.to,
                **({"case_sensitive": True} if r.case_sensitive else {}),
                **({} if r.whole_word else {"whole_word": False}),
                **({"learned": True, "ts": r.ts} if r.learned else {}),
            }
            for r in d.replacements
        ],
    }
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    os.replace(tmp, p)
