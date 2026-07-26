"""Environment-only configuration for the Ziniao integration."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

from .errors import ZiniaoConfigurationError


PROJECT_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(PROJECT_ROOT / ".env")


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
