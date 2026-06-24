"""The dev build and the production build must use disjoint storage.

Sharing config / engine state / lock / log between 'TRD Speak' and
'TRD Speak Dev' lets one build overwrite the other's settings and makes the log
ambiguous about which binary ran. flow.paths._derive is the single source of
truth; these tests pin the isolation it must guarantee.
"""

import flow.paths as paths


def test_production_and_dev_storage_are_disjoint():
    prod = paths._derive("TRD Speak")
    dev = paths._derive("TRD Speak Dev")
    for key in ("support", "log", "lock", "hotkeys", "engine", "dictations"):
        assert str(prod[key]) != str(dev[key]), f"{key} is shared between builds"


def test_dev_paths_are_clearly_the_dev_build():
    dev = paths._derive("TRD Speak Dev")
    assert "TRD Speak Dev" in str(dev["support"])
    assert "TRD Speak Dev" in str(dev["hotkeys"])
    assert "TRD Speak Dev" in str(dev["dictations"])
    assert dev["log"].endswith("trd-speak-dev.log")


def test_production_paths_unchanged():
    prod = paths._derive("TRD Speak")
    assert str(prod["support"]).endswith("/Library/Application Support/TRD Speak")
    assert prod["log"].endswith("/Library/Logs/trd-speak.log")
    assert str(prod["hotkeys"]).endswith("/TRD Speak/hotkeys.json")


def test_without_a_bundle_defaults_to_production_storage():
    # A (non-frozen) test run has no .app bundle, so it must read as production
    # and never accidentally adopt the dev storage (or relocate production's).
    assert paths._is_dev_bundle() is False
    assert paths.IS_DEV is False
    assert paths.APP_NAME == "TRD Speak"


def test_only_the_dev_build_is_relocated():
    # Production storage name must stay exactly "TRD Speak" so an installed
    # production build's existing config is never orphaned.
    assert paths._app_name(is_dev=False) == "TRD Speak"
    assert paths._app_name(is_dev=True) == "TRD Speak Dev"
