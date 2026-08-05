"""Small, bounded retries for idempotent SQLite-only database operations."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TypeVar

from django.db import OperationalError


T = TypeVar("T")


def retry_locked_database_operation(
    operation: Callable[[], T],
    *,
    attempts: int = 4,
    initial_delay: float = 0.05,
) -> T:
    """Retry only SQLite busy/locked failures around DB-only operations."""

    for attempt in range(attempts):
        try:
            return operation()
        except OperationalError as error:
            message = str(error).casefold()
            retryable = "locked" in message or "busy" in message
            if not retryable or attempt + 1 >= attempts:
                raise
            time.sleep(initial_delay * (2**attempt))
    raise AssertionError("unreachable")
