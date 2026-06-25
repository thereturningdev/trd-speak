# Getting started with TRD Speak

This guide takes you from nothing to dictating into any app on your Mac in
about 10 minutes (most of it is a one-time model download). No cloud, no
account — everything runs on your machine.

## What you'll end up with

Hold **Ctrl+Shift**, speak, release — your words appear at your cursor, in any
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
git clone <repo-url> trd-speak
cd trd-speak
./setup.sh
```

`setup.sh` creates an isolated Python environment, installs the
dependencies, and downloads the speech model (~500 MB — this is the slow
part, one time only). When it finishes:

```sh
./make_dev_app.sh
open "dist/TRD Speak Dev.app"
```

> The build is self-contained, so its location does not matter. Drag
> **TRD Speak Dev.app** from `dist/` (or open `dist/TRD Speak Dev.dmg`) to
> /Applications to install it. Each rebuild is ad-hoc signed afresh, so macOS
> may ask you to re-grant permissions after you install a new build.

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
   Settings, switch on **TRD Speak**.
3. **Step 3: Input Monitoring** — same: click the row, switch on
   **TRD Speak** in System Settings. If macOS asks to **quit and reopen**
   the app, accept — that's the only restart in the whole process.

When the icon turns **🎤**, you're done. These grants survive reboots; you
do this once.

(Granted something in the wrong order? Denied a prompt by mistake? Don't
worry — the menu always shows exactly the step that still needs fixing.)

## 4. Dictate

1. Click into any text field (your editor, browser, terminal, chat…).
2. **Hold Ctrl+Shift** — the menu bar icon turns 🔴 and the mic indicator
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
keys = ["ctrl", "shift"]  # e.g. ["ctrl", "alt"] or ["f19"]

[whisper]
model = "base.en"         # "tiny.en" = faster, "small.en" = more accurate

[engine]
name = "whisper"          # faster-whisper (the only engine)
```

Restart the app (menu bar icon → Quit TRD Speak, then `open TRDSpeak.app`)
to apply changes. The full option list is in the
[README](README.md#configuration).

To change just the hotkeys, you don't even need the file: menu bar icon →
**Configuration…** (under Recent Dictations) opens a panel where you click a
field, *press* the shortcut you want, and Save. It applies right away and is
remembered next time.

**Start at login:** System Settings → General → Login Items → add
TRD Speak.

## Day-to-day

| What | How |
| --- | --- |
| Start | `open TRDSpeak.app` (or double-click it in Finder) |
| Stop | menu bar icon → **Quit TRD Speak** (or `./stop.sh`) |
| Is it running? | 🎤 in the menu bar (and a Dock icon) |
| See what it's doing | menu bar icon → **Open Log** |
| Recover a lost dictation | menu bar icon → **Recent Dictations** → click an entry to copy it, then paste into the right window |
| Re-paste it into the right window | switch to the right window and **tap Cmd+Ctrl** (press and release, nothing else) to insert your most recent dictation directly |
| Correct a dictation and teach the app | **tap Cmd+Alt** right after dictating → edit the text → Save (the app learns the correction for next time) |
| Review or remove learned rules | menu bar icon → **Learned words** |
| Change a shortcut | menu bar icon → **Configuration…** → click a field, press the combo, Save (applies immediately) |

## Teaching TRD Speak your words (optional)

TRD Speak can learn from your corrections — no cloud, no accounts.

**Correcting a dictation:**

1. After a dictation lands, **tap Cmd+Alt** (or your configured correction shortcut).
2. A small editor opens with the text that was just transcribed. Edit it to what you meant.
3. Click **Save**. The app compares your edit to the original and records what changed.

Next time you say the same thing, it applies the correction automatically.

**How it learns:**

- Words you consistently replace (e.g. "fast whisper" → "faster-whisper") become silent rewrite rules.
- Proper nouns and technical terms (e.g. "Diotalevi", "CTranslate2") are added to the vocabulary so Whisper recognises them better.
- Common-word homophones (e.g. cloud↔Claude) get vocabulary biasing, not hard rewrites — so dictating "it's cloudy today" still works.
- The correction is **learn-only**: it teaches future dictations; it does not re-paste the current text.

**Managing what it has learned:**

- Menu bar icon → **Learned Words** (a submenu): lists every learned rule. Click one to remove it.
- Menu bar icon → **Open Dictionary File…** (a separate item, not inside Learned Words): reveals the JSON file in Finder so you can inspect or hand-edit it.

**The dictionary file** lives at `~/Library/Application Support/TRD Speak[ Dev]/dictionary.json` and survives reinstall. You can also seed it manually — see `dictionary.json.example` in the repo for the format: a `vocabulary` list and a `replacements` list. Each replacement has `from` and `to` (required), plus optional `case_sensitive` (default `false`) and `whole_word` (default `true`; set `false` to match inside larger words). The `learned` and `ts` fields are added automatically by the app for rules it learns — you don't write those.

**Change the shortcut:** menu bar icon → **Configuration…** → click the Correction field, press the combo you want, Save.

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

Then delete the `trd-speak` folder, remove TRD Speak from
System Settings → Privacy & Security (Microphone, Input Monitoring,
Accessibility), and optionally delete the downloaded model:
`rm -rf ~/.cache/huggingface/hub/models--Systran--faster-whisper-base.en`.
