#!/usr/bin/env python
"""Django command-line utility for the Browser Agent UI."""

import os
import sys


def main() -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Django 未安装。请先运行 scripts/setup-macos.sh 或 "
            "scripts/setup-windows.ps1。"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
