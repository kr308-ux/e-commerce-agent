"""Resolve immutable application resources and writable runtime data paths."""

from __future__ import annotations

import sys
from pathlib import Path


SOURCE_ROOT = Path(__file__).resolve().parents[1]


def is_frozen() -> bool:
    """Return whether the current process is running from a frozen executable."""
    return bool(getattr(sys, "frozen", False))


def app_home() -> Path:
    """Return the writable directory beside the executable in frozen builds."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return SOURCE_ROOT


def resource_root() -> Path:
    """Return the root containing bundled, read-only project resources."""
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", app_home())).resolve()
    return SOURCE_ROOT


APP_HOME = app_home()
RESOURCE_ROOT = resource_root()
