from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from ziniao_automation.actions.collaboration_sync import (
    IN_PROGRESS,
    _TABLE_SNAPSHOT_SCRIPT,
    _PageSnapshot,
    TargetCollaborationSync,
)
from ziniao_automation.errors import ZiniaoWorkflowError


def record(identifier: str, name: str) -> dict[str, object]:
    return {
        "id": identifier,
        "name": name,
        "update_time": "1784542166000",
        "creator_cnt": 26,
        "creator_added_cnt": 4,
        "creator_posted_cnt": 2,
        "product_cnt": 3,
        "group_status": 1,
    }


def snapshot(
    records: list[dict[str, object]],
    *,
    page: int,
    total: int,
) -> _PageSnapshot:
    return _PageSnapshot(
        records=tuple(records),
        current_page=page,
        page_size=1,
        total=total,
        source="react-table",
    )


class TargetCollaborationSyncTests(unittest.TestCase):
    def test_read_only_click_waits_random_one_to_two_seconds(self) -> None:
        driver = Mock()
        element = Mock()
        workflow = TargetCollaborationSync(driver)
        with (
            patch(
                "ziniao_automation.actions.collaboration_sync.random.uniform",
                return_value=1.625,
            ) as uniform,
            patch(
                "ziniao_automation.actions.collaboration_sync.time.sleep"
            ) as sleep,
        ):
            workflow._click_read_only(element)

        uniform.assert_called_once_with(1.0, 2.0)
        sleep.assert_called_once_with(1.625)
        element.click.assert_called_once_with()

    def test_record_maps_service_fields_and_preserves_raw_data(self) -> None:
        raw = record("7664550207413847821", "金色拉链+短裤13")

        option = TargetCollaborationSync.option_from_record(raw)

        self.assertEqual(option.name, "金色拉链+短裤13")
        self.assertEqual(
            option.invitationGroupId,
            "7664550207413847821",
        )
        self.assertEqual(option.status, IN_PROGRESS)
        self.assertEqual(option.productCount, 3)
        self.assertEqual(option.invitedCreatorCount, 26)
        self.assertEqual(option.acceptedCreatorCount, 4)
        self.assertEqual(option.promotedCreatorCount, 2)
        self.assertEqual(option.modifiedAt, "2026-07-20T10:09:26Z")
        self.assertEqual(option.rawData, raw)

    def test_dom_fallback_record_without_group_id_fails_closed(self) -> None:
        raw = {
            "name": "Fallback",
            "invitationGroupId": "",
            "productCount": "2 件商品",
            "invitedCreatorCount": "15",
            "acceptedCreatorCount": 8,
            "promotedCreatorCount": 1,
            "modifiedAt": "2026/7/19",
            "rawData": {
                "source": "dom",
                "rowText": "Fallback",
            },
        }

        with self.assertRaisesRegex(
            ZiniaoWorkflowError,
            "invitationGroupId",
        ):
            TargetCollaborationSync.option_from_record(raw)

    def test_react_script_uses_dynamic_fiber_prefix(self) -> None:
        self.assertIn("startsWith('__reactFiber$')", _TABLE_SNAPSHOT_SCRIPT)
        self.assertNotIn("__reactFiber$fixed", _TABLE_SNAPSHOT_SCRIPT)
        self.assertIn("props.pagination", _TABLE_SNAPSHOT_SCRIPT)
        self.assertIn("props.record", _TABLE_SNAPSHOT_SCRIPT)

    def test_sync_collects_and_deduplicates_all_pages(self) -> None:
        driver = Mock()
        sync = TargetCollaborationSync(driver)
        first = snapshot(
            [record("1", "One")],
            page=1,
            total=2,
        )
        second = snapshot(
            [record("2", "Two")],
            page=2,
            total=2,
        )
        next_button = Mock()
        sync.open_target_page = Mock(return_value="https://example.test")
        sync.activate_in_progress = Mock()
        sync._wait_for_ready_snapshot = Mock(return_value=first)
        sync._next_page_button = Mock(return_value=next_button)
        sync._click_read_only = Mock()
        sync._read_snapshot = Mock(return_value=second)
        sync._wait = Mock(
            side_effect=lambda condition, **_kwargs: condition(driver)
        )

        options = sync.sync_in_progress()

        self.assertEqual(
            [option["invitationGroupId"] for option in options],
            ["1", "2"],
        )
        sync._click_read_only.assert_called_once_with(next_button)
        sync._next_page_button.assert_called_once()

    def test_total_reached_does_not_click_next(self) -> None:
        sync = TargetCollaborationSync(Mock())
        only = snapshot(
            [record("1", "One")],
            page=1,
            total=1,
        )
        sync.open_target_page = Mock()
        sync.activate_in_progress = Mock()
        sync._wait_for_ready_snapshot = Mock(return_value=only)
        sync._next_page_button = Mock()

        options = sync.sync_in_progress()

        self.assertEqual(len(options), 1)
        sync._next_page_button.assert_not_called()

    def test_unknown_total_pages_until_next_is_disabled(self) -> None:
        sync = TargetCollaborationSync(Mock())
        first = snapshot(
            [record("1", "One")],
            page=1,
            total=-1,
        )
        second = snapshot(
            [record("2", "Two")],
            page=2,
            total=-1,
        )
        next_button = Mock()
        sync.open_target_page = Mock()
        sync.activate_in_progress = Mock()
        sync._wait_for_ready_snapshot = Mock(return_value=first)
        sync._next_page_button = Mock(
            side_effect=[next_button, None]
        )
        sync._click_read_only = Mock()
        sync._read_snapshot = Mock(return_value=second)
        sync._wait = Mock(
            side_effect=lambda condition, **_kwargs: condition(None)
        )

        options = sync.sync_in_progress()

        self.assertEqual(
            [option["invitationGroupId"] for option in options],
            ["1", "2"],
        )
        sync._click_read_only.assert_called_once_with(next_button)

    def test_missing_next_page_fails_instead_of_returning_partial_data(
        self,
    ) -> None:
        sync = TargetCollaborationSync(Mock())
        first = snapshot(
            [record("1", "One")],
            page=1,
            total=2,
        )
        sync.open_target_page = Mock()
        sync.activate_in_progress = Mock()
        sync._wait_for_ready_snapshot = Mock(return_value=first)
        sync._next_page_button = Mock(return_value=None)

        with self.assertRaisesRegex(
            ZiniaoWorkflowError,
            "分页提前结束",
        ):
            sync.sync_in_progress()

    def test_any_nonempty_row_without_group_id_fails_whole_sync(
        self,
    ) -> None:
        sync = TargetCollaborationSync(Mock())
        invalid = record("1", "One")
        invalid["id"] = ""
        complete = snapshot(
            [record("2", "Two"), invalid],
            page=1,
            total=2,
        )
        sync.open_target_page = Mock()
        sync.activate_in_progress = Mock()
        sync._wait_for_ready_snapshot = Mock(return_value=complete)

        with self.assertRaisesRegex(
            ZiniaoWorkflowError,
            "invitationGroupId",
        ):
            sync.sync_in_progress()

    def test_duplicate_group_id_fails_whole_sync(self) -> None:
        sync = TargetCollaborationSync(Mock())
        complete = snapshot(
            [record("1", "One"), record("1", "Duplicate")],
            page=1,
            total=2,
        )
        sync.open_target_page = Mock()
        sync.activate_in_progress = Mock()
        sync._wait_for_ready_snapshot = Mock(return_value=complete)

        with self.assertRaisesRegex(
            ZiniaoWorkflowError,
            "重复 invitationGroupId",
        ):
            sync.sync_in_progress()

    def test_repeated_page_fails_closed(self) -> None:
        sync = TargetCollaborationSync(Mock())
        first = snapshot(
            [record("1", "One")],
            page=1,
            total=2,
        )
        sync.open_target_page = Mock()
        sync.activate_in_progress = Mock()
        sync._wait_for_ready_snapshot = Mock(return_value=first)
        sync._next_page_button = Mock(return_value=Mock())
        sync._click_read_only = Mock()
        sync._wait = Mock(return_value=first)

        with self.assertRaisesRegex(
            ZiniaoWorkflowError,
            "分页没有前进",
        ):
            sync.sync_in_progress()


if __name__ == "__main__":
    unittest.main()
