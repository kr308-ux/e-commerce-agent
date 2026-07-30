from __future__ import annotations

import base64
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from ziniao_automation.errors import ZiniaoWorkflowError
from ziniao_automation.mcp_server import (
    TOOL_ORDER,
    ContactAutomationState,
    ZiniaoContactMcpServer,
)
from ziniao_automation.models import StartedStore, StoreInfo


class ZiniaoContactMcpServerTests(unittest.TestCase):
    def test_regular_operation_writes_input_and_output_log(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(
                "os.environ",
                {"RUNTIME_LOG_ROOT": directory},
            ):
                state = ContactAutomationState()
                result = state.call(
                    "unknown-operation",
                    {
                        "taskId": "task-log",
                        "stepId": "unknown",
                        "value": "完整输入",
                    },
                )

            paths = list(Path(directory).glob("*/regular/*.jsonl"))
            self.assertEqual(len(paths), 1)
            records = [
                json.loads(line)
                for line in paths[0].read_text(
                    encoding="utf-8"
                ).splitlines()
            ]
            self.assertEqual(len(records), 2)
            self.assertEqual(records[0]["event"], "operation_started")
            self.assertEqual(records[0]["input"]["value"], "完整输入")
            self.assertEqual(records[1]["event"], "operation_finished")
            self.assertEqual(records[1]["output"], result)

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
        self.assertTrue(greeting["annotations"]["destructiveHint"])
        self.assertTrue(invitation["annotations"]["destructiveHint"])
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
        self.assertNotIn("ziniao_send_collaboration_card", tools)
        self.assertNotIn(
            "creatorId",
            tools["ziniao_verify_chat_recipient"][
                "inputSchema"
            ]["required"],
        )
        for tool_name in (
            "ziniao_select_invitation",
            "ziniao_send_selected_invitation",
        ):
            schema = tools[tool_name]["inputSchema"]
            self.assertIn("invitationGroupId", schema["required"])
            self.assertEqual(
                schema["properties"]["invitationGroupId"]["pattern"],
                "^[0-9]+$",
            )

    def test_recipient_verification_precedes_mutations(self) -> None:
        self.assertLess(
            TOOL_ORDER.index("ziniao_verify_chat_recipient"),
            TOOL_ORDER.index("ziniao_send_greeting"),
        )
        self.assertLess(
            TOOL_ORDER.index("ziniao_select_invitation"),
            TOOL_ORDER.index("ziniao_send_selected_invitation"),
        )
        self.assertNotIn("ziniao_send_collaboration_card", TOOL_ORDER)

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

    def test_runner_snapshot_rejects_model_changed_creator(self) -> None:
        with patch.dict(
            "os.environ",
            {"ZINIAO_EXPECTED_CREATOR": "@catshrank"},
        ):
            state = ContactAutomationState()
        state.workflow = Mock()

        with self.assertRaisesRegex(
            ZiniaoWorkflowError,
            "不得更换目标达人",
        ):
            state._search_creator({"creator": "@catshrank_88"})

        state.workflow.search_creator.assert_not_called()

    def test_runner_snapshot_rejects_model_changed_store(self) -> None:
        with patch.dict(
            "os.environ",
            {"ZINIAO_EXPECTED_STORE_ID": "27850427216664"},
        ):
            state = ContactAutomationState()

        with self.assertRaisesRegex(
            ZiniaoWorkflowError,
            "固定店铺不一致",
        ):
            state._connect({"storeId": "wrong-store"})

    def _connect_fixtures(
        self,
    ) -> tuple[Mock, StoreInfo, StartedStore, Mock, Mock, Mock]:
        settings = Mock()
        settings.browser_session_dir = Path("/tmp/ziniao-test-cache")
        settings.browser_probe_timeout_seconds = 2
        settings.browser_lock_timeout_seconds = 30
        settings.reuse_browser_session = True
        store = StoreInfo("store-1", "Demo", "secret")
        started = StartedStore(
            store=store,
            debugging_port=9222,
            core_version="146.1.4.29",
            core_type="Chromium",
            browser_path="",
            launcher_page="",
            ip_detection_page="",
            download_path="",
        )
        client = Mock()
        client.list_stores.return_value = [store]
        lease = Mock()
        lease_factory = Mock()
        lease_factory.acquire.return_value = lease
        session = Mock()
        session.started = started
        session.driver.current_url = (
            "https://example.test/connection/creator"
        )
        session.driver.title = "Find creators"
        return settings, store, started, client, lease_factory, session

    def test_connect_reuses_live_cached_browser_without_starting(
        self,
    ) -> None:
        (
            settings,
            _store,
            started,
            _client,
            _lease_factory,
            session,
        ) = self._connect_fixtures()
        connection = Mock(
            session=session,
            store=_store,
            connection_mode="reused",
            cache_persisted=True,
        )
        with (
            patch(
                "ziniao_automation.mcp_server.ZiniaoSettings.from_env",
                return_value=settings,
            ),
            patch(
                "ziniao_automation.mcp_server.connect_reusable_store",
                return_value=connection,
            ),
            patch("ziniao_automation.mcp_server.CreatorContactWorkflow"),
        ):
            state = ContactAutomationState()
            result = state._connect({"storeId": "store-1"})

        self.assertEqual(result["connectionMode"], "reused")
        self.assertTrue(result["browserSessionCachePersisted"])
        state.close()
        connection.close.assert_called_once()

    def test_connect_starts_once_and_persists_cache_on_miss(
        self,
    ) -> None:
        (
            settings,
            _store,
            started,
            _client,
            _lease_factory,
            session,
        ) = self._connect_fixtures()
        connection = Mock(
            session=session,
            store=_store,
            connection_mode="started",
            cache_persisted=True,
        )
        with (
            patch(
                "ziniao_automation.mcp_server.ZiniaoSettings.from_env",
                return_value=settings,
            ),
            patch(
                "ziniao_automation.mcp_server.connect_reusable_store",
                return_value=connection,
            ),
            patch("ziniao_automation.mcp_server.CreatorContactWorkflow"),
        ):
            state = ContactAutomationState()
            result = state._connect({"storeId": "store-1"})

        self.assertEqual(result["connectionMode"], "started")
        self.assertTrue(result["browserSessionCachePersisted"])
        state.close()
        connection.close.assert_called_once()

    def test_runner_injects_exact_greeting_snapshot(self) -> None:
        greeting = "Exact emoji sequence 🧘‍♀️"
        greeting_sha256 = hashlib.sha256(
            greeting.encode("utf-8")
        ).hexdigest()
        with patch.dict(
            "os.environ",
            {
                "ZINIAO_EXPECTED_GREETING_B64": (
                    base64.b64encode(greeting.encode("utf-8")).decode(
                        "ascii"
                    )
                ),
                "ZINIAO_EXPECTED_GREETING_SHA256": greeting_sha256,
            },
        ):
            state = ContactAutomationState()
        state.creator = "@creator"
        state.creator_id = "7493994012378827459"
        state.workflow = Mock()
        state.workflow.send_greeting.return_value.to_dict.return_value = {
            "success": True,
            "evidence": {"messageSent": True},
        }

        state._send_greeting(
            {
                "creator": "@creator",
                "creatorId": "7493994012378827459",
                "greetingMessage": "Model-normalized emoji 🧘♀️",
                "greetingSha256": greeting_sha256,
                "confirmSendGreeting": True,
            }
        )

        state.workflow.send_greeting.assert_called_once_with(
            "@creator",
            "7493994012378827459",
            greeting,
            confirm_send=True,
            expected_sha256=greeting_sha256,
        )

    def test_runner_canonicalizes_crlf_snapshot_before_hash_check(
        self,
    ) -> None:
        raw_greeting = "We’re ready\r\nUnicode 🧘‍♀️\rFinal line"
        canonical = "We’re ready\nUnicode 🧘‍♀️\nFinal line"
        raw_sha256 = hashlib.sha256(
            raw_greeting.encode("utf-8")
        ).hexdigest()
        canonical_sha256 = hashlib.sha256(
            canonical.encode("utf-8")
        ).hexdigest()
        with patch.dict(
            "os.environ",
            {
                "ZINIAO_EXPECTED_GREETING_B64": base64.b64encode(
                    raw_greeting.encode("utf-8")
                ).decode("ascii"),
                "ZINIAO_EXPECTED_GREETING_SHA256": raw_sha256,
            },
        ):
            state = ContactAutomationState()
        state.creator = "@creator"
        state.creator_id = "7493994012378827459"
        state.workflow = Mock()
        state.workflow.send_greeting.return_value.to_dict.return_value = {
            "success": True,
            "evidence": {"messageSent": True},
        }

        state._send_greeting(
            {
                "creator": "@creator",
                "creatorId": "7493994012378827459",
                "greetingMessage": canonical,
                "greetingSha256": canonical_sha256,
                "confirmSendGreeting": True,
            }
        )

        self.assertEqual(state.expected_greeting, canonical)
        self.assertEqual(
            state.expected_greeting_sha256,
            canonical_sha256,
        )
        state.workflow.send_greeting.assert_called_once_with(
            "@creator",
            "7493994012378827459",
            canonical,
            confirm_send=True,
            expected_sha256=canonical_sha256,
        )

    def test_first_tool_failure_closes_session_and_blocks_retries(
        self,
    ) -> None:
        state = ContactAutomationState()
        workflow = Mock()
        workflow.search_creator.side_effect = ZiniaoWorkflowError(
            "不可重试失败"
        )
        state.workflow = workflow
        state._check_order = Mock()  # type: ignore[method-assign]

        first = state.call(
            "ziniao_search_creator",
            {"creator": "@creator"},
        )
        second = state.call(
            "ziniao_search_creator",
            {"creator": "@creator"},
        )

        self.assertFalse(first["success"])
        self.assertEqual(
            first["error"]["code"],
            "ZiniaoWorkflowError",
        )
        self.assertFalse(second["success"])
        self.assertEqual(
            second["error"]["code"],
            "TASK_TERMINATED_AFTER_FAILURE",
        )
        self.assertTrue(state.should_exit)
        self.assertIsNone(state.workflow)
        workflow.search_creator.assert_called_once_with("@creator")

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

    def test_send_invitation_allows_completed_state_without_invitation_id(
        self,
    ) -> None:
        state = self._bound_state()
        state.invitation_name = "计划 A"
        state.invitation_group_id = "7664550207413847821"
        state.workflow.send_selected_invitation.return_value.to_dict.return_value = {
            "success": True,
            "evidence": {
                "invitationGroupId": "7664550207413847821",
                "invitationCompleted": True,
                "invitationButtonClicked": True,
                "invitationSubmissionConfirmed": True,
                "creatorTabsClosed": True,
                "creatorDetailTargetGone": True,
                "searchTabKept": True,
                "returnedToFindCreators": True,
                "findCreatorsSearchReady": True,
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

        self.assertTrue(result["evidence"]["invitationCompleted"])
        self.assertNotIn("invitationId", result["evidence"])

    def test_send_invitation_accepts_button_click_without_panel_confirmation(
        self,
    ) -> None:
        state = self._bound_state()
        state.invitation_name = "计划 A"
        state.invitation_group_id = "7664550207413847821"
        state.workflow.send_selected_invitation.return_value.to_dict.return_value = {
            "success": True,
            "evidence": {
                "invitationGroupId": "7664550207413847821",
                "invitationCompleted": True,
                "invitationButtonClicked": True,
                "invitationSubmissionConfirmed": False,
                "creatorTabsClosed": True,
                "creatorDetailTargetGone": True,
                "searchTabKept": True,
                "returnedToFindCreators": True,
                "findCreatorsSearchReady": True,
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

        self.assertTrue(result["evidence"]["invitationCompleted"])
        self.assertFalse(
            result["evidence"]["invitationSubmissionConfirmed"]
        )


if __name__ == "__main__":
    unittest.main()
