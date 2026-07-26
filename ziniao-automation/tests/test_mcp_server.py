from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from ziniao_automation.errors import ZiniaoWorkflowError
from ziniao_automation.mcp_server import (
    TOOL_ORDER,
    ContactAutomationState,
    ZiniaoContactMcpServer,
)


class ZiniaoContactMcpServerTests(unittest.TestCase):
    def test_initialize_advertises_tools(self) -> None:
        server = ZiniaoContactMcpServer()
        response = server.handle(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-03-26"},
            }
        )
        assert response is not None
        result = response["result"]
        self.assertEqual(result["protocolVersion"], "2025-03-26")
        self.assertIn("tools", result["capabilities"])

    def test_tool_list_matches_enforced_order(self) -> None:
        server = ZiniaoContactMcpServer()
        response = server.handle(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/list",
                "params": {},
            }
        )
        assert response is not None
        names = [
            tool["name"]
            for tool in response["result"]["tools"]
        ]
        self.assertEqual(names, list(TOOL_ORDER))

    def test_prompt_rules_match_current_tool_order(self) -> None:
        rules_path = (
            Path(__file__).resolve().parents[2]
            / "prompts"
            / "ziniao-contact-rules.md"
        )
        rules = rules_path.read_text(encoding="utf-8")
        positions = [rules.index(f"`{name}`") for name in TOOL_ORDER]
        self.assertEqual(positions, sorted(positions))
        self.assertNotIn("ziniao_send_approved_greeting", rules)

    def test_remote_mutations_require_explicit_true_confirmation(self) -> None:
        server = ZiniaoContactMcpServer()
        response = server.handle(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/list",
                "params": {},
            }
        )
        assert response is not None
        tools = {
            tool["name"]: tool
            for tool in response["result"]["tools"]
        }
        greeting = tools["ziniao_send_greeting"]
        invitation = tools["ziniao_send_selected_invitation"]
        card = tools["ziniao_send_collaboration_card"]
        self.assertTrue(greeting["annotations"]["destructiveHint"])
        self.assertTrue(invitation["annotations"]["destructiveHint"])
        self.assertTrue(card["annotations"]["destructiveHint"])
        self.assertIs(
            greeting["inputSchema"]["properties"][
                "confirmSendGreeting"
            ]["const"],
            True,
        )
        self.assertIs(
            invitation["inputSchema"]["properties"][
                "confirmSendInvitation"
            ]["const"],
            True,
        )
        self.assertIs(
            card["inputSchema"]["properties"][
                "confirmSendCard"
            ]["const"],
            True,
        )
        self.assertNotIn(
            "creatorId",
            tools["ziniao_verify_chat_recipient"][
                "inputSchema"
            ]["required"],
        )
        for tool_name in (
            "ziniao_select_invitation",
            "ziniao_send_selected_invitation",
            "ziniao_send_collaboration_card",
        ):
            schema = tools[tool_name]["inputSchema"]
            self.assertIn("invitationGroupId", schema["required"])
            self.assertEqual(
                schema["properties"]["invitationGroupId"]["pattern"],
                "^[0-9]+$",
            )

    def test_recipient_verification_precedes_both_mutations(self) -> None:
        self.assertLess(
            TOOL_ORDER.index("ziniao_verify_chat_recipient"),
            TOOL_ORDER.index("ziniao_send_greeting"),
        )
        self.assertLess(
            TOOL_ORDER.index("ziniao_select_invitation"),
            TOOL_ORDER.index("ziniao_send_selected_invitation"),
        )
        self.assertLess(
            TOOL_ORDER.index("ziniao_send_selected_invitation"),
            TOOL_ORDER.index("ziniao_send_collaboration_card"),
        )

    def _bound_state(self) -> ContactAutomationState:
        state = ContactAutomationState()
        state.creator = "@creator"
        state.creator_id = "7493994012378827459"
        state.workflow = Mock()
        return state

    def test_select_invitation_binds_group_id_into_state_and_result(
        self,
    ) -> None:
        state = self._bound_state()
        state.workflow.select_invitation.return_value.to_dict.return_value = {
            "success": True,
            "evidence": {"invitationName": "计划 A"},
        }
        result = state._select_invitation(
            {
                "creator": "@creator",
                "creatorId": "7493994012378827459",
                "invitationName": "计划 A",
                "invitationGroupId": "7664550207413847821",
            }
        )
        self.assertEqual(
            state.invitation_group_id,
            "7664550207413847821",
        )
        self.assertEqual(
            result["evidence"]["invitationGroupId"],
            "7664550207413847821",
        )
        self.assertIs(
            result["evidence"]["invitationGroupIdBound"],
            True,
        )
        state.workflow.select_invitation.assert_called_once_with(
            "@creator",
            "7493994012378827459",
            "计划 A",
            invitation_group_id="7664550207413847821",
        )

    def test_select_rejects_group_different_from_runner_snapshot(
        self,
    ) -> None:
        with patch.dict(
            "os.environ",
            {
                "ZINIAO_INVITATION_GROUP_ID": (
                    "7664550207413847821"
                )
            },
        ):
            state = ContactAutomationState()
        state.creator = "@creator"
        state.creator_id = "7493994012378827459"
        state.workflow = Mock()
        with self.assertRaisesRegex(
            ZiniaoWorkflowError,
            "不得更换 invitationGroupId",
        ):
            state._select_invitation(
                {
                    "creator": "@creator",
                    "creatorId": "7493994012378827459",
                    "invitationName": "计划 A",
                    "invitationGroupId": "9999999999999999999",
                }
            )
        state.workflow.select_invitation.assert_not_called()

    def test_send_invitation_rejects_result_from_other_group(
        self,
    ) -> None:
        state = self._bound_state()
        state.invitation_name = "计划 A"
        state.invitation_group_id = "7664550207413847821"
        state.workflow.send_selected_invitation.return_value.to_dict.return_value = {
            "success": True,
            "evidence": {
                "invitationId": "7666768491349280526",
                "invitationGroupId": "9999999999999999999",
            },
        }
        with self.assertRaisesRegex(
            ZiniaoWorkflowError,
            "invitationGroupId",
        ):
            state._send_selected_invitation(
                {
                    "creator": "@creator",
                    "creatorId": "7493994012378827459",
                    "invitationName": "计划 A",
                    "invitationGroupId": "7664550207413847821",
                    "confirmSendInvitation": True,
                }
            )
        state.workflow.send_selected_invitation.assert_called_once_with(
            "@creator",
            "7493994012378827459",
            "计划 A",
            invitation_group_id="7664550207413847821",
            confirm_send=True,
        )

    def test_send_invitation_allows_explicit_skip_without_invitation_id(
        self,
    ) -> None:
        state = self._bound_state()
        state.invitation_name = "计划 A"
        state.invitation_group_id = "7664550207413847821"
        state.workflow.send_selected_invitation.return_value.to_dict.return_value = {
            "success": True,
            "evidence": {
                "invitationId": "",
                "invitationGroupId": "7664550207413847821",
                "skipCreator": True,
                "skipReason": "连续刷新三次仍未显示",
            },
        }

        result = state._send_selected_invitation(
            {
                "creator": "@creator",
                "creatorId": "7493994012378827459",
                "invitationName": "计划 A",
                "invitationGroupId": "7664550207413847821",
                "confirmSendInvitation": True,
            }
        )

        self.assertTrue(result["evidence"]["skipCreator"])
        self.assertIsNone(state.invitation_id)

    def test_card_rejects_changed_group_before_workflow_call(
        self,
    ) -> None:
        state = self._bound_state()
        state.invitation_name = "计划 A"
        state.invitation_group_id = "7664550207413847821"
        state.invitation_id = "7666768491349280526"
        with self.assertRaisesRegex(
            ZiniaoWorkflowError,
            "不得更换 invitationGroupId",
        ):
            state._send_collaboration_card(
                {
                    "creator": "@creator",
                    "creatorId": "7493994012378827459",
                    "invitationName": "计划 A",
                    "invitationId": "7666768491349280526",
                    "invitationGroupId": "9999999999999999999",
                    "confirmSendCard": True,
                }
            )
        state.workflow.send_collaboration_card.assert_not_called()

    def test_card_rejects_result_from_other_group(self) -> None:
        state = self._bound_state()
        state.invitation_name = "计划 A"
        state.invitation_group_id = "7664550207413847821"
        state.invitation_id = "7666768491349280526"
        state.workflow.send_collaboration_card.return_value.to_dict.return_value = {
            "success": True,
            "evidence": {
                "invitationId": "7666768491349280526",
                "invitationGroupId": "9999999999999999999",
            },
        }
        with self.assertRaisesRegex(
            ZiniaoWorkflowError,
            "invitationGroupId",
        ):
            state._send_collaboration_card(
                {
                    "creator": "@creator",
                    "creatorId": "7493994012378827459",
                    "invitationName": "计划 A",
                    "invitationId": "7666768491349280526",
                    "invitationGroupId": "7664550207413847821",
                    "confirmSendCard": True,
                }
            )
        state.workflow.send_collaboration_card.assert_called_once_with(
            "@creator",
            "7493994012378827459",
            "计划 A",
            "7666768491349280526",
            invitation_group_id="7664550207413847821",
            confirm_send=True,
        )


if __name__ == "__main__":
    unittest.main()
