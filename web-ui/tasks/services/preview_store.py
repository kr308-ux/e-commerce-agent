"""Process-local, expiring storage for unconfirmed spreadsheet previews."""

from __future__ import annotations

import threading
import time
from collections import OrderedDict

from .import_types import PreviewState


class PreviewExpiredError(KeyError):
    """Raised when an unconfirmed preview no longer exists."""


class InMemoryPreviewStore:
    def __init__(self, *, ttl_seconds: int, max_entries: int = 20):
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self._lock = threading.RLock()
        self._entries: OrderedDict[str, tuple[float, PreviewState]] = OrderedDict()

    def put(self, state: PreviewState) -> None:
        with self._lock:
            self._purge()
            self._entries[state.preview_id] = (
                time.monotonic() + self.ttl_seconds,
                state,
            )
            self._entries.move_to_end(state.preview_id)
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)

    def get(self, preview_id: str) -> PreviewState:
        with self._lock:
            self._purge()
            item = self._entries.get(str(preview_id))
            if item is None:
                raise PreviewExpiredError(str(preview_id))
            expires_at, state = item
            self._entries[str(preview_id)] = (
                expires_at,
                state,
            )
            self._entries.move_to_end(str(preview_id))
            return state

    def delete(self, preview_id: str) -> None:
        with self._lock:
            self._entries.pop(str(preview_id), None)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def _purge(self) -> None:
        now = time.monotonic()
        expired = [
            key
            for key, (expires_at, _) in self._entries.items()
            if expires_at <= now
        ]
        for key in expired:
            self._entries.pop(key, None)

