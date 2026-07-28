from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from ziniao_automation.browser_session_cache import (
    BrowserSessionCache,
    BrowserSessionCacheEntry,
    DevToolsEndpoint,
    StoreBrowserLease,
)
from ziniao_automation.errors import ZiniaoConnectionError
from ziniao_automation.models import StartedStore, StoreInfo


class BrowserSessionCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.cache = BrowserSessionCache(self.directory)
        self.store = StoreInfo("store-1", "Demo", "secret")
        self.started = StartedStore(
            store=self.store,
            debugging_port=9222,
            core_version="146.1.4.29",
            core_type="Chromium",
            browser_path="/Applications/Ziniao Chromium",
            launcher_page="",
            ip_detection_page="",
            download_path="",
        )
        self.endpoint = DevToolsEndpoint(9222, "browser-instance-1")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _entry(self) -> BrowserSessionCacheEntry:
        return BrowserSessionCacheEntry.from_started(
            self.started,
            self.endpoint,
        )

    def test_save_and_load_round_trip(self) -> None:
        entry = self._entry()

        self.cache.save(entry)
        loaded = self.cache.load(self.store.browser_id)

        self.assertEqual(loaded, entry)
        self.assertEqual(
            loaded.to_started_store(self.store).debugging_port,
            9222,
        )

    def test_corrupt_cache_is_treated_as_miss(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.cache._entry_path(self.store.browser_id)
        path.write_text("{not-json", encoding="utf-8")

        self.assertIsNone(self.cache.load(self.store.browser_id))

    def test_matching_devtools_uuid_is_reused(self) -> None:
        entry = self._entry()
        self.cache.save(entry)
        with patch.object(
            self.cache,
            "probe",
            return_value=self.endpoint,
        ):
            resolved = self.cache.resolve_live(self.store.browser_id)

        self.assertEqual(resolved, entry)

    def test_changed_devtools_uuid_invalidates_cache(self) -> None:
        self.cache.save(self._entry())
        with patch.object(
            self.cache,
            "probe",
            return_value=DevToolsEndpoint(9222, "different-browser"),
        ):
            resolved = self.cache.resolve_live(self.store.browser_id)

        self.assertIsNone(resolved)
        self.assertIsNone(self.cache.load(self.store.browser_id))

    def test_probe_requires_real_browser_websocket_endpoint(self) -> None:
        response = Mock()
        response.json.return_value = {
            "webSocketDebuggerUrl": (
                "ws://127.0.0.1:9222/devtools/browser/browser-instance-1"
            )
        }
        with patch(
            "ziniao_automation.browser_session_cache.requests.get",
            return_value=response,
        ):
            endpoint = self.cache.probe(9222)

        self.assertEqual(endpoint, self.endpoint)
        response.raise_for_status.assert_called_once_with()

    def test_store_lease_blocks_second_same_store_holder(self) -> None:
        first = StoreBrowserLease(
            self.directory,
            self.store.browser_id,
            timeout_seconds=0.1,
        ).acquire()
        try:
            second = StoreBrowserLease(
                self.directory,
                self.store.browser_id,
                timeout_seconds=0.05,
            )
            with self.assertRaises(ZiniaoConnectionError):
                second.acquire()
        finally:
            first.release()

    def test_cache_file_does_not_expose_store_id_in_filename(self) -> None:
        self.cache.save(self._entry())
        files = list(self.directory.glob("*.json"))

        self.assertEqual(len(files), 1)
        self.assertNotIn(self.store.browser_id, files[0].name)
        payload = json.loads(files[0].read_text(encoding="utf-8"))
        self.assertEqual(payload["storeId"], self.store.browser_id)


if __name__ == "__main__":
    unittest.main()
