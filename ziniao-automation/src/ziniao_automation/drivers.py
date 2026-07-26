"""Prepare the ChromeDriver matching the Ziniao store core."""

from __future__ import annotations

import hashlib
import os
import platform
from pathlib import Path
from urllib.parse import urlparse

import requests

from .config import ZiniaoSettings
from .errors import ZiniaoDriverError
from .models import StartedStore


OFFICIAL_CDN_HOST = "cdn-superbrowser-attachment.ziniao.com"


def _sha1(path: Path) -> str:
    digest = hashlib.sha1()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _built_in_driver(started: StartedStore) -> Path | None:
    if not started.browser_path:
        return None
    browser_path = Path(started.browser_path)
    executable_name = "webdriver.exe" if platform.system() == "Windows" else "webdriver"
    candidates = []
    if browser_path.is_dir():
        candidates.append(browser_path / executable_name)
    else:
        candidates.extend(
            [
                browser_path.parent / executable_name,
                browser_path / executable_name,
            ]
        )
    return next((candidate for candidate in candidates if candidate.is_file()), None)


def _config_url() -> str:
    system = platform.system()
    if system == "Windows":
        return f"https://{OFFICIAL_CDN_HOST}/webdriver/exe_32/config.json"
    if system == "Darwin":
        machine = platform.machine()
        if machine == "arm64":
            architecture = "arm64"
        elif machine == "x86_64":
            architecture = "x64"
        else:
            raise ZiniaoDriverError(f"不支持的 macOS 架构：{machine}")
        return (
            f"https://{OFFICIAL_CDN_HOST}/webdriver/mac/"
            f"{architecture}/config.json"
        )
    raise ZiniaoDriverError(
        "当前系统应使用紫鸟内核自带的 webdriver，未找到可用驱动。"
    )


def _download_verified_driver(
    started: StartedStore,
    settings: ZiniaoSettings,
) -> Path:
    if not started.core_version:
        raise ZiniaoDriverError("startBrowser 未返回 core_version。")
    major_version = started.core_version.split(".", 1)[0]
    try:
        response = requests.get(
            _config_url(),
            timeout=settings.request_timeout_seconds,
        )
        response.raise_for_status()
        entries = response.json()
    except (requests.RequestException, ValueError) as error:
        raise ZiniaoDriverError("无法读取紫鸟官方 ChromeDriver 配置。") from error
    if not isinstance(entries, list):
        raise ZiniaoDriverError("紫鸟官方 ChromeDriver 配置格式无效。")
    expected_name = f"chromedriver{major_version}"
    entry = next(
        (
            item
            for item in entries
            if isinstance(item, dict) and item.get("name") == expected_name
        ),
        None,
    )
    if entry is None:
        raise ZiniaoDriverError(
            f"紫鸟官方驱动列表中没有内核 {started.core_version} 的驱动。"
        )
    download_url = str(entry.get("url") or "")
    expected_sha1 = str(entry.get("sha1") or "").lower()
    parsed_url = urlparse(download_url)
    if parsed_url.scheme != "https" or parsed_url.hostname != OFFICIAL_CDN_HOST:
        raise ZiniaoDriverError("紫鸟驱动下载地址不是受信任的官方 CDN。")
    settings.driver_dir.mkdir(parents=True, exist_ok=True)
    suffix = ".exe" if platform.system() == "Windows" else ""
    target = settings.driver_dir / f"{expected_name}{suffix}"
    if target.is_file() and _sha1(target) == expected_sha1:
        return target
    temporary = target.with_name(f"{target.name}.download")
    try:
        with requests.get(
            download_url,
            stream=True,
            timeout=settings.request_timeout_seconds,
        ) as download:
            download.raise_for_status()
            with temporary.open("wb") as file:
                for chunk in download.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        file.write(chunk)
        if _sha1(temporary) != expected_sha1:
            raise ZiniaoDriverError("下载的 ChromeDriver SHA-1 校验失败。")
        os.replace(temporary, target)
        target.chmod(0o755)
        return target
    except requests.RequestException as error:
        raise ZiniaoDriverError("下载紫鸟官方 ChromeDriver 失败。") from error
    finally:
        temporary.unlink(missing_ok=True)


def resolve_driver(started: StartedStore, settings: ZiniaoSettings) -> Path:
    built_in = _built_in_driver(started)
    if built_in is not None:
        return built_in
    if started.core_type not in {"", "0", "Chromium"}:
        raise ZiniaoDriverError(f"暂不支持紫鸟内核类型：{started.core_type}")
    return _download_verified_driver(started, settings)
