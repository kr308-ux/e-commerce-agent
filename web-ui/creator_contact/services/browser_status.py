"""Read and live-probe the browser readiness record shown by Django."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import urlopen

from django.conf import settings


STATUS_SERVICE_NAME = "ziniao-store-browser"


def _empty_status(message: str) -> dict[str, object]:
    return {
        "ready": False,
        "status": "unavailable",
        "statusLabel": "浏览器未就绪",
        "connectionMode": "",
        "connectionModeLabel": "",
        "debuggingPort": None,
        "currentUrl": "",
        "title": "",
        "checkedAt": "",
        "errorMessage": message,
    }


def _read_status(path: Path) -> dict[str, object] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None
    if (
        not isinstance(value, dict)
        or value.get("schemaVersion") != 1
        or value.get("service") != STATUS_SERVICE_NAME
    ):
        return None
    return value


def _live_pages(
    port: int,
    expected_browser_id: str,
    *,
    timeout_seconds: float,
) -> list[dict[str, object]] | None:
    try:
        with urlopen(
            f"http://127.0.0.1:{port}/json/version",
            timeout=timeout_seconds,
        ) as response:
            version = json.loads(response.read().decode("utf-8"))
        websocket_url = str(version.get("webSocketDebuggerUrl") or "")
        browser_id = urlparse(websocket_url).path.rsplit("/", 1)[-1]
        if not browser_id or browser_id != expected_browser_id:
            return None
        with urlopen(
            f"http://127.0.0.1:{port}/json/list",
            timeout=timeout_seconds,
        ) as response:
            pages = json.loads(response.read().decode("utf-8"))
    except (OSError, URLError, ValueError, TypeError, json.JSONDecodeError):
        return None
    if not isinstance(pages, list):
        return None
    return [page for page in pages if isinstance(page, dict)]


def _is_store_home_url(url: object) -> bool:
    parsed = urlparse(str(url or "").strip())
    host = (parsed.hostname or "").lower().rstrip(".")
    path = parsed.path.lower().rstrip("/")
    if (
        parsed.scheme not in {"http", "https"}
        or host.startswith(("affiliate.", "creator.", "im."))
    ):
        return False
    seller_host = (
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
    return (
        seller_host or global_shop_host
    ) and (
        path in {"", "/home", "/homepage", "/dashboard"}
        or path.startswith(("/home/", "/homepage/", "/dashboard/"))
    )


def _ready_status(
    value: dict[str, object],
    *,
    port: int,
    current_url: str,
) -> dict[str, object]:
    connection_mode = str(value.get("connectionMode") or "")
    return {
        "ready": True,
        "status": "ready",
        "statusLabel": "店铺首页已就绪",
        "connectionMode": connection_mode,
        "connectionModeLabel": (
            "复用已有浏览器"
            if connection_mode == "reused"
            else "本次启动新浏览器"
        ),
        "debuggingPort": port,
        "currentUrl": current_url,
        "title": str(value.get("title") or ""),
        "checkedAt": str(value.get("checkedAt") or ""),
        "errorMessage": "",
    }


def browser_readiness(store_id: str) -> dict[str, object]:
    """Return display-safe readiness after probing the saved DevTools endpoint."""
    value = _read_status(Path(settings.ZINIAO_BROWSER_STATUS_PATH))
    if value is None:
        return _empty_status("尚未执行店铺浏览器预启动。")

    expected_store = str(store_id or "").strip()
    if str(value.get("storeId") or "").strip() != expected_store:
        return _empty_status("浏览器状态属于其他店铺，请重新执行预启动。")

    try:
        port = int(value.get("debuggingPort") or 0)
    except (TypeError, ValueError):
        port = 0
    browser_id = str(value.get("devtoolsBrowserId") or "")
    current_url = str(value.get("currentUrl") or "")
    has_endpoint = 1 <= port <= 65535 and bool(browser_id)
    if not has_endpoint:
        status = _empty_status(
            str(value.get("errorMessage") or "浏览器状态记录不完整。")
        )
        status["status"] = "error"
        status["checkedAt"] = str(value.get("checkedAt") or "")
        return status

    pages = _live_pages(
        port,
        browser_id,
        timeout_seconds=settings.ZINIAO_BROWSER_STATUS_PROBE_TIMEOUT_SECONDS,
    )
    if pages is None:
        return _empty_status("浏览器 DevTools 端点已失效，请重新执行预启动。")
    if value.get("status") != "ready" or value.get("homePageReady") is not True:
        recovered_home_url = next(
            (
                str(page.get("url") or "")
                for page in pages
                if page.get("type") == "page"
                and _is_store_home_url(page.get("url"))
            ),
            "",
        )
        if recovered_home_url:
            return _ready_status(
                value,
                port=port,
                current_url=recovered_home_url,
            )
        status = _empty_status(
            str(value.get("errorMessage") or "店铺首页验收未通过。")
        )
        waiting_for_login = value.get("status") == "waiting_for_login"
        status["status"] = (
            "waiting_for_login" if waiting_for_login else "error"
        )
        if waiting_for_login:
            status["statusLabel"] = "等待登录或验证码"
            status["connectionMode"] = "reused"
            status["connectionModeLabel"] = "浏览器保持连接"
            status["debuggingPort"] = port
            status["currentUrl"] = str(value.get("currentUrl") or "")
        status["checkedAt"] = str(value.get("checkedAt") or "")
        return status
    if not current_url:
        return _empty_status("浏览器状态记录缺少已验收的店铺首页。")
    home_page_live = any(
        page.get("type") == "page" and str(page.get("url") or "") == current_url
        for page in pages
    )
    if not home_page_live:
        return _empty_status("已验收的店铺首页标签页已关闭或发生跳转。")

    return _ready_status(value, port=port, current_url=current_url)
