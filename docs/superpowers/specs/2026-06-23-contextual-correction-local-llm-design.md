# Contextual correction via a local LLM — design

**Date:** 2026-06-23
**Status:** Draft — research complete, **BLOCKED on a feasibility spike** (see [Phase 0](#phase-0--the-gating-spike-do-this-first)).
**Issue:** #1 — "explore how to build deterministic word replacement and custom vocabulary"
**Extends:** [`2026-06-23-word-replacement-and-custom-vocabulary-design.md`](./2026-06-23-word-replacement-and-custom-vocabulary-design.md)
(the deterministic-replacement + decode-time-biasing design). This document adds
the third tier that the first two cannot provide.

> Built from a second multi-agent, adversarially-verified online research pass
> (per ground rule #3), **not** from memory. Verified findings, the critical
> caveat, and sources are in [Research basis](#research-basis). Machine facts
> below were read from the dev machine, not assumed.

## Why the first spec is not enough

The companion spec gives two tiers — **decode-time biasing** (`hotwords`) and
**deterministic find-and-replace**. Both are real wins, but they share a hard
ceiling: **neither can resolve a *contextual* homophone**, because the right
answer depends on the meaning of the sentence, not on sound or on a fixed lookup.

The maintainer's own examples are the proof:

| You say | Whisper writes | Fixable by tiers 1–2? |
| --- | --- | --- |
| "push it to **GitHub**" | "push it to **guitar**" | ✅ deterministic `guitar→GitHub` — *only because you never say "guitar"* |
| "I asked **Claude** to write the code" | "I asked **cloud** to write the code" | ❌ **no** — "cloud" is a word you really use |
| "I deployed it to the **cloud**" | "I deployed it to the **cloud**" | ❌ must stay "cloud" |

A blind `cloud→Claude` rule corrupts the third sentence; acoustic biasing only
nudges by sound and can't reliably pick Claude-vs-cloud from meaning. The only
thing that separates sentences 2 and 3 is **context** — which is a language-model
job. This tier is what actually fixes the case the maintainer cares about.

## The answer (one paragraph)

Add a third, optional stage: a **small local LLM "cleanup" pass** that reads the
raw transcript plus the user's custom vocabulary and fixes contextual word-choice
errors (homophones, named entities) **without paraphrasing, reordering, or adding
content**, fully on-device. This is a **shipping pattern, not speculation** —
superwhisper does exactly this through local `llama.cpp` on Apple Silicon. The
published technique reaches ~30% relative named-entity WER reduction by injecting
the glossary as context while holding the rest of the text unchanged. The honest
risk (below) is that those gains are only proven on *large* models; so this tier
is **gated behind an empirical spike** and **wrapped in a deterministic safety
net** so it can never silently corrupt a paste.

## The honest risk — read this before building

This is the load-bearing caveat from the research, verified 3-0:

- **No published benchmark shows the big contextual-correction gains with a small
  (1–3B) local model.** Every sizeable result (~30–35% relative WER/NE-WER
  reduction) used GPT-4o / GPT-4o-mini / Claude 3.5 Sonnet / Llama-3.1-70B.
- **Independent small-model evidence is discouraging:** an 8B-class local
  reproduction gained only ~1 WER point and **hallucinated in ~25% of cases**,
  over-correcting proper names; a paper testing LLaMA-3.2-1B found GER gains "much
  weaker" than 8B and concluded "the parameter size of the LLM is critical."
- **The dominant failure mode is hallucination / over-correction** that corrupts
  an *already-correct* transcript. Because TRD Speak pastes at the cursor, a bad
  edit lands directly in the user's document.

Conclusion: the *architecture* is sound and proven; the open question is whether a
**small enough model to run locally** can do contextual correction **well enough**
on faster-whisper `base.en` output. That must be answered **empirically, on the
maintainer's real cases**, before this tier is built — hence Phase 0.

## Target machine (read from the dev box, 2026-06-23)

- **Apple M1, macOS 26.5, 8 GB RAM.** `FoundationModels.framework` is present.
- Implication: **Apple's on-device ~3B Foundation Model is the ideal runtime
  *here*** — reachable from Python via Apple's official `apple-fm-sdk`,
  ANE-accelerated, **zero extra model download**, and **OS-memory-managed**
  (critical on 8 GB, where bundling a separate 2 GB+ llama.cpp model alongside
  faster-whisper is tight). It requires Apple Intelligence to be enabled (the
  user's choice — we never enable it for them).
- For **distribution** to other users (Intel Macs, pre-macOS-26), Apple FM is
  unavailable, so the portable fallback is a bundled small model via
  `llama.cpp`/`llama-cpp-python` or MLX/`mlx-lm`. The design supports both behind
  one interface.

## Goals

- Fix **contextual** homophone / named-entity errors (Claude/cloud, GitHub/guitar
  in context) that tiers 1–2 cannot, using the user's vocabulary as context.
- **Fully local** — no cloud, no accounts (preserve the app's core promise).
- **Never corrupt output:** a failed/hallucinated correction must fall back to the
  raw transcript. The safety property is deterministic and testable.
- **Optional and off by default** until the spike proves it earns its place.
- **Stay within the push-to-talk latency budget** (~1–2 s after key release), or
  degrade gracefully (see latency strategy).

## Non-goals

- **No cloud LLM fallback in v1.** (Possible later as an explicit opt-in for users
  who'll trade the local promise for quality — but not the default, and not now.)
- **No free-form rewriting / summarizing / "make it formal" modes.** This tier
  fixes *wrong words only*. Formatting/tone transforms are a separate feature.
- **No reliance on Apple FM as the only path** — it is gated to macOS 26+/Apple
  Silicon/Apple-Intelligence; the app must still build for others.
- **No blocking the app on a slow/failed model** — fallback is mandatory.

## Decisions (from research + brainstorming)

| Decision | Choice | Why |
| --- | --- | --- |
| Add a contextual tier? | Yes — a local LLM cleanup pass (Tier 3). | Only thing that fixes contextual homophones (Claude/cloud). |
| Proven? | Yes, in production (superwhisper, local llama.cpp). | De-risks the architecture. |
| Build it now? | **No — spike first.** | Small-model quality on `base.en` is unproven; could be net-negative. |
| Default state | **Off**, opt-in. | Probabilistic; user must choose it knowingly. |
| Safety model | Deterministic validator gates every LLM output; reject → raw transcript. | Hallucination must never reach the cursor. |
| Runtime (this Mac) | Apple FM via `apple-fm-sdk`. | Zero download, ANE, OS-managed memory — fits 8 GB M1. |
| Runtime (portable) | Bundled `llama.cpp`/MLX small model behind the same interface. | Apple FM excludes Intel/pre-26. |
| Prompt | Constrained: correct only wrong/low-confidence words, stay phonetically close, keep everything else, glossary-injected, output only corrected text. | Evidence-backed anti-over-correction guardrails. |
| Vocabulary source | The same `dictionary.json` `vocabulary` list from spec 1. | One place to name your terms; feeds biasing *and* the LLM glossary. |
| Latency | Warm-load the model; size-gate; measure on M1; consider async re-paste. | Generation, not prefill, dominates; 30 tok/s ⇒ a sentence can be ~1–2 s. |

## Architecture

The cleanup pass slots in as **Tier 3**, after the corrector from spec 1 and
before history/paste — so the *corrected* string flows through history, re-paste,
and paste uniformly (same placement rationale as spec 1).

```
audio ─► transcriber.transcribe(audio, hotwords=vocab_hint)   # Tier 1  (flow/engines/whisper.py)
      ─► text = corrector.correct(text)                        # Tier 2  (flow/corrector.py)
      ─► text = cleaner.clean(text, vocabulary) if enabled     # Tier 3  (flow/cleaner.py)  ← NEW
            │   └─► local LLM (Apple FM | llama.cpp) under a constrained prompt
            │   └─► DETERMINISTIC validator: accept only small, word-level edits
            │         else discard → keep pre-LLM text  (fail-safe)
      ─► history.add(text)
      ─► paste_text(text + " ")
```

### Components

1. **`flow/cleaner.py` — new.** The Tier-3 engine, behind a tiny interface so the
   runtime is swappable and the whole thing is testable with a fake backend.
   - `class ContextualCleaner` with a pluggable `backend` (an object exposing
     `generate(prompt) -> str`).
   - `clean(text: str, vocabulary: list[str]) -> str`:
     1. Build the **constrained prompt** (below), injecting the user's
        `vocabulary` as the glossary and, if available, Whisper's **low-confidence
        words** (faster-whisper exposes word-level probabilities with
        `word_timestamps=True`) so the model is told *which* words to scrutinise —
        the arXiv 2407.21414 technique.
     2. Call `backend.generate(...)` with a short output cap (emit only the
        corrected transcript, no reasoning, to bound latency).
     3. Run the **deterministic validator** (next component). Accept or reject.
   - Pure Python except the backend; unit-testable with a scripted fake backend
     (no model needed in CI).

2. **`flow/cleaner_guard.py` (or a function in `cleaner.py`) — the deterministic
   safety net.** This is the spine that makes a probabilistic component safe, and
   it is itself fully deterministic and unit-tested:
   - **Accept the LLM output only if all hold:**
     - length change ≤ a small bound (e.g. ±15% chars / ±N tokens);
     - token-level edit distance from the input ≤ a small bound (only a few
       word-level substitutions, no wholesale rewrite);
     - no added sentence terminators / newlines that weren't in the input
       (blocks "continue writing" hallucinations);
     - every *changed* source token is either phonetically close to its
       replacement **or** the replacement is in the user's vocabulary (reuses the
       phonetic idea — Double Metaphone — from spec 1's future tier; this bounds
       edits to plausible ASR fixes, not creative ones).
   - **On reject → return the pre-LLM text unchanged.** The feature's worst case
     is a no-op; it can never make a transcript worse.

3. **`flow/cleaner_backends/` — runtime adapters behind one interface.**
   - `apple_fm.py` — calls Apple's on-device model via `apple-fm-sdk`
     (`@fm.generable` / `fm.guide()` for structured/bounded output). Used when
     macOS ≥ 26, Apple Silicon, Apple Intelligence enabled, and the framework +
     model are available (all detected, never enabled by us).
   - `llama_cpp.py` — a bundled quantized small model (e.g. Llama 3.2 3B / Qwen2.5
     3B Q4) via `llama-cpp-python` on Metal, kept warm. The portable fallback.
   - `null.py` — disabled / unavailable → `clean()` is identity. Default.
   - A `select_backend(config)` picks the best available; failure to load any
     backend silently degrades to `null` (logged once).

4. **`flow/engines/whisper.py` — surface word confidences (optional).** To enable
   the low-confidence-word prompt technique, allow `transcribe()` to run with
   `word_timestamps=True` and return per-word probabilities when Tier 3 is on.
   Off by default (it has a small cost); only enabled when the cleaner is enabled.

5. **`flow/app.py` — wiring.** Build `self.cleaner` in `__init__`; in `_process()`
   apply `text = self.cleaner.clean(text, self.dictionary.vocabulary)` after Tier
   2 and before `history.add`. Guard with the enabled flag; never raise into the
   dictation path (any cleaner exception is caught → keep pre-LLM text).

6. **`flow/config.py` / `dictionary.json` — settings.** A `[cleanup]` section:
   `enabled` (default `false`), `backend` (`"auto"|"apple_fm"|"llama_cpp"|"off"`),
   `max_seconds` (latency budget; skip/abort beyond it), and the validator bounds.
   Reuses the existing `vocabulary` list as the glossary.

### The constrained prompt (anti-over-correction)

Evidence-backed guardrails, assembled into one short instruction:

- **Role:** "Fix only misrecognised words in this dictation transcript."
- **Glossary injection:** "The speaker uses these terms: {vocabulary}. Prefer them
  when a word was likely misheard as a common word (e.g. 'cloud'→'Claude',
  'guitar'→'GitHub') *but only when the sentence is about that topic*."
- **Hard constraints:** "Change **only** words that are clearly wrong. Keep every
  other word, punctuation, and formatting **exactly**. Do **not** rephrase,
  reorder, add, or remove content. Corrections must sound like the original word.
  If nothing is clearly wrong, return the text unchanged."
- **Low-confidence focus (when available):** pass `{text, low_confidence_words}`
  and "correct only words from low_confidence_words."
- **Output:** the corrected transcript only — no explanations (keeps generation
  short → latency bounded).

The validator (component 2) enforces these constraints *mechanically* regardless
of whether the small model actually obeyed them — which is the whole point, since
small models are the ones likely to disobey.

### Latency strategy

- **Warm-load** the model at startup or first dictation; never pay cold-load per
  use.
- **Generation dominates** (prefill of 100–300 tokens is sub-second on
  Metal/ANE). Apple's ~3B anchors ~30 tok/s, so a one-sentence dictation (~70
  tokens out) is roughly ~1–2 s — **borderline**; this must be **measured on the
  M1** in the spike, not assumed.
- **Mitigations, chosen after measuring:** size-gate (skip very long transcripts
  or those with no low-confidence words / no vocabulary hits); cap output tokens;
  and — if blocking is too slow — **paste the raw transcript immediately and run
  cleanup asynchronously, offering the corrected version via the existing
  re-paste hotkey / Recent Dictations menu.** This reuses shipped machinery and
  keeps the hot path instant. (Async-vs-blocking is a UX decision to settle with
  the measured numbers.)

### Considered and rejected

- **Cloud LLM (what Wispr Flow actually uses).** Highest quality, but breaks the
  "no cloud, no accounts" promise that defines the app. Rejected as default;
  possible explicit opt-in later.
- **Apple FM as the only backend.** Cleanest on this Mac, but excludes Intel and
  pre-macOS-26 users. Kept as the preferred *available* backend, not the only one.
- **Blind deterministic `cloud→Claude` rule.** Corrupts legitimate "cloud" usage —
  the exact problem this tier exists to solve.
- **Phonetic fuzzy replacement (spec 1's future tier) as the contextual fix.** It
  maps *sound*, not *meaning*; it can't tell Claude-the-AI from cloud-the-infra.
  It survives here only as a *constraint* inside the validator, not as the fix.
- **Building before the spike.** The single biggest risk (small-model quality) is
  cheaply testable first; building blind risks a feature that's net-negative.

## Phase 0 — the gating spike (do this first)

A tiny, throwaway experiment to answer "can a small *local* model actually fix the
maintainer's cases without wrecking other text?" **before** any feature work.

**Protocol (on the dev M1, macOS 26.5):**
1. Assemble ~15–25 short test sentences covering the real cases: Claude/cloud
   (both meanings), GitHub/guitar (both meanings), a few other personal terms, and
   several sentences with **no** error (to measure over-correction).
2. Get raw transcripts: either run them through the app's actual
   `WhisperTranscriber` (`base.en`) from short recordings, or start from realistic
   `base.en`-style mis-transcriptions.
3. Run the **constrained prompt** against the **Apple on-device model** via
   `apple-fm-sdk` (zero download; needs Apple Intelligence enabled), and — if
   feasible — one bundled `llama.cpp` 3B for the portable-path data point.
4. Apply the deterministic validator to each output.
5. **Measure:** (a) homophone/NE fixes that are correct; (b) **over-corrections**
   (clean sentences damaged) — the number that actually matters; (c) end-to-end
   **latency** per sentence on the M1.

**Decision gate:**
- **Green** (fixes the cases, ~0 over-corrections survive the validator, latency
  acceptable or async-able) → build Tier 3 per this spec.
- **Red** (misses cases, or over-corrects, or too slow) → **do not ship Tier 3
  locally.** Honest fallback: ship tiers 1–2 only, document the contextual
  limitation, and offer an explicit cloud-LLM opt-in as a separate decision.

The spike **modifies the machine** (a `pip install apple-fm-sdk`, possibly a model
download for the llama.cpp data point) and depends on Apple Intelligence being
enabled — so it runs **only with the maintainer's go-ahead**, consistent with the
"don't touch my machine" rule. It is otherwise fully reversible (`pip uninstall`).

## Edge cases

- **Cleaner disabled / no backend / Apple Intelligence off:** `clean()` is
  identity; behaviour is exactly tiers 1–2. This is the default for everyone.
- **LLM hallucinates / over-edits:** validator rejects → raw transcript pasted.
  Logged for tuning. Never reaches the cursor.
- **Empty vocabulary:** the glossary is empty; the pass still runs but has little
  to anchor to — size-gate may skip it. (Tier 3 is most valuable *with* a curated
  vocabulary.)
- **Very long dictation:** size-gate skips or runs async to protect latency.
- **Model slow on a cold cache / first run:** warm-load; if still over budget,
  async path; never block indefinitely.
- **8 GB memory pressure:** prefer Apple FM (OS-managed) on this Mac; if using
  bundled llama.cpp, unload between uses or pick a ≤3B Q4 model and measure RSS
  alongside faster-whisper.
- **Both meanings in one breath** ("I asked Claude to deploy to the cloud"): this
  is the model's job and the spike's hardest test; the validator at least
  guarantees a wrong guess can't also damage the surrounding words.

## Testing

Per ground rule #1, every feature ships an automated functional test.

- **`tests/test_cleaner_guard.py` (new) — the deterministic safety net (most
  important):** a scripted fake backend returns crafted outputs; assert the
  validator **accepts** small word-level fixes and **rejects** (→ raw text):
  length blow-ups, multi-word rewrites, added sentences, and edits that are
  neither phonetically close nor in vocabulary. This is pure logic, fully
  deterministic — the core safety property is locked down without any model.
- **`tests/test_cleaner.py` (new):** with a fake backend, assert prompt
  construction injects vocabulary + low-confidence words, output cap is set, and a
  backend exception degrades to the pre-LLM text.
- **`tests/test_app_engine.py` (extend):** with a fake cleaner that returns a known
  correction, drive `App._process()` and assert the corrected string lands in both
  the paste and `history.items()[0]`; with a fake cleaner that "hallucinates,"
  assert the raw transcript is pasted (guard works end-to-end).
- **Spike report (Phase 0):** the empirical accuracy/over-correction/latency
  numbers on real cases — the functional evidence that gates the build.
- **Backend adapters** (`apple_fm`, `llama_cpp`) are integration-tested behind a
  capability check and skipped in CI when the runtime/model is absent.

## Phasing

0. **Spike (gate).** Empirical viability on the maintainer's cases. Go / no-go.
1. **Tier 3 core (if green).** `cleaner.py` + deterministic guard + Apple FM
   backend + `app.py` wiring + `[cleanup]` config, **off by default**.
2. **Portable backend.** Bundled `llama.cpp`/MLX small model for non-Apple-FM
   machines; packaging (PyInstaller, code-signing, model on disk) — an open
   question to validate (see below).
3. **Latency UX.** Blocking vs async-re-paste, decided on measured numbers.
4. **(Separate, later)** optional cloud-LLM opt-in for users who want max quality
   over the local guarantee.

## Files touched (if the spike is green)

- `flow/cleaner.py` — new (Tier-3 engine + constrained prompt).
- `flow/cleaner_guard.py` — new (deterministic validator).
- `flow/cleaner_backends/{apple_fm,llama_cpp,null}.py` — new (runtime adapters).
- `flow/engines/whisper.py` — optional word-confidence output when Tier 3 is on.
- `flow/app.py` — build + apply the cleaner in `_process()` with fail-safe.
- `flow/config.py` / `dictionary.json(.example)` — `[cleanup]` settings.
- `tests/test_cleaner_guard.py`, `tests/test_cleaner.py` — new; `tests/test_app_engine.py` — extended.
- `requirements*.txt` / `TRDSpeak.spec` — `apple-fm-sdk` (and/or `llama-cpp-python`
  + bundled model) once a backend is chosen.
- `README.md` / `GETTING_STARTED.md` — document the optional cleanup, its local
  models, and the privacy/latency tradeoff.

---

## Research basis

Second research pass: 106 agents, 24 sources, 25 claims adversarially verified
(21 confirmed, 4 killed). Machine facts read directly from the dev box.

### Confirmed (high confidence)

- **On-device LLM cleanup is a shipping pattern.** superwhisper runs local LLMs
  via **llama.cpp on Apple Silicon** (Llama 3.2 3B, Mistral 7B, Phi, …) to
  post-process the transcript — "No internet needed." Also supports Ollama and
  cloud as alternatives.
- **Apple on-device model from Python.** Apple's official `apple-fm-sdk`
  (Apache-2.0, PyPI) exposes the ~3B on-device Foundation Model with
  guided/structured generation — **but** requires macOS 26+, Apple Silicon, Apple
  Intelligence on (excludes Intel / pre-26). The dev Mac (M1, macOS 26.5, FM
  framework present) qualifies if Apple Intelligence is enabled.
- **LLM post-correction genuinely fixes contextual NE/homophone errors:** ~30%
  relative NE WER reduction (32.3%→22.7%) via Double Metaphone + NER + glossary
  injection, *with non-NE text preserved* (non-NE WER held at 7.0%); a separate
  study showed ~35% relative WER reduction via prompt optimisation.
- **Dominant failure = hallucination/over-correction** that corrupts correct text
  — must be guarded; for a paste-at-cursor app this is a correctness hazard, not a
  cosmetic one.
- **Evidence-backed guardrails:** structured low-confidence-word lists, "correct
  only those," phonetic-closeness constraints, constrained CoT to bound the output
  space, "keep everything else unchanged," glossary injection. (Validated on large
  models; small-model obedience unproven — hence the deterministic validator.)
- **Latency:** prefill of 100–300 tokens is sub-second on Apple Silicon GPU/Metal
  (even at 7B); Apple's ~3B anchors ~30 tok/s, ~0.6 ms/token TTFT (iPhone 15 Pro
  figure). Generation, not prefill, dominates — keep outputs short, warm the
  model, measure on the actual Mac.

### The critical caveat (verified 3-0)

- **No published result demonstrates the big gains with a small 1–3B model.** All
  used GPT-4-class or 70B models. The best small-model evidence is discouraging
  (8B: ~1 WER point + ~25% hallucination + proper-noun over-correction; 1B: "much
  weaker," "parameter size is critical"). **Small-model quality on `base.en`
  output is the central unproven risk → Phase 0 spike.**

### Refuted / do-not-rely-on

- "1–3B generation is comfortably sub-second" (1-2) — measure, don't assume.
- A blanket "LLM post-correction does **not** improve fine-tuned Whisper output
  even with context" (0-3, fully refuted) — pessimism didn't survive, but results
  are dataset/config-dependent.

### Open questions (for the spike + Phase 2)

- Can a 1–3B local model actually fix these cases on `base.en` output without
  over-correcting? (No benchmark this small exists — must test.)
- Real measured end-to-end latency of a warm 3B pass on the M1 (Apple FM/ANE vs
  llama.cpp/Metal)?
- PyInstaller bundling mechanics for a quantized model + `llama-cpp-python`/`mlx`
  in a signed `.app` (model size/location, Metal from a frozen app)?
- Open-source faster-whisper dictation projects with a local-LLM cleanup stage
  worth copying (beyond closed-source superwhisper)?

### Sources

- superwhisper local language models: https://superwhisper.com/docs/models/language · https://superwhisper.com/models · https://superwhisper.com/changelog
- Apple FoundationModels Python SDK: https://github.com/apple/python-apple-fm-sdk
- Apple on-device foundation models: https://machinelearning.apple.com/research/introducing-apple-foundation-models · https://machinelearning.apple.com/research/introducing-third-generation-of-apple-foundation-models
- Double Metaphone + glossary NE correction (~30% NE WER): https://arxiv.org/html/2506.10779v1
- EvoPrompt ASR correction (~35% WER): https://arxiv.org/html/2407.16370v1
- Low-confidence-word constrained correction: https://arxiv.org/html/2407.21414
- Hallucination/over-correction + constrained CoT: https://arxiv.org/html/2505.24347v1
- Small-model GER is critical to size (LLaMA-3.2-1B vs 8B): https://arxiv.org/abs/2505.17410
- Independent small/8B local reproduction (modest gains, ~25% hallucination): https://alphacephei.com/nsh/2025/03/
- llama.cpp Apple Silicon throughput: https://github.com/ggml-org/llama.cpp/discussions/4167
