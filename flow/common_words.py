"""Is a word common enough that auto-replacing it would corrupt real text?

The Cupertino guardrail: Tier B may auto-learn a fixed wrong->right rule ONLY
when 'wrong' is uncommon, so the rule can never rewrite a word the user
legitimately types. Backed by a bundled lowercase word list.
"""

from __future__ import annotations

import functools
from pathlib import Path

_PATH = Path(__file__).resolve().parent / "data" / "common_words.txt"


@functools.lru_cache(maxsize=1)
def _common() -> frozenset[str]:
    try:
        return frozenset(
            line.strip().lower()
            for line in _PATH.read_text().splitlines()
            if line.strip()
        )
    except OSError:
        return frozenset()


def is_common(word: str) -> bool:
    """Return True if *word* is a common English word.

    A True result means the word IS common — something a user might legitimately
    type — so it is NOT safe to auto-learn as a deterministic replacement rule
    (doing so could silently rewrite intentional text).  Only words for which
    this returns False are safe candidates for Tier-B rule creation.
    """
    return word.lower() in _common()
