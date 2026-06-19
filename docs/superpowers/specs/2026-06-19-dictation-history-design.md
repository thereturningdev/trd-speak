# Recent Dictations history — design

**Date:** 2026-06-19
**Status:** Approved (pending spec review)

## Problem

When dictating with LocalFlow, the transcribed text is pasted into whatever
app has focus. If focus is on the wrong window at release time, the text lands
somewhere useless and is effectively lost — you have to redictate it.

This feature gives a way to recover those dictations: a menu-bar list of the
last 10 things you dictated, where clicking any entry copies it to the
clipboard so you can switch to the right window and paste it yourself.

## Goals

- See the last 10 dictations from the menu-bar icon.
- Click any entry to copy its full text to the clipboard.
- Brief confirmation that the copy happened.
- Clear the history on demand.

## Non-goals

- No persistence to disk (privacy: matches the app's local-only, no-trace
  ethos). History lives only in memory and is gone on quit.
- No auto-paste of a history entry (the frontmost app right after a menu
  interaction is unreliable — re-pasting into the wrong window is the exact
  problem we're solving).
- No editing, search, pinning, or export of entries.
- No configurable cap in this version (fixed at 10; trivially bumpable later).

## Decisions (from brainstorming)

| Decision | Choice |
| --- | --- |
| Persistence | In-memory only; cleared on quit. |
| Click action | Copy to clipboard **+** a "Copied" notification. |
| Row label | Dictated text only, truncated; no timestamp. |
| Order | Newest first. |
| Clear option | Yes — a "Clear Recent Dictations" row. |
| Cap | 10, as a module constant. |
| What's captured | Every non-empty transcription, **including** ones that failed to paste. |
| Clipboard on copy | Set and **left** on the clipboard (not restored). |

## Architecture

The history mutates on a worker thread (each dictation finishes off the main
run loop) but the menu is read on the AppKit main thread. We bridge that with
**lazy population**: the submenu has an `NSMenuDelegate` whose
`menuNeedsUpdate:` runs on the main thread right before the submenu opens and
rebuilds the rows from a thread-safe store. The menu is always fresh, and no
menu object is ever touched from a worker thread. All thread-safety lives in
the store's lock; the menu only reads, only on the main thread.

(Considered and rejected: eager re-render, where `App` pushes each new
dictation to `MenuBar` to rebuild rows immediately. More wiring and it churns
menu items constantly even when the menu is never opened.)

### Components

1. **`flow/history.py` — new.** A thread-safe `History` class wrapping
   `collections.deque(maxlen=MAX_HISTORY)` with `MAX_HISTORY = 10`:
   - `add(text: str) -> None` — append under a `threading.Lock`.
   - `items() -> list[str]` — snapshot, **newest first**.
   - `clear() -> None` — empty it.
   - Pure Python, no AppKit import — directly unit-testable.

2. **`flow/app.py` — capture point.** `App.__init__` creates
   `self.history = History()`. In `_process()`, when `text` is non-empty, call
   `self.history.add(text)` **before** the paste attempt, so dictations that
   fail to paste (trigger keys still held, Accessibility missing) are still
   captured — those are prime recovery cases. Empty / "heard nothing" results
   are not stored. The raw text is stored (no trailing space; the trailing
   space is a paste-only concern).

3. **`flow/menubar.py` — the submenu.** A "Recent Dictations" submenu placed
   alongside the engine picker, shown only in the normal fully-granted state
   (same visibility rule as the engine menu and its separator). Its delegate's
   `menuNeedsUpdate:`:
   - reads `logic.history.items()`;
   - rebuilds the dynamic rows: each row's **title** = the text with newlines
     collapsed to spaces and truncated (~60 chars + `…`); **tooltip** = the
     full text; **representedObject** = the full untruncated text; **action** =
     `copyDictation:`;
   - if the history is empty, shows a single disabled "No dictations yet" row;
   - appends a separator and a **"Clear Recent Dictations"** row (action
     `clearDictations:`), disabled when the history is empty.

4. **Actions on `_Delegate`:**
   - `copyDictation_(sender)` → `paster.set_clipboard(str(sender.representedObject()))`
     — **no** save/restore; we intentionally leave the dictation on the
     clipboard — then `_notify("Copied — switch to your window and paste")`.
   - `clearDictations_(sender)` → `logic.history.clear()`.

### Data flow

```
hold hotkey → record → transcribe → text
                                      │
                  (non-empty) ──► history.add(text) ──► then try paste
                                      │
menu opens ──► menuNeedsUpdate: reads history.items() (newest first) → rebuild rows
click row  ──► set_clipboard(full text) + "Copied" notification
click Clear ─► history.clear()
```

## Edge cases

- **In-memory only:** `History` is a field on the `App` instance; process exit
  clears it. Nothing is written to disk.
- **Cap = 10:** `deque(maxlen=10)` evicts the oldest automatically.
- **Duplicates kept:** dictating the same text twice yields two entries.
- **Newlines in a dictation:** collapsed to spaces in the row title only; the
  tooltip and the copied text keep the original.
- **Copy does not restore the clipboard:** deliberately different from the
  dictation paste flow — the point is to leave the text ready to paste.
- **Visibility:** the submenu and its separator are hidden during onboarding /
  restart-needed states, exactly like the engine picker.

## Testing

- **`tests/test_history.py` (new):**
  - `add` then `items()` returns newest-first;
  - capacity holds at 10 and evicts the oldest in order;
  - `clear()` empties it;
  - a basic concurrent-add test (threading style mirrors `test_app_engine.py`)
    confirms no lost/garbled entries under contention.
- **Extend `tests/test_app_engine.py`:**
  - the full-cycle happy-path test also asserts the text landed in
    `app.history.items()`;
  - a new test asserts an **empty** transcription is **not** recorded;
  - a new test asserts a **paste-skipped** dictation **is** still recorded.
- **Menu wiring** (submenu, delegate, actions) is verified manually —
  consistent with the existing `menubar.py`, which is not unit-tested.

## Files touched

- `flow/history.py` — new.
- `flow/app.py` — instantiate `History`, capture in `_process()`.
- `flow/menubar.py` — submenu, delegate `menuNeedsUpdate:`, copy/clear actions,
  visibility wiring.
- `tests/test_history.py` — new.
- `tests/test_app_engine.py` — extended assertions.
- `README.md` / `GETTING_STARTED.md` — brief mention of the feature (optional,
  during implementation).
