"""Shared reusable Ziniao store-browser connection lifecycle."""

from __future__ import annotations

from dataclasses import dataclass

from .browser_session_cache import (
    BrowserSessionCache,
    BrowserSessionCacheEntry,
    StoreBrowserLease,
)
from .client import ZiniaoClient
from .config import ZiniaoSettings
from .errors import (
    ZiniaoConnectionError,
    ZiniaoStoreSelectionError,
)
from .models import StoreInfo
from .process import ZiniaoProcessManager
from .session import SeleniumStoreSession


def _select_store(
    stores: list[StoreInfo],
    store_id: str,
) -> StoreInfo:
    matches = [
        store
        for store in stores
        if store.browser_id == store_id and not store.is_expired
    ]
    if len(matches) != 1:
        raise ZiniaoStoreSelectionError(
            "未找到唯一且未过期的已授权目标店铺。"
        )
    return matches[0]


@dataclass
class ReusableStoreConnection:
    settings: ZiniaoSettings
    client: ZiniaoClient
    store: StoreInfo
    session: SeleniumStoreSession
    lease: StoreBrowserLease
    connection_mode: str
    cache_persisted: bool
    _closed: bool = False

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.session.close()
        finally:
            self.lease.release()

    def __enter__(self) -> "ReusableStoreConnection":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


def connect_reusable_store(
    settings: ZiniaoSettings,
    store_id: str,
) -> ReusableStoreConnection:
    """Attach to a live cached store browser, starting only on cache miss."""
    requested_store_id = str(store_id or "").strip()
    manager = ZiniaoProcessManager(settings)
    manager.ensure_started(restart=False)
    client = ZiniaoClient(settings)
    client.update_core()
    store = _select_store(client.list_stores(), requested_store_id)
    cache = BrowserSessionCache(
        settings.browser_session_dir,
        probe_timeout_seconds=settings.browser_probe_timeout_seconds,
    )
    lease = StoreBrowserLease(
        settings.browser_session_dir,
        store.browser_id,
        timeout_seconds=settings.browser_lock_timeout_seconds,
    ).acquire()
    session = SeleniumStoreSession(client, settings, store)
    connection_mode = "started"
    cache_persisted = False
    try:
        cached = (
            cache.resolve_live(store.browser_id)
            if settings.reuse_browser_session
            else None
        )
        if cached is not None:
            session.attach(cached.to_started_store(store))
            connection_mode = "reused"
            cache_persisted = True
        else:
            session.connect()
            if settings.reuse_browser_session:
                assert session.started is not None
                endpoint = cache.probe(session.started.debugging_port)
                if endpoint is None:
                    raise ZiniaoConnectionError(
                        "店铺浏览器已启动，但无法验证 Chrome DevTools "
                        "端点，未写入跨进程复用缓存。"
                    )
                cache.save(
                    BrowserSessionCacheEntry.from_started(
                        session.started,
                        endpoint,
                    )
                )
                cache_persisted = True
    except Exception:
        session.close()
        lease.release()
        raise
    return ReusableStoreConnection(
        settings=settings,
        client=client,
        store=store,
        session=session,
        lease=lease,
        connection_mode=connection_mode,
        cache_persisted=cache_persisted,
    )
