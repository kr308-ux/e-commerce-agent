from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from ziniao_automation.actions.accepted_collaboration import (
    AcceptedCollaborationWorkflow,
)
from ziniao_automation.actions.creator_contact import WorkflowStepResult
from ziniao_automation.errors import ZiniaoWorkflowError


class AcceptedCollaborationWorkflowTests(unittest.TestCase):
    @staticmethod
    def _plan_evidence(
        success: bool = False,
        *,
        suffix: str = "1",
    ) -> dict[str, object]:
        message_key = f"message:client:server-accepted-{suffix}"
        invitation_id = f"766676849134928052{suffix}"
        verified_cards = (
            [
                {
                    "messageKey": message_key,
                    "invitationId": invitation_id,
                    "serverId": f"server-accepted-{suffix}",
                    "flightStatus": 3,
                    "createTime": 1_800_000_000_000,
                }
            ]
            if success
            else []
        )
        return {
            "exactPlanCardVisible": success,
            "exactPlanCardCount": 1 if success else 0,
            "planCardServerIds": (
                [f"server-accepted-{suffix}"] if success else []
            ),
            "planCardMessageKeys": [message_key] if success else [],
            "verifiedPlanCardMessageKeys": (
                [message_key] if success else []
            ),
            "targetPlanInvitationIds": (
                [invitation_id] if success else []
            ),
            "verifiedPlanCards": verified_cards,
            "targetPlanMessageVerified": success,
            "targetPlanFlightStatus": 3 if success else None,
            "targetPlanPendingCount": 0,
            "targetPlanFailedCount": 0,
            "targetPlanCreateTimes": (
                [1_800_000_000_000] if success else []
            ),
        }

    def _ready_workflow(self) -> AcceptedCollaborationWorkflow:
        workflow = AcceptedCollaborationWorkflow(Mock())
        workflow._project_name = "金色拉链+短裤13"
        workflow._project_group_id = "7664550207413847821"
        workflow._accepted_creator = "highest"
        return workflow

    def test_card_send_requires_explicit_confirmation(self) -> None:
        workflow = self._ready_workflow()
        with self.assertRaisesRegex(
            ZiniaoWorkflowError,
            "必须显式确认",
        ):
            workflow.send_collaboration_card(
                "@highest",
                "金色拉链+短裤13",
                "7664550207413847821",
            )

    def test_creator_details_is_clicked_before_reading_creator_rows(
        self,
    ) -> None:
        workflow = AcceptedCollaborationWorkflow(Mock())
        toggle = Mock()
        row = Mock()
        workflow._visible_rows = Mock(side_effect=[[], [row]])
        workflow._creator_details_toggle = Mock(return_value=toggle)
        workflow._click = Mock()
        workflow._wait = Mock(
            side_effect=lambda condition, **_kwargs: condition(Mock())
        )

        clicked = workflow._ensure_creator_details_expanded()

        self.assertTrue(clicked)
        workflow._click.assert_called_once_with(toggle)

    def test_chat_icon_auto_selects_recent_contact(self) -> None:
        workflow = self._ready_workflow()
        row = Mock()
        chat_button = Mock()
        drawer_contact = Mock()
        workflow._unique_row_with_text = Mock(return_value=row)
        workflow._creator_chat_button = Mock(return_value=chat_button)
        workflow._wait = Mock(return_value=drawer_contact)
        workflow._drawer_conversation_ready = Mock(
            return_value={
                "drawerSelectedHandleMatched": True,
                "drawerComposerVisible": True,
            }
        )
        workflow._click = Mock()

        result = workflow.open_creator_chat("@highest")

        self.assertEqual(
            [call.args[0] for call in workflow._click.call_args_list],
            [chat_button],
        )
        self.assertTrue(result.evidence["recipientVerified"])
        self.assertTrue(result.evidence["drawerComposerVisible"])

    def test_project_membership_requires_unique_exact_creator_row(
        self,
    ) -> None:
        workflow = self._ready_workflow()
        row = Mock()
        row.get_attribute.return_value = "highest\nHighest Creator"
        workflow._unique_row_with_text = Mock(return_value=row)

        result = workflow.verify_creator_membership("@highest")

        self.assertTrue(result.evidence["projectMembershipVerified"])
        self.assertTrue(result.evidence["acceptedCreatorsPageVisible"])
        self.assertFalse(result.evidence["finalInviteButtonClicked"])
        self.assertFalse(result.evidence["cardSendButtonClicked"])

    def test_reuses_marked_project_page_without_refreshing(self) -> None:
        driver = Mock()
        driver.window_handles = ["project", "other"]
        workflow = AcceptedCollaborationWorkflow(driver)
        workflow._activate_accepted_creators_window = Mock(
            side_effect=[
                {
                    "handle": "project",
                    "currentUrl": "https://example.test/project/detail",
                },
                False,
            ]
        )
        workflow._ensure_creator_details_expanded = Mock(
            return_value=False
        )
        workflow._visible_rows = Mock(return_value=[Mock(), Mock()])
        workflow._unique_row_with_text = Mock()
        workflow._project_name_click_target = Mock()
        workflow._project_snapshot = Mock(return_value={})
        workflow._click = Mock()
        workflow._wait = Mock(return_value={"handle": "new-detail"})
        workflow.driver.execute_script = Mock()
        workflow.driver.close = Mock()
        sync = Mock()
        sync._affiliate_window.return_value = "affiliate"
        sync._direct_target_url.return_value = ""
        sync.open_target_page = Mock()
        sync.activate_in_progress = Mock()
        with patch(
            "ziniao_automation.actions.accepted_collaboration."
            "TargetCollaborationSync",
            return_value=sync,
        ):
            result = workflow.open_project_accepted_creators(
                "金色拉链+短裤13",
                "7664550207413847821",
            )

        self.assertTrue(result.evidence["projectPageReused"] is False)
        self.assertTrue(result.evidence["reusedProjectPageClosed"])
        driver.close.assert_called_once()

    @patch(
        "ziniao_automation.actions.accepted_collaboration."
        "TargetCollaborationSync"
    )
    def test_opens_project_by_name_without_checking_accepted_count(
        self,
        sync_class: Mock,
    ) -> None:
        driver = Mock()
        driver.current_url = "https://example.test/target-invitation"
        driver.current_window_handle = "project-detail"
        driver.window_handles = ["project-detail"]
        workflow = AcceptedCollaborationWorkflow(driver)
        sync = sync_class.return_value
        sync._affiliate_window.return_value = "affiliate"
        sync._direct_target_url.return_value = ""
        workflow._project_snapshot = Mock(
            return_value={"acceptedCreatorCount": 0}
        )
        row = Mock()
        project_name = Mock()
        workflow._unique_row_with_text = Mock(return_value=row)
        workflow._project_name_click_target = Mock(
            return_value=project_name
        )
        workflow._click = Mock()
        workflow._wait = Mock(
            return_value={
                "handle": "project-detail",
                "currentUrl": "https://example.test/project/detail",
            }
        )
        workflow._ensure_creator_details_expanded = Mock(
            return_value=False
        )
        workflow._visible_rows = Mock(return_value=[])

        result = workflow.open_project_accepted_creators(
            "金色拉链+短裤13",
            "7664550207413847821",
        )

        workflow._project_name_click_target.assert_called_once_with(
            row,
            "金色拉链+短裤13",
        )
        workflow._click.assert_called_once_with(project_name)
        self.assertTrue(result.success)
        self.assertEqual(result.evidence["acceptedCreatorCount"], 0)

    def test_chat_icon_then_recent_contact_are_both_clicked(self) -> None:
        workflow = self._ready_workflow()
        row = Mock()
        chat_button = Mock()
        drawer_contact = Mock()
        workflow._unique_row_with_text = Mock(return_value=row)
        workflow._creator_chat_button = Mock(return_value=chat_button)
        workflow._wait = Mock(
            side_effect=[
                drawer_contact,
                {
                    "drawerSelectedHandleMatched": True,
                    "drawerComposerVisible": True,
                },
            ]
        )
        workflow._drawer_conversation_ready = Mock(return_value=False)
        workflow._click = Mock()

        result = workflow.open_creator_chat("@highest")

        self.assertEqual(
            [call.args[0] for call in workflow._click.call_args_list],
            [chat_button, drawer_contact],
        )
        self.assertTrue(result.evidence["recipientVerified"])
        self.assertTrue(result.evidence["drawerSelectedHandleMatched"])

    def test_sends_from_accepted_creator_chat_without_refresh(self) -> None:
        workflow = self._ready_workflow()
        card = Mock()
        send_button = Mock()
        workflow._right_panel_invitation_card = Mock(
            return_value=(
                card,
                "7664550207413847821",
                "",
            )
        )
        workflow._collaboration_card_send_button = Mock(
            return_value=send_button
        )
        workflow._chat_plan_card_evidence = Mock(
            side_effect=[
                self._plan_evidence(),
                self._plan_evidence(success=True),
            ]
        )
        workflow._click = Mock()
        workflow._wait = Mock(
            side_effect=lambda condition, **_kwargs: condition(Mock())
        )

        result = workflow.send_collaboration_card(
            "@highest",
            "金色拉链+短裤13",
            "7664550207413847821",
            confirm_send=True,
        )

        workflow._click.assert_called_once_with(send_button)
        workflow.driver.refresh.assert_not_called()
        self.assertTrue(result.evidence["cardSent"])
        self.assertTrue(result.evidence["finalSendVerified"])
        self.assertEqual(
            result.evidence["planCardServerIds"],
            ["server-accepted-1"],
        )

    def test_card_not_found_retries_close_and_reopen_then_skips(self) -> None:
        workflow = self._ready_workflow()
        workflow._right_panel_invitation_card = Mock(return_value=None)
        workflow.close_chat_drawer = Mock(
            return_value=WorkflowStepResult(
                step=3,
                action="close_accepted_creator_chat_drawer",
                success=True,
                evidence={"chatDrawerClosed": True},
            )
        )
        workflow.open_creator_chat = Mock(
            return_value=WorkflowStepResult(
                step=2,
                action="open_accepted_creator_chat",
                success=True,
                evidence={"recipientVerified": True},
            )
        )
        workflow._wait = Mock(
            side_effect=ZiniaoWorkflowError("未加载合作卡片")
        )

        result = workflow.send_collaboration_card(
            "@highest",
            "金色拉链+短裤13",
            "7664550207413847821",
            confirm_send=True,
        )

        self.assertFalse(result.evidence["cardSent"])
        self.assertTrue(result.evidence["cardSkipped"])
        self.assertTrue(result.evidence["reviewRequired"])
        self.assertEqual(result.evidence["cardRetryCount"], 2)
        self.assertEqual(workflow.close_chat_drawer.call_count, 2)
        self.assertEqual(workflow.open_creator_chat.call_count, 2)

    def test_card_not_found_recovers_on_first_reopen(self) -> None:
        workflow = self._ready_workflow()
        card = Mock()
        send_button = Mock()
        workflow._right_panel_invitation_card = Mock(
            side_effect=[None, (card, "7664550207413847821", "")]
        )
        workflow._collaboration_card_send_button = Mock(
            return_value=send_button
        )
        workflow._chat_plan_card_evidence = Mock(
            side_effect=[
                self._plan_evidence(),
                self._plan_evidence(success=True),
            ]
        )
        workflow.close_chat_drawer = Mock(
            return_value=WorkflowStepResult(
                step=3,
                action="close_accepted_creator_chat_drawer",
                success=True,
                evidence={"chatDrawerClosed": True},
            )
        )
        workflow.open_creator_chat = Mock(
            return_value=WorkflowStepResult(
                step=2,
                action="open_accepted_creator_chat",
                success=True,
                evidence={"recipientVerified": True},
            )
        )
        workflow._click = Mock()
        workflow._wait = Mock(
            side_effect=lambda condition, **_kwargs: condition(Mock())
        )

        result = workflow.send_collaboration_card(
            "@highest",
            "金色拉链+短裤13",
            "7664550207413847821",
            confirm_send=True,
        )

        self.assertTrue(result.evidence["cardSent"])
        self.assertTrue(result.evidence["finalSendVerified"])
        self.assertEqual(workflow.close_chat_drawer.call_count, 1)
        self.assertEqual(workflow.open_creator_chat.call_count, 1)
        workflow._click.assert_called_once_with(send_button)

    def test_historical_plan_card_does_not_verify_new_delivery(self) -> None:
        workflow = self._ready_workflow()
        send_button = Mock()
        workflow._right_panel_invitation_card = Mock(
            return_value=(
                Mock(),
                "7664550207413847821",
                "",
            )
        )
        workflow._collaboration_card_send_button = Mock(
            return_value=send_button
        )
        baseline = self._plan_evidence(success=True, suffix="1")
        after = self._plan_evidence(success=True, suffix="2")
        after["verifiedPlanCards"] = [
            *baseline["verifiedPlanCards"],
            *after["verifiedPlanCards"],
        ]
        after["verifiedPlanCardMessageKeys"] = [
            *baseline["verifiedPlanCardMessageKeys"],
            *after["verifiedPlanCardMessageKeys"],
        ]
        workflow._chat_plan_card_evidence = Mock(
            side_effect=[baseline, after]
        )
        workflow._wait = Mock(
            side_effect=lambda condition, **_kwargs: condition(Mock())
        )
        workflow._click = Mock()

        result = workflow.send_collaboration_card(
            "@highest",
            "金色拉链+短裤13",
            "7664550207413847821",
            confirm_send=True,
        )

        workflow._click.assert_called_once_with(send_button)
        self.assertFalse(result.evidence["alreadySent"])
        self.assertEqual(
            result.evidence["invitationId"],
            "7666768491349280522",
        )

    def test_close_chat_drawer_uses_first_clickable_and_verifies_gone(
        self,
    ) -> None:
        workflow = AcceptedCollaborationWorkflow(Mock())
        close_button = Mock()
        workflow._first_clickable = Mock(return_value=close_button)
        workflow._click = Mock()
        workflow._wait = Mock(
            side_effect=lambda condition, **_kwargs: condition(Mock())
        )
        workflow._drawer_gone = Mock(return_value=True)

        result = workflow.close_chat_drawer()

        workflow._first_clickable.assert_called_once()
        workflow._click.assert_called_once_with(close_button)
        self.assertTrue(result.evidence["chatDrawerClosed"])
        self.assertTrue(result.evidence["chatDrawerComposerGone"])

    def test_drawer_gone_detects_visible_composer(self) -> None:
        workflow = AcceptedCollaborationWorkflow(Mock())
        workflow._visible_message_composers = Mock(return_value=[Mock()])

        self.assertFalse(workflow._drawer_gone())

    def test_drawer_gone_treats_close_button_still_visible_as_open(
        self,
    ) -> None:
        workflow = AcceptedCollaborationWorkflow(Mock())
        workflow._visible_message_composers = Mock(return_value=[])
        visible_button = Mock()
        workflow.driver.find_elements = Mock(
            side_effect=[[], [visible_button]]
        )
        workflow._is_visible = Mock(
            side_effect=lambda element: element is visible_button
        )

        self.assertFalse(workflow._drawer_gone())

    def test_close_project_tab_closes_matching_group_marker(self) -> None:
        driver = Mock()
        workflow = AcceptedCollaborationWorkflow(driver)
        workflow._find_creators_handle = "find"
        workflow._creator_detail_handle = "detail"
        workflow._chat_handle = "chat"
        driver.window_handles = ["project-tab", "find", "other"]
        driver.execute_script.return_value = "7664550207413847821"

        result = workflow.close_project_accepted_creators_tab(
            "7664550207413847821"
        )

        self.assertTrue(result["projectAcceptedCreatorsTabClosed"])
        driver.close.assert_called_once()

    def test_close_project_tab_skips_protected_handles(self) -> None:
        driver = Mock()
        workflow = AcceptedCollaborationWorkflow(driver)
        workflow._find_creators_handle = "find"
        workflow._creator_detail_handle = None
        workflow._chat_handle = None
        driver.window_handles = ["find"]
        driver.execute_script.return_value = "7664550207413847821"

        result = workflow.close_project_accepted_creators_tab(
            "7664550207413847821"
        )

        self.assertFalse(result["projectAcceptedCreatorsTabClosed"])
        driver.close.assert_not_called()


if __name__ == "__main__":
    unittest.main()
