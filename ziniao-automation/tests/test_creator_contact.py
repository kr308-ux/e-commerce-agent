from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from ziniao_automation.actions.creator_contact import (
    APPROVED_GREETING_MESSAGE,
    CreatorContactWorkflow,
    WorkflowStepResult,
)
from ziniao_automation.errors import ZiniaoWorkflowError


class CreatorContactWorkflowTests(unittest.TestCase):
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
        self.assertFalse(result.evidence["invitationSent"])
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
        switch_window("chat")
        workflow = CreatorContactWorkflow(driver)
        workflow._find_creators_handle = "search"
        workflow._find_creators_url = urls["search"]
        workflow._creator_detail_handle = "detail"
        workflow._chat_handle = "chat"

        result = workflow.close_creator_tabs_keep_search()

        self.assertEqual(handles, ["search"])
        self.assertTrue(result["creatorTabsClosed"])
        self.assertEqual(result["closedCreatorTabCount"], 2)
        self.assertTrue(result["searchTabKept"])
        self.assertEqual(driver.current_window_handle, "search")

    def test_invitation_refresh_verification_never_reclicks(self) -> None:
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
        workflow._wait = Mock(
            side_effect=ZiniaoWorkflowError("实时面板未更新")
        )
        workflow._refresh_invitation_with_retries = Mock(
            return_value={
                "rightPanelInvitationVisible": True,
                "invitationId": "7666768491349280526",
                "invitationGroupId": "7664550207413847821",
                "successMessage": "",
                "verifiedAfterRefresh": True,
                "refreshAttempts": 1,
                "randomWaitSeconds": [3.5],
                "invitationSyncPending": False,
                "skipCreator": False,
            }
        )
        workflow._right_panel_invitation_card = Mock(
            return_value=(
                Mock(),
                "7664550207413847821",
                "7666768491349280526",
            )
        )
        workflow._click = Mock()

        result = workflow.send_selected_invitation(
            "@delaneykreusel",
            "7493994012378827459",
            "金色拉链+短裤13",
            confirm_send=True,
        )

        workflow._click.assert_called_once_with(invite_button)
        workflow._refresh_invitation_with_retries.assert_called_once()
        self.assertTrue(result.evidence["verifiedAfterRefresh"])
        self.assertTrue(result.evidence["invitationCreated"])
        self.assertFalse(result.evidence["invitationSent"])

    def test_invitation_refresh_retries_three_times_with_random_waits(
        self,
    ) -> None:
        workflow = CreatorContactWorkflow(Mock(), timeout_seconds=60)
        workflow._refresh_and_verify_invitation = Mock(
            side_effect=ZiniaoWorkflowError("卡片尚未同步")
        )
        with (
            patch(
                "ziniao_automation.actions.creator_contact.random.uniform",
                side_effect=[3.25, 4.5, 4.875],
            ),
            patch(
                "ziniao_automation.actions.creator_contact.time.sleep"
            ) as sleep,
        ):
            result = workflow._refresh_invitation_with_retries(
                "@delaneykreusel",
                "7493994012378827459",
                "金色拉链+短裤13",
            )

        self.assertTrue(result["skipCreator"])
        self.assertTrue(result["invitationSyncPending"])
        self.assertEqual(result["refreshAttempts"], 3)
        self.assertEqual(
            result["randomWaitSeconds"],
            [3.25, 4.5, 4.875],
        )
        self.assertEqual(
            workflow._refresh_and_verify_invitation.call_count,
            3,
        )
        self.assertEqual(
            [call.args[0] for call in sleep.call_args_list],
            [3.25, 4.5, 4.875],
        )

    def test_invitation_success_text_accepts_add_success_toast(self) -> None:
        driver = Mock()
        toast = Mock()
        toast.text = "添加成功"
        driver.find_elements.return_value = [toast]
        workflow = CreatorContactWorkflow(driver)
        workflow._is_visible = Mock(return_value=True)

        self.assertEqual(
            workflow._visible_invitation_success_text(),
            "添加成功",
        )

    def test_success_toast_without_synced_card_skips_creator(
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
        workflow._wait = Mock(
            return_value={
                "rightPanelInvitationVisible": False,
                "successMessage": "邀请添加成功",
            }
        )
        workflow._refresh_invitation_with_retries = Mock(
            return_value={
                "rightPanelInvitationVisible": False,
                "verifiedAfterRefresh": False,
                "refreshAttempts": 3,
                "randomWaitSeconds": [3.2, 4.1, 4.9],
                "invitationSyncPending": True,
                "skipCreator": True,
                "skipReason": "连续刷新三次仍未显示",
            }
        )
        workflow.close_creator_tabs_keep_search = Mock(
            return_value={
                "creatorTabsClosed": True,
                "closedCreatorTabCount": 2,
                "searchTabKept": True,
                "returnedToFindCreators": True,
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
        self.assertTrue(result.evidence["skipCreator"])
        self.assertFalse(result.evidence["invitationCreated"])
        self.assertFalse(result.evidence["cardReadyToSend"])
        self.assertEqual(result.evidence["refreshAttempts"], 3)
        self.assertEqual(
            result.evidence["invitationGroupId"],
            "7664550207413847821",
        )
        workflow._click.assert_called_once_with(invite_button)
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
