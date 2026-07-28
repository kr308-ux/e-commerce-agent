from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from ziniao_automation.config import ZiniaoCredentials, ZiniaoSettings
from ziniao_automation.models import StartedStore, StoreInfo
from ziniao_automation.session import SeleniumStoreSession


class SeleniumStoreSessionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = StoreInfo("store-1", "Demo", "secret")
        self.started = StartedStore(
            store=self.store,
            debugging_port=9222,
            core_version="120",
            core_type="Chromium",
            browser_path="",
            launcher_page="",
            ip_detection_page="",
            download_path="",
        )
        self.settings = ZiniaoSettings(
            credentials=ZiniaoCredentials("company", "user", "password"),
            client_path=Path("/Applications/ziniao.app"),
        )

    def test_close_detaches_driver_and_preserves_store_by_default(self) -> None:
        client = Mock()
        driver = Mock()
        session = SeleniumStoreSession(client, self.settings, self.store)
        session.driver = driver
        session.started = self.started
        driver_process = driver.service.process

        session.close()

        driver.command_executor.close.assert_called_once()
        driver_process.terminate.assert_called_once()
        driver_process.wait.assert_called_once_with(timeout=3)
        self.assertIsNone(driver.service.process)
        driver.service.stop.assert_not_called()
        driver.quit.assert_not_called()
        client.stop_store.assert_not_called()

    def test_explicit_shutdown_closes_store_browser(self) -> None:
        client = Mock()
        driver = Mock()
        session = SeleniumStoreSession(client, self.settings, self.store)
        session.driver = driver
        session.started = self.started

        session.shutdown_store()

        driver.quit.assert_called_once()
        client.stop_store.assert_called_once_with(self.started)

    def test_attach_reuses_started_store_without_starting_browser(
        self,
    ) -> None:
        client = Mock()
        driver = Mock()
        with (
            patch(
                "ziniao_automation.session.resolve_driver",
                return_value=Path("/tmp/chromedriver"),
            ),
            patch(
                "ziniao_automation.session.webdriver.Chrome",
                return_value=driver,
            ),
        ):
            session = SeleniumStoreSession(
                client,
                self.settings,
                self.store,
            ).attach(self.started)

        client.start_store.assert_not_called()
        client.stop_store.assert_not_called()
        self.assertIs(session.started, self.started)
        self.assertIs(session.driver, driver)
        driver.set_page_load_timeout.assert_called_once_with(120)
        driver.set_script_timeout.assert_called_once_with(120)

    def test_attach_failure_never_stops_existing_browser(self) -> None:
        client = Mock()
        with (
            patch(
                "ziniao_automation.session.resolve_driver",
                return_value=Path("/tmp/chromedriver"),
            ),
            patch(
                "ziniao_automation.session.webdriver.Chrome",
                side_effect=RuntimeError("attach failed"),
            ),
        ):
            session = SeleniumStoreSession(
                client,
                self.settings,
                self.store,
            )
            with self.assertRaisesRegex(RuntimeError, "attach failed"):
                session.attach(self.started)

        client.start_store.assert_not_called()
        client.stop_store.assert_not_called()
        self.assertIsNone(session.started)
        self.assertIsNone(session.driver)


if __name__ == "__main__":
    unittest.main()
