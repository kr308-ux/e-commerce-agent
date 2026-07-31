"""Cross-process registry for reusable Ziniao browser sessions."""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO
from urllib.parse import urlparse

import requests

from .errors import ZiniaoConnectionError
from .models import StartedStore, StoreInfo


SCHEMA_VERSION = 1


def _store_key(store_id: str) -> str:
    normalized = str(store_id or "").strip()
    if not normalized:
        raise ZiniaoConnectionError("浏览器会话缓存缺少 store_id。")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class DevToolsEndpoint:
    debugging_port: int
    browser_id: str


@dataclass(frozen=True)
class BrowserSessionCacheEntry:
    store_id: str
    debugging_port: int
    core_version: str
    core_type: str
    browser_path: str
    duplicate: int
    devtools_browser_id: str
    cached_at: str

    @classmethod
    def from_mapping(
        cls,
        value: dict[str, Any],
    ) -> "BrowserSessionCacheEntry":
        if int(value.get("schemaVersion") or 0) != SCHEMA_VERSION:
            raise ValueError("unsupported cache schema")
        entry = cls(
            store_id=str(value.get("storeId") or "").strip(),
            debugging_port=int(value.get("debuggingPort") or 0),
            core_version=str(value.get("coreVersion") or "").strip(),
            core_type=str(value.get("coreType") or "").strip(),
            browser_path=str(value.get("browserPath") or ""),
            duplicate=int(value.get("duplicate") or 0),
            devtools_browser_id=str(
                value.get("devtoolsBrowserId") or ""
            ).strip(),
            cached_at=str(value.get("cachedAt") or "").strip(),
        )
        if (
            not entry.store_id
            or not 1 <= entry.debugging_port <= 65535
            or not entry.core_version
            or not entry.devtools_browser_id
            or not entry.cached_at
        ):
            raise ValueError("incomplete browser session cache")
        return entry

    @classmethod
    def from_started(
        cls,
        started: StartedStore,
        endpoint: DevToolsEndpoint,
    ) -> "BrowserSessionCacheEntry":
        return cls(
            store_id=started.store.browser_id,
            debugging_port=started.debugging_port,
            core_version=started.core_version,
            core_type=started.core_type,
            browser_path=started.browser_path,
            duplicate=started.duplicate,
            devtools_browser_id=endpoint.browser_id,
            cached_at=datetime.now(UTC).isoformat(),
        )

    def to_mapping(self) -> dict[str, object]:
        return {
            "schemaVersion": SCHEMA_VERSION,
            "storeId": self.store_id,
            "debuggingPort": self.debugging_port,
            "coreVersion": self.core_version,
            "coreType": self.core_type,
            "browserPath": self.browser_path,
            "duplicate": self.duplicate,
            "devtoolsBrowserId": self.devtools_browser_id,
            "cachedAt": self.cached_at,
        }

    def to_started_store(self, store: StoreInfo) -> StartedStore:
        if store.browser_id != self.store_id:
            raise ZiniaoConnectionError("浏览器会话缓存与目标店铺不一致。")
        return StartedStore(
            store=store,
            debugging_port=self.debugging_port,
            core_version=self.core_version,
            core_type=self.core_type,
            browser_path=self.browser_path,
            launcher_page="",
            ip_detection_page="",
            download_path="",
            duplicate=self.duplicate,
        )


class BrowserSessionCache:
    def __init__(
        self,
        directory: Path,
        *,
        probe_timeout_seconds: float = 2.0,
        live_probe_attempts: int = 3,
        live_probe_retry_seconds: float = 0.15,
    ):
        self.directory = directory
        self.probe_timeout_seconds = probe_timeout_seconds
        self.live_probe_attempts = max(1, int(live_probe_attempts))
        self.live_probe_retry_seconds = max(
            0.0,
            float(live_probe_retry_seconds),
        )

    def _entry_path(self, store_id: str) -> Path:
        return self.directory / f"{_store_key(store_id)}.json"

    def load(self, store_id: str) -> BrowserSessionCacheEntry | None:
        path = self._entry_path(store_id)
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                return None
            entry = BrowserSessionCacheEntry.from_mapping(value)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return None
        if entry.store_id != str(store_id).strip():
            return None
        return entry

    def save(self, entry: BrowserSessionCacheEntry) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        target = self._entry_path(entry.store_id)
        temporary = self.directory / (
            f".{target.stem}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
        )
        try:
            temporary.write_text(
                json.dumps(
                    entry.to_mapping(),
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            temporary.chmod(0o600)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)

    def invalidate(self, store_id: str) -> None:
        self._entry_path(store_id).unlink(missing_ok=True)

    def probe(self, debugging_port: int) -> DevToolsEndpoint | None:
        if not 1 <= int(debugging_port) <= 65535:
            return None
        try:
            response = requests.get(
                f"http://127.0.0.1:{debugging_port}/json/version",
                timeout=self.probe_timeout_seconds,
            )
            response.raise_for_status()
            value = response.json()
        except (requests.RequestException, ValueError, TypeError):
            return None
        if not isinstance(value, dict):
            return None
        websocket_url = str(value.get("webSocketDebuggerUrl") or "")
        parsed = urlparse(websocket_url)
        path_prefix = "/devtools/browser/"
        try:
            websocket_port = parsed.port
        except ValueError:
            return None
        if (
            parsed.scheme not in {"ws", "wss"}
            or parsed.hostname not in {"127.0.0.1", "localhost"}
            or websocket_port != int(debugging_port)
            or not parsed.path.startswith(path_prefix)
        ):
            return None
        browser_id = parsed.path.removeprefix(path_prefix).strip("/")
        if not browser_id or "/" in browser_id:
            return None
        return DevToolsEndpoint(
            debugging_port=int(debugging_port),
            browser_id=browser_id,
        )

    def resolve_live(
        self,
        store_id: str,
    ) -> BrowserSessionCacheEntry | None:
        entry = self.load(store_id)
        if entry is None:
            return None
        endpoint = None
        for attempt in range(self.live_probe_attempts):
            endpoint = self.probe(entry.debugging_port)
            if endpoint is not None:
                break
            if (
                attempt + 1 < self.live_probe_attempts
                and self.live_probe_retry_seconds
            ):
                time.sleep(self.live_probe_retry_seconds)
        if (
            endpoint is None
            or endpoint.browser_id != entry.devtools_browser_id
        ):
            self.invalidate(store_id)
            return None
        return entry


class StoreBrowserLease:
    """Hold an exclusive store lock for the lifetime of one MCP session."""

    def __init__(
        self,
        directory: Path,
        store_id: str,
        *,
        timeout_seconds: float,
    ):
        self.directory = directory
        self.store_id = str(store_id or "").strip()
        self.timeout_seconds = timeout_seconds
        self._file: BinaryIO | None = None

    @property
    def path(self) -> Path:
        return self.directory / f"{_store_key(self.store_id)}.lock"

    def acquire(self) -> "StoreBrowserLease":
        if self._file is not None:
            return self
        self.directory.mkdir(parents=True, exist_ok=True)
        lock_file = self.path.open("a+b")
        deadline = time.monotonic() + self.timeout_seconds
        try:
            while True:
                try:
                    self._lock(lock_file)
                    self._file = lock_file
                    return self
                except (BlockingIOError, OSError):
                    if time.monotonic() >= deadline:
                        raise ZiniaoConnectionError(
                            "目标店铺正由另一个自动化进程操作，"
                            "等待浏览器控制权超时。"
                        )
                    time.sleep(0.1)
        except Exception:
            lock_file.close()
            raise

    @staticmethod
    def _lock(lock_file: BinaryIO) -> None:
        if os.name == "nt":
            import msvcrt

            lock_file.seek(0)
            if lock_file.read(1) == b"":
                lock_file.write(b"0")
                lock_file.flush()
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            return
        import fcntl

        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    @staticmethod
    def _unlock(lock_file: BinaryIO) -> None:
        if os.name == "nt":
            import msvcrt

            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            return
        import fcntl

        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def release(self) -> None:
        lock_file = self._file
        self._file = None
        if lock_file is None:
            return
        try:
            self._unlock(lock_file)
        finally:
            lock_file.close()

    def __enter__(self) -> "StoreBrowserLease":
        return self.acquire()

    def __exit__(self, *_args: object) -> None:
        self.release()
