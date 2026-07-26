from creator_contact.models import (
    ContactedCreator,
    CreatorContactTarget,
    CreatorContactTask,
)
from creator_contact.services.candidate_selector import freeze_task_targets
from creator_contact.services.collaboration_sync import (
    CollaborationSyncBatch,
    CollaborationSyncRunner,
    SubprocessCollaborationExecutor,
)
import json
import os
import subprocess
from unittest.mock import patch

from creator_contact.services.contact_runner import (
    CreatorContactRunner,
    SubprocessContactExecutor,
)
from creator_contact.models import CollaborationSyncJob, DirectedCollaborationOption

from .base import CreatorContactTestCase


class ContactRunnerTests(CreatorContactTestCase):
    @staticmethod
    def successful_executor(task, target):
        return {
            "success": True,
            "creatorId": f"700000000000000000{target.rank}",
            "messageSent": True,
            "invitationCreated": True,
            "invitationSent": True,
            "invitationId": f"766676849134928052{target.rank}",
            "invitationGroupId": task.invitation_id_snapshot,
            "cardSent": True,
            "targetPlanMessageVerified": True,
            "planCardServerIds": [f"server-{target.rank}"],
            "targetPlanFlightStatus": 3,
            "finalSendVerified": True,
            "creatorTabsClosed": True,
            "searchTabKept": True,
            "returnedToFindCreators": True,
            "sessionID": f"session-{target.rank}",
            "steps": [
                {
                    "stepId": "verify-chat-recipient",
                    "operation": "ziniao_verify_chat_recipient",
                    "label": "核验聊天对象",
                    "status": "SUCCESS",
                    "outputSummary": {
                        "creatorId": f"700000000000000000{target.rank}",
                    },
                },
                {
                    "stepId": "send-card",
                    "operation": "ziniao_send_collaboration_card",
                    "label": "发送定向合作卡片",
                    "status": "SUCCESS",
                    "outputSummary": {
                        "cardSent": True,
                        "finalSendVerified": True,
                    },
                },
            ],
        }

    def test_serial_runner_persists_dynamic_ids_steps_and_history(self):
        task = self.create_contact_task(top_n=2)
        freeze_task_targets(task)

        result = CreatorContactRunner(
            task,
            executor=self.successful_executor,
        ).run()

        self.assertEqual(result.status, CreatorContactTask.Status.SUCCESS)
        self.assertEqual(ContactedCreator.objects.count(), 2)
        self.assertEqual(task.steps.count(), 4)
        targets = list(task.targets.order_by("rank"))
        self.assertTrue(all(target.final_send_verified for target in targets))
        self.assertEqual(
            targets[0].chat_creator_id,
            "7000000000000000001",
        )
        self.assertEqual(
            targets[0].actual_invitation_id,
            "7666768491349280521",
        )
        self.assertTrue(targets[0].target_plan_message_verified)

    def test_success_without_final_card_proof_is_failed_and_not_deduped(self):
        task = self.create_contact_task(top_n=1)
        freeze_task_targets(task)

        def incomplete_executor(task, target):
            return {
                "success": True,
                "creatorId": "7001",
                "messageSent": True,
                "invitationCreated": True,
                "invitationId": "7666768491349280521",
                "invitationGroupId": task.invitation_id_snapshot,
                "cardSent": True,
                "targetPlanMessageVerified": False,
                "finalSendVerified": False,
                "steps": [],
            }

        result = CreatorContactRunner(
            task,
            executor=incomplete_executor,
        ).run()

        self.assertEqual(result.status, CreatorContactTask.Status.FAILED)
        self.assertEqual(
            task.targets.get().status,
            CreatorContactTarget.Status.FAILED,
        )
        self.assertFalse(ContactedCreator.objects.exists())

    def test_invitation_sync_timeout_is_skipped_and_not_deduped(self):
        task = self.create_contact_task(top_n=1)
        freeze_task_targets(task)

        def skipped_executor(current_task, target):
            return {
                "success": False,
                "creatorId": "7001",
                "messageSent": True,
                "invitationCreated": False,
                "invitationId": "",
                "invitationGroupId": current_task.invitation_id_snapshot,
                "cardSent": False,
                "finalSendVerified": False,
                "skipCreator": True,
                "skipReason": "随机等待后连续刷新三次仍未显示卡片",
                "invitationSyncPending": True,
                "refreshAttempts": 3,
                "randomWaitSeconds": [3.2, 4.1, 4.9],
                "steps": [],
            }

        result = CreatorContactRunner(
            task,
            executor=skipped_executor,
        ).run()
        target = task.targets.get()

        self.assertEqual(result.status, CreatorContactTask.Status.SUCCESS)
        self.assertEqual(target.status, CreatorContactTarget.Status.SKIPPED)
        self.assertEqual(
            target.error_code,
            "INVITATION_SYNC_NOT_VISIBLE",
        )
        self.assertTrue(target.message_sent)
        self.assertFalse(target.invitation_created)
        self.assertFalse(ContactedCreator.objects.exists())

    def test_terminal_success_rejects_weak_or_mismatched_server_evidence(self):
        cases = (
            (
                "group mismatch",
                {"invitationGroupId": "7664550207413847999"},
                "INVITATION_GROUP_MISMATCH",
            ),
            (
                "non-numeric invitation id",
                {"invitationId": "actual-invitation-1"},
                "INVALID_INVITATION_ID",
            ),
            (
                "missing plan card server ids",
                {"planCardServerIds": []},
                "MISSING_PLAN_CARD_SERVER_IDS",
            ),
            (
                "invalid target plan flight status",
                {"targetPlanFlightStatus": 2},
                "INVALID_TARGET_PLAN_FLIGHT_STATUS",
            ),
            (
                "creator tabs not cleaned",
                {"creatorTabsClosed": False},
                "CREATOR_TABS_NOT_CLEANED",
            ),
        )
        for label, replacement, expected_code in cases:
            with self.subTest(label=label):
                task = self.create_contact_task(top_n=1)
                freeze_task_targets(task)

                def executor(current_task, target):
                    result = self.successful_executor(current_task, target)
                    result.update(replacement)
                    return result

                result = CreatorContactRunner(task, executor=executor).run()
                target = task.targets.get()

                self.assertEqual(
                    result.status,
                    CreatorContactTask.Status.FAILED,
                )
                self.assertEqual(
                    target.status,
                    CreatorContactTarget.Status.FAILED,
                )
                self.assertEqual(target.error_code, expected_code)
                self.assertFalse(
                    ContactedCreator.objects.filter(
                        contact_task=task,
                    ).exists()
                )

    def test_subprocess_executor_uses_full_flow_contract_and_parses_jsonl(self):
        task = self.create_contact_task(top_n=1)
        freeze_task_targets(task)
        target = task.targets.get()
        tool_event = {
            "type": "tool_use",
            "sessionID": "session-live-shape",
            "part": {
                "tool": "ziniao-contact_ziniao_open_chat_new_tab",
                "state": {
                    "status": "completed",
                    "input": {
                        "taskId": "test",
                        "stepId": "open-chat-new-tab",
                        "creator": "@highest",
                    },
                    "output": json.dumps(
                        {
                            "success": True,
                            "status": "SUCCESS",
                            "data": {
                                "step": 5,
                                "action": "open_chat_in_new_tab",
                                "success": True,
                                "evidence": {
                                    "creatorId": "7493994012378827459",
                                },
                            },
                            "error": None,
                        }
                    ),
                },
            },
        }
        final_event = {
            "type": "text",
            "part": {
                "text": json.dumps(
                    {
                        "success": True,
                        "creatorId": "7493994012378827459",
                        "messageSent": True,
                        "invitationCreated": True,
                        "invitationSent": True,
                        "cardSent": True,
                        "finalSendVerified": True,
                    }
                )
            },
        }
        card_event = {
            "type": "tool_use",
            "sessionID": "session-live-shape",
            "part": {
                "tool": (
                    "ziniao-contact_"
                    "ziniao_send_collaboration_card"
                ),
                "state": {
                    "status": "completed",
                    "input": {
                        "taskId": "test",
                        "stepId": "send-collaboration-card",
                        "creator": "@highest",
                    },
                    "output": json.dumps(
                        {
                            "success": True,
                            "status": "SUCCESS",
                            "data": {
                                "step": 12,
                                "action": "send_collaboration_card",
                                "success": True,
                                "evidence": {
                                    "invitationId": (
                                        "7666768491349280526"
                                    ),
                                    "invitationGroupId": (
                                        "7664550207413847821"
                                    ),
                                    "cardSent": True,
                                    "finalSendVerified": True,
                                    "targetPlanMessageVerified": True,
                                    "planCardServerIds": ["server-1"],
                                    "targetPlanFlightStatus": 3,
                                    "creatorTabsClosed": True,
                                    "searchTabKept": True,
                                    "returnedToFindCreators": True,
                                },
                            },
                            "error": None,
                        }
                    ),
                },
            },
        }
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=(
                json.dumps(tool_event)
                + "\n"
                + json.dumps(card_event)
                + "\n"
                + json.dumps(final_event)
                + "\n"
            ),
            stderr="",
        )

        with patch.dict(
            os.environ,
            {
                "DEEPSEEK_API_KEY": "test-key",
                "ZINIAO_COMPANY": "test-company",
                "ZINIAO_USERNAME": "test-user",
                "ZINIAO_PASSWORD": "test-password",
            },
        ), patch(
            "creator_contact.services.contact_runner.subprocess.run",
            return_value=completed,
        ) as run:
            result = SubprocessContactExecutor()(task, target)

        command = run.call_args.args[0]
        self.assertIn("--greeting-message", command)
        self.assertEqual(
            command[command.index("--invitation-group-id") + 1],
            task.invitation_id_snapshot,
        )
        self.assertIn("--confirm-send-card", command)
        self.assertEqual(command[command.index("--through-step") + 1], "12")
        self.assertTrue(result["finalSendVerified"])
        self.assertEqual(result["creatorId"], "7493994012378827459")
        self.assertEqual(result["sessionID"], "session-live-shape")
        self.assertEqual(
            result["invitationId"],
            "7666768491349280526",
        )
        self.assertTrue(result["targetPlanMessageVerified"])
        self.assertEqual(len(result["steps"]), 2)

    def test_subprocess_parser_preserves_invitation_sync_skip_evidence(self):
        skip_event = {
            "type": "tool_use",
            "sessionID": "session-skip",
            "part": {
                "tool": (
                    "ziniao-contact_"
                    "ziniao_send_selected_invitation"
                ),
                "state": {
                    "status": "completed",
                    "input": {
                        "stepId": "send-selected-invitation",
                    },
                    "output": json.dumps(
                        {
                            "success": True,
                            "status": "SUCCESS",
                            "data": {
                                "step": 11,
                                "action": "send_selected_invitation",
                                "success": True,
                                "evidence": {
                                    "messageSent": True,
                                    "invitationGroupId": (
                                        "7664550207413847821"
                                    ),
                                    "skipCreator": True,
                                    "skipReason": "连续刷新三次仍未显示",
                                    "invitationSyncPending": True,
                                    "refreshAttempts": 3,
                                    "randomWaitSeconds": [
                                        3.2,
                                        4.1,
                                        4.9,
                                    ],
                                    "creatorTabsClosed": True,
                                    "closedCreatorTabCount": 2,
                                    "searchTabKept": True,
                                    "returnedToFindCreators": True,
                                    "findCreatorsUrl": (
                                        "https://example.test/"
                                        "connection/creator"
                                    ),
                                },
                            },
                            "error": None,
                        }
                    ),
                },
            },
        }
        final_event = {
            "type": "text",
            "part": {
                "text": json.dumps(
                    {
                        "success": False,
                        "status": "SKIPPED",
                        "errorCode": "INVITATION_SYNC_NOT_VISIBLE",
                    }
                )
            },
        }
        output = (
            json.dumps(skip_event)
            + "\n"
            + json.dumps(final_event)
            + "\n"
        )

        result = SubprocessContactExecutor._parse_output(output)

        self.assertTrue(result["skipCreator"])
        self.assertEqual(
            result["skipReason"],
            "连续刷新三次仍未显示",
        )
        self.assertTrue(result["invitationSyncPending"])
        self.assertEqual(result["refreshAttempts"], 3)
        self.assertEqual(result["randomWaitSeconds"], [3.2, 4.1, 4.9])
        self.assertTrue(result["creatorTabsClosed"])
        self.assertEqual(result["closedCreatorTabCount"], 2)
        self.assertTrue(result["searchTabKept"])
        self.assertTrue(result["returnedToFindCreators"])


class CollaborationSyncRunnerTests(CreatorContactTestCase):
    def test_upserts_ongoing_rows_and_deactivates_missing_options(self):
        stale = DirectedCollaborationOption.objects.create(
            store_id=self.store_id,
            external_invitation_id="stale",
            name="Stale invitation",
        )
        job = CollaborationSyncJob.objects.create(store_id=self.store_id)

        runner = CollaborationSyncRunner(
            job,
            executor=lambda store_id: [
                {
                    "invitationGroupId": "new-13",
                    "name": "金色拉链+短裤13",
                    "status": "进行中",
                    "productCount": 2,
                    "invitedCreatorCount": 26,
                    "promotedCreatorCount": 4,
                }
            ],
        )
        result = runner.run()

        self.assertEqual(result.status, CollaborationSyncJob.Status.SUCCESS)
        self.assertEqual(result.imported_count, 1)
        stale.refresh_from_db()
        self.assertFalse(stale.is_active)
        synced = DirectedCollaborationOption.objects.get(
            external_invitation_id="new-13"
        )
        self.assertEqual(synced.product_count, 2)
        self.assertEqual(synced.invited_creator_count, 26)
        self.assertEqual(synced.promoted_creator_count, 4)

    def test_missing_group_id_fails_and_preserves_active_cache(self):
        cached = DirectedCollaborationOption.objects.create(
            store_id=self.store_id,
            external_invitation_id="cached-13",
            name="Cached invitation",
        )
        cached_ids = set(
            DirectedCollaborationOption.objects.filter(
                store_id=self.store_id,
                is_active=True,
            ).values_list("external_invitation_id", flat=True)
        )
        job = CollaborationSyncJob.objects.create(store_id=self.store_id)

        result = CollaborationSyncRunner(
            job,
            executor=lambda _store_id: [
                {
                    "name": "DOM fallback without ID",
                    "status": "ONGOING",
                }
            ],
        ).run()

        self.assertEqual(result.status, CollaborationSyncJob.Status.FAILED)
        self.assertIn("invitationGroupId", result.error_message)
        cached.refresh_from_db()
        self.assertTrue(cached.is_active)
        self.assertEqual(
            set(
                DirectedCollaborationOption.objects.filter(
                    store_id=self.store_id,
                    is_active=True,
                ).values_list("external_invitation_id", flat=True)
            ),
            cached_ids,
        )

    def test_duplicate_group_id_rolls_back_and_preserves_cache(self):
        cached = DirectedCollaborationOption.objects.create(
            store_id=self.store_id,
            external_invitation_id="cached-13",
            name="Cached invitation",
        )
        job = CollaborationSyncJob.objects.create(store_id=self.store_id)

        result = CollaborationSyncRunner(
            job,
            executor=lambda _store_id: [
                {
                    "invitationGroupId": "duplicate-13",
                    "name": "First",
                    "status": "ONGOING",
                },
                {
                    "invitationGroupId": "duplicate-13",
                    "name": "Second",
                    "status": "ONGOING",
                },
            ],
        ).run()

        self.assertEqual(result.status, CollaborationSyncJob.Status.FAILED)
        self.assertIn("重复 invitationGroupId", result.error_message)
        cached.refresh_from_db()
        self.assertTrue(cached.is_active)
        self.assertFalse(
            DirectedCollaborationOption.objects.filter(
                store_id=self.store_id,
                external_invitation_id="duplicate-13",
            ).exists()
        )

    def test_unconfirmed_empty_result_preserves_cache(self):
        cached = DirectedCollaborationOption.objects.create(
            store_id=self.store_id,
            external_invitation_id="cached-13",
            name="Cached invitation",
        )
        job = CollaborationSyncJob.objects.create(store_id=self.store_id)

        result = CollaborationSyncRunner(
            job,
            executor=lambda _store_id: [],
        ).run()

        self.assertEqual(result.status, CollaborationSyncJob.Status.FAILED)
        self.assertIn("未明确确认的空结果", result.error_message)
        cached.refresh_from_db()
        self.assertTrue(cached.is_active)

    def test_explicit_complete_empty_result_can_deactivate_cache(self):
        cached = DirectedCollaborationOption.objects.create(
            store_id=self.store_id,
            external_invitation_id="cached-13",
            name="Cached invitation",
        )
        active_before = DirectedCollaborationOption.objects.filter(
            store_id=self.store_id,
            is_active=True,
        ).count()
        job = CollaborationSyncJob.objects.create(store_id=self.store_id)

        result = CollaborationSyncRunner(
            job,
            executor=lambda _store_id: CollaborationSyncBatch.from_rows(
                [],
                complete=True,
                explicit_empty=True,
            ),
        ).run()

        self.assertEqual(result.status, CollaborationSyncJob.Status.SUCCESS)
        self.assertEqual(result.imported_count, 0)
        self.assertEqual(result.deactivated_count, active_before)
        cached.refresh_from_db()
        self.assertFalse(cached.is_active)

    def test_subprocess_sync_executor_parses_last_standard_json(self):
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=(
                "diagnostic line\n"
                + json.dumps(
                    {
                        "success": True,
                        "storeId": "store-test-1",
                        "options": [
                            {
                                "invitationGroupId": "group-13",
                                "name": "金色拉链+短裤13",
                                "status": "ONGOING",
                            }
                        ],
                    }
                )
                + "\n"
            ),
            stderr="",
        )
        with patch.dict(
            os.environ,
            {
                "ZINIAO_COMPANY": "test-company",
                "ZINIAO_USERNAME": "test-user",
                "ZINIAO_PASSWORD": "test-password",
            },
        ), patch(
            "creator_contact.services.collaboration_sync.subprocess.run",
            return_value=completed,
        ) as run:
            rows = list(SubprocessCollaborationExecutor()(self.store_id))

        command = run.call_args.args[0]
        self.assertIn("ziniao_automation.collaboration_sync_runner", command)
        self.assertEqual(
            command[-3:],
            ["--store-id", "store-test-1", "--json"],
        )
        self.assertEqual(rows[0]["invitationGroupId"], "group-13")
