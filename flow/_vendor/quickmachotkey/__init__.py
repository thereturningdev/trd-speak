"""Vendored subset of quickmachotkey 2025.7.28 (MIT, Glyph Lefkowitz).

Upstream: https://pypi.org/project/quickmachotkey/  — LICENSE preserved
alongside this file.

Vendored VERBATIM: ``_MinimalHIToolbox/`` (the PyObjC bridge metadata for the
Carbon HIToolbox hotkey APIs — the valuable, hard-to-recreate part) and
``constants.py`` (VirtualKey/ModifierKey constants). Omitted: upstream's
``configurators/`` (JSON persistence we do not use) and ``py.typed``.

PATCHED: this ``__init__.py`` replaces upstream's. Two reasons this is a
vendored patch rather than a pip dependency with a subclass (issue #23):

1. Upstream installs, AT IMPORT TIME, a Carbon event handler for
   kEventHotKeyPressed ONLY, whose catch-all callback returns noErr (0,
   "handled") even for hotkey IDs it does not own — importing it alongside
   our own handler would let it swallow our events, and it can never deliver
   kEventHotKeyReleased, which push-to-talk needs. The released-events
   extension therefore requires replacing the module-level handler, not
   subclassing around it.
2. Vendoring under flow/ guarantees PyInstaller bundles it via the existing
   ``collect_submodules("flow")`` in TRDSpeak.spec — no hidden-import risk.

The press+release event handler and the hotkey classes live in
flow.carbon_hotkey, built directly on ``_MinimalHIToolbox``.
"""
