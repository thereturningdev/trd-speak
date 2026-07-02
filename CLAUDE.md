# CLAUDE.md

## Non-negotiable requirements

- Every requirement in the documents under [nfr/](./nfr/) is non-negotiable (see the
  "Non-Functional Requirements (MANDATORY)" section below for the full list).
- These requirements apply to every single interaction with the user, without exception.
- Read and comply with them before responding.
- Every ground rule below is equally non-negotiable.

## Ground rules

1. **Functional-test every feature.** For every new feature you implement, write and run an
   automated functional test that exercises the feature's real behaviour (not just unit tests of
   helpers). Every functional test must pass before you may declare the feature complete.

2. **Builds are automated — development and production.**
   - The **development build** is created on the development machine, by a committed, repeatable
     script.
   - The **production build** is created only by the release workflow running as a GitHub Action.
     The user invokes that workflow on GitHub and specifies the release number; production builds
     are never produced locally.
   - The development build's version is the **latest stable release number published on GitHub
     with `+dev` appended** (e.g. latest stable `0.1.3` → development build `0.1.3+dev`). Derive it
     from the latest published release/tag — never hand-type it.

3. **Investigate via online research, not memory.** When the user asks you to investigate an issue
   — a bug in the code, or the feasibility of an approach — do not trust your own knowledge; it is
   likely wrong. Spin up **at least two agents** to search online for how other developers have
   handled that issue, and base your conclusions on their findings.

4. **Never modify the user's machine or application configuration.** You must not change the
   user's machine configuration or the settings of their applications. The **only** exception is
   while running automated tests, during which you may configure the machine and run software. When
   you deliver a development build, provide **only the application** — do not install it, do not
   configure it, do not grant it permissions. The user installs, configures, and tests it.

<!-- nfr:start -->
## Non-Functional Requirements (MANDATORY)

These requirements are mandatory for all work in this project. Read each file
and follow it:

- [NFR-general](nfr/NFR-general.md)
- [NFR-development](nfr/NFR-development.md)
- [NFR-python](nfr/NFR-python.md)
- [NFR-desktop-app](nfr/NFR-desktop-app.md)
<!-- nfr:end -->
