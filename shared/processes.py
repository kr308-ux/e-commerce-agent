"""Cross-platform subprocess group creation and termination."""

from __future__ import annotations

import os
import signal
import subprocess
import time


def new_process_group_kwargs() -> dict[str, object]:
    if os.name == "posix":
        return {"start_new_session": True}
    if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {}


def terminate_process_tree(
    process: subprocess.Popen,
    *,
    graceful_timeout: float = 10,
) -> None:
    if process.poll() is not None:
        return
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
    else:
        ctrl_break = getattr(signal, "CTRL_BREAK_EVENT", None)
        if ctrl_break is not None:
            try:
                process.send_signal(ctrl_break)
            except OSError:
                pass
    try:
        process.wait(timeout=graceful_timeout)
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
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            process.kill()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        return


def terminate_process_id_tree(
    process_id: int,
    *,
    graceful_timeout: float = 3,
) -> None:
    """Terminate a recorded process-group leader without a Popen handle."""

    if process_id < 1:
        return
    if os.name == "posix":
        try:
            os.killpg(process_id, signal.SIGTERM)
        except ProcessLookupError:
            return
        deadline = time.monotonic() + graceful_timeout
        while time.monotonic() < deadline:
            try:
                os.kill(process_id, 0)
            except ProcessLookupError:
                return
            time.sleep(0.05)
        try:
            os.killpg(process_id, signal.SIGKILL)
        except ProcessLookupError:
            return
        return
    try:
        subprocess.run(
            ["taskkill", "/PID", str(process_id), "/T"],
            capture_output=True,
            timeout=graceful_timeout,
            check=False,
        )
        subprocess.run(
            ["taskkill", "/PID", str(process_id), "/T", "/F"],
            capture_output=True,
            timeout=graceful_timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return
