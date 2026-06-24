# Deterministic word replacement & custom vocabulary — design

**Date:** 2026-06-23
**Status:** Draft — research complete, pending review.
**Issue:** #1 — "explore how to build deterministic word replacement and custom vocabulary"
**See also:** [`2026-06-23-contextual-correction-local-llm-design.md`](./2026-06-23-contextual-correction-local-llm-design.md)
— the third tier. The two tiers below (decode-time biasing + deterministic
replacement) **cannot** fix *contextual* homophones like "Claude"↔"cloud", because
the right word depends on sentence meaning. That companion spec adds an optional
local-LLM cleanup pass for exactly those cases.

> Built from online research (multiple verification agents) per the project's
> ground rules, **not** from memory. The verified findings and their sources are
> in [Research basis](#research-basis) at the end; the design decisions above it
> are derived from those findings and from the actual TRD Speak code, with file
> and line references confirmed against the working tree (faster-whisper
> **1.2.1**, the version pinned in this checkout).

## Problem

TRD Speak transcribes speech locally with faster-whisper and pastes the result
at the cursor. Two recurring failures have no remedy today:

1. **Consistent mis-transcriptions.** Whisper reliably mishears certain words the
   same wrong way — names, jargon, acronyms, product names — and the user has to
   fix them by hand after every dictation.
2. **No custom vocabulary.** Words that are not in Whisper's "comfort zone"
   (personal names, domain terms, brand spellings) are guessed at and come out
   inconsistently.

The maintainer wants what Wispr Flow and similar tools offer: a way to **replace
words that are commonly misspelled/misheard** and to teach the app **custom
vocabulary**. This document is the full specification for that functionality.

## What the research says (one paragraph)

Every comparable product — Wispr Flow, superwhisper, MacWhisper — independently
converges on the **same two-tier model**, and that is the model this spec adopts:

- **Tier A — custom vocabulary as a decode-time hint.** The custom terms are fed
  to the recognizer *before* text exists, biasing it toward those words.
  Probabilistic; best for words Whisper has never reliably heard. In
  faster-whisper this is the `hotwords` / `initial_prompt` parameters.
- **Tier B — deterministic post-transcription replacement.** After text exists,
  known wrong→right pairs are swapped by exact, case-aware, whole-word
  find-and-replace. Repeatable and unit-testable; best for words Whisper
  *consistently* gets wrong, and for the "commonly misspelled" cases in the
  issue. superwhisper's docs describe this tier as "performed programmatically …
  does not rely on AI interpretation, ensuring replacements happen exactly as you
  define them" — exactly the determinism the issue title asks for.

They fix different failure modes, so production apps ship both. **Tier B is the
heart of this issue and the heart of v1**; Tier A is a best-effort second part;
phonetic/fuzzy matching is a deliberate future phase (see Non-goals).

## Goals

- A user-curated **replacement dictionary** of wrong→right pairs, applied
  deterministically to every transcription before it is pasted (Tier B).
- Per-rule control matching the prior art: **whole-word** matching by default
  (so `cat`→`dog` does not turn `category` into `dogegory`) and an optional
  **case-sensitive** toggle.
- A user-curated **custom vocabulary** list fed to faster-whisper as a
  decode-time `hotwords` hint to bias recognition toward those terms (Tier A).
- **Fully local, no new network or cloud dependency** — consistent with the
  app's "no cloud, no accounts" ethos.
- **Deterministic and functionally testable**: given the same input text and the
  same dictionary, the output is always identical, and there is an automated test
  that exercises the real pipeline (per the project's ground rule #1).
- Dictionary lives in a **user-editable file that survives app reinstall**
  (the frozen `.app` bundle is read-only), following the existing
  `hotkey_state` / `engine_state` precedent.

## Non-goals

- **No phonetic / fuzzy ("sounds-like") matching in v1.** Double Metaphone +
  edit-distance can catch the *variable* ways Whisper mishears a term, but it
  carries real over-replacement risk (the canonical failure is "I like
  **algorithms**" → "I like **Al Gore**") and must be threshold-tuned and
  heavily tested. It is specified as a gated future phase, off by default — see
  [Phasing](#phasing). v1 stays deterministic.
- **No regex, wildcards, or context rules in the replacement data model.** None
  of Wispr Flow, superwhisper, or MacWhisper expose these for the basic feature;
  plain wrong→right string pairs cover the issue. (Regex is noted as a trivially
  addable per-rule opt-in later.)
- **No in-app GUI editor for the dictionary in v1.** Editing is by hand in a JSON
  file (and an optional menu "Reload dictionary" / "Open dictionary file" row).
  A Configuration-panel editor is a clean follow-up but is out of scope here.
- **No pronunciation training** (Dragon-style "speak the word to teach it"):
  faster-whisper exposes no such hook; `hotwords` is the only biasing lever.
- **No cross-language support.** The engine is English-only (`language="en"`);
  the word-boundary handling is specified for English (see Edge cases).

## Decisions (from the research + brainstorming)

| Decision | Choice | Why |
| --- | --- | --- |
| Overall model | Two tiers: vocabulary→decode-time hint, replacements→deterministic post-processing. | Convergent prior art (Wispr/superwhisper/MacWhisper). |
| v1 scope | Tier B (replacements) is the core; Tier A (vocabulary/hotwords) included as best-effort. | Issue centers on deterministic replacement; Tier A is probabilistic/unproven. |
| Replacement data model | List of `{from, to, case_sensitive?, whole_word?}`. | Mirrors MacWhisper (Original→Replacement + Case-Sensitive + Only-Separate-Words). |
| Matching default | Whole-word, case-insensitive **match**; output verbatim as written. | superwhisper's model; avoids `category`/`dogegory` substring bugs. |
| Replacement engine | **Hand-rolled, pure-`re`**, longest-match-first, single compiled pass. | Dictionaries are small (tens–hundreds); Aho-Corasick's linear-time win is irrelevant, and a C extension complicates the PyInstaller bundle. Avoids FlashText's known overlapping-keyword bug. |
| Vocabulary → engine | Joined into a `hotwords` hint string passed to `transcribe()`. | faster-whisper 1.2.1 supports `hotwords`; verified present in the installed signature. |
| `prefix` | Not used (we must not set it). | `hotwords` is **silently ignored when `prefix` is set** — documented faster-whisper pitfall. |
| Storage | `~/Library/Application Support/TRD Speak/dictionary.json`. | User-editable, survives reinstall; matches `hotkey_state.py`/`engine_state.py`. config.toml is read-only inside the frozen bundle. |
| Correction point | In `App._process()`, right after transcribe, **before** `history.add`. | One corrected string flows through history, re-paste, and paste uniformly. |
| Empty / missing dictionary | Feature is inert (identity transform, no hotwords). | Zero behavior change for users who never configure it. |

## Architecture

Two new pure-Python, AppKit-free, directly-unit-testable modules, plus small
edits to the engine, the app loop, and startup wiring. Nothing touches the
fragile hotkey/event-tap code.

```
~/Library/Application Support/TRD Speak/dictionary.json
        │  (load at startup; reload on demand)
        ▼
flow/dictionary.py ──► Dictionary{ vocabulary: [str], replacements: [Replacement] }
        │                              │
        │ vocabulary                   │ replacements
        ▼                              ▼
flow/engines/whisper.py          flow/corrector.py
  transcribe(audio,                TextCorrector.correct(text) -> text
    hotwords=<vocab hint>)         (deterministic, case/word-boundary aware)
        │                              ▲
        ▼                              │
        raw text ─────────────────────┘  applied in App._process()
                                          (after transcribe, before history.add)
```

### Data flow inside `App._process()`

```
audio ─► transcriber.transcribe(audio, hotwords=vocab_hint)   # Tier A (flow/app.py:139)
      ─► text = corrector.correct(text)                        # Tier B (new line ~140)
      ─► history.add(text)                                     # corrected text stored (:146)
      ─► paste_text(text + " ")                                # corrected text pasted (:160)
```

Putting the correction immediately after transcription (between the current
`flow/app.py:139` and `:146`) means the **single corrected string** is what gets
stored in history, re-pasted by the re-paste hotkey, and shown in the Recent
Dictations menu — no double-correction, no raw/corrected divergence. This is the
same "capture the right thing before the paste attempt" reasoning the existing
`_process()` comment already uses.

### Components

1. **`flow/dictionary.py` — new.** Loads and validates the user dictionary.
   - `@dataclass Replacement: from_: str; to: str; case_sensitive: bool = False;
     whole_word: bool = True` (field named to avoid the `from` keyword; JSON key
     is `"from"`).
   - `@dataclass Dictionary: vocabulary: list[str]; replacements:
     list[Replacement]`.
   - `def load_dictionary(path: Path | None = None) -> Dictionary`:
     - Default path `~/Library/Application Support/TRD Speak/dictionary.json`
       (resolve via the same Application-Support helper `hotkey_state.py` uses;
       factor it into a shared `flow/app_support.py` if not already shared).
     - **Missing file → `Dictionary([], [])`** (feature inert). Never created
       automatically with surprising contents; a documented example ships in the
       repo (`dictionary.json.example`).
     - **Malformed JSON / bad types → raise `ValueError`** with a clear message;
       `main.py` logs it and continues with an empty dictionary so a typo in the
       file can never stop dictation from working.
     - Validation: `vocabulary` is a list of non-empty strings; each replacement
       has non-empty string `from`/`to`, optional bool flags; unknown keys
       ignored (same liberal-load policy as `load_config`).
   - Pure Python, no AppKit import — unit-testable.

2. **`flow/corrector.py` — new.** The deterministic Tier-B engine.
   - `class TextCorrector` built from `list[Replacement]`.
   - **Construction:** sort rules **longest-`from`-first** (so a multi-word rule
     like `"machine learning"` wins over `"machine"`), then compile a behavior
     per rule:
     - `whole_word=True` → wrap the escaped `from` in `\b…\b` (ASCII word
       boundaries — see the Unicode edge case below).
     - `case_sensitive=False` (default) → compile with `re.IGNORECASE`.
   - **`correct(text: str) -> str`:** apply rules in longest-first order. Two
     viable implementations, decided during build:
     - (a) one combined alternation compiled once, with a callback that maps each
       match back to its rule's `to` (single pass, longest-first via alternation
       order); or
     - (b) sequential `pattern.sub` per rule.
     For a personal dictionary (tens–hundreds of rules) both are instant; (a) is
     preferred for single-pass determinism (a later rule cannot re-edit an
     earlier rule's output). The choice is an implementation detail covered by
     the same behavioral tests.
   - **Output casing:** the replacement is emitted **verbatim as written** by the
     user (superwhisper's "output in the case you specify" model). An optional
     `match_case` behavior — mirror the matched token's shape (ALL-CAPS / Capitalized
     / lower) for single-word rules — is specified but defaults **off** to keep v1
     dead simple and fully predictable. Documented, tested if enabled.
   - **Empty rule list → identity** (`correct(text) is text` semantics).
   - Pure Python, no AppKit import — unit-testable.

3. **`flow/engines/whisper.py` — Tier A hint.** Extend the transcriber so the
   custom vocabulary biases decoding.
   - `WhisperTranscriber.__init__` gains `hotwords: str | None = None` (or
     `vocabulary: list[str]`, joined internally into a single hint string, e.g.
     comma- or space-separated).
   - `transcribe()` passes it through:
     ```python
     segments, _info = self._model.transcribe(
         audio,
         language="en",
         beam_size=self.beam_size,
         vad_filter=True,
         condition_on_previous_text=False,
         hotwords=self._hotwords or None,   # NEW — None ⇒ unchanged behavior
     )
     ```
   - **Must not set `prefix`** — `hotwords` is silently ignored when `prefix` is
     non-`None` (verified in faster-whisper's `get_prompt`). We never set it, so
     this is just a "don't add it later" guard documented in the code.
   - `initial_prompt` is an equally valid lever; `hotwords` is the more targeted,
     purpose-built one for vocabulary biasing, so v1 uses `hotwords`. Keep the
     hint **short** (a bounded list, see Open questions) — do not dump an
     unbounded dictionary into it.

4. **`flow/engines/__init__.py` — wiring.** `make_transcriber("whisper", config,
   dictionary)` passes the vocabulary hint into the `WhisperTranscriber`
   constructor (thread the dictionary through, or pass the precomputed hint
   string).

5. **`flow/app.py` — apply Tier B.** In `App.__init__`, build
   `self.corrector = TextCorrector(dictionary.replacements)`. In `_process()`,
   immediately after `text = self.transcriber.transcribe(audio)` (line 139) and
   **before** `self.history.add(text)` (line 146):
   ```python
   text = self.corrector.correct(text)
   ```
   Everything downstream (history, re-paste, paste) then uses the corrected text
   unchanged.

6. **`main.py` — startup.** Load the dictionary once at boot
   (`load_dictionary()`), log a one-line summary (`"dictionary: N replacements,
   M vocabulary terms"`), pass it into `App`/`make_transcriber`. A load error is
   logged and degrades to an empty dictionary — never fatal.

7. **`flow/menubar.py` — optional, minimal.** A single "Reload dictionary" row
   (re-reads the file, rebuilds `TextCorrector`; the vocabulary hint applies to
   the *next* model load) and/or "Open dictionary file…" (reveals the JSON in
   Finder). Keyboard/menu wiring is verified manually, consistent with
   `menubar.py` not being unit-tested. Can be deferred — editing-then-restart
   works without it.

### Storage format (`dictionary.json`)

```json
{
  "vocabulary": ["TRD Speak", "faster-whisper", "Diotalevi", "CTranslate2"],
  "replacements": [
    { "from": "fast whisper",   "to": "faster-whisper" },
    { "from": "see translate",  "to": "CTranslate2" },
    { "from": "lo cal flow",    "to": "LocalFlow", "case_sensitive": true },
    { "from": "github",         "to": "GitHub", "whole_word": true }
  ]
}
```

A committed `dictionary.json.example` documents every field and the defaults
(`case_sensitive=false`, `whole_word=true`), mirroring `config.toml.example`.

### Considered and rejected

- **FlashText for the replacement tier.** It is the most-cited copyable
  implementation (Trie + Aho-Corasick, whole-word by default), but it is largely
  unmaintained (last release ~2020), has a **documented longest-match bug on
  overlapping keywords** (returns the shorter match, issue #104) and a Unicode
  word-boundary limitation (#48). For a *small* personal dictionary its
  linear-time advantage buys nothing. Rejected in favor of a hand-rolled
  `re`-based pass we fully control and test.
- **pyahocorasick (C extension).** Maintained and correct, but it is a compiled
  CPython extension — extra weight and a PyInstaller bundling concern — for zero
  practical benefit at this scale. Rejected for v1; revisit only if dictionaries
  ever grow huge.
- **Storing the dictionary in `config.toml`.** `config.toml` resolves to a path
  *inside* the package/bundle (`flow/config.py:_default_path()`), which is
  read-only in the frozen `.app` and reset on reinstall. User-curated data must
  live in `~/Library/Application Support/TRD Speak/`, exactly like hotkeys and
  the engine choice. (A `[replacements]`/`[vocabulary]` seed in config.toml for
  source checkouts is a possible convenience, deferred.)
- **Phonetic/fuzzy matching in v1.** High over-replacement risk, needs tuning and
  a large test corpus to trust; deferred to a gated phase (see Phasing).
- **Correcting inside the engine vs. in the app loop.** Keeping `transcribe()`
  returning raw text and doing Tier B in `app._process()` keeps the engine a pure
  audio→text function and puts the deterministic, testable correction on the one
  code path every dictation already flows through.

## Edge cases

- **Empty / missing dictionary file:** identity correction, no hotwords; behavior
  is byte-for-byte the same as today. This is the default for every existing
  user.
- **Malformed `dictionary.json`:** logged, treated as empty — never blocks
  dictation.
- **Substring safety:** `whole_word=True` (default) means `cat`→`dog` leaves
  `category` untouched. Disabling it (`whole_word=false`) is the documented escape
  hatch for affix-style replacements, matching MacWhisper's "Only Replace Separate
  Words" toggle.
- **Overlapping / nested rules:** longest-`from`-first ordering makes
  `"machine learning"` win over `"machine"`. This is the case FlashText gets
  wrong; the hand-rolled single-pass alternation gets it right and is the reason
  we control the matcher.
- **Casing:** default match is case-insensitive and output is verbatim as the
  user wrote `to`. `case_sensitive=true` requires the source casing to match.
  Context-aware case preservation (sentence-initial capitalization, ALL-CAPS
  mirroring) is the optional, off-by-default `match_case` behavior — surveyed apps
  don't do it, so v1 doesn't either by default.
- **Unicode word boundaries:** Python `re`'s `\b` with `re.UNICODE` (the default
  for `str`) handles accented letters correctly — this *avoids* FlashText's
  documented `[A-Za-z0-9_]`-only boundary bug (#48). Tests cover an accented term
  to lock this in. The engine is English-only, so this is a safety margin rather
  than a feature.
- **Punctuation adjacency:** `\b` boundaries let a rule fire next to punctuation
  (`"github."` → `"GitHub."`). Tested.
- **Leading/trailing spacing at paste:** the paste path already appends one
  trailing space (`text + " "`); correction operates on the bare transcript and
  must not introduce or strip edge whitespace. Tested with rules at string start
  and end.
- **Plurals / inflections:** out of scope for exact matching — `cat`→`dog` will
  not touch `cats`. Documented; the user adds a second rule if they want it. (A
  future phonetic/lemmatized tier is where this would live.)
- **`hotwords` over-biasing / hallucination:** decode-time biasing is
  probabilistic and can, in principle, push the model toward a hinted word that
  wasn't said. Keep the vocabulary hint **bounded** and let users curate it;
  measured during build (see Open questions). Tier B never has this risk — it only
  fires on exact matches.
- **`prefix` interaction:** documented guard — never set `prefix`, or `hotwords`
  goes silently dead.

## Testing

Per ground rule #1, every feature ships with an automated **functional** test
that exercises real behavior, and each must pass before the feature is declared
complete.

- **`tests/test_corrector.py` (new) — Tier B unit behavior:**
  - whole-word default: `cat`→`dog` rewrites `cat` but not `category`;
  - `whole_word=false` rewrites the substring;
  - longest-match-first: with both `machine` and `machine learning` rules, the
    longer one wins;
  - case-insensitive match + verbatim output; `case_sensitive=true` only fires on
    matching case;
  - punctuation adjacency, start-of-string and end-of-string rules, no stray
    whitespace introduced;
  - an accented/Unicode term respects word boundaries;
  - empty rule list is an exact identity transform;
  - determinism: same input twice → identical output.
- **`tests/test_dictionary.py` (new):**
  - missing file → empty `Dictionary`;
  - a valid file parses vocabulary + replacements with defaults applied;
  - malformed JSON and bad types raise `ValueError`;
  - unknown keys ignored.
- **`tests/test_whisper_engine.py` (extend) — Tier A wiring:** monkeypatch the
  `WhisperModel`/`transcribe` to capture kwargs; assert the vocabulary hint is
  passed as `hotwords` and that `prefix` is never set; assert `hotwords=None`
  when the vocabulary is empty (unchanged call).
- **`tests/test_app_engine.py` (extend) — pipeline functional test:** with a stub
  transcriber returning a known *raw* mis-transcription and a loaded dictionary,
  drive `App._process()` (monkeypatching `paste_text`, stubbing
  `wait_all_released`→`True` as the existing tests do) and assert **both** the
  pasted text **and** `app.history.items()[0]` equal the **corrected** string —
  proving correction happens once, before history, and flows to the paste.
- **`tests/test_corrector_e2e.py` (new) — gold end-to-end functional test:** run
  a short fixture WAV (committed, a few seconds, recorded or TTS-synthesized) that
  reliably produces a known mis-transcription through the **real**
  `WhisperTranscriber`, then through the real `TextCorrector` loaded from a test
  `dictionary.json`, and assert the final text contains the corrected term. This
  is the test that exercises the feature's *real* behavior end to end (audio →
  faster-whisper → deterministic correction), not just helpers. Marked slow; runs
  in CI on the same model the app bundles (`base.en`).
- **Menu rows** (Reload / Open file), if built, are verified manually — consistent
  with `menubar.py` being un-unit-tested.

## Phasing

1. **Phase 1 — Tier B, deterministic replacements (this issue's core).**
   `dictionary.py` + `corrector.py` + `app.py` wiring + the replacement half of
   `dictionary.json`. Fully deterministic, fully tested. Ships the headline value
   ("replace commonly misspelled words").
2. **Phase 2 — Tier A, custom vocabulary via `hotwords`.** Engine + wiring + the
   `vocabulary` half of the file. Best-effort biasing; gated behind measuring its
   real effect (Open questions). Has no effect on Tier B's determinism.
3. **Phase 3 — phonetic/fuzzy fallback (future, off by default).** Double
   Metaphone (`metaphone`/`phonetics`) + edit-distance (`jellyfish` /
   `rapidfuzz`) to map the *variable* ways Whisper mishears a curated term onto
   its intended spelling, applied only after exact Tier-B misses, behind a
   per-rule opt-in and a tunable similarity threshold, with over-replacement
   guard tests. Specified now so the data model leaves room for it; not built in
   v1.

## Files touched

- `flow/dictionary.py` — new (load/validate `dictionary.json`).
- `flow/corrector.py` — new (`TextCorrector`, deterministic Tier-B engine).
- `flow/app_support.py` — new or extended (shared Application-Support path
  helper, if not already factored out of `hotkey_state.py`).
- `flow/engines/whisper.py` — `hotwords` constructor arg + pass-through.
- `flow/engines/__init__.py` — thread the vocabulary hint into `make_transcriber`.
- `flow/app.py` — build `TextCorrector`; apply `corrector.correct()` in
  `_process()` before `history.add`.
- `main.py` — load the dictionary at startup, log summary, degrade on error.
- `flow/menubar.py` — optional "Reload dictionary" / "Open dictionary file…" rows.
- `dictionary.json.example` — new, documented example.
- `tests/test_corrector.py`, `tests/test_dictionary.py`,
  `tests/test_corrector_e2e.py` — new.
- `tests/test_whisper_engine.py`, `tests/test_app_engine.py` — extended.
- `README.md` / `GETTING_STARTED.md` — document the dictionary file and format.

---

## Research basis

Findings below were gathered by a multi-agent web-research pass and
**adversarially verified** (each claim voted by 3 independent agents; 21 of 25
checked claims confirmed, 4 refuted). Confidence and sources are noted. The
faster-whisper API facts were additionally **confirmed against the installed
package (1.2.1)** in this checkout, not taken from memory.

### Confirmed (high confidence)

- **Two-tier convergence.** Wispr Flow, superwhisper, and MacWhisper all separate
  *vocabulary boosting at decode time* from *post-transcription replacement*.
  superwhisper: vocabulary "gives recognition hints during the transcription
  process"; Replacements operate "at the post-transcription stage … performed
  programmatically and do not rely on AI interpretation," whole-word only
  ("Cat→Dog but Caterpillar not Dogerpillar").
  Sources: Wispr Flow Dictionary docs; superwhisper vocabulary docs; MacWhisper
  find-and-replace docs.
- **MacWhisper data model:** Original→Replacement string pairs, a **Case
  Sensitive** toggle, an **Only Replace Separate Words** (whole-word) toggle, JSON
  import/export, applied to future transcripts. No regex. This is the model v1
  copies.
- **faster-whisper decode-time biasing:** done via `hotwords` (a hint phrase) or
  `initial_prompt`. **Critical pitfall:** `hotwords` is **silently ignored when
  `prefix` is set** (docstring: "Has no effect if prefix is not None"; the code's
  hotwords branch runs only `when not prefix`). v1 never sets `prefix`.
- **Installed-API check (1.2.1):** `WhisperModel.transcribe()` exposes
  `initial_prompt`, `prefix`, `hotwords`, and `suppress_tokens` — confirmed by
  inspecting the signature in this venv. (An earlier exploration claim that these
  params don't exist was **wrong** — a good reminder that this checkout's pinned
  version is the authority.)
- **Deterministic replacement libraries:** FlashText (Trie + Aho-Corasick,
  whole-word by default, won't match `apple` in `pineapple`) is the canonical
  copyable design, **but** it is largely unmaintained, has a longest-match bug on
  overlapping keywords (issue #104, returns the *shorter* match) and a Unicode
  word-boundary limitation (#48). pyahocorasick is a maintained C alternative;
  `flashtext2` fixes the overlap bug. → v1 hand-rolls a small `re`-based matcher
  to avoid all three for small dictionaries.
- **Fuzzy/phonetic (future tier):** `jellyfish` provides Soundex, Metaphone,
  NYSIIS, Match-Rating + Levenshtein/Damerau-Levenshtein/Jaro/Jaro-Winkler, but
  **not** Double Metaphone (use `metaphone`/`phonetics`). `rapidfuzz` is the fast
  fuzzy lib. Double Metaphone (primary + alternative code; match if either
  matches) is the recommended "sounds-like" test. A published pipeline reports up
  to ~30% relative WER reduction on named entities **using a large cloud LLM in
  the loop** — so the headline number does **not** characterize a purely local
  pipeline; only the Double Metaphone technique is cleanly on-device portable.
  Canonical over-correction failure: "I like algorithms" → "I like Al Gore."

### Refuted / do-not-rely-on

- A hard **448-token** `len(prompt)+max_new_tokens` cap on the prompt window
  (1-2, unverified) — do **not** rely on a specific token cap; re-read current
  `transcribe.py` and measure if it matters.
- The exact `initial_prompt` token-encoding path (0-3).
- That a replacement system **must** always enforce longest-match-first (0-3) —
  it is the right call *here* for nested rules, but it is a choice, not a law.
- Dragon requiring separate written-form vs spoken-form entries (0-3) — Dragon
  instead offers optional spoken **pronunciation training**, which has no
  faster-whisper analogue.

### Open questions (resolve during build, by measurement)

- **How many vocabulary terms can `hotwords`/`initial_prompt` take before biasing
  degrades or truncates?** Token-cap specifics were refuted; needs a direct read
  of the pinned `transcribe.py` plus an empirical test. Keep the hint bounded
  until measured.
- **How effective is `hotwords` in practice** on real audio for novel
  names/acronyms, and does it cause over-biasing/hallucination? No quantitative
  benchmark survived verification — measure on real dictations before promoting
  Tier A from best-effort to a headline feature.
- **Best concrete recipe for context-aware case preservation** (sentence-initial,
  ALL-CAPS, title case) and inflection handling — none of the surveyed apps
  document it; v1 defers it behind the off-by-default `match_case` option.

### Sources

- Wispr Flow Dictionary: https://docs.wisprflow.ai/articles/4052411709-teach-flow-your-words-with-the-dictionary
- superwhisper vocabulary & replacements: https://superwhisper.com/docs/get-started/interface-vocabulary
- MacWhisper find & replace: https://macwhisper.helpscoutdocs.com/article/37-find-and-replace-in-transcriptions
- Dragon vocabulary / pronunciation training: https://www.nuance.com/products/help/dragon/dragon-for-pc/enx/professionalgroup/main/Content/Vocabulary/adding_words_to_your_vocabulary.htm
- faster-whisper `transcribe.py` (hotwords/prefix/initial_prompt): https://github.com/SYSTRAN/faster-whisper/blob/master/faster_whisper/transcribe.py
- FlashText (README + overlap bug #104 + Unicode #48): https://github.com/vi3k6i5/flashtext / https://github.com/vi3k6i5/flashtext/issues/104 / https://github.com/vi3k6i5/flashtext/issues/48
- pyahocorasick: https://pypi.org/project/pyahocorasick/
- jellyfish (phonetic + edit-distance): https://github.com/jamesturk/jellyfish
- rapidfuzz: https://github.com/rapidfuzz/RapidFuzz
- Double Metaphone + LLM NE-correction pipeline (cloud-LLM caveat): https://arxiv.org/html/2506.10779v2
- Whisper prompting overview: https://medium.com/axinc-ai/prompt-engineering-in-whisper-6bb18003562d
- OpenAI Whisper prompt-vs-vocabulary discussion: https://github.com/openai/whisper/discussions/1477
