from __future__ import annotations

import unittest
from unittest.mock import Mock, call, patch

from selenium.common.exceptions import (
    StaleElementReferenceException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys

from ziniao_automation.actions.creator_contact import (
    APPROVED_GREETING_MESSAGE,
    CreatorContactWorkflow,
    WorkflowStepResult,
)
from ziniao_automation.errors import ZiniaoWorkflowError


class CreatorContactWorkflowTests(unittest.TestCase):
    def test_every_click_waits_random_one_to_two_seconds(self) -> None:
        driver = Mock()
        element = Mock()
        workflow = CreatorContactWorkflow(driver)

        with (
            patch(
                "ziniao_automation.actions.creator_contact.random.uniform",
                return_value=1.425,
            ) as uniform,
            patch(
                "ziniao_automation.actions.creator_contact.time.sleep"
            ) as sleep,
        ):
            workflow._click(element)

        uniform.assert_called_once_with(1.0, 2.0)
        sleep.assert_called_once_with(1.425)
        element.click.assert_called_once_with()
        self.assertEqual(workflow._action_wait_seconds, [1.425])

    def test_fixed_click_wait_bypasses_random_cadence(self) -> None:
        driver = Mock()
        element = Mock()
        workflow = CreatorContactWorkflow(driver)

        with (
            patch(
                "ziniao_automation.actions.creator_contact.random.uniform"
            ) as uniform,
            patch(
                "ziniao_automation.actions.creator_contact.time.sleep"
            ) as sleep,
        ):
            workflow._click(element, fixed_wait_seconds=3.0)

        uniform.assert_not_called()
        sleep.assert_called_once_with(3.0)
        element.click.assert_called_once_with()
        self.assertEqual(workflow._action_wait_seconds, [3.0])

    def test_every_refresh_waits_random_five_to_eight_seconds(self) -> None:
        driver = Mock()
        workflow = CreatorContactWorkflow(driver)
        workflow._wait_for_document = Mock()

        with (
            patch(
                "ziniao_automation.actions.creator_contact.random.uniform",
                return_value=6.75,
            ) as uniform,
            patch(
                "ziniao_automation.actions.creator_contact.time.sleep"
            ) as sleep,
        ):
            waited = workflow._refresh_page()

        self.assertEqual(waited, 6.75)
        uniform.assert_called_once_with(5.0, 8.0)
        sleep.assert_called_once_with(6.75)
        driver.refresh.assert_called_once_with()
        workflow._wait_for_document.assert_called_once_with()
        self.assertEqual(workflow._refresh_wait_seconds, [6.75])

    def test_target_collaboration_recovery_refresh_waits_three_seconds(
        self,
    ) -> None:
        driver = Mock()
        workflow = CreatorContactWorkflow(driver)
        workflow._wait_for_document = Mock()

        with patch(
            "ziniao_automation.actions.creator_contact.time.sleep"
        ) as sleep:
            waited = workflow._refresh_page(fixed_wait_seconds=3.0)

        self.assertEqual(waited, 3.0)
        sleep.assert_called_once_with(3.0)
        driver.refresh.assert_called_once_with()
        self.assertEqual(workflow._refresh_wait_seconds, [3.0])

    def test_missing_other_invitation_refreshes_once_then_rechecks(
        self,
    ) -> None:
        driver = Mock()
        workflow = CreatorContactWorkflow(driver)
        workflow._greeting_delivery_verified = True
        recipient = {
            "creatorHandle": "creator",
            "creatorId": "7493994012378827459",
        }
        workflow._require_verified_recipient = Mock(
            return_value=recipient
        )
        tab = Mock()
        tab.text = "定向合作"
        tab.get_attribute.side_effect = lambda name: (
            "true" if name == "aria-selected" else ""
        )
        workflow._target_collaboration_tab = Mock(return_value=tab)
        workflow._wait = Mock(return_value=tab)
        button = Mock()
        workflow._other_invitation_button = Mock(
            side_effect=[
                ZiniaoWorkflowError(
                    "第 8 步失败：定向合作页未找到"
                    "“发送其他邀请开展合作”。"
                ),
                button,
            ]
        )
        workflow._refresh_page = Mock(return_value=3.0)
        workflow._is_visible = Mock(return_value=True)

        result = workflow.open_target_collaboration(
            "@creator",
            "7493994012378827459",
        )

        workflow._refresh_page.assert_called_once_with(
            fixed_wait_seconds=3.0
        )
        self.assertEqual(
            workflow._other_invitation_button.call_count,
            2,
        )
        self.assertTrue(
            result.evidence["targetCollaborationRefreshAttempted"]
        )
        self.assertEqual(
            result.evidence[
                "targetCollaborationRefreshWaitSeconds"
            ],
            3.0,
        )

    def test_click_refinds_same_unique_target_after_react_redraw(
        self,
    ) -> None:
        driver = Mock()
        stale = Mock()
        stale.tag_name = "button"
        stale.text = "联盟"
        stale.get_attribute.side_effect = lambda name: (
            "affiliate-entry" if name == "data-e2e" else ""
        )
        stale.is_enabled.side_effect = StaleElementReferenceException()
        fresh = Mock()
        fresh.id = "fresh-affiliate-entry"
        fresh.is_displayed.return_value = True
        fresh.is_enabled.return_value = True
        driver.find_elements.return_value = [fresh]
        workflow = CreatorContactWorkflow(driver)

        with patch.object(workflow, "_random_pause", return_value=4.0):
            workflow._click(stale)

        fresh.click.assert_called_once_with()
        stale.click.assert_not_called()

    def test_click_uses_cdp_to_recover_unique_redrawn_target(self) -> None:
        driver = Mock()
        stale = Mock()
        stale.tag_name = "button"
        stale.text = ""
        stale.get_attribute.side_effect = lambda name: (
            "chat-action" if name == "aria-label" else ""
        )
        stale.is_enabled.side_effect = StaleElementReferenceException()
        fresh = Mock()
        fresh.is_displayed.return_value = True
        fresh.is_enabled.return_value = True

        def find_elements(by: str, value: str) -> list[Mock]:
            if by == "css selector" and "data-ziniao-cdp-click-target" in value:
                return [fresh]
            return []

        driver.find_elements.side_effect = find_elements
        driver.execute_cdp_cmd.return_value = {
            "result": {"value": {"matchCount": 1}}
        }
        workflow = CreatorContactWorkflow(driver)

        with patch.object(workflow, "_random_pause", return_value=4.0):
            workflow._click(stale)

        driver.execute_cdp_cmd.assert_called_with(
            "Runtime.evaluate",
            {
                "expression": unittest.mock.ANY,
                "returnByValue": True,
                "awaitPromise": False,
            },
        )
        fresh.click.assert_called_once_with()
        stale.click.assert_not_called()
        self.assertEqual(workflow._cdp_click_recovery_count, 1)

    def test_click_does_not_retry_after_click_returns_successfully(
        self,
    ) -> None:
        driver = Mock()
        element = Mock()
        workflow = CreatorContactWorkflow(driver)

        with patch.object(workflow, "_random_pause", return_value=4.0):
            workflow._click(element)

        element.click.assert_called_once_with()
        driver.execute_cdp_cmd.assert_not_called()

    def test_click_commits_healed_locator_only_after_success(self) -> None:
        driver = Mock()
        element = Mock()
        fallback = Mock()
        workflow = CreatorContactWorkflow(
            driver,
            dom_fallback=fallback,
        )

        with patch.object(workflow, "_random_pause", return_value=1.0):
            workflow._click(element)

        element.click.assert_called_once_with()
        fallback.record_interaction_success.assert_called_once_with(element)

    def test_cache_write_failure_does_not_change_click_success(self) -> None:
        driver = Mock()
        element = Mock()
        fallback = Mock()
        fallback.record_interaction_success.side_effect = OSError(
            "cache unavailable"
        )
        workflow = CreatorContactWorkflow(
            driver,
            dom_fallback=fallback,
        )

        with patch.object(workflow, "_random_pause", return_value=1.0):
            workflow._click(element)

        element.click.assert_called_once_with()

    def test_click_revalidates_target_after_random_wait(self) -> None:
        driver = Mock()
        element = Mock()
        workflow = CreatorContactWorkflow(driver)

        with (
            patch.object(workflow, "_random_pause", return_value=4.0),
            self.assertRaisesRegex(
                ZiniaoWorkflowError,
                "精确目标",
            ),
        ):
            workflow._click(
                element,
                validator=lambda _target: False,
                validation_message="候选项不再是精确目标。",
            )

        element.click.assert_not_called()

    def test_step_result_is_json_serializable_shape(self) -> None:
        result = WorkflowStepResult(
            step=1,
            action="open_find_creators",
            success=True,
            evidence={"currentUrl": "https://example.test/connection/creator"},
        )
        self.assertEqual(
            result.to_dict(),
            {
                "step": 1,
                "action": "open_find_creators",
                "success": True,
                "evidence": {
                    "currentUrl": "https://example.test/connection/creator"
                },
            },
        )

    def test_creator_handle_is_normalized(self) -> None:
        self.assertEqual(
            CreatorContactWorkflow.normalize_creator_handle(
                "  @delaneykreusel "
            ),
            ("@delaneykreusel", "delaneykreusel"),
        )

    def test_creator_handle_cannot_be_empty(self) -> None:
        with self.assertRaises(ZiniaoWorkflowError):
            CreatorContactWorkflow.normalize_creator_handle("@")

    def test_creator_id_is_numeric_and_normalized(self) -> None:
        self.assertEqual(
            CreatorContactWorkflow.normalize_creator_id(
                " 7493994012378827459 "
            ),
            "7493994012378827459",
        )
        with self.assertRaises(ZiniaoWorkflowError):
            CreatorContactWorkflow.normalize_creator_id("creator-123")

    def test_creator_id_is_read_from_chat_url(self) -> None:
        self.assertEqual(
            CreatorContactWorkflow.creator_id_from_url(
                "https://example.test/seller/im?"
                "creator_id=7493994012378827459&shop_region=US"
            ),
            "7493994012378827459",
        )

    def test_chat_launch_accepts_reused_window_with_changed_target(self) -> None:
        driver = Mock()
        driver.window_handles = ["creator-list", "cooperation-chat"]
        current_urls = {
            "creator-list": (
                "https://example.test/connection/creator?shop_region=US"
            ),
            "cooperation-chat": (
                "https://example.test/seller/im?"
                "creator_id=7494801498215254213"
            ),
        }

        def switch_window(handle: str) -> None:
            driver.current_url = current_urls[handle]

        driver.switch_to.window.side_effect = switch_window
        workflow = CreatorContactWorkflow(driver)
        workflow._chat_target_handle_visible = Mock(return_value=True)

        result = workflow._activate_chat_window_after_launch(
            handles_before={"creator-list", "cooperation-chat"},
            urls_before={
                "creator-list": current_urls["creator-list"],
                "cooperation-chat": (
                    "https://example.test/seller/im?"
                    "creator_id=7493994012378827459"
                ),
            },
            bare_handle="isnt_ellie",
        )

        self.assertEqual(result["handle"], "cooperation-chat")
        self.assertFalse(result["openedNewWindow"])
        self.assertTrue(result["reusedExistingWindow"])
        self.assertTrue(result["urlChangedAfterLaunch"])

    def test_chat_launch_accepts_existing_exact_target_window(self) -> None:
        driver = Mock()
        driver.window_handles = ["cooperation-chat"]
        target_url = (
            "https://example.test/seller/im?"
            "creator_id=7494801498215254213"
        )
        driver.switch_to.window.side_effect = (
            lambda _handle: setattr(driver, "current_url", target_url)
        )
        workflow = CreatorContactWorkflow(driver)
        workflow._chat_target_handle_visible = Mock(return_value=True)

        result = workflow._activate_chat_window_after_launch(
            handles_before={"cooperation-chat"},
            urls_before={"cooperation-chat": target_url},
            bare_handle="isnt_ellie",
        )

        self.assertEqual(result["handle"], "cooperation-chat")
        self.assertFalse(result["openedNewWindow"])
        self.assertTrue(result["reusedExistingWindow"])
        self.assertFalse(result["urlChangedAfterLaunch"])

    def test_cdp_window_lookup_avoids_switching_unrelated_tabs(self) -> None:
        driver = Mock()
        driver.window_handles = [
            "CDwindow-seller",
            "CDwindow-unrelated",
            "CDwindow-find",
        ]
        urls = {
            "CDwindow-seller": "https://seller.example.test/",
            "CDwindow-unrelated": "https://example.test/unrelated",
            "CDwindow-find": (
                "https://affiliate.tiktokshopglobalselling.com/"
                "connection/creator?shop_id=1"
            ),
        }
        driver.current_window_handle = "CDwindow-seller"
        driver.current_url = urls["CDwindow-seller"]
        driver.execute_cdp_cmd.return_value = {
            "targetInfos": [
                {
                    "type": "page",
                    "targetId": handle.removeprefix("CDwindow-"),
                    "url": url,
                }
                for handle, url in urls.items()
            ]
        }

        def switch_window(handle: str) -> None:
            driver.current_window_handle = handle
            driver.current_url = urls[handle]

        driver.switch_to.window.side_effect = switch_window
        workflow = CreatorContactWorkflow(driver)

        result = workflow._activate_window_matching(
            ("/connection/creator",)
        )

        self.assertIn("/connection/creator", result)
        driver.switch_to.window.assert_called_once_with("CDwindow-find")

    def test_approved_greeting_matches_requested_multiline_copy(self) -> None:
        self.assertEqual(
            APPROVED_GREETING_MESSAGE,
            "Hi We’re obsessed with your content 🥰\n"
            "Your aesthetic goes so well with VAELOS activewear ✨\n"
            "We’re a reliable brand selling soft, stretchy yoga & gym wear "
            "🩳🧘‍♀️\n"
            "We sent you an official collab invite: free samples + high "
            "commission 🎁💰\n"
            "Accept it in your TikTok dashboard to get your products ASAP 🚀\n"
            "Let’s partner long term and make great content together! 🤩",
        )

    def test_greeting_send_requires_explicit_confirmation(self) -> None:
        workflow = CreatorContactWorkflow(Mock())
        workflow._require_verified_recipient = Mock(return_value={})
        workflow._chat_message_elements = Mock(return_value=[])
        with self.assertRaisesRegex(
            ZiniaoWorkflowError,
            "必须显式确认发送招呼语",
        ):
            workflow.send_approved_greeting(
                "@delaneykreusel",
                "7493994012378827459",
            )

    def test_invitation_send_requires_explicit_confirmation(self) -> None:
        workflow = CreatorContactWorkflow(Mock())
        workflow._require_verified_recipient = Mock(return_value={})
        with self.assertRaisesRegex(
            ZiniaoWorkflowError,
            "必须显式确认发送邀请",
        ):
            workflow.send_selected_invitation(
                "@delaneykreusel",
                "7493994012378827459",
                "金色拉链+短裤13",
            )

    def test_select_invitation_rejects_group_mismatch_before_click(
        self,
    ) -> None:
        workflow = CreatorContactWorkflow(Mock())
        workflow._require_verified_recipient = Mock(return_value={})
        workflow._invitation_dialog_verified = True
        modal = Mock()
        row = Mock()
        workflow._visible_invitation_modal = Mock(return_value=modal)
        workflow._wait = Mock(return_value=row)
        workflow._invitation_group_ids_from_react = Mock(
            return_value={"7664255664765880078"}
        )
        workflow._click = Mock()

        with self.assertRaisesRegex(
            ZiniaoWorkflowError,
            "invitationGroupId 与任务快照不一致",
        ):
            workflow.select_invitation(
                "@delaneykreusel",
                "7493994012378827459",
                "金色拉链+短裤13",
                invitation_group_id="7664550207413847821",
            )

        workflow._click.assert_not_called()

    def test_existing_invitation_is_not_clicked_again(self) -> None:
        workflow = CreatorContactWorkflow(Mock())
        workflow._require_verified_recipient = Mock(return_value={})
        workflow._greeting_delivery_verified = True
        workflow._target_collaboration_verified = True
        workflow._invitation_dialog_verified = True
        workflow._selected_invitation = "金色拉链+短裤13"
        modal = Mock()
        row = Mock()
        radio = Mock()
        radio.is_selected.return_value = True
        row.find_elements.return_value = [radio]
        invite_button = Mock()
        invite_button.is_enabled.return_value = True
        invite_button.get_attribute.return_value = "false"
        workflow._visible_invitation_modal = Mock(return_value=modal)
        workflow._invitation_row = Mock(return_value=row)
        workflow._modal_invite_button = Mock(
            return_value=invite_button
        )
        workflow._right_panel_invitation_visible = Mock(
            return_value=True
        )
        workflow._right_panel_invitation_card = Mock(
            return_value=(
                Mock(),
                "7664550207413847821",
                "7666768491349280526",
            )
        )
        workflow._close_invitation_modal = Mock()
        workflow.close_creator_tabs_keep_search = Mock(
            return_value={
                "creatorTabsClosed": True,
                "closedCreatorTabCount": 2,
                "creatorDetailTargetGone": True,
                "searchTabKept": True,
                "returnedToFindCreators": True,
                "findCreatorsSearchReady": True,
            }
        )
        workflow._click = Mock()

        result = workflow.send_selected_invitation(
            "@delaneykreusel",
            "7493994012378827459",
            "金色拉链+短裤13",
            confirm_send=True,
        )

        self.assertTrue(result.evidence["alreadySent"])
        self.assertFalse(result.evidence["finalInviteButtonClicked"])
        self.assertTrue(result.evidence["invitationCreated"])
        self.assertTrue(result.evidence["invitationCompleted"])
        self.assertTrue(result.evidence["invitationSent"])
        workflow._click.assert_not_called()
        workflow._close_invitation_modal.assert_called_once_with(modal)

    def test_success_cleanup_closes_detail_and_chat_keeps_search(
        self,
    ) -> None:
        driver = Mock()
        handles = ["search", "detail", "chat"]
        urls = {
            "search": "https://example.test/connection/creator",
            "detail": (
                "https://example.test/connection/creator/detail?cid=123"
            ),
            "chat": "https://example.test/seller/im?creator_id=123",
        }
        driver.window_handles = handles

        def switch_window(handle: str) -> None:
            driver.current_window_handle = handle
            driver.current_url = urls[handle]

        def close_window() -> None:
            handles.remove(driver.current_window_handle)

        driver.switch_to.window.side_effect = switch_window
        driver.close.side_effect = close_window
        search_input = Mock()
        search_input.id = "find-creators-search"
        search_input.is_displayed.return_value = True
        search_input.is_enabled.return_value = True
        driver.find_elements.return_value = [search_input]
        switch_window("chat")
        workflow = CreatorContactWorkflow(driver)
        workflow._find_creators_handle = "search"
        workflow._find_creators_url = urls["search"]
        workflow._creator_detail_handle = "detail"
        workflow._creator_detail_url = urls["detail"]
        workflow._chat_handle = "chat"

        result = workflow.close_creator_tabs_keep_search()

        self.assertEqual(handles, ["search"])
        self.assertTrue(result["creatorTabsClosed"])
        self.assertEqual(result["closedCreatorTabCount"], 2)
        self.assertTrue(result["creatorDetailTabClosed"])
        self.assertTrue(result["creatorDetailTargetGone"])
        self.assertTrue(result["creatorChatTabClosed"])
        self.assertTrue(result["searchTabKept"])
        self.assertTrue(result["findCreatorsSearchReady"])
        self.assertEqual(driver.current_window_handle, "search")

    def test_open_find_creators_reuses_ready_page_without_navigation(
        self,
    ) -> None:
        driver = Mock()
        driver.current_url = (
            "https://example.test/connection/creator?shop_id=123"
        )
        driver.current_window_handle = "search"
        driver.title = "Find creators"
        driver.find_elements.return_value = []
        search_input = Mock()
        workflow = CreatorContactWorkflow(driver)
        workflow._wait_for_document = Mock()
        workflow._activate_existing_find_creators = Mock(
            return_value={
                "handle": "search",
                "url": driver.current_url,
                "duplicateFindCreatorsTabsClosed": 0,
            }
        )
        workflow._visible_find_creators_search_inputs = Mock(
            return_value=[search_input]
        )
        workflow._wait = Mock(
            side_effect=lambda condition, **_kwargs: condition(driver)
        )

        result = workflow.open_find_creators()

        self.assertTrue(
            result.evidence["reusedExistingFindCreatorsTab"]
        )
        self.assertTrue(result.evidence["pageNavigationSkipped"])
        self.assertTrue(result.evidence["pageRefreshSkipped"])
        self.assertTrue(result.evidence["findCreatorsSearchReady"])
        self.assertFalse(
            result.evidence["existingPageReloadedForRecovery"]
        )
        driver.get.assert_not_called()
        driver.refresh.assert_not_called()

    def test_open_find_creators_reloads_only_when_reused_dom_is_stale(
        self,
    ) -> None:
        driver = Mock()
        driver.current_url = (
            "https://example.test/connection/creator?shop_id=123"
        )
        driver.current_window_handle = "search"
        driver.title = "Find creators"
        driver.find_elements.return_value = []
        search_input = Mock()
        workflow = CreatorContactWorkflow(driver)
        workflow._wait_for_document = Mock()
        workflow._random_pause = Mock(return_value=6.5)
        workflow._activate_existing_find_creators = Mock(
            return_value={
                "handle": "search",
                "url": driver.current_url,
                "duplicateFindCreatorsTabsClosed": 0,
            }
        )
        workflow._visible_find_creators_search_inputs = Mock(
            return_value=[search_input]
        )
        workflow._wait = Mock(
            side_effect=[
                ZiniaoWorkflowError("stale page"),
                [search_input],
            ]
        )

        result = workflow.open_find_creators()

        driver.get.assert_called_once_with(driver.current_url)
        workflow._random_pause.assert_called_once_with(
            5.0,
            8.0,
            refresh=True,
        )
        self.assertTrue(
            result.evidence["reusedExistingFindCreatorsTab"]
        )
        self.assertFalse(result.evidence["pageNavigationSkipped"])
        self.assertFalse(result.evidence["pageRefreshSkipped"])
        self.assertTrue(
            result.evidence["existingPageReloadedForRecovery"]
        )

    def test_open_find_creators_ignores_already_maximized_driver_error(
        self,
    ) -> None:
        driver = Mock()
        driver.current_url = (
            "https://example.test/affiliate/creator?shop_id=123"
        )
        driver.current_window_handle = "search"
        driver.title = "Find creators"
        driver.find_elements.return_value = []
        driver.maximize_window.side_effect = WebDriverException(
            "failed to change window state to 'normal', "
            "current state is 'maximized'"
        )
        search_input = Mock()
        workflow = CreatorContactWorkflow(driver)
        workflow._wait_for_document = Mock()
        workflow._activate_existing_find_creators = Mock(
            return_value={
                "handle": "search",
                "url": driver.current_url,
                "duplicateFindCreatorsTabsClosed": 0,
            }
        )
        workflow._visible_find_creators_search_inputs = Mock(
            return_value=[search_input]
        )
        workflow._wait = Mock(
            side_effect=lambda condition, **_kwargs: condition(driver)
        )

        with patch("ziniao_automation.actions.creator_contact.time.sleep"):
            result = workflow.open_find_creators()

        self.assertTrue(result.success)
        driver.maximize_window.assert_called_once_with()

    def test_search_closes_obstruction_and_types_imported_id_without_at(
        self,
    ) -> None:
        driver = Mock()
        driver.current_url = "https://example.test/connection/creator"
        search_input = Mock()
        search_input.get_attribute.return_value = "creator_id_123"
        suggestion = Mock()
        suggestion.text = "creator_id_123\nCreator"
        result_row = Mock()
        result_row.is_displayed.return_value = True
        result_row.tag_name = "tr"
        workflow = CreatorContactWorkflow(driver)
        workflow._close_find_creators_obstruction = Mock(
            return_value={
                "findCreatorsObstructionPresent": True,
                "findCreatorsObstructionClosed": True,
                "findCreatorsObstructionSelector": "#target",
            }
        )
        workflow._first_clickable = Mock(
            side_effect=[search_input, suggestion]
        )
        workflow._click = Mock()
        workflow._wait = Mock(side_effect=[True, result_row])

        result = workflow.search_creator("@creator_id_123")

        search_input.send_keys.assert_any_call("creator_id_123")
        self.assertNotIn(
            (("@creator_id_123",), {}),
            [
                (call.args, call.kwargs)
                for call in search_input.send_keys.call_args_list
            ],
        )
        self.assertEqual(result.evidence["inputValue"], "creator_id_123")
        self.assertEqual(
            result.evidence["importedCreatorId"],
            "creator_id_123",
        )
        self.assertTrue(
            result.evidence["findCreatorsObstructionClosed"]
        )
        self.assertEqual(
            workflow._first_clickable.call_args_list[1].kwargs[
                "timeout_seconds"
            ],
            8,
        )

    def test_search_selects_all_with_platform_modifier(self) -> None:
        def run_search(platform_name: str):
            driver = Mock()
            driver.current_url = "https://example.test/connection/creator"
            search_input = Mock()
            search_input.get_attribute.return_value = "creator_id_123"
            suggestion = Mock()
            suggestion.text = "creator_id_123\nCreator"
            result_row = Mock()
            result_row.is_displayed.return_value = True
            result_row.tag_name = "tr"
            workflow = CreatorContactWorkflow(driver)
            workflow._close_find_creators_obstruction = Mock(
                return_value={
                    "findCreatorsObstructionPresent": False,
                    "findCreatorsObstructionClosed": False,
                    "findCreatorsObstructionSelector": "",
                }
            )
            workflow._first_clickable = Mock(
                side_effect=[search_input, suggestion]
            )
            workflow._click = Mock()
            workflow._wait = Mock(side_effect=[True, result_row])
            with patch(
                "ziniao_automation.keyboard.platform.system",
                return_value=platform_name,
            ):
                workflow.search_creator("@creator_id_123")
            return search_input.send_keys.call_args_list

        cases = (
            ("Windows", Keys.CONTROL, Keys.COMMAND),
            ("Linux", Keys.CONTROL, Keys.COMMAND),
            ("Darwin", Keys.COMMAND, Keys.CONTROL),
        )
        for platform_name, expected_modifier, wrong_modifier in cases:
            with self.subTest(platform_name=platform_name):
                calls = run_search(platform_name)
                self.assertEqual(
                    calls,
                    [
                        call(expected_modifier, "a"),
                        call(Keys.BACKSPACE),
                        call("creator_id_123"),
                    ],
                )
                self.assertNotIn(call(wrong_modifier, "a"), calls)

    def test_search_replaces_previous_creator_on_reused_page(self) -> None:
        class StatefulSearchInput:
            def __init__(self, value: str, modifier: str) -> None:
                self.value = value
                self.modifier = modifier
                self.all_selected = False
                self.calls: list[tuple[str, ...]] = []

            def send_keys(self, *keys: str) -> None:
                self.calls.append(keys)
                if keys == (self.modifier, "a"):
                    self.all_selected = True
                    return
                if keys == (Keys.BACKSPACE,):
                    self.value = "" if self.all_selected else self.value[:-1]
                    self.all_selected = False
                    return
                self.value += "".join(keys)

            def get_attribute(self, name: str) -> str:
                return self.value if name == "value" else ""

        cases = (
            ("Windows", Keys.CONTROL),
            ("Darwin", Keys.COMMAND),
        )
        for platform_name, expected_modifier in cases:
            with self.subTest(platform_name=platform_name):
                driver = Mock()
                driver.current_url = (
                    "https://example.test/connection/creator"
                )
                search_input = StatefulSearchInput(
                    "previous_creator",
                    expected_modifier,
                )
                suggestion = Mock()
                suggestion.text = "matched creator"
                result_row = Mock()
                result_row.is_displayed.return_value = True
                result_row.tag_name = "tr"
                workflow = CreatorContactWorkflow(driver)
                workflow._close_find_creators_obstruction = Mock(
                    return_value={
                        "findCreatorsObstructionPresent": False,
                        "findCreatorsObstructionClosed": False,
                        "findCreatorsObstructionSelector": "",
                    }
                )
                workflow._first_clickable = Mock(
                    side_effect=[
                        search_input,
                        suggestion,
                        search_input,
                        suggestion,
                    ]
                )
                workflow._click = Mock()

                def wait(condition, *, message: str, **_kwargs):
                    if "导入达人 ID 未完整写入" in message:
                        self.assertTrue(condition(driver))
                        return True
                    return result_row

                workflow._wait = Mock(side_effect=wait)

                with patch(
                    "ziniao_automation.keyboard.platform.system",
                    return_value=platform_name,
                ):
                    first = workflow.search_creator("@creator_a")
                    second = workflow.search_creator("@creator_b")

                self.assertEqual(first.evidence["inputValue"], "creator_a")
                self.assertEqual(second.evidence["inputValue"], "creator_b")
                self.assertEqual(search_input.value, "creator_b")
                self.assertEqual(
                    search_input.calls,
                    [
                        (expected_modifier, "a"),
                        (Keys.BACKSPACE,),
                        ("creator_a",),
                        (expected_modifier, "a"),
                        (Keys.BACKSPACE,),
                        ("creator_b",),
                    ],
                )

    def test_store_page_priority_rejects_extension_and_accepts_seller(
        self,
    ) -> None:
        self.assertEqual(
            CreatorContactWorkflow._store_page_priority(
                "chrome-extension://example/index.html"
            ),
            0,
        )
        self.assertEqual(
            CreatorContactWorkflow._store_page_priority(
                "https://seller.us.tiktokshopglobalselling.com/homepage"
            ),
            1,
        )
        self.assertEqual(
            CreatorContactWorkflow._store_page_priority(
                "https://affiliate.tiktokshopglobalselling.com/"
                "connection/creator?shop_id=1"
            ),
            3,
        )
        self.assertEqual(
            CreatorContactWorkflow._store_page_priority(
                "https://affiliate.tiktokshopglobalselling.com/"
                "affiliate/creator?shop_id=1"
            ),
            3,
        )

    def test_find_creators_list_url_accepts_current_and_legacy_routes(
        self,
    ) -> None:
        valid_urls = (
            "https://affiliate.example.test/connection/creator?shop_id=1",
            "https://affiliate.example.test/connection/creator/",
            "https://affiliate.example.test/affiliate/creator?shop_id=1",
            "https://affiliate.example.test/AFFILIATE/CREATOR/",
        )
        invalid_urls = (
            "https://affiliate.example.test/connection/creator/detail?id=1",
            "https://affiliate.example.test/affiliate/creator/detail?id=1",
            "https://affiliate.example.test/insights/transaction-analysis",
        )

        for url in valid_urls:
            with self.subTest(url=url):
                self.assertTrue(
                    CreatorContactWorkflow._is_find_creators_list_url(url)
                )
        for url in invalid_urls:
            with self.subTest(url=url):
                self.assertFalse(
                    CreatorContactWorkflow._is_find_creators_list_url(url)
                )

    def test_find_creators_obstruction_uses_requested_selector(self) -> None:
        driver = Mock()
        close_button = Mock()
        close_button.is_displayed.side_effect = [True, False]
        close_button.is_enabled.return_value = True
        driver.find_elements.side_effect = [[close_button], []]
        workflow = CreatorContactWorkflow(driver)
        workflow._click = Mock()

        evidence = workflow._close_find_creators_obstruction()

        self.assertTrue(evidence["findCreatorsObstructionPresent"])
        self.assertTrue(evidence["findCreatorsObstructionClosed"])
        self.assertIn(
            '[id^="garfish_app_for_creator_"]',
            evidence["findCreatorsObstructionSelector"],
        )
        workflow._click.assert_called_once_with(close_button)

    def test_find_creators_ai_search_switch_is_verified_as_off(self) -> None:
        driver = Mock()
        ai_search_switch = Mock()
        ai_search_switch.is_displayed.return_value = True
        ai_search_switch.is_enabled.return_value = True
        switch_state = {"aria-checked": "true"}

        def get_attribute(name: str) -> str | None:
            if name == "role":
                return "switch"
            return switch_state.get(name)

        ai_search_switch.get_attribute.side_effect = get_attribute
        driver.find_elements.return_value = [ai_search_switch]
        workflow = CreatorContactWorkflow(driver)
        workflow._click = Mock(
            side_effect=lambda _element: switch_state.update(
                {"aria-checked": "false"}
            )
        )
        workflow._wait = Mock(
            side_effect=lambda condition, **_kwargs: condition(driver)
        )

        evidence = workflow._close_find_creators_obstruction()

        workflow._click.assert_called_once_with(ai_search_switch)
        self.assertTrue(evidence["findCreatorsObstructionClosed"])
        self.assertFalse(
            evidence["findCreatorsObstructionAlreadyClosed"]
        )
        self.assertEqual(switch_state["aria-checked"], "false")

    def test_find_creators_ai_search_switch_already_off_is_not_clicked(
        self,
    ) -> None:
        driver = Mock()
        ai_search_switch = Mock()
        ai_search_switch.is_displayed.return_value = True
        ai_search_switch.is_enabled.return_value = True
        ai_search_switch.get_attribute.side_effect = lambda name: {
            "role": "switch",
            "aria-checked": "false",
        }.get(name)
        driver.find_elements.return_value = [ai_search_switch]
        workflow = CreatorContactWorkflow(driver)
        workflow._click = Mock()

        evidence = workflow._close_find_creators_obstruction()

        workflow._click.assert_not_called()
        self.assertTrue(evidence["findCreatorsObstructionClosed"])
        self.assertTrue(
            evidence["findCreatorsObstructionAlreadyClosed"]
        )

    def test_find_creators_ai_search_switch_uses_dom_fallback(self) -> None:
        driver = Mock()
        driver.find_elements.return_value = []
        ai_search_switch = Mock()
        ai_search_switch.is_displayed.return_value = True
        ai_search_switch.is_enabled.return_value = True
        switch_state = {"aria-checked": "true"}
        ai_search_switch.get_attribute.side_effect = lambda name: {
            "role": "switch",
            **switch_state,
        }.get(name)
        fallback = Mock()
        fallback.locate.return_value = ai_search_switch
        workflow = CreatorContactWorkflow(
            driver,
            dom_fallback=fallback,
        )
        workflow._click = Mock(
            side_effect=lambda _element: switch_state.update(
                {"aria-checked": "false"}
            )
        )
        workflow._wait = Mock(
            side_effect=lambda condition, **_kwargs: condition(driver)
        )

        evidence = workflow._close_find_creators_obstruction()

        fallback.locate.assert_called_once()
        workflow._click.assert_called_once_with(ai_search_switch)
        self.assertEqual(
            evidence["findCreatorsObstructionLocatorSource"],
            "dom_fallback",
        )
        self.assertTrue(evidence["findCreatorsObstructionClosed"])

    def test_first_clickable_uses_dom_fallback_only_after_timeout(
        self,
    ) -> None:
        driver = Mock()
        driver.find_elements.return_value = []
        recovered = Mock()
        fallback = Mock()
        attempt = object()
        fallback.begin_persisted_attempt.return_value = attempt
        fallback.probe_persisted.return_value = None
        fallback.locate.return_value = recovered
        workflow = CreatorContactWorkflow(
            driver,
            dom_fallback=fallback,
        )
        workflow.set_model_fallback_enabled(True)
        timeout_error = ZiniaoWorkflowError("未找到搜索框")
        timeout_error.__cause__ = TimeoutException()
        workflow._wait = Mock(side_effect=timeout_error)

        result = workflow._first_clickable(
            ((By.CSS_SELECTOR, "input.old-selector"),),
            missing_message="未找到搜索框",
        )

        self.assertIs(result, recovered)
        fallback.locate.assert_called_once()
        fallback.finalize_persisted_timeout.assert_called_once_with(
            attempt
        )
        self.assertFalse(
            fallback.locate.call_args.kwargs["include_persisted"]
        )

    def test_first_clickable_rechecks_original_selectors_after_fallback(
        self,
    ) -> None:
        driver = Mock()
        late_element = Mock()
        late_element.is_displayed.return_value = True
        late_element.is_enabled.return_value = True
        driver.find_elements.return_value = [late_element]
        fallback = Mock()
        attempt = object()
        fallback.begin_persisted_attempt.return_value = attempt
        fallback.locate.return_value = None
        workflow = CreatorContactWorkflow(
            driver,
            dom_fallback=fallback,
        )
        workflow.set_model_fallback_enabled(True)
        timeout_error = ZiniaoWorkflowError("未出现精确候选")
        timeout_error.__cause__ = TimeoutException()
        workflow._wait = Mock(side_effect=timeout_error)

        result = workflow._first_clickable(
            ((By.CSS_SELECTOR, ".exact-creator"),),
            missing_message="未出现精确候选",
            timeout_seconds=8,
        )

        self.assertIs(result, late_element)
        fallback.locate.assert_called_once()
        fallback.finalize_persisted_timeout.assert_called_once_with(
            attempt
        )

    def test_non_timeout_wait_error_does_not_penalize_recipe(
        self,
    ) -> None:
        driver = Mock()
        driver.find_elements.return_value = []
        recovered = Mock()
        fallback = Mock()
        attempt = object()
        fallback.begin_persisted_attempt.return_value = attempt
        fallback.probe_persisted.return_value = None
        fallback.locate.return_value = recovered
        workflow = CreatorContactWorkflow(
            driver,
            dom_fallback=fallback,
        )
        workflow.set_model_fallback_enabled(True)
        driver_error = ZiniaoWorkflowError("浏览器连接中断")
        driver_error.__cause__ = RuntimeError("disconnected")
        workflow._wait = Mock(side_effect=driver_error)

        result = workflow._first_clickable(
            ((By.CSS_SELECTOR, "button.old"),),
            missing_message="未找到发送按钮",
        )

        self.assertIs(result, recovered)
        fallback.finalize_persisted_timeout.assert_not_called()

    def test_first_clickable_polls_persisted_recipe_inside_wait(
        self,
    ) -> None:
        driver = Mock()
        driver.find_elements.return_value = []
        recovered = Mock()
        fallback = Mock()
        attempt = object()
        fallback.begin_persisted_attempt.return_value = attempt
        fallback.probe_persisted.return_value = recovered
        workflow = CreatorContactWorkflow(
            driver,
            dom_fallback=fallback,
        )
        workflow._wait = Mock(
            side_effect=lambda condition, **_kwargs: condition(driver)
        )

        result = workflow._first_clickable(
            ((By.CSS_SELECTOR, "button.old"),),
            missing_message="未找到发送按钮",
        )

        self.assertIs(result, recovered)
        workflow._wait.assert_called_once()
        fallback.probe_persisted.assert_called_once_with(driver, attempt)
        fallback.finalize_persisted_timeout.assert_not_called()
        fallback.locate.assert_not_called()

    def test_slow_persisted_recipe_render_is_not_finalized_as_failure(
        self,
    ) -> None:
        driver = Mock()
        driver.find_elements.return_value = []
        recovered = Mock()
        fallback = Mock()
        attempt = object()
        fallback.begin_persisted_attempt.return_value = attempt
        fallback.probe_persisted.side_effect = [None, recovered]
        workflow = CreatorContactWorkflow(
            driver,
            dom_fallback=fallback,
        )

        def wait_for_second_poll(condition, **_kwargs):
            self.assertFalse(condition(driver))
            return condition(driver)

        workflow._wait = Mock(side_effect=wait_for_second_poll)

        result = workflow._first_clickable(
            ((By.CSS_SELECTOR, "button.old"),),
            missing_message="未找到发送按钮",
        )

        self.assertIs(result, recovered)
        self.assertEqual(fallback.probe_persisted.call_count, 2)
        fallback.finalize_persisted_timeout.assert_not_called()
        fallback.locate.assert_not_called()

    def test_first_clickable_does_not_call_model_on_normal_path(
        self,
    ) -> None:
        driver = Mock()
        driver.find_elements.return_value = []
        deterministic_element = Mock()
        fallback = Mock()
        fallback.begin_persisted_attempt.return_value = None
        workflow = CreatorContactWorkflow(
            driver,
            dom_fallback=fallback,
        )
        workflow._wait = Mock(return_value=deterministic_element)

        result = workflow._first_clickable(
            ((By.CSS_SELECTOR, "input.known-selector"),),
            missing_message="未找到搜索框",
        )

        self.assertIs(result, deterministic_element)
        fallback.locate.assert_not_called()

    def test_invitation_click_completes_without_waiting_for_panel_update(
        self,
    ) -> None:
        workflow = CreatorContactWorkflow(Mock())
        workflow._require_verified_recipient = Mock(return_value={})
        workflow._greeting_delivery_verified = True
        workflow._target_collaboration_verified = True
        workflow._invitation_dialog_verified = True
        workflow._selected_invitation = "金色拉链+短裤13"
        modal = Mock()
        row = Mock()
        radio = Mock()
        radio.is_selected.return_value = True
        row.find_elements.return_value = [radio]
        invite_button = Mock()
        invite_button.is_enabled.return_value = True
        invite_button.get_attribute.return_value = "false"
        workflow._visible_invitation_modal = Mock(return_value=modal)
        workflow._invitation_row = Mock(return_value=row)
        workflow._modal_invite_button = Mock(
            return_value=invite_button
        )
        workflow._right_panel_invitation_visible = Mock(
            return_value=False
        )
        workflow._refresh_invitation_with_retries = Mock(
            side_effect=AssertionError("不得刷新后扫描邀请状态")
        )
        workflow.close_creator_tabs_keep_search = Mock(
            return_value={
                "creatorTabsClosed": True,
                "closedCreatorTabCount": 2,
                "creatorDetailTargetGone": True,
                "searchTabKept": True,
                "returnedToFindCreators": True,
                "findCreatorsSearchReady": True,
            }
        )
        workflow._click = Mock()

        result = workflow.send_selected_invitation(
            "@delaneykreusel",
            "7493994012378827459",
            "金色拉链+短裤13",
            confirm_send=True,
        )

        self.assertTrue(result.evidence["invitationButtonClicked"])
        self.assertTrue(result.evidence["invitationCompleted"])
        self.assertFalse(
            result.evidence["invitationSubmissionConfirmed"]
        )
        workflow._click.assert_called_once_with(
            invite_button,
            fixed_wait_seconds=3.0,
        )
        workflow._refresh_invitation_with_retries.assert_not_called()
        workflow.driver.refresh.assert_not_called()

    def test_missing_panel_card_does_not_block_invitation_handoff(
        self,
    ) -> None:
        workflow = CreatorContactWorkflow(Mock())
        workflow._require_verified_recipient = Mock(
            return_value={"creatorId": "7493994012378827459"}
        )
        workflow._greeting_delivery_verified = True
        workflow._target_collaboration_verified = True
        workflow._invitation_dialog_verified = True
        workflow._selected_invitation = "金色拉链+短裤13"
        workflow._selected_invitation_group_id = "7664550207413847821"
        modal = Mock()
        row = Mock()
        radio = Mock()
        radio.is_selected.return_value = True
        row.find_elements.return_value = [radio]
        invite_button = Mock()
        invite_button.is_enabled.return_value = True
        invite_button.get_attribute.return_value = "false"
        workflow._visible_invitation_modal = Mock(return_value=modal)
        workflow._invitation_row = Mock(return_value=row)
        workflow._modal_invite_button = Mock(return_value=invite_button)
        workflow._right_panel_invitation_visible = Mock(return_value=False)
        workflow._refresh_invitation_with_retries = Mock(
            side_effect=AssertionError("不得刷新后扫描邀请状态")
        )
        workflow.close_creator_tabs_keep_search = Mock(
            return_value={
                "creatorTabsClosed": True,
                "closedCreatorTabCount": 2,
                "creatorDetailTargetGone": True,
                "searchTabKept": True,
                "returnedToFindCreators": True,
                "findCreatorsSearchReady": True,
            }
        )
        workflow._click = Mock()

        result = workflow.send_selected_invitation(
            "@delaneykreusel",
            "7493994012378827459",
            "金色拉链+短裤13",
            invitation_group_id="7664550207413847821",
            confirm_send=True,
        )

        self.assertTrue(result.evidence["invitationCompleted"])
        self.assertFalse(
            result.evidence["invitationSubmissionConfirmed"]
        )
        workflow._click.assert_called_once_with(
            invite_button,
            fixed_wait_seconds=3.0,
        )
        workflow._refresh_invitation_with_retries.assert_not_called()
        workflow.driver.refresh.assert_not_called()

    def test_final_invite_click_is_the_invitation_phase_receipt(
        self,
    ) -> None:
        workflow = CreatorContactWorkflow(Mock())
        workflow._require_verified_recipient = Mock(
            return_value={"creatorId": "7493994012378827459"}
        )
        workflow._greeting_delivery_verified = True
        workflow._target_collaboration_verified = True
        workflow._invitation_dialog_verified = True
        workflow._selected_invitation = "金色拉链+短裤13"
        workflow._selected_invitation_group_id = "7664550207413847821"
        modal = Mock()
        row = Mock()
        radio = Mock()
        radio.is_selected.return_value = True
        row.find_elements.return_value = [radio]
        invite_button = Mock()
        invite_button.is_enabled.return_value = True
        invite_button.get_attribute.return_value = "false"
        workflow._visible_invitation_modal = Mock(return_value=modal)
        workflow._invitation_row = Mock(return_value=row)
        workflow._modal_invite_button = Mock(return_value=invite_button)
        workflow._right_panel_invitation_visible = Mock(return_value=False)
        workflow._refresh_invitation_with_retries = Mock()
        workflow.close_creator_tabs_keep_search = Mock(
            return_value={
                "creatorTabsClosed": True,
                "closedCreatorTabCount": 2,
                "creatorDetailTargetGone": True,
                "searchTabKept": True,
                "returnedToFindCreators": True,
                "findCreatorsSearchReady": True,
            }
        )
        workflow._click = Mock()

        result = workflow.send_selected_invitation(
            "@delaneykreusel",
            "7493994012378827459",
            "金色拉链+短裤13",
            invitation_group_id="7664550207413847821",
            confirm_send=True,
        )

        self.assertTrue(result.success)
        self.assertTrue(result.evidence["invitationCompleted"])
        self.assertTrue(result.evidence["invitationCreated"])
        self.assertTrue(result.evidence["invitationSent"])
        self.assertEqual(
            result.evidence["invitationCompletionSource"],
            "final_invite_button_click",
        )
        self.assertFalse(
            result.evidence["invitationSubmissionConfirmed"]
        )
        self.assertTrue(result.evidence["invitationButtonClicked"])
        self.assertNotIn("invitationId", result.evidence)
        self.assertFalse(result.evidence["cardReadyToSend"])
        self.assertEqual(
            result.evidence["invitationGroupId"],
            "7664550207413847821",
        )
        workflow._click.assert_called_once_with(
            invite_button,
            fixed_wait_seconds=3.0,
        )
        workflow._refresh_invitation_with_retries.assert_not_called()
        workflow.driver.refresh.assert_not_called()
        workflow.close_creator_tabs_keep_search.assert_called_once()

    def test_dynamic_greeting_hash_mismatch_is_rejected(self) -> None:
        workflow = CreatorContactWorkflow(Mock())
        workflow._require_verified_recipient = Mock(return_value={})
        with self.assertRaisesRegex(
            ZiniaoWorkflowError,
            "任务快照哈希不一致",
        ):
            workflow.send_greeting(
                "@delaneykreusel",
                "7493994012378827459",
                "Exact greeting",
                confirm_send=True,
                expected_sha256="0" * 64,
            )

    @staticmethod
    def _plan_evidence(
        *,
        success: bool = False,
        pending: int = 0,
        failed: int = 0,
    ) -> dict[str, object]:
        return {
            "exactPlanCardVisible": success,
            "exactPlanCardCount": 1 if success else 0,
            "planCardServerIds": ["server-1"] if success else [],
            "planCardMessageKeys": (
                ["message-1:client-1:server-1"] if success else []
            ),
            "targetPlanMessageVerified": success,
            "targetPlanFlightStatus": 3 if success else None,
            "targetPlanFromMe": True if success else None,
            "targetPlanPendingCount": pending,
            "targetPlanFailedCount": failed,
            "targetPlanCreateTimes": [1_800_000_000_000] if success else [],
        }

    def _card_workflow(self) -> CreatorContactWorkflow:
        driver = Mock()
        driver.execute_script.return_value = 1_800_000_000_000
        workflow = CreatorContactWorkflow(driver)
        workflow._require_verified_recipient = Mock(
            return_value={"creatorId": "7493994012378827459"}
        )
        workflow._created_invitation = {
            "name": "金色拉链+短裤13",
            "invitationId": "7666768491349280526",
            "invitationGroupId": "7664550207413847821",
        }
        workflow._refresh_and_verify_invitation = Mock(
            return_value={
                "rightPanelInvitationVisible": True,
                "targetCollaborationCount": 1,
                "invitationId": "7666768491349280526",
                "invitationGroupId": "7664550207413847821",
                "verifiedAfterRefresh": True,
            }
        )
        workflow._click = Mock()
        workflow.close_creator_tabs_keep_search = Mock(
            return_value={
                "creatorTabsClosed": True,
                "closedCreatorTabCount": 2,
                "searchTabKept": True,
                "returnedToFindCreators": True,
            }
        )
        return workflow

    def test_card_send_requires_explicit_confirmation(self) -> None:
        workflow = self._card_workflow()
        with self.assertRaisesRegex(
            ZiniaoWorkflowError,
            "必须显式确认发送合作卡片",
        ):
            workflow.send_collaboration_card(
                "@delaneykreusel",
                "7493994012378827459",
                "金色拉链+短裤13",
            )
        workflow._refresh_and_verify_invitation.assert_not_called()

    def test_existing_successful_target_plan_is_never_reclicked(
        self,
    ) -> None:
        workflow = self._card_workflow()
        workflow._chat_plan_card_evidence = Mock(
            return_value=self._plan_evidence(success=True)
        )
        result = workflow.send_collaboration_card(
            "@delaneykreusel",
            "7493994012378827459",
            "金色拉链+短裤13",
            confirm_send=True,
        )
        self.assertTrue(result.evidence["alreadySent"])
        self.assertTrue(result.evidence["finalSendVerified"])
        workflow._click.assert_not_called()

    def test_pending_target_plan_blocks_duplicate_click(self) -> None:
        workflow = self._card_workflow()
        workflow._chat_plan_card_evidence = Mock(
            return_value=self._plan_evidence(pending=1)
        )
        with self.assertRaisesRegex(
            ZiniaoWorkflowError,
            "已有发送中消息",
        ):
            workflow.send_collaboration_card(
                "@delaneykreusel",
                "7493994012378827459",
                "金色拉链+短裤13",
                confirm_send=True,
            )
        workflow._click.assert_not_called()

    def test_failed_target_plan_requires_manual_review(self) -> None:
        workflow = self._card_workflow()
        workflow._chat_plan_card_evidence = Mock(
            return_value=self._plan_evidence(failed=1)
        )
        with self.assertRaisesRegex(
            ZiniaoWorkflowError,
            "需要人工复核",
        ):
            workflow.send_collaboration_card(
                "@delaneykreusel",
                "7493994012378827459",
                "金色拉链+短裤13",
                confirm_send=True,
            )
        workflow._click.assert_not_called()

    def test_card_click_requires_new_successful_target_plan(self) -> None:
        workflow = self._card_workflow()
        card = Mock()
        send_button = Mock()
        workflow._right_panel_invitation_card = Mock(
            return_value=(
                card,
                "7664550207413847821",
                "7666768491349280526",
            )
        )
        workflow._collaboration_card_send_button = Mock(
            return_value=send_button
        )
        workflow._visible_card_send_success_text = Mock(
            return_value="发送成功"
        )
        workflow._chat_plan_card_evidence = Mock(
            side_effect=[
                self._plan_evidence(),
                self._plan_evidence(success=True),
            ]
        )
        workflow._wait = Mock(
            side_effect=lambda condition, **_kwargs: condition(Mock())
        )

        result = workflow.send_collaboration_card(
            "@delaneykreusel",
            "7493994012378827459",
            "金色拉链+短裤13",
            confirm_send=True,
        )

        workflow._click.assert_called_once_with(send_button)
        self.assertTrue(result.evidence["newTargetPlanMessage"])
        self.assertTrue(result.evidence["finalSendVerified"])

    def test_xpath_literal_handles_quotes(self) -> None:
        self.assertEqual(
            CreatorContactWorkflow._xpath_literal("plain"),
            "'plain'",
        )
        self.assertEqual(
            CreatorContactWorkflow._xpath_literal("single'quote"),
            '"single\'quote"',
        )


if __name__ == "__main__":
    unittest.main()
