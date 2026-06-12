# Getting started with local-flow

This guide takes you from nothing to dictating into any app on your Mac in
about 10 minutes (most of it is a one-time model download). No cloud, no
account — everything runs on your machine.

## What you'll end up with

Hold **Ctrl+Alt**, speak, release — your words appear at your cursor, in any
app: a terminal, a browser, a document. A 🎤 icon in the menu bar shows the
app is running.

## 1. Prerequisites (one time)

You need three things. Open **Terminal** (⌘-Space, type "Terminal") and check
each:

**Python 3.11 or newer**

```sh
python3 --version
```

If that prints 3.11.x or higher, you're fine. Otherwise install it with
[Homebrew](https://brew.sh):

```sh
brew install python@3.12
```

**Xcode Command Line Tools** (compiles the app's tiny launcher)

```sh
xcode-select --install
```

If they're already installed it says so — that's fine.

**~1 GB of free disk** for the speech-recognition model.

## 2. Install

```sh
git clone <repo-url> local-flow
cd local-flow
./setup.sh
```

`setup.sh` creates an isolated Python environment, installs the
dependencies, and downloads the speech model (~500 MB — this is the slow
part, one time only). When it finishes:

```sh
./make_app.sh
open LocalFlow.app
```

> **Don't move the folder afterwards.** The app remembers where it was
> built; if you relocate the folder, run `./make_app.sh` again.

## 3. Grant the three permissions

macOS requires your explicit permission for the three things a dictation
app does: hear you (Microphone), see your hotkey (Input Monitoring), and
type for you (Accessibility).

When the app starts you'll see a **⚠️ icon in the menu bar** (top-right of
your screen) and one notification. Nothing else pops up — the ⚠️ menu itself
walks you through setup, **one step at a time**:

1. **Click the ⚠️ icon.** The menu says "Setup — step 1 of 3: Microphone"
   and exactly one row is clickable. Click it → macOS asks → **Allow**.
2. The menu advances by itself within a couple of seconds. **Step 2:
   Accessibility** — click the row, follow macOS's dialog to System
   Settings, switch on **LocalFlow**.
3. **Step 3: Input Monitoring** — same: click the row, switch on
   **LocalFlow** in System Settings. If macOS asks to **quit and reopen**
   the app, accept — that's the only restart in the whole process.

When the icon turns **🎤**, you're done. These grants survive reboots; you
do this once.

(Granted something in the wrong order? Denied a prompt by mistake? Don't
worry — the menu always shows exactly the step that still needs fixing.)

## 4. Dictate

1. Click into any text field (your editor, browser, terminal, chat…).
2. **Hold Ctrl+Alt** — the menu bar icon turns 🔴 and the mic indicator
   lights up.
3. Speak normally.
4. **Release** — the icon shows ✍️ for a moment, then your words are pasted
   at the cursor.

A few seconds of speech transcribes in about a second; long dictations take
proportionally longer after release. Maximum recording length is 3 minutes.

## 5. Make it yours (optional)

Settings live in `config.toml`. The file is optional — create it from the
template:

```sh
cp config.toml.example config.toml
```

The two settings most people change:

```toml
[hotkey]
keys = ["ctrl", "alt"]    # e.g. ["ctrl", "shift"] or ["f19"]

[whisper]
model = "small.en"        # "base.en" = faster, "medium.en" = more accurate
```

Restart the app (menu bar icon → Quit LocalFlow, then `open LocalFlow.app`)
to apply changes. The full option list is in the
[README](README.md#configuration).

**Start at login:** System Settings → General → Login Items → add
LocalFlow.

## Day-to-day

| What | How |
| --- | --- |
| Start | `open LocalFlow.app` (or double-click it in Finder) |
| Stop | menu bar icon → **Quit LocalFlow** (or `./stop.sh`) |
| Is it running? | 🎤 in the menu bar (and a Dock icon) |
| See what it's doing | menu bar icon → **Open Log** |

## If something doesn't work

The menu bar icon tells you most of it: **⚠️ means a permission is missing**
— click it and the menu shows exactly which one and fixes it in one click.
For everything else (slow transcription, paste oddities, no audio), see the
[Troubleshooting section of the README](README.md#troubleshooting), and the
log (menu bar icon → Open Log) reports each dictation cycle and each
permission by name.

## Uninstall

```sh
./stop.sh
```

Then delete the `local-flow` folder, remove LocalFlow from
System Settings → Privacy & Security (Microphone, Input Monitoring,
Accessibility), and optionally delete the downloaded model:
`rm -rf ~/.cache/huggingface/hub/models--Systran--faster-whisper-small.en`.
