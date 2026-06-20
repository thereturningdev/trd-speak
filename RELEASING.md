# Releasing LocalFlow

Release process for the **0.1** milestone. Tracked in issues #5–#17.

## Locked decisions

- **Target architecture:** arm64 only (Apple Silicon). Intel is not supported in 0.1.
- **Bundling tool:** PyInstaller. Best native-dependency handling for ctranslate2/faster-whisper and PortAudio; PyObjC supported via PyInstaller hooks.
- **Distribution channel:** Developer ID + notarization (direct download DMG). Not the Mac App Store — the app needs Input Monitoring, Accessibility, and a global CGEventTap, which the sandbox forbids.
- **Signing identity:** `Developer ID Application: Filippo Diotalevi (2FV8WB29XC)`.
- **Notary profile:** `trd-notary` (keychain profile, Team `2FV8WB29XC`).

## Two builds

### Dev build — `./make_app.sh`

- For local testing on the developer's own machine only.
- Ad-hoc signed (`codesign --sign -`).
- Repo-linked: the launcher `chdir`s to the repo and runs the repo `.venv` via `PYTHONEXECUTABLE`.
- Not distributable (depends on the repo and `.venv` existing at a fixed path).

### Distribution build — `./make_release.sh` (to be implemented, #14)

- For distribution to end users.
- Self-contained: PyInstaller bundles Python + all dependencies into `LocalFlow.app`; no repo or `.venv` required.
- arm64 only.
- Developer ID-signed with Hardened Runtime + secure timestamp + entitlements.
- Notarized with `trd-notary` and stapled.
- Packaged as a signed, notarized, stapled `LocalFlow.dmg`.

## Pipeline (distribution)

1. Self-contained bundle (PyInstaller) — #7
2. Native deps verified relocatable — #8
3. Whisper model download-on-first-run — #9
4. Hardened Runtime entitlements + Info.plist — #10
5. Developer ID code-signing (inside-out) — #11
6. Notarize + staple the app — #12
7. Build + sign + notarize + staple the DMG — #13
8. `make_release.sh` codifies steps 1–7 — #14
9. Version 0.1.0, notes, tag — #15
10. Clean-machine QA — #16
11. Publish GitHub Release — #17
