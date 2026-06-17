import pytest

import flow.app as app_mod
from flow.app import App, IDLE, LOADING, PROCESSING
from flow.config import Config
from flow.engines import EngineUnavailable, Transcriber


class FakeEngine(Transcriber):
    def __init__(self, name, fail=False):
        self.name = name
        self.label = f"fake-{name}"
        self.loaded = False
        self.unloaded = False
        self._fail = fail

    def load(self):
        if self._fail:
            raise EngineUnavailable("nope")
        self.loaded = True

    def transcribe(self, audio):
        return "x"

    def unload(self):
        self.unloaded = True


@pytest.fixture
def app(monkeypatch, tmp_path):
    # Persist into a temp state file, not the real home dir.
    monkeypatch.setattr(
        app_mod.engine_state,
        "save_engine",
        lambda name, path=tmp_path / "engine": path.write_text(name),
    )
    return App(Config())


def test_switch_from_idle_swaps_and_unloads_old(app, monkeypatch):
    old = FakeEngine("whisper")
    new = FakeEngine("parakeet")
    app.transcriber = old
    app.engine_name = "whisper"
    monkeypatch.setattr(app_mod, "make_transcriber", lambda name, cfg: new)

    app.set_engine("parakeet")
    app._switch_thread.join(timeout=5)

    assert app.transcriber is new
    assert app.engine_name == "parakeet"
    assert new.loaded
    assert old.unloaded
    assert app._state == IDLE


def test_switch_refused_while_processing(app, monkeypatch):
    notes = []
    app.notify = notes.append
    app._state = PROCESSING
    app.engine_name = "whisper"
    called = []
    monkeypatch.setattr(
        app_mod, "make_transcriber", lambda name, cfg: called.append(name)
    )

    app.set_engine("parakeet")

    assert app.engine_name == "whisper"
    assert called == []  # never tried to build
    assert notes  # user was told


def test_failed_load_reverts(app, monkeypatch):
    old = FakeEngine("whisper")
    app.transcriber = old
    app.engine_name = "whisper"
    notes = []
    app.notify = notes.append
    monkeypatch.setattr(
        app_mod, "make_transcriber", lambda name, cfg: FakeEngine(name, fail=True)
    )

    app.set_engine("parakeet")
    app._switch_thread.join(timeout=5)

    assert app.transcriber is old
    assert app.engine_name == "whisper"
    assert app._state == IDLE
    assert notes
