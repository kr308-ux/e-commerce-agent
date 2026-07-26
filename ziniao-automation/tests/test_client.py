from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import Mock

from ziniao_automation.client import ZiniaoClient
from ziniao_automation.config import ZiniaoCredentials, ZiniaoSettings
from ziniao_automation.errors import ZiniaoApiError


class FakeResponse:
    def __init__(self, data: dict[str, object]):
        self.data = data

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return self.data


class ZiniaoClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.credentials = ZiniaoCredentials(
            company="company-secret",
            username="user-secret",
            password="password-secret",
        )
        self.settings = ZiniaoSettings(
            credentials=self.credentials,
            client_path=Path("/Applications/ziniao.app"),
        )

    def test_list_stores_parses_and_hides_browser_oauth(self) -> None:
        http = Mock()
        http.post.return_value = FakeResponse(
            {
                "statusCode": 0,
                "browserList": [
                    {
                        "browserId": 123,
                        "browserOauth": "opaque-store-token",
                        "browserName": "Demo",
                        "siteId": 458,
                        "platform_id": 92,
                        "platform_name": "TikTok Shop",
                    }
                ],
            }
        )
        stores = ZiniaoClient(self.settings, session=http).list_stores()
        self.assertEqual(stores[0].browser_id, "123")
        self.assertNotIn("opaque-store-token", repr(stores[0]))
        payload = http.post.call_args.kwargs["json"]
        self.assertEqual(payload["password"], "password-secret")

    def test_api_error_redacts_all_credentials(self) -> None:
        http = Mock()
        http.post.return_value = FakeResponse(
            {
                "statusCode": -10003,
                "err": "company-secret user-secret password-secret login failed",
            }
        )
        with self.assertRaises(ZiniaoApiError) as captured:
            ZiniaoClient(self.settings, session=http).list_stores()
        message = str(captured.exception)
        self.assertNotIn("company-secret", message)
        self.assertNotIn("user-secret", message)
        self.assertNotIn("password-secret", message)

    def test_start_store_prefers_numeric_browser_id(self) -> None:
        http = Mock()
        http.post.return_value = FakeResponse(
            {
                "statusCode": 0,
                "debuggingPort": 9222,
                "core_version": "119.1.0.16",
                "core_type": "Chromium",
            }
        )
        list_response = FakeResponse(
            {
                "statusCode": 0,
                "browserList": [
                    {
                        "browserId": 123,
                        "browserOauth": "opaque",
                        "browserName": "Demo",
                    }
                ],
            }
        )
        http.post.return_value = list_response
        client = ZiniaoClient(self.settings, session=http)
        store = client.list_stores()[0]
        http.post.return_value = FakeResponse(
            {
                "statusCode": 0,
                "debuggingPort": 9222,
                "core_version": "119.1.0.16",
                "core_type": "Chromium",
            }
        )
        client.start_store(store)
        payload = http.post.call_args.kwargs["json"]
        self.assertEqual(payload["browserId"], "123")
        self.assertNotIn("browserOauth", payload)
        self.assertFalse(payload["privacyMode"])


if __name__ == "__main__":
    unittest.main()
