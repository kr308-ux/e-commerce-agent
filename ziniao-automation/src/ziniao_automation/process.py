"""Start and restart the local Ziniao client in WebDriver mode."""

from __future__ import annotations

import platform
import json
import os
import socket
import subprocess
import time
from collections.abc import Sequence

from .config import ZiniaoSettings
from .errors import ZiniaoConnectionError


class ZiniaoProcessManager:
    def __init__(self, settings: ZiniaoSettings):
        self.settings = settings
        self.system = platform.system()

    def endpoint_ready(self) -> bool:
        try:
            with socket.create_connection(
                ("127.0.0.1", self.settings.socket_port),
                timeout=1,
            ):
                return True
        except OSError:
            return False

    def _main_process_running(self) -> bool:
        if self.system == "Windows":
            return bool(self._windows_processes())
        if self.system == "Darwin":
            result = subprocess.run(
                ["pgrep", "-x", "ziniao"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            return result.returncode == 0
        return False

    def _windows_processes(self) -> tuple[dict[str, object], ...]:
        if self.system != "Windows":
            return ()
        command = (
            "Get-CimInstance Win32_Process -Filter "
            "\"Name = 'ziniao.exe'\" | "
            "Select-Object ProcessId,ExecutablePath,CommandLine | "
            "ConvertTo-Json -Compress"
        )
        try:
            result = subprocess.run(
                ["powershell.exe", "-NoProfile", "-Command", command],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return ()
        if result.returncode != 0 or not result.stdout.strip():
            return ()
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            return ()
        rows = payload if isinstance(payload, list) else [payload]
        return tuple(row for row in rows if isinstance(row, dict))

    def _main_process_commands(self) -> Sequence[str]:
        """Return Ziniao main-process commands without shell expansion."""
        if self.system == "Windows":
            return tuple(
                str(row.get("CommandLine") or "")
                for row in self._windows_processes()
                if row.get("CommandLine")
            )
        if self.system != "Darwin":
            return ()
        pgrep_result = subprocess.run(
            ["pgrep", "-x", "ziniao"],
            capture_output=True,
            text=True,
            check=False,
        )
        if pgrep_result.returncode != 0:
            return ()
        process_ids = [
            value
            for value in pgrep_result.stdout.split()
            if value.isascii() and value.isdigit()
        ]
        if not process_ids:
            return ()
        ps_result = subprocess.run(
            ["ps", "-p", ",".join(process_ids), "-o", "command="],
            capture_output=True,
            text=True,
            check=False,
        )
        if ps_result.returncode != 0:
            return ()
        return tuple(
            command.strip()
            for command in ps_result.stdout.splitlines()
            if command.strip()
        )

    def webdriver_mode_running(self) -> bool:
        """Require the existing macOS main process to be WebDriver HTTP mode."""
        if self.system not in {"Darwin", "Windows"}:
            return self.endpoint_ready()
        expected_arguments = (
            "--run_type=web_driver",
            "--ipc_type=http",
            f"--port={self.settings.socket_port}",
        )
        return any(
            all(argument in command for argument in expected_arguments)
            for command in self._main_process_commands()
        )

    def assert_webdriver_mode(self) -> None:
        if not self.endpoint_ready():
            raise ZiniaoConnectionError("紫鸟 WebDriver HTTP 端口尚未就绪。")
        if (
            self.system in {"Darwin", "Windows"}
            and not self.webdriver_mode_running()
        ):
            raise ZiniaoConnectionError(
                "检测到紫鸟不是 WebDriver 模式。只能使用 "
                "--run_type=web_driver --ipc_type=http 启动；"
                "禁止使用普通紫鸟工作台或直接启动店铺浏览器。"
            )

    def stop(self) -> None:
        if self.system == "Windows":
            rows = self._windows_processes()
            expected_path = os.path.normcase(
                os.path.abspath(str(self.settings.client_path))
            )
            process_ids = []
            for row in rows:
                executable = str(row.get("ExecutablePath") or "")
                command = str(row.get("CommandLine") or "")
                path_matches = executable and os.path.normcase(
                    os.path.abspath(executable)
                ) == expected_path
                mode_matches = all(
                    value in command
                    for value in (
                        "--run_type=web_driver",
                        "--ipc_type=http",
                        f"--port={self.settings.socket_port}",
                    )
                )
                process_id = row.get("ProcessId")
                if (path_matches or mode_matches) and str(
                    process_id or ""
                ).isdigit():
                    process_ids.append(str(process_id))
            for process_id in process_ids:
                subprocess.run(
                    ["taskkill", "/pid", process_id, "/t"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
        elif self.system == "Darwin":
            subprocess.run(
                ["killall", "ziniao"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        elif self.system == "Linux":
            subprocess.run(
                ["killall", "ziniaobrowser"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        else:
            raise ZiniaoConnectionError(f"不支持的操作系统：{self.system}")
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if not self.endpoint_ready() and not self._main_process_running():
                return
            time.sleep(0.5)
        if self.system == "Windows" and self._main_process_running():
            for process_id in process_ids:
                subprocess.run(
                    ["taskkill", "/pid", process_id, "/t", "/f"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
        elif self.system == "Darwin" and self._main_process_running():
            subprocess.run(
                ["killall", "-KILL", "ziniao"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        elif self.system == "Linux" and self._main_process_running():
            subprocess.run(
                ["killall", "-KILL", "ziniaobrowser"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        force_deadline = time.monotonic() + 5
        while time.monotonic() < force_deadline:
            if not self.endpoint_ready() and not self._main_process_running():
                return
            time.sleep(0.25)
        raise ZiniaoConnectionError("紫鸟主进程未能在 15 秒内退出。")

    def start(self) -> None:
        if self.endpoint_ready():
            self.assert_webdriver_mode()
            return
        if self._main_process_running():
            raise ZiniaoConnectionError(
                "紫鸟客户端已运行但未处于 WebDriver 模式；普通工作台模式被禁止，"
                "请先退出后再以 --run_type=web_driver --ipc_type=http 启动。"
            )
        client_path = str(self.settings.client_path)
        arguments = [
            "--run_type=web_driver",
            "--ipc_type=http",
            f"--port={self.settings.socket_port}",
        ]
        if self.system == "Windows":
            command = [client_path, *arguments]
        elif self.system == "Darwin":
            command = ["open", "-a", client_path, "--args", *arguments]
        elif self.system == "Linux":
            command = [client_path, "--no-sandbox", *arguments]
        else:
            raise ZiniaoConnectionError(f"不支持的操作系统：{self.system}")
        try:
            subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as error:
            raise ZiniaoConnectionError(f"无法启动紫鸟客户端：{client_path}") from error
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if self.endpoint_ready():
                self.assert_webdriver_mode()
                return
            time.sleep(0.5)
        raise ZiniaoConnectionError("紫鸟 WebDriver 端口未能在 30 秒内就绪。")

    def ensure_started(
        self,
        *,
        restart: bool = False,
        restart_incompatible: bool = False,
    ) -> None:
        if restart:
            self.stop()
        elif (
            restart_incompatible
            and self._main_process_running()
            and not self.webdriver_mode_running()
        ):
            self.stop()
        self.start()
