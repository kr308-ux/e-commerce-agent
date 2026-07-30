"""Environment-only configuration for the Ziniao integration."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

from .errors import ZiniaoConfigurationError


PROJECT_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(PROJECT_ROOT / ".env")


def _boolean_from_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ZiniaoConfigurationError(
        f"{name} 必须是 true/false、yes/no、on/off 或 1/0。"
    )


@dataclass(frozen=True)
class ZiniaoCredentials:
    """Enterprise credentials kept out of repr and command-line arguments."""

    company: str
    username: str
    password: str = field(repr=False)

    @classmethod
    def from_env(cls) -> "ZiniaoCredentials":
        values = {
            "company": os.getenv("ZINIAO_COMPANY", "").strip(),
            "username": os.getenv("ZINIAO_USERNAME", "").strip(),
            "password": os.getenv("ZINIAO_PASSWORD", ""),
        }
        missing = [name for name, value in values.items() if not value]
        if missing:
            variables = ", ".join(f"ZINIAO_{name.upper()}" for name in missing)
            raise ZiniaoConfigurationError(f"缺少紫鸟本地环境变量：{variables}")
        return cls(**values)


@dataclass(frozen=True)
class ZiniaoSettings:
    """Validated local client, timeout, and driver settings."""

    credentials: ZiniaoCredentials
    client_path: Path
    socket_port: int = 16851
    request_timeout_seconds: int = 120
    core_timeout_seconds: int = 600
    driver_dir: Path = PROJECT_ROOT / "temporary" / "ziniao-webdrivers"
    browser_session_dir: Path = (
        PROJECT_ROOT / "temporary" / "ziniao-browser-sessions"
    )
    reuse_browser_session: bool = True
    browser_probe_timeout_seconds: float = 2.0
    browser_lock_timeout_seconds: float = 30.0
    browser_home_timeout_seconds: float = 60.0
    browser_home_stable_seconds: float = 2.0
    browser_login_wait_seconds: float = 0.0
    browser_status_path: Path = (
        PROJECT_ROOT / "temporary" / "ziniao-browser-status.json"
    )

    @classmethod
    def from_env(
        cls,
        *,
        credentials: ZiniaoCredentials | None = None,
    ) -> "ZiniaoSettings":
        client_default = (
            "/Applications/ziniao.app"
            if os.name != "nt"
            else r"C:\Program Files\ziniao\ziniao.exe"
        )
        driver_setting = Path(
            os.getenv("ZINIAO_DRIVER_DIR", "temporary/ziniao-webdrivers")
        )
        if not driver_setting.is_absolute():
            driver_setting = PROJECT_ROOT / driver_setting
        browser_session_setting = Path(
            os.getenv(
                "ZINIAO_BROWSER_SESSION_DIR",
                "temporary/ziniao-browser-sessions",
            )
        )
        if not browser_session_setting.is_absolute():
            browser_session_setting = (
                PROJECT_ROOT / browser_session_setting
            )
        browser_status_setting = Path(
            os.getenv(
                "ZINIAO_BROWSER_STATUS_PATH",
                "temporary/ziniao-browser-status.json",
            )
        )
        if not browser_status_setting.is_absolute():
            browser_status_setting = PROJECT_ROOT / browser_status_setting
        settings = cls(
            credentials=credentials or ZiniaoCredentials.from_env(),
            client_path=Path(os.getenv("ZINIAO_CLIENT_PATH", client_default)),
            socket_port=int(os.getenv("ZINIAO_SOCKET_PORT", "16851")),
            request_timeout_seconds=int(
                os.getenv("ZINIAO_REQUEST_TIMEOUT_SECONDS", "120")
            ),
            core_timeout_seconds=int(
                os.getenv("ZINIAO_CORE_TIMEOUT_SECONDS", "600")
            ),
            driver_dir=driver_setting.resolve(),
            browser_session_dir=browser_session_setting.resolve(),
            reuse_browser_session=_boolean_from_env(
                "ZINIAO_REUSE_BROWSER_SESSION",
                True,
            ),
            browser_probe_timeout_seconds=float(
                os.getenv("ZINIAO_BROWSER_PROBE_TIMEOUT_SECONDS", "2")
            ),
            browser_lock_timeout_seconds=float(
                os.getenv("ZINIAO_BROWSER_LOCK_TIMEOUT_SECONDS", "30")
            ),
            browser_home_timeout_seconds=float(
                os.getenv("ZINIAO_BROWSER_HOME_TIMEOUT_SECONDS", "60")
            ),
            browser_home_stable_seconds=float(
                os.getenv("ZINIAO_BROWSER_HOME_STABLE_SECONDS", "2")
            ),
            browser_login_wait_seconds=float(
                os.getenv("ZINIAO_BROWSER_LOGIN_WAIT_SECONDS", "0")
            ),
            browser_status_path=browser_status_setting.resolve(),
        )
        settings.validate()
        return settings

    @property
    def endpoint(self) -> str:
        return f"http://127.0.0.1:{self.socket_port}"

    def validate(self) -> None:
        if not 1 <= self.socket_port <= 65535:
            raise ZiniaoConfigurationError("ZINIAO_SOCKET_PORT 必须是有效端口。")
        if self.request_timeout_seconds < 120:
            raise ZiniaoConfigurationError(
                "紫鸟官方要求 ZINIAO_REQUEST_TIMEOUT_SECONDS 不低于 120。"
            )
        if self.core_timeout_seconds < self.request_timeout_seconds:
            raise ZiniaoConfigurationError(
                "ZINIAO_CORE_TIMEOUT_SECONDS 不能小于单次请求超时。"
            )
        if self.browser_probe_timeout_seconds <= 0:
            raise ZiniaoConfigurationError(
                "ZINIAO_BROWSER_PROBE_TIMEOUT_SECONDS 必须大于 0。"
            )
        if self.browser_lock_timeout_seconds <= 0:
            raise ZiniaoConfigurationError(
                "ZINIAO_BROWSER_LOCK_TIMEOUT_SECONDS 必须大于 0。"
            )
        if self.browser_home_timeout_seconds <= 0:
            raise ZiniaoConfigurationError(
                "ZINIAO_BROWSER_HOME_TIMEOUT_SECONDS 必须大于 0。"
            )
        if self.browser_home_stable_seconds < 0:
            raise ZiniaoConfigurationError(
                "ZINIAO_BROWSER_HOME_STABLE_SECONDS 不能小于 0。"
            )
        if self.browser_login_wait_seconds < 0:
            raise ZiniaoConfigurationError(
                "ZINIAO_BROWSER_LOGIN_WAIT_SECONDS 不能小于 0。"
            )
