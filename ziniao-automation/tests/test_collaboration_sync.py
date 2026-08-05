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


def dom_element(
    identifier: str,
    *,
    tag: str,
    text: str,
    href: str = "",
    role: str = "",
    descendants: list[Mock] | None = None,
) -> Mock:
    element = Mock()
    element.id = identifier
    element.tag_name = tag
    element.text = text
    element.rect = {"width": 120, "height": 32}
    element.is_displayed.return_value = True
    attributes = {"href": href, "role": role}
    element.get_attribute.side_effect = lambda name: attributes.get(name, "")
    element.find_elements.return_value = descendants or []
    return element


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

    def test_navigation_click_forces_site_window_open_into_current_tab(
        self,
    ) -> None:
        driver = Mock()
        element = Mock()
        workflow = TargetCollaborationSync(driver)
        with (
            patch(
                "ziniao_automation.actions.collaboration_sync.random.uniform",
                return_value=1.25,
            ),
            patch(
                "ziniao_automation.actions.collaboration_sync.time.sleep"
            ),
        ):
            workflow._click_navigation_in_current_tab(element)

        script = driver.execute_script.call_args.args[0]
        self.assertIn("window.location.assign", script)
        self.assertIn("target.click()", script)
        self.assertIs(driver.execute_script.call_args.args[1], element)
        driver.switch_to.new_window.assert_not_called()

    def test_sync_restores_original_tab_and_closes_temporary_tabs(
        self,
    ) -> None:
        driver = Mock()
        driver.current_window_handle = "original"
        driver.window_handles = ["original"]
        sync = TargetCollaborationSync(driver)

        def run_sync() -> list[dict[str, object]]:
            driver.window_handles = ["original", "temporary"]
            return [{"invitationGroupId": "1"}]

        sync.sync_in_progress = Mock(side_effect=run_sync)

        result = sync.sync()

        self.assertEqual(result, [{"invitationGroupId": "1"}])
        driver.switch_to.window.assert_any_call("temporary")
        driver.close.assert_called_once_with()
        driver.switch_to.window.assert_called_with("original")

    def test_sync_waits_before_closing_temporary_target_tab(self) -> None:
        driver = Mock()
        driver.current_window_handle = "original"
        driver.current_url = (
            "https://affiliate.tiktokshopglobalselling.com/"
            "connection/target-invitation?shop_id=1"
        )
        driver.window_handles = ["original"]
        sync = TargetCollaborationSync(driver)

        def run_sync() -> list[dict[str, object]]:
            driver.window_handles = ["original", "temporary"]
            return []

        sync.sync_in_progress = Mock(side_effect=run_sync)
        with (
            patch(
                "ziniao_automation.actions.collaboration_sync.random.uniform",
                return_value=1.438,
            ) as uniform,
            patch(
                "ziniao_automation.actions.collaboration_sync.time.sleep"
            ) as sleep,
        ):
            sync.sync()

        uniform.assert_called_once_with(1.0, 2.0)
        sleep.assert_called_once_with(1.438)
        driver.close.assert_called_once_with()

    def test_open_target_page_can_enter_affiliate_from_store(self) -> None:
        driver = Mock()
        driver.current_url = (
            "https://seller.us.tiktokshopglobalselling.com/homepage"
        )
        sync = TargetCollaborationSync(driver)
        sync._activate_target_window = Mock(return_value=False)
        sync._affiliate_window = Mock(side_effect=[None, "affiliate"])
        sync._open_affiliate_from_store = Mock(return_value="affiliate")
        sync._direct_target_url = Mock(
            return_value=(
                "https://affiliate.tiktokshopglobalselling.com/"
                "connection/target-invitation?shop_id=1"
            )
        )
        sync._wait_for_document = Mock()
        sync._is_target_url = Mock(return_value=True)

        sync.open_target_page()

        sync._open_affiliate_from_store.assert_called_once_with()
        driver.get.assert_called_once()

    def test_affiliate_window_prefers_existing_affiliate_detail_page(
        self,
    ) -> None:
        driver = Mock()
        driver.window_handles = ["seller-landing", "affiliate-detail"]
        sync = TargetCollaborationSync(driver)
        sync._window_urls = Mock(
            return_value={
                "seller-landing": (
                    "https://seller.us.tiktokshopglobalselling.com/"
                    "affiliate/landing?shop_region=US"
                ),
                "affiliate-detail": (
                    "https://affiliate.tiktokshopglobalselling.com/"
                    "connection/target-invitation/detail"
                    "?invitation_id=1&shop_id=2"
                ),
            }
        )

        selected = sync._affiliate_window()

        self.assertEqual(selected, "affiliate-detail")
        driver.switch_to.window.assert_called_once_with("affiliate-detail")

    def test_seller_affiliate_bridge_is_not_an_affiliate_window(self) -> None:
        driver = Mock()
        driver.window_handles = ["seller-landing"]
        sync = TargetCollaborationSync(driver)
        sync._window_urls = Mock(
            return_value={
                "seller-landing": (
                    "https://seller.us.tiktokshopglobalselling.com/"
                    "affiliate/landing?shop_region=US"
                )
            }
        )

        selected = sync._affiliate_window()

        self.assertIsNone(selected)
        driver.switch_to.window.assert_not_called()

    def test_open_affiliate_from_store_reuses_tab_and_follows_bridge(
        self,
    ) -> None:
        seller_home = (
            "https://seller.us.tiktokshopglobalselling.com/homepage"
        )
        seller_bridge = (
            "https://seller.us.tiktokshopglobalselling.com/"
            "affiliate/landing?shop_region=US"
        )
        first_entry = dom_element(
            "seller-affiliate",
            tag="a",
            text="Affiliate",
            href=seller_bridge,
        )
        driver = Mock()
        driver.current_url = seller_home
        driver.current_window_handle = "seller"
        driver.window_handles = ["seller"]
        sync = TargetCollaborationSync(driver)
        sync._seller_window = Mock(
            side_effect=[
                ("seller", seller_home),
                ("seller", seller_home),
                ("seller", seller_bridge),
            ]
        )
        sync._affiliate_window = Mock(
            side_effect=[None, None, None]
        )
        sync._affiliate_entry = Mock(return_value=first_entry)
        sync._wait_for_document = Mock()
        sync._wait = Mock(
            side_effect=lambda condition, **_kwargs: condition(driver)
        )

        selected = sync._open_affiliate_from_store()

        self.assertEqual(selected, "seller")
        driver.switch_to.new_window.assert_not_called()
        driver.switch_to.window.assert_called_once_with("seller")
        self.assertEqual(
            [call.args[0] for call in driver.get.call_args_list],
            [seller_bridge],
        )

    def test_landing_target_card_accepts_direct_target_link(self) -> None:
        direct_link = dom_element(
            "target-link",
            tag="a",
            text="Manage target collaborations",
            href=(
                "https://affiliate.tiktokshopglobalselling.com/"
                "connection/target-invitation?shop_id=2"
            ),
        )
        driver = Mock()
        driver.find_elements.side_effect = [
            [direct_link],
            [],
            [],
            [],
        ]
        sync = TargetCollaborationSync(driver)

        selected = sync._landing_target_card()

        self.assertIs(selected, direct_link)

    def test_landing_target_card_accepts_react_onclick_wrapper(self) -> None:
        label = dom_element(
            "target-label",
            tag="div",
            text="定向合作设置",
        )
        card = dom_element(
            "target-card",
            tag="div",
            text="定向合作设置 邀请你喜欢的达人",
        )
        driver = Mock()
        driver.find_elements.side_effect = [
            [],
            [],
            [],
            [],
            [label],
            [],
            [],
        ]
        driver.execute_script.return_value = card
        sync = TargetCollaborationSync(driver)

        selected = sync._landing_target_card()

        self.assertIs(selected, card)

    def test_affiliate_entry_collapses_equivalent_visible_controls(
        self,
    ) -> None:
        anchor = dom_element(
            "anchor",
            tag="a",
            text="联盟",
            href=(
                "https://seller.us.tiktokshopglobalselling.com/"
                "affiliate/landing"
            ),
        )
        menu = dom_element(
            "menu",
            tag="div",
            text="联盟",
            role="menuitem",
            descendants=[anchor],
        )
        button = dom_element(
            "button",
            tag="button",
            text="前往联盟中心首页",
        )
        driver = Mock()
        driver.find_elements.side_effect = [
            [anchor],
            [anchor, menu, button],
        ]
        sync = TargetCollaborationSync(driver)

        selected = sync._affiliate_entry()

        self.assertIs(selected, anchor)

    def test_direct_target_url_discards_detail_only_parameters(self) -> None:
        current = (
            "https://affiliate.tiktokshopglobalselling.com/"
            "connection/target-invitation/detail"
            "?invitation_id=1&enter_from=list&shop_region=US&shop_id=2"
        )

        target = TargetCollaborationSync._direct_target_url(current)

        self.assertIn("/connection/target-invitation?", target)
        self.assertIn("shop_region=US", target)
        self.assertIn("shop_id=2", target)
        self.assertIn("tab=1", target)
        self.assertNotIn("invitation_id", target)
        self.assertNotIn("enter_from", target)

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
