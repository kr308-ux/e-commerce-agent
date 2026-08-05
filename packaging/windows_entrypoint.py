"""Frozen Windows entrypoint for user and allowlisted internal processes."""

from __future__ import annotations

import multiprocessing
import os
import sys
from collections.abc import Callable


def _run_manage(arguments: list[str]) -> int:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    from django.core.management import execute_from_command_line

    execute_from_command_line([sys.executable, *arguments])
    return 0


def _run_module(
    module_main: Callable[[list[str] | None], int],
    arguments: list[str],
) -> int:
    return int(module_main(arguments) or 0)


def main(argv: list[str] | None = None) -> int:
    multiprocessing.freeze_support()
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] == "--internal-manage":
        if len(arguments) == 1:
            raise SystemExit("--internal-manage 缺少 Django 命令。")
        return _run_manage(arguments[1:])
    if arguments and arguments[0] == "--internal-contact-task":
        from ziniao_automation.contact_task_runner import main as runner_main

        return _run_module(runner_main, arguments[1:])
    if arguments and arguments[0] == "--internal-accepted-card":
        from ziniao_automation.accepted_card_runner import main as runner_main

        return _run_module(runner_main, arguments[1:])
    if arguments and arguments[0] == "--internal-collaboration-sync":
        from ziniao_automation.collaboration_sync_runner import (
            main as runner_main,
        )

        return _run_module(runner_main, arguments[1:])
    return _run_manage(["run_runtime_supervisor", *arguments])


if __name__ == "__main__":
    raise SystemExit(main())
