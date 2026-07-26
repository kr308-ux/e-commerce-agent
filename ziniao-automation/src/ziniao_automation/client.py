"""Client for the local Ziniao WebDriver HTTP service."""

from __future__ import annotations

import time
import uuid
from typing import Any

import requests

from .config import ZiniaoSettings
from .errors import ZiniaoApiError, ZiniaoConnectionError
from .models import StartedStore, StoreInfo


RETRYABLE_CORE_CODES = {-10000, -10004}
AUTH_REQUIRED_CODES = {-10003, -10013}


class ZiniaoClient:
    def __init__(
        self,
        settings: ZiniaoSettings,
        *,
        session: requests.Session | None = None,
    ):
        self.settings = settings
        self.session = session or requests.Session()

    def _redact(self, message: object) -> str:
        text = str(message or "未知错误")
        credentials = self.settings.credentials
        for sensitive in (
            credentials.company,
            credentials.username,
            credentials.password,
        ):
            if sensitive:
                text = text.replace(sensitive, "[REDACTED]")
        return text

    @staticmethod
    def _status_code(result: dict[str, Any]) -> int | None:
        raw_code = result.get("statusCode")
        try:
            return int(raw_code)
        except (TypeError, ValueError):
            return None

    def _request(
        self,
        action: str,
        *,
        authenticated: bool = True,
        allow_failure: bool = False,
        **extra: object,
    ) -> dict[str, Any]:
        payload: dict[str, object] = {
            "action": action,
            "requestId": str(uuid.uuid4()),
            **extra,
        }
        if authenticated:
            credentials = self.settings.credentials
            payload.update(
                {
                    "company": credentials.company,
                    "username": credentials.username,
                    "password": credentials.password,
                }
            )
        try:
            response = self.session.post(
                self.settings.endpoint,
                json=payload,
                timeout=self.settings.request_timeout_seconds,
            )
            response.raise_for_status()
            result = response.json()
        except (requests.RequestException, ValueError) as error:
            raise ZiniaoConnectionError(
                f"无法调用本地紫鸟 WebDriver 服务 {self.settings.endpoint}。"
            ) from error
        if not isinstance(result, dict):
            raise ZiniaoConnectionError("紫鸟 WebDriver 返回了非 JSON 对象。")
        status_code = self._status_code(result)
        if status_code != 0 and not allow_failure:
            message = result.get("err") or result.get("LastError") or result.get("msg")
            raise ZiniaoApiError(action, status_code, self._redact(message))
        return result

    def update_core(self, *, poll_seconds: float = 2.0) -> None:
        deadline = time.monotonic() + self.settings.core_timeout_seconds
        last_code: int | None = None
        last_message = ""
        while time.monotonic() < deadline:
            result = self._request("updateCore", allow_failure=True)
            last_code = self._status_code(result)
            last_message = self._redact(result.get("msg") or result.get("err"))
            if last_code == 0:
                return
            if last_code in AUTH_REQUIRED_CODES:
                raise ZiniaoApiError("updateCore", last_code, last_message)
            if last_code not in RETRYABLE_CORE_CODES and last_code is not None:
                raise ZiniaoApiError("updateCore", last_code, last_message)
            time.sleep(poll_seconds)
        raise ZiniaoApiError(
            "updateCore",
            last_code,
            f"等待内核就绪超时：{last_message}",
        )

    def apply_auth(self) -> None:
        self._request("applyAuth")

    def list_stores(self) -> list[StoreInfo]:
        result = self._request("getBrowserList")
        raw_stores = result.get("browserList") or []
        if not isinstance(raw_stores, list):
            raise ZiniaoConnectionError("紫鸟返回的 browserList 格式无效。")
        return [
            StoreInfo.from_mapping(value)
            for value in raw_stores
            if isinstance(value, dict)
        ]

    def start_store(
        self,
        store: StoreInfo,
        *,
        headless: bool = False,
        privacy_mode: bool = False,
    ) -> StartedStore:
        identity_name, identity_value = store.api_identity
        result = self._request(
            "startBrowser",
            **{
                identity_name: identity_value,
                "isHeadless": headless,
                "privacyMode": privacy_mode,
                "cookieTypeLoad": 0,
                "cookieTypeSave": 0,
                "notPromptForDownload": 1,
                "isLoadUserPlugin": True,
            },
        )
        if not result.get("debuggingPort"):
            raise ZiniaoConnectionError("startBrowser 成功但未返回 debuggingPort。")
        return StartedStore.from_mapping(store, result)

    def stop_store(self, started: StartedStore) -> None:
        identity_name, identity_value = started.store.api_identity
        self._request(
            "stopBrowser",
            **{
                identity_name: identity_value,
                "duplicate": started.duplicate,
            },
        )

    def exit_client(self) -> None:
        self._request("exit")
