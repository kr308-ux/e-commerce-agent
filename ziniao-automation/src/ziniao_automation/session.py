"""Managed Selenium session attached to one Ziniao store window."""

from __future__ import annotations

from types import TracebackType

from selenium import webdriver
from selenium.webdriver.chrome.service import Service

from .client import ZiniaoClient
from .config import ZiniaoSettings
from .drivers import resolve_driver
from .models import StartedStore, StoreInfo


class SeleniumStoreSession:
    def __init__(
        self,
        client: ZiniaoClient,
        settings: ZiniaoSettings,
        store: StoreInfo,
    ):
        self.client = client
        self.settings = settings
        self.store = store
        self.started: StartedStore | None = None
        self.driver: webdriver.Chrome | None = None

    def connect(self) -> "SeleniumStoreSession":
        if self.driver is not None:
            return self
        self.started = self.client.start_store(self.store)
        try:
            driver_path = resolve_driver(self.started, self.settings)
            options = webdriver.ChromeOptions()
            options.add_argument("--log-level=3")
            options.add_experimental_option(
                "debuggerAddress",
                f"127.0.0.1:{self.started.debugging_port}",
            )
            self.driver = webdriver.Chrome(
                service=Service(str(driver_path)),
                options=options,
            )
            self.driver.set_page_load_timeout(
                self.settings.request_timeout_seconds
            )
            self.driver.set_script_timeout(self.settings.request_timeout_seconds)
            return self
        except Exception:
            self.client.stop_store(self.started)
            self.started = None
            raise

    def close(self) -> None:
        driver, started = self.driver, self.started
        self.driver = None
        self.started = None
        if driver is not None:
            try:
                driver.quit()
            except Exception:
                pass
        if started is not None:
            self.client.stop_store(started)

    def __enter__(self) -> "SeleniumStoreSession":
        return self.connect()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()
