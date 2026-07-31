from __future__ import annotations

from datetime import timedelta
from importlib import import_module
from unittest.mock import patch

from django.apps import apps
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from creator_contact.models import (
    ContactedCreator,
    CreatorContactTarget,
    CreatorContactTask,
    CreatorContactTaskStep,
)
from mailing.models import EmailDelivery
from mailing.models import EmailDeliveryAttempt
from mailing.services.queue import queue_creators

from .models import Creator, ImportTask, ImportTaskCreator


@override_settings(ZINIAO_CONTACT_STORE_ID="store-1")
class OutreachRecordsViewTests(TestCase):
    def setUp(self) -> None:
        now = timezone.now()
        self.import_task = ImportTask.objects.create(
            file_name="july-creators.xlsx",
            file_sha256="a" * 64,
            status=ImportTask.Status.SUCCESS,
            snapshot_date=now.date(),
            confirmed_at=now,
        )
        self.contact_task = CreatorContactTask.objects.create(
            store_id="store-1",
            name="July outreach",
            source_import_task=self.import_task,
            top_n=50,
            greeting_snapshot="Hello",
            invitation_name_snapshot="July invitation",
            invitation_id_snapshot="invite-july",
        )

    def _creator(
        self,
        creator_id: str,
        *,
        nickname: str = "",
        email: str = "",
    ) -> Creator:
        creator = Creator.objects.create(
            creator_id=creator_id,
            nickname=nickname,
            email=email,
        )
        ImportTaskCreator.objects.create(
            import_task=self.import_task,
            creator=creator,
            first_row_number=Creator.objects.count() + 1,
        )
        return creator

    def _success(self, creator: Creator) -> ContactedCreator:
        return ContactedCreator.objects.create(
            store_id="store-1",
            normalized_handle=creator.creator_id,
            creator_handle=creator.creator_id,
            creator=creator,
            contact_task=self.contact_task,
            greeting_sha256="b" * 64,
            invitation_id="invite-july",
            invitation_name="July invitation",
        )

    def test_dashboard_renders_success_email_state_and_clean_sidebar(
        self,
    ) -> None:
        alice = self._creator(
            "alice-shop",
            nickname="Alice",
            email="alice@example.com",
        )
        bob = self._creator("bob-shop", nickname="Bob")
        self._success(alice)
        self._success(bob)
        EmailDelivery.objects.create(
            creator=alice,
            source_import_task=self.import_task,
            creator_id_snapshot=alice.creator_id,
            creator_name_snapshot=alice.nickname,
            recipient_email=alice.email,
            status=EmailDelivery.Status.SENT,
            sent_at=timezone.now(),
        )

        response = self.client.get(reverse("tasks:dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "触达记录")
        self.assertContains(response, "alice@example.com")
        self.assertContains(response, "发送成功")
        self.assertContains(response, "无邮箱")
        self.assertNotContains(response, "系统设置")
        self.assertNotContains(response, "导入状态")
        self.assertNotContains(response, ">KR<", html=False)

        email_search = self.client.get(
            reverse("tasks:outreach-records"),
            {
                "record_type": "email",
                "creator_id": "alice@example.com",
            },
        )
        self.assertEqual(
            email_search.content.count(b"data-record-row"),
            1,
        )
        self.assertContains(email_search, "alice-shop")

    def test_historical_delivery_batch_is_backfilled_from_verified_contact(
        self,
    ) -> None:
        creator = self._creator(
            "historical-creator",
            email="historical@example.com",
        )
        CreatorContactTarget.objects.create(
            task=self.contact_task,
            creator=creator,
            rank=1,
            creator_handle_snapshot=creator.creator_id,
            status=CreatorContactTarget.Status.SUCCESS,
            card_sent=True,
            final_send_verified=True,
            finished_at=timezone.now() - timedelta(minutes=1),
        )
        delivery = EmailDelivery.objects.create(
            creator=creator,
            creator_id_snapshot=creator.creator_id,
            creator_name_snapshot=creator.nickname,
            recipient_email=creator.email,
            status=EmailDelivery.Status.SENT,
            sent_at=timezone.now(),
        )

        migration = import_module(
            "mailing.migrations.0006_backfill_contact_source_batches"
        )
        migration.backfill_source_from_verified_contacts(apps, None)

        delivery.refresh_from_db()
        self.assertEqual(delivery.source_import_task, self.import_task)

    def test_failed_contact_filters_and_exposes_failure_details(self) -> None:
        creator = self._creator("failed-creator", nickname="Failed Creator")
        target = CreatorContactTarget.objects.create(
            task=self.contact_task,
            creator=creator,
            rank=1,
            creator_handle_snapshot=creator.creator_id,
            nickname_snapshot=creator.nickname,
            status=CreatorContactTarget.Status.FAILED,
            current_step="发送合作卡片",
            error_code="CARD_SEND_FAILED",
            error_message="合作卡片按钮不可用",
            finished_at=timezone.now(),
        )
        CreatorContactTaskStep.objects.create(
            task=self.contact_task,
            target=target,
            step_id="failed-card-step",
            sequence=3,
            operation="send_card",
            label="确认合作卡片发送",
            status=CreatorContactTaskStep.Status.FAILED,
            error_code="CARD_SEND_FAILED",
            error_message="合作卡片按钮不可用",
        )

        response = self.client.get(
            reverse("tasks:outreach-records"),
            {
                "record_type": "contact",
                "contact_result": "failed",
                "creator_id": "failed",
                "import_task": str(self.import_task.pk),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "failed-creator")
        self.assertContains(response, "确认合作卡片发送")
        self.assertContains(response, "合作卡片按钮不可用")
        self.assertContains(response, "july-creators.xlsx")

    def test_email_records_are_server_paginated_at_twenty(self) -> None:
        for index in range(25):
            creator = self._creator(
                f"mail-{index:02d}",
                nickname=f"Mailer {index:02d}",
                email=f"mail-{index:02d}@example.com",
            )
            EmailDelivery.objects.create(
                creator=creator,
                source_import_task=self.import_task,
                creator_id_snapshot=creator.creator_id,
                creator_name_snapshot=creator.nickname,
                recipient_email=creator.email,
                status=EmailDelivery.Status.SENT,
                sent_at=timezone.now() - timedelta(minutes=index),
            )

        first_page = self.client.get(
            reverse("tasks:outreach-records"),
            {
                "record_type": "email",
                "email_status": EmailDelivery.Status.SENT,
                "import_task": str(self.import_task.pk),
            },
        )
        second_page = self.client.get(
            reverse("tasks:outreach-records"),
            {
                "record_type": "email",
                "email_status": EmailDelivery.Status.SENT,
                "import_task": str(self.import_task.pk),
                "page": "2",
            },
        )

        self.assertEqual(
            first_page.content.count(b"data-record-row"),
            20,
        )
        self.assertContains(first_page, "第 1 / 2 页")
        self.assertEqual(
            second_page.content.count(b"data-record-row"),
            5,
        )
        self.assertContains(second_page, "第 2 / 2 页")

    def test_success_fragment_has_constant_query_count(self) -> None:
        for index in range(4):
            self._success(
                self._creator(
                    f"success-{index}",
                    email=f"success-{index}@example.com",
                )
            )

        with self.assertNumQueries(3):
            response = self.client.get(
                reverse("tasks:outreach-records"),
                {
                    "record_type": "contact",
                    "contact_result": "success",
                },
            )
            self.assertEqual(response.status_code, 200)

    def test_dashboard_counts_today_failed_recipients(self) -> None:
        creator = self._creator(
            "today-failed",
            email="today-failed@example.com",
        )
        delivery = EmailDelivery.objects.create(
            creator=creator,
            source_import_task=self.import_task,
            creator_id_snapshot=creator.creator_id,
            creator_name_snapshot=creator.nickname,
            recipient_email=creator.email,
            status=EmailDelivery.Status.FAILED,
            last_failure_at=timezone.now(),
        )
        EmailDeliveryAttempt.objects.create(
            delivery=delivery,
            sequence=1,
            attempt_date=timezone.localdate(),
            daily_sequence=1,
            status=EmailDeliveryAttempt.Status.FAILED,
            started_at=timezone.now(),
            finished_at=timezone.now(),
        )

        response = self.client.get(reverse("tasks:dashboard"))

        self.assertEqual(response.context["email_failed_today"], 1)
        self.assertContains(response, "今日邮件发送失败人数")


class EmailSourceBatchTests(TestCase):
    @patch(
        "mailing.services.queue.get_active_template_version",
        return_value=None,
    )
    def test_queue_persists_selected_import_batch(
        self,
        _active_template,
    ) -> None:
        now = timezone.now()
        import_task = ImportTask.objects.create(
            file_name="source.xlsx",
            file_sha256="c" * 64,
            status=ImportTask.Status.SUCCESS,
            snapshot_date=now.date(),
            confirmed_at=now,
        )
        creator = Creator.objects.create(
            creator_id="source-creator",
            nickname="Source Creator",
            email="source@example.com",
        )

        result = queue_creators(
            creators=[creator],
            limit=1,
            source_import_task=import_task,
        )

        self.assertEqual(result.queued, 1)
        delivery = EmailDelivery.objects.get()
        self.assertEqual(delivery.source_import_task, import_task)
