from __future__ import annotations

import unittest

from ziniao_automation.agent_runner import build_contact_prompt
from ziniao_automation.errors import ZiniaoWorkflowError


class AgentRunnerTests(unittest.TestCase):
    def test_read_only_prompt_stops_before_recipient_mutations(self) -> None:
        prompt = build_contact_prompt(
            task_id="task-1",
            store_id="store-1",
            creator="@delaneykreusel",
        )
        self.assertNotIn("ziniao_send_greeting", prompt)
        self.assertNotIn("ziniao_send_selected_invitation", prompt)
        self.assertIn("本次未授权输入或发送消息", prompt)

    def test_full_prompt_contains_all_explicit_confirmations(self) -> None:
        prompt = build_contact_prompt(
            task_id="task-1",
            store_id="store-1",
            creator="@delaneykreusel",
            creator_id="7493994012378827459",
            invitation_name="金色拉链+短裤13",
            invitation_group_id="7664550207413847821",
            greeting_message="Exact task greeting",
            through_step=12,
            confirm_send_greeting=True,
            confirm_send_invitation=True,
            confirm_send_card=True,
        )
        self.assertIn("confirmSendGreeting=true", prompt)
        self.assertIn("confirmSendInvitation=true", prompt)
        self.assertIn("confirmSendCard=true", prompt)
        self.assertIn("invitationName=金色拉链+短裤13", prompt)
        self.assertEqual(
            prompt.count(
                "invitationGroupId=7664550207413847821"
            ),
            3,
        )
        self.assertIn(
            "invitationGroupId 都必须与其精确相等",
            prompt,
        )
        self.assertIn("greetingMessage=\"Exact task greeting\"", prompt)
        self.assertIn("invitationId 使用上一步返回", prompt)
        self.assertIn("invitationGroupId、", prompt)
        self.assertIn("skipCreator=true", prompt)
        self.assertIn("随机等待 3–5 秒", prompt)
        self.assertIn("status=SKIPPED", prompt)

    def test_full_prompt_discovers_creator_id_when_not_preprovided(
        self,
    ) -> None:
        prompt = build_contact_prompt(
            task_id="task-1",
            store_id="store-1",
            creator="@dynamic_creator",
            through_step=7,
            confirm_send_greeting=True,
        )
        self.assertIn("不得自行猜测或使用外部达人 UID", prompt)
        self.assertNotIn("creatorId=None", prompt)

    def test_prompt_refuses_unconfirmed_mutation(self) -> None:
        with self.assertRaises(ZiniaoWorkflowError):
            build_contact_prompt(
                task_id="task-1",
                store_id="store-1",
                creator="@delaneykreusel",
                creator_id="7493994012378827459",
                through_step=7,
            )

    def test_prompt_requires_group_id_before_selecting_invitation(
        self,
    ) -> None:
        with self.assertRaisesRegex(
            ZiniaoWorkflowError,
            "invitationGroupId",
        ):
            build_contact_prompt(
                task_id="task-1",
                store_id="store-1",
                creator="@delaneykreusel",
                through_step=10,
                confirm_send_greeting=True,
            )


if __name__ == "__main__":
    unittest.main()
