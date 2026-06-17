# Selectable Transcription Engine — Design

**Date:** 2026-06-17
**Status:** Approved (design)

## Goal

Let the user choose between two local transcription engines and switch
between them directly from the menu-bar icon, without restarting the app:

- **faster-whisper** (current) — CTranslate2, CPU, `base.en`/int8. Light
  dependencies. The default.
- **parakeet-mlx** — NVIDIA Parakeet TDT 0.6B via Apple MLX, runs on the M1
  GPU. Heavier dependency and ~1.2 GB resident, but on this machine it is
  faster than `base.en` on short utterances *and* more accurate, with
  punctuation/capitalization out of the box.

### Benchmarks that motivate this (M1, 8 GB, best-of-5, measured 2026-06-17)

| Engine | Short (2.4 s) | Long (19.2 s) | Peak RSS |
| --- | --- | --- | --- |
| faster-whisper `base.en` (current) | 0.28 s | 0.70 s | ~150 MB |
| parakeet-tdt-0.6b (GPU) | 0.20 s | 0.76 s | ~1.2 GB |

Parakeet's first transcription after load is ~5.4 s (Metal kernel
compilation), then settles. This is handled by a warm-up at load time.

## Decisions (confirmed with user)

1. **Packaging:** parakeet is an *optional, lazily-imported* engine. The
   default install stays light (whisper only). The menu always lists both;
   choosing parakeet when it is not installed shows a clear message and
   leaves the current engine active.
2. **Switching:** *load now, unload old.* Switching loads + warms the new
   engine on a worker thread (menu shows a loading state), then frees the
   previous model so whisper and parakeet are never both resident.
3. **Persistence:** the menu choice persists across restarts, stored in a
   separate state file so the hand-edited `config.toml` is never rewritten.

## Architecture

### Engine package

Transcription becomes a small pluggable package (replacing the single
`flow/transcriber.py`):

```
flow/engines/
  __init__.py   # Transcriber ABC, ENGINES registry, make_transcriber()
  whisper.py    # WhisperTranscriber (today's code, moved)
  parakeet.py   # ParakeetTranscriber (new)
```

**`Transcriber` ABC** — the only surface `App` depends on:

| Member | Contract |
| --- | --- |
| `name: str` | Stable key, e.g. `"whisper"`, `"parakeet"`. |
| `label: str` | Human label for the menu, e.g. `"faster-whisper (base.en)"`. |
| `load() -> None` | Idempotent. Instantiates the model and warms it up. |
| `transcribe(audio: np.ndarray) -> str` | 16 kHz mono float32 in; text out. Returns `""` for too-short audio. |
| `unload() -> None` | Drop the model and free memory. Idempotent. |

**Registry + factory.** `ENGINES` maps name → class and carries display
metadata (label, short description) so the menu can be built without
importing the heavy modules. `make_transcriber(name, config)` returns an
unloaded instance. Engine classes are imported lazily inside the factory /
inside `load()` so an absent `parakeet-mlx` never affects whisper users.

### Parakeet engine details

- **Lazy import** of `parakeet_mlx` inside `load()`. `ImportError` is
  translated into a typed `EngineUnavailable` error the caller can handle.
- **Model:** `mlx-community/parakeet-tdt-0.6b-v2`.
- **No ffmpeg.** parakeet-mlx 0.5.2 only loads audio from a file path via
  ffmpeg and exposes no array-input API. We feed the mic's already-decoded
  float32 numpy array by substituting parakeet's module-level audio loader
  with one that returns our pre-set array (transcription is serialized by
  the app state machine, so a stashed-array approach is safe). The audio is
  converted to a float32 `mx.array` (float32 is required by parakeet's mel
  step, which views the FFT output through the input dtype). This couples to
  a library internal; `load()` verifies the patch target exists and raises a
  clear error if a future version removes it.
- **Warm-up.** After instantiation, `load()` runs one transcription on a
  short dummy buffer so the ~5.4 s Metal cold-start happens during the
  existing "Loading model…" phase, not on the user's first dictation.
- **`unload()`** drops the model reference and triggers MLX/GC cleanup.

### App integration

`flow/app.py`:

- Holds the current `Transcriber` (built via `make_transcriber`).
- New app state **`LOADING`** alongside `IDLE`/`RECORDING`/`PROCESSING`.
  While `LOADING`, `_on_activate` refuses to start recording (mirroring the
  existing `PROCESSING` guard), and the menu shows the ⏳ icon.
- New `set_engine(name)`:
  1. Under `_lock`: proceed only if state is `IDLE`; otherwise post a
     notification ("Finish the current dictation first") and return without
     changing anything. On success, set state `LOADING`.
  2. On a worker thread: `make_transcriber(name)` → `load()` (load + warm).
  3. On success: swap the instance in, `unload()` the previous engine,
     persist the choice (state file), update the menu checkmark, return to
     `IDLE`.
  4. On `EngineUnavailable` / any load error: keep the previous engine,
     notify the user, return to `IDLE`. Never leave the app engineless.
- A UI callback (analogous to the existing `on_state`) reports the active
  engine so the menu can refresh its checkmark.

### Config & persistence

- `flow/config.py`: add `engine: str = "whisper"`, validated against the
  registry (unknown value → `ValueError`, consistent with existing
  validation). New `[engine]` section in `config.toml` /
  `config.toml.example`: `name = "whisper"`.
- **State file:** `~/Library/Application Support/LocalFlow/engine`, a single
  line containing the engine name. Written on every successful menu switch.
- **Startup precedence:** state file (if present and valid) → `config.toml`
  → built-in default. An invalid/absent state file is ignored silently and
  falls through to config.

### Menu UI

`flow/menubar.py` gains a **Transcription Engine ▸** submenu, inserted above
the existing Open Log / Quit rows:

```
🎤 Ready — hold ctrl+shift to dictate
─────────────
Transcription Engine ▸   ✓ faster-whisper (base.en)
                           Parakeet — GPU, more accurate
─────────────
Open Log
Quit LocalFlow
```

- Items are built from the `ENGINES` registry (no heavy imports). Checkmark
  marks the active engine.
- New delegate action `selectEngine:` with `representedObject` = engine
  name; calls `App.set_engine` (which does the worker-thread work).
- Items are disabled while state is `LOADING`, `RECORDING`, or `PROCESSING`.
- The submenu coexists with the onboarding/permission rows: it is hidden
  while permissions are missing (same as the rest of the normal-state UI).

## Error handling summary

| Situation | Behavior |
| --- | --- |
| Parakeet not installed | `selectEngine` → notification with install hint; stay on current engine. |
| Parakeet load/warm-up fails | Log, notify, revert to previous engine, return to IDLE. |
| Switch requested mid-dictation | Notification ("finish current dictation first"); no change. |
| Library internal (audio loader) missing | `load()` raises a clear error; treated as load failure → revert. |
| Invalid state file or config value | State file ignored; bad config value raises at startup (existing pattern). |

## Setup & docs

- `requirements.txt` stays whisper-only. New `requirements-parakeet.txt`
  lists the parakeet stack (`parakeet-mlx`).
- `setup.sh` gains an optional `--parakeet` flag that additionally installs
  `requirements-parakeet.txt`.
- README + GETTING_STARTED: short "Choosing an engine" subsection covering
  the menu, the optional install, and the speed/accuracy/RAM trade-off.

## Out of scope (YAGNI)

- More than two engines / a generic plugin discovery mechanism.
- Per-engine model pickers in the UI (model stays config-driven).
- Streaming/chunked transcription (separate future feature).
- Auto-installing parakeet from the menu.

## Testing

- **Engine package, pure logic:** factory returns the right class; registry
  metadata; config/state-file precedence resolution; unknown-name handling.
  Unit-testable without loading models.
- **WhisperTranscriber:** behavior preserved (too-short audio → `""`); a
  real transcription of a fixture clip asserts non-empty text. (Existing
  behavior, now under the ABC.)
- **ParakeetTranscriber:** guarded/skipped when `parakeet-mlx` is absent;
  when present, transcribes a fixture clip to expected text and `unload()`
  releases the reference. The no-ffmpeg array path is exercised directly.
- **set_engine state machine:** switching from IDLE succeeds and swaps;
  switching while PROCESSING is refused; a load failure reverts to the
  previous engine. Driven with a fake/stub engine so no model is loaded.
- **Manual:** menu switch whisper↔parakeet, checkmark updates, first
  post-switch dictation is warm (not 5 s), choice survives restart, parakeet
  refusal message when not installed.
```
