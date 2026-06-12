"""Application wiring: hotkey -> record -> transcribe -> paste."""

import threading
import time
from typing import Callable

from flow import permissions
from flow.config import Config
from flow.hotkey import HotkeyListener
from flow.paster import paste_text
from flow.recorder import Recorder
from flow.transcriber import Transcriber

IDLE = "idle"
RECORDING = "recording"
PROCESSING = "processing"


class App:
    """Push-to-talk dictation app with an IDLE/RECORDING/PROCESSING state machine."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.recorder = Recorder(
            sample_rate=config.sample_rate, max_seconds=config.max_seconds
        )
        self.transcriber = Transcriber(
            model_name=config.model,
            compute_type=config.compute_type,
            beam_size=config.beam_size,
        )
        self.hotkey = HotkeyListener(
            keys=config.keys,
            on_activate=self._on_activate,
            on_deactivate=self._on_deactivate,
        )
        self._state = IDLE
        self._lock = threading.Lock()
        # Optional UI hook: called with ("ready"|"recording"|"processing").
        self.on_state: Callable[[str], None] | None = None
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
        """Hotkey combo held: start recording (called on the listener thread)."""
        with self._lock:
            if self._state == PROCESSING:
                print("Still processing the previous dictation — ignored.")
                return
            if self._state != IDLE:
                return
            self._state = RECORDING
        try:
            self.recorder.start()
            self._notify("recording")
            print("Recording… release to transcribe.")
        except Exception as exc:
            print(f"Could not start recording: {exc}")
            with self._lock:
                self._state = IDLE
            self._notify("ready")

    def _on_deactivate(self) -> None:
        """Any combo key released: process the recording on a worker thread."""
        with self._lock:
            if self._state != RECORDING:
                return
            self._state = PROCESSING
        self._notify("processing")
        threading.Thread(target=self._process, daemon=True).start()

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

    def start(self) -> None:
        """Load the model and start the hotkey listener (call off-main)."""
        print(f"Loading model {self.config.model}…")
        self.transcriber.load()
        self.hotkey.start()
        combo = "+".join(self.config.keys)
        print(f"Ready — hold {combo} to dictate.")
        self._notify("ready")

    def shutdown(self) -> None:
        """Stop the listener and any in-flight recording."""
        self.hotkey.stop()
        try:
            self.recorder.stop()
        except Exception:
            pass
