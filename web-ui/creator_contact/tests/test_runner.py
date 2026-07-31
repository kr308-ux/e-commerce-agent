from creator_contact.models import (
    ContactedCreator,
    CreatorContactTarget,
    CreatorContactTask,
)
from creator_contact.services.candidate_selector import freeze_task_targets
from creator_contact.services.collaboration_sync import (
    CollaborationSyncBatch,
    CollaborationSyncBrowserBusyError,
    CollaborationSyncRunner,
    SubprocessCollaborationExecutor,
)
import json
import os
import subprocess
from unittest.mock import patch

from creator_contact.services.contact_runner import (
    CreatorContactRunner,
    SubprocessCardExecutor,
    SubprocessContactExecutor,
    _exact_creator_unavailable,
)
from creator_contact.services.subprocess_control import (
    TaskCancellationRequested,
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
            "invitationCompleted": True,
            "invitationButtonClicked": True,
            "invitationSubmissionAttempted": True,
            "invitationSubmissionConfirmed": True,
            "invitationCompletionSource": "final_invite_button_click",
            "invitationSent": True,
            "invitationGroupId": task.invitation_id_snapshot,
            "creatorTabsClosed": True,
            "creatorDetailTabClosed": True,
            "creatorDetailTargetGone": True,
            "creatorChatTabClosed": True,
            "searchTabKept": True,
            "returnedToFindCreators": True,
            "findCreatorsSearchReady": True,
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
                    "stepId": "send-selected-invitation",
                    "operation": "ziniao_send_selected_invitation",
                    "label": "发送已选择邀请",
                    "status": "SUCCESS",
                    "outputSummary": {
                        "invitationCompleted": True,
                        "invitationButtonClicked": True,
                        "invitationSubmissionConfirmed": True,
                    },
                },
            ],
        }

    @staticmethod
    def invitation_executor(task, target):
        return {
            "success": True,
            "creatorId": f"700000000000000000{target.rank}",
            "messageSent": True,
            "invitationCreated": True,
            "invitationCompleted": True,
            "invitationButtonClicked": True,
            "invitationSubmissionAttempted": True,
            "invitationSubmissionConfirmed": True,
            "invitationCompletionSource": "final_invite_button_click",
            "invitationSent": True,
            "invitationGroupId": task.invitation_id_snapshot,
            "creatorTabsClosed": True,
            "creatorDetailTabClosed": True,
            "creatorDetailTargetGone": True,
            "creatorChatTabClosed": True,
            "searchTabKept": True,
            "returnedToFindCreators": True,
            "findCreatorsSearchReady": True,
            "steps": [],
        }

    @staticmethod
    def accepted_card_executor(task, target):
        return {
            "success": True,
            "invitationId": f"766676849134928052{target.rank}",
            "invitationGroupId": task.invitation_id_snapshot,
            "cardSent": True,
            "targetPlanMessageVerified": True,
            "planCardServerIds": [f"accepted-server-{target.rank}"],
            "targetPlanFlightStatus": 3,
            "finalSendVerified": True,
            "deliverySource": "accepted_creator_list",
            "acceptedCreatorsPageVisible": True,
            "projectMembershipVerified": True,
            "recipientVerified": True,
            "steps": [],
        }

    def test_invitation_then_card_runs_for_every_target(self):
        task = self.create_contact_task(top_n=3)
        freeze_task_targets(task)
        events = []

        def invite(current_task, target):
            events.append(f"invite-{target.rank}")
            return self.invitation_executor(current_task, target)

        def card(current_task, target):
            events.append(f"card-{target.rank}")
            return self.accepted_card_executor(current_task, target)

        result = CreatorContactRunner(
            task,
            executor=invite,
            card_executor=card,
        ).run()

        self.assertEqual(
            events,
            [
                "invite-1",
                "invite-2",
                "invite-3",
                "card-1",
                "card-2",
                "card-3",
            ],
        )
        self.assertEqual(result.status, CreatorContactTask.Status.SUCCESS)
        self.assertEqual(
            list(task.targets.values_list("status", flat=True)),
            [CreatorContactTarget.Status.SUCCESS] * 3,
        )
        self.assertEqual(ContactedCreator.objects.count(), 3)
        self.assertEqual(
            result.final_summary["invitationCompletedCount"],
            3,
        )
        self.assertEqual(result.final_summary["cardSentCount"], 3)
        self.assertEqual(result.final_summary["cardPendingCount"], 0)

    def test_card_batch_executor_is_called_once_for_all_targets(self):
        task = self.create_contact_task(top_n=3)
        freeze_task_targets(task)
        calls = []

        class BatchExecutor:
            def __call__(self, *_args):
                raise AssertionError("批量执行器不应退回逐达人调用")

            def run_many(self, current_task, targets):
                calls.append([target.normalized_handle for target in targets])
                return {
                    target.pk: ContactRunnerTests.accepted_card_executor(
                        current_task,
                        target,
                    )
                    for target in targets
                }

        result = CreatorContactRunner(
            task,
            executor=self.invitation_executor,
            card_executor=BatchExecutor(),
        ).run()

        self.assertEqual(result.status, CreatorContactTask.Status.SUCCESS)
        self.assertEqual(calls, [["highest", "middle", "low"]])
        self.assertEqual(
            list(task.targets.values_list("status", flat=True)),
            [CreatorContactTarget.Status.SUCCESS] * 3,
        )

    def test_missing_creator_in_card_batch_stays_invitation_completed(self):
        task = self.create_contact_task(top_n=2)
        freeze_task_targets(task)

        class PartialBatch:
            def run_many(self, current_task, targets):
                return {
                    targets[0].pk: (
                        ContactRunnerTests.accepted_card_executor(
                            current_task,
                            targets[0],
                        )
                    )
                }

        result = CreatorContactRunner(
            task,
            executor=self.invitation_executor,
            card_executor=PartialBatch(),
        ).run()
        targets = list(task.targets.order_by("rank"))

        self.assertEqual(
            result.status,
            CreatorContactTask.Status.PARTIAL_SUCCESS,
        )
        self.assertEqual(
            targets[0].status,
            CreatorContactTarget.Status.SUCCESS,
        )
        self.assertEqual(
            targets[1].status,
            CreatorContactTarget.Status.INVITATION_COMPLETED,
        )
        self.assertEqual(
            targets[1].error_code,
            "CARD_BATCH_RESULT_MISSING",
        )
        self.assertEqual(ContactedCreator.objects.count(), 2)
        self.assertTrue(
            ContactedCreator.objects.filter(
                normalized_handle=targets[1].normalized_handle,
            ).exists()
        )
        self.assertEqual(result.final_summary["cardPendingCount"], 1)

    def test_lost_dom_receipt_is_not_recovered_by_membership(self):
        task = self.create_contact_task(top_n=2)
        freeze_task_targets(task)
        events = []

        def lost_receipt(current_task, target):
            events.append(f"invite-{target.rank}")
            return {
                "success": False,
                "creatorId": f"700000000000000000{target.rank}",
                "messageSent": True,
                "invitationCreated": False,
                "invitationGroupId": current_task.invitation_id_snapshot,
                "errorCode": "INVITATION_RECEIPT_LOST",
                "errorMessage": "邀请弹窗已关闭但本地回执丢失",
                "steps": [],
            }

        def membership(current_task, target):
            events.append(f"membership-{target.rank}")
            return {
                "success": True,
                "invitationGroupId": current_task.invitation_id_snapshot,
                "invitationCompleted": True,
                "invitationSubmitAcknowledged": True,
                "acceptedCreatorsPageVisible": True,
                "projectMembershipVerified": True,
                "deliverySource": (
                    "project_creator_details_reconciliation"
                ),
                "steps": [],
            }

        def card(current_task, target):
            events.append(f"card-{target.rank}")
            return self.accepted_card_executor(current_task, target)

        result = CreatorContactRunner(
            task,
            executor=lost_receipt,
            membership_executor=membership,
            card_executor=card,
        ).run()

        self.assertEqual(events, ["invite-1", "invite-2"])
        self.assertEqual(result.status, CreatorContactTask.Status.FAILED)
        self.assertEqual(
            list(task.targets.values_list("status", flat=True)),
            [CreatorContactTarget.Status.FAILED] * 2,
        )

    def test_membership_and_card_executors_are_ignored(
        self,
    ):
        task = self.create_contact_task(top_n=3)
        freeze_task_targets(task)
        events = []

        def lost_receipt(current_task, target):
            events.append(f"invite-{target.rank}")
            return {
                "success": False,
                "creatorId": f"700000000000000000{target.rank}",
                "messageSent": True,
                "invitationCreated": True,
                "invitationGroupId": current_task.invitation_id_snapshot,
                "errorCode": "INVITATION_RECEIPT_LOST",
                "errorMessage": "邀请回执不完整",
                "steps": [],
            }

        def membership(current_task, target):
            events.append(f"membership-{target.rank}")
            if target.rank == 2:
                return {
                    "success": False,
                    "projectMembershipVerified": False,
                    "errorCode": "MEMBERSHIP_NOT_FOUND",
                    "errorMessage": "项目列表未找到目标达人",
                    "steps": [],
                }
            return {
                "success": True,
                "invitationGroupId": current_task.invitation_id_snapshot,
                "invitationCompleted": True,
                "invitationSubmitAcknowledged": True,
                "acceptedCreatorsPageVisible": True,
                "projectMembershipVerified": True,
                "deliverySource": (
                    "project_creator_details_reconciliation"
                ),
                "steps": [],
            }

        def card(current_task, target):
            events.append(f"card-{target.rank}")
            return self.accepted_card_executor(current_task, target)

        result = CreatorContactRunner(
            task,
            executor=lost_receipt,
            membership_executor=membership,
            card_executor=card,
        ).run()

        self.assertEqual(events, ["invite-1", "invite-2", "invite-3"])
        self.assertEqual(
            list(
                task.targets.order_by("rank").values_list(
                    "status",
                    flat=True,
                )
            ),
            [
                CreatorContactTarget.Status.FAILED,
                CreatorContactTarget.Status.FAILED,
                CreatorContactTarget.Status.FAILED,
            ],
        )
        self.assertEqual(
            result.status,
            CreatorContactTask.Status.FAILED,
        )
        self.assertEqual(ContactedCreator.objects.count(), 0)

    def test_existing_invitation_completed_targets_go_to_card_batch(self):
        task = self.create_contact_task(top_n=2)
        freeze_task_targets(task)
        targets = list(task.targets.order_by("rank"))
        for target in targets:
            invitation_result = self.invitation_executor(task, target)
            target.status = CreatorContactTarget.Status.INVITATION_COMPLETED
            target.message_sent = True
            target.invitation_created = True
            target.invitation_group_id = task.invitation_id_snapshot
            target.result = invitation_result
            target.save()
        calls = []

        class BatchCard:
            def __call__(self, *_args):
                raise AssertionError("批量卡片不应退回逐达人调用")

            def run_many(self, current_task, current_targets):
                calls.append(
                    [target.normalized_handle for target in current_targets]
                )
                return {
                    target.pk: {
                        **ContactRunnerTests.accepted_card_executor(
                            current_task,
                            target,
                        )
                    }
                    for target in current_targets
                }

        result = CreatorContactRunner(
            task,
            executor=self.invitation_executor,
            card_executor=BatchCard(),
        ).run()

        self.assertEqual(result.status, CreatorContactTask.Status.SUCCESS)
        self.assertEqual(calls, [["highest", "middle"]])

    def test_serial_runner_persists_dynamic_ids_steps_and_history(self):
        task = self.create_contact_task(top_n=2)
        freeze_task_targets(task)

        result = CreatorContactRunner(
            task,
            executor=self.successful_executor,
            card_executor=self.accepted_card_executor,
        ).run()

        self.assertEqual(result.status, CreatorContactTask.Status.SUCCESS)
        self.assertEqual(ContactedCreator.objects.count(), 2)
        self.assertEqual(task.steps.count(), 4)
        targets = list(task.targets.order_by("rank"))
        self.assertTrue(all(target.invitation_created for target in targets))
        self.assertTrue(all(target.card_sent for target in targets))
        self.assertEqual(
            targets[0].chat_creator_id,
            "7000000000000000001",
        )
        self.assertEqual(
            targets[0].actual_invitation_id,
            "7666768491349280521",
        )
        self.assertTrue(targets[0].target_plan_message_verified)

    def test_success_without_invitation_click_proof_is_failed(self):
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
            "INVITATION_NOT_VERIFIED",
        )
        self.assertTrue(target.message_sent)
        self.assertFalse(target.invitation_created)
        self.assertFalse(ContactedCreator.objects.exists())

    def test_review_required_target_does_not_stop_following_creator(self):
        task = self.create_contact_task(top_n=2)
        freeze_task_targets(task)

        def executor(current_task, target):
            if target.rank == 1:
                return {
                    "success": False,
                    "messageSent": True,
                    "reviewRequired": True,
                    "failureStage": "REVIEW_REQUIRED",
                    "errorCode": "MODEL_FALLBACK_EXHAUSTED",
                    "errorMessage": "写入后无法恢复当前页面",
                    "steps": [],
                }
            return self.invitation_executor(current_task, target)

        result = CreatorContactRunner(
            task,
            executor=executor,
            card_executor=self.accepted_card_executor,
        ).run()
        targets = list(task.targets.order_by("rank"))

        self.assertEqual(
            targets[0].status,
            CreatorContactTarget.Status.REVIEW_REQUIRED,
        )
        self.assertEqual(
            targets[1].status,
            CreatorContactTarget.Status.SUCCESS,
        )
        self.assertEqual(
            result.status,
            CreatorContactTask.Status.PARTIAL_SUCCESS,
        )
        self.assertEqual(
            result.final_summary["reviewRequiredCount"],
            1,
        )

    def test_task_fatal_result_stops_batch_but_not_worker_contract(self):
        task = self.create_contact_task(top_n=2)
        freeze_task_targets(task)
        calls = []

        def executor(_current_task, target):
            calls.append(target.rank)
            return {
                "success": False,
                "taskFatal": True,
                "errorCode": "CONNECTION_ERROR",
                "errorMessage": "店铺浏览器不可连接",
                "steps": [],
            }

        result = CreatorContactRunner(
            task,
            executor=executor,
        ).run()

        self.assertEqual(calls, [1])
        self.assertEqual(
            result.status,
            CreatorContactTask.Status.FAILED,
        )
        self.assertEqual(result.error_code, "CONNECTION_ERROR")

    def test_button_click_is_deduped_even_when_panel_sync_times_out(self):
        task = self.create_contact_task(top_n=1)
        freeze_task_targets(task)

        def clicked_executor(current_task, target):
            return {
                "success": True,
                "creatorId": "7001",
                "messageSent": True,
                "invitationCreated": False,
                "invitationCompleted": False,
                "invitationButtonClicked": True,
                "invitationGroupId": current_task.invitation_id_snapshot,
                "creatorTabsClosed": True,
                "creatorDetailTargetGone": True,
                "searchTabKept": True,
                "returnedToFindCreators": True,
                "findCreatorsSearchReady": True,
                "skipCreator": True,
                "skipReason": "右侧合作卡片未同步",
                "invitationSyncPending": True,
                "steps": [],
            }

        CreatorContactRunner(task, executor=clicked_executor).run()
        target = task.targets.get()
        record = ContactedCreator.objects.get(
            store_id=task.store_id,
            normalized_handle=target.normalized_handle,
        )

        self.assertEqual(target.status, CreatorContactTarget.Status.SKIPPED)
        self.assertEqual(
            record.evidence["contactStage"],
            "INVITATION_COMPLETED",
        )
        self.assertTrue(record.evidence["invitationButtonClicked"])

    def test_invitation_phase_rejects_missing_click_or_cleanup_evidence(self):
        cases = (
            (
                "group mismatch",
                {"invitationGroupId": "7664550207413847999"},
                "INVITATION_GROUP_MISMATCH",
                False,
            ),
            (
                "missing final invitation click",
                {
                    "invitationButtonClicked": False,
                },
                "INVITATION_COMPLETION_NOT_VERIFIED",
                False,
            ),
            (
                "creator detail target still open",
                {"creatorDetailTargetGone": False},
                "INVITATION_COMPLETION_NOT_VERIFIED",
                True,
            ),
        )
        for label, replacement, expected_code, expected_contact in cases:
            with self.subTest(label=label):
                task = self.create_contact_task(top_n=1)
                freeze_task_targets(task)

                def executor(current_task, target):
                    result = self.successful_executor(current_task, target)
                    result.update(replacement)
                    return result

                result = CreatorContactRunner(
                    task,
                    executor=executor,
                    card_executor=self.accepted_card_executor,
                ).run()
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
                self.assertEqual(
                    ContactedCreator.objects.filter(
                        contact_task=task,
                    ).exists(),
                    expected_contact,
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
                "text": (
                    "All steps completed successfully.\n```json\n"
                    + json.dumps(
                        {
                            "success": True,
                            "creatorId": "7493994012378827459",
                            "messageSent": True,
                            "invitationCreated": True,
                            "invitationCompleted": True,
                            "invitationButtonClicked": True,
                            "invitationSubmissionConfirmed": True,
                            "invitationSent": True,
                        }
                    )
                    + "\n```"
                )
            },
        }
        invitation_event = {
            "type": "tool_use",
            "sessionID": "session-live-shape",
            "part": {
                "tool": (
                    "ziniao-contact_"
                    "ziniao_send_selected_invitation"
                ),
                "state": {
                    "status": "completed",
                    "input": {
                        "taskId": "test",
                        "stepId": "send-selected-invitation",
                        "creator": "@highest",
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
                                    "invitationGroupId": (
                                        "7664550207413847821"
                                    ),
                                    "messageSent": True,
                                    "invitationCreated": True,
                                    "invitationCompleted": True,
                                    "invitationButtonClicked": True,
                                    "invitationSubmissionAttempted": True,
                                    "invitationSubmissionConfirmed": True,
                                    "invitationSent": True,
                                    "creatorTabsClosed": True,
                                    "creatorDetailTargetGone": True,
                                    "searchTabKept": True,
                                    "returnedToFindCreators": True,
                                    "findCreatorsSearchReady": True,
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
                + json.dumps(invitation_event)
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
            "creator_contact.services.contact_runner.run_task_subprocess",
            return_value=completed,
        ) as run:
            result = SubprocessContactExecutor()(task, target)

        command = run.call_args.args[1]
        self.assertIn("ziniao_automation.contact_task_runner", command)
        self.assertIn("--greeting-message", command)
        self.assertEqual(
            command[command.index("--creator") + 1],
            target.imported_creator_id,
        )
        self.assertNotEqual(
            command[command.index("--creator") + 1],
            target.nickname_snapshot,
        )
        self.assertFalse(
            command[command.index("--creator") + 1].startswith("@")
        )
        self.assertEqual(
            command[command.index("--invitation-group-id") + 1],
            task.invitation_id_snapshot,
        )
        self.assertEqual(
            command[command.index("--model") + 1],
            task.model_name,
        )
        self.assertNotIn("--confirm-send-card", command)
        self.assertEqual(command[command.index("--through-step") + 1], "11")
        self.assertTrue(result["invitationCompleted"])
        self.assertTrue(result["success"])
        self.assertTrue(result["invitationButtonClicked"])
        self.assertTrue(result["invitationSubmissionConfirmed"])
        self.assertTrue(result["creatorDetailTargetGone"])
        self.assertTrue(result["findCreatorsSearchReady"])
        self.assertEqual(result["creatorId"], "7493994012378827459")
        self.assertEqual(result["sessionID"], "session-live-shape")
        self.assertNotIn("invitationId", result)
        self.assertNotIn("finalSendVerified", result)
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
                                    "findCreatorsSearchReady": True,
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
        self.assertTrue(result["findCreatorsSearchReady"])

    def test_subprocess_parser_ignores_non_mcp_list_output(self):
        unauthorized_event = {
            "type": "tool_use",
            "part": {
                "tool": "bash",
                "state": {
                    "status": "completed",
                    "input": {"command": "not-run-by-contact-parser"},
                    "output": json.dumps([{"unexpected": "shape"}]),
                },
            },
        }
        disconnect_event = {
            "type": "tool_use",
            "part": {
                "tool": "ziniao-contact_ziniao_disconnect",
                "state": {
                    "status": "completed",
                    "input": {"stepId": "disconnect"},
                    "output": json.dumps(
                        {
                            "success": True,
                            "status": "SUCCESS",
                            "data": {"disconnected": True},
                            "error": None,
                        }
                    ),
                },
            },
        }

        result = SubprocessContactExecutor._parse_output(
            json.dumps(unauthorized_event)
            + "\n"
            + json.dumps(disconnect_event)
        )

        self.assertEqual(len(result["steps"]), 1)
        self.assertEqual(
            result["steps"][0]["operation"],
            "ziniao_disconnect",
        )

    def test_subprocess_parser_preserves_tool_error_root_cause(self):
        failed_event = {
            "type": "tool_use",
            "sessionID": "session-failed",
            "part": {
                "tool": "ziniao-contact_ziniao_send_greeting",
                "state": {
                    "status": "error",
                    "input": {
                        "stepId": "send-greeting",
                        "confirmSendGreiting": True,
                    },
                    "error": json.dumps(
                        {
                            "success": False,
                            "status": "FAILED",
                            "data": None,
                            "error": {
                                "code": "ZiniaoWorkflowError",
                                "userMessage": "必须显式确认发送招呼语。",
                                "retryable": False,
                                "domFallback": {
                                    "classification": "business_safety"
                                },
                            },
                        }
                    ),
                },
            },
        }

        result = SubprocessContactExecutor._parse_output(
            json.dumps(failed_event)
        )

        self.assertEqual(result["errorCode"], "ZiniaoWorkflowError")
        self.assertEqual(
            result["errorMessage"],
            "必须显式确认发送招呼语。",
        )
        self.assertEqual(result["steps"][0]["status"], "FAILED")
        self.assertEqual(
            result["steps"][0]["domFallback"]["classification"],
            "business_safety",
        )

    def test_final_json_parser_accepts_prose_before_bare_object(self):
        final_event = {
            "type": "text",
            "part": {
                "text": (
                    "流程已经停止。\n"
                    '{"success":false,"errorCode":"ROOT_CAUSE",'
                    '"errorMessage":"精确错误"}'
                )
            },
        }

        result = SubprocessContactExecutor._parse_output(
            json.dumps(final_event)
        )

        self.assertEqual(result["errorCode"], "ROOT_CAUSE")
        self.assertEqual(result["errorMessage"], "精确错误")

    def test_final_json_parser_preserves_review_required_state(self):
        final_event = {
            "type": "text",
            "part": {
                "text": json.dumps(
                    {
                        "success": False,
                        "reviewRequired": True,
                        "skipCreator": False,
                        "failureStage": "REVIEW_REQUIRED",
                        "errorCode": "MODEL_FALLBACK_EXHAUSTED",
                    }
                )
            },
        }

        result = SubprocessContactExecutor._parse_output(
            json.dumps(final_event)
        )

        self.assertTrue(result["reviewRequired"])
        self.assertFalse(result["skipCreator"])
        self.assertEqual(
            result["failureStage"],
            "REVIEW_REQUIRED",
        )

    def test_card_batch_executor_opens_project_once_for_all_targets(self):
        task = self.create_contact_task(top_n=2)
        freeze_task_targets(task)
        targets = list(task.targets.order_by("rank"))
        output = {
            "success": True,
            "projectOpenedOnce": True,
            "projectOpenCount": 1,
            "steps": [
                {
                    "action": "open_project_accepted_creators",
                    "success": True,
                    "evidence": {"acceptedCreatorsPageVisible": True},
                }
            ],
            "results": [
                {
                    **self.accepted_card_executor(task, target),
                    "creator": f"@{target.normalized_handle}",
                }
                for target in targets
            ],
        }
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps(output) + "\n",
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
            "creator_contact.services.contact_runner.run_task_subprocess",
            return_value=completed,
        ) as run:
            results = SubprocessCardExecutor().run_many(task, targets)

        command = run.call_args.args[1]
        self.assertIn("--creators-json", command)
        creators = json.loads(
            command[command.index("--creators-json") + 1]
        )
        self.assertEqual(creators, ["@highest", "@middle"])
        self.assertEqual(run.call_count, 1)
        self.assertTrue(results[targets[0].pk]["cardSent"])
        self.assertTrue(results[targets[1].pk]["finalSendVerified"])
        self.assertEqual(
            results[targets[0].pk]["steps"][0]["operation"],
            "open_project_accepted_creators",
        )

    def test_cancellation_stops_remaining_targets(self):
        task = self.create_contact_task(top_n=2)
        freeze_task_targets(task)

        def cancelled_executor(current_task, target):
            CreatorContactTask.objects.filter(pk=current_task.pk).update(
                status=CreatorContactTask.Status.CANCELLED
            )
            raise TaskCancellationRequested("cancelled")

        result = CreatorContactRunner(
            task,
            executor=cancelled_executor,
        ).run()

        self.assertEqual(result.status, CreatorContactTask.Status.CANCELLED)
        self.assertEqual(
            set(task.targets.values_list("status", flat=True)),
            {CreatorContactTarget.Status.SKIPPED},
        )

    def test_exact_creator_safety_failure_is_skippable(self):
        self.assertTrue(
            _exact_creator_unavailable(
                {
                    "messageSent": False,
                    "errorMessage": (
                        "第 2 步被安全门阻止：等待后候选项"
                        "不再是精确目标 @catshrank。"
                    ),
                }
            )
        )
        self.assertFalse(
            _exact_creator_unavailable(
                {
                    "messageSent": True,
                    "errorMessage": "候选项不再是精确目标",
                }
            )
        )
        self.assertTrue(
            _exact_creator_unavailable(
                {
                    "messageSent": False,
                    "errorMessage": (
                        "第 2 步跳过：输入 @catshrank 后"
                        "未出现精确同名候选项。"
                    ),
                }
            )
        )
        self.assertTrue(
            _exact_creator_unavailable(
                {
                    "messageSent": False,
                    "errorMessage": (
                        "聊天对象验收失败：聊天页顶部未显示"
                        "目标达人用户名。"
                    ),
                }
            )
        )


class CollaborationSyncRunnerTests(CreatorContactTestCase):
    def test_busy_browser_requeues_job_instead_of_failing(self):
        job = CollaborationSyncJob.objects.create(store_id=self.store_id)

        def busy_executor(_store_id):
            raise CollaborationSyncBrowserBusyError(
                "等待浏览器控制权超时。"
            )

        result = CollaborationSyncRunner(
            job,
            executor=busy_executor,
        ).run()

        self.assertEqual(result.status, CollaborationSyncJob.Status.PENDING)
        self.assertEqual(result.error_code, "BROWSER_BUSY")
        self.assertIsNone(result.finished_at)

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

    def test_subprocess_sync_executor_classifies_busy_browser(self):
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=1,
            stdout=json.dumps(
                {
                    "success": False,
                    "storeId": self.store_id,
                    "options": [],
                    "errorCode": "BROWSER_BUSY",
                    "errorMessage": "等待浏览器控制权超时。",
                }
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
        ):
            with self.assertRaises(
                CollaborationSyncBrowserBusyError
            ):
                list(SubprocessCollaborationExecutor()(self.store_id))
