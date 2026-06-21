import sys

from flow.menubar import _bundle_path


def test_bundle_path_none_when_not_frozen():
    # ./run.sh dev mode: there is no .app bundle around the interpreter.
    assert not getattr(sys, "frozen", False)
    assert _bundle_path() is None


def test_bundle_path_resolves_app_from_frozen_executable(monkeypatch):
    # Frozen build: executable is <App>.app/Contents/MacOS/<exe>; the bundle is
    # three levels up. Guards the TRDSPEAK_BUNDLE-env removal (the env var that
    # only the old make_app.sh launcher set).
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(
        sys,
        "executable",
        "/Applications/TRD Speak Dev.app/Contents/MacOS/TRDSpeak",
    )
    assert str(_bundle_path()) == "/Applications/TRD Speak Dev.app"
