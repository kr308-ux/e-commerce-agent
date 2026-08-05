"""Safe retention cleanup for date-partitioned runtime logs."""

from __future__ import annotations

import re
import shutil
from datetime import date, datetime, timedelta
from pathlib import Path


_DATE_DIRECTORY = re.compile(r"\d{4}-\d{2}-\d{2}")


def cleanup_old_logs(
    root: str | Path,
    *,
    retention_days: int = 14,
    today: date | None = None,
    dry_run: bool = False,
) -> tuple[Path, ...]:
    """Remove recognized log-day directories outside the retention window.

    The current day counts as the first retained day. Unknown entries and
    symbolic links are deliberately ignored.
    """

    if retention_days < 1:
        raise ValueError("日志保留天数必须大于 0。")
    root_path = Path(root)
    if not root_path.exists():
        return ()
    if not root_path.is_dir() or root_path.is_symlink():
        raise ValueError("日志根路径必须是普通目录。")

    current_day = today or datetime.now().date()
    oldest_retained = current_day - timedelta(days=retention_days - 1)
    removable: list[Path] = []
    for child in root_path.iterdir():
        if child.is_symlink() or not child.is_dir():
            continue
        if _DATE_DIRECTORY.fullmatch(child.name) is None:
            continue
        try:
            child_day = date.fromisoformat(child.name)
        except ValueError:
            continue
        if child_day < oldest_retained:
            removable.append(child)

    removed: list[Path] = []
    for child in sorted(removable):
        if not dry_run:
            try:
                shutil.rmtree(child)
            except FileNotFoundError:
                continue
        removed.append(child)
    return tuple(removed)
