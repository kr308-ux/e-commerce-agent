"""Structured values returned by the Ziniao local API."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class StoreInfo:
    browser_id: str
    browser_name: str
    browser_oauth: str = field(repr=False)
    site_id: str = ""
    platform_id: str = ""
    platform_name: str = ""
    is_expired: bool = False

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "StoreInfo":
        return cls(
            browser_id=str(value.get("browserId") or ""),
            browser_name=str(value.get("browserName") or ""),
            browser_oauth=str(value.get("browserOauth") or ""),
            site_id=str(value.get("siteId") or ""),
            platform_id=str(value.get("platform_id") or ""),
            platform_name=str(value.get("platform_name") or ""),
            is_expired=bool(value.get("isExpired", False)),
        )

    @property
    def api_identity(self) -> tuple[str, str]:
        if self.browser_id:
            return "browserId", self.browser_id
        return "browserOauth", self.browser_oauth

    def to_public_dict(self) -> dict[str, object]:
        return {
            "browserId": self.browser_id,
            "browserName": self.browser_name,
            "siteId": self.site_id,
            "platformId": self.platform_id,
            "platformName": self.platform_name,
            "isExpired": self.is_expired,
        }


@dataclass(frozen=True)
class StartedStore:
    store: StoreInfo
    debugging_port: int
    core_version: str
    core_type: str
    browser_path: str
    launcher_page: str
    ip_detection_page: str
    download_path: str
    duplicate: int = 0
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_mapping(
        cls,
        store: StoreInfo,
        value: dict[str, Any],
    ) -> "StartedStore":
        return cls(
            store=store,
            debugging_port=int(value["debuggingPort"]),
            core_version=str(
                value.get("core_version") or value.get("coreVersion") or ""
            ),
            core_type=str(value.get("core_type") or value.get("coreType") or ""),
            browser_path=str(value.get("browserPath") or ""),
            launcher_page=str(value.get("launcherPage") or ""),
            ip_detection_page=str(value.get("ipDetectionPage") or ""),
            download_path=str(value.get("downloadPath") or ""),
            duplicate=int(value.get("duplicate") or 0),
            raw=value,
        )
