"""Deterministic Tier-B replacement: whole-word, case-preserving, single pass.

All rules compile into ONE alternation (longest 'from' first) and apply in a
single re.sub, so a rule's output can never be re-matched by another rule — no
cascade, no order bug, no infinite loop. Per-rule case sensitivity is scoped
with inline (?i:...) flags. A lowercase target mirrors the matched token's case
(so 'the' fixes at sentence start); a target with deliberate casing (GitHub,
CTranslate2, names) is emitted verbatim.
"""

from __future__ import annotations

import re

from flow.dictionary import Replacement


def _apply_case(matched: str, replacement: str) -> str:
    if not replacement.islower():
        return replacement  # deliberate brand/name casing — keep verbatim
    if matched.isupper() and len(matched) > 1:
        return replacement.upper()
    if matched[:1].isupper():
        return replacement[:1].upper() + replacement[1:]
    return replacement


class TextCorrector:
    def __init__(self, replacements: list[Replacement]) -> None:
        # Longest 'from' first so a multi-word rule wins over its prefix.
        self._rules = sorted(replacements, key=lambda r: len(r.from_), reverse=True)
        self._pattern = self._compile(self._rules)

    @staticmethod
    def _compile(rules: list[Replacement]) -> re.Pattern | None:
        if not rules:
            return None
        parts = []
        for i, r in enumerate(rules):
            body = re.escape(r.from_)
            if r.whole_word:
                body = rf"\b{body}\b"
            if not r.case_sensitive:
                body = f"(?i:{body})"
            parts.append(f"(?P<g{i}>{body})")
        return re.compile("|".join(parts))

    def correct(self, text: str) -> str:
        if self._pattern is None:
            return text

        def repl(m: re.Match) -> str:
            rule = self._rules[int(m.lastgroup[1:])]
            matched = m.group()
            if not rule.case_sensitive and " " not in rule.from_:
                return _apply_case(matched, rule.to)
            return rule.to

        return self._pattern.sub(repl, text)
