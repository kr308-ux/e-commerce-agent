from __future__ import annotations

import base64
import hashlib
import os
import unittest
from unittest.mock import Mock, patch

from ziniao_automation.agent_runner import build_contact_prompt, main
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

    def test_full_prompt_contains_invitation_confirmations(self) -> None:
        prompt = build_contact_prompt(
            task_id="task-1",
            store_id="store-1",
            creator="@delaneykreusel",
            creator_id="7493994012378827459",
            invitation_name="金色拉链+短裤13",
            invitation_group_id="7664550207413847821",
            greeting_message="Exact task greeting",
            through_step=11,
            confirm_send_greeting=True,
            confirm_send_invitation=True,
        )
        self.assertIn("confirmSendGreeting=true", prompt)
        self.assertIn("confirmSendInvitation=true", prompt)
        self.assertNotIn("confirmSendCard", prompt)
        self.assertNotIn("ziniao_send_collaboration_card", prompt)
        self.assertIn("invitationName=金色拉链+短裤13", prompt)
        self.assertEqual(
            prompt.count(
                "invitationGroupId=7664550207413847821"
            ),
            2,
        )
        self.assertIn(
            "invitationGroupId 都必须与其精确相等",
            prompt,
        )
        self.assertIn("greetingMessage=\"Exact task greeting\"", prompt)
        self.assertNotIn("invitationId", prompt)
        self.assertIn("点击一次指定邀请的最终邀请按钮", prompt)
        self.assertIn("按钮点击调用成功后立即清理", prompt)
        self.assertIn("不得等待右侧合作卡片同步", prompt)
        self.assertNotIn("MutationObserver", prompt)
        self.assertIn("invitationGroupId、", prompt)
        self.assertIn("每次点击前随机停留 3–5 秒", prompt)
        self.assertIn("刷新前随机停留", prompt)
        self.assertIn("5–8 秒", prompt)
        self.assertIn("不得自行追加刷新", prompt)

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

    @patch("ziniao_automation.agent_runner.subprocess.run")
    @patch("ziniao_automation.agent_runner._credentials")
    def test_main_uses_one_canonical_greeting_for_prompt_and_mcp(
        self,
        mocked_credentials: Mock,
        mocked_run: Mock,
    ) -> None:
        greeting = "We’re ready\r\nUnicode 🧘‍♀️\rFinal line"
        canonical = "We’re ready\nUnicode 🧘‍♀️\nFinal line"
        expected_sha256 = hashlib.sha256(
            canonical.encode("utf-8")
        ).hexdigest()
        mocked_credentials.return_value = Mock(
            company="company",
            username="username",
            password="password",
        )
        mocked_run.return_value = Mock(returncode=0)

        with patch.dict(
            os.environ,
            {"DEEPSEEK_API_KEY": "test-key"},
        ):
            result = main(
                [
                    "--store-id",
                    "store-1",
                    "--creator",
                    "@creator",
                    "--greeting-message",
                    greeting,
                    "--through-step",
                    "7",
                    "--confirm-send-greeting",
                ]
            )

        self.assertEqual(result, 0)
        call = mocked_run.call_args
        prompt = call.args[0][-1]
        environment = call.kwargs["env"]
        self.assertEqual(
            base64.b64decode(
                environment["ZINIAO_EXPECTED_GREETING_B64"]
            ).decode("utf-8"),
            canonical,
        )
        self.assertEqual(
            environment["ZINIAO_EXPECTED_GREETING_SHA256"],
            expected_sha256,
        )
        self.assertIn(f"其 SHA-256 固定为\n{expected_sha256}", prompt)
        self.assertIn(
            'greetingMessage="We’re ready\\nUnicode 🧘‍♀️\\nFinal line"',
            prompt,
        )


if __name__ == "__main__":
    unittest.main()
