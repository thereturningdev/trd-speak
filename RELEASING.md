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

### Distribution build — `./make_release.sh`

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

## One command (#14)

`./make_release.sh` runs the whole distribution pipeline (steps 1–7 above) from a
clean tree and leaves a notarized, stapled `dist/LocalFlow.dmg`:

```sh
./make_release.sh
```

Prerequisites (one-time):

- `./setup.sh` — creates `.venv`; then `pip install -r requirements-build.txt` (PyInstaller).
- Developer ID Application cert imported into the login keychain.
- notarytool keychain profile `trd-notary` (`xcrun notarytool store-credentials`).
- Xcode Command Line Tools.

No secrets are stored in the repo — the cert lives in the keychain and the notary
credential in the keychain profile. Override the names if needed:
`CODESIGN_IDENTITY=… NOTARY_PROFILE=… ./make_release.sh`.

It self-checks at the end (`stapler validate` + `spctl` on both the app and the
DMG). The model is fetched automatically if `models/faster-whisper-base.en` is
missing. The step scripts (`sign_app.sh`, `notarize_app.sh`, `make_dmg.sh`) can
also be run individually.

## Code-signing (#11)

`./sign_app.sh [path]` signs the bundle **inside-out** with the Developer ID
Application cert, Hardened Runtime (`--options runtime`), and a secure timestamp:
every nested `.so`/`.dylib` and the embedded `Python.framework` first, then
`LocalFlow.app` last. No `--deep` — nested code is signed explicitly. The final
sign attaches `entitlements.plist` (codesign puts it on the main executable).

- Identity: `$CODESIGN_IDENTITY` (defaults to the Developer ID above).
- `entitlements.plist` grants three Hardened Runtime relaxations the embedded
  CPython needs for its dlopen'd native deps (ctranslate2, onnxruntime, ffmpeg,
  PortAudio): `allow-unsigned-executable-memory`, `allow-jit`,
  `disable-library-validation`.
- **Keep `entitlements.plist` comment-free** — codesign's AMFI plist parser
  rejects XML comments (`AMFIUnserializeXML: syntax error`).
- Verify: `codesign --verify --deep --strict --verbose=2 LocalFlow.app`.

## Notarize & staple (#12)

`./notarize_app.sh [path]` zips the signed app (`ditto -c -k --keepParent`),
submits it to Apple's notary service and waits, then staples the ticket so the
app launches on a clean Mac offline. Runs only on a Hardened-Runtime-signed
bundle (it checks first).

- Profile: `$NOTARY_PROFILE` notarytool keychain profile (default: `trd-notary`).
- On a non-`Accepted` verdict it prints `xcrun notarytool log <id>` and exits 1.
- Verify: `xcrun stapler validate LocalFlow.app` and
  `spctl -a -vvv -t exec LocalFlow.app` → `Notarized Developer ID`.

## Distributable DMG (#13)

`./make_dmg.sh [path]` packages the notarized + stapled app into
`dist/LocalFlow.dmg`: it stages the app plus an `/Applications` drop-target
symlink, builds a compressed UDZO DMG (`hdiutil`), signs the DMG with the
Developer ID cert, notarizes the DMG directly (no zip), and staples it.

- Requires the payload app to already be stapled (it checks).
- Verify: `xcrun stapler validate LocalFlow.dmg` and
  `spctl -a -t open --context context:primary-signature LocalFlow.dmg` →
  `Notarized Developer ID`.
