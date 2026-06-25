"""Derive safe Tier-B rules + Tier-A vocab terms from a user's correction.

Word-level diff of original vs edited; keep only 1-word→1-word substitutions
(inserts/deletes/multi-word edits are rephrasings, never rules). Always bias the
vocabulary toward the corrected target. Create a deterministic rule ONLY when the
misheard word is uncommon, so a learned rule can never corrupt legitimate text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Callable

from flow.dictionary import Replacement

# Letters/digits, internal apostrophes/hyphens; underscore and other
# punctuation split tokens.
_WORD = re.compile(r"[^\W_]+(?:['-][^\W_]+)*", re.UNICODE)
# Skip single-letter edits (too noisy / likely punctuation artifacts) and
# pathologically long tokens (likely garbage from the ASR decoder).
_MIN_LEN, _MAX_LEN = 2, 30


def _words(text: str) -> list[str]:
    return _WORD.findall(text)


@dataclass
class LearnResult:
    rules: list[Replacement] = field(default_factory=list)
    vocab: list[str] = field(default_factory=list)


def derive(
    original: str,
    edited: str,
    is_common: Callable[[str], bool],
    ts: str | None = None,
) -> LearnResult:
    a, b = _words(original), _words(edited)
    res = LearnResult()
    seen_rule: set[str] = set()
    seen_vocab: set[str] = set()
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag != "replace" or (i2 - i1) != (j2 - j1):
            continue  # only equal-count replaces are word-for-word substitutions
        for wrong, right in zip(a[i1:i2], b[j1:j2]):
            if not (_MIN_LEN <= len(wrong) <= _MAX_LEN and _MIN_LEN <= len(right) <= _MAX_LEN):
                continue
            if not (any(c.isalpha() for c in wrong) and any(c.isalpha() for c in right)):
                continue  # require a letter — don't learn pure-number "corrections"
            if wrong.lower() == right.lower():
                continue
            if right.lower() not in seen_vocab:
                seen_vocab.add(right.lower())
                res.vocab.append(right)
            if not is_common(wrong) and wrong.lower() not in seen_rule:
                seen_rule.add(wrong.lower())
                # from_ deliberately preserves the ASR transcript's casing (e.g.
                # "diotaleavy" rather than "Diotaleavy").  The corrector is
                # case-insensitive by default, so this is harmless.
                res.rules.append(Replacement(from_=wrong, to=right, learned=True, ts=ts))
    return res
