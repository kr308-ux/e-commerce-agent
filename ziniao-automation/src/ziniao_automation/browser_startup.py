"""Prewarm and verify one reusable Ziniao store browser."""

from __future__ import annotations

import json
import os
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

from selenium.common.exceptions import WebDriverException
from selenium.webdriver.remote.webdriver import WebDriver

from .browser_connection import connect_reusable_store
from .config import ZiniaoSettings
from .errors import ZiniaoConnectionError
from .process import ZiniaoProcessManager


STATUS_SCHEMA_VERSION = 1
STATUS_SERVICE_NAME = "ziniao-store-browser"


def _is_known_seller_host(host: str) -> bool:
    if host.startswith(("affiliate.", "creator.", "im.")):
        return False
    seller_tiktok_host = (
        ("seller" in host or host.startswith("shop."))
        and (
            host == "tiktok.com"
            or host.endswith(".tiktok.com")
            or host == "tiktokshop.com"
            or host.endswith(".tiktokshop.com")
        )
    )
    global_shop_host = (
        host == "tiktokglobalshop.com"
        or host.endswith(".tiktokglobalshop.com")
        or host == "tiktokshopglobalselling.com"
        or host.endswith(".tiktokshopglobalselling.com")
    )
    return seller_tiktok_host or global_shop_host


def is_store_home_url(url: object) -> bool:
    """Accept only known TikTok Shop seller home/dashboard URLs."""
    parsed = urlparse(str(url or "").strip())
    host = (parsed.hostname or "").lower().rstrip(".")
    path = parsed.path.lower().rstrip("/")
    if (
        parsed.scheme not in {"http", "https"}
        or not host
        or not _is_known_seller_host(host)
    ):
        return False
    return path in {"", "/home", "/homepage", "/dashboard"} or path.startswith(
        ("/home/", "/homepage/", "/dashboard/")
    )


def is_store_login_url(url: object) -> bool:
    parsed = urlparse(str(url or "").strip())
    host = (parsed.hostname or "").lower().rstrip(".")
    path = parsed.path.lower().rstrip("/")
    return (
        parsed.scheme in {"http", "https"}
        and _is_known_seller_host(host)
        and (
            path in {"/login", "/signin", "/account/login"}
            or path.startswith(("/login/", "/signin/", "/account/login/"))
        )
    )


@dataclass(frozen=True)
class BrowserStartupResult:
    store_id: str
    status: str
    connection_mode: str
    debugging_port: int
    devtools_browser_id: str
    current_url: str
    title: str
    home_page_ready: bool
    checked_at: str

    def to_mapping(self) -> dict[str, object]:
        return {
            "schemaVersion": STATUS_SCHEMA_VERSION,
            "service": STATUS_SERVICE_NAME,
            "storeId": self.store_id,
            "status": self.status,
            "connectionMode": self.connection_mode,
            "debuggingPort": self.debugging_port,
            "devtoolsBrowserId": self.devtools_browser_id,
            "currentUrl": self.current_url,
            "title": self.title,
            "homePageReady": self.home_page_ready,
            "checkedAt": self.checked_at,
        }


def _document_ready(driver: WebDriver) -> bool:
    try:
        state = driver.execute_script("return document.readyState")
        body_present = driver.execute_script(
            "return Boolean(document.body && document.body.isConnected)"
        )
    except WebDriverException:
        return False
    return state in {"interactive", "complete"} and body_present is True


def _activate_store_home(driver: WebDriver) -> tuple[str, str] | None:
    try:
        handles = list(driver.window_handles)
    except WebDriverException:
        return None
    try:
        current_handle = driver.current_window_handle
    except WebDriverException:
        current_handle = ""
    ordered_handles = [
        *([current_handle] if current_handle in handles else []),
        *(handle for handle in handles if handle != current_handle),
    ]
    for handle in ordered_handles:
        try:
            driver.switch_to.window(handle)
            current_url = str(driver.current_url or "")
            if is_store_home_url(current_url) and _document_ready(driver):
                return current_url, str(driver.title or "")
        except WebDriverException:
            continue
    return None


def _active_store_login(driver: WebDriver) -> str:
    try:
        handles = list(driver.window_handles)
    except WebDriverException:
        return ""
    for handle in handles:
        try:
            driver.switch_to.window(handle)
            current_url = str(driver.current_url or "")
            if is_store_login_url(current_url) and _document_ready(driver):
                return current_url
        except WebDriverException:
            continue
    return ""


def _wait_for_store_home(
    driver: WebDriver,
    *,
    timeout_seconds: float,
    stable_seconds: float,
    login_wait_seconds: float,
    on_login_required: Callable[[str], None] | None = None,
) -> tuple[str, str]:
    deadline = time.monotonic() + timeout_seconds
    stable_url = ""
    stable_title = ""
    stable_since = 0.0
    login_url = ""
    waiting_for_login = False
    login_deadline: float | None = None
    while True:
        now = time.monotonic()
        home = _activate_store_home(driver)
        if home is not None:
            current_url, title = home
            if current_url != stable_url:
                stable_url = current_url
                stable_title = title
                stable_since = now
                if stable_seconds <= 0:
                    return stable_url, stable_title
            elif now - stable_since >= stable_seconds:
                return stable_url, stable_title
        else:
            stable_url = ""
            stable_title = ""
            stable_since = 0.0
            current_login_url = _active_store_login(driver)
            if current_login_url:
                if not waiting_for_login:
                    waiting_for_login = True
                    login_url = current_login_url
                    if login_wait_seconds > 0:
                        login_deadline = now + login_wait_seconds
                    if on_login_required is not None:
                        on_login_required(login_url)
                elif current_login_url != login_url:
                    login_url = current_login_url
            elif not waiting_for_login:
                login_url = ""
        if waiting_for_login:
            if login_deadline is not None and now >= login_deadline:
                raise ZiniaoConnectionError(
                    "等待用户完成 TikTok Shop 登录或验证码超时。"
                    f"最后检测到的验证页面：{login_url}"
                )
        elif now >= deadline:
            break
        time.sleep(0.25)
    visible_urls: list[str] = []
    try:
        for handle in driver.window_handles:
            try:
                driver.switch_to.window(handle)
                url = str(driver.current_url or "")
                if url:
                    visible_urls.append(url)
            except WebDriverException:
                continue
    except WebDriverException:
        pass
    summary = ", ".join(visible_urls[:5]) or "没有可读取的页面"
    raise ZiniaoConnectionError(
        "店铺浏览器已连接，但未在限定时间内发现可验收的 TikTok Shop "
        f"店铺首页。当前页面：{summary}"
    )


def ensure_store_browser_ready(
    settings: ZiniaoSettings,
    store_id: str,
    *,
    on_login_required: Callable[[str], None] | None = None,
) -> BrowserStartupResult:
    """Start or reuse the store browser and verify a loaded store home tab."""
    normalized_store_id = str(store_id or "").strip()
    if not normalized_store_id:
        raise ZiniaoConnectionError("未配置要预启动的紫鸟店铺 ID。")

    ZiniaoProcessManager(settings).ensure_started(
        restart_incompatible=True,
    )
    connection = connect_reusable_store(settings, normalized_store_id)
    try:
        session = connection.session
        if session.driver is None or session.started is None:
            raise ZiniaoConnectionError("店铺浏览器连接未返回可用的 Selenium 会话。")
        current_url, title = _wait_for_store_home(
            session.driver,
            timeout_seconds=settings.browser_home_timeout_seconds,
            stable_seconds=settings.browser_home_stable_seconds,
            login_wait_seconds=settings.browser_login_wait_seconds,
            on_login_required=on_login_required,
        )
        cache_entry = connection.cache.resolve_live(normalized_store_id)
        if cache_entry is None:
            raise ZiniaoConnectionError(
                "店铺首页已打开，但浏览器 DevTools 复用缓存未通过验收。"
            )
        return BrowserStartupResult(
            store_id=normalized_store_id,
            status="ready",
            connection_mode=connection.connection_mode,
            debugging_port=session.started.debugging_port,
            devtools_browser_id=cache_entry.devtools_browser_id,
            current_url=current_url,
            title=title,
            home_page_ready=True,
            checked_at=datetime.now(UTC).isoformat(),
        )
    finally:
        connection.close()


def write_browser_status(
    path: Path,
    payload: BrowserStartupResult | dict[str, object],
) -> None:
    """Atomically persist a local, non-secret browser readiness record."""
    value = (
        payload.to_mapping()
        if isinstance(payload, BrowserStartupResult)
        else dict(payload)
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / (
        f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    )
    try:
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
        temporary.chmod(0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def error_status(store_id: str, error: Exception) -> dict[str, object]:
    return {
        "schemaVersion": STATUS_SCHEMA_VERSION,
        "service": STATUS_SERVICE_NAME,
        "storeId": str(store_id or "").strip(),
        "status": "error",
        "connectionMode": "",
        "debuggingPort": 0,
        "devtoolsBrowserId": "",
        "currentUrl": "",
        "title": "",
        "homePageReady": False,
        "checkedAt": datetime.now(UTC).isoformat(),
        "errorMessage": str(error),
    }


def waiting_for_login_status(
    store_id: str,
    login_url: str,
) -> dict[str, object]:
    return {
        "schemaVersion": STATUS_SCHEMA_VERSION,
        "service": STATUS_SERVICE_NAME,
        "storeId": str(store_id or "").strip(),
        "status": "waiting_for_login",
        "connectionMode": "reused",
        "debuggingPort": 0,
        "devtoolsBrowserId": "",
        "currentUrl": str(login_url or ""),
        "title": "",
        "homePageReady": False,
        "checkedAt": datetime.now(UTC).isoformat(),
        "errorMessage": (
            "等待用户在已打开的店铺浏览器中完成登录或输入验证码。"
        ),
    }
