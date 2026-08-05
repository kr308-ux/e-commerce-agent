"""Managed Selenium session attached to one Ziniao store window."""

from __future__ import annotations

from types import TracebackType

from selenium import webdriver
from selenium.webdriver.chrome.service import Service

from .client import ZiniaoClient
from .config import ZiniaoSettings
from .drivers import resolve_driver
from .errors import ZiniaoConnectionError
from .models import StartedStore, StoreInfo


class SeleniumStoreSession:
    def __init__(
        self,
        client: ZiniaoClient,
        settings: ZiniaoSettings,
        store: StoreInfo,
        *,
        stop_store_on_close: bool = False,
    ):
        self.client = client
        self.settings = settings
        self.store = store
        self.stop_store_on_close = stop_store_on_close
        self.started: StartedStore | None = None
        self.driver: webdriver.Chrome | None = None

    def connect(self) -> "SeleniumStoreSession":
        if self.driver is not None:
            return self
        started = self.client.start_store(
            self.store,
            privacy_mode=self.settings.privacy_mode,
        )
        try:
            return self._attach_driver(started)
        except Exception:
            self.client.stop_store(started)
            self.started = None
            raise

    def attach(self, started: StartedStore) -> "SeleniumStoreSession":
        """Attach to a verified running browser without calling startBrowser."""
        if self.driver is not None:
            return self
        if started.store.browser_id != self.store.browser_id:
            raise ZiniaoConnectionError(
                "不能把 Selenium 会话附加到其他店铺的浏览器。"
            )
        try:
            return self._attach_driver(started)
        except Exception:
            self.started = None
            self.driver = None
            raise

    def _attach_driver(
        self,
        started: StartedStore,
    ) -> "SeleniumStoreSession":
        driver_path = resolve_driver(started, self.settings)
        options = webdriver.ChromeOptions()
        options.add_argument("--log-level=3")
        options.add_experimental_option(
            "debuggerAddress",
            f"127.0.0.1:{started.debugging_port}",
        )
        driver: webdriver.Chrome | None = None
        try:
            driver = webdriver.Chrome(
                service=Service(str(driver_path)),
                options=options,
            )
            driver.set_page_load_timeout(
                self.settings.request_timeout_seconds
            )
            driver.set_script_timeout(
                self.settings.request_timeout_seconds
            )
        except Exception:
            if driver is not None:
                try:
                    self._detach_driver(driver)
                except Exception:
                    pass
            raise
        self.started = started
        self.driver = driver
        return self

    @staticmethod
    def _detach_driver(driver: webdriver.Chrome) -> None:
        """Terminate only the local driver transport, never the store browser."""
        executor = getattr(driver, "command_executor", None)
        close = getattr(executor, "close", None)
        if callable(close):
            close()
        service = getattr(driver, "service", None)
        process = getattr(service, "process", None)
        if process is None:
            return
        process.terminate()
        try:
            process.wait(timeout=3)
        except Exception:
            process.kill()
            process.wait(timeout=3)
        try:
            service.process = None
        except Exception:
            pass

    def close(self, *, stop_store: bool | None = None) -> None:
        driver, started = self.driver, self.started
        self.driver = None
        self.started = None
        should_stop_store = (
            self.stop_store_on_close
            if stop_store is None
            else stop_store
        )
        if driver is not None:
            try:
                if should_stop_store:
                    driver.quit()
                else:
                    self._detach_driver(driver)
            except Exception:
                pass
        if should_stop_store and started is not None:
            self.client.stop_store(started)

    def shutdown_store(self) -> None:
        """Explicit user-directed shutdown of the attached store browser."""
        self.close(stop_store=True)

    def __enter__(self) -> "SeleniumStoreSession":
        return self.connect()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()
