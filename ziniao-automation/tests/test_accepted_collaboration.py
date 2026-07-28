from __future__ import annotations

import unittest
from unittest.mock import Mock

from ziniao_automation.actions.accepted_collaboration import (
    AcceptedCollaborationWorkflow,
)
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
        workflow = AcceptedCollaborationWorkflow(driver)
        workflow._activate_accepted_creators_window = Mock(
            return_value={
                "handle": "project",
                "currentUrl": "https://example.test/project/detail",
            }
        )
        workflow._ensure_creator_details_expanded = Mock(
            return_value=False
        )
        workflow._visible_rows = Mock(return_value=[Mock(), Mock()])

        result = workflow.open_project_accepted_creators(
            "金色拉链+短裤13",
            "7664550207413847821",
        )

        self.assertTrue(result.evidence["projectPageReused"])
        self.assertTrue(result.evidence["pageRefreshSkipped"])
        self.assertEqual(result.evidence["acceptedCreatorCount"], 2)
        driver.get.assert_not_called()
        driver.refresh.assert_not_called()

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


if __name__ == "__main__":
    unittest.main()
