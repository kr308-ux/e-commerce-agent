from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from ziniao_automation.browser_startup import (
    _wait_for_store_home,
    ensure_store_browser_ready,
    is_store_home_url,
    is_store_login_url,
)
from ziniao_automation.errors import ZiniaoConnectionError


class BrowserStartupTests(unittest.TestCase):
    def test_store_home_url_requires_known_seller_home(self) -> None:
        self.assertTrue(
            is_store_home_url(
                "https://seller-us.tiktok.com/homepage?shop_region=US"
            )
        )
        self.assertTrue(
            is_store_home_url("https://seller.tiktokglobalshop.com/")
        )
        self.assertFalse(
            is_store_home_url(
                "https://affiliate.tiktokshopglobalselling.com/"
                "connection/creator"
            )
        )
        self.assertFalse(is_store_home_url("https://example.test/homepage"))
        self.assertTrue(
            is_store_login_url(
                "https://seller.tiktokshopglobalselling.com/account/login"
            )
        )

    def _connection(self, current_url: str) -> tuple[Mock, Mock]:
        driver = Mock()
        driver.window_handles = ["home"]
        driver.current_window_handle = "home"
        driver.current_url = current_url
        driver.title = "TikTok Shop Seller Center"
        driver.execute_script.side_effect = ["complete", True]

        started = Mock(debugging_port=9222)
        session = Mock(driver=driver, started=started)
        cache_entry = Mock(devtools_browser_id="browser-1")
        cache = Mock()
        cache.resolve_live.return_value = cache_entry
        connection = Mock(
            session=session,
            cache=cache,
            connection_mode="reused",
        )
        return connection, driver

    def test_ready_home_reuses_browser_and_releases_connection(self) -> None:
        connection, driver = self._connection(
            "https://seller-us.tiktok.com/homepage?shop_region=US"
        )
        settings = Mock(
            browser_home_timeout_seconds=1,
            browser_home_stable_seconds=0,
        )

        with patch(
            "ziniao_automation.browser_startup.ZiniaoProcessManager"
        ) as manager, patch(
            "ziniao_automation.browser_startup.connect_reusable_store",
            return_value=connection,
        ) as connect:
            result = ensure_store_browser_ready(settings, "store-1")

        manager.return_value.ensure_started.assert_called_once_with(
            restart_incompatible=True
        )
        connect.assert_called_once_with(settings, "store-1")
        driver.switch_to.window.assert_called_once_with("home")
        connection.close.assert_called_once_with()
        self.assertTrue(result.home_page_ready)
        self.assertEqual(result.connection_mode, "reused")
        self.assertEqual(result.debugging_port, 9222)

    def test_non_home_page_fails_without_restarting_endpoint(self) -> None:
        connection, _driver = self._connection(
            "https://affiliate.tiktokshopglobalselling.com/connection/creator"
        )
        settings = Mock(
            browser_home_timeout_seconds=0,
            browser_home_stable_seconds=0,
        )

        with patch(
            "ziniao_automation.browser_startup.ZiniaoProcessManager"
        ), patch(
            "ziniao_automation.browser_startup.connect_reusable_store",
            return_value=connection,
        ):
            with self.assertRaises(ZiniaoConnectionError):
                ensure_store_browser_ready(settings, "store-1")

        connection.close.assert_called_once_with()

    def test_login_page_waits_for_user_then_continues_to_home(self) -> None:
        driver = Mock()
        notify = Mock()
        login_url = (
            "https://seller.tiktokshopglobalselling.com/account/login"
        )
        home = (
            "https://seller.tiktokshopglobalselling.com/",
            "TikTok Shop Seller Center",
        )
        with (
            patch(
                "ziniao_automation.browser_startup._activate_store_home",
                side_effect=[None, home],
            ),
            patch(
                "ziniao_automation.browser_startup._active_store_login",
                return_value=login_url,
            ),
            patch(
                "ziniao_automation.browser_startup.time.monotonic",
                side_effect=[0.0, 0.0, 0.25],
            ),
            patch("ziniao_automation.browser_startup.time.sleep"),
        ):
            result = _wait_for_store_home(
                driver,
                timeout_seconds=1,
                stable_seconds=0,
                login_wait_seconds=0,
                on_login_required=notify,
            )

        self.assertEqual(result, home)
        notify.assert_called_once_with(login_url)

    def test_finite_login_wait_fails_after_timeout(self) -> None:
        driver = Mock()
        login_url = (
            "https://seller.tiktokshopglobalselling.com/account/login"
        )
        with (
            patch(
                "ziniao_automation.browser_startup._activate_store_home",
                return_value=None,
            ),
            patch(
                "ziniao_automation.browser_startup._active_store_login",
                return_value=login_url,
            ),
            patch(
                "ziniao_automation.browser_startup.time.monotonic",
                side_effect=[0.0, 0.0, 1.1],
            ),
            patch("ziniao_automation.browser_startup.time.sleep"),
        ):
            with self.assertRaisesRegex(
                ZiniaoConnectionError,
                "等待用户完成.*超时",
            ):
                _wait_for_store_home(
                    driver,
                    timeout_seconds=1,
                    stable_seconds=0,
                    login_wait_seconds=1,
                )


if __name__ == "__main__":
    unittest.main()
