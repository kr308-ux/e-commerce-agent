from unittest.mock import patch

from django.conf import settings
from django.http import HttpResponse
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from creator_contact.models import (
    CollaborationSyncJob,
    ContactedCreator,
    CreatorContactTarget,
    CreatorContactTask,
    GreetingTemplate,
)
from creator_contact.services.candidate_selector import freeze_task_targets

from .base import CreatorContactTestCase


@override_settings(ZINIAO_CONTACT_STORE_ID="store-test-1")
class CreatorContactViewTests(CreatorContactTestCase):
    def test_dashboard_exposes_required_context_contract(self):
        with patch("creator_contact.views.render") as mocked_render:
            mocked_render.return_value = HttpResponse("ok")
            response = self.client.get(reverse("creator_contact:dashboard"))

        self.assertEqual(response.status_code, 200)
        context = mocked_render.call_args.args[2]
        for key in (
            "form",
            "greeting_templates",
            "collaboration_options",
            "source_tasks",
            "import_batches",
            "candidate_preview",
            "excluded_count",
            "latest_contact_tasks",
            "store_id",
            "browser_status",
            "sync_status",
        ):
            self.assertIn(key, context)

    def test_dashboard_and_task_detail_templates_render(self):
        dashboard = self.client.get(reverse("creator_contact:dashboard"))
        task = self.create_contact_task(top_n=1)
        freeze_task_targets(task)
        detail = self.client.get(
            reverse(
                "creator_contact:task_detail",
                kwargs={"task_id": task.pk},
            )
        )

        self.assertEqual(dashboard.status_code, 200)
        self.assertContains(dashboard, "达人联系")
        self.assertContains(dashboard, "达人选择")
        self.assertNotContains(dashboard, "选择导入批次与高销售额达人")
        self.assertContains(dashboard, "总销售额")
        self.assertContains(dashboard, "Highest Creator")
        self.assertContains(dashboard, "达人 ID：Highest")
        self.assertContains(dashboard, "达人 ID：Middle")
        self.assertNotContains(dashboard, "达人 ID：@Middle")
        self.assertNotContains(dashboard, "确认全部远端发送操作")
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, "目标达人执行状态")
        self.assertContains(detail, "达人 ID：Highest")
        self.assertContains(detail, "聊天 creator_id")
        self.assertContains(detail, "从 Django 启动测试")

    def test_browser_status_endpoint_returns_readiness(self):
        with patch(
            "creator_contact.views.browser_readiness",
            return_value={"ready": True, "status": "ready"},
        ):
            response = self.client.get(
                reverse("creator_contact:browser_status")
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ready"])

    def test_candidates_returns_ranked_json(self):
        response = self.client.get(
            reverse("creator_contact:candidates"),
            {
                "import_task_id": self.import_task.pk,
                "top_n": 2,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [row["creatorId"] for row in response.json()["creators"]],
            ["Highest", "Middle"],
        )
        self.assertEqual(
            [row["creatorHandle"] for row in response.json()["creators"]],
            ["Highest", "Middle"],
        )

    def test_candidates_supports_creator_id_auto_select_and_manual_rules(self):
        by_id = self.client.get(
            reverse("creator_contact:candidates"),
            {
                "import_task_id": self.import_task.pk,
                "selection_method": "CREATOR_ID",
            },
        )
        manual = self.client.get(
            reverse("creator_contact:candidates"),
            {
                "import_task_id": self.import_task.pk,
                "selection_method": "MANUAL",
            },
        )

        self.assertEqual(by_id.status_code, 200)
        self.assertEqual(
            [row["creatorId"] for row in by_id.json()["creators"]],
            ["Highest", "Middle", "low", "null_revenue"],
        )
        self.assertEqual(by_id.json()["requestedCount"], 50)
        self.assertEqual(manual.status_code, 200)
        self.assertEqual(len(manual.json()["creators"]), 4)

    def test_create_task_snapshots_configuration_and_targets(self):
        response = self.client.post(
            reverse("creator_contact:dashboard"),
            {
                "action": "create_task",
                "store_id": self.store_id,
                "source_import_task": self.import_task.pk,
                "top_n": 3,
                "greeting_template": self.greeting.pk,
                "collaboration_option": self.invitation.pk,
                "confirm_send_greeting": "on",
                "confirm_send_invitation": "on",
                "confirm_send_card": "on",
            },
        )

        task = CreatorContactTask.objects.get()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.url,
            reverse(
                "creator_contact:task_detail",
                kwargs={"task_id": task.pk},
            ),
        )
        self.assertEqual(task.greeting_snapshot, "Hello creator")
        self.assertEqual(task.invitation_name_snapshot, "金色拉链+短裤13")
        self.assertEqual(task.targets.count(), 3)
        self.assertEqual(task.model_name, settings.DOM_FALLBACK_MODEL)

    def test_create_task_from_creator_id_rule_auto_selects_full_batch(self):
        response = self.client.post(
            reverse("creator_contact:dashboard"),
            {
                "action": "create_task",
                "store_id": self.store_id,
                "source_import_task": self.import_task.pk,
                "selection_method": "CREATOR_ID",
                "greeting_template": self.greeting.pk,
                "collaboration_option": self.invitation.pk,
            },
        )

        task = CreatorContactTask.objects.get()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(task.selection_method, "CREATOR_ID")
        self.assertEqual(task.top_n, 4)
        self.assertTrue(task.confirm_send_greeting)
        self.assertTrue(task.confirm_send_invitation)
        self.assertTrue(task.confirm_send_card)
        self.assertEqual(
            list(
                task.targets.order_by("rank").values_list(
                    "creator_handle_snapshot",
                    flat=True,
                )
            ),
            ["Highest", "@Middle", "low", "null_revenue"],
        )

    def test_create_task_by_total_sales_uses_total_revenue_order(self):
        for creator, amount in (
            (self.high, 100),
            (self.middle, 900),
            (self.low, 500),
        ):
            self.create_creator(
                creator.creator_id,
                creator.nickname,
                revenue_30=None,
                revenue_7=None,
                revenue_total=amount,
                row=creator.import_memberships.get(
                    import_task=self.import_task
                ).first_row_number,
            )

        response = self.client.post(
            reverse("creator_contact:dashboard"),
            {
                "action": "create_task",
                "store_id": self.store_id,
                "source_import_task": self.import_task.pk,
                "selection_method": "SALES",
                "sales_window_days": "0",
                "top_n": 2,
                "greeting_template": self.greeting.pk,
                "collaboration_option": self.invitation.pk,
                "confirm_send_greeting": "on",
                "confirm_send_invitation": "on",
                "confirm_send_card": "on",
            },
        )

        task = CreatorContactTask.objects.get()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(task.sales_window_days, 0)
        self.assertEqual(
            list(
                task.targets.order_by("rank").values_list(
                    "creator_handle_snapshot",
                    flat=True,
                )
            ),
            ["@Middle", "low"],
        )
        self.assertEqual(
            str(task.targets.order_by("rank").first().total_revenue_snapshot),
            "900.00",
        )

    def test_create_task_from_manual_checkboxes_uses_checked_creators(self):
        response = self.client.post(
            reverse("creator_contact:dashboard"),
            {
                "action": "create_task",
                "store_id": self.store_id,
                "source_import_task": self.import_task.pk,
                "selection_method": "MANUAL",
                "selected_creator_ids": [
                    str(self.middle.pk),
                    str(self.high.pk),
                ],
                "greeting_template": self.greeting.pk,
                "collaboration_option": self.invitation.pk,
                "confirm_send_greeting": "on",
                "confirm_send_invitation": "on",
                "confirm_send_card": "on",
            },
        )

        task = CreatorContactTask.objects.get()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(task.selection_method, "MANUAL")
        self.assertEqual(task.top_n, 2)
        self.assertEqual(
            list(
                task.targets.order_by("rank").values_list(
                    "creator_handle_snapshot",
                    flat=True,
                )
            ),
            ["@Middle", "Highest"],
        )

    def test_save_greeting_and_enqueue_collaboration_sync(self):
        greeting_response = self.client.post(
            reverse("creator_contact:dashboard"),
            {
                "action": "save_greeting",
                "name": "Second greeting",
                "content": "A saved message",
            },
        )
        sync_response = self.client.post(
            reverse("creator_contact:sync_collaborations"),
            HTTP_ACCEPT="application/json",
        )

        self.assertEqual(greeting_response.status_code, 302)
        self.assertTrue(
            GreetingTemplate.objects.filter(name="Second greeting").exists()
        )
        self.assertEqual(sync_response.status_code, 202)
        self.assertEqual(CollaborationSyncJob.objects.count(), 1)

    def test_saving_an_existing_greeting_name_updates_it(self):
        response = self.client.post(
            reverse("creator_contact:dashboard"),
            {
                "action": "save_greeting",
                "name": self.greeting.name,
                "content": "Updated saved message",
                "is_default": "on",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.greeting.refresh_from_db()
        self.assertEqual(self.greeting.content, "Updated saved message")
        self.assertEqual(
            GreetingTemplate.objects.filter(
                name=self.greeting.name
            ).count(),
            1,
        )

    def test_saving_a_new_default_replaces_the_previous_default(self):
        response = self.client.post(
            reverse("creator_contact:dashboard"),
            {
                "action": "save_greeting",
                "name": "New default",
                "content": "New default message",
                "is_default": "on",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.greeting.refresh_from_db()
        self.assertFalse(self.greeting.is_default)
        self.assertTrue(
            GreetingTemplate.objects.get(name="New default").is_default
        )

    def test_detail_context_and_status_json(self):
        task = self.create_contact_task(top_n=1)
        freeze_task_targets(task)
        with patch("creator_contact.views.render") as mocked_render:
            mocked_render.return_value = HttpResponse("ok")
            detail = self.client.get(
                reverse(
                    "creator_contact:task_detail",
                    kwargs={"task_id": task.pk},
                )
            )
        status = self.client.get(
            reverse(
                "creator_contact:task_status",
                kwargs={"task_id": task.pk},
            )
        )

        self.assertEqual(detail.status_code, 200)
        context = mocked_render.call_args.args[2]
        self.assertEqual(context["contact_task"], task)
        self.assertIn("targets", context)
        self.assertIn("steps", context)
        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.json()["targetCount"], 1)
        self.assertEqual(status.json()["invitationCompletedCount"], 0)
        self.assertEqual(status.json()["cardSentCount"], 0)
        self.assertEqual(status.json()["cardPendingCount"], 0)

    def test_pending_task_can_be_started_from_django_server(self):
        task = self.create_contact_task(top_n=1)
        freeze_task_targets(task)

        with patch(
            "creator_contact.views.worker_endpoint_ready",
            return_value=False,
        ), patch(
            "creator_contact.views.wait_for_worker_endpoint",
            return_value=True,
        ), patch("creator_contact.views.subprocess.Popen") as popen:
            response = self.client.post(
                reverse(
                    "creator_contact:start_task",
                    kwargs={"task_id": task.pk},
                ),
                HTTP_ACCEPT="application/json",
            )

        task.refresh_from_db()
        self.assertEqual(response.status_code, 202)
        self.assertEqual(task.status, CreatorContactTask.Status.RUNNING)
        self.assertEqual(
            task.current_step,
            "等待常驻达人联系 Worker 领取任务",
        )
        command = popen.call_args.args[0]
        self.assertEqual(
            command[0],
            settings.AUTOMATION_PYTHON_EXECUTABLE,
        )
        self.assertIn("run_creator_contact_worker", command)
        self.assertIn("--server-mode", command)
        self.assertIn("--control-port", command)

        with patch("creator_contact.views.subprocess.Popen") as duplicate:
            repeated = self.client.post(
                reverse(
                    "creator_contact:start_task",
                    kwargs={"task_id": task.pk},
                ),
                HTTP_ACCEPT="application/json",
            )
        self.assertEqual(repeated.status_code, 409)
        duplicate.assert_not_called()

    def test_start_task_reuses_worker_health_port(self):
        task = self.create_contact_task(top_n=1)
        freeze_task_targets(task)

        with patch(
            "creator_contact.views.worker_endpoint_ready",
            return_value=True,
        ), patch("creator_contact.views.subprocess.Popen") as popen:
            response = self.client.post(
                reverse(
                    "creator_contact:start_task",
                    kwargs={"task_id": task.pk},
                ),
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 202)
        self.assertTrue(response.json()["workerReused"])
        self.assertIn(":16852/health", response.json()["workerEndpoint"])
        popen.assert_not_called()

    def test_running_task_can_be_cancelled_from_django_server(self):
        task = self.create_contact_task(top_n=1)
        freeze_task_targets(task)
        target = task.targets.get()
        task.status = CreatorContactTask.Status.RUNNING
        task.current_step = "联系达人"
        task.save()
        target.status = "RUNNING"
        target.save()

        response = self.client.post(
            reverse(
                "creator_contact:cancel_task",
                kwargs={"task_id": task.pk},
            ),
            HTTP_ACCEPT="application/json",
        )

        task.refresh_from_db()
        target.refresh_from_db()
        self.assertEqual(response.status_code, 202)
        self.assertEqual(task.status, CreatorContactTask.Status.CANCELLED)
        self.assertEqual(task.error_code, "TASK_CANCELLED")
        self.assertIsNotNone(task.finished_at)
        self.assertEqual(target.status, "SKIPPED")

    def test_finished_task_cannot_be_cancelled(self):
        task = self.create_contact_task(top_n=1)
        task.status = CreatorContactTask.Status.SUCCESS
        task.save()

        response = self.client.post(
            reverse(
                "creator_contact:cancel_task",
                kwargs={"task_id": task.pk},
            ),
            HTTP_ACCEPT="application/json",
        )

        self.assertEqual(response.status_code, 409)

    def test_retry_failed_task_resets_task_and_clean_failures(self):
        task = self.create_contact_task(top_n=3)
        freeze_task_targets(task)
        targets = list(task.targets.order_by("rank"))
        task.status = CreatorContactTask.Status.FAILED
        task.error_code = "INVITATION_NOT_VERIFIED"
        task.error_message = "邀请未验证"
        task.finished_at = timezone.now()
        task.save()
        targets[0].status = CreatorContactTarget.Status.FAILED
        targets[1].status = CreatorContactTarget.Status.INVITATION_COMPLETED
        targets[1].message_sent = True
        targets[1].invitation_created = True
        targets[2].status = CreatorContactTarget.Status.REVIEW_REQUIRED
        for t in targets:
            t.save()

        response = self.client.post(
            reverse(
                "creator_contact:retry_task",
                kwargs={"task_id": task.pk},
            ),
            HTTP_ACCEPT="application/json",
        )

        task.refresh_from_db()
        targets[0].refresh_from_db()
        targets[1].refresh_from_db()
        targets[2].refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["success"])
        self.assertEqual(task.status, CreatorContactTask.Status.PENDING)
        self.assertEqual(task.error_code, "")
        self.assertIsNone(task.finished_at)
        self.assertEqual(targets[0].status, CreatorContactTarget.Status.PENDING)
        self.assertEqual(targets[1].status, CreatorContactTarget.Status.PENDING)
        self.assertEqual(
            targets[2].status,
            CreatorContactTarget.Status.REVIEW_REQUIRED,
        )

    def test_retry_rejects_running_or_success_task(self):
        task = self.create_contact_task(top_n=1)
        freeze_task_targets(task)
        task.status = CreatorContactTask.Status.SUCCESS
        task.save()

        response = self.client.post(
            reverse(
                "creator_contact:retry_task",
                kwargs={"task_id": task.pk},
            ),
            HTTP_ACCEPT="application/json",
        )

        self.assertEqual(response.status_code, 409)
        self.assertFalse(response.json()["success"])
        task.refresh_from_db()
        self.assertEqual(task.status, CreatorContactTask.Status.SUCCESS)

    def test_confirm_review_marks_success_and_writes_dedup_record(self):
        task = self.create_contact_task(top_n=1)
        freeze_task_targets(task)
        target = task.targets.get()
        target.status = CreatorContactTarget.Status.REVIEW_REQUIRED
        target.current_step = "存在写入证据，需人工复核"
        target.error_code = "ZiniaoWorkflowError"
        target.error_message = "第 10 步验收失败：目标邀请未保持选中状态。"
        target.message_sent = True
        target.chat_creator_id = "7493994012378827459"
        target.save()

        response = self.client.post(
            reverse(
                "creator_contact:confirm_review_target",
                kwargs={
                    "task_id": task.pk,
                    "target_id": target.pk,
                },
            ),
            HTTP_ACCEPT="application/json",
        )

        target.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["success"])
        self.assertEqual(
            target.status,
            CreatorContactTarget.Status.SUCCESS,
        )
        self.assertEqual(target.current_step, "已人工复核确认完成")
        self.assertEqual(target.error_code, "")
        self.assertIsNotNone(target.finished_at)
        record = ContactedCreator.objects.get(
            store_id=task.store_id,
            normalized_handle=target.normalized_handle,
        )
        self.assertEqual(record.contact_task_id, task.pk)
        self.assertEqual(
            record.evidence["contactStage"],
            "MANUAL_REVIEW_CONFIRMED",
        )

    def test_confirm_review_rejects_non_review_required_target(self):
        task = self.create_contact_task(top_n=1)
        freeze_task_targets(task)
        target = task.targets.get()
        target.status = CreatorContactTarget.Status.SUCCESS
        target.save()

        response = self.client.post(
            reverse(
                "creator_contact:confirm_review_target",
                kwargs={
                    "task_id": task.pk,
                    "target_id": target.pk,
                },
            ),
            HTTP_ACCEPT="application/json",
        )

        self.assertEqual(response.status_code, 409)
        self.assertFalse(response.json()["success"])
        self.assertFalse(
            ContactedCreator.objects.filter(
                store_id=task.store_id,
                normalized_handle=target.normalized_handle,
            ).exists()
        )

    def test_confirm_review_is_idempotent_on_dedup_record(self):
        task = self.create_contact_task(top_n=1)
        freeze_task_targets(task)
        target = task.targets.get()
        target.status = CreatorContactTarget.Status.REVIEW_REQUIRED
        target.save()
        ContactedCreator.objects.create(
            store_id=task.store_id,
            normalized_handle=target.normalized_handle,
            creator_handle=target.creator_handle_snapshot,
            greeting_sha256=task.greeting_sha256,
            invitation_id="7664550207413847821",
            invitation_name=task.invitation_name_snapshot,
            contact_task=task,
        )

        response = self.client.post(
            reverse(
                "creator_contact:confirm_review_target",
                kwargs={
                    "task_id": task.pk,
                    "target_id": target.pk,
                },
            ),
            HTTP_ACCEPT="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            ContactedCreator.objects.filter(
                store_id=task.store_id,
                normalized_handle=target.normalized_handle,
            ).count(),
            1,
        )

    def test_confirm_review_accepts_failed_target(self):
        task = self.create_contact_task(top_n=1)
        freeze_task_targets(task)
        target = task.targets.get()
        target.status = CreatorContactTarget.Status.FAILED
        target.current_step = "联系达人失败"
        target.error_code = "INVITATION_NOT_VERIFIED"
        target.error_message = "未取得邀请证据"
        target.save()

        response = self.client.post(
            reverse(
                "creator_contact:confirm_review_target",
                kwargs={
                    "task_id": task.pk,
                    "target_id": target.pk,
                },
            ),
            HTTP_ACCEPT="application/json",
        )

        target.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["success"])
        self.assertEqual(
            target.status,
            CreatorContactTarget.Status.SUCCESS,
        )
        self.assertEqual(target.current_step, "已人工复核确认完成")
        self.assertTrue(
            ContactedCreator.objects.filter(
                store_id=task.store_id,
                normalized_handle=target.normalized_handle,
            ).exists()
        )

    def test_confirm_review_rejects_running_target(self):
        task = self.create_contact_task(top_n=1)
        freeze_task_targets(task)
        target = task.targets.get()
        target.status = CreatorContactTarget.Status.RUNNING
        target.save()

        response = self.client.post(
            reverse(
                "creator_contact:confirm_review_target",
                kwargs={
                    "task_id": task.pk,
                    "target_id": target.pk,
                },
            ),
            HTTP_ACCEPT="application/json",
        )

        self.assertEqual(response.status_code, 409)
        self.assertFalse(response.json()["success"])
        target.refresh_from_db()
        self.assertEqual(
            target.status,
            CreatorContactTarget.Status.RUNNING,
        )
