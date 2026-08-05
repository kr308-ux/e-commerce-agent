"""Durably turn verified contact-task results into email deliveries."""

from __future__ import annotations

from dataclasses import asdict, fields
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from creator_contact.models import CreatorContactTarget, CreatorContactTask
from tasks.models import Creator

from mailing.services.launcher import launch_email_sender
from mailing.services.queue import QueueResult, queue_creators
from mailing.services.template_content import get_active_template_version


TERMINAL_CONTACT_STATUSES = {
    CreatorContactTask.Status.SUCCESS,
    CreatorContactTask.Status.PARTIAL_SUCCESS,
    CreatorContactTask.Status.FAILED,
}
MAX_DISPATCH_ATTEMPTS = 3
DISPATCH_RETRY_DELAY_SECONDS = 60


def record_dispatch_failure(task_id: object, error: Exception) -> None:
    task = CreatorContactTask.objects.get(pk=task_id)
    prior = (
        task.email_dispatch_summary
        if isinstance(task.email_dispatch_summary, dict)
        else {}
    )
    attempts = int(prior.get("attempts") or 0) + 1
    task.email_dispatch_status = (
        CreatorContactTask.EmailDispatchStatus.FAILED
        if attempts >= MAX_DISPATCH_ATTEMPTS
        else CreatorContactTask.EmailDispatchStatus.PENDING
    )
    task.email_dispatch_summary = {
        "attempts": attempts,
        "errorType": type(error).__name__,
        "errorMessage": str(error)[:1000],
    }
    task.email_dispatched_at = timezone.now()
    task.save(
        update_fields=[
            "email_dispatch_status",
            "email_dispatch_summary",
            "email_dispatched_at",
            "updated_at",
        ]
    )


def eligible_contact_task_creators(task: CreatorContactTask):
    return (
        Creator.objects.filter(
            contact_targets__task=task,
            contact_targets__status=CreatorContactTarget.Status.SUCCESS,
            contact_targets__card_sent=True,
            contact_targets__final_send_verified=True,
        )
        .exclude(email="")
        .distinct()
        .order_by("creator_id")
    )


def dispatch_contact_task_emails(
    task_id: object,
    *,
    launch_sender: bool = True,
) -> QueueResult:
    """Idempotently queue one terminal contact task's eligible creators."""

    with transaction.atomic():
        task = (
            CreatorContactTask.objects
            .select_related("source_import_task")
            .get(pk=task_id)
        )
        if task.status not in TERMINAL_CONTACT_STATUSES:
            raise ValueError("只有已结束的联系任务可以派发邮件。")
        if (
            task.email_dispatch_status
            == CreatorContactTask.EmailDispatchStatus.COMPLETED
        ):
            return QueueResult()

        creators = eligible_contact_task_creators(task)
        eligible_count = creators.count()
        if eligible_count:
            result = QueueResult()
            creator_list = list(creators)
            chunk_size = max(1, settings.CREATOR_EMAIL_DAILY_LIMIT)
            template_version = get_active_template_version()
            for offset in range(0, eligible_count, chunk_size):
                chunk = creator_list[offset : offset + chunk_size]
                chunk_result = queue_creators(
                    creators=chunk,
                    limit=len(chunk),
                    source_import_task=task.source_import_task,
                    retry_failed=False,
                    template_version=template_version,
                )
                for field in fields(QueueResult):
                    setattr(
                        result,
                        field.name,
                        getattr(result, field.name)
                        + getattr(chunk_result, field.name),
                    )
        else:
            result = QueueResult()
        task.email_dispatch_status = (
            CreatorContactTask.EmailDispatchStatus.COMPLETED
        )
        task.email_dispatch_summary = {
            "eligible": eligible_count,
            **asdict(result),
        }
        task.email_dispatched_at = timezone.now()
        task.save(
            update_fields=[
                "email_dispatch_status",
                "email_dispatch_summary",
                "email_dispatched_at",
                "updated_at",
            ]
        )

    if launch_sender and result.selected:
        launch_email_sender(resume_paused=False)
    return result


def dispatch_pending_contact_tasks(*, limit: int = 20) -> int:
    retry_before = timezone.now() - timedelta(
        seconds=DISPATCH_RETRY_DELAY_SECONDS
    )
    task_ids = list(
        CreatorContactTask.objects.filter(
            status__in=TERMINAL_CONTACT_STATUSES,
            email_dispatch_status=(
                CreatorContactTask.EmailDispatchStatus.PENDING
            ),
        ).filter(
            Q(email_dispatched_at__isnull=True)
            | Q(email_dispatched_at__lte=retry_before)
        )
        .order_by("finished_at", "created_at")
        .values_list("pk", flat=True)[:limit]
    )
    dispatched = 0
    for task_id in task_ids:
        try:
            dispatch_contact_task_emails(task_id, launch_sender=False)
        except Exception as error:
            record_dispatch_failure(task_id, error)
        else:
            dispatched += 1
    return dispatched
