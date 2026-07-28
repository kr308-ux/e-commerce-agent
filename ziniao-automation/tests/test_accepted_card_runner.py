from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import MagicMock, Mock, patch

from ziniao_automation.accepted_card_runner import main
from ziniao_automation.actions.creator_contact import WorkflowStepResult


class AcceptedCardRunnerTests(unittest.TestCase):
    def test_batch_uses_one_connection_and_one_project_page(self) -> None:
        driver = Mock()
        session = Mock(driver=driver)
        connection = MagicMock(
            session=session,
            connection_mode="reused",
            cache_persisted=True,
        )
        connection.__enter__ = Mock(return_value=connection)
        connection.__exit__ = Mock(return_value=False)
        workflow = Mock()
        workflow._cdp_click_recovery_count = 0
        workflow.open_project_accepted_creators.return_value = (
            WorkflowStepResult(
                step=1,
                action="open_project_accepted_creators",
                success=True,
                evidence={
                    "acceptedCreatorsPageVisible": True,
                    "projectPageReused": True,
                },
            )
        )
        workflow.verify_creator_membership.side_effect = [
            WorkflowStepResult(
                step=2,
                action="verify_project_creator_membership",
                success=True,
                evidence={"projectMembershipVerified": True},
            ),
            WorkflowStepResult(
                step=2,
                action="verify_project_creator_membership",
                success=True,
                evidence={"projectMembershipVerified": True},
            ),
        ]
        workflow.open_creator_chat.side_effect = [
            WorkflowStepResult(
                step=2,
                action="open_accepted_creator_chat",
                success=True,
                evidence={"recipientVerified": True},
            ),
            WorkflowStepResult(
                step=2,
                action="open_accepted_creator_chat",
                success=True,
                evidence={"recipientVerified": True},
            ),
        ]
        workflow.send_collaboration_card.side_effect = [
            WorkflowStepResult(
                step=3,
                action="send_accepted_creator_collaboration_card",
                success=True,
                evidence={
                    "invitationId": f"766676849134928052{index}",
                    "invitationGroupId": "7664550207413847821",
                    "cardSent": True,
                    "targetPlanMessageVerified": True,
                    "planCardServerIds": [f"server-{index}"],
                    "targetPlanFlightStatus": 3,
                    "finalSendVerified": True,
                },
            )
            for index in (1, 2)
        ]
        output = io.StringIO()

        with (
            patch(
                "ziniao_automation.accepted_card_runner.ZiniaoSettings.from_env"
            ),
            patch(
                "ziniao_automation.accepted_card_runner.connect_reusable_store",
                return_value=connection,
            ) as connect,
            patch(
                "ziniao_automation.accepted_card_runner.AcceptedCollaborationWorkflow",
                return_value=workflow,
            ),
            redirect_stdout(output),
        ):
            exit_code = main(
                [
                    "--store-id",
                    "store-1",
                    "--creators-json",
                    '["@highest", "@middle"]',
                    "--invitation-name",
                    "金色拉链+短裤13",
                    "--invitation-group-id",
                    "7664550207413847821",
                    "--confirm-send-card",
                ]
            )

        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(len(payload["results"]), 2)
        self.assertEqual(connect.call_count, 1)
        workflow.open_project_accepted_creators.assert_called_once()
        self.assertEqual(
            workflow.verify_creator_membership.call_count,
            2,
        )
        self.assertEqual(workflow.open_creator_chat.call_count, 2)
        self.assertEqual(
            workflow.send_collaboration_card.call_count,
            2,
        )
        self.assertEqual(payload["connectionMode"], "reused")


if __name__ == "__main__":
    unittest.main()
