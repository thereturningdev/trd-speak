import threading
import time

import numpy as np
import pytest

import flow.app as app_mod
from flow.app import App, IDLE, LOADING, PROCESSING, RECORDING
from flow.config import Config
from flow.engines import EngineUnavailable, Transcriber


def _wait_until(predicate, timeout=5.0):
    """Spin until predicate() is true or the timeout elapses."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


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
    new = FakeEngine("other")
    app.transcriber = old
    app.engine_name = "whisper"
    monkeypatch.setattr(app_mod, "make_transcriber", lambda name, cfg: new)

    app.set_engine("other")
    app._switch_thread.join(timeout=5)

    assert app.transcriber is new
    assert app.engine_name == "other"
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

    app.set_engine("other")

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

    app.set_engine("other")
    app._switch_thread.join(timeout=5)

    assert app.transcriber is old
    assert app.engine_name == "whisper"
    assert app._state == IDLE
    assert notes


# --- recording must never block the hotkey (event-tap) callback ----------


class _BlockingRecorder:
    """Recorder whose start() blocks until released — mimics a stalled
    PortAudio device. stop() records call order so tests can assert that
    stop never runs before start has finished."""

    def __init__(self):
        self.order = []
        self.release = threading.Event()
        self.started = threading.Event()

    def start(self):
        self.order.append("start-begin")
        self.started.set()
        assert self.release.wait(5), "start() was never released"
        self.order.append("start-end")

    def stop(self):
        self.order.append("stop")
        return np.zeros(16000, dtype=np.float32)


def test_on_activate_returns_without_waiting_for_recorder_start(app):
    """The hotkey combo fires _on_activate on the macOS main run-loop thread;
    it MUST return immediately even if recorder.start() stalls, or the run
    loop freezes and macOS disables the event tap."""
    rec = _BlockingRecorder()
    app.recorder = rec

    t = threading.Thread(target=app._on_activate)
    t.start()
    t.join(timeout=1.0)
    returned_promptly = not t.is_alive()

    rec.release.set()  # let the (offloaded) start() finish
    t.join(timeout=5)

    assert returned_promptly, "_on_activate blocked on recorder.start()"
    assert rec.started.wait(2), "recording never actually started"


def test_release_during_slow_start_still_stops_after_start(app, monkeypatch):
    """If the user releases the combo while recorder.start() is still running,
    stop() must run only AFTER start() completes (no start/stop race)."""
    pasted = []
    monkeypatch.setattr(
        app_mod, "paste_text", lambda text, restore_delay=0: pasted.append(text)
    )
    app.can_paste = lambda: True
    app.hotkey.wait_all_released = lambda timeout=2.0: True
    rec = _BlockingRecorder()
    app.recorder = rec
    app.transcriber = FakeEngine("whisper")  # transcribe() -> "x"

    t = threading.Thread(target=app._on_activate)
    t.start()
    assert _wait_until(lambda: rec.order == ["start-begin"], 2)

    app._on_deactivate()  # user releases before start() has finished
    rec.release.set()  # start() now completes
    t.join(timeout=5)

    assert _wait_until(lambda: app._state == IDLE, 5)
    assert rec.order == ["start-begin", "start-end", "stop"]
    assert pasted == ["x "]


def test_full_dictation_cycle_records_transcribes_and_pastes(app, monkeypatch):
    """End-to-end happy path across the new threading: activate -> deactivate
    -> transcribe -> paste -> back to IDLE."""
    pasted = []
    monkeypatch.setattr(
        app_mod, "paste_text", lambda text, restore_delay=0: pasted.append(text)
    )
    app.can_paste = lambda: True
    app.hotkey.wait_all_released = lambda timeout=2.0: True

    class FakeRec:
        def start(self):
            pass

        def stop(self):
            return np.zeros(16000, dtype=np.float32)

    app.recorder = FakeRec()
    app.transcriber = FakeEngine("whisper")

    app._on_activate()
    assert _wait_until(lambda: app._state == RECORDING, 2)
    app._on_deactivate()

    assert _wait_until(lambda: app._state == IDLE, 5)
    assert pasted == ["x "]
    assert app.history.items() == ["x"]  # captured (raw, no trailing space)


def test_empty_transcription_is_not_recorded(app, monkeypatch):
    """A 'heard nothing' result must not land in the history."""
    monkeypatch.setattr(app_mod, "paste_text", lambda text, restore_delay=0: None)
    app.can_paste = lambda: True
    app.hotkey.wait_all_released = lambda timeout=2.0: True

    class FakeRec:
        def start(self):
            pass

        def stop(self):
            return np.zeros(16000, dtype=np.float32)

    app.recorder = FakeRec()
    silent = FakeEngine("whisper")
    silent.transcribe = lambda audio: ""  # heard nothing
    app.transcriber = silent

    app._on_activate()
    assert _wait_until(lambda: app._state == RECORDING, 2)
    app._on_deactivate()

    assert _wait_until(lambda: app._state == IDLE, 5)
    assert app.history.items() == []


def test_paste_skipped_dictation_is_still_recorded(app, monkeypatch):
    """A dictation that fails to paste (Accessibility missing, trigger keys
    still held) must STILL be captured — those are prime recovery cases."""
    pasted = []
    monkeypatch.setattr(
        app_mod, "paste_text", lambda text, restore_delay=0: pasted.append(text)
    )
    app.can_paste = lambda: False  # paste refused
    app.hotkey.wait_all_released = lambda timeout=2.0: True

    class FakeRec:
        def start(self):
            pass

        def stop(self):
            return np.zeros(16000, dtype=np.float32)

    app.recorder = FakeRec()
    app.transcriber = FakeEngine("whisper")  # transcribe -> "x"

    app._on_activate()
    assert _wait_until(lambda: app._state == RECORDING, 2)
    app._on_deactivate()

    assert _wait_until(lambda: app._state == IDLE, 5)
    assert pasted == []  # never pasted
    assert app.history.items() == ["x"]  # but recorded


# --- re-paste-last-dictation hotkey --------------------------------------


def _repaste_ready(app, monkeypatch):
    """Wire an app so _do_repaste can run deterministically; return `pasted`."""
    pasted = []
    monkeypatch.setattr(
        app_mod, "paste_text", lambda text, restore_delay=0: pasted.append(text)
    )
    app.can_paste = lambda: True
    app.repaste_hotkey.wait_all_released = lambda timeout=2.0: True
    app._state = IDLE
    return pasted


def test_repaste_pastes_most_recent_dictation(app, monkeypatch):
    """The hotkey re-pastes the newest dictation (with a trailing space)."""
    pasted = _repaste_ready(app, monkeypatch)
    app.history.add("first")
    app.history.add("second")

    app._do_repaste()

    assert pasted == ["second "]
    assert app._state == IDLE


def test_repaste_noop_on_empty_history(app, monkeypatch):
    """Nothing to re-paste: nothing pasted, the user is notified."""
    pasted = _repaste_ready(app, monkeypatch)
    notes = []
    app.notify = notes.append

    app._do_repaste()

    assert pasted == []
    assert notes  # told there was nothing
    assert app._state == IDLE


def test_repaste_skipped_without_paste_permission(app, monkeypatch):
    """No Accessibility permission: refuse, as the dictation flow does."""
    pasted = _repaste_ready(app, monkeypatch)
    app.can_paste = lambda: False
    app.history.add("hello")

    app._do_repaste()

    assert pasted == []
    assert app._state == IDLE


def test_repaste_skipped_when_busy(app, monkeypatch):
    """A re-paste must never race an in-flight dictation's clipboard work."""
    pasted = _repaste_ready(app, monkeypatch)
    app.history.add("hello")
    notes = []
    app.notify = notes.append
    app._state = PROCESSING  # mid-dictation

    app._do_repaste()

    assert pasted == []
    assert notes
    assert app._state == PROCESSING  # untouched


def test_on_repaste_pastes_via_worker_thread(app, monkeypatch):
    """_on_repaste runs on the run-loop thread: it must offload and return,
    and the worker performs the paste."""
    pasted = _repaste_ready(app, monkeypatch)
    app.history.add("recovered")

    app._on_repaste()  # spawns the worker

    assert _wait_until(lambda: pasted == ["recovered "], 5)
    assert _wait_until(lambda: app._state == IDLE, 5)
