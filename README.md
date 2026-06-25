# TRD Speak

Minimal push-to-talk dictation for macOS, running entirely on your machine.
Hold a hotkey combo, speak, release — your words are transcribed locally with
[faster-whisper](https://github.com/SYSTRAN/faster-whisper) and pasted at the
cursor of whatever app has focus. No cloud, no accounts, no GUI.

**New here? Follow the [Getting Started guide](GETTING_STARTED.md)** — it
walks through install, permissions, and first dictation step by step.

## Requirements

- macOS (Apple Silicon or Intel)
- Python 3.11+ (`brew install python@3.12` if you don't have one)
- ~1 GB of disk for the Whisper model
- The Xcode Command Line Tools (`xcode-select --install`) for the app's tiny
  launcher

## Quick start

```sh
git clone <repo-url> trd-speak
cd trd-speak
./setup.sh           # venv, dependencies, model download
./make_dev_app.sh    # builds dist/TRD Speak Dev.app (+ .dmg)
open "dist/TRD Speak Dev.app"
```

That's it. TRD Speak shows up in the **Dock** and the **menu bar**. On a
fresh machine the menu bar icon is **⚠️** — the app needs three permissions,
and it deliberately does **not** throw dialogs at you on launch. You get one
notification ("TRD Speak needs setup — click the ⚠️ icon in the menu bar")
and that's it: **the ⚠️ menu itself is the setup wizard**, one step at a
time.

Click the ⚠️ icon (or the Dock icon — same menu). The header reads
**"Setup — step N of 3"** and exactly one row is clickable: the current
step. Completed steps show as "✓ …", later steps are greyed out until it's
their turn.

1. **Step 1 — Microphone.** Click the row: macOS shows its native
   Allow/Deny prompt (or, if you denied it before, the row opens the
   Microphone privacy pane so you can flip the switch). Applies
   immediately — the menu checks it off and moves to step 2.
2. **Step 2 — Accessibility.** Click the row: macOS registers TRD Speak in
   the Accessibility pane and usually shows its own dialog with an "Open
   System Settings" button — follow it and switch on **TRD Speak**. (If the
   dialog is gone, click the row again and it opens the pane directly.)
   Applies immediately, no restart.
3. **Step 3 — Input Monitoring.** Same drill: click the row, follow macOS's
   dialog (or re-click to open the pane), switch on **TRD Speak**. This is
   the one permission macOS may tie to a restart: when you flip the toggle,
   **System Settings itself** offers "Quit & Reopen" — accept it and the
   relaunched app boots straight to Ready.

The app re-checks every couple of seconds and never restarts itself. Once
all three permissions are in place it simply finishes starting up
in-process. Only if macOS insists on a fresh process for the new Input
Monitoring grant (and you skipped System Settings' "Quit & Reopen") does
the menu change to **"Setup complete — restart TRD Speak to finish"** with
a single **"Restart TRD Speak now"** row — the restart happens only when
*you* click it.

When the icon shows **🎤 Ready**, focus any text field, hold **Ctrl+Shift**
(or whatever hotkey you set in `config.toml`), speak, and release.

The steps are a guide, not a cage: if you grant permissions directly in
System Settings — in any order — the menu notices and checks them off on
its own. And you can revoke a permission at any time; the icon goes back to
⚠️ and the setup menu reappears at exactly the step that needs fixing.

## The app

- The **menu bar icon** shows the state: ⚠️ permissions missing, ⏳ loading
  the model, 🎤 ready, 🔴 recording, ✍️ transcribing.
- **Recent Dictations** lists your last 10 dictations (newest first). Click
  one to copy its full text to the clipboard — so you can switch to the right
  window and paste it yourself when a dictation landed in the wrong app (there
  is no auto-paste). "Clear Recent Dictations" empties the list. The history
  is in memory only and is gone when you quit.
- **Re-paste the last dictation** with a global shortcut (default
  `cmd+ctrl`): if a dictation landed in the wrong window, switch to the right
  one and *tap* the combo — press and release it without pressing anything
  else — to insert your most recent dictation there. Unlike Recent Dictations,
  this pastes directly, no menu. Pressing any other key during the hold cancels
  it, so app shortcuts that build on the combo (e.g. `Cmd+Ctrl+Space` for the
  emoji picker, `Cmd+Ctrl+F` for fullscreen) still work normally. Change the
  combo with `repaste.keys` in `config.toml`. It must not be a subset of your
  dictation combo (and vice versa), or the two would fire together.
- **Configuration…** (just under Recent Dictations) opens a small panel where
  you can change the dictate and re-paste shortcuts without editing
  `config.toml`: click a field, *press* the combo you want, and Save. The new
  shortcut applies immediately — no restart — and persists across launches.
- **Correct & learn** (default `cmd+alt`): after a dictation, tap this shortcut
  to open a small editor showing your last dictation. Edit the text as you
  intended it — then click Save. The app learns from the diff: any word you
  consistently replace becomes a silent rewrite rule applied to all future
  dictations. Common homophones (e.g. cloud↔Claude) are added to the
  vocabulary for acoustic biasing instead of hard rewrite rules, so real uses
  of the common word are not corrupted. Learned rules accumulate silently;
  the shortcut is configurable in **Configuration…** alongside the dictation
  and re-paste shortcuts.
- **Learned Words** (a submenu) lists every rule the app has learned; you can
  remove individual rules from here. A separate **Open Dictionary File…** item
  (a sibling of the submenu, not nested under it) reveals the underlying JSON
  in Finder so you can inspect or hand-edit it.
- The **dictionary file** lives at
  `~/Library/Application Support/TRD Speak[ Dev]/dictionary.json` and
  survives reinstall. It contains two sections: `vocabulary` (proper nouns and
  technical terms that bias transcription) and `replacements` (from→to rewrite
  rules). Each replacement supports: `from` and `to` (required strings),
  `case_sensitive` (default `false`), and `whole_word` (default `true` — set
  `false` to match inside larger words). The app also writes `learned` and `ts`
  on rules it learns from your corrections; you don't write those yourself. See
  `dictionary.json.example` in the repo for the format. The file is created on
  first save; you can seed it manually at any time.
- The **correction feature is fully local** — no cloud, no accounts. A
  contextual LLM tier that rewrites for meaning is planned for a future
  release.
- The menu always ends with **Open Log** and **Quit TRD Speak**.
- The **Dock icon** is visible while the app runs; clicking it opens the
  menu bar menu, so it always leads straight to the controls.
- On first launch with missing permissions the app stays quiet: no system
  prompts, no Settings panes, no dialogs — just the ⚠️ icon and a single
  notification pointing you at it. Every permission prompt and Settings
  pane is triggered by *you* clicking the current step's menu row, one step
  at a time (Microphone, then Accessibility, then Input Monitoring).
- The app never restarts itself. If a restart is needed at all, either
  System Settings offers its own "Quit & Reopen" when you flip the Input
  Monitoring toggle, or the menu offers a "Restart TRD Speak now" row that
  acts only when you click it.
- Status lines also go to `~/Library/Logs/trd-speak.log` (`tail -f` to
  watch).
- Stop it from the menu ("Quit TRD Speak") or with `./stop.sh`.
- To start dictation automatically at login, add TRD Speak in
  **System Settings -> General -> Login Items**.
- The dev build is self-contained and ad-hoc signed; it does not depend on
  this folder's location. Each rebuild is signed afresh, so expect macOS to
  ask you to re-grant permissions after you install a new build.

### Terminal alternative (dev)

You can also run TRD Speak directly in a terminal with `./run.sh`. In that
case the three permissions attach to your **terminal app** (Terminal,
iTerm2, …) instead of TRD Speak — grant them in System Settings -> Privacy &
Security manually. Don't run the app and `./run.sh` at the same time — both
would react to the hotkey and paste twice.

## Configuration

The app runs on built-in defaults with no config file at all. To customize,
copy the template and edit it (`config.toml` is gitignored, so updates never
clobber your settings):

```sh
cp config.toml.example config.toml
```

Delete any key (or the whole file) to fall back to defaults. You can also
point at another file with `./run.sh --config PATH`. Restart the app to
apply changes.

| Option | Default | Meaning |
| --- | --- | --- |
| `hotkey.keys` | `["ctrl", "shift"]` | 1–3 keys that must ALL be held to record; releasing any one stops and transcribes. |
| `repaste.keys` | `["cmd", "ctrl"]` | 1–3 keys for the re-paste shortcut; tap them (no other key) to re-insert your last dictation into the focused window. Same key names as `hotkey.keys`; must not be a subset of (or superset of) `hotkey.keys`. |
| `correct.keys` | `["cmd", "alt"]` | 1–3 keys for the correction shortcut; tap them (no other key) to open the correction editor for your last dictation. Configurable in **Configuration…** without editing this file. |
| `engine.name` | `"whisper"` | Transcription engine. Currently only `whisper` (faster-whisper) is available. |
| `whisper.model` | `"base.en"` | faster-whisper model: `tiny.en`, `base.en`, `small.en`, `medium.en`, … |
| `whisper.compute_type` | `"int8"` | Quantization for CPU inference; `int8` is fastest and lightest. |
| `whisper.beam_size` | `1` | Decoding beam width; `1` (greedy) is fastest, higher is slightly more accurate but much slower on long recordings. |
| `recording.max_seconds` | `180` | Maximum recording length; audio beyond this is dropped. |
| `recording.sample_rate` | `16000` | Microphone sample rate in Hz; Whisper expects 16000 — leave as is. |
| `paste.restore_delay` | `0.4` | Seconds to wait after Cmd+V before restoring your previous clipboard. |

### Transcription engine

TRD Speak transcribes locally with faster-whisper, which runs on the CPU and
is light to install.

Valid key names for `hotkey.keys`:

- Modifiers: `ctrl`, `alt` (alias `option`), `cmd` (alias `command`), `shift`
- Arrows: `right`, `left`, `up`, `down`
- Others: `space`, `tab`, `enter`, `esc`, `f1` … `f20`
- Or any single character, e.g. `"z"` — single-character hotkeys are matched
  by physical key position and assume an ANSI (US-style) keyboard layout;
  modifier-only combos (the default) are layout-independent

Example alternate combos:

```toml
[hotkey]
keys = ["cmd", "shift", "z"]   # three-key combo
# keys = ["f19"]               # a single spare function key
# keys = ["ctrl", "space"]     # note: may conflict with input-source switching
```

Modifier-only combos are the most reliable: they don't type anything into the
focused app and rarely collide with system shortcuts.

## Troubleshooting

- **The menu bar icon shows ⚠️** — one or more permissions are missing.
  Click the icon (or the Dock icon) and follow the menu: it shows
  "Setup — step N of 3" with exactly one clickable row, the current step.
  Click it, deal with the macOS prompt or Settings pane it brings up, and
  the menu advances to the next step on its own (it re-checks every couple
  of seconds). Granting things directly in System Settings, in any order,
  works too — the steps check themselves off.
- **The menu says "Setup complete — restart TRD Speak to finish"** — all
  permissions are granted, but macOS will only honor the fresh Input
  Monitoring grant in a new process. Click **"Restart TRD Speak now"** (or
  accept System Settings' own "Quit & Reopen" if it's still showing). The
  app never restarts on its own.
- **The icon never leaves ⚠️ even though System Settings looks right** —
  toggle the TRD Speak entry off and on in the relevant pane, or remove it
  (the "–" button) and click the menu row again. Rebuilding with
  `./make_dev_app.sh` changes the ad-hoc signing identity, so after installing
  a rebuild macOS may require you to re-grant.
- **Recording works (🔴 shows) but no text is ever inserted** — the
  **Accessibility** permission is missing, so the synthetic Cmd+V is
  silently dropped by macOS. The ⚠️ menu walks you to the right step; with
  `./run.sh` grant it to your terminal app instead, then restart that
  terminal session.
- **Hotkey does nothing at all** — the **Input Monitoring** permission is
  missing. Same drill: follow the menu step (app) or grant it to your
  terminal app (`./run.sh`) and restart the terminal session.
- **No audio / always "Heard nothing"** — the **Microphone** permission is
  missing, or the wrong input device is selected in Sound settings.
- **Nothing seems to be running** — the app shows a Dock icon and a menu bar
  icon while running; if neither is there, check
  `~/Library/Logs/trd-speak.log` and start it again with
  `open TRDSpeak.app`.
- **First transcription is slow** — the model is loaded into memory at startup;
  the very first inference also warms caches. Subsequent dictations are much
  faster. Smaller models (`base.en`, `tiny.en`) trade accuracy for speed.
- **Paste comes out as old clipboard text** — increase `paste.restore_delay`.

## Roadmap

Planned but not yet implemented:

- LLM post-processing to clean up filler words and punctuation

## License

[MIT](LICENSE)
