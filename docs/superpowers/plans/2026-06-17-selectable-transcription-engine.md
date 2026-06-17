# Selectable Transcription Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the user pick between faster-whisper and parakeet-mlx and switch engines live from the menu-bar icon, with the choice persisted across restarts.

**Architecture:** Transcription becomes a small `flow/engines/` package behind a `Transcriber` ABC (whisper + parakeet implementations, lazy-imported via a registry/factory). `App` holds the active engine and gains `set_engine()` with a new `LOADING` state that loads+warms the new engine on a worker thread then unloads the old. The menu adds a registry-driven "Transcription Engine" submenu. The choice is stored in a state file that takes precedence over `config.toml`.

**Tech Stack:** Python 3.12, faster-whisper, parakeet-mlx (optional), pyobjc (AppKit/Quartz), pytest (new dev dep), MLX.

---

## File structure

| File | Responsibility |
| --- | --- |
| `flow/engines/__init__.py` (create) | `Transcriber` ABC, `EngineInfo`, `ENGINES` registry, `ENGINE_NAMES`, `EngineUnavailable`, `make_transcriber()`. No heavy imports. |
| `flow/engines/whisper.py` (create) | `WhisperTranscriber` — today's `flow/transcriber.py` code under the ABC. |
| `flow/engines/parakeet.py` (create) | `ParakeetTranscriber` — lazy-import parakeet-mlx, no-ffmpeg array path, warm-up, unload. |
| `flow/transcriber.py` (delete) | Replaced by the package. |
| `flow/engine_state.py` (create) | Persist/resolve engine choice in `~/Library/Application Support/LocalFlow/engine`. |
| `flow/config.py` (modify) | Add `engine` field + `[engine] name` parsing/validation. |
| `flow/app.py` (modify) | Active engine, `LOADING` state, `set_engine()`, startup fallback, `on_engine`/`notify` hooks. |
| `flow/menubar.py` (modify) | Engine submenu, `selectEngine:` action, checkmark refresh, wiring. |
| `setup.sh` (modify) | `--parakeet` flag; engine-aware model pre-download. |
| `requirements-parakeet.txt` (create) | parakeet stack. |
| `requirements-dev.txt` (create) | pytest. |
| `tests/` (create) | Unit tests. |
| `README.md`, `GETTING_STARTED.md` (modify) | "Choosing an engine" section. |

---

## Task 0: Test infrastructure

**Files:**
- Create: `requirements-dev.txt`
- Create: `tests/__init__.py` (empty)
- Create: `tests/conftest.py`

- [ ] **Step 1: Create dev requirements**

`requirements-dev.txt`:
```
pytest>=8
```

- [ ] **Step 2: Install pytest into the venv**

Run: `.venv/bin/python -m pip install -r requirements-dev.txt`
Expected: installs pytest; `.venv/bin/python -m pytest --version` prints a version.

- [ ] **Step 3: Create test package files**

`tests/__init__.py`: empty file.

`tests/conftest.py`:
```python
"""Shared test fixtures."""
import sys
from pathlib import Path

# Make the project root importable when running pytest from anywhere.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
```

- [ ] **Step 4: Verify pytest collects nothing yet (no error)**

Run: `.venv/bin/python -m pytest -q`
Expected: "no tests ran" (exit code 5 is fine).

- [ ] **Step 5: Commit**

```bash
git add requirements-dev.txt tests/__init__.py tests/conftest.py
git commit -m "test: add pytest dev dependency and test scaffold"
```

---

## Task 1: Engine package — ABC, registry, factory, whisper engine

**Files:**
- Create: `flow/engines/__init__.py`
- Create: `flow/engines/whisper.py`
- Delete: `flow/transcriber.py`
- Test: `tests/test_engines.py`

- [ ] **Step 1: Write failing tests**

`tests/test_engines.py`:
```python
import numpy as np
import pytest

from flow.config import Config
from flow.engines import (
    ENGINE_NAMES,
    ENGINES,
    EngineUnavailable,
    Transcriber,
    make_transcriber,
)


def test_registry_lists_both_engines():
    names = [e.name for e in ENGINES]
    assert names == ["whisper", "parakeet"]
    assert ENGINE_NAMES == ("whisper", "parakeet")
    for e in ENGINES:
        assert e.label and e.description


def test_make_whisper_returns_whisper_transcriber():
    from flow.engines.whisper import WhisperTranscriber

    t = make_transcriber("whisper", Config())
    assert isinstance(t, WhisperTranscriber)
    assert isinstance(t, Transcriber)
    assert t.name == "whisper"
    assert t.label  # non-empty


def test_make_unknown_engine_raises():
    with pytest.raises(ValueError):
        make_transcriber("nope", Config())


def test_whisper_too_short_audio_returns_empty_without_loading():
    t = make_transcriber("whisper", Config())
    # 0.1 s < the 0.25 s minimum: must return "" and never touch the model.
    audio = np.zeros(1600, dtype=np.float32)
    assert t.transcribe(audio) == ""
    assert t._model is None


def test_engine_unavailable_is_runtime_error():
    assert issubclass(EngineUnavailable, RuntimeError)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_engines.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'flow.engines'`.

- [ ] **Step 3: Create the engine package base**

`flow/engines/__init__.py`:
```python
"""Pluggable local transcription engines.

This package keeps the heavy, engine-specific imports (CTranslate2, MLX) out
of module import time: the concrete classes are imported lazily inside
``make_transcriber`` so that, e.g., a missing ``parakeet-mlx`` never affects
faster-whisper users. ``App`` depends only on the ``Transcriber`` interface.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass

import numpy as np


class EngineUnavailable(RuntimeError):
    """Raised when an engine cannot be used (e.g. its package isn't installed)."""


class Transcriber(abc.ABC):
    """Common interface for all transcription engines.

    Audio is always 16 kHz mono float32. ``transcribe`` returns "" for audio
    shorter than the engine's minimum (no model work for empty/blip input).
    """

    name: str = ""
    label: str = ""

    @abc.abstractmethod
    def load(self) -> None:
        """Instantiate and warm up the model. Idempotent."""

    @abc.abstractmethod
    def transcribe(self, audio: np.ndarray) -> str:
        """Transcribe 16 kHz mono float32 audio to text."""

    def unload(self) -> None:
        """Release the model and its memory. Idempotent. Default: no-op."""


@dataclass(frozen=True)
class EngineInfo:
    """Static, import-free metadata for building the menu."""

    name: str
    label: str
    description: str


ENGINES: tuple[EngineInfo, ...] = (
    EngineInfo("whisper", "faster-whisper", "CPU · light · default"),
    EngineInfo("parakeet", "Parakeet", "Apple GPU · faster · more accurate"),
)

ENGINE_NAMES: tuple[str, ...] = tuple(e.name for e in ENGINES)


def make_transcriber(name: str, config) -> Transcriber:
    """Return an unloaded engine instance for ``name``.

    Concrete classes are imported here (lazily) so a missing optional engine
    dependency does not break the others.
    """
    if name == "whisper":
        from flow.engines.whisper import WhisperTranscriber

        return WhisperTranscriber(
            model_name=config.model,
            compute_type=config.compute_type,
            beam_size=config.beam_size,
        )
    if name == "parakeet":
        from flow.engines.parakeet import ParakeetTranscriber

        return ParakeetTranscriber()
    raise ValueError(f"unknown engine: {name!r}")
```

- [ ] **Step 4: Create the whisper engine (moved from `flow/transcriber.py`)**

`flow/engines/whisper.py`:
```python
"""faster-whisper engine (CTranslate2, CPU)."""

import logging

import numpy as np

from flow.engines import Transcriber

_SAMPLE_RATE = 16000
_MIN_SECONDS = 0.25

# Quiet the noisy download/progress loggers; warnings and errors still show.
for _name in ("faster_whisper", "huggingface_hub", "ctranslate2"):
    logging.getLogger(_name).setLevel(logging.WARNING)


class WhisperTranscriber(Transcriber):
    """Wraps a faster-whisper model for English transcription on CPU."""

    name = "whisper"

    def __init__(
        self,
        model_name: str = "base.en",
        compute_type: str = "int8",
        beam_size: int = 1,
    ) -> None:
        self.model_name = model_name
        self.compute_type = compute_type
        self.beam_size = beam_size
        self._model = None

    @property
    def label(self) -> str:
        return f"faster-whisper ({self.model_name})"

    def load(self) -> None:
        if self._model is not None:
            return
        from faster_whisper import WhisperModel

        self._model = WhisperModel(
            self.model_name, device="cpu", compute_type=self.compute_type
        )

    def transcribe(self, audio: np.ndarray) -> str:
        if len(audio) < _SAMPLE_RATE * _MIN_SECONDS:
            return ""
        self.load()
        # vad_filter skips non-speech; condition_on_previous_text=False avoids
        # repetition spirals and their expensive temperature re-decodes.
        segments, _info = self._model.transcribe(
            audio,
            language="en",
            beam_size=self.beam_size,
            vad_filter=True,
            condition_on_previous_text=False,
        )
        return "".join(segment.text for segment in segments).strip()

    def unload(self) -> None:
        self._model = None
```

Note: `label` is a property here but a plain class attribute on the ABC — Python allows a subclass property to override a class attribute, and tests only read `t.label`.

- [ ] **Step 5: Delete the old module**

```bash
git rm flow/transcriber.py
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_engines.py -q`
Expected: PASS (5 passed). `test_whisper_too_short...` confirms no model load.

- [ ] **Step 7: Commit**

```bash
git add flow/engines tests/test_engines.py
git commit -m "feat: extract transcription into a pluggable engines package"
```

---

## Task 2: Parakeet engine

**Files:**
- Create: `flow/engines/parakeet.py`
- Test: `tests/test_parakeet.py`

- [ ] **Step 1: Write failing tests**

`tests/test_parakeet.py`:
```python
import importlib.util

import numpy as np
import pytest

from flow.engines import EngineUnavailable, Transcriber, make_transcriber
from flow.config import Config

_HAS_PARAKEET = importlib.util.find_spec("parakeet_mlx") is not None


def test_parakeet_instance_metadata():
    t = make_transcriber("parakeet", Config())
    assert isinstance(t, Transcriber)
    assert t.name == "parakeet"
    assert t.label


def test_parakeet_too_short_returns_empty():
    t = make_transcriber("parakeet", Config())
    assert t.transcribe(np.zeros(1600, dtype=np.float32)) == ""


@pytest.mark.skipif(_HAS_PARAKEET, reason="parakeet-mlx IS installed")
def test_parakeet_load_raises_when_missing():
    t = make_transcriber("parakeet", Config())
    with pytest.raises(EngineUnavailable):
        t.load()


@pytest.mark.skipif(not _HAS_PARAKEET, reason="parakeet-mlx not installed")
def test_parakeet_transcribes_real_clip(tmp_path):
    import wave

    import numpy as np

    # 0.5 s of silence is enough to exercise the no-ffmpeg array path without
    # asserting on recognized words.
    t = make_transcriber("parakeet", Config())
    t.load()
    out = t.transcribe(np.zeros(8000, dtype=np.float32))
    assert isinstance(out, str)
    t.unload()
    assert t._model is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_parakeet.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'flow.engines.parakeet'`.

- [ ] **Step 3: Create the parakeet engine**

`flow/engines/parakeet.py`:
```python
"""Parakeet (NVIDIA Parakeet TDT 0.6B) engine via Apple MLX — runs on the GPU.

parakeet-mlx is an OPTIONAL dependency. It is imported lazily in ``load`` so a
machine without it still runs faster-whisper. parakeet-mlx 0.5.2 only loads
audio from a file path via ffmpeg and exposes no array input; this app feeds
the microphone's already-decoded float32 audio by substituting parakeet's
module-level audio loader with one that returns our pre-set array. Because the
app's state machine serializes transcription, stashing the pending array on the
instance is safe.
"""

import numpy as np

from flow.engines import EngineUnavailable, Transcriber

_SAMPLE_RATE = 16000
_MIN_SECONDS = 0.25
_MODEL = "mlx-community/parakeet-tdt-0.6b-v2"


class ParakeetTranscriber(Transcriber):
    name = "parakeet"
    label = "Parakeet (parakeet-tdt-0.6b, GPU)"

    def __init__(self) -> None:
        self._model = None
        self._mx = None
        self._pending = None  # mx.array stashed for the patched loader

    def load(self) -> None:
        if self._model is not None:
            return
        try:
            import mlx.core as mx
            import parakeet_mlx.parakeet as pk
            from parakeet_mlx import from_pretrained
        except ImportError as exc:
            raise EngineUnavailable(
                "Parakeet isn't installed. Run ./setup.sh --parakeet"
            ) from exc

        if not hasattr(pk, "load_audio"):
            raise EngineUnavailable(
                "Incompatible parakeet-mlx: no load_audio to substitute."
            )

        # Feed pre-decoded audio instead of shelling out to ffmpeg.
        def _loader(filename, sampling_rate, dtype=None):
            return self._pending

        pk.load_audio = _loader

        self._mx = mx
        self._model = from_pretrained(_MODEL)
        # Warm up: first transcription compiles Metal kernels (~5 s) — pay it
        # here, during the "Loading…" phase, not on the user's first dictation.
        self._transcribe_array(np.zeros(_SAMPLE_RATE // 2, dtype=np.float32))

    def _transcribe_array(self, audio: np.ndarray) -> str:
        self._pending = self._mx.array(audio, dtype=self._mx.float32)
        result = self._model.transcribe("<memory>")
        return result.text.strip()

    def transcribe(self, audio: np.ndarray) -> str:
        if len(audio) < _SAMPLE_RATE * _MIN_SECONDS:
            return ""
        self.load()
        return self._transcribe_array(audio)

    def unload(self) -> None:
        self._model = None
        self._pending = None
        if self._mx is not None:
            try:
                self._mx.clear_cache()
            except Exception:
                pass
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_parakeet.py -q`
Expected: PASS. In the app venv (no parakeet), `test_parakeet_load_raises_when_missing` runs and the real-clip test is skipped.

- [ ] **Step 5: Commit**

```bash
git add flow/engines/parakeet.py tests/test_parakeet.py
git commit -m "feat: add optional parakeet-mlx engine (GPU, no ffmpeg)"
```

---

## Task 3: Config engine setting

**Files:**
- Modify: `flow/config.py`
- Test: `tests/test_config_engine.py`

- [ ] **Step 1: Write failing tests**

`tests/test_config_engine.py`:
```python
import pytest

from flow.config import Config, load_config


def test_default_engine_is_whisper():
    assert Config().engine == "whisper"


def test_load_engine_from_toml(tmp_path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text('[engine]\nname = "parakeet"\n')
    assert load_config(str(cfg_file)).engine == "parakeet"


def test_invalid_engine_name_raises(tmp_path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text('[engine]\nname = "bogus"\n')
    with pytest.raises(ValueError):
        load_config(str(cfg_file))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_config_engine.py -q`
Expected: FAIL — `AttributeError: 'Config' object has no attribute 'engine'`.

- [ ] **Step 3: Add the field**

In `flow/config.py`, add to the `Config` dataclass after `model`:
```python
    engine: str = "whisper"
```

- [ ] **Step 4: Add parsing + validation**

In `flow/config.py` `load_config`, after the `whisper` block and before `recording`:
```python
    engine = data.get("engine", {})
    if not isinstance(engine, dict):
        raise ValueError("[engine] must be a TOML table")
    if "name" in engine:
        from flow.engines import ENGINE_NAMES

        if engine["name"] not in ENGINE_NAMES:
            raise ValueError(
                f"engine.name must be one of {ENGINE_NAMES}"
            )
        cfg.engine = engine["name"]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_config_engine.py -q`
Expected: PASS (3 passed).

- [ ] **Step 6: Commit**

```bash
git add flow/config.py tests/test_config_engine.py
git commit -m "feat: add engine selection to config"
```

---

## Task 4: Engine-choice persistence (state file)

**Files:**
- Create: `flow/engine_state.py`
- Test: `tests/test_engine_state.py`

- [ ] **Step 1: Write failing tests**

`tests/test_engine_state.py`:
```python
from pathlib import Path

from flow import engine_state


def test_save_then_load(tmp_path):
    p = tmp_path / "engine"
    engine_state.save_engine("parakeet", path=p)
    assert engine_state.load_engine(path=p) == "parakeet"


def test_load_missing_returns_none(tmp_path):
    assert engine_state.load_engine(path=tmp_path / "absent") is None


def test_resolve_prefers_valid_state_file(tmp_path):
    p = tmp_path / "engine"
    engine_state.save_engine("parakeet", path=p)
    assert engine_state.resolve_engine("whisper", ("whisper", "parakeet"), path=p) == "parakeet"


def test_resolve_ignores_invalid_state_file(tmp_path):
    p = tmp_path / "engine"
    p.write_text("bogus")
    assert engine_state.resolve_engine("whisper", ("whisper", "parakeet"), path=p) == "whisper"


def test_resolve_falls_back_to_config_when_no_state(tmp_path):
    assert engine_state.resolve_engine("parakeet", ("whisper", "parakeet"), path=tmp_path / "absent") == "parakeet"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_engine_state.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'flow.engine_state'`.

- [ ] **Step 3: Create the module**

`flow/engine_state.py`:
```python
"""Persist the user's menu engine choice outside config.toml.

The choice the user makes from the menu bar is stored here, NOT written back
into config.toml (which the user hand-edits with comments). At startup this
file takes precedence over config.toml.
"""

import os
from pathlib import Path

_DEFAULT_PATH = Path(
    os.path.expanduser("~/Library/Application Support/LocalFlow/engine")
)


def load_engine(path: Path = _DEFAULT_PATH) -> str | None:
    """Return the saved engine name, or None if unset/unreadable."""
    try:
        return path.read_text().strip() or None
    except OSError:
        return None


def save_engine(name: str, path: Path = _DEFAULT_PATH) -> None:
    """Persist the engine name, creating the parent directory as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(name)


def resolve_engine(
    config_engine: str, valid_names, path: Path = _DEFAULT_PATH
) -> str:
    """State file (if valid) wins, else the config value."""
    saved = load_engine(path)
    if saved in valid_names:
        return saved
    return config_engine
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_engine_state.py -q`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add flow/engine_state.py tests/test_engine_state.py
git commit -m "feat: persist engine choice in a state file"
```

---

## Task 5: App engine switching

**Files:**
- Modify: `flow/app.py`
- Test: `tests/test_app_engine.py`

- [ ] **Step 1: Write failing tests**

`tests/test_app_engine.py`:
```python
import numpy as np
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
        app_mod.engine_state, "_DEFAULT_PATH", tmp_path / "engine", raising=False
    )
    monkeypatch.setattr(
        app_mod.engine_state, "save_engine",
        lambda name, path=tmp_path / "engine": path.write_text(name),
    )
    cfg = Config()
    a = App(cfg)
    return a


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
    monkeypatch.setattr(app_mod, "make_transcriber", lambda name, cfg: called.append(name))

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
    monkeypatch.setattr(app_mod, "make_transcriber", lambda name, cfg: FakeEngine(name, fail=True))

    app.set_engine("parakeet")
    app._switch_thread.join(timeout=5)

    assert app.transcriber is old
    assert app.engine_name == "whisper"
    assert app._state == IDLE
    assert notes
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_app_engine.py -q`
Expected: FAIL — `ImportError: cannot import name 'LOADING'`.

- [ ] **Step 3: Update `flow/app.py` imports and state constants**

Replace the import line `from flow.transcriber import Transcriber` with:
```python
from flow import engine_state
from flow.engines import EngineUnavailable, make_transcriber
```

Add the new state constant next to the others:
```python
IDLE = "idle"
RECORDING = "recording"
PROCESSING = "processing"
LOADING = "loading"
```

- [ ] **Step 4: Update `App.__init__`**

Replace the `self.transcriber = Transcriber(...)` block with:
```python
        self.engine_name = config.engine
        self.transcriber = make_transcriber(self.engine_name, config)
        self._switch_thread = None
```

Add two hooks next to `self.on_state`:
```python
        # Optional UI hooks.
        self.on_state: Callable[[str], None] | None = None
        self.on_engine: Callable[[str], None] | None = None
        # User-facing notifier (menubar wires this to a macOS notification).
        self.notify: Callable[[str], None] = lambda _msg: None
```
(Remove the duplicate original `self.on_state` line.)

- [ ] **Step 5: Add `set_engine` + worker**

Add these methods to `App`:
```python
    def set_engine(self, name: str) -> None:
        """Switch transcription engine: load+warm new, then unload old.

        Refused (with a notification) unless the app is IDLE, so a switch
        never interrupts an in-flight dictation.
        """
        with self._lock:
            if name == self.engine_name:
                return
            if self._state != IDLE:
                self.notify("Finish the current dictation first.")
                return
            self._state = LOADING
        self._notify("loading")
        self._switch_thread = threading.Thread(
            target=self._switch_engine, args=(name,), daemon=True
        )
        self._switch_thread.start()

    def _switch_engine(self, name: str) -> None:
        old = self.transcriber
        try:
            new = make_transcriber(name, self.config)
            new.load()
        except EngineUnavailable as exc:
            print(f"Cannot switch to {name}: {exc}")
            self.notify(str(exc))
        except Exception as exc:  # noqa: BLE001 - report any load failure
            print(f"Failed to load {name}: {exc}")
            self.notify(f"Could not load {name}.")
        else:
            self.transcriber = new
            self.engine_name = name
            try:
                old.unload()
            except Exception:
                pass
            engine_state.save_engine(name)
            if self.on_engine is not None:
                self.on_engine(name)
            print(f"Switched engine to {new.label}.")
        finally:
            with self._lock:
                self._state = IDLE
            self._notify("ready")
```

- [ ] **Step 6: Make startup engine-aware with fallback**

Replace `App.start` body with:
```python
    def start(self) -> None:
        """Load the active engine and start the hotkey listener (call off-main)."""
        print(f"Loading engine {self.engine_name}…")
        try:
            self.transcriber.load()
        except EngineUnavailable as exc:
            print(f"{exc} Falling back to faster-whisper.")
            self.notify(f"{exc} Using faster-whisper.")
            self.engine_name = "whisper"
            self.transcriber = make_transcriber("whisper", self.config)
            self.transcriber.load()
        self.hotkey.start()
        combo = "+".join(self.config.keys)
        print(f"Ready — hold {combo} to dictate.")
        if self.on_engine is not None:
            self.on_engine(self.engine_name)
        self._notify("ready")
```

- [ ] **Step 7: Block recording while LOADING**

In `_on_activate`, the existing guard `if self._state != IDLE: return` already prevents recording during LOADING. Add a friendlier message right after the PROCESSING check:
```python
            if self._state == LOADING:
                print("Switching engine — try again in a moment.")
                return
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_app_engine.py -q`
Expected: PASS (3 passed).

- [ ] **Step 9: Run the whole suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass (parakeet real-clip test skipped).

- [ ] **Step 10: Commit**

```bash
git add flow/app.py tests/test_app_engine.py
git commit -m "feat: live engine switching with load-now-unload-old and fallback"
```

---

## Task 6: Menu-bar engine submenu

**Files:**
- Modify: `flow/menubar.py`

No unit test (AppKit UI). Verified by import-smoke + manual run.

- [ ] **Step 1: Import the registry**

At the top of `flow/menubar.py`, add to the `from flow ...` imports:
```python
from flow import engine_state
from flow.engines import ENGINES, ENGINE_NAMES
```

- [ ] **Step 2: Add icon + text for the loading state**

In `_STATE_ICONS` add:
```python
    "loading": "⏳",
```
In `_render`, the normal-state `texts` dict, add:
```python
                "loading": "Switching engine…",
```

- [ ] **Step 3: Build the engine submenu in `MenuBar.__init__`**

After the permission rows / restart row are added and before the `log_item`, insert:
```python
        # Transcription engine picker (registry-driven).
        self._engine_root = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Transcription Engine", None, ""
        )
        engine_menu = AppKit.NSMenu.alloc().init()
        engine_menu.setAutoenablesItems_(False)
        self._engine_items: dict = {}
        for info in ENGINES:
            item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                info.label, "selectEngine:", ""
            )
            item.setTarget_(delegate)
            item.setRepresentedObject_(info.name)
            item.setToolTip_(info.description)
            engine_menu.addItem_(item)
            self._engine_items[info.name] = item
        self._engine_root.setSubmenu_(engine_menu)
        menu.addItem_(self._engine_root)
        menu.addItem_(AppKit.NSMenuItem.separatorItem())
```

- [ ] **Step 4: Add `update_engine` + enable/disable logic**

Add to `MenuBar`:
```python
    def update_engine(self, active_name: str) -> None:
        """Thread-safe: tick the active engine, refresh enabled state."""
        self._active_engine = active_name
        _on_main(self._render)
```
In `MenuBar.__init__`, initialise `self._active_engine = ""` near the other state fields.

In `_render`, at the end (after the permission rows loop), add:
```python
        # Engine picker: visible only in normal (granted) state; the active
        # engine is checked; all disabled unless idle/ready.
        show_engine = not (missing or mic_unknown or restart)
        self._engine_root.setHidden_(not show_engine)
        ready = self._app_state == "ready"
        for name, item in self._engine_items.items():
            on = AppKit.NSControlStateValueOn if name == self._active_engine else AppKit.NSControlStateValueOff
            item.setState_(on)
            item.setEnabled_(ready)
```
Also hide the separator that follows the engine root when not shown — give that separator a handle in `__init__` (`self._engine_separator = AppKit.NSMenuItem.separatorItem()`, add it instead of the inline one in Step 3) and add:
```python
        self._engine_separator.setHidden_(not show_engine)
```

- [ ] **Step 5: Add the delegate action**

In `_Delegate`, add:
```python
    def selectEngine_(self, sender) -> None:
        name = str(sender.representedObject())
        logic = getattr(self, "logic", None)
        if logic is not None:
            logic.set_engine(name)
```

- [ ] **Step 6: Wire it up in `run`**

In `run`, after `logic.on_state = ui.set_state`, add:
```python
    logic.on_engine = ui.update_engine
    logic.notify = _notify
    delegate.logic = logic
    ui.update_engine(logic.engine_name)
```

And resolve the persisted engine before `App` is built. Replace `logic = App(config)` region: right after `snap = _snapshot_inprocess()` is too late — instead, immediately after `combo = "+".join(config.keys)` and before `logic = App(config)`, add:
```python
    config.engine = engine_state.resolve_engine(config.engine, ENGINE_NAMES)
```

- [ ] **Step 7: Update the boot header to name the engine**

In `boot()`, change:
```python
        ui.set_state("waiting", f"Loading model {config.model}…")
```
to:
```python
        ui.set_state("waiting", f"Loading {config.engine} engine…")
```

- [ ] **Step 8: Import-smoke test**

Run: `.venv/bin/python -c "import flow.menubar; print('ok')"`
Expected: prints `ok` (no import/syntax errors).

- [ ] **Step 9: Commit**

```bash
git add flow/menubar.py
git commit -m "feat: menu-bar engine picker submenu"
```

---

## Task 7: Setup & dependencies

**Files:**
- Create: `requirements-parakeet.txt`
- Modify: `setup.sh`

- [ ] **Step 1: Create the parakeet requirements**

`requirements-parakeet.txt`:
```
parakeet-mlx>=0.5
```

- [ ] **Step 2: Add a `--parakeet` flag to `setup.sh`**

Near the top of `setup.sh`, after `cd "$(dirname "$0")"`, add:
```bash
WANT_PARAKEET=0
for arg in "$@"; do
    case "$arg" in
        --parakeet) WANT_PARAKEET=1 ;;
        *) echo "Unknown option: $arg" >&2; exit 2 ;;
    esac
done
```

In both the `uv` and `pip` install branches, after installing `requirements.txt`, add a conditional install. For the uv branch:
```bash
    if [ "$WANT_PARAKEET" = "1" ]; then
        echo "Installing parakeet-mlx…"
        uv pip install --python .venv/bin/python -r requirements-parakeet.txt
    fi
```
For the pip branch:
```bash
    if [ "$WANT_PARAKEET" = "1" ]; then
        echo "Installing parakeet-mlx…"
        .venv/bin/python -m pip install -r requirements-parakeet.txt
    fi
```

- [ ] **Step 3: Make the model pre-download engine-aware**

Replace the Whisper pre-download heredoc's Python so it reads the engine and only pre-downloads whisper (parakeet downloads on first use). Change the inline script to:
```python
import tomllib
from pathlib import Path

model = "base.en"
engine = "whisper"
path = Path("config.toml")
if path.exists():
    try:
        data = tomllib.loads(path.read_text())
        model = data.get("whisper", {}).get("model", model)
        engine = data.get("engine", {}).get("name", engine)
    except Exception:
        pass

if engine == "whisper":
    from faster_whisper import WhisperModel
    WhisperModel(model, device="cpu", compute_type="int8")
    print(f"Whisper model '{model}' is ready.")
else:
    print(f"Engine is '{engine}'; its model downloads on first use.")
```

- [ ] **Step 4: Verify the flag parses**

Run: `bash -n setup.sh && echo "syntax ok"`
Expected: `syntax ok`.

- [ ] **Step 5: Commit**

```bash
git add requirements-parakeet.txt setup.sh
git commit -m "feat: optional --parakeet setup install"
```

---

## Task 8: Documentation

**Files:**
- Modify: `README.md`
- Modify: `GETTING_STARTED.md`
- Modify: `config.toml.example`

- [ ] **Step 1: Add the `[engine]` section to `config.toml.example`**

Add above the `[whisper]` section:
```toml
[engine]
# Which transcription engine to use: "whisper" (faster-whisper, CPU, light)
# or "parakeet" (Apple-GPU, faster + more accurate, needs ./setup.sh --parakeet).
# You can also switch live from the menu-bar icon; that choice is remembered
# separately and overrides this value.
name = "whisper"
```

- [ ] **Step 2: Add a "Choosing an engine" subsection to README.md**

After the configuration table in `README.md`, add:
```markdown
### Choosing an engine

local-flow ships with two transcription engines, switchable live from the
menu-bar icon (**Transcription Engine ▸**):

- **faster-whisper** (default) — runs on the CPU, light to install.
- **Parakeet** — runs on the Apple GPU; faster on short dictations *and* more
  accurate, with punctuation/capitalization. Heavier: ~1.2 GB resident and a
  one-time ~2.3 GB model download. Install it with `./setup.sh --parakeet`.

Your menu choice is remembered across restarts (stored under
`~/Library/Application Support/LocalFlow/`) and overrides `config.toml`.
Selecting Parakeet before installing it shows a reminder and keeps whisper.
```

- [ ] **Step 3: Note the engine option in GETTING_STARTED.md**

In `GETTING_STARTED.md`, under "The two settings most people change", add after the `[whisper]` block:
```toml
[engine]
name = "whisper"          # or "parakeet" (run ./setup.sh --parakeet first)
```
And one line: "You can switch engines any time from the menu-bar icon."

- [ ] **Step 4: Commit**

```bash
git add README.md GETTING_STARTED.md config.toml.example
git commit -m "docs: document engine selection"
```

---

## Task 9: Full verification

- [ ] **Step 1: Run the whole suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass; parakeet real-clip test skipped (not installed in app venv).

- [ ] **Step 2: Import-smoke the whole app**

Run: `.venv/bin/python -c "import flow.menubar, flow.app, flow.config, flow.engine_state, flow.engines.whisper; print('ok')"`
Expected: `ok`.

- [ ] **Step 3: Confirm no lingering references to the old module**

Run: `grep -rn "flow.transcriber\|from flow import transcriber" flow tests || echo "clean"`
Expected: `clean`.

- [ ] **Step 4: Manual smoke (user-run)**

Restart the app (`./stop.sh; open LocalFlow.app`), open the menu: confirm the **Transcription Engine** submenu, checkmark on faster-whisper, and that selecting Parakeet (not installed) shows the reminder. (Full parakeet path requires `./setup.sh --parakeet` and re-granting permissions if the bundle identity changed.)

---

## Self-review notes

- **Spec coverage:** engine package + ABC (T1), parakeet no-ffmpeg + warm-up + unavailable handling (T2), config setting (T3), state-file persistence + precedence (T4), set_engine/LOADING/load-now-unload-old/refusal/fallback (T5), submenu + checkmark + disabled-while-busy + notifications wiring (T6), optional install (T7), docs (T8), error-handling table covered across T2/T5/T6. All spec sections map to a task.
- **Type consistency:** `make_transcriber(name, config)`, `Transcriber.{name,label,load,transcribe,unload}`, `EngineUnavailable`, `ENGINE_NAMES`, `engine_state.{load_engine,save_engine,resolve_engine}`, `App.{engine_name,set_engine,on_engine,notify,_switch_thread}` used consistently across tasks.
- **No placeholders:** every code step shows complete code.
```
