"""Run creator-contact subprocesses with database-backed cancellation."""

from __future__ import annotations

import os
import signal
import subprocess
import time
from collections.abc import Mapping, Sequence
from pathlib import Path

from django.db import OperationalError

from creator_contact.models import CreatorContactTask
from shared.db import retry_locked_database_operation
from shared.logger import JsonlAuditLogger, project_log_root


POLL_INTERVAL_SECONDS = 0.25


class TaskCancellationRequested(RuntimeError):
    """Raised after a cancelled task's active subprocess has been stopped."""


def _record_active_process(
    task_id: object,
    process_id: int | None,
    *,
    expected_process_id: int | None = None,
) -> bool:
    def update() -> int:
        queryset = CreatorContactTask.objects.filter(pk=task_id)
        if expected_process_id is not None:
            queryset = queryset.filter(
                active_process_id=expected_process_id
            )
        return queryset.update(active_process_id=process_id)

    try:
        return retry_locked_database_operation(update) == 1
    except OperationalError:
        return False


def task_cancellation_requested(task_id: object) -> bool:
    try:
        return CreatorContactTask.objects.filter(
            pk=task_id,
            status=CreatorContactTask.Status.CANCELLED,
        ).exists()
    except OperationalError:
        # A transient SQLite lock must not accidentally kill an automation.
        return False


def _terminate_process_tree(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
    else:
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T"],
                capture_output=True,
                timeout=3,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            process.terminate()
    try:
        process.wait(timeout=3)
        return
    except subprocess.TimeoutExpired:
        pass
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            return
    else:
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                timeout=3,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            process.kill()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        return


def run_task_subprocess(
    task_id: object,
    command: Sequence[str],
    *,
    cwd: str | os.PathLike[str] | Path,
    env: Mapping[str, str],
    timeout: float,
) -> subprocess.CompletedProcess[str]:
    """Run a process while polling the task cancellation status."""
    audit_logger = JsonlAuditLogger(
        root=project_log_root(Path(cwd)),
        category="regular",
        component="creator-contact-subprocess",
        task_id=task_id,
    )
    started_at = time.perf_counter()
    audit_logger.write(
        "subprocess_started",
        status="STARTED",
        operation=str(command[2] if len(command) > 2 else command[0]),
        input_content={
            "command": list(command),
            "cwd": str(cwd),
            "timeoutSeconds": timeout,
        },
    )
    popen_kwargs: dict[str, object] = {
        "cwd": cwd,
        "env": env,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "encoding": "utf-8",
    }
    if os.name == "posix":
        popen_kwargs["start_new_session"] = True
    elif hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP

    try:
        process = subprocess.Popen(list(command), **popen_kwargs)
    except Exception as error:
        audit_logger.write(
            "subprocess_finished",
            status="ERROR",
            operation=str(command[2] if len(command) > 2 else command[0]),
            input_content={"command": list(command), "cwd": str(cwd)},
            error={
                "type": type(error).__name__,
                "message": str(error),
            },
            duration_ms=(time.perf_counter() - started_at) * 1000,
        )
        raise
    if not _record_active_process(task_id, process.pid):
        _terminate_process_tree(process)
        raise RuntimeError(
            "无法持久化自动化子进程 PID，已停止进程以避免失控。"
        )
    deadline = time.monotonic() + timeout
    while True:
        if task_cancellation_requested(task_id):
            _terminate_process_tree(process)
            stdout, stderr = process.communicate()
            audit_logger.write(
                "subprocess_finished",
                status="CANCELLED",
                operation=str(
                    command[2] if len(command) > 2 else command[0]
                ),
                input_content={"command": list(command), "cwd": str(cwd)},
                output_content={
                    "returnCode": process.returncode,
                    "stdout": stdout,
                    "stderr": stderr,
                },
                duration_ms=(time.perf_counter() - started_at) * 1000,
            )
            _record_active_process(
                task_id,
                None,
                expected_process_id=process.pid,
            )
            raise TaskCancellationRequested("达人联系任务已由用户终止。")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _terminate_process_tree(process)
            stdout, stderr = process.communicate()
            audit_logger.write(
                "subprocess_finished",
                status="TIMEOUT",
                operation=str(
                    command[2] if len(command) > 2 else command[0]
                ),
                input_content={"command": list(command), "cwd": str(cwd)},
                output_content={
                    "returnCode": process.returncode,
                    "stdout": stdout,
                    "stderr": stderr,
                },
                error={
                    "type": "TimeoutExpired",
                    "message": f"子进程超过 {timeout} 秒。",
                },
                duration_ms=(time.perf_counter() - started_at) * 1000,
            )
            _record_active_process(
                task_id,
                None,
                expected_process_id=process.pid,
            )
            raise subprocess.TimeoutExpired(
                command,
                timeout,
                output=stdout,
                stderr=stderr,
            )
        try:
            stdout, stderr = process.communicate(
                timeout=min(POLL_INTERVAL_SECONDS, remaining)
            )
        except subprocess.TimeoutExpired:
            continue
        completed = subprocess.CompletedProcess(
            args=list(command),
            returncode=process.returncode,
            stdout=stdout,
            stderr=stderr,
        )
        audit_logger.write(
            "subprocess_finished",
            status="SUCCESS" if process.returncode == 0 else "FAILED",
            operation=str(command[2] if len(command) > 2 else command[0]),
            input_content={"command": list(command), "cwd": str(cwd)},
            output_content={
                "returnCode": process.returncode,
                "stdout": stdout,
                "stderr": stderr,
            },
            duration_ms=(time.perf_counter() - started_at) * 1000,
        )
        _record_active_process(
            task_id,
            None,
            expected_process_id=process.pid,
        )
        return completed
