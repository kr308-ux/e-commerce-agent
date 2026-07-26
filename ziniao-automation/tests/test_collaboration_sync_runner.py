from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import Mock, patch

from ziniao_automation.collaboration_sync_runner import main, sync_store
from ziniao_automation.errors import ZiniaoConfigurationError
from ziniao_automation.models import StoreInfo


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
        "SeleniumStoreSession"
    )
    @patch("ziniao_automation.collaboration_sync_runner.ZiniaoClient")
    @patch(
        "ziniao_automation.collaboration_sync_runner."
        "ZiniaoProcessManager"
    )
    @patch(
        "ziniao_automation.collaboration_sync_runner."
        "ZiniaoSettings.from_env"
    )
    def test_success_uses_exact_store_and_returns_contract(
        self,
        settings_from_env: Mock,
        process_manager: Mock,
        client_class: Mock,
        session_class: Mock,
        sync_class: Mock,
    ) -> None:
        settings = Mock()
        settings_from_env.return_value = settings
        client = client_class.return_value
        store = StoreInfo(
            browser_id="store-1",
            browser_name="Vaelos",
            browser_oauth="secret",
        )
        client.list_stores.return_value = [
            store,
            StoreInfo(
                browser_id="store-2",
                browser_name="Other",
                browser_oauth="other-secret",
            ),
        ]
        attached = Mock(driver=Mock())
        session = session_class.return_value
        session.__enter__.return_value = attached
        session.__exit__.return_value = None
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
        process_manager.return_value.ensure_started.assert_called_once()
        client.update_core.assert_called_once()
        session_class.assert_called_once_with(client, settings, store)
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


if __name__ == "__main__":
    unittest.main()
