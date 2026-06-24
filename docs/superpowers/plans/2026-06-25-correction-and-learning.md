# Correction & Learning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A global shortcut that opens an editor on the last dictation; saving it teaches the app safe wrong→right rules (Tier B) and vocabulary biasing (Tier A) applied to every future dictation.

**Architecture:** A shared `dictionary.json` (vocabulary + replacements) drives a deterministic, single-pass, whole-word, case-preserving corrector applied in `App._process()` between transcribe and `history.add`. A new `learning.derive()` turns a correction diff into safe rules (uncommon-`wrong`-only) plus vocab targets. A popup `NSWindow` captures the correction; menu rows manage learned rules. Tier 3 (local-LLM) is out of scope (future, spike-gated).

**Tech Stack:** Python 3.12, faster-whisper 1.2.1 (`hotwords`), stdlib `re`/`difflib`/`json`, PyObjC/AppKit (GUI), pytest. Run tests with `.venv/bin/python -m pytest`.

**Spec:** `docs/superpowers/specs/2026-06-25-correction-and-learning-design.md`

**Branch:** create `feat/correction-and-learning` before Task 1 (do not work on `main`).

---

## File structure

| File | Responsibility |
| --- | --- |
| `flow/paths.py` | add `DICTIONARY_PATH` (per-build storage) |
| `flow/dictionary.py` | NEW — `Replacement`/`Dictionary` data model, load/validate/save JSON |
| `flow/corrector.py` | NEW — `TextCorrector`: deterministic Tier-B replacement |
| `flow/common_words.py` + `flow/data/common_words.txt` | NEW — `is_common(word)` (the Cupertino guardrail) |
| `flow/learning.py` | NEW — `derive(original, edited, is_common)` → safe rules + vocab |
| `flow/engines/whisper.py`, `flow/engines/__init__.py` | Tier-A `hotwords` pass-through |
| `flow/config.py` | `correct_keys` + `[correct]` table |
| `flow/app.py` | load dictionary, build corrector, apply in `_process`, correction hotkey, `_on_correct`, `learn()` |
| `flow/correction_window.py` | NEW — popup editor (GUI) |
| `flow/menubar.py` | "Correct last dictation…", "Learned words…", "Open dictionary file…" |
| `flow/settings_window.py` | record a third (correct) shortcut |
| `main.py` | load dictionary at startup, log summary |
| `dictionary.json.example`, `README.md`, `GETTING_STARTED.md`, `TRDSpeak.spec` | docs + bundle `flow/data/` |
| `tests/test_dictionary.py`, `tests/test_corrector.py`, `tests/test_common_words.py`, `tests/test_learning.py`, `tests/test_correct_learn_e2e.py` | NEW tests |
| `tests/test_whisper_engine.py`, `tests/test_app_engine.py`, `tests/test_paths.py`, `tests/test_config_*` | extended |

---

## Phase 1 — Engine core (pure logic, TDD)

### Task 1: `DICTIONARY_PATH` in paths.py

**Files:**
- Modify: `flow/paths.py` (the `_derive` dict + module-level exports)
- Test: `tests/test_paths.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_paths.py  (add)
def test_dictionary_path_is_under_support_and_per_build():
    from flow import paths
    prod = paths._derive("TRD Speak")
    dev = paths._derive("TRD Speak Dev")
    assert prod["dictionary"].name == "dictionary.json"
    assert prod["dictionary"].parent == prod["support"]
    assert prod["dictionary"] != dev["dictionary"]  # builds never clobber
```

- [ ] **Step 2: Run it — expect FAIL** (`KeyError: 'dictionary'`)

Run: `.venv/bin/python -m pytest tests/test_paths.py::test_dictionary_path_is_under_support_and_per_build -v`

- [ ] **Step 3: Implement**

In `flow/paths.py`, inside `_derive`'s returned dict add:
```python
        "dictionary": support / "dictionary.json",
```
After the `DICTATIONS_PATH = _PATHS["dictations"]` line add:
```python
DICTIONARY_PATH = _PATHS["dictionary"]
```

- [ ] **Step 4: Run it — expect PASS**

- [ ] **Step 5: Commit**

```bash
git add flow/paths.py tests/test_paths.py
git commit -m "feat: per-build DICTIONARY_PATH for the user dictionary"
```

---

### Task 2: `dictionary.py` — data model + load/validate/save

**Files:**
- Create: `flow/dictionary.py`
- Test: `tests/test_dictionary.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_dictionary.py
import json
import pytest
from flow.dictionary import Dictionary, Replacement, load_dictionary, save_dictionary

def test_missing_file_is_empty(tmp_path):
    d = load_dictionary(tmp_path / "nope.json")
    assert d == Dictionary(vocabulary=[], replacements=[])

def test_valid_file_parses_with_defaults(tmp_path):
    p = tmp_path / "dictionary.json"
    p.write_text(json.dumps({
        "vocabulary": ["GitHub", "Diotalevi"],
        "replacements": [
            {"from": "fast whisper", "to": "faster-whisper"},
            {"from": "diotaleavy", "to": "Diotalevi", "learned": True, "ts": "2026-06-25T10:00:00"},
        ],
    }))
    d = load_dictionary(p)
    assert d.vocabulary == ["GitHub", "Diotalevi"]
    r0, r1 = d.replacements
    assert (r0.from_, r0.to, r0.case_sensitive, r0.whole_word, r0.learned) == \
           ("fast whisper", "faster-whisper", False, True, False)
    assert (r1.from_, r1.to, r1.learned, r1.ts) == ("diotaleavy", "Diotalevi", True, "2026-06-25T10:00:00")

def test_malformed_json_raises(tmp_path):
    p = tmp_path / "dictionary.json"; p.write_text("{not json")
    with pytest.raises(ValueError):
        load_dictionary(p)

def test_bad_types_raise(tmp_path):
    p = tmp_path / "dictionary.json"
    p.write_text(json.dumps({"replacements": [{"from": "", "to": "x"}]}))
    with pytest.raises(ValueError):
        load_dictionary(p)

def test_unknown_keys_ignored(tmp_path):
    p = tmp_path / "dictionary.json"
    p.write_text(json.dumps({"vocabulary": ["a"], "replacements": [], "extra": 1}))
    assert load_dictionary(p).vocabulary == ["a"]

def test_save_round_trips(tmp_path):
    p = tmp_path / "dictionary.json"
    d = Dictionary(vocabulary=["GitHub"],
                   replacements=[Replacement("diotaleavy", "Diotalevi", learned=True, ts="t")])
    save_dictionary(d, p)
    assert load_dictionary(p) == d
```

- [ ] **Step 2: Run — expect FAIL** (`ModuleNotFoundError: flow.dictionary`)

Run: `.venv/bin/python -m pytest tests/test_dictionary.py -v`

- [ ] **Step 3: Implement `flow/dictionary.py`**

```python
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
```

- [ ] **Step 4: Run — expect PASS**

- [ ] **Step 5: Commit**

```bash
git add flow/dictionary.py tests/test_dictionary.py
git commit -m "feat: dictionary.py — load/validate/save the user dictionary"
```

---

### Task 3: `corrector.py` — deterministic Tier-B engine

**Files:**
- Create: `flow/corrector.py`
- Test: `tests/test_corrector.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_corrector.py
from flow.corrector import TextCorrector
from flow.dictionary import Replacement

def mk(*pairs, **kw):
    return TextCorrector([Replacement(a, b, **kw) for a, b in pairs])

def test_whole_word_default_does_not_touch_substring():
    c = mk(("cat", "dog"))
    assert c.correct("the cat in category") == "the dog in category"

def test_whole_word_false_replaces_substring():
    c = TextCorrector([Replacement("cat", "dog", whole_word=False)])
    assert c.correct("category") == "dogegory"

def test_longest_from_wins():
    c = mk(("machine", "device"), ("machine learning", "ML"))
    assert c.correct("machine learning is machine work") == "ML is device work"

def test_case_insensitive_match_lowercase_target_mirrors_case():
    c = mk(("teh", "the"))
    assert c.correct("Teh start. teh end. TEH END") == "The start. the end. THE END"

def test_branded_target_is_verbatim_regardless_of_match_case():
    c = mk(("github", "GitHub"))
    assert c.correct("push to github and Github and GITHUB") == \
        "push to GitHub and GitHub and GitHub"

def test_case_sensitive_only_fires_on_matching_case():
    c = TextCorrector([Replacement("LocalFlow", "LocalFlow!", case_sensitive=True)])
    assert c.correct("localflow vs LocalFlow") == "localflow vs LocalFlow!"

def test_punctuation_adjacency_and_edges():
    c = mk(("github", "GitHub"))
    assert c.correct("github.") == "GitHub."
    assert c.correct("github") == "GitHub"

def test_no_cascade_between_rules():
    # 'a'->'b' then a 'b'->'c' rule must NOT turn 'a' into 'c' (single pass)
    c = mk(("aa", "bb"), ("bb", "cc"))
    assert c.correct("aa bb") == "bb cc"

def test_unicode_word_boundary():
    c = mk(("eleve", "élève"))
    assert c.correct("the eleve") == "the élève"

def test_empty_rules_is_identity():
    c = TextCorrector([])
    assert c.correct("anything at all") == "anything at all"
```

- [ ] **Step 2: Run — expect FAIL** (`ModuleNotFoundError: flow.corrector`)

Run: `.venv/bin/python -m pytest tests/test_corrector.py -v`

- [ ] **Step 3: Implement `flow/corrector.py`**

```python
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
```

- [ ] **Step 4: Run — expect PASS**

- [ ] **Step 5: Commit**

```bash
git add flow/corrector.py tests/test_corrector.py
git commit -m "feat: corrector.py — single-pass, whole-word, case-preserving Tier-B engine"
```

---

### Task 4: `common_words.py` + bundled word list (Cupertino guardrail)

**Files:**
- Create: `flow/common_words.py`
- Create: `flow/data/common_words.txt`
- Test: `tests/test_common_words.py`

- [ ] **Step 1: Create the word list**

Download a public top-~10k English list (e.g. the `google-10000-english-usa.txt` /
`dwyl/english-words` common set) and save it lowercased, one word per line, to
`flow/data/common_words.txt`. It MUST include everyday homophones we must never
auto-rule on: confirm `cloud`, `guitar`, `code`, `program`, `the` are present;
confirm invented mishearings like `diotaleavy` and `ctranslate` are absent.

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_common_words.py
from flow.common_words import is_common

def test_everyday_words_are_common():
    for w in ("the", "cloud", "guitar", "code", "program"):
        assert is_common(w), w

def test_case_insensitive():
    assert is_common("Cloud") and is_common("THE")

def test_invented_mishearings_are_not_common():
    for w in ("diotaleavy", "ctranslate", "qwxzjk"):
        assert not is_common(w), w
```

- [ ] **Step 3: Run — expect FAIL** (`ModuleNotFoundError`)

Run: `.venv/bin/python -m pytest tests/test_common_words.py -v`

- [ ] **Step 4: Implement `flow/common_words.py`**

```python
"""Is a word common enough that auto-replacing it would corrupt real text?

The Cupertino guardrail: Tier B may auto-learn a fixed wrong→right rule ONLY
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
    return word.lower() in _common()
```

- [ ] **Step 5: Run — expect PASS**

- [ ] **Step 6: Commit**

```bash
git add flow/common_words.py flow/data/common_words.txt tests/test_common_words.py
git commit -m "feat: common_words.py + word list — the auto-learn Cupertino guardrail"
```

---

### Task 5: `learning.py` — derive safe rules + vocab from a correction

**Files:**
- Create: `flow/learning.py`
- Test: `tests/test_learning.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_learning.py
from flow.learning import derive

# Fake predicate: only these are "common" so tests don't depend on the word list.
COMMON = {"the", "code", "program", "cloud", "to", "a", "is"}
def is_common(w): return w.lower() in COMMON

def test_uncommon_single_word_swap_learns_rule_and_vocab():
    r = derive("call diotaleavy now", "call Diotalevi now", is_common, ts="t")
    assert [(x.from_, x.to, x.learned) for x in r.rules] == [("diotaleavy", "Diotalevi", True)]
    assert r.vocab == ["Diotalevi"]

def test_common_wrong_word_is_vocab_only_no_rule():
    # cloud->Claude: 'cloud' is common ⇒ NO deterministic rule (would corrupt
    # real "cloud"); still bias vocabulary toward Claude.
    r = derive("ask cloud now", "ask Claude now", is_common)
    assert r.rules == []
    assert r.vocab == ["Claude"]

def test_inserts_and_deletes_learn_nothing():
    assert derive("hello world", "hello there world", is_common).rules == []   # insert
    assert derive("hello there world", "hello world", is_common).rules == []   # delete

def test_multi_word_replace_is_skipped():
    r = derive("fast whisper rocks", "faster-whisper rocks", is_common)
    assert r.rules == []  # 2 words -> 1 word is not a 1:1 swap

def test_format_guard_rejects_too_short_or_digits():
    # 'i' -> 'I' too short; numbers excluded by the word tokenizer.
    assert derive("i ran", "I ran", is_common).rules == []

def test_dedupe_keeps_first_per_wrong():
    r = derive("zzx and zzx", "Zed and Zedd", is_common)
    froms = [x.from_ for x in r.rules]
    assert froms.count("zzx") == 1
```

- [ ] **Step 2: Run — expect FAIL** (`ModuleNotFoundError`)

Run: `.venv/bin/python -m pytest tests/test_learning.py -v`

- [ ] **Step 3: Implement `flow/learning.py`**

```python
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

# Letters only (no digits/punctuation), allowing internal apostrophes/hyphens.
_WORD = re.compile(r"[^\W\d_]+(?:['-][^\W\d_]+)*", re.UNICODE)
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
        if tag != "replace" or (i2 - i1) != 1 or (j2 - j1) != 1:
            continue
        wrong, right = a[i1], b[j1]
        if not (_MIN_LEN <= len(wrong) <= _MAX_LEN and _MIN_LEN <= len(right) <= _MAX_LEN):
            continue
        if wrong.lower() == right.lower():
            continue
        if right.lower() not in seen_vocab:
            seen_vocab.add(right.lower())
            res.vocab.append(right)
        if not is_common(wrong) and wrong.lower() not in seen_rule:
            seen_rule.add(wrong.lower())
            res.rules.append(Replacement(from_=wrong, to=right, learned=True, ts=ts))
    return res
```

- [ ] **Step 4: Run — expect PASS**

- [ ] **Step 5: Commit**

```bash
git add flow/learning.py tests/test_learning.py
git commit -m "feat: learning.py — derive safe rules + vocab from a correction diff"
```

---

## Phase 2 — Pipeline integration

### Task 6: Tier-A `hotwords` pass-through

**Files:**
- Modify: `flow/engines/whisper.py:65-78` (`transcribe`), `flow/engines/__init__.py`
- Test: `tests/test_whisper_engine.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_whisper_engine.py  (add; create file if absent)
import numpy as np
from flow.engines.whisper import WhisperTranscriber

class _FakeModel:
    def __init__(self): self.kwargs = None
    def transcribe(self, audio, **kw):
        self.kwargs = kw
        class S: text = "hello"
        return [S()], object()

def _ready(monkeypatch):
    t = WhisperTranscriber()
    fake = _FakeModel()
    monkeypatch.setattr(t, "load", lambda: None)
    t._model = fake
    return t, fake

def test_hotwords_passed_through(monkeypatch):
    t, fake = _ready(monkeypatch)
    audio = np.ones(16000, dtype=np.float32)
    t.transcribe(audio, hotwords="GitHub, Claude")
    assert fake.kwargs["hotwords"] == "GitHub, Claude"
    assert "prefix" not in fake.kwargs  # must never be set

def test_empty_hotwords_is_none(monkeypatch):
    t, fake = _ready(monkeypatch)
    t.transcribe(np.ones(16000, dtype=np.float32), hotwords="")
    assert fake.kwargs["hotwords"] is None
```

- [ ] **Step 2: Run — expect FAIL** (`transcribe() got unexpected keyword 'hotwords'`)

Run: `.venv/bin/python -m pytest tests/test_whisper_engine.py -v`

- [ ] **Step 3: Implement**

In `flow/engines/__init__.py`, change the abstract signature (line ~36):
```python
    @abc.abstractmethod
    def transcribe(self, audio: np.ndarray, hotwords: str | None = None) -> str:
        """Transcribe 16 kHz mono float32 audio to text, optionally biased by hotwords."""
```

In `flow/engines/whisper.py`, replace `transcribe` (lines 65-78):
```python
    def transcribe(self, audio: np.ndarray, hotwords: str | None = None) -> str:
        if len(audio) < _SAMPLE_RATE * _MIN_SECONDS:
            return ""
        self.load()
        # vad_filter skips non-speech; condition_on_previous_text=False avoids
        # repetition spirals. hotwords biases decoding toward custom vocabulary;
        # NEVER set prefix — hotwords is silently ignored when prefix is set.
        segments, _info = self._model.transcribe(
            audio,
            language="en",
            beam_size=self.beam_size,
            vad_filter=True,
            condition_on_previous_text=False,
            hotwords=hotwords or None,
        )
        return "".join(segment.text for segment in segments).strip()
```

- [ ] **Step 4: Run — expect PASS**

- [ ] **Step 5: Commit**

```bash
git add flow/engines/whisper.py flow/engines/__init__.py tests/test_whisper_engine.py
git commit -m "feat: Tier-A hotwords pass-through in the whisper engine"
```

---

### Task 7: `correct_keys` config

**Files:**
- Modify: `flow/config.py` (`Config` dataclass + `load_config`)
- Test: `tests/test_config_correct.py` (new)

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_config_correct.py
from flow.config import Config, load_config

def test_default_correct_keys():
    assert Config().correct_keys == ["cmd", "alt"]

def test_correct_keys_loaded(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('[correct]\nkeys = ["cmd", "shift"]\n')
    assert load_config(str(p)).correct_keys == ["cmd", "shift"]

def test_correct_table_must_be_table(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('correct = 5\n')
    import pytest
    with pytest.raises(ValueError):
        load_config(str(p))
```

- [ ] **Step 2: Run — expect FAIL**

Run: `.venv/bin/python -m pytest tests/test_config_correct.py -v`

- [ ] **Step 3: Implement**

In `flow/config.py`, add to `Config` (after `repaste_keys`, line ~19):
```python
    correct_keys: list[str] = field(default_factory=lambda: ["cmd", "alt"])
```
In `load_config`, after the `repaste` block (line ~78):
```python
    correct = data.get("correct", {})
    if not isinstance(correct, dict):
        raise ValueError("[correct] must be a TOML table")
    if "keys" in correct:
        cfg.correct_keys = _validate_keys(correct["keys"], "correct.keys")
```

- [ ] **Step 4: Run — expect PASS**

- [ ] **Step 5: Commit**

```bash
git add flow/config.py tests/test_config_correct.py
git commit -m "feat: [correct] shortcut config (default cmd+alt)"
```

---

### Task 8: Apply correction + Tier-A in `App._process()`

**Files:**
- Modify: `flow/app.py` (`__init__` ~24-55, `_process` ~139-167)
- Test: `tests/test_app_engine.py`

- [ ] **Step 1: Write the failing functional test**

```python
# tests/test_app_engine.py  (add)
def test_process_applies_correction_to_paste_and_history(monkeypatch, tmp_path):
    import flow.app as app_mod
    from flow.config import Config
    from flow.dictionary import Dictionary, Replacement

    monkeypatch.setattr(app_mod.paths, "DICTATIONS_PATH", tmp_path / "d.json")
    monkeypatch.setattr(app_mod.paths, "DICTIONARY_PATH", tmp_path / "dict.json")
    monkeypatch.setattr(app_mod.HotkeyListener, "start", lambda self: None)
    monkeypatch.setattr(app_mod.HotkeyListener, "stop", lambda self: None)
    monkeypatch.setattr(app_mod, "make_transcriber", lambda *a, **k: object())

    app = app_mod.App(Config())
    app.dictionary = Dictionary(replacements=[Replacement("diotaleavy", "Diotalevi", learned=True)])
    app.corrector = app_mod.TextCorrector(app.dictionary.replacements)

    app.recorder = type("R", (), {"stop": lambda self: __import__("numpy").ones(16000, dtype="float32")})()
    app.transcriber = type("T", (), {"transcribe": lambda self, audio, hotwords=None: "call diotaleavy"})()
    monkeypatch.setattr(app, "can_paste", lambda: True)
    monkeypatch.setattr(app.hotkey, "wait_all_released", lambda: True)
    pasted = []
    monkeypatch.setattr(app_mod, "paste_text", lambda s, **k: pasted.append(s))

    app._process()
    assert pasted == ["call Diotalevi "]
    assert app.history.items()[0] == "call Diotalevi"
```

- [ ] **Step 2: Run — expect FAIL** (`App has no attribute corrector` / `TextCorrector`)

Run: `.venv/bin/python -m pytest tests/test_app_engine.py::test_process_applies_correction_to_paste_and_history -v`

- [ ] **Step 3: Implement**

In `flow/app.py` imports (top):
```python
from flow.corrector import TextCorrector
from flow.dictionary import load_dictionary
```
In `App.__init__`, after `self.history = History(...)` (line ~33):
```python
        # User dictionary (Tier A vocabulary + Tier B replacements). A malformed
        # file must never stop dictation, so load failures degrade to empty.
        try:
            self.dictionary = load_dictionary(paths.DICTIONARY_PATH)
        except ValueError as exc:
            print(f"dictionary.json ignored ({exc}); using an empty dictionary.")
            from flow.dictionary import Dictionary
            self.dictionary = Dictionary()
        self.corrector = TextCorrector(self.dictionary.replacements)
```
Add a helper method on `App`:
```python
    def _vocab_hint(self) -> str | None:
        return ", ".join(self.dictionary.vocabulary) or None
```
In `_process`, change the transcribe call (line 145) and add correction before `history.add` (line 152):
```python
            text = self.transcriber.transcribe(audio, hotwords=self._vocab_hint())
            elapsed = time.monotonic() - start
            timing = f"[{audio_secs:.0f}s audio, transcribed in {elapsed:.1f}s]"
            if text:
                try:
                    text = self.corrector.correct(text)
                except Exception as exc:  # never let correction break dictation
                    print(f"Correction skipped ({exc}); pasting raw transcript.")
                # Capture BEFORE the paste attempt: ...
                self.history.add(text)
```

- [ ] **Step 4: Run — expect PASS**, then full suite

Run: `.venv/bin/python -m pytest -q`
Expected: all pass (143 prior + new).

- [ ] **Step 5: Commit**

```bash
git add flow/app.py tests/test_app_engine.py
git commit -m "feat: apply Tier-B correction and Tier-A hotwords in App._process"
```

---

## Phase 3 — Capture & learn

### Task 9: Correction hotkey + `_on_correct` + `learn()`

**Files:**
- Modify: `flow/app.py` (`__init__`, new methods)
- Test: `tests/test_correct_learn_e2e.py` (new)

- [ ] **Step 1: Write the failing end-to-end test**

```python
# tests/test_correct_learn_e2e.py
def test_correction_is_learned_and_applied_next_time(monkeypatch, tmp_path):
    import flow.app as app_mod
    from flow.config import Config
    from flow.dictionary import load_dictionary

    monkeypatch.setattr(app_mod.paths, "DICTATIONS_PATH", tmp_path / "d.json")
    monkeypatch.setattr(app_mod.paths, "DICTIONARY_PATH", tmp_path / "dict.json")
    monkeypatch.setattr(app_mod.HotkeyListener, "start", lambda self: None)
    monkeypatch.setattr(app_mod.HotkeyListener, "stop", lambda self: None)
    monkeypatch.setattr(app_mod, "make_transcriber", lambda *a, **k: object())

    app = app_mod.App(Config())

    # User corrects an uncommon mishearing via the editor (original, edited).
    app.learn("call diotaleavy", "call Diotalevi")

    # Persisted with the learned flag.
    saved = load_dictionary(tmp_path / "dict.json")
    assert any(r.from_ == "diotaleavy" and r.to == "Diotalevi" and r.learned
               for r in saved.replacements)
    assert "Diotalevi" in saved.vocabulary

    # Applied to the next dictation.
    app.recorder = type("R", (), {"stop": lambda self: __import__("numpy").ones(16000, dtype="float32")})()
    app.transcriber = type("T", (), {"transcribe": lambda self, audio, hotwords=None: "meet diotaleavy"})()
    monkeypatch.setattr(app, "can_paste", lambda: True)
    monkeypatch.setattr(app.hotkey, "wait_all_released", lambda: True)
    pasted = []
    monkeypatch.setattr(app_mod, "paste_text", lambda s, **k: pasted.append(s))
    app._process()
    assert pasted == ["meet Diotalevi "]
```

- [ ] **Step 2: Run — expect FAIL** (`App has no attribute learn`)

Run: `.venv/bin/python -m pytest tests/test_correct_learn_e2e.py -v`

- [ ] **Step 3: Implement**

In `flow/app.py` imports:
```python
from datetime import datetime
from flow.common_words import is_common
from flow.dictionary import save_dictionary
from flow.learning import derive
```
In `App.__init__`, after the `repaste_hotkey` block (line ~53), add the correction tap:
```python
        # Third independent listener: a clean tap opens the correction editor on
        # the last dictation (learn-from-correction). Same tap pattern as re-paste.
        self.correction_hotkey = HotkeyListener(
            keys=config.correct_keys,
            on_trigger=self._on_correct,
        )
```
Add methods to `App`:
```python
    def _on_correct(self) -> None:
        """Correction hotkey tapped: open the editor on the main thread."""
        if self.open_correction_window is not None:
            self.open_correction_window()  # set by the GUI layer

    def learn(self, original: str, edited: str) -> None:
        """Derive safe rules + vocab from a correction, persist, and apply live."""
        if original == edited:
            return
        result = derive(original, edited, is_common, ts=datetime.now().isoformat())
        if not result.rules and not result.vocab:
            return
        existing = {r.from_.lower() for r in self.dictionary.replacements}
        for r in result.rules:
            if r.from_.lower() in existing:
                self.dictionary.replacements = [
                    x for x in self.dictionary.replacements
                    if x.from_.lower() != r.from_.lower()
                ]
            self.dictionary.replacements.append(r)
        have = {v.lower() for v in self.dictionary.vocabulary}
        for v in result.vocab:
            if v.lower() not in have:
                self.dictionary.vocabulary.append(v)
        save_dictionary(self.dictionary, paths.DICTIONARY_PATH)
        self.corrector = TextCorrector(self.dictionary.replacements)
        if self.on_learned is not None:
            self.on_learned()  # refresh the menu's "Learned words" list
```
In `__init__`, near the other UI hooks (line ~56-60), add:
```python
        # GUI hooks (set by the menubar/window layer).
        self.open_correction_window: Callable[[], None] | None = None
        self.on_learned: Callable[[], None] | None = None
```

- [ ] **Step 4: Run — expect PASS**, then full suite (`.venv/bin/python -m pytest -q`)

- [ ] **Step 5: Commit**

```bash
git add flow/app.py tests/test_correct_learn_e2e.py
git commit -m "feat: correction hotkey + App.learn() — persist and apply learned rules"
```

---

### Task 10: `correction_window.py` — the popup editor (GUI)

**Files:**
- Create: `flow/correction_window.py`

GUI is verified by import + manual run (like `settings_window.py`/`menubar.py`),
not unit-tested.

- [ ] **Step 1: Implement** a programmatic `NSWindow` modeled on
  `flow/settings_window.py`. Read that file first and mirror its structure:
  - `open_correction_window(app)`: if `app.history.latest()` is None, show a
    disabled "Nothing to correct yet" state and return.
  - Call `app.suspend_hotkeys()` for the window's lifetime (so typing never
    self-triggers), resume on close.
  - An `NSTextView` pre-filled with `app.history.latest()` (the *original*).
  - A live label under it showing the derived preview by calling
    `flow.learning.derive(original, current_text, flow.common_words.is_common)`
    and formatting `Will learn: <from> → <to>` lines plus
    `Bias vocabulary: <terms>`.
  - **Save & learn** button → `app.learn(original, current_text)` then close.
  - **Cancel**/Esc (keycode 53) → close, learn nothing.
  - All on the AppKit main thread, like `settings_window`.

- [ ] **Step 2: Verify import**

Run: `.venv/bin/python -c "import flow.correction_window"`
Expected: no error.

- [ ] **Step 3: Wire the hook (menubar/app startup will set it in Task 11)** — leave
  a module-level `open_correction_window(app)` callable that the menubar binds to
  `app.open_correction_window`.

- [ ] **Step 4: Commit**

```bash
git add flow/correction_window.py
git commit -m "feat: correction_window.py — popup editor for correct-and-learn"
```

---

### Task 11: Menu rows + Learned-words management (GUI)

**Files:**
- Modify: `flow/menubar.py`

- [ ] **Step 1: Read `flow/menubar.py`** to match its row-building pattern. Add:
  - **"Correct last dictation…"** row → calls `open_correction_window(app)`; also
    set `app.open_correction_window = lambda: open_correction_window(app)` at build
    time so the ⌘⌥ tap works.
  - **"Learned words"** submenu listing each `r` in `app.dictionary.replacements`
    where `r.learned`, label `"{r.from_} → {r.to}"`, each row’s action deletes that
    rule (`app.dictionary.replacements.remove(r)`, `save_dictionary`, rebuild
    `app.corrector`, refresh). A trailing **"Reset learned words"** row removes all
    `learned` rules. (Manual entries are left untouched.)
  - **"Open dictionary file…"** row → reveal `paths.DICTIONARY_PATH` in Finder.
  - Set `app.on_learned` to a callable that rebuilds the "Learned words" submenu so
    a new rule appears immediately.
  - Use **menu rows, not notifications** (banners are unreliable on this Mac).

- [ ] **Step 2: Verify import + manual**

Run: `.venv/bin/python -c "import flow.menubar"`
Manual: launch the app (`./run.sh`), dictate, tap ⌘⌥, edit, Save; confirm the
rule appears under "Learned words" and re-dictating applies it.

- [ ] **Step 3: Commit**

```bash
git add flow/menubar.py
git commit -m "feat: menu rows for correction + learned-words management"
```

---

### Task 12: Record the third (correct) shortcut in settings

**Files:**
- Modify: `flow/settings_window.py`

- [ ] **Step 1: Read `flow/settings_window.py`** and add a third recorder row
  ("Correct last dictation") beside dictate + re-paste, reusing the existing
  recorder/validation. On Save: validate via `validate_combo`, apply live
  (`App.set_hotkeys` — extend it to also (re)bind `correction_hotkey`), persist via
  `hotkey_state.save`, and update any menu header. On Cancel, resume unchanged.

- [ ] **Step 2: Verify import + manual**

Run: `.venv/bin/python -c "import flow.settings_window"`
Manual: open Configuration, record a new correct combo, Save, confirm it triggers
the editor and persists across restart.

- [ ] **Step 3: Commit**

```bash
git add flow/settings_window.py flow/app.py
git commit -m "feat: configure the correction shortcut in the settings window"
```

---

## Phase 4 — Startup, packaging, docs

### Task 13: Startup load + example + packaging + docs

**Files:**
- Modify: `main.py`, `TRDSpeak.spec`, `README.md`, `GETTING_STARTED.md`
- Create: `dictionary.json.example`

- [ ] **Step 1: `main.py`** — after config load, log a one-line dictionary summary
  using the already-loaded `app.dictionary`:
  ```python
  d = app.dictionary
  learned = sum(1 for r in d.replacements if r.learned)
  print(f"dictionary: {len(d.replacements)} replacements ({learned} learned), "
        f"{len(d.vocabulary)} vocabulary terms")
  ```
  (Dictionary loading already happens in `App.__init__`; do not load twice.)

- [ ] **Step 2: `dictionary.json.example`** — commit a documented example:
  ```json
  {
    "vocabulary": ["TRD Speak", "faster-whisper", "Diotalevi", "CTranslate2", "GitHub", "Claude"],
    "replacements": [
      { "from": "fast whisper", "to": "faster-whisper" },
      { "from": "see translate", "to": "CTranslate2" },
      { "from": "lo cal flow", "to": "LocalFlow", "case_sensitive": true }
    ]
  }
  ```

- [ ] **Step 3: `TRDSpeak.spec`** — add `flow/data/common_words.txt` to the bundle
  `datas` so the guardrail list ships in the frozen app. Confirm the path resolves
  via `Path(__file__).parent / "data"` inside the bundle (matches `common_words.py`).

- [ ] **Step 4: Docs** — in `README.md` / `GETTING_STARTED.md` document: the
  correction shortcut (default ⌘⌥), the `dictionary.json` location/format, that
  learning is silent-but-reversible (Learned words menu), and that common-word
  homophones (cloud/Claude) get vocabulary biasing, not fixed rules (LLM tier is
  future).

- [ ] **Step 5: Full suite + import checks**

Run: `.venv/bin/python -m pytest -q`
Run: `.venv/bin/python -c "import flow.correction_window, flow.menubar, flow.settings_window, main"`

- [ ] **Step 6: Commit**

```bash
git add main.py TRDSpeak.spec dictionary.json.example README.md GETTING_STARTED.md
git commit -m "feat: startup dictionary summary, example, bundling, and docs"
```

---

## Final verification (before declaring complete)

- [ ] `.venv/bin/python -m pytest -q` — all pass.
- [ ] Functional, on a dev build (ground rule #1): dictate a sentence with an
  uncommon mishearing → tap ⌘⌥ → fix it → Save → re-dictate → confirm the paste is
  corrected, the rule shows under "Learned words", and a common-word homophone
  (cloud→Claude) is NOT turned into a rule but Claude appears in vocabulary.
- [ ] Deliver a dev build per the standing workflow (user installs/tests it).
