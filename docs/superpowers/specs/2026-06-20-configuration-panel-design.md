# Configuration panel — design

**Date:** 2026-06-20
**Status:** Designed (not yet implemented).
**Issue:** #3 — "Add a configuration panel"
**Mockup:** [`2026-06-20-configuration-panel-mockup.html`](./2026-06-20-configuration-panel-mockup.html)
— an interactive HTML mockup of the window (open in a browser; click a field and
press a combo to try the recorder). It is a layout/UX preview only; the shipped
window uses native AppKit controls.

## Problem

LocalFlow's two global shortcuts — **dictate** (push-to-talk hold) and **paste
last dictation** (re-paste tap) — can only be changed by hand-editing
`config.toml` and relaunching the app. There is no in-app way to see or change
them. A menu-bar app should let the user reconfigure its shortcuts from the menu.

This feature adds a **"Configuration…"** menu item (directly under "Recent
Dictations") that opens a settings window where the user records new shortcuts
by pressing them, with the change taking effect immediately.

## Goals

- A **"Configuration…"** row in the menu, just under "Recent Dictations".
- A settings window that shows the current dictate and re-paste shortcuts and
  lets the user **record** a new one by pressing the actual key combo
  (macOS System Settings style).
- Changes **apply immediately** — the live event taps are rebuilt on save, no app
  restart.
- The chosen shortcuts **persist** across launches, stored outside the
  hand-edited `config.toml`.

## Non-goals

- No other settings in this version (engine, model, max recording seconds,
  paste delay, …). The issue scopes the panel to the two shortcuts only;
  everything else stays in `config.toml`. The window is built so more settings
  can be added later.
- No rewriting of `config.toml` — it stays the user's commented, hand-edited
  file (same stance as `engine_state.py`).
- No remapping of the menu/onboarding behavior — the panel is purely the two
  shortcut recorders plus Save/Cancel.
- No per-combo "disable" toggle — both shortcuts remain always-on; only their
  key combos are editable.

## Decisions (from brainstorming)

| Decision | Choice |
| --- | --- |
| Menu placement | A "Configuration…" row directly under "Recent Dictations", shown only in the normal fully-granted state. |
| Shortcut entry | **Record by pressing** the actual combo (not dropdowns). |
| Apply timing | **Immediately** — rebuild and restart both event taps on Save. |
| Persistence | An **App Support JSON file**, precedence over `config.toml` (mirrors `engine_state.py`). |
| Allowed keys | **Full vocabulary** — modifiers + letters + named keys, exactly what `config.toml` already accepts. |
| Combo length | 2–3 keys, at least one modifier, for both shortcuts (a 1-key global hotkey is unusable). |
| Conflict policy | Block **identical** dictate/re-paste combos; allow overlapping-but-different combos with a non-blocking warning. |
| Recording interference | Suspend both global taps while the window is open; restart on close (new combos on Save, existing on Cancel). |
| Window code | A new `flow/settings_window.py` module, keeping `menubar.py` from growing further. |

## The recording problem (why this is the crux)

The user records a shortcut by **pressing it**. Two wrinkles make this the
delicate part of the feature:

1. **The global taps are listen-only**, so they keep observing key events even
   while the settings window is focused. Pressing `ctrl+shift` to *record* the
   dictate shortcut would also fire the live push-to-talk listener and start a
   real dictation. The window therefore **suspends both global listeners while it
   is open** and restarts them on close (with the new combos on Save, the
   existing ones on Cancel). Push-to-talk being idle while the panel is open is
   acceptable — the user is configuring, not dictating.

2. **Capturing both modifier+key and modifier-only combos.** A recorder field, on
   click, enters "Recording… press a shortcut" and installs an `NSEvent` *local*
   monitor for `keyDown` + `flagsChanged` (swallowing events so nothing leaks
   through while recording):
   - **Modifier+key** (e.g. `⌘⌃V`): finalize on the non-modifier `keyDown` →
     `[held modifiers…] + [key]`.
   - **Modifier-only** (e.g. `⌃⇧`, the current dictate default): track the *peak*
     set of held modifiers from `flagsChanged`; finalize when a modifier is
     released with no non-modifier key having been pressed.
   - **Esc** cancels recording and restores the previous value.

## Architecture

A new **`flow/settings_window.py`** owns the `NSWindow`, the two recorder
controls, and the save/cancel logic. `flow/app.py` gains the methods to apply,
suspend, and resume the hotkeys live. `flow/hotkey_state.py` (new) persists the
combos. `flow/hotkey.py` exposes small keycode/flags→token helpers so the
recorder reuses the existing keycode tables instead of duplicating them.
`flow/menubar.py` adds the menu row and wiring and resolves the persisted combos
at startup.

(Considered and rejected: dropdown pickers for each key. The maintainer chose
record-by-press for the native feel; dropdowns are simpler but were not wanted.
Considered and rejected: writing changes back into `config.toml`. It would
reformat the file and lose comments — the engine choice already established the
App-Support-file precedence pattern, and this reuses it.)

### Components

1. **`flow/hotkey_state.py` (new) — persistence.**
   Mirrors `engine_state.py`. A single JSON file at
   `~/Library/Application Support/LocalFlow/hotkeys.json`:
   ```json
   {"dictate": ["ctrl", "shift"], "repaste": ["cmd", "ctrl"]}
   ```
   - `load(path=…) -> dict | None` — parsed dict, or None if unset/unreadable.
   - `save(dictate_keys, repaste_keys, path=…)` — create the parent dir, write.
   - `resolve(config, path=…) -> tuple[list[str], list[str]]` — for each combo,
     the saved value (if present and valid per the shared key validator) wins,
     else the `config.toml` value. Invalid/partial state silently falls back to
     config, never wedges startup.

2. **`flow/config.py` — expose the key validator.**
   - Promote the existing private `_validate_keys` to a public `validate_keys`
     (keep a private alias if convenient) so `hotkey_state` and the window can
     reuse the exact same 1–3-token validation. No behavior change.

3. **`flow/hotkey.py` — keycode/flags → token helpers.**
   - `token_for_keycode(keycode: int) -> str | None` — invert `_NAMED_KEYCODES`
     and `_CHAR_KEYCODES` (and the modifier keycodes) to map an NSEvent/Quartz
     virtual keycode to a canonical token, or None if unmapped.
   - `modifier_tokens_from_flags(flags: int) -> set[str]` — map an NSEvent
     `modifierFlags` value to the set of modifier tokens currently down, using
     `_MODIFIER_MASKS` (NSEvent and Quartz share the same mask bits).
   - `validate_combo(keys: list[str], *, min_keys=2, max_keys=3,
     require_modifier=True) -> None` — raise `ValueError` with a human message if
     the combo is too short/long or has no modifier. Reused by the window and
     covered by unit tests. (NSEvent keyCodes equal Quartz virtual keycodes, so
     these tables are authoritative for both.)

4. **`flow/app.py` — apply / suspend / resume live.**
   - `suspend_hotkeys()` — `self.hotkey.stop()` and `self.repaste_hotkey.stop()`.
   - `resume_hotkeys()` — `self.hotkey.start()` and `self.repaste_hotkey.start()`
     (each `start()` recreates its tap after a `stop()` cleared it).
   - `set_hotkeys(dictate_keys, repaste_keys)` — stop both, rebuild the two
     `HotkeyListener` objects with the new keys and the same callbacks
     (`_on_activate`/`_on_deactivate` for dictate, `_on_trigger` for re-paste),
     start both, and update `self.config.keys` / `self.config.repaste_keys`. All
     on the main thread (the Save button action), the same thread as the menu
     poll/watchdog, so there is no race; the watchdog reads
     `logic.hotkey`/`logic.repaste_hotkey` fresh each tick and picks up the new
     objects automatically.

5. **`flow/settings_window.py` (new) — the window + recorder.**
   - A programmatically-built `NSWindow` (no nib): two labeled **recorder
     controls** (dictate, re-paste), a status/validation line, and
     **Save** / **Cancel** buttons.
   - **Recorder control** — a custom `NSView`/`NSButton` subclass:
     - Click → "Recording… press a shortcut"; install an `NSEvent` local monitor
       for `keyDown` + `flagsChanged`, swallowing events while recording.
     - Capture logic per "The recording problem" above (modifier+key on the
       non-modifier `keyDown`; modifier-only on first release of the peak set;
       Esc cancels). Captured keycodes/flags → tokens via the `hotkey.py`
       helpers; display as macOS glyphs (`⌘⌥⌃⇧` + uppercased key).
   - **Open** — a controller method (called from the menu action): activate the
     app (`NSApp.activateIgnoringOtherApps_(True)`), `logic.suspend_hotkeys()`,
     show the window populated with the current combos.
   - **Save** — read both recorded combos; `validate_combo` each; reject
     identical combos (status line message, no save); on a non-identical overlap,
     show a non-blocking warning but allow. On success: `logic.set_hotkeys(...)`,
     `hotkey_state.save(...)`, `menubar.update_combo(...)`, close. Closing while
     hotkeys are live means **no** separate resume is needed (set_hotkeys already
     started them).
   - **Cancel / close without save** — `logic.resume_hotkeys()` (restart with the
     unchanged config keys), close.

6. **`flow/menubar.py` — menu row, action, startup resolve.**
   - At startup in `run()`, alongside the existing
     `engine_state.resolve_engine(...)`, call
     `config.keys, config.repaste_keys = hotkey_state.resolve(config)` *before*
     building `App` and the combo display string.
   - Add a **"Configuration…"** `NSMenuItem` immediately after `_history_root`
     (Recent Dictations) and before the engine picker, target the delegate with a
     new `openConfig:` action. Include it in the `show_engine` visibility gate so
     it only appears in the normal fully-granted state.
   - `_Delegate.openConfig_` lazily builds/raises the settings window controller,
     passing it `logic` and the `MenuBar` (for the combo refresh). Keep a strong
     reference to the controller on the delegate (windows/controllers must not be
     GC'd).
   - Add `MenuBar.update_combo(dictate_keys, repaste_keys)` — refresh
     `self._combo` (the "Ready — hold … to dictate" header) and re-render on the
     main thread.

### Data flow

```
menu: Configuration… ──► openConfig: ──► controller.open()
        │
   NSApp.activate + logic.suspend_hotkeys() + show window (current combos)
        │
   user clicks a recorder ──► local NSEvent monitor captures keyDown/flagsChanged
        │                         (global taps are suspended, so no stray dictation)
   combo captured ──► glyphs shown in the field
        │
   Save ──► validate_combo + identical-combo check
        │
   logic.set_hotkeys(dictate, repaste)   (stop → rebuild → start both taps)
   hotkey_state.save(dictate, repaste)   (~/Library/Application Support/LocalFlow/hotkeys.json)
   menubar.update_combo(...)             (header text)
        │
   window closes ──► new shortcuts live immediately

   Cancel ──► logic.resume_hotkeys() ──► window closes ──► shortcuts unchanged

startup: hotkey_state.resolve(config) ──► saved combos win over config.toml
```

## Edge cases

- **Recording self-trigger** — solved by suspending both global taps for the
  window's whole lifetime; the local NSEvent monitor is the only listener active
  during recording.
- **Modifier-only vs modifier+key** — both captured (see the recording section);
  the peak-modifier rule handles `⌃⇧`-style combos that the dictate default uses.
- **Esc / abandon** — Esc cancels an in-progress recording and keeps the prior
  value; Cancel/close discards all edits and resumes the unchanged hotkeys.
- **Invalid combo** (1 key, >3 keys, no modifier) — `validate_combo` rejects it
  with a status-line message; Save is blocked until both fields are valid.
- **Identical combos** — blocked: dictate is hold-mode and re-paste is tap-mode,
  so the same combo firing both is ambiguous. The status line explains why.
- **Overlapping-but-different combos** (e.g. dictate `ctrl+shift`, re-paste
  `ctrl+shift+v`) — allowed with a non-blocking warning, consistent with the
  existing "the user owns that choice" stance from the re-paste design.
- **Crash/partial persisted state** — `hotkey_state.resolve` validates each combo
  and falls back to `config.toml` for anything missing or invalid, so a bad file
  never blocks startup.
- **Settings window open during a dictation** — unlikely (the user is in the
  menu), but `set_hotkeys` rebuilds listeners on the main thread; if a dictation
  is somehow in flight it will be cut by the suspend, which is acceptable for a
  deliberate configuration action.

## Testing

- **`tests/test_hotkey_state.py` (new):** `save` then `load` round-trips both
  combos; `load` of a missing/garbage file returns None; `resolve` returns saved
  combos when valid, falls back to config per-combo when missing/invalid, and
  saved-wins-over-config.
- **`tests/test_hotkey.py` (extend):** `token_for_keycode` for a modifier, a
  letter, a named key, and an unmapped code; `modifier_tokens_from_flags` for
  single/multiple/zero modifiers; `validate_combo` accepts 2–3-key combos with a
  modifier and rejects 1-key, 4-key, and modifier-less combos.
- **`tests/test_app_hotkeys.py` (new) or extend `tests/test_app_engine.py`:**
  `set_hotkeys` replaces both listener objects and updates `config.keys` /
  `config.repaste_keys`; `suspend_hotkeys`/`resume_hotkeys` call `stop`/`start` on
  both (monkeypatch `HotkeyListener.start`/`stop` so no real tap is created).
- **Window + recorder view** — verified manually (recording a modifier+key and a
  modifier-only combo, Esc/Cancel, identical-combo rejection, immediate apply),
  consistent with `menubar.py` being un-unit-tested.

## Files touched

- `flow/settings_window.py` (new) — NSWindow, recorder controls, save/cancel.
- `flow/hotkey_state.py` (new) — App Support JSON persistence + resolve.
- `flow/hotkey.py` — `token_for_keycode`, `modifier_tokens_from_flags`,
  `validate_combo`.
- `flow/app.py` — `set_hotkeys`, `suspend_hotkeys`, `resume_hotkeys`.
- `flow/menubar.py` — "Configuration…" row + `openConfig:` action, startup
  `hotkey_state.resolve`, `MenuBar.update_combo`.
- `flow/config.py` — expose `validate_keys`.
- `config.toml.example` / `README.md` / `GETTING_STARTED.md` — mention the panel.
- `tests/test_hotkey_state.py` (new), `tests/test_hotkey.py` (extend),
  `tests/test_app_hotkeys.py` (new or extend `tests/test_app_engine.py`).
