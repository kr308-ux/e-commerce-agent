from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from ziniao_automation.browser_session_cache import DevToolsEndpoint
from ziniao_automation.browser_connection import connect_reusable_store
from ziniao_automation.models import StartedStore, StoreInfo


class ReusableBrowserConnectionTests(unittest.TestCase):
    def _fixtures(self):
        settings = Mock(
            browser_session_dir=Path("/tmp/ziniao-browser-connection-test"),
            browser_probe_timeout_seconds=2,
            browser_lock_timeout_seconds=30,
            reuse_browser_session=True,
        )
        store = StoreInfo("store-1", "Demo", "secret")
        started = StartedStore(
            store=store,
            debugging_port=9222,
            core_version="146.1.4.29",
            core_type="Chromium",
            browser_path="",
            launcher_page="",
            ip_detection_page="",
            download_path="",
        )
        client = Mock()
        client.list_stores.return_value = [store]
        lease = Mock()
        lease_factory = Mock()
        lease_factory.acquire.return_value = lease
        session = Mock(started=started)
        return settings, store, started, client, lease, lease_factory, session

    def test_live_cache_attaches_without_starting_browser(self) -> None:
        (
            settings,
            _store,
            started,
            client,
            lease,
            lease_factory,
            session,
        ) = self._fixtures()
        cached = Mock()
        cached.to_started_store.return_value = started
        cache = Mock()
        cache.resolve_live.return_value = cached

        with (
            patch(
                "ziniao_automation.browser_connection.ZiniaoProcessManager"
            ),
            patch(
                "ziniao_automation.browser_connection.ZiniaoClient",
                return_value=client,
            ),
            patch(
                "ziniao_automation.browser_connection.BrowserSessionCache",
                return_value=cache,
            ),
            patch(
                "ziniao_automation.browser_connection.StoreBrowserLease",
                return_value=lease_factory,
            ),
            patch(
                "ziniao_automation.browser_connection.SeleniumStoreSession",
                return_value=session,
            ),
        ):
            connection = connect_reusable_store(settings, "store-1")

        session.attach.assert_called_once_with(started)
        session.connect.assert_not_called()
        cache.save.assert_not_called()
        self.assertEqual(connection.connection_mode, "reused")
        connection.close()
        session.close.assert_called_once()
        lease.release.assert_called_once()

    def test_cache_miss_starts_once_and_persists_verified_endpoint(
        self,
    ) -> None:
        (
            settings,
            _store,
            started,
            client,
            lease,
            lease_factory,
            session,
        ) = self._fixtures()
        cache = Mock()
        cache.resolve_live.return_value = None
        cache.probe.return_value = DevToolsEndpoint(
            9222,
            "browser-instance-1",
        )

        with (
            patch(
                "ziniao_automation.browser_connection.ZiniaoProcessManager"
            ),
            patch(
                "ziniao_automation.browser_connection.ZiniaoClient",
                return_value=client,
            ),
            patch(
                "ziniao_automation.browser_connection.BrowserSessionCache",
                return_value=cache,
            ),
            patch(
                "ziniao_automation.browser_connection.StoreBrowserLease",
                return_value=lease_factory,
            ),
            patch(
                "ziniao_automation.browser_connection.SeleniumStoreSession",
                return_value=session,
            ),
        ):
            connection = connect_reusable_store(settings, "store-1")

        session.connect.assert_called_once_with()
        session.attach.assert_not_called()
        cache.probe.assert_called_once_with(started.debugging_port)
        cache.save.assert_called_once()
        self.assertEqual(connection.connection_mode, "started")
        connection.close()
        lease.release.assert_called_once()


if __name__ == "__main__":
    unittest.main()
