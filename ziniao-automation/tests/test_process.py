from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from ziniao_automation.config import ZiniaoCredentials, ZiniaoSettings
from ziniao_automation.errors import ZiniaoConnectionError
from ziniao_automation.process import ZiniaoProcessManager


def build_manager() -> ZiniaoProcessManager:
    settings = ZiniaoSettings(
        credentials=ZiniaoCredentials("company", "username", "password"),
        client_path=Path("/Applications/ziniao.app"),
        socket_port=16851,
    )
    manager = ZiniaoProcessManager(settings)
    manager.system = "Darwin"
    return manager


class ZiniaoProcessManagerTests(unittest.TestCase):
    def test_existing_endpoint_requires_webdriver_process_arguments(self) -> None:
        manager = build_manager()
        with (
            patch.object(manager, "endpoint_ready", return_value=True),
            patch.object(manager, "webdriver_mode_running", return_value=False),
        ):
            with self.assertRaises(ZiniaoConnectionError) as captured:
                manager.start()
        self.assertIn("只能使用", str(captured.exception))
        self.assertIn("--run_type=web_driver", str(captured.exception))

    def test_existing_webdriver_endpoint_is_accepted(self) -> None:
        manager = build_manager()
        with (
            patch.object(manager, "endpoint_ready", return_value=True),
            patch.object(manager, "webdriver_mode_running", return_value=True),
        ):
            manager.start()

    def test_normal_workbench_process_is_rejected(self) -> None:
        manager = build_manager()
        with (
            patch.object(manager, "endpoint_ready", return_value=False),
            patch.object(manager, "_main_process_running", return_value=True),
        ):
            with self.assertRaises(ZiniaoConnectionError) as captured:
                manager.start()
        self.assertIn("普通工作台模式被禁止", str(captured.exception))

    def test_prewarm_restarts_only_an_incompatible_main_process(self) -> None:
        manager = build_manager()
        with (
            patch.object(manager, "_main_process_running", return_value=True),
            patch.object(manager, "webdriver_mode_running", return_value=False),
            patch.object(manager, "stop") as stop,
            patch.object(manager, "start") as start,
        ):
            manager.ensure_started(restart_incompatible=True)

        stop.assert_called_once_with()
        start.assert_called_once_with()

    def test_prewarm_preserves_an_existing_webdriver_process(self) -> None:
        manager = build_manager()
        with (
            patch.object(manager, "_main_process_running", return_value=True),
            patch.object(manager, "webdriver_mode_running", return_value=True),
            patch.object(manager, "stop") as stop,
            patch.object(manager, "start") as start,
        ):
            manager.ensure_started(restart_incompatible=True)

        stop.assert_not_called()
        start.assert_called_once_with()

    def test_webdriver_mode_matches_all_required_arguments(self) -> None:
        manager = build_manager()
        expected = (
            "/Applications/ziniao.app/Contents/MacOS/ziniao "
            "--run_type=web_driver --ipc_type=http --port=16851"
        )
        with patch.object(manager, "_main_process_commands", return_value=(expected,)):
            self.assertTrue(manager.webdriver_mode_running())
        with patch.object(
            manager,
            "_main_process_commands",
            return_value=("/Applications/ziniao.app/Contents/MacOS/ziniao",),
        ):
            self.assertFalse(manager.webdriver_mode_running())


if __name__ == "__main__":
    unittest.main()
