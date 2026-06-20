# CI release (GitHub Actions)

`.github/workflows/release.yml` builds, signs, notarizes and staples the app
with `make_release.sh` on a `macos-15` runner, then publishes a GitHub Release
with the notarized `TRDSpeak.dmg` attached.

This mirrors the local release (see `RELEASING.md`); the only difference is that
the Developer ID signing key and the notary credentials come from **repository
secrets** instead of your login keychain.

## Triggers

- **Push a `v*` tag** (e.g. `git push origin v0.2.0`) → that tag is released
  automatically.
- **Manual run** (Actions tab → *Release* → *Run workflow*) → releases an
  **existing** tag you type in. Use this for `v0.1.0`, whose tag was pushed
  before this workflow existed.

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

- The runner imports the cert into a temporary keychain, sets it as default so
  `notarytool --keychain-profile trd-notary` finds the stored credential, then
  runs `make_release.sh` unchanged.
- The Whisper model is fetched during the build (`make_release.sh` calls
  `scripts/fetch_model.py`), so no model needs to be committed.
- A run takes ~20–40 min (dependency install + PyInstaller + two notarization
  round-trips).
- The signing identity **name** is set in the workflow env (`CODESIGN_IDENTITY`)
  and is not secret; only the private key (in the `.p12`) is.
