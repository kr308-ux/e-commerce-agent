from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import Mock, patch

from ziniao_automation.collaboration_sync_runner import main, sync_store
from ziniao_automation.errors import (
    ZiniaoConfigurationError,
    ZiniaoConnectionError,
)


class CollaborationSyncRunnerTests(unittest.TestCase):
    def test_missing_credentials_fail_non_interactively_as_json(self) -> None:
        output = io.StringIO()
        with patch(
            "ziniao_automation.collaboration_sync_runner."
            "ZiniaoSettings.from_env",
            side_effect=ZiniaoConfigurationError(
                "缺少紫鸟本地环境变量：ZINIAO_PASSWORD"
            ),
        ):
            with redirect_stdout(output):
                exit_code = main(
                    ["--store-id", "store-1", "--json"]
                )

        payload = json.loads(output.getvalue().splitlines()[-1])
        self.assertEqual(exit_code, 1)
        self.assertFalse(payload["success"])
        self.assertEqual(payload["storeId"], "store-1")
        self.assertEqual(
            payload["errorCode"],
            "CONFIGURATION_ERROR",
        )
        self.assertEqual(payload["options"], [])

    @patch(
        "ziniao_automation.collaboration_sync_runner."
        "TargetCollaborationSync"
    )
    @patch(
        "ziniao_automation.collaboration_sync_runner."
        "connect_reusable_store"
    )
    @patch(
        "ziniao_automation.collaboration_sync_runner."
        "ZiniaoSettings.from_env"
    )
    def test_success_uses_exact_store_and_returns_contract(
        self,
        settings_from_env: Mock,
        connect_reusable: Mock,
        sync_class: Mock,
    ) -> None:
        settings = Mock()
        settings_from_env.return_value = settings
        attached = Mock(driver=Mock())
        connection = connect_reusable.return_value
        connection.__enter__.return_value = connection
        connection.__exit__.return_value = None
        connection.session = attached
        expected = [
            {
                "name": "Plan",
                "invitationGroupId": "123",
                "status": "IN_PROGRESS",
            }
        ]
        sync_class.return_value.sync.return_value = expected

        payload = sync_store("store-1")

        self.assertTrue(payload["success"])
        self.assertEqual(payload["storeId"], "store-1")
        self.assertEqual(payload["options"], expected)
        self.assertEqual(payload["errorCode"], "")
        connect_reusable.assert_called_once_with(
            settings,
            "store-1",
            allow_start=False,
        )
        sync_class.assert_called_once_with(attached.driver)

    def test_empty_store_id_returns_standard_error_shape(self) -> None:
        payload = sync_store("  ")

        self.assertEqual(
            payload,
            {
                "success": False,
                "storeId": "",
                "options": [],
                "errorCode": "STORE_ID_REQUIRED",
                "errorMessage": "必须提供非空 storeId。",
            },
        )

    @patch(
        "ziniao_automation.collaboration_sync_runner."
        "connect_reusable_store"
    )
    @patch(
        "ziniao_automation.collaboration_sync_runner."
        "ZiniaoSettings.from_env"
    )
    def test_busy_browser_is_reported_as_retryable_queue_condition(
        self,
        settings_from_env: Mock,
        connect_reusable: Mock,
    ) -> None:
        settings_from_env.return_value = Mock()
        connect_reusable.side_effect = ZiniaoConnectionError(
            "目标店铺正由另一个自动化进程操作，"
            "等待浏览器控制权超时。"
        )

        payload = sync_store("store-1")

        self.assertFalse(payload["success"])
        self.assertEqual(payload["errorCode"], "BROWSER_BUSY")


if __name__ == "__main__":
    unittest.main()
