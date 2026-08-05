from __future__ import annotations

import os
import uuid
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from creator_contact.models import CreatorContactTarget, CreatorContactTask
from mailing.models import (
    EmailDelivery,
    EmailDeliveryAttempt,
    EmailSendingService,
)
from mailing.services.contact_dispatch import dispatch_contact_task_emails
from mailing.services.launcher import launch_email_sender
from mailing.services.runtime import (
    finish_service_run,
    recover_interrupted_email_state,
)
from tasks.models import Creator, ImportTask, ImportTaskCreator


class ContactTaskEmailDispatchTests(TestCase):
    def setUp(self) -> None:
        self.import_task = ImportTask.objects.create(
            file_name="contact-email.xlsx",
            file_sha256="d" * 64,
            status=ImportTask.Status.SUCCESS,
            snapshot_date=timezone.localdate(),
            confirmed_at=timezone.now(),
            finished_at=timezone.now(),
        )
        self.task = CreatorContactTask.objects.create(
            store_id="dispatch-store",
            source_import_task=self.import_task,
            selection_method=CreatorContactTask.SelectionMethod.MANUAL,
            top_n=3,
            greeting_snapshot="Hello",
            invitation_name_snapshot="Plan",
            invitation_id_snapshot="plan-id",
            status=CreatorContactTask.Status.SUCCESS,
            finished_at=timezone.now(),
        )

    def add_target(
        self,
        handle: str,
        email: str,
        *,
        success: bool = True,
    ) -> CreatorContactTarget:
        creator = Creator.objects.create(
            creator_id=handle,
            nickname=handle,
            email=email,
        )
        ImportTaskCreator.objects.create(
            import_task=self.import_task,
            creator=creator,
            first_row_number=creator.pk,
        )
        return CreatorContactTarget.objects.create(
            task=self.task,
            creator=creator,
            rank=self.task.targets.count() + 1,
            creator_handle_snapshot=handle,
            status=(
                CreatorContactTarget.Status.SUCCESS
                if success
                else CreatorContactTarget.Status.FAILED
            ),
            card_sent=success,
            final_send_verified=success,
            finished_at=timezone.now(),
        )

    def test_dispatches_only_verified_success_with_email(self) -> None:
        selected = self.add_target("selected", "selected@example.com")
        self.add_target("no-email", "")
        self.add_target("failed", "failed@example.com", success=False)

        with patch(
            "mailing.services.contact_dispatch.launch_email_sender"
        ) as launcher:
            result = dispatch_contact_task_emails(self.task.pk)

        self.assertEqual(result.queued, 1)
        delivery = EmailDelivery.objects.get()
        self.assertEqual(delivery.creator, selected.creator)
        launcher.assert_called_once_with(resume_paused=False)
        self.task.refresh_from_db()
        self.assertEqual(
            self.task.email_dispatch_status,
            CreatorContactTask.EmailDispatchStatus.COMPLETED,
        )
        self.assertEqual(
            self.task.email_dispatch_summary["eligible"],
            1,
        )

    def test_dispatch_is_idempotent(self) -> None:
        self.add_target("selected", "selected@example.com")
        with patch(
            "mailing.services.contact_dispatch.launch_email_sender"
        ):
            dispatch_contact_task_emails(self.task.pk)
            dispatch_contact_task_emails(self.task.pk)
        self.assertEqual(EmailDelivery.objects.count(), 1)

    def test_persistent_worker_dispatches_task_then_runs_sender(self) -> None:
        self.add_target("worker-selected", "worker@example.com")

        with patch(
            "mailing.management.commands.run_email_worker.call_command"
        ) as sender_command:
            call_command(
                "run_email_worker",
                once=True,
                stdout=StringIO(),
                stderr=StringIO(),
            )

        self.assertEqual(EmailDelivery.objects.count(), 1)
        sender_command.assert_called_once()
        self.assertEqual(
            sender_command.call_args.args[0],
            "send_creator_emails",
        )


class InterruptedEmailRecoveryTests(TestCase):
    def test_sending_delivery_becomes_uncertain_without_retry(self) -> None:
        creator = Creator.objects.create(
            creator_id="uncertain",
            email="uncertain@example.com",
        )
        delivery = EmailDelivery.objects.create(
            creator=creator,
            creator_id_snapshot=creator.creator_id,
            creator_name_snapshot="Uncertain",
            recipient_email=creator.email,
            status=EmailDelivery.Status.SENDING,
            attempt_count=1,
        )
        attempt = EmailDeliveryAttempt.objects.create(
            delivery=delivery,
            sequence=1,
            attempt_date=timezone.localdate(),
            daily_sequence=1,
            status=EmailDeliveryAttempt.Status.STARTED,
            started_at=timezone.now(),
        )
        service = EmailSendingService.objects.create(
            id=1,
            status=EmailSendingService.Status.RUNNING,
            process_id=99999999,
            heartbeat_at=timezone.now(),
        )

        active, recovered = recover_interrupted_email_state(
            current_process_id=os.getpid()
        )

        self.assertFalse(active)
        self.assertEqual(recovered, 1)
        delivery.refresh_from_db()
        attempt.refresh_from_db()
        service.refresh_from_db()
        self.assertEqual(delivery.status, EmailDelivery.Status.UNCERTAIN)
        self.assertFalse(delivery.retryable)
        self.assertEqual(
            attempt.status,
            EmailDeliveryAttempt.Status.UNCERTAIN,
        )
        self.assertEqual(
            service.status,
            EmailSendingService.Status.STOPPED,
        )

    def test_uncertain_delivery_requires_explicit_user_decision(self) -> None:
        creator = Creator.objects.create(
            creator_id="manual-review",
            email="review@example.com",
        )
        delivery = EmailDelivery.objects.create(
            creator=creator,
            creator_id_snapshot=creator.creator_id,
            creator_name_snapshot="Review",
            recipient_email=creator.email,
            status=EmailDelivery.Status.UNCERTAIN,
            retryable=False,
        )

        with patch("mailing.views.launch_email_sender") as launcher:
            response = self.client.post(
                reverse(
                    "mailing:retry_uncertain",
                    kwargs={"delivery_id": delivery.pk},
                )
            )

        self.assertEqual(response.status_code, 302)
        launcher.assert_called_once_with()
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, EmailDelivery.Status.PENDING)

    def test_user_can_confirm_uncertain_delivery_as_sent(self) -> None:
        creator = Creator.objects.create(
            creator_id="confirmed",
            email="confirmed@example.com",
        )
        delivery = EmailDelivery.objects.create(
            creator=creator,
            creator_id_snapshot=creator.creator_id,
            creator_name_snapshot="Confirmed",
            recipient_email=creator.email,
            status=EmailDelivery.Status.UNCERTAIN,
            retryable=False,
        )

        response = self.client.post(
            reverse(
                "mailing:confirm_uncertain_sent",
                kwargs={"delivery_id": delivery.pk},
            )
        )

        self.assertEqual(response.status_code, 302)
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, EmailDelivery.Status.SENT)

    def test_requested_stop_finishes_in_paused_state(self) -> None:
        run_id = uuid.uuid4()
        EmailSendingService.objects.create(
            id=1,
            status=EmailSendingService.Status.STOPPING,
            stop_requested=True,
            run_id=run_id,
            process_id=os.getpid(),
        )

        finish_service_run(run_id)

        service = EmailSendingService.objects.get(pk=1)
        self.assertEqual(
            service.status,
            EmailSendingService.Status.PAUSED,
        )
        self.assertFalse(service.stop_requested)

    def test_automatic_dispatch_does_not_resume_manual_pause(self) -> None:
        creator = Creator.objects.create(
            creator_id="paused",
            email="paused@example.com",
        )
        EmailDelivery.objects.create(
            creator=creator,
            creator_id_snapshot=creator.creator_id,
            creator_name_snapshot="Paused",
            recipient_email=creator.email,
        )
        EmailSendingService.objects.create(
            id=1,
            status=EmailSendingService.Status.PAUSED,
        )

        with patch(
            "mailing.services.launcher.connection"
        ) as connection, patch(
            "mailing.services.launcher.subprocess.Popen"
        ) as popen:
            connection.settings_dict = {"NAME": "/tmp/agent.db"}
            started = launch_email_sender(resume_paused=False)

        self.assertFalse(started)
        popen.assert_not_called()
        self.assertEqual(
            EmailSendingService.objects.get(pk=1).status,
            EmailSendingService.Status.PAUSED,
        )
