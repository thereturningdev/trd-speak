# local-flow

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
git clone <repo-url> local-flow
cd local-flow
./setup.sh           # venv, dependencies, model download
./make_app.sh        # builds LocalFlow.app
open LocalFlow.app
```

That's it. LocalFlow shows up in the **Dock** and the **menu bar**. On a
fresh machine the menu bar icon is **⚠️** — the app needs three permissions,
and it deliberately does **not** throw dialogs at you on launch. You get one
notification ("LocalFlow needs setup — click the ⚠️ icon in the menu bar")
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
2. **Step 2 — Accessibility.** Click the row: macOS registers LocalFlow in
   the Accessibility pane and usually shows its own dialog with an "Open
   System Settings" button — follow it and switch on **LocalFlow**. (If the
   dialog is gone, click the row again and it opens the pane directly.)
   Applies immediately, no restart.
3. **Step 3 — Input Monitoring.** Same drill: click the row, follow macOS's
   dialog (or re-click to open the pane), switch on **LocalFlow**. This is
   the one permission macOS may tie to a restart: when you flip the toggle,
   **System Settings itself** offers "Quit & Reopen" — accept it and the
   relaunched app boots straight to Ready.

The app re-checks every couple of seconds and never restarts itself. Once
all three permissions are in place it simply finishes starting up
in-process. Only if macOS insists on a fresh process for the new Input
Monitoring grant (and you skipped System Settings' "Quit & Reopen") does
the menu change to **"Setup complete — restart LocalFlow to finish"** with
a single **"Restart LocalFlow now"** row — the restart happens only when
*you* click it.

When the icon shows **🎤 Ready**, focus any text field, hold **Ctrl+Alt**
(or whatever hotkey you set in `config.toml`), speak, and release.

The steps are a guide, not a cage: if you grant permissions directly in
System Settings — in any order — the menu notices and checks them off on
its own. And you can revoke a permission at any time; the icon goes back to
⚠️ and the setup menu reappears at exactly the step that needs fixing.

## The app

- The **menu bar icon** shows the state: ⚠️ permissions missing, ⏳ loading
  the model, 🎤 ready, 🔴 recording, ✍️ transcribing.
- The menu always ends with **Open Log** and **Quit LocalFlow**.
- The **Dock icon** is visible while the app runs; clicking it opens the
  menu bar menu, so it always leads straight to the controls.
- On first launch with missing permissions the app stays quiet: no system
  prompts, no Settings panes, no dialogs — just the ⚠️ icon and a single
  notification pointing you at it. Every permission prompt and Settings
  pane is triggered by *you* clicking the current step's menu row, one step
  at a time (Microphone, then Accessibility, then Input Monitoring).
- The app never restarts itself. If a restart is needed at all, either
  System Settings offers its own "Quit & Reopen" when you flip the Input
  Monitoring toggle, or the menu offers a "Restart LocalFlow now" row that
  acts only when you click it.
- Status lines also go to `~/Library/Logs/local-flow.log` (`tail -f` to
  watch).
- Stop it from the menu ("Quit LocalFlow") or with `./stop.sh`.
- To start dictation automatically at login, add LocalFlow in
  **System Settings -> General -> Login Items**.
- The bundle bakes in this folder's absolute path and is ad-hoc signed:
  re-run `./make_app.sh` if you move the folder, and expect macOS to ask you
  to re-grant permissions after a rebuild.

### Terminal alternative (dev)

You can also run local-flow directly in a terminal with `./run.sh`. In that
case the three permissions attach to your **terminal app** (Terminal,
iTerm2, …) instead of LocalFlow — grant them in System Settings -> Privacy &
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
| `hotkey.keys` | `["ctrl", "alt"]` | 1–3 keys that must ALL be held to record; releasing any one stops and transcribes. |
| `whisper.model` | `"small.en"` | faster-whisper model: `tiny.en`, `base.en`, `small.en`, `medium.en`, … |
| `whisper.compute_type` | `"int8"` | Quantization for CPU inference; `int8` is fastest and lightest. |
| `whisper.beam_size` | `1` | Decoding beam width; `1` (greedy) is fastest, higher is slightly more accurate but much slower on long recordings. |
| `recording.max_seconds` | `180` | Maximum recording length; audio beyond this is dropped. |
| `recording.sample_rate` | `16000` | Microphone sample rate in Hz; Whisper expects 16000 — leave as is. |
| `paste.restore_delay` | `0.4` | Seconds to wait after Cmd+V before restoring your previous clipboard. |

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
- **The menu says "Setup complete — restart LocalFlow to finish"** — all
  permissions are granted, but macOS will only honor the fresh Input
  Monitoring grant in a new process. Click **"Restart LocalFlow now"** (or
  accept System Settings' own "Quit & Reopen" if it's still showing). The
  app never restarts on its own.
- **The icon never leaves ⚠️ even though System Settings looks right** —
  toggle the LocalFlow entry off and on in the relevant pane, or remove it
  (the "–" button) and click the menu row again. Rebuilding with
  `./make_app.sh` changes the ad-hoc signing identity, so after a rebuild
  macOS may require you to re-grant.
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
  `~/Library/Logs/local-flow.log` and start it again with
  `open LocalFlow.app`.
- **First transcription is slow** — the model is loaded into memory at startup;
  the very first inference also warms caches. Subsequent dictations are much
  faster. Smaller models (`base.en`, `tiny.en`) trade accuracy for speed.
- **Paste comes out as old clipboard text** — increase `paste.restore_delay`.

## Roadmap

Planned but not yet implemented:

- LLM post-processing to clean up filler words and punctuation
- A configuration UI

## License

[MIT](LICENSE)
