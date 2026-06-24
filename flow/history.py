"""Disk-backed store of the most recent dictations.

Each dictation is appended to a per-build JSON file (see flow.paths), capped at
``MAX_HISTORY`` entries. Two readers:

  * the re-paste hotkey, which pastes ``latest()`` into the focused window;
  * the menu-bar "Recent Dictations" submenu, which lists ``items()``.

Persisting to disk (rather than only in memory) means the most recent dictation
survives the app restarting — notably the restart macOS forces when a fresh
Input Monitoring grant is only honored in a new process. The dev build and the
production build write to separate files, so they never clobber each other.

A dictation finishes on a worker thread while the menu reads on the AppKit main
thread, so every access is guarded by a lock; writes are atomic (temp file +
os.replace) so a reader never sees a half-written file. Reads tolerate a
missing or corrupt file by returning an empty history rather than raising.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from flow import paths

MAX_HISTORY = 10


class History:
    """Thread-safe, fixed-capacity, disk-backed store of recent dictations.

    On disk the entries are a JSON list, oldest first; ``items()`` returns them
    newest first. ``path`` defaults to the per-build location and is injectable
    for tests so they never touch the real user storage.
    """

    def __init__(self, path: Path = paths.DICTATIONS_PATH) -> None:
        self._path = Path(path)
        self._lock = threading.Lock()

    def _read(self) -> list[str]:
        """Load the stored list (oldest first), or [] if missing/corrupt."""
        try:
            data = json.loads(self._path.read_text())
        except (OSError, json.JSONDecodeError):
            return []
        if not isinstance(data, list):
            return []
        return [s for s in data if isinstance(s, str)]

    def _write(self, entries: list[str]) -> None:
        """Atomically persist the list, creating the parent dir as needed."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_name(self._path.name + ".tmp")
        tmp.write_text(json.dumps(entries, ensure_ascii=False))
        os.replace(tmp, self._path)

    def add(self, text: str) -> None:
        """Record a dictation as the newest entry; trim to ``MAX_HISTORY``."""
        with self._lock:
            entries = self._read()
            entries.append(text)
            del entries[:-MAX_HISTORY]  # keep only the last MAX_HISTORY
            self._write(entries)

    def latest(self) -> str | None:
        """The most recent dictation, or None if the history is empty."""
        with self._lock:
            entries = self._read()
        return entries[-1] if entries else None

    def items(self) -> list[str]:
        """A snapshot of the stored dictations, newest first."""
        with self._lock:
            return list(reversed(self._read()))

    def clear(self) -> None:
        """Drop all stored dictations."""
        with self._lock:
            self._write([])
