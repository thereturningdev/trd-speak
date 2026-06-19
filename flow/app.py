"""Application wiring: hotkey -> record -> transcribe -> paste."""

import threading
import time
from typing import Callable

from flow import engine_state, permissions
from flow.config import Config
from flow.engines import EngineUnavailable, make_transcriber
from flow.history import History
from flow.hotkey import HotkeyListener
from flow.paster import paste_text
from flow.recorder import Recorder

IDLE = "idle"
RECORDING = "recording"
PROCESSING = "processing"
LOADING = "loading"


class App:
    """Push-to-talk dictation app with an IDLE/RECORDING/PROCESSING state machine."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.recorder = Recorder(
            sample_rate=config.sample_rate, max_seconds=config.max_seconds
        )
        self.engine_name = config.engine
        self.transcriber = make_transcriber(self.engine_name, config)
        # Recent-dictations history (in-memory, menu-bar surfaced).
        self.history = History()
        self._switch_thread = None
        self._dictation_thread = None
        # Signals the active dictation worker to stop recording and process.
        self._stop_recording = threading.Event()
        self.hotkey = HotkeyListener(
            keys=config.keys,
            on_activate=self._on_activate,
            on_deactivate=self._on_deactivate,
        )
        # Second, independent listener (its own tap): a clean tap of this combo
        # re-pastes the most recent dictation into the focused window.
        self.repaste_hotkey = HotkeyListener(
            keys=config.repaste_keys,
            on_trigger=self._on_repaste,
        )
        self._state = IDLE
        self._lock = threading.Lock()
        # Optional UI hooks.
        # on_state: called with ("ready"|"recording"|"processing"|"loading").
        self.on_state: Callable[[str], None] | None = None
        # on_engine: called with the active engine name after start/switch.
        self.on_engine: Callable[[str], None] | None = None
        # User-facing notifier (menubar wires this to a macOS notification).
        self.notify: Callable[[str], None] = lambda _msg: None
        # Pre-paste permission check. The default in-process preflight is
        # CACHED BY macOS at first call: a grant made during this process's
        # lifetime keeps reading as False and every paste would be refused.
        # flow.menubar overrides this with its fresh-child snapshot.
        self.can_paste: Callable[[], bool] = permissions.can_post

    def _notify(self, state: str) -> None:
        cb = self.on_state
        if cb is not None:
            try:
                cb(state)
            except Exception:
                pass

    def _on_activate(self) -> None:
        """Hotkey combo held: begin a dictation.

        This runs on the macOS main run-loop thread (the event tap's
        callback), so it MUST return immediately: the blocking work
        (recorder.start(), which can stall for seconds on a flaky audio
        device) is handed to a worker thread. Blocking here would freeze the
        run loop and macOS would disable the event tap, killing the hotkey.
        """
        with self._lock:
            if self._state == PROCESSING:
                print("Still processing the previous dictation — ignored.")
                return
            if self._state == LOADING:
                print("Switching engine — try again in a moment.")
                return
            if self._state != IDLE:
                return
            self._state = RECORDING
            # Created under the lock, atomically with the state, so a release
            # racing in cannot signal a stale event.
            self._stop_recording = threading.Event()
        self._dictation_thread = threading.Thread(target=self._dictate, daemon=True)
        self._dictation_thread.start()

    def _dictate(self) -> None:
        """Own one dictation end-to-end on a worker thread.

        Start recording, wait for the combo release, then transcribe and
        paste. Keeping start() and stop() sequential on a single thread means
        stop() can never race ahead of a slow start().
        """
        stop_recording = self._stop_recording
        try:
            self.recorder.start()
        except Exception as exc:
            print(f"Could not start recording: {exc}")
            with self._lock:
                self._state = IDLE
            self._notify("ready")
            return
        # If the user already released during a slow start(), we are already
        # in PROCESSING — skip the "recording" announcement and go straight on.
        if not stop_recording.is_set():
            self._notify("recording")
            print("Recording… release to transcribe.")
        stop_recording.wait()
        self._process()

    def _on_deactivate(self) -> None:
        """Any combo key released: tell the dictation worker to stop.

        Also runs on the main run-loop thread, so it only flips state and
        signals — the actual stop/transcribe/paste happens on the worker.
        """
        with self._lock:
            if self._state != RECORDING:
                return
            self._state = PROCESSING
        self._notify("processing")
        self._stop_recording.set()

    def _process(self) -> None:
        """Stop recording, transcribe, paste. Always returns to IDLE."""
        try:
            audio = self.recorder.stop()
            audio_secs = len(audio) / self.config.sample_rate
            start = time.monotonic()
            text = self.transcriber.transcribe(audio)
            elapsed = time.monotonic() - start
            timing = f"[{audio_secs:.0f}s audio, transcribed in {elapsed:.1f}s]"
            if text:
                # Capture BEFORE the paste attempt: a dictation that fails to
                # paste (keys still held, Accessibility missing) is exactly the
                # kind the user needs to recover from the history.
                self.history.add(text)
                shown = text if len(text) <= 80 else text[:77] + "…"
                if not self.can_paste():
                    print(
                        f"Transcribed but CANNOT paste — Accessibility permission "
                        f"is missing (see warning above). Text was: {shown}"
                    )
                    return
                # Cmd+V must never fire while a trigger key is still held
                # (a held Ctrl would turn the paste into Ctrl+Cmd+V).
                if not self.hotkey.wait_all_released():
                    print(f"Trigger keys still held — paste skipped. Text was: {shown}")
                    return
                # Trailing space so consecutive dictations don't run together.
                paste_text(text + " ", restore_delay=self.config.paste_restore_delay)
                print(f"Pasted {timing}: {shown}")
            else:
                print(f"Heard nothing {timing} — nothing pasted.")
        except Exception as exc:
            print(f"Error during transcription/paste: {exc}")
        finally:
            with self._lock:
                self._state = IDLE
            self._notify("ready")

    def _on_repaste(self) -> None:
        """Re-paste hotkey tapped: re-insert the most recent dictation.

        Runs on the main run-loop thread (the re-paste tap's callback), so it
        MUST return immediately — the blocking work (waiting for the keys to
        release, then the clipboard paste) is handed to a worker thread, exactly
        like _on_activate.
        """
        threading.Thread(target=self._do_repaste, daemon=True).start()

    def _do_repaste(self) -> None:
        """Paste the newest dictation into the focused window, on a worker.

        Only runs when the app is IDLE so it never races an in-flight
        dictation's clipboard save/restore.
        """
        # Wait for the combo to be fully released so the synthesized Cmd+V is a
        # plain paste, not Cmd+<modifiers>+V.
        self.repaste_hotkey.wait_all_released()
        with self._lock:
            if self._state != IDLE:
                self.notify("Finish the current dictation first.")
                return
            self._state = PROCESSING
        self._notify("processing")
        try:
            items = self.history.items()
            if not items:
                print("Re-paste requested but the history is empty.")
                self.notify("No recent dictation to re-paste.")
                return
            text = items[0]
            shown = text if len(text) <= 80 else text[:77] + "…"
            if not self.can_paste():
                print(
                    f"Re-paste CANNOT proceed — Accessibility permission is "
                    f"missing. Text was: {shown}"
                )
                return
            paste_text(text + " ", restore_delay=self.config.paste_restore_delay)
            print(f"Re-pasted: {shown}")
        except Exception as exc:
            print(f"Error during re-paste: {exc}")
        finally:
            with self._lock:
                self._state = IDLE
            self._notify("ready")

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
        # The re-paste tap is a convenience: if it cannot start, log and carry
        # on — push-to-talk must not be held hostage to it (both need the same
        # Input Monitoring grant, so in practice they succeed or fail together).
        try:
            self.repaste_hotkey.start()
        except Exception as exc:
            print(f"Could not start the re-paste hotkey listener: {exc}")
        else:
            repaste_combo = "+".join(self.config.repaste_keys)
            print(f"Tap {repaste_combo} to re-paste the last dictation.")
        combo = "+".join(self.config.keys)
        print(f"Ready — hold {combo} to dictate.")
        if self.on_engine is not None:
            self.on_engine(self.engine_name)
        self._notify("ready")

    def shutdown(self) -> None:
        """Stop the listeners and any in-flight recording."""
        self.hotkey.stop()
        self.repaste_hotkey.stop()
        try:
            self.recorder.stop()
        except Exception:
            pass
