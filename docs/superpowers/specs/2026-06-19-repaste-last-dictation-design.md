# Re-paste last dictation hotkey — design

**Date:** 2026-06-19
**Status:** Implemented.
**Issue:** #2 — "add a shortcut to replace the last insertion"

> **Shipped-default note.** The defaults were later changed, at the
> maintainer's request, to `ctrl+shift` (dictate) / `cmd+ctrl` (re-paste) so the
> two combos don't overlap on the maintainer's setup. The design below describes
> the original `cmd+ctrl+shift` re-paste default and its screenshot-prefix
> rationale; the clean-tap mechanism it specifies is unchanged and is exactly
> what keeps the two-key `cmd+ctrl` default from firing on combos like
> `Cmd+Ctrl+Space`.

## Problem

When dictating with LocalFlow, the transcribed text is pasted into whatever app
has focus at release time. If focus was on the wrong window, the text lands
somewhere useless. The Recent Dictations menu (see
`2026-06-19-dictation-history-design.md`) already lets you recover a lost
dictation by clicking it to copy it to the clipboard — but that is a
mouse-driven, menu-then-copy-then-paste sequence.

This feature gives a one-press keyboard recovery: a global hotkey
(`Cmd+Ctrl+Shift` by default) that **pastes the most recent dictation directly
into the currently focused window**. Switch to the right window, press the
combo, and the last dictation lands where it should have.

## Goals

- A global hotkey that inserts the most recent dictation into the focused
  window (synthesized Cmd+V), without touching the menu.
- The combo is configurable, defaulting to `cmd+ctrl+shift`.
- It coexists with macOS screenshot shortcuts (`Cmd+Ctrl+Shift+3/4/5`): taking a
  screenshot must NOT fire a stray paste.
- It never interferes with an in-flight dictation.

## Non-goals

- No stepping back through history (always the single most recent dictation).
  "Last insertion" is singular; the menu already exposes the full list.
- No "delete the previously inserted wrong text" — we re-insert, we do not hunt
  down and remove an earlier mis-paste (the app cannot know where that went).
- No way to disable the re-paste hotkey via config in this version (it is always
  on; the combo is configurable). A disable flag is a trivial future addition.
- No new persistence: re-paste reads the existing in-memory `History`.
- No change to the menu's existing copy-to-clipboard behavior.

## Decisions (from brainstorming)

| Decision | Choice |
| --- | --- |
| Action | Auto-paste into the focused window (synthesize Cmd+V), not copy-only. |
| Which dictation | The most recent (`history.items()[0]`). |
| Combo | Configurable; default `cmd+ctrl+shift`. |
| Trigger timing | Fire once on a **clean release** of the combo (see below). |
| Screenshot conflict | Avoided by clean-tap detection — a contaminating keypress cancels the trigger. |
| Trailing space | Appended (`last + " "`), matching the dictation paste flow. |
| Concurrency | Runs only when the app is `IDLE`; otherwise notified-and-skipped. |
| Empty history | No-op with a "No recent dictation to re-paste" notification. |
| Listener architecture | A second, independent `HotkeyListener` instance (its own event tap). |

## The clean-tap rule (why this is the crux)

`Cmd+Ctrl+Shift` is the **prefix of the macOS screenshot shortcuts**
(`Cmd+Ctrl+Shift+3` full screen to clipboard, `+4` area to clipboard, `+5`
capture UI). A naive "fire the moment all three modifiers are held" would trigger
a paste every time the user starts a screenshot.

The trigger therefore fires **on release**, and **only if the hold was clean** —
i.e. between the moment the three modifiers were all down and the moment one was
released, **no other key was pressed**. Taking a screenshot presses `3`/`4`/`5`
during the hold, which "contaminates" the tap, so re-paste does not fire. A bare
press-and-release of `Cmd+Ctrl+Shift` (pressing nothing else) is the only thing
that triggers it.

Firing on release also guarantees the synthesized Cmd+V is clean: the combo
modifiers are physically up, so the paste is a plain `Cmd+V`, not
`Cmd+Ctrl+Shift+V`.

## Architecture

A **second `HotkeyListener` instance** owns its own CGEventTap for the re-paste
combo. The existing push-to-talk listener is left byte-for-byte unchanged — its
callback is delicate (macOS 26 main-thread asserts, App Nap, missed-release
self-healing), and a second independent tap keeps that critical path untouched
and gives the two combos fully isolated held-key state.

`HotkeyListener` gains an optional **tap mode**. Today it is a *hold* listener:
`on_activate` fires when the combo is fully held, `on_deactivate` when a key is
released. Tap mode adds an `on_trigger` callback that fires once on a clean
release. The two modes share the same tap/run-loop/watchdog/heartbeat machinery;
only the press/release bookkeeping differs, and the hold-mode path is unchanged.

(Considered and rejected: multiplexing both combos onto one tap. It would mean
rewriting the fragile single-combo callback the core feature depends on, and
`wait_all_released` semantics would blur across combos. Two taps are cheap — both
listen-only — and isolation is worth far more than saving one tap.)

### Components

1. **`flow/config.py` — re-paste combo config.**
   - `Config.repaste_keys: list[str]` defaulting to `["cmd", "ctrl", "shift"]`.
   - Parse a `[repaste]` table with a `keys` list, validated exactly like
     `hotkey.keys` (a list of 1–3 non-empty strings, lower-cased). The validation
     logic is identical to the existing `[hotkey] keys` block; factor the shared
     check into a small helper rather than duplicating it.

2. **`flow/hotkey.py` — tap mode.**
   - New signature:
     `HotkeyListener(keys, on_activate=None, on_deactivate=None, on_trigger=None)`.
   - If `on_trigger` is provided → **tap mode**; otherwise → **hold mode**
     (current behavior, `on_activate`/`on_deactivate` required as today).
   - Tap-mode bookkeeping, all under the existing `_cond` lock:
     - When the combo becomes fully held (same condition as hold-mode activate),
       set `_armed = True`, `_contaminated = False`.
     - In `_tap_callback`, a `kCGEventKeyDown` for a keycode **not** in the
       combo, or a `flagsChanged` that adds a modifier not in the combo, while
       `_armed` → set `_contaminated = True`. (These are the events the current
       callback returns early on; tap mode must inspect them. Hold mode keeps
       returning early — the new inspection is gated on `_armed`/tap mode.)
     - On release of any combo key while `_armed`: clear `_armed`; if not
       `_contaminated`, call `on_trigger()` exactly once.
   - `wait_all_released`, `ensure_enabled`, `take_event_count`, `start`, `stop`
     are unchanged and serve both modes.

3. **`flow/app.py` — re-paste wiring.**
   - `App.__init__` builds
     `self.repaste_hotkey = HotkeyListener(config.repaste_keys, on_trigger=self._on_repaste)`.
   - `_on_repaste()` runs on the run-loop thread, so it MUST return immediately:
     it only spawns a worker thread running `_do_repaste` (mirrors how
     `_on_activate` offloads blocking work).
   - `_do_repaste()` on the worker:
     1. `self.repaste_hotkey.wait_all_released()` — ensure the combo is fully up
        so Cmd+V is clean (and the user has finished the gesture).
     2. Under `self._lock`: if `self._state != IDLE`, notify "busy, finish the
        current dictation first" and return; else set `self._state = PROCESSING`
        (so a dictation cannot start mid-paste and race the clipboard).
     3. `items = self.history.items()`. If empty → restore `IDLE`, notify "No
        recent dictation to re-paste", return.
     4. If `not self.can_paste()` → restore `IDLE`, log the Accessibility-missing
        message, return.
     5. `paste_text(items[0] + " ", restore_delay=self.config.paste_restore_delay)`;
        log "Re-pasted: …".
     6. `finally`: restore `self._state = IDLE` and `_notify("ready")`.
   - `start()` calls `self.repaste_hotkey.start()` after `self.hotkey.start()`,
     wrapped so a failure there is logged but does NOT prevent push-to-talk from
     working (both need the same Input Monitoring grant, so in practice they
     succeed or fail together).
   - `shutdown()` also stops `self.repaste_hotkey`.

4. **`flow/menubar.py` — watchdog coverage.**
   - The 2 s poll's tap watchdog also calls `logic.repaste_hotkey.ensure_enabled()`
     (re-asserting it if macOS disabled it), and folds its
     `take_event_count()` into the ~30 s liveness heartbeat log.
   - No new menu rows — the feature is keyboard-only. The combo is reflected in
     docs, not the menu, in this version.

### Data flow

```
focus wrong window after a dictation
        │
press Cmd+Ctrl+Shift, release cleanly (no other key)
        │
repaste tap fires on_trigger ──► _on_repaste (returns immediately)
        │
worker _do_repaste: wait_all_released → state IDLE? → history.items()[0]
        │
can_paste? ──► paste_text(last + " ")  ──► text lands in the focused window

Cmd+Ctrl+Shift+4 (screenshot): the "4" contaminates the hold → on_trigger never fires
```

## Edge cases

- **Screenshot shortcuts** (`Cmd+Ctrl+Shift+3/4/5`): the digit keypress
  contaminates the hold; re-paste does not fire. This is the primary reason for
  the clean-tap rule.
- **Empty history**: notify "No recent dictation to re-paste"; nothing pasted.
- **Accessibility (post) permission missing**: same guard as the dictation flow
  (`can_paste()`); logged, not pasted.
- **Mid-dictation**: if the app is `RECORDING`/`PROCESSING`/`LOADING` when the
  combo is released, re-paste is skipped with a notification — it never races an
  in-flight dictation's clipboard save/restore.
- **Re-paste while the combo is still held**: `wait_all_released()` blocks the
  worker until the modifiers are up, so Cmd+V is never combined with them.
- **Combo overlap with push-to-talk**: the default combos (`ctrl+alt` and
  `cmd+ctrl+shift`) share only `ctrl`, and each listener tracks its own held
  state on its own tap, so neither combo's matching is affected by the other. A
  user who configures overlapping combos owns that choice (documented).
- **Repeated presses**: each clean tap re-pastes the same most-recent dictation
  again. There is no history cursor.
- **Tap disabled by the system**: the menubar watchdog re-enables the re-paste
  tap exactly as it already does for the push-to-talk tap.

## Testing

- **`tests/test_hotkey.py` (extend):** drive `_tap_callback` directly (as the
  existing tests do, monkeypatching `CGEventGetIntegerValueField`) for a
  tap-mode listener:
  - holding all combo keys then releasing one, with no other key pressed, fires
    `on_trigger` exactly once;
  - pressing a non-combo key (e.g. keycode for `4`) between full-hold and release
    suppresses `on_trigger` (the screenshot case);
  - `on_trigger` fires at most once per hold (a second release does not re-fire);
  - hold-mode listeners are unaffected (existing tests still pass; add an
    assertion that `on_trigger`-less construction keeps `on_activate`/
    `on_deactivate` behavior).
- **Config tests (extend `tests/test_config_engine.py` or a new
  `tests/test_config_repaste.py`):**
  - default `repaste_keys == ["cmd", "ctrl", "shift"]` when no `[repaste]` table;
  - a valid `[repaste] keys` is parsed and lower-cased;
  - invalid values (empty list, >3 keys, non-string entries) raise `ValueError`,
    mirroring the `hotkey.keys` validation tests.
- **`tests/test_app_engine.py` (extend):**
  - `_do_repaste` pastes `history.items()[0] + " "` when the app is `IDLE`,
    `can_paste()` is true, and history is non-empty (monkeypatch `paste_text`,
    stub `repaste_hotkey.wait_all_released` → `True`, as the dictation tests do);
  - empty history → nothing pasted, a notification is emitted;
  - `can_paste()` false → nothing pasted;
  - state not `IDLE` → nothing pasted (notified-and-skipped).
- **Menu/listener wiring** (second tap start/stop, watchdog coverage) is verified
  manually, consistent with `menubar.py` being un-unit-tested.

## Files touched

- `flow/config.py` — `repaste_keys` field, `[repaste]` parsing, shared
  keys-validation helper.
- `flow/hotkey.py` — tap mode (`on_trigger`, clean-tap/contamination logic).
- `flow/app.py` — `repaste_hotkey`, `_on_repaste`, `_do_repaste`, start/shutdown.
- `flow/menubar.py` — watchdog + heartbeat coverage for the second tap.
- `config.toml.example` — documented `[repaste]` section.
- `tests/test_hotkey.py` — tap-mode tests.
- `tests/test_config_engine.py` (or new `tests/test_config_repaste.py`) — config
  tests.
- `tests/test_app_engine.py` — re-paste behavior tests.
- `README.md` / `GETTING_STARTED.md` — brief mention of the re-paste hotkey.
