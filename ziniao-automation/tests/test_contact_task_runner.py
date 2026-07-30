from __future__ import annotations

import unittest

from ziniao_automation.contact_task_runner import _calls, _parser


class ContactTaskRunnerTests(unittest.TestCase):
    def test_full_flow_builds_fixed_safe_arguments(self) -> None:
        arguments = _parser().parse_args(
            [
                "--store-id",
                "store-1",
                "--creator",
                "@creator_id_123",
                "--invitation-name",
                "计划 A",
                "--invitation-group-id",
                "7664255664765880078",
                "--greeting-message",
                "Hello",
                "--through-step",
                "11",
                "--confirm-send-greeting",
                "--confirm-send-invitation",
            ]
        )

        calls = _calls(arguments)
        tools = [name for name, _input in calls]
        greeting = dict(calls)["ziniao_send_greeting"]

        self.assertEqual(tools[0], "ziniao_connect")
        self.assertEqual(tools[-1], "ziniao_disconnect")
        self.assertEqual(len(tools), 13)
        self.assertEqual(
            dict(calls)["ziniao_search_creator"]["creator"],
            "creator_id_123",
        )
        self.assertIs(greeting["confirmSendGreeting"], True)
        self.assertNotIn("confirmSendGreiting", greeting)
        self.assertEqual(
            dict(calls)["ziniao_send_selected_invitation"][
                "invitationGroupId"
            ],
            "7664255664765880078",
        )


if __name__ == "__main__":
    unittest.main()
