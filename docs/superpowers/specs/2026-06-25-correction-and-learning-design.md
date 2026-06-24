# Correction & Learning — unified design

**Date:** 2026-06-25
**Status:** Draft — research complete, pending review.
**Issue:** #1 — "explore how to build deterministic word replacement and custom vocabulary"
**Consolidates:** the two 2026-06-23 specs into one complete design (engine + capture UX + UI):
- [`2026-06-23-word-replacement-and-custom-vocabulary-design.md`](./2026-06-23-word-replacement-and-custom-vocabulary-design.md) — Tier A (decode-time `hotwords` biasing) + Tier B (deterministic find-and-replace).
- [`2026-06-23-contextual-correction-local-llm-design.md`](./2026-06-23-contextual-correction-local-llm-design.md) — Tier 3 (local-LLM contextual fix), gated on a feasibility spike.

This document is the authoritative design. It adds the part neither prior spec
covered — a **shortcut-driven "correct your last dictation, and the app learns
from it" loop** — and ties the three tiers together so the engine, the capture
gesture, and the management UI are specified as one feature.

> The two prior specs were each built from a multi-agent, adversarially-verified
> online research pass (per ground rule #3). The *novel* parts of this spec — the
> correct-and-learn capture UX and the safe auto-learning pipeline — were grounded
> in a fresh two-agent research pass on 2026-06-25; verified findings and sources
> are in [Research basis](#research-basis). Code facts (line numbers, the
> `_process()` insertion point, existing modules) were read from the working tree.

## The feature in one gesture

A new global shortcut opens a small editor pre-filled with your **last dictation**.
You fix the text. On **Save**, the app diffs the original against your edit,
**learns the safe corrections silently** (no confirmation dialog), and applies them
to **every future dictation**. It is *learn-only*: it does not re-paste the current
text. Every learned rule is visible and one-click removable.

No surveyed product binds these two halves into one gesture: Dragon does
correct-last-and-silently-learn (via an in-editor menu, not a "grab the last
result" hotkey); Wispr Flow learns from edits silently (proper-nouns only, ✨-
flagged, reversible) but has no "fix the last dictation" shortcut. This combined
flow is a deliberate differentiator (see [Research basis](#research-basis)).

## The three tiers

The full system is three layers over one shared `dictionary.json`. This spec
**builds Tier A + Tier B** and the capture/learn loop; **Tier 3 is documented as a
gated future phase** (off by default, behind the feasibility spike from the
companion spec). The learn loop only ever feeds Tiers A and B.

| Tier | What it does | When it runs | v1? |
| --- | --- | --- | --- |
| **A — vocabulary biasing** | Feeds custom terms to faster-whisper as a `hotwords` hint so it is more likely to hear them right *before text exists*. | At transcribe time. | **Yes** |
| **B — deterministic replacement** | Exact, whole-word, case-preserving wrong→right swaps applied to the transcript *after* it exists. | After transcribe, before history/paste. | **Yes** |
| **3 — local-LLM contextual fix** | Resolves *contextual* homophones (cloud↔Claude by sentence meaning) that no fixed rule can. | Optional pass after Tier B. | **No — future, spike-gated** |

### The honest limit that shapes v1

A deterministic `cloud→Claude` rule would corrupt every legitimate use of "cloud";
`guitar→GitHub` corrupts every real "guitar". This is the **Cupertino trap** — a
blind replacement silently rewriting correct text. Therefore:

- **Tier B auto-learns a fixed rule only when the misheard ("wrong") word is
  uncommon / out-of-vocabulary** — a word you would not otherwise type, so the
  rule cannot corrupt legitimate text (e.g. `fastwhisper→faster-whisper`,
  `see translate→CTranslate2`, mishearings of "Diotalevi").
- **Common-word homophones** (cloud↔Claude, guitar↔GitHub) are **not** turned into
  Tier-B rules. For these, v1 still does the safe, useful thing: it **adds the
  corrected target to the vocabulary (Tier A)**, biasing the recognizer toward
  "Claude"/"GitHub" so the mishearing happens *less* in the first place. A true
  contextual fix for these is exactly **Tier 3's** job, later.

This makes the system honest: it never silently corrupts a paste, and it routes
each correction to the tier that can handle it safely.

## Goals

- A single **shortcut** that opens an editor on the last dictation, captures the
  user's correction, and **learns from it** with no confirmation dialog.
- **Deterministic Tier-B engine**: whole-word (default), case-preserving,
  single-pass, longest-match-first replacement; identical output for identical
  input + dictionary (testable).
- **Tier-A vocabulary biasing** via faster-whisper `hotwords`, applied on the very
  next dictation (no model reload).
- **Safe auto-learning**: only derivations that cannot silently corrupt future
  pastes become rules; common-word homophones get vocabulary biasing instead.
- **Every learned rule is visible and one-click reversible** (the trust net that
  silent learning requires).
- **Fully local** — no cloud, no accounts, no new network dependency.
- Dictionary lives in a **user-editable file that survives reinstall**, isolated
  per build, following the `paths.py` / `history.py` precedent.

## Non-goals

- **No re-paste of the current text on Save** (the chosen behavior is learn-only).
- **No confirmation dialog** before learning — the explicit edit is the intent
  signal; safety comes from automatic gates + reversibility, not a prompt.
- **No Tier-3 local-LLM in v1** — documented as a future, spike-gated phase.
- **No phonetic / fuzzy replacement in v1** (Double Metaphone etc.). Specified as a
  future tier in the companion spec; not built here. (It survives only as an
  *optional* secondary signal in the learn gate, never as the primary mechanism.)
- **No regex / wildcards / context rules** in the replacement data model — plain
  wrong→right pairs with `case_sensitive` / `whole_word` flags, matching the
  MacWhisper/superwhisper data model.
- **No cross-language support** — English-only (`language="en"`); word boundaries
  specified for English.

## Decisions

| Decision | Choice | Why |
| --- | --- | --- |
| Capture UX | **Popup editor window** pre-filled with the last dictation. | User choice; richest signal, no second recognition pass. Models the existing `settings_window.py` NSWindow. |
| On Save | **Learn only** (no re-paste of current text). | User choice; the editor teaches, it does not fix the current instance. |
| Confirmation | **None** (no dialog). | User choice; replaced by automatic safety gates + reversibility (Dragon/Wispr precedent). |
| What is learned | Tier-B rule **only for uncommon `wrong` words**; **always** add the corrected target to Tier-A vocabulary. | Avoids the Cupertino trap; common-word homophones are Tier 3's job. |
| Learn timing | **Immediately** on the explicit correction; no repetition threshold. | The deliberate edit is the intent signal (Dragon's LM adapts immediately from one Correct). |
| Reversibility | Learned rules flagged + listed with disable/delete; "Learned words…" surface. | Silent learning is only safe if visible and undoable. |
| Replacement engine | Hand-rolled, pure-`re`, single combined alternation, longest-first, case-preserving. | Small dictionaries; avoids FlashText's overlap bug and a C-extension bundling cost. |
| Diff → rules | `difflib.SequenceMatcher` over **word tokens** (`autojunk=False`), keep **1-word→1-word `replace`** opcodes only. | Token diff yields one clean swap per changed word; inserts/deletes/multi-word edits are rephrasings, not safe rules. |
| "Uncommon word" test | Membership in a **bundled common-English-word list** (e.g. `wordfreq` or a shipped top-N set), decided in the plan. | Offline, deterministic; the Cupertino guardrail. |
| Vocabulary → engine | Joined into a `hotwords` hint passed **per `transcribe()` call**. | faster-whisper 1.2.1 exposes `hotwords` on `transcribe`; per-call means a learned term applies on the next dictation with no reload. |
| `prefix` | Never set. | `hotwords` is silently ignored when `prefix` is set (documented faster-whisper pitfall). |
| Correction point | `App._process()`, right after `transcribe()` (app.py:145), before `history.add()` (app.py:152). | One corrected string flows through history, re-paste, and paste uniformly. |
| Shortcut default | Modifier-only **⌘⌥** tap; configurable via the settings window (extended to 3 combos). | Modifier-only clean-release combos are the ones proven to fire reliably (the repaste lesson); char-chords did not. |
| Storage | `~/Library/Application Support/TRD Speak[ Dev]/dictionary.json` via `paths.py`. | User-editable, survives reinstall, isolated per build (like `dictations.json`). |
| Empty / missing dictionary | Feature inert: identity correction, no hotwords. | Zero behavior change for users who never correct anything. |

## Architecture

```
~/Library/Application Support/TRD Speak[ Dev]/dictionary.json
        │  (load at startup; rebuilt live on Save / Reload)
        ▼
flow/dictionary.py ──► Dictionary{ vocabulary: [str], replacements: [Replacement] }
        │                                  │
        │ vocabulary (Tier A)              │ replacements (Tier B)
        ▼                                  ▼
flow/engines/whisper.py                flow/corrector.py
  transcribe(audio,                      TextCorrector.correct(text) -> text
    hotwords=<vocab hint>)               (deterministic, whole-word, case-preserving)
        │                                  ▲
        ▼                                  │  applied in App._process()
   raw text ───────────────────────────────┘  (after transcribe, before history.add)


Capture / learn loop (the new gesture):

  ⌘⌥ shortcut ─► correction_hotkey (CGEventTap, like the re-paste tap)
              ─► App._on_correct() ─► correction_window (NSWindow, taps suspended)
                     │  pre-filled with History.latest()
                     │  user edits ──► live "Will learn: …" preview
                     ▼  Save & learn
              flow/learning.py: derive_rules(original, edited)
                     │  word-diff ─► 1:1 replace opcodes ─► gates
                     ├─► always: add target to Dictionary.vocabulary  (Tier A)
                     └─► if `wrong` is uncommon: add Replacement       (Tier B)
                     ▼
              dictionary.py.save()  ─►  App rebuilds TextCorrector + vocab hint live
                     ▼
              menubar "Learned words…" list (flag + disable/delete)
```

### Components

1. **`flow/dictionary.py` — new.** Load/validate/save the user dictionary.
   - `@dataclass Replacement: from_: str; to: str; case_sensitive: bool = False;
     whole_word: bool = True; learned: bool = False; ts: str | None = None`
     (field named `from_` to avoid the keyword; JSON key is `"from"`; `learned`/`ts`
     mark and date auto-learned rules for the management UI).
   - `@dataclass Dictionary: vocabulary: list[str]; replacements: list[Replacement]`.
   - `load_dictionary(path=DICTIONARY_PATH) -> Dictionary`: missing file →
     `Dictionary([], [])` (inert); malformed JSON / bad types → `ValueError`
     (caller logs and degrades to empty so a typo never stops dictation); unknown
     keys ignored.
   - `save_dictionary(dict, path=DICTIONARY_PATH)`: atomic write (temp + `os.replace`),
     creating the parent dir — same durability pattern as `history.py`.
   - Pure Python, no AppKit import — unit-testable.

2. **`flow/corrector.py` — new.** The deterministic Tier-B engine.
   - `TextCorrector(replacements: list[Replacement])`.
   - Construction: sort rules **longest-`from`-first** (so `"machine learning"`
     wins over `"machine"`); build **one combined alternation** `\b(r1|r2|…)\b`
     (raw-string Unicode `\b`), escaped, with case-insensitive rules folded in.
   - `correct(text) -> text`: **single `re.sub`** dispatched through a function
     `repl` that maps each match back to its rule's `to` and applies
     **case-preservation** (mirror lower / Title / ALL-CAPS of the matched token
     for single-word rules; verbatim otherwise). Single pass ⇒ a rule's output can
     never be re-matched by another rule (no cascade, no order bug, no loop).
   - `whole_word=False` rules are matched without `\b`; `case_sensitive=True` rules
     are matched case-sensitively. Empty rule list → identity.
   - Pure Python, no AppKit import — unit-testable.

3. **`flow/learning.py` — new (the novel, safety-critical module).**
   - `derive(original: str, edited: str, is_common: Callable[[str], bool])
     -> LearnResult` where `LearnResult` carries `rules: list[Replacement]` (safe
     Tier-B additions) and `vocab: list[str]` (Tier-A target terms).
   - Algorithm:
     1. Tokenize both into words; `SequenceMatcher(None, a, b, autojunk=False)`;
        take `get_opcodes()`.
     2. Keep only `replace` opcodes spanning **exactly one token on each side**.
        Discard `insert`, `delete`, and any multi-token `replace` (rephrasings).
     3. Reject by format guard: either side empty, or containing
        digits/punctuation/whitespace, or length outside ~[2, 30].
     4. **Always** emit the *target* token into `vocab` (deduped, bounded).
     5. Emit a `Replacement(wrong→target, learned=True)` into `rules` **only if
        `is_common(wrong)` is False** (uncommon/OOV ⇒ safe). Common `wrong` ⇒
        vocab-only (contextual case; flagged for the future Tier-3).
     6. Dedupe; if a `from` already maps elsewhere, the newest correction wins.
   - The `is_common` predicate is injected (a bundled common-word set / `wordfreq`),
     keeping `learning.py` pure and fully unit-testable with a fake predicate.

4. **`flow/correction_window.py` — new.** The popup editor (NSWindow, no nib),
   modeled on `settings_window.py`.
   - Opened from `App._on_correct`; `App.suspend_hotkeys()` stops the global taps
     for the window's lifetime so typing the correction never self-triggers.
   - An `NSTextView` pre-filled with `History.latest()`; a live label showing the
     derived `Will learn: …` preview (recomputed from `derive()` as the text
     changes); **Save & learn** / **Cancel**. Save calls back into `App` to learn +
     persist + rebuild; Cancel/Esc resumes taps and learns nothing.
   - Empty history → a disabled state / "Nothing to correct yet."
   - Not unit-tested (GUI) — verified by import (`python -c "import
     flow.correction_window"`) + manual run, consistent with `settings_window.py`
     and `menubar.py`.

5. **`flow/engines/whisper.py` + `flow/engines/__init__.py` — Tier-A hint.**
   - `transcribe(audio, hotwords: str | None = None)` passes `hotwords` straight
     through to `self._model.transcribe(... , hotwords=hotwords or None)`; **never
     set `prefix`**. `make_transcriber` threads the dictionary through.
   - `App._process()` computes the hint from the *current* `Dictionary.vocabulary`
     on each call, so a just-learned term biases the next dictation immediately.
     Keep the hint bounded (see Open questions).

6. **`flow/app.py` — wiring.**
   - `__init__`: load `Dictionary`; build `self.corrector = TextCorrector(...)`;
     build a second-style `correction_hotkey` listener (its own tap, like
     `repaste_hotkey`).
   - `_process()`: after `text = transcribe(...)` (line 145) and before
     `history.add(text)` (line 152): `text = self.corrector.correct(text)`. Pass
     the vocab hint into `transcribe`. Any corrector exception is caught → keep the
     raw text (never raise into the dictation path).
   - `_on_correct()`: open the correction window (main-thread, like the menu
     actions). `learn(original, edited)`: run `learning.derive`, merge into the
     `Dictionary`, `save_dictionary`, rebuild `TextCorrector`, refresh the menu's
     "Learned words" list.

7. **`flow/config.py` + `flow/paths.py` — settings & storage.**
   - `config.py`: `correct_keys: list[str] = ["cmd", "alt"]`, loaded from a
     `[correct]` TOML table, validated by the shared `validate_keys`.
   - `paths.py`: add `DICTIONARY_PATH = support / "dictionary.json"` (per-build,
     like `DICTATIONS_PATH`).

8. **`flow/menubar.py` — management UI (the trust net).**
   - "Correct last dictation…" row (also bound to ⌘⌥) → opens the window.
   - "Learned words…" → a submenu/list of learned rules (`wrong → right`, flagged
     distinctly from manual ones), each with **disable/delete**; a "Reset learned
     words" row. Plus "Open dictionary file…" (reveal the JSON in Finder).
   - Menu rows, **not** notifications — `display-notification` banners are
     unreliable on this Mac (use always-visible rows for essential info).

9. **`flow/settings_window.py` — extend.** Record a **third** shortcut (correct)
   alongside dictate + re-paste; validate, apply live (`App.set_hotkeys`), persist
   (`hotkey_state.save`). Same recorder pattern already in place.

10. **`main.py` — startup.** `load_dictionary()` once at boot; log a one-line
    summary (`"dictionary: N replacements (M learned), K vocabulary terms"`); pass
    it into `App` / `make_transcriber`. A load error logs and degrades to empty —
    never fatal.

### Data flow inside `App._process()`

```
audio ─► text = transcriber.transcribe(audio, hotwords=<vocab hint>)  # Tier A (app.py:145)
      ─► text = corrector.correct(text)                                # Tier B (new line ~146)
      ─► history.add(text)                                             # corrected text stored (:152)
      ─► paste_text(text + " ")                                        # corrected text pasted (:166)
```

Putting correction between transcribe and `history.add` means the **single
corrected string** is what history stores, the re-paste hotkey re-pastes, and the
Recent Dictations menu shows — no raw/corrected divergence. (Same reasoning the
existing `_process()` comment already uses for capturing before the paste attempt.)

### Storage format (`dictionary.json`)

```json
{
  "vocabulary": ["TRD Speak", "faster-whisper", "Diotalevi", "CTranslate2", "GitHub", "Claude"],
  "replacements": [
    { "from": "fast whisper",  "to": "faster-whisper" },
    { "from": "see translate", "to": "CTranslate2", "learned": true, "ts": "2026-06-25T10:00:00" },
    { "from": "diotaleavy",    "to": "Diotalevi", "learned": true, "ts": "2026-06-25T10:01:00" }
  ]
}
```

A committed `dictionary.json.example` documents every field and the defaults
(`case_sensitive=false`, `whole_word=true`, `learned=false`). Manual edits are
first-class; the file is the source of truth and the management UI just edits it.

## The capture & learn flow (step by step)

1. Press the correction shortcut (⌘⌥ tap by default).
2. `correction_window` opens; global taps are suspended; the field is pre-filled
   with `History.latest()`. (Empty history → "Nothing to correct yet.")
3. The user edits the text. A live label shows what *would* be learned, e.g.
   `Will learn: diotaleavy → Diotalevi  ·  bias vocabulary: Claude`.
4. **Save & learn**: `learning.derive(original, edited, is_common)` runs;
   safe Tier-B rules + Tier-A vocab terms are merged into the `Dictionary`;
   `save_dictionary` persists atomically; `App` rebuilds `TextCorrector` and the
   menu list. The window closes. **Nothing is pasted.**
5. **Cancel / Esc**: taps resume, nothing learned.
6. The learned rule is now in "Learned words…", flagged, with disable/delete.

## Edge cases

- **Empty / missing dictionary:** identity correction, no `hotwords`; byte-for-byte
  today's behavior. Default for every existing user.
- **Malformed `dictionary.json`:** logged, treated as empty — never blocks dictation.
- **Empty history at correction time:** the window shows "Nothing to correct yet";
  no learning possible.
- **User rephrases instead of fixing a mishearing** (e.g. "code"→"program"): it is
  a 1:1 `replace`, but `program`/`code` are common words → no Tier-B rule is
  created; at most a vocab nudge. Multi-word rephrases are dropped entirely.
- **Common-word homophone** (cloud→Claude, guitar→GitHub): no Tier-B rule (would
  corrupt legit "cloud"/"guitar"); the target is added to vocabulary so the
  recognizer biases toward it next time; flagged as a Tier-3 candidate.
- **Substring safety:** `whole_word=True` (default) ⇒ `cat`→`dog` leaves `category`
  untouched (the Clbuttic/Scunthorpe guard). Whole-word matching uses raw-string
  Unicode `\b`.
- **Overlapping / nested rules:** longest-`from`-first ordering + single-pass
  alternation make `"machine learning"` win over `"machine"`, with no re-matching.
- **Casing:** match is case-insensitive by default; output mirrors the matched
  token's case for single-word rules (so a learned rule fires correctly at sentence
  start), verbatim otherwise.
- **Hyphens / apostrophes / possessives:** `\b` splits `mother-in-law` and a
  possessive `'s`; covered by explicit tests so behavior is known.
- **A learned `wrong` later becomes something the user says:** the rule is visible
  and one-click removable; "Reset learned words" is the escape hatch.
- **Dictation while the correction window is open:** impossible — taps are
  suspended for the window's lifetime (the `settings_window` invariant).
- **Vocabulary hint grows large:** keep it bounded (see Open questions); over-long
  `hotwords` can degrade/truncate biasing.

## Testing

Per ground rule #1, every feature ships an automated **functional** test that
exercises real behavior, and each must pass before the feature is declared complete.

- **`tests/test_dictionary.py` (new):** missing file → empty `Dictionary`; a valid
  file parses vocabulary + replacements with defaults; malformed JSON / bad types →
  `ValueError`; unknown keys ignored; `save_dictionary` round-trips atomically.
- **`tests/test_corrector.py` (new):** whole-word default (`cat`→`dog` not
  `category`); `whole_word=False` substring; longest-match-first; case-insensitive
  match + case-preserving output; `case_sensitive=True`; punctuation adjacency;
  start/end-of-string; an accented/Unicode term; single-pass (no cascade between
  rules); empty rule list = identity; determinism.
- **`tests/test_learning.py` (new — most important, the safety net):** with a fake
  `is_common` predicate — a 1:1 swap of an **uncommon** word becomes a rule
  *and* a vocab term; a 1:1 swap of a **common** word becomes **vocab-only, no
  rule**; inserts / deletes / multi-word `replace` produce **no rule**; format
  guard rejects digits/punctuation/length; dedupe + newest-wins; the derived
  preview matches what is persisted.
- **`tests/test_whisper_engine.py` (extend):** monkeypatch the model to capture
  kwargs; assert the vocabulary hint is passed as `hotwords`, `prefix` is never
  set, and `hotwords=None` when vocabulary is empty.
- **`tests/test_app_engine.py` (extend) — pipeline functional test:** stub
  transcriber returns a known raw mis-transcription, dictionary loaded with a rule;
  drive `App._process()` (stub `wait_all_released`→True, monkeypatch `paste_text`)
  and assert **both** the pasted text and `history.items()[0]` equal the
  **corrected** string (correction happens once, before history, and flows to the
  paste).
- **`tests/test_correct_learn_e2e.py` (new) — the feature's real behavior end to
  end:** simulate a Save from the editor (original + edited strings) → assert
  `dictionary.json` gains the expected rule/vocab → run a *subsequent*
  `App._process()` with a stub transcriber emitting the wrong word → assert the
  paste is corrected. This exercises capture → learn → persist → apply without a
  GUI.
- **`flow/correction_window.py`** — import check + manual run (GUI, like
  `settings_window`/`menubar`).
- **Tier 3 (LLM)** — out of scope here; its tests live in the companion spec and
  are gated on the spike.

## Phasing

1. **Tier B + the capture/learn loop (this issue's core).** `dictionary.py`,
   `corrector.py`, `learning.py`, `correction_window.py`, app/menubar/settings
   wiring, `dictionary.json(.example)`, the management UI, the `[correct]` shortcut.
   Fully deterministic, fully tested. Ships the headline value.
2. **Tier A — vocabulary biasing via `hotwords`.** Engine pass-through + the
   per-call hint; learned targets feed it. Best-effort; gated behind measuring its
   real effect (Open questions). No effect on Tier-B determinism.
3. **Tier 3 — local-LLM contextual fix (future).** Per the companion spec: a
   feasibility spike first (Apple Foundation Models / llama.cpp), then a guarded
   cleanup pass behind a deterministic validator, off by default. Handles the
   common-word homophones Tier B deliberately refuses.

(Tiers 1 and 2 here are small and land together; the numbering matches the
companion specs' tier names, not the build order.)

## Files touched

- `flow/dictionary.py` — new (load/validate/save `dictionary.json`).
- `flow/corrector.py` — new (`TextCorrector`, deterministic Tier-B engine).
- `flow/learning.py` — new (`derive` — diff → safe rules + vocab, the gate logic).
- `flow/correction_window.py` — new (the popup editor NSWindow).
- `flow/engines/whisper.py` — `hotwords` pass-through (never `prefix`).
- `flow/engines/__init__.py` — thread the vocabulary hint into `make_transcriber`.
- `flow/app.py` — load dictionary; build corrector + correction hotkey; apply
  `corrector.correct()` in `_process()`; `_on_correct` + `learn()`.
- `flow/config.py` — `correct_keys` + `[correct]` table.
- `flow/paths.py` — `DICTIONARY_PATH`.
- `flow/menubar.py` — "Correct last dictation…", "Learned words…" (+ disable/delete,
  reset), "Open dictionary file…".
- `flow/settings_window.py` — record a third (correct) shortcut.
- `main.py` — load dictionary at startup, log summary, degrade on error.
- `dictionary.json.example` — new, documented.
- `tests/test_dictionary.py`, `tests/test_corrector.py`, `tests/test_learning.py`,
  `tests/test_correct_learn_e2e.py` — new; `tests/test_whisper_engine.py`,
  `tests/test_app_engine.py` — extended.
- `README.md` / `GETTING_STARTED.md` — document the correction shortcut, the
  dictionary file, learned-rule management, and the local-only promise.

---

## Research basis

The engine tiers (A/B/3) inherit the two prior specs' adversarially-verified
research. The **novel** parts below — the correct-and-learn capture UX and the safe
auto-learn pipeline — were verified by a fresh two-agent web pass on 2026-06-25.

### Capture-and-learn UX (prior art)

- **This combined gesture is a market gap.** Across Wispr Flow, superwhisper,
  MacWhisper, Apple Dictation, macOS Voice Control, Dragon, and Talon, a *one-off
  correction* and the *persistent teaching plane* are almost always separate. Only
  **Dragon** (deeply) and **Wispr Flow** (narrowly) learn *from* the correction
  itself. No tool binds a single shortcut that edits the **last result** *and*
  teaches a rule — a viable differentiator.
- **Dragon** is the canonical fix-and-learn loop: "Correct That" / a correction
  hotkey targets the last utterance; the language model adapts **immediately**, the
  acoustic model in **batch**, and corrected words are auto-added to the active
  vocabulary **by default** — no per-correction approval. Crucial caveat: learning
  happens only if you **Correct**, not if you select-and-retype. (Supports
  *immediate* learning from an explicit correction, with no dialog.)
- **Wispr Flow** is the modern *silent* learn-from-edit precedent: opt-in,
  **proper-nouns only** (never common words), each auto-added item **✨-flagged**
  and reversible, no approval step. (Supports the proper-noun bias + reversibility.)
- **Vocabulary vs. Replacements split is near-universal** (Wispr, superwhisper,
  MacWhisper, Voice Control, Dragon, Talon) — confirms the Tier-A/Tier-B model.

### Safe auto-learning (no-confirmation pipeline)

- **Diff at the word-token level**, not characters: `difflib.SequenceMatcher` over
  word lists, `get_opcodes()`; a `replace` opcode is the candidate swap. Character
  diffing fragments one word swap into many edits.
- **Only 1-word→1-word `replace` is safe** to auto-convert to a rule. `insert` /
  `delete` have no stable wrong→right anchor; many→1 and 1→many and multi-word are
  rephrasings/expansions. Disable `autojunk` for long inputs.
- **The dominant danger is silent corruption** — the *Clbuttic* mistake
  ("classic"→"clbuttic"), the *Scunthorpe* substring problem, and the *Cupertino*
  effect (a valid word silently swapped, reaching the NYT). The decisive guard for
  a deterministic engine: **never auto-learn a rule whose `wrong` side is a common
  word.** This is why v1 routes common-word homophones to vocabulary biasing / the
  future LLM tier instead of a fixed rule.
- **Whole-word matching with raw-string Unicode `\b`** (`r"\bword\b"`; a non-raw
  `\b` is the backspace char and never matches). **Apply all rules in one pass**
  via a combined alternation + a function `repl`, never sequentially (avoids
  cascade/order/loop bugs). **Case-preserving** replacement via the `repl`
  function.
- **Reversibility is mandatory** for silent learning: a visible, editable list with
  per-rule disable/delete, auto-learned items flagged distinctly, and a global
  reset — "users forgive an AI that explains its mistakes; they cannot forgive one
  that fails silently."
- **Phonetic similarity** (Double Metaphone) is a *useful secondary* signal but is
  **not** the primary gate here: it would wrongly reject legitimate non-phonetic
  mishearings, and the common-word test already covers the corruption risk. Note
  for any future use: `jellyfish` does **not** provide Double Metaphone — use the
  `Metaphone` / `phonetics` PyPI packages.

### Open questions (resolve during build, by measurement)

- The concrete **common-word** data source and threshold (`wordfreq` vs a bundled
  top-N list; where to draw the "uncommon" line).
- How many **vocabulary** terms `hotwords` can take before biasing degrades or
  truncates (read the pinned `transcribe.py`; measure on real dictations).
- Whether to expose a repetition-threshold dial (learn only after a correction
  recurs) for users who want even more conservative learning than immediate.

### Sources

Capture-and-learn UX:
- Wispr Flow Dictionary / auto-add: https://docs.wisprflow.ai/articles/4052411709-teach-flow-your-words-with-the-dictionary · https://docs.wisprflow.ai/articles/2772472373-what-is-flow
- superwhisper vocabulary & replacements: https://superwhisper.com/docs/get-started/interface-vocabulary
- MacWhisper find & replace: https://macwhisper.helpscoutdocs.com/article/37-find-and-replace-in-transcriptions
- Apple Dictation / Voice Control: https://support.apple.com/guide/mac-help/use-dictation-mh40584/mac · https://support.apple.com/guide/mac-help/use-voice-control-commands-mh40719/mac · https://support.apple.com/guide/mac-help/use-a-custom-vocabulary-mchl3eb7b79a/mac
- Dragon correction & learning: https://www.nuance.com/products/help/dragon/dragon-for-pc/enx/professionalgroup/main/Content/Correction/about_correction.htm · https://dragonspeechtips.com/should-i-correct-or-select-when-editing-dragon-professional-dictated-mistakes/
- Talon recognition/replacements: https://talon.wiki/Resource%20Hub/Speech%20Recognition/improving_recognition_accuracy/

Safe auto-learning / diff / over-correction:
- difflib: https://docs.python.org/3/library/difflib.html · https://czarrar.github.io/python-diff/
- autocorrect / edit distance: https://norvig.com/spell-correct.html · https://www.datablist.com/learn/data-cleaning/fuzzy-matching-levenshtein-distance
- Clbuttic / Scunthorpe / Cupertino: https://www.computerhope.com/jargon/c/clbuttic.htm · https://en.wikipedia.org/wiki/Scunthorpe_problem · https://en.wikipedia.org/wiki/Cupertino_effect
- whole-word / case-preserving regex: https://www.regular-expressions.info/wordboundaries.html · https://developmentality.wordpress.com/2011/09/22/python-gotcha-word-boundaries-in-regular-expressions/ · https://devblogs.microsoft.com/visualstudio/keep-your-casing-with-case-preserving-find-and-replace/
- Double Metaphone packages: https://pypi.org/project/Metaphone/ · https://github.com/oubiwann/metaphone
- review/undo UX: https://www.nngroup.com/articles/user-control-and-freedom/ · https://blog.logrocket.com/ux-design/ux-reversible-actions-framework/

Engine tiers (inherited): see the two 2026-06-23 companion specs' Research basis
sections for faster-whisper `hotwords`/`prefix` (verified against 1.2.1), the
deterministic-replacement library survey (FlashText overlap bug, pyahocorasick),
and the local-LLM contextual-correction findings (superwhisper, Apple FM, the
small-model caveat).
