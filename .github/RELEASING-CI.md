# CI release (GitHub Actions)

`.github/workflows/release.yml` builds, signs, notarizes and staples the app
with `make_release.sh` on a `macos-14` runner, then attaches the notarized
`TRDSpeak.dmg` to a **draft** GitHub Release. Same flow as the TRD Workbench
project.

This mirrors the local release (see `RELEASING.md`); the only difference is that
the Developer ID signing key and the notary credentials come from **repository
secrets** instead of your login keychain.

## How to release

1. **Actions** tab → **Release** → **Run workflow**.
2. Enter the version, e.g. `0.1.1` (no leading `v`), and run.
3. The run builds + signs + notarizes, then creates a **draft** release
   `v<version>` with `TRDSpeak.dmg` attached. `gh` creates the tag from the
   version — you do **not** tag anything by hand, and there is no tag trigger.
4. Open the draft release, download the DMG, test it on a clean Mac.
5. Click **Publish release** to go public.

There is intentionally **no tag-push trigger** — releases only ever start from a
manual run with an explicit version, which avoids duplicate/racing runs.

## One-time setup: repository secrets

Add these under **Settings → Secrets and variables → Actions → New repository
secret**.

| Secret | What it is |
|--------|------------|
| `MACOS_CERT_P12_BASE64` | Developer ID Application certificate **and private key**, exported as `.p12`, base64-encoded |
| `MACOS_CERT_PASSWORD` | the password you set when exporting the `.p12` |
| `AC_API_KEY_P8` | App Store Connect API key — the **contents** of the `AuthKey_XXXX.p8` file |
| `AC_API_KEY_ID` | that key's **Key ID** |
| `AC_API_ISSUER_ID` | that key's **Issuer ID** |

The three `AC_API_*` values are the **same App Store Connect API key you already
use for notarization in your other project** — reuse those exact values (or
promote them to Organization secrets so both repos share them). Notarization
uses this API key, not an app-specific password.

### Producing `MACOS_CERT_P12_BASE64`

1. **Keychain Access** → **login** keychain → **My Certificates**.
2. Select **Developer ID Application: Filippo Diotalevi (2FV8WB29XC)** (expand it
   and confirm it has a private key under it).
3. Right-click → **Export…** → save as `cert.p12`, set an export password (that
   password becomes `MACOS_CERT_PASSWORD`).
4. Base64-encode it for the secret value:

   ```sh
   base64 -i cert.p12 | pbcopy   # now paste into MACOS_CERT_P12_BASE64
   ```

5. Delete `cert.p12` afterwards.

## Notes

- The dispatched version is passed to the build (`TRDSPEAK_VERSION`), which
  stamps it into `flow/__init__.py` and the app's `Info.plist` — the tag is the
  single source of truth for the version.
- The runner imports the cert into a temporary keychain, sets it as default so
  `notarytool --keychain-profile trd-notary` finds the stored credential, then
  runs `make_release.sh` unchanged.
- The Whisper model is fetched during the build (`make_release.sh` calls
  `scripts/fetch_model.py`), so no model needs to be committed.
- A run takes roughly 5–10 min (the bundle is large: bundled CPython + ML stack
  + the ~140 MB model, notarized twice).
- The signing identity **name** is set in the workflow env (`CODESIGN_IDENTITY`)
  and is not secret; only the private key (in the `.p12`) is.
