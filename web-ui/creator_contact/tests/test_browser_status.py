import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, override_settings

from creator_contact.services.browser_status import browser_readiness


class BrowserReadinessTests(SimpleTestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.status_path = Path(self.temporary.name) / "status.json"
        self.status_path.write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "service": "ziniao-store-browser",
                    "storeId": "store-1",
                    "status": "ready",
                    "connectionMode": "reused",
                    "debuggingPort": 9222,
                    "devtoolsBrowserId": "browser-1",
                    "currentUrl": "https://seller-us.tiktok.com/homepage",
                    "title": "Seller Center",
                    "homePageReady": True,
                    "checkedAt": "2026-07-29T12:00:00+00:00",
                }
            ),
            encoding="utf-8",
        )

    def tearDown(self):
        self.temporary.cleanup()

    @override_settings(ZINIAO_BROWSER_STATUS_PROBE_TIMEOUT_SECONDS=0.1)
    def test_live_home_page_reports_ready(self):
        version = Mock()
        version.__enter__ = Mock(return_value=version)
        version.__exit__ = Mock(return_value=False)
        version.read.return_value = json.dumps(
            {
                "webSocketDebuggerUrl": (
                    "ws://127.0.0.1:9222/devtools/browser/browser-1"
                )
            }
        ).encode()
        pages = Mock()
        pages.__enter__ = Mock(return_value=pages)
        pages.__exit__ = Mock(return_value=False)
        pages.read.return_value = json.dumps(
            [
                {
                    "type": "page",
                    "url": "https://seller-us.tiktok.com/homepage",
                }
            ]
        ).encode()

        with override_settings(ZINIAO_BROWSER_STATUS_PATH=self.status_path):
            with patch(
                "creator_contact.services.browser_status.urlopen",
                side_effect=[version, pages],
            ):
                status = browser_readiness("store-1")

        self.assertTrue(status["ready"])
        self.assertEqual(status["connectionModeLabel"], "复用已有浏览器")

    def test_stale_devtools_endpoint_reports_unavailable(self):
        with override_settings(ZINIAO_BROWSER_STATUS_PATH=self.status_path):
            with patch(
                "creator_contact.services.browser_status.urlopen",
                side_effect=OSError("closed"),
            ):
                status = browser_readiness("store-1")

        self.assertFalse(status["ready"])
        self.assertIn("DevTools", status["errorMessage"])

    @override_settings(ZINIAO_BROWSER_STATUS_PROBE_TIMEOUT_SECONDS=0.1)
    def test_login_error_recovers_when_home_page_appears(self):
        value = json.loads(self.status_path.read_text(encoding="utf-8"))
        value.update(
            {
                "status": "error",
                "homePageReady": False,
                "connectionMode": "reused",
                "currentUrl": "",
                "errorMessage": "登录状态已失效。",
            }
        )
        self.status_path.write_text(json.dumps(value), encoding="utf-8")
        version = Mock()
        version.__enter__ = Mock(return_value=version)
        version.__exit__ = Mock(return_value=False)
        version.read.return_value = json.dumps(
            {
                "webSocketDebuggerUrl": (
                    "ws://127.0.0.1:9222/devtools/browser/browser-1"
                )
            }
        ).encode()
        pages = Mock()
        pages.__enter__ = Mock(return_value=pages)
        pages.__exit__ = Mock(return_value=False)
        pages.read.return_value = json.dumps(
            [
                {
                    "type": "page",
                    "url": "https://seller-us.tiktok.com/homepage",
                }
            ]
        ).encode()

        with override_settings(ZINIAO_BROWSER_STATUS_PATH=self.status_path):
            with patch(
                "creator_contact.services.browser_status.urlopen",
                side_effect=[version, pages],
            ):
                status = browser_readiness("store-1")

        self.assertTrue(status["ready"])
        self.assertEqual(status["debuggingPort"], 9222)

    @override_settings(ZINIAO_BROWSER_STATUS_PROBE_TIMEOUT_SECONDS=0.1)
    def test_waiting_for_login_is_exposed_to_dashboard(self):
        value = json.loads(self.status_path.read_text(encoding="utf-8"))
        value.update(
            {
                "status": "waiting_for_login",
                "homePageReady": False,
                "connectionMode": "reused",
                "currentUrl": (
                    "https://seller-us.tiktok.com/account/login"
                ),
                "errorMessage": "等待用户输入验证码。",
            }
        )
        self.status_path.write_text(json.dumps(value), encoding="utf-8")
        version = Mock()
        version.__enter__ = Mock(return_value=version)
        version.__exit__ = Mock(return_value=False)
        version.read.return_value = json.dumps(
            {
                "webSocketDebuggerUrl": (
                    "ws://127.0.0.1:9222/devtools/browser/browser-1"
                )
            }
        ).encode()
        pages = Mock()
        pages.__enter__ = Mock(return_value=pages)
        pages.__exit__ = Mock(return_value=False)
        pages.read.return_value = json.dumps(
            [
                {
                    "type": "page",
                    "url": "https://seller-us.tiktok.com/account/login",
                }
            ]
        ).encode()

        with override_settings(ZINIAO_BROWSER_STATUS_PATH=self.status_path):
            with patch(
                "creator_contact.services.browser_status.urlopen",
                side_effect=[version, pages],
            ):
                status = browser_readiness("store-1")

        self.assertFalse(status["ready"])
        self.assertEqual(status["status"], "waiting_for_login")
        self.assertEqual(status["statusLabel"], "等待登录或验证码")
        self.assertEqual(status["debuggingPort"], 9222)
