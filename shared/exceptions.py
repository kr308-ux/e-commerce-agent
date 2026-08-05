"""Process-wide uncaught exception reporting with secret redaction."""

from __future__ import annotations

import sys
import threading
import traceback
from pathlib import Path
from types import TracebackType

from shared.logger import JsonlAuditLogger, project_log_root


_INSTALL_LOCK = threading.Lock()
_INSTALLED = False


def install_exception_hooks(project_root: str | Path) -> None:
    """Install idempotent process and thread exception hooks."""

    global _INSTALLED
    with _INSTALL_LOCK:
        if _INSTALLED:
            return
        _INSTALLED = True

    audit = JsonlAuditLogger(
        root=project_log_root(project_root),
        category="regular",
        component="uncaught-exception",
    )
    original_sys_hook = sys.excepthook
    original_thread_hook = threading.excepthook

    def record(
        *,
        source: str,
        exc_type: type[BaseException],
        exc_value: BaseException,
        exc_traceback: TracebackType | None,
        thread_name: str = "",
    ) -> None:
        try:
            audit.write(
                "uncaught_exception",
                status="CRITICAL",
                operation=source,
                error={
                    "type": exc_type.__name__,
                    "message": str(exc_value),
                    "traceback": "".join(
                        traceback.format_exception(
                            exc_type,
                            exc_value,
                            exc_traceback,
                        )
                    ),
                    "thread": thread_name,
                },
            )
        except Exception:
            # Exception reporting must never replace the original failure.
            return

    def sys_hook(
        exc_type: type[BaseException],
        exc_value: BaseException,
        exc_traceback: TracebackType | None,
    ) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            original_sys_hook(exc_type, exc_value, exc_traceback)
            return
        record(
            source="sys.excepthook",
            exc_type=exc_type,
            exc_value=exc_value,
            exc_traceback=exc_traceback,
        )
        original_sys_hook(exc_type, exc_value, exc_traceback)

    def thread_hook(args: threading.ExceptHookArgs) -> None:
        if args.exc_type is not SystemExit:
            record(
                source="threading.excepthook",
                exc_type=args.exc_type,
                exc_value=args.exc_value,
                exc_traceback=args.exc_traceback,
                thread_name=args.thread.name if args.thread else "",
            )
        original_thread_hook(args)

    sys.excepthook = sys_hook
    threading.excepthook = thread_hook
