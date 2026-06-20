# Changelog

All notable changes to TRD Speak are documented here. This project adheres to
[Semantic Versioning](https://semver.org/).

## 0.1.0 — first release

Minimal push-to-talk dictation for macOS that runs entirely on your machine —
no cloud, no accounts. Hold a hotkey, speak, release; your words are
transcribed locally with [faster-whisper](https://github.com/SYSTRAN/faster-whisper)
and pasted at the cursor.

### Features

- **Push-to-talk dictation.** Hold the hotkey (default **Ctrl+Shift**), speak,
  release — the audio is transcribed locally and pasted into the focused app.
- **Recent Dictations.** The menu lists your last 10 dictations (newest first);
  click one to copy its full text. History is in-memory and cleared on quit.
- **Re-paste the last dictation.** A separate global shortcut (default
  **Cmd+Ctrl**): tap it to insert your most recent dictation into whatever
  window now has focus — for when a dictation landed in the wrong place.
- **Configuration panel.** Change the dictate and re-paste shortcuts from a
  small UI (no editing `config.toml`); changes apply immediately and persist.
- **Menu-bar + Dock app.** A status icon shows the state (⚠️ permissions, ⏳
  loading, 🎤 ready, 🔴 recording, ✍️ transcribing); logs at
  `~/Library/Logs/trd-speak.log`.
- **Fully local & offline.** The default `base.en` Whisper model is embedded in
  the app; nothing leaves your machine and no download is needed at first run.

### System requirements

- **macOS 12.0 (Monterey) or later.**
- **Apple Silicon (arm64) only.** Intel Macs are not supported by the
  distributed app in 0.1. (Running from source via `./run.sh` still works on
  Intel.)

### Permissions

On first launch the menu-bar icon is **⚠️** and the menu guides you through
three macOS permissions, one step at a time. All three are required:

1. **Microphone** — to record while the hotkey is held.
2. **Accessibility** — to paste the transcription into the focused app.
3. **Input Monitoring** — to detect the global hotkey.

### Install

Open `TRDSpeak.dmg`, drag **TRD Speak** to **Applications**, and launch it.
The app is signed with a Developer ID and notarized by Apple, so it opens with
no Gatekeeper warning. Grant the three permissions via the ⚠️ menu.
