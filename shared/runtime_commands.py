"""Build child-process commands that work in source and frozen runtimes."""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from pathlib import Path

from shared.runtime_paths import APP_HOME, RESOURCE_ROOT, is_frozen


_AUTOMATION_ENTRYPOINTS = {
    "ziniao_automation.contact_task_runner": "--internal-contact-task",
    "ziniao_automation.accepted_card_runner": "--internal-accepted-card",
    "ziniao_automation.collaboration_sync_runner": (
        "--internal-collaboration-sync"
    ),
}


def django_command(
    *arguments: object,
    python_executable: str | os.PathLike[str] | None = None,
) -> list[str]:
    """Return a Django management command for the active runtime."""
    normalized = [str(argument) for argument in arguments]
    if is_frozen():
        return [sys.executable, "--internal-manage", *normalized]
    executable = str(python_executable or sys.executable)
    return [
        executable,
        str(RESOURCE_ROOT / "web-ui" / "manage.py"),
        *normalized,
    ]


def automation_command(
    module: str,
    *arguments: object,
    python_executable: str | os.PathLike[str] | None = None,
) -> list[str]:
    """Return an allowlisted Ziniao module command for the active runtime."""
    normalized = [str(argument) for argument in arguments]
    if is_frozen():
        try:
            internal_flag = _AUTOMATION_ENTRYPOINTS[module]
        except KeyError as error:
            raise ValueError(f"不支持的冻结自动化入口：{module}") from error
        return [sys.executable, internal_flag, *normalized]
    executable = str(python_executable or sys.executable)
    return [executable, "-m", module, *normalized]


def automation_environment(
    source: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return a child environment with source-only package discovery."""
    environment = dict(source or os.environ)
    if is_frozen():
        return environment
    package_src = RESOURCE_ROOT / "ziniao-automation" / "src"
    prior_pythonpath = environment.get("PYTHONPATH", "")
    environment["PYTHONPATH"] = (
        str(package_src)
        if not prior_pythonpath
        else f"{package_src}{os.pathsep}{prior_pythonpath}"
    )
    return environment


def runtime_cwd() -> Path:
    """Return the writable working directory for child processes."""
    return APP_HOME
