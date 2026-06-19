"""In-memory store of the most recent dictations for the menu-bar history.

A dictation finishes on a worker thread, but the menu reads the history on the
AppKit main thread, so every access is guarded by a lock. Nothing is written to
disk — the history lives only as long as the process (privacy: the app keeps no
trace of what you dictated).
"""

from __future__ import annotations

import threading
from collections import deque

MAX_HISTORY = 10


class History:
    """Thread-safe, fixed-capacity store of recent dictations."""

    def __init__(self) -> None:
        self._items: deque[str] = deque(maxlen=MAX_HISTORY)
        self._lock = threading.Lock()

    def add(self, text: str) -> None:
        """Record a dictation; the oldest is evicted past ``MAX_HISTORY``."""
        with self._lock:
            self._items.append(text)

    def items(self) -> list[str]:
        """Return a snapshot of the stored dictations, newest first."""
        with self._lock:
            return list(reversed(self._items))

    def clear(self) -> None:
        """Drop all stored dictations."""
        with self._lock:
            self._items.clear()
