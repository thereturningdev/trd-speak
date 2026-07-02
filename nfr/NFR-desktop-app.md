
## Development and Stable build

**Builds are automated — development and production.**
   - The **development build** is created on the development machine, by a committed, repeatable script.
   - The **production build** is created only by the release workflow running as a GitHub Action. The user invokes that workflow on GitHub and specifies the release number; production builds are never produced locally.
   - The **production build** produced on Github must be delivered as DMG file and signed (both the application and the DMG file) with the Apple Developer user key
   - The development build's version is the **latest stable release number published on GitHub with `+dev` appended** (e.g. latest stable `0.1.3` → development build `0.1.3+dev`). Derive it from the latest published release/tag — never hand-type it.


## Testing

**Functional-test every feature.** For every new feature you implement, write and run an
   automated functional test that exercises the feature's real behaviour (not just unit tests of
   helpers). Every functional test must pass before you may declare the feature complet


## Bundling a desktop app: prove the package is complete 

This applies to **any** distributable desktop app — a macOS `.app`, a Windows
installer/`.exe`, a Linux AppImage/`.deb`, an Electron/Tauri package, etc. — and to
everything it must carry: libraries, frameworks, plugins, runtimes, fonts, data files,
locales, models, any resource the app loads at runtime.

YOU MUST ENSURE ALL THE NECESSARY DEPENDENCIES ARE BUNDLE IN THE APPLICATION.
1. **Build the package from a clean tree for releases.** Stale incremental build output
   silently omits the resources/libraries of newly-added dependencies. Don't package over a
   build dir of unknown age.
2. **Make packaging self-verifying** Maintain an explicit list of the
   libraries/frameworks/resources the app *must* contain, and after assembling the package,
   assert each one is actually present in the final bundle. **Fail the build loudly** if any
   is missing
3. **"It signed / notarized / linted / CI-passed" is NOT evidence it runs.** Never report a
   build as done on the strength of those steps alone.
